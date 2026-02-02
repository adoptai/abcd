#!/usr/bin/env python3
"""
Discovery Module - Unified tool and API discovery with FAISS + fuzzy search.

Features:
- Semantic search using FAISS embeddings
- Fuzzy text matching for exact/partial name search
- Hybrid mode combining both approaches
- Smart caching at environment workspace level
- Incremental cache updates (only embeds new items)

Cache Location:
    workspaces/{env}/.cache/
    ├── actions_cache.json      # Action data + embeddings
    └── apis_cache.json         # API data + embeddings
"""

import hashlib
import json
import os
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import faiss
import numpy as np
import requests
from sentence_transformers import SentenceTransformer

# Constants
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384
CACHE_DIR_NAME = ".cache"
ACTIONS_CACHE_FILE = "actions_cache.json"
APIS_CACHE_FILE = "apis_cache.json"


class DiscoveryCache:
    """Manages cached data with embeddings at environment level."""

    def __init__(self, env_path: Optional[Path] = None):
        """
        Initialize cache manager.

        Args:
            env_path: Path to environment workspace. If None, uses fallback location.
        """
        self.env_path = env_path
        self._cache_dir: Optional[Path] = None

    @property
    def cache_dir(self) -> Path:
        """Get cache directory, creating if needed."""
        if self._cache_dir is None:
            if self.env_path:
                self._cache_dir = self.env_path / CACHE_DIR_NAME
            else:
                # Fallback to workspaces root
                from cli.wdl_common.workspace_manager import WORKSPACES_DIR
                self._cache_dir = WORKSPACES_DIR / CACHE_DIR_NAME

            self._cache_dir.mkdir(parents=True, exist_ok=True)
        return self._cache_dir

    def load_cache(self, cache_type: str) -> Dict[str, Any]:
        """
        Load cache from file.

        Args:
            cache_type: "actions" or "apis"

        Returns:
            Cache dict with items, embeddings, and metadata
        """
        filename = ACTIONS_CACHE_FILE if cache_type == "actions" else APIS_CACHE_FILE
        cache_file = self.cache_dir / filename

        if not cache_file.exists():
            return {"items": [], "embeddings": {}, "last_updated": None}

        try:
            with open(cache_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"items": [], "embeddings": {}, "last_updated": None}

    def save_cache(self, cache_type: str, cache_data: Dict[str, Any]) -> None:
        """Save cache to file."""
        filename = ACTIONS_CACHE_FILE if cache_type == "actions" else APIS_CACHE_FILE
        cache_file = self.cache_dir / filename

        cache_data["last_updated"] = datetime.now().isoformat()

        try:
            with open(cache_file, "w") as f:
                json.dump(cache_data, f, indent=2)
        except IOError as e:
            print(f"⚠️  Warning: Could not save cache: {e}", file=sys.stderr)

    def get_item_hash(self, item: Dict[str, Any]) -> str:
        """Generate hash for item to detect changes."""
        # Use id + title + description for hash
        content = f"{item.get('id', '')}{item.get('title', '')}{item.get('description', '')}"
        return hashlib.md5(content.encode()).hexdigest()


class EmbeddingManager:
    """Manages embeddings and FAISS index."""

    def __init__(self):
        self.model: Optional[SentenceTransformer] = None
        self.dimension = EMBEDDING_DIMENSION
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize embedding model. Returns True if successful."""
        if self._initialized:
            return True

        try:
            print("⏳ Loading embedding model (first time may take a moment)...")
            self.model = SentenceTransformer(EMBEDDING_MODEL)
            self.dimension = self.model.get_sentence_embedding_dimension()
            self._initialized = True
            print(f"✅ Model loaded (dimension: {self.dimension})")
            return True
        except Exception as e:
            print(f"⚠️  Could not load embedding model: {e}", file=sys.stderr)
            return False

    def embed_text(self, text: str) -> Optional[List[float]]:
        """Embed a single text string."""
        if not self._initialized or not self.model:
            self.initialize()

        try:
            embedding = self.model.encode(text, convert_to_numpy=True)
            return embedding.tolist()
        except Exception:
            return None

    def embed_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Embed multiple texts efficiently."""
        if not self._initialized or not self.model:
            self.initialize()

        try:
            embeddings = self.model.encode(texts, convert_to_numpy=True)
            return embeddings.tolist()
        except Exception:
            return None

    def build_faiss_index(self, embeddings: List[List[float]]) -> Optional[faiss.IndexFlatIP]:
        """Build FAISS index from embeddings."""
        if not embeddings:
            return None

        try:
            embeddings_array = np.array(embeddings, dtype=np.float32)
            index = faiss.IndexFlatIP(self.dimension)  # Inner product for cosine similarity
            faiss.normalize_L2(embeddings_array)
            index.add(embeddings_array)
            return index
        except Exception:
            return None

    def search_index(
        self, index: faiss.IndexFlatIP, query_embedding: List[float], top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """
        Search FAISS index.

        Returns:
            List of (index, score) tuples
        """
        if index is None:
            return []

        try:
            query_array = np.array([query_embedding], dtype=np.float32)
            faiss.normalize_L2(query_array)
            scores, indices = index.search(query_array, top_k)
            return [(int(idx), float(score)) for idx, score in zip(indices[0], scores[0]) if idx >= 0]
        except Exception:
            return []


class Discovery:
    """
    Unified discovery for actions and APIs.

    Usage:
        discovery = Discovery(bearer_token="...", env_path=Path("workspaces/staging"))

        # Semantic search (FAISS)
        results = discovery.search_actions("inventory management", mode="semantic")

        # Fuzzy name search
        results = discovery.search_actions("get-orderpoints", mode="fuzzy")

        # Hybrid (both)
        results = discovery.search_actions("orderpoints", mode="hybrid")

        # Filter by type
        results = discovery.search_actions("inventory", tools_only=True)
    """

    def __init__(
        self,
        bearer_token: Optional[str] = None,
        env_path: Optional[Path] = None,
        api_endpoint: Optional[str] = None,
        actions_endpoint: Optional[str] = None,
    ):
        """
        Initialize discovery.

        Args:
            bearer_token: Auth token. Will fetch from auth module if not provided.
            env_path: Environment workspace path for cache location.
            api_endpoint: Connect API endpoint (default from env).
            actions_endpoint: Actions API endpoint (default from env).
        """
        self._bearer_token = bearer_token
        self.env_path = env_path
        self.api_endpoint = api_endpoint or os.getenv(
            "ADOPT_API_ENDPOINT", "https://connect.adopt.ai"
        )
        self.actions_endpoint = actions_endpoint or os.getenv(
            "ADOPT_ACTIONS_ENDPOINT", "https://api.adopt.ai"
        )

        self.cache = DiscoveryCache(env_path)
        self.embeddings = EmbeddingManager()

        # In-memory caches
        self._actions: List[Dict[str, Any]] = []
        self._apis: List[Dict[str, Any]] = []
        self._actions_index: Optional[Any] = None
        self._apis_index: Optional[Any] = None

    @property
    def bearer_token(self) -> str:
        """Get bearer token, fetching if needed."""
        if self._bearer_token is None:
            from cli.auth import get_bearer_token
            self._bearer_token = get_bearer_token()
        return self._bearer_token

    @property
    def headers(self) -> Dict[str, str]:
        """Standard headers for API calls."""
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

    # =========================================================================
    # Action Discovery
    # =========================================================================

    def fetch_actions(
        self,
        tools_only: bool = False,
        force_refresh: bool = False,
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Fetch actions from API, update cache with new items.

        Args:
            tools_only: If True, only fetch actions with execution_type=TOOL
            force_refresh: If True, re-embed all items

        Returns:
            Tuple of (success, actions, message)
        """
        # Load existing cache
        cache_data = self.cache.load_cache("actions")
        cached_items = {self.cache.get_item_hash(item): item for item in cache_data.get("items", [])}
        cached_embeddings = cache_data.get("embeddings", {})

        # Fetch from API
        url = f"{self.api_endpoint}/v1/actions/list"
        params = {}
        if tools_only:
            params["execution_type"] = "TOOL"

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)

            if response.status_code != 200:
                # Fall back to cache
                if cached_items:
                    self._actions = list(cached_items.values())
                    return True, self._actions, f"Using cached {len(self._actions)} actions (API error)"
                return False, [], f"Failed: {response.status_code} - {response.text}"

            data = response.json()
            actions = data.get("capabilities", [])

        except requests.exceptions.RequestException as e:
            # Fall back to cache
            if cached_items:
                self._actions = list(cached_items.values())
                return True, self._actions, f"Using cached {len(self._actions)} actions (network error)"
            return False, [], f"Network error: {e}"

        # Identify new items that need embedding
        new_items = []
        updated_embeddings = dict(cached_embeddings)

        for action in actions:
            item_hash = self.cache.get_item_hash(action)
            if item_hash not in cached_embeddings or force_refresh:
                new_items.append((item_hash, action))

        # Embed new items if we have the capability
        if new_items and self.embeddings.initialize():
            print(f"⏳ Embedding {len(new_items)} new actions...")
            texts = [self._build_action_text(item) for _, item in new_items]
            embeddings = self.embeddings.embed_batch(texts)

            if embeddings:
                for (item_hash, _), embedding in zip(new_items, embeddings):
                    updated_embeddings[item_hash] = embedding
                print(f"✅ Embedded {len(new_items)} new actions")

        # Update cache
        cache_data = {
            "items": actions,
            "embeddings": updated_embeddings,
        }
        self.cache.save_cache("actions", cache_data)

        self._actions = actions
        self._actions_index = None  # Reset index

        return True, actions, f"Fetched {len(actions)} actions"

    def _build_action_text(self, action: Dict[str, Any]) -> str:
        """Build searchable text from action."""
        parts = []
        if title := action.get("title"):
            parts.extend([title, title])  # Weight title higher
        if description := action.get("description"):
            parts.append(description)
        if tags := action.get("tags"):
            parts.append(" ".join(tags) if isinstance(tags, list) else str(tags))
        return " ".join(parts) or "untitled action"

    def _get_actions_index(self) -> Optional[Any]:
        """Get or build FAISS index for actions."""
        if self._actions_index is not None:
            return self._actions_index

        cache_data = self.cache.load_cache("actions")
        embeddings_dict = cache_data.get("embeddings", {})

        if not embeddings_dict:
            return None

        # Build ordered embeddings list matching actions order
        embeddings = []
        for action in self._actions:
            item_hash = self.cache.get_item_hash(action)
            if item_hash in embeddings_dict:
                embeddings.append(embeddings_dict[item_hash])
            else:
                # Missing embedding - use zero vector
                embeddings.append([0.0] * self.embeddings.dimension)

        self._actions_index = self.embeddings.build_faiss_index(embeddings)
        return self._actions_index

    def search_actions(
        self,
        query: str,
        mode: str = "hybrid",
        tools_only: bool = False,
        top_k: int = 10,
        fuzzy_threshold: float = 0.4,
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Search actions using semantic and/or fuzzy matching.

        Args:
            query: Search query
            mode: "semantic" (FAISS), "fuzzy" (text match), or "hybrid" (both)
            tools_only: Only return tool-type actions
            top_k: Maximum results
            fuzzy_threshold: Minimum fuzzy match score (0-1)

        Returns:
            Tuple of (success, results_with_scores, message)
        """
        # Ensure actions are loaded
        if not self._actions:
            success, _, msg = self.fetch_actions(tools_only=tools_only)
            if not success:
                return False, [], msg

        results: Dict[str, Tuple[Dict[str, Any], float]] = {}

        # Semantic search with FAISS
        if mode in ("semantic", "hybrid"):
            if self.embeddings.initialize():
                query_embedding = self.embeddings.embed_text(query)
                if query_embedding:
                    index = self._get_actions_index()
                    if index is not None:
                        matches = self.embeddings.search_index(index, query_embedding, top_k * 2)
                        for idx, score in matches:
                            if 0 <= idx < len(self._actions):
                                action = self._actions[idx]
                                action_id = action.get("id", str(idx))
                                if action_id not in results or score > results[action_id][1]:
                                    results[action_id] = (action, score)

        # Fuzzy text search
        if mode in ("fuzzy", "hybrid"):
            query_lower = query.lower()
            for action in self._actions:
                action_id = action.get("id", "")
                title = (action.get("title") or "").lower()
                description = (action.get("description") or "").lower()

                # Calculate fuzzy score
                title_score = SequenceMatcher(None, query_lower, title).ratio()
                desc_score = SequenceMatcher(None, query_lower, description).ratio() * 0.5

                # Boost exact substring matches
                if query_lower in title:
                    title_score = max(title_score, 0.9)
                if query_lower in description:
                    desc_score = max(desc_score, 0.6)

                score = max(title_score, desc_score)

                if score >= fuzzy_threshold:
                    if action_id not in results or score > results[action_id][1]:
                        results[action_id] = (action, score)

        # Filter by tools_only if needed
        if tools_only:
            results = {
                k: v for k, v in results.items()
                if v[0].get("execution_type") == "TOOL"
            }

        # Sort by score and limit
        sorted_results = sorted(results.values(), key=lambda x: x[1], reverse=True)[:top_k]

        # Add scores to results
        final_results = []
        for action, score in sorted_results:
            action_with_score = dict(action)
            action_with_score["_search_score"] = round(score * 100, 1)
            final_results.append(action_with_score)

        return True, final_results, f"Found {len(final_results)} matching actions"

    # =========================================================================
    # API Discovery
    # =========================================================================

    def fetch_apis(
        self,
        page_size: int = 50,
        force_refresh: bool = False,
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Fetch APIs from platform, update cache with new items.

        Args:
            page_size: APIs per page
            force_refresh: Re-embed all items

        Returns:
            Tuple of (success, apis, message)
        """
        # Load existing cache
        cache_data = self.cache.load_cache("apis")
        cached_items = {self.cache.get_item_hash(item): item for item in cache_data.get("items", [])}
        cached_embeddings = cache_data.get("embeddings", {})

        # Fetch from API
        url = f"{self.api_endpoint}/v1/tools/apis"
        all_apis: List[Dict[str, Any]] = []
        page = 1

        try:
            while True:
                params = {"page": page, "page_size": page_size}
                response = requests.get(url, headers=self.headers, params=params, timeout=30)

                if response.status_code != 200:
                    if all_apis:
                        break  # Use what we have
                    if cached_items:
                        self._apis = list(cached_items.values())
                        return True, self._apis, f"Using cached {len(self._apis)} APIs (API error)"
                    return False, [], f"Failed: {response.status_code}"

                data = response.json()

                # Handle different response formats
                page_apis = []
                if isinstance(data, list):
                    page_apis = data
                elif isinstance(data, dict):
                    page_apis = data.get("apis", data.get("data", data.get("items", [])))

                if not page_apis:
                    break

                all_apis.extend(page_apis)

                if len(page_apis) < page_size:
                    break
                page += 1

        except requests.exceptions.RequestException as e:
            if cached_items:
                self._apis = list(cached_items.values())
                return True, self._apis, f"Using cached {len(self._apis)} APIs (network error)"
            return False, [], f"Network error: {e}"

        # Identify new items
        new_items = []
        updated_embeddings = dict(cached_embeddings)

        for api in all_apis:
            item_hash = self.cache.get_item_hash(api)
            if item_hash not in cached_embeddings or force_refresh:
                new_items.append((item_hash, api))

        # Embed new items
        if new_items and self.embeddings.initialize():
            print(f"⏳ Embedding {len(new_items)} new APIs...")
            texts = [self._build_api_text(item) for _, item in new_items]
            embeddings = self.embeddings.embed_batch(texts)

            if embeddings:
                for (item_hash, _), embedding in zip(new_items, embeddings):
                    updated_embeddings[item_hash] = embedding
                print(f"✅ Embedded {len(new_items)} new APIs")

        # Update cache
        cache_data = {
            "items": all_apis,
            "embeddings": updated_embeddings,
        }
        self.cache.save_cache("apis", cache_data)

        self._apis = all_apis
        self._apis_index = None

        return True, all_apis, f"Fetched {len(all_apis)} APIs"

    def _build_api_text(self, api: Dict[str, Any]) -> str:
        """Build searchable text from API."""
        parts = []
        if name := api.get("name"):
            parts.extend([name, name])  # Weight name higher
        if description := api.get("description"):
            parts.append(description)
        if path := api.get("path"):
            parts.append(path)
        if method := api.get("method"):
            parts.append(method)
        return " ".join(parts) or "untitled api"

    def _get_apis_index(self) -> Optional[Any]:
        """Get or build FAISS index for APIs."""
        if self._apis_index is not None:
            return self._apis_index

        cache_data = self.cache.load_cache("apis")
        embeddings_dict = cache_data.get("embeddings", {})

        if not embeddings_dict:
            return None

        embeddings = []
        for api in self._apis:
            item_hash = self.cache.get_item_hash(api)
            if item_hash in embeddings_dict:
                embeddings.append(embeddings_dict[item_hash])
            else:
                embeddings.append([0.0] * self.embeddings.dimension)

        self._apis_index = self.embeddings.build_faiss_index(embeddings)
        return self._apis_index

    def search_apis(
        self,
        query: str,
        mode: str = "hybrid",
        top_k: int = 10,
        fuzzy_threshold: float = 0.4,
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Search APIs using semantic and/or fuzzy matching.

        Args:
            query: Search query
            mode: "semantic" (FAISS), "fuzzy" (text match), or "hybrid" (both)
            top_k: Maximum results
            fuzzy_threshold: Minimum fuzzy match score (0-1)

        Returns:
            Tuple of (success, results_with_scores, message)
        """
        if not self._apis:
            success, _, msg = self.fetch_apis()
            if not success:
                return False, [], msg

        results: Dict[str, Tuple[Dict[str, Any], float]] = {}

        # Semantic search
        if mode in ("semantic", "hybrid"):
            if self.embeddings.initialize():
                query_embedding = self.embeddings.embed_text(query)
                if query_embedding:
                    index = self._get_apis_index()
                    if index is not None:
                        matches = self.embeddings.search_index(index, query_embedding, top_k * 2)
                        for idx, score in matches:
                            if 0 <= idx < len(self._apis):
                                api = self._apis[idx]
                                api_id = api.get("id", str(idx))
                                if api_id not in results or score > results[api_id][1]:
                                    results[api_id] = (api, score)

        # Fuzzy search
        if mode in ("fuzzy", "hybrid"):
            query_lower = query.lower()
            for api in self._apis:
                api_id = api.get("id", "")
                name = (api.get("name") or "").lower()
                description = (api.get("description") or "").lower()
                path = (api.get("path") or "").lower()

                # Calculate scores
                name_score = SequenceMatcher(None, query_lower, name).ratio()
                desc_score = SequenceMatcher(None, query_lower, description).ratio() * 0.5
                path_score = SequenceMatcher(None, query_lower, path).ratio() * 0.7

                # Boost substring matches
                if query_lower in name:
                    name_score = max(name_score, 0.9)
                if query_lower in path:
                    path_score = max(path_score, 0.85)
                if query_lower in description:
                    desc_score = max(desc_score, 0.6)

                score = max(name_score, desc_score, path_score)

                if score >= fuzzy_threshold:
                    if api_id not in results or score > results[api_id][1]:
                        results[api_id] = (api, score)

        # Sort and limit
        sorted_results = sorted(results.values(), key=lambda x: x[1], reverse=True)[:top_k]

        final_results = []
        for api, score in sorted_results:
            api_with_score = dict(api)
            api_with_score["_search_score"] = round(score * 100, 1)
            final_results.append(api_with_score)

        return True, final_results, f"Found {len(final_results)} matching APIs"

    # =========================================================================
    # Convenience Methods
    # =========================================================================

    def get_action_details(self, action_id: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Fetch detailed info for a specific action."""
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/current/"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code}"
            return True, response.json(), "Action details fetched"
        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def get_api_details(self, api_id: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Fetch detailed info for a specific API."""
        url = f"{self.api_endpoint}/v1/tools/apis/{api_id}"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code}"
            return True, response.json(), "API details fetched"
        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def discover_for_requirements(
        self,
        requirements: str,
        include_actions: bool = True,
        include_apis: bool = True,
        tools_only: bool = True,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        """
        Discover relevant actions and APIs for a requirements document.

        Args:
            requirements: Requirements text
            include_actions: Search for matching actions
            include_apis: Search for matching APIs
            tools_only: Only return tool-type actions
            top_k: Results per category

        Returns:
            Dict with 'actions' and 'apis' lists
        """
        result = {"actions": [], "apis": []}

        if include_actions:
            success, actions, _ = self.search_actions(
                requirements, mode="semantic", tools_only=tools_only, top_k=top_k
            )
            if success:
                result["actions"] = actions

        if include_apis:
            success, apis, _ = self.search_apis(
                requirements, mode="semantic", top_k=top_k
            )
            if success:
                result["apis"] = apis

        return result

    # =========================================================================
    # Backward Compatibility / Helper Methods
    # =========================================================================

    def fetch_tools(self) -> Tuple[bool, List[Dict[str, Any]], str]:
        """Alias for fetch_actions with tools_only=True."""
        return self.fetch_actions(tools_only=True)

    def get_tool_details(self, tool_id: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Alias for get_action_details."""
        return self.get_action_details(tool_id)

    def get_tool_context(self, tool_id: str) -> Optional[str]:
        """Get WDL context for a tool (for including in prompts)."""
        success, details, _ = self.get_action_details(tool_id)
        if not success or not details:
            return None

        # Build context string from the action details
        wdl = details.get("wdl") or details.get("definition") or {}
        if isinstance(wdl, str):
            try:
                wdl = json.loads(wdl)
            except json.JSONDecodeError:
                return None

        return json.dumps(wdl, indent=2)

    def semantic_search_tools(
        self, query: str, top_k: int = 5
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """Semantic search for tools only."""
        return self.search_actions(query, mode="semantic", tools_only=True, top_k=top_k)

    def semantic_search_apis(
        self, query: str, top_k: int = 5
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """Semantic search for APIs."""
        return self.search_apis(query, mode="semantic", top_k=top_k)

    def discover_tools_for_requirements(
        self, requirements: str, top_k: int = 5
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Discover tools and APIs for requirements.

        Returns a flat list with _type markers for each item.
        """
        result = self.discover_for_requirements(
            requirements,
            include_actions=True,
            include_apis=True,
            tools_only=True,
            top_k=top_k,
        )

        # Flatten results with type markers
        combined = []
        for action in result.get("actions", []):
            action["_type"] = "tool"
            combined.append(action)
        for api in result.get("apis", []):
            api["_type"] = "api"
            combined.append(api)

        # Sort by score
        combined.sort(key=lambda x: x.get("_search_score", 0), reverse=True)

        return True, combined, f"Found {len(combined)} relevant tools and APIs"

    def display_tools(self, tools: List[Dict[str, Any]], limit: Optional[int] = None) -> None:
        """Display tools in formatted output."""
        display_list = tools[:limit] if limit else tools
        print(f"\n📦 Found {len(tools)} tools:")
        for i, tool in enumerate(display_list, 1):
            title = tool.get("title", "Untitled")
            tool_id = tool.get("id", "")[:20]
            desc = (tool.get("description") or "")[:60]
            score = tool.get("_search_score", "")
            score_str = f" [{score}%]" if score else ""
            print(f"\n  {i}. {title}{score_str}")
            print(f"     ID: {tool_id}...")
            if desc:
                print(f"     {desc}...")

    def display_apis(self, apis: List[Dict[str, Any]], limit: Optional[int] = None) -> None:
        """Display APIs in formatted output."""
        display_list = apis[:limit] if limit else apis
        print(f"\n🌐 Found {len(apis)} APIs:")
        for i, api in enumerate(display_list, 1):
            name = api.get("name", api.get("title", "Untitled"))
            method = api.get("method", "")
            path = api.get("path", "")[:40]
            desc = (api.get("description") or "")[:60]
            score = api.get("_search_score", "")
            score_str = f" [{score}%]" if score else ""
            print(f"\n  {i}. {name}{score_str}")
            print(f"     {method} {path}")
            if desc:
                print(f"     {desc}...")

    def export_search_results_json(self, results: List[Dict[str, Any]]) -> str:
        """Export search results as JSON string."""
        return json.dumps({"count": len(results), "results": results}, indent=2, default=str)


# Convenience function for CLI usage
def get_discovery(env_name: Optional[str] = None) -> Discovery:
    """
    Get Discovery instance for an environment.

    Args:
        env_name: Environment name. If None, uses active environment.

    Returns:
        Configured Discovery instance
    
    Raises:
        ValueError: If no environment is available
    """
    from cli.wdl_common.workspace_manager import WORKSPACES_DIR, get_workspace_manager, DEFAULT_ENV

    manager = get_workspace_manager()
    
    # Determine environment
    env = env_name or manager.active_env or DEFAULT_ENV
    env_path = WORKSPACES_DIR / env

    if not env_path.exists():
        raise ValueError(
            f"Environment not found: {env}. "
            f"Create one with: python cli/workspace.py env create --id {env}"
        )

    return Discovery(env_path=env_path)

