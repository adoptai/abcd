#!/usr/bin/env python3
"""
Tool Discovery - Fetch and list available tools/APIs from AdoptAI.

Integrates with existing Tool Builder infrastructure:
- Uses ToolManager from list_tools.py for fetching tools
- Uses APIManager from create_tools.py for fetching APIs
- Uses ToolSearcher from search_tools.py for semantic search
- Provides unified interface for the WDL action generator

This allows the WDL generator (and Cursor) to autonomously discover
and select relevant tools/APIs based on requirements.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cli.auth import get_bearer_token

# Try to import semantic search - may not be available
try:
    from cli.search_tools import ToolSearcher
    SEMANTIC_SEARCH_AVAILABLE = True
except ImportError:
    ToolSearcher = None  # type: ignore
    SEMANTIC_SEARCH_AVAILABLE = False


class ToolDiscovery:
    """
    Discover available tools and APIs from AdoptAI platform.

    Provides:
    - List all available tools (existing actions)
    - List all available APIs (for REST operations)
    - Fetch detailed info for specific tools/APIs
    - Search tools by name/description
    """

    def __init__(self, bearer_token: Optional[str] = None, verbose: bool = False) -> None:
        """
        Initialize tool discovery.

        Args:
            bearer_token: Optional pre-fetched token. Will fetch if not provided.
            verbose: If True, print function entry/exit messages for debugging.
        """
        self._verbose = verbose
        self._verbose_print("__init__", "ENTER")
        self._bearer_token = bearer_token
        self.api_endpoint = os.getenv(
            "ADOPT_API_ENDPOINT", "https://connect.adopt.ai"
        )
        self.actions_endpoint = os.getenv(
            "ADAPT_ACTIONS_ENDPOINT", "https://api.adopt.ai"
        )
        self._tools_cache: List[Dict[str, Any]] = []
        self._apis_cache: List[Dict[str, Any]] = []
        self._verbose_print("__init__", "EXIT")

    def _verbose_print(self, func_name: str, stage: str, extra: str = "") -> None:
        """Print verbose message if verbose mode is enabled."""
        if self._verbose:
            msg = f"[VERBOSE] ToolDiscovery.{func_name}: {stage}"
            if extra:
                msg += f" - {extra}"
            print(msg, file=sys.stderr)

    @property
    def bearer_token(self) -> str:
        """Get bearer token, fetching if needed."""
        self._verbose_print("bearer_token", "ENTER")
        if self._bearer_token is None:
            self._verbose_print("bearer_token", "fetching token")
            self._bearer_token = get_bearer_token()
        self._verbose_print("bearer_token", "EXIT")
        return self._bearer_token

    @property
    def headers(self) -> Dict[str, str]:
        """Get standard headers for API calls."""
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

    # =========================================================================
    # Tool Discovery (Existing Actions)
    # =========================================================================

    def fetch_tools(
        self, force_refresh: bool = False, execution_type: str = "TOOL"
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Fetch all available tools/actions with specified execution_type.

        Args:
            force_refresh: If True, bypass cache and fetch fresh
            execution_type: Type of actions to fetch. Options:
                - "TOOL" (default): Regular tools
                - "DEFAULT": All actions with default execution mode
                - "WORKFLOW": Workflow-type actions

        Returns:
            Tuple of (success, tools_list, message)
        """
        self._verbose_print("fetch_tools", "ENTER", f"force_refresh={force_refresh}, execution_type={execution_type}")
        
        # Use different cache files for different execution types
        cache_suffix = execution_type.lower().replace(" ", "_")
        cache_file = Path(f"tools_cache_{cache_suffix}.json") if execution_type != "TOOL" else Path("tools_cache.json")
        
        if cache_file.exists() and not force_refresh:
            self._verbose_print("fetch_tools", "checking file cache")
            try:
                with open(cache_file, 'r') as f:
                    cached_tools = json.load(f)
                    if cached_tools:
                        # Only use memory cache for default TOOL type
                        if execution_type == "TOOL":
                            self._tools_cache = cached_tools
                        self._verbose_print("fetch_tools", "EXIT", "loaded from file cache")
                        return True, cached_tools, f"Loaded {len(cached_tools)} items from cache"
            except Exception:
                pass  # Fall through to fetch from API
        
        # Only use memory cache for default TOOL type
        if execution_type == "TOOL" and self._tools_cache and not force_refresh:
            self._verbose_print("fetch_tools", "EXIT", "using memory cache")
            return True, self._tools_cache, f"Cached {len(self._tools_cache)} tools"

        self._verbose_print("fetch_tools", "fetching from API")
        url = f"{self.api_endpoint}/v1/actions/list"
        params = {"execution_type": execution_type}

        try:
            self._verbose_print("fetch_tools", "making HTTP request", f"url={url}")
            response = requests.get(
                url, headers=self.headers, params=params, timeout=30
            )
            self._verbose_print("fetch_tools", "HTTP response received", f"status={response.status_code}")

            if response.status_code != 200:
                self._verbose_print("fetch_tools", "EXIT", "request failed")
                return False, [], f"Failed: {response.status_code} - {response.text}"

            self._verbose_print("fetch_tools", "parsing JSON response")
            data = response.json()
            tools = data.get("capabilities", [])
            
            # Only update memory cache for default TOOL type
            if execution_type == "TOOL":
                self._tools_cache = tools

            # Save to cache file
            self._verbose_print("fetch_tools", "saving to file cache")
            try:
                with open(cache_file, 'w') as f:
                    json.dump(tools, f, indent=2)
            except Exception as e:
                print(f"⚠️  Warning: Could not save cache: {e}", file=sys.stderr)

            # Use appropriate label based on execution_type
            if execution_type == "TOOL":
                item_label = "tools"
            elif execution_type == "WORKFLOW":
                item_label = "workflows"
            elif execution_type == "DEFAULT":
                item_label = "actions"
            else:
                item_label = f"{execution_type} items"
            self._verbose_print("fetch_tools", "EXIT", f"fetched {len(tools)} {item_label}")
            return True, tools, f"Fetched {len(tools)} {item_label}"

        except requests.exceptions.RequestException as e:
            self._verbose_print("fetch_tools", "EXIT", f"network error: {e}")
            return False, [], f"Network error: {e}"

    def get_tool_details(
        self, tool_id: str
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Fetch detailed information for a specific tool.

        Args:
            tool_id: The tool/action ID

        Returns:
            Tuple of (success, tool_data, message)
        """
        self._verbose_print("get_tool_details", "ENTER", f"tool_id={tool_id}")
        url = f"{self.actions_endpoint}/v1/actions/{tool_id}/current/"

        try:
            self._verbose_print("get_tool_details", "making HTTP request", f"url={url}")
            response = requests.get(url, headers=self.headers, timeout=30)
            self._verbose_print("get_tool_details", "HTTP response received", f"status={response.status_code}")

            if response.status_code != 200:
                self._verbose_print("get_tool_details", "EXIT", "request failed")
                return False, None, f"Failed: {response.status_code} - {response.text}"

            self._verbose_print("get_tool_details", "EXIT", "success")
            return True, response.json(), "Tool details fetched"

        except requests.exceptions.RequestException as e:
            self._verbose_print("get_tool_details", "EXIT", f"network error: {e}")
            return False, None, f"Network error: {e}"

    def search_tools(
        self, query: str, limit: int = 10
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Search tools by name/description.

        Args:
            query: Search query
            limit: Maximum results to return

        Returns:
            Tuple of (success, matching_tools, message)
        """
        self._verbose_print("search_tools", "ENTER", f"query={query}, limit={limit}")
        # Ensure tools are fetched
        success, tools, msg = self.fetch_tools()
        if not success:
            self._verbose_print("search_tools", "EXIT", "fetch_tools failed")
            return False, [], msg

        # Simple text-based search (could be enhanced with semantic search)
        self._verbose_print("search_tools", "performing text search")
        query_lower = query.lower()
        matches = []

        for tool in tools:
            title = (tool.get("title") or "").lower()
            description = (tool.get("description") or "").lower()

            if query_lower in title or query_lower in description:
                matches.append(tool)

            if len(matches) >= limit:
                break

        self._verbose_print("search_tools", "EXIT", f"found {len(matches)} matches")
        return True, matches, f"Found {len(matches)} matching tools"

    # =========================================================================
    # API Discovery (Available APIs for REST operations)
    # =========================================================================

    def fetch_apis(
        self, page_size: int = 50, force_refresh: bool = False
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Fetch all available APIs from the platform.

        Args:
            page_size: Number of APIs per page
            force_refresh: If True, bypass cache

        Returns:
            Tuple of (success, apis_list, message)
        """
        self._verbose_print("fetch_apis", "ENTER", f"page_size={page_size}, force_refresh={force_refresh}")
        # Try to load from cache file first
        cache_file = Path("apis_cache.json")
        if cache_file.exists() and not force_refresh:
            self._verbose_print("fetch_apis", "checking file cache")
            try:
                with open(cache_file, 'r') as f:
                    cached_apis = json.load(f)
                    if cached_apis:
                        self._apis_cache = cached_apis
                        self._verbose_print("fetch_apis", "EXIT", "loaded from file cache")
                        return True, self._apis_cache, f"Loaded {len(self._apis_cache)} APIs from cache"
            except Exception:
                pass  # Fall through to fetch from API
        
        if self._apis_cache and not force_refresh:
            self._verbose_print("fetch_apis", "EXIT", "using memory cache")
            return True, self._apis_cache, f"Cached {len(self._apis_cache)} APIs"

        self._verbose_print("fetch_apis", "fetching from API")
        url = f"{self.api_endpoint}/v1/tools/apis"
        all_apis: List[Dict[str, Any]] = []
        page = 1

        try:
            while True:
                params = {"page": page, "page_size": page_size}
                self._verbose_print("fetch_apis", "making HTTP request", f"page={page}, url={url}")
                response = requests.get(
                    url, headers=self.headers, params=params, timeout=30
                )
                self._verbose_print("fetch_apis", "HTTP response received", f"status={response.status_code}")

                if response.status_code != 200:
                    self._verbose_print("fetch_apis", "EXIT", "request failed")
                    return (
                        False,
                        [],
                        f"Failed: {response.status_code} - {response.text}",
                    )

                self._verbose_print("fetch_apis", "parsing JSON response")
                data = response.json()

                # Handle different response formats
                page_apis = []
                if isinstance(data, list):
                    page_apis = data
                elif isinstance(data, dict):
                    page_apis = data.get("apis", data.get("data", data.get("items", [])))

                self._verbose_print("fetch_apis", "page processed", f"got {len(page_apis)} APIs")
                if not page_apis:
                    break

                all_apis.extend(page_apis)

                # Check if more pages
                has_more = (
                    data.get("has_more", False)
                    if isinstance(data, dict)
                    else len(page_apis) >= page_size
                )
                if not has_more or len(page_apis) < page_size:
                    break

                page += 1

            self._apis_cache = all_apis
            
            # Save to cache file
            self._verbose_print("fetch_apis", "saving to file cache")
            try:
                with open(cache_file, 'w') as f:
                    json.dump(all_apis, f, indent=2)
            except Exception as e:
                print(f"⚠️  Warning: Could not save API cache: {e}", file=sys.stderr)
            
            self._verbose_print("fetch_apis", "EXIT", f"fetched {len(all_apis)} APIs")
            return True, all_apis, f"Fetched {len(all_apis)} APIs"

        except requests.exceptions.RequestException as e:
            self._verbose_print("fetch_apis", "EXIT", f"network error: {e}")
            return False, [], f"Network error: {e}"

    def get_api_details(
        self, api_id: str
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Fetch detailed information for a specific API using the detailed endpoint.
        
        Falls back to apis_cache.json if remote fetch fails.

        Args:
            api_id: The API ID

        Returns:
            Tuple of (success, api_data, message)
        """
        self._verbose_print("get_api_details", "ENTER", f"api_id={api_id}")
        url = f"{self.api_endpoint}/v1/tools/apis-detailed/{api_id}"

        try:
            self._verbose_print("get_api_details", "making HTTP request", f"url={url}")
            response = requests.get(url, headers=self.headers, timeout=30)
            self._verbose_print("get_api_details", "HTTP response received", f"status={response.status_code}")

            if response.status_code == 200:
                self._verbose_print("get_api_details", "EXIT", "success")
                return True, response.json(), "API details fetched"
            
            # Remote fetch failed - try cache fallback
            self._verbose_print("get_api_details", "remote failed, trying cache fallback")
            print(f"   ⚠️  Remote API fetch failed ({response.status_code}), checking cache...", file=sys.stderr)
            
        except requests.exceptions.RequestException as e:
            self._verbose_print("get_api_details", "network error, trying cache fallback", str(e))
            print(f"   ⚠️  Network error fetching API: {e}, checking cache...", file=sys.stderr)
        
        # Fallback: Try to find API in cache
        self._verbose_print("get_api_details", "checking file cache")
        cache_file = Path("apis_cache.json")
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cached_apis = json.load(f)
                    for api in cached_apis:
                        if api.get("id") == api_id:
                            print(f"   ✅ Found API in cache", file=sys.stderr)
                            self._verbose_print("get_api_details", "EXIT", "found in cache")
                            return True, api, "API details loaded from cache"
            except Exception as e:
                print(f"   ⚠️  Could not read cache: {e}", file=sys.stderr)
        
        self._verbose_print("get_api_details", "EXIT", "not found")
        return False, None, f"API not found in remote or cache: {api_id}"

    def semantic_search_apis(
        self, query: str, top_k: int = 5
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Hybrid search for APIs using semantic search + fuzzy matching.

        This is the preferred method for Cursor to discover relevant APIs
        based on natural language requirements. Uses hybrid search combining
        semantic embeddings with fuzzy word matching.

        Args:
            query: Natural language description of needed functionality
            top_k: Number of results to return

        Returns:
            Tuple of (success, matching_apis_with_scores, message)
        """
        self._verbose_print("semantic_search_apis", "ENTER", f"query={query[:50]}..., top_k={top_k}")
        try:
            self._verbose_print("semantic_search_apis", "importing APISearcher")
            from cli.search_apis import APISearcher, FAISS_AVAILABLE, SENTENCE_TRANSFORMERS_AVAILABLE
            
            # Check if dependencies are available
            if not FAISS_AVAILABLE or not SENTENCE_TRANSFORMERS_AVAILABLE:
                # Fall back to text search if semantic search dependencies not available
                self._verbose_print("semantic_search_apis", "falling back to text search (deps not available)")
                return self.search_apis(query, top_k)
        except ImportError:
            # Fall back to text search if semantic search module not available
            self._verbose_print("semantic_search_apis", "falling back to text search (import error)")
            return self.search_apis(query, top_k)

        try:
            self._verbose_print("semantic_search_apis", "creating APISearcher")
            searcher = APISearcher()
            self._verbose_print("semantic_search_apis", "building index")
            if not searcher.build_index(self.bearer_token):
                self._verbose_print("semantic_search_apis", "EXIT", "index build failed")
                return False, [], "Failed to build API search index"

            # Use hybrid search (semantic + fuzzy)
            self._verbose_print("semantic_search_apis", "performing hybrid search")
            results = searcher.search(query, top_k=top_k, bearer_token=self.bearer_token, use_hybrid=True)

            # Format results with scores
            self._verbose_print("semantic_search_apis", "formatting results")
            formatted = []
            for api, score in results:
                api_with_score = dict(api)
                api_with_score["_similarity_score"] = round(score * 100, 1)
                formatted.append(api_with_score)

            self._verbose_print("semantic_search_apis", "EXIT", f"found {len(formatted)} APIs")
            return True, formatted, f"Found {len(formatted)} relevant APIs"

        except Exception as e:
            # Fall back to text search on any error
            self._verbose_print("semantic_search_apis", "falling back to text search (exception)", str(e))
            print(f"⚠️  Hybrid search failed: {e}, falling back to text search", file=sys.stderr)
            return self.search_apis(query, top_k)

    def search_apis(
        self, query: str, limit: int = 10
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Search APIs by name/description/path using simple text matching.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            Tuple of (success, matching_apis, message)
        """
        self._verbose_print("search_apis", "ENTER", f"query={query}, limit={limit}")
        success, apis, msg = self.fetch_apis()
        if not success:
            self._verbose_print("search_apis", "EXIT", "fetch_apis failed")
            return False, [], msg

        # Extract keywords from query
        self._verbose_print("search_apis", "performing text search")
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) > 2]  # Words longer than 2 chars
        
        matches = []
        scored_matches = []

        for api in apis:
            title = (api.get("title") or api.get("name") or "").lower()
            description = (api.get("description") or "").lower()
            
            # Extract all API paths from endpoints
            paths = []
            for endpoint in api.get("endpoints", []):
                path = endpoint.get("path", "")
                if path:
                    paths.append(path.lower())
            paths_text = " ".join(paths)
            
            # Calculate match score based on keyword matches
            score = 0
            matched_words = []
            
            for word in query_words:
                if word in title:
                    score += 3  # Title matches are more important
                    matched_words.append(word)
                elif word in description:
                    score += 1
                    matched_words.append(word)
                elif word in paths_text:
                    score += 2  # Path matches are moderately important
                    matched_words.append(word)
            
            # Also check for full query match (exact substring)
            if query_lower in title:
                score += 5
            elif query_lower in description:
                score += 2
            elif query_lower in paths_text:
                score += 3
            
            if score > 0:
                api_with_score = dict(api)
                api_with_score["_match_score"] = score
                scored_matches.append(api_with_score)

        # Sort by score (highest first)
        scored_matches.sort(key=lambda x: x.get("_match_score", 0), reverse=True)
        
        # Return top matches
        matches = scored_matches[:limit]
        
        # Add similarity score for consistency
        for match in matches:
            match_score = match.get("_match_score", 0)
            # Normalize to 0-100 scale (match_score typically 1-20)
            match["_similarity_score"] = min(match_score * 5, 100)

        self._verbose_print("search_apis", "EXIT", f"found {len(matches)} matches")
        return True, matches, f"Found {len(matches)} matching APIs"

    # =========================================================================
    # Semantic Search (Cursor-friendly)
    # =========================================================================

    def semantic_search_tools(
        self, query: str, top_k: int = 5
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Hybrid search for tools using semantic search + fuzzy matching.

        This is the preferred method for Cursor to discover relevant tools
        based on natural language requirements. Uses hybrid search combining
        semantic embeddings with fuzzy word matching.

        Args:
            query: Natural language description of needed functionality
            top_k: Number of results to return

        Returns:
            Tuple of (success, matching_tools_with_scores, message)
        """
        self._verbose_print("semantic_search_tools", "ENTER", f"query={query[:50]}..., top_k={top_k}")
        if not SEMANTIC_SEARCH_AVAILABLE:
            # Fall back to text search
            self._verbose_print("semantic_search_tools", "falling back to text search (not available)")
            return self.search_tools(query, top_k)

        try:
            self._verbose_print("semantic_search_tools", "creating ToolSearcher")
            searcher = ToolSearcher()
            self._verbose_print("semantic_search_tools", "building index")
            if not searcher.build_index(self.bearer_token):
                self._verbose_print("semantic_search_tools", "EXIT", "index build failed")
                return False, [], "Failed to build search index"

            # Use hybrid search (semantic + fuzzy)
            self._verbose_print("semantic_search_tools", "performing hybrid search")
            results = searcher.search(query, top_k=top_k, bearer_token=self.bearer_token, use_hybrid=True)

            # Format results with scores
            self._verbose_print("semantic_search_tools", "formatting results")
            formatted = []
            for tool, score in results:
                tool_with_score = dict(tool)
                tool_with_score["_similarity_score"] = round(score * 100, 1)
                formatted.append(tool_with_score)

            self._verbose_print("semantic_search_tools", "EXIT", f"found {len(formatted)} tools")
            return True, formatted, f"Found {len(formatted)} relevant tools"

        except Exception as e:
            self._verbose_print("semantic_search_tools", "EXIT", f"error: {e}")
            return False, [], f"Hybrid search error: {e}"

    def discover_tools_for_requirements(
        self, requirements: str, top_k: int = 5
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Discover relevant tools AND APIs based on requirements document.

        Extracts key concepts from requirements and searches for matching tools and APIs.
        Designed for Cursor to call autonomously.

        Args:
            requirements: Full requirements document text
            top_k: Number of results to return per type (tools and APIs)

        Returns:
            Tuple of (success, combined_results_with_type, message)
        """
        self._verbose_print("discover_tools_for_requirements", "ENTER", f"requirements_len={len(requirements)}, top_k={top_k}")
        # Search for tools using semantic search
        self._verbose_print("discover_tools_for_requirements", "searching tools")
        tools_success, tools, tools_msg = self.semantic_search_tools(requirements, top_k)
        
        # Search for APIs using semantic search
        self._verbose_print("discover_tools_for_requirements", "searching APIs")
        apis_success, apis, apis_msg = self.semantic_search_apis(requirements, top_k)
        
        # Combine results with type markers
        self._verbose_print("discover_tools_for_requirements", "combining results")
        combined = []
        
        if tools_success and tools:
            for tool in tools:
                tool_with_type = dict(tool)
                tool_with_type["_type"] = "tool"
                combined.append(tool_with_type)
        
        if apis_success and apis:
            for api in apis:
                api_with_type = dict(api)
                api_with_type["_type"] = "api"
                # Similarity score already set by semantic_search_apis
                if "_similarity_score" not in api_with_type:
                    api_with_type["_similarity_score"] = 0
                combined.append(api_with_type)
        
        # Sort by similarity score (if available) or keep original order
        combined.sort(key=lambda x: x.get("_similarity_score", 0), reverse=True)
        
        # Limit to top_k * 2 (top_k tools + top_k APIs)
        combined = combined[:top_k * 2]
        
        msg_parts = []
        if tools_success:
            msg_parts.append(f"{len([t for t in combined if t.get('_type') == 'tool'])} tools")
        if apis_success:
            msg_parts.append(f"{len([a for a in combined if a.get('_type') == 'api'])} APIs")
        
        message = f"Found {', '.join(msg_parts)}" if msg_parts else "No results found"
        
        self._verbose_print("discover_tools_for_requirements", "EXIT", message)
        return True, combined, message

    def export_search_results_json(
        self, results: List[Dict[str, Any]]
    ) -> str:
        """
        Export search results as JSON for Cursor to parse.

        Args:
            results: List of tools/APIs with similarity scores and type markers

        Returns:
            JSON string of results with separate tools and apis arrays
        """
        self._verbose_print("export_search_results_json", "ENTER", f"results_count={len(results)}")
        tools = [t for t in results if t.get("_type") == "tool"]
        apis = [a for a in results if a.get("_type") == "api"]
        
        export = {
            "count": len(results),
            "tools_count": len(tools),
            "apis_count": len(apis),
            "tools": [
                {
                    "id": t.get("id"),
                    "title": t.get("title"),
                    "description": t.get("description", "")[:200],
                    "similarity": t.get("_similarity_score", 0),
                }
                for t in tools
            ],
            "apis": [
                {
                    "id": a.get("id"),
                    "title": a.get("title") or a.get("name"),
                    "description": a.get("description", "")[:200],
                    "base_url": a.get("base_url", ""),
                    "similarity": a.get("_similarity_score", 0),
                }
                for a in apis
            ],
        }
        self._verbose_print("export_search_results_json", "EXIT")
        return json.dumps(export, indent=2)

    # =========================================================================
    # Display Helpers
    # =========================================================================

    def display_tools(
        self, tools: Optional[List[Dict[str, Any]]] = None, limit: int = 20
    ) -> None:
        """
        Display tools in a formatted table.

        Args:
            tools: List of tools to display (uses cache if None)
            limit: Maximum tools to display
        """
        self._verbose_print("display_tools", "ENTER", f"limit={limit}")
        if tools is None:
            success, tools, msg = self.fetch_tools()
            if not success:
                print(f"❌ {msg}")
                self._verbose_print("display_tools", "EXIT", "fetch failed")
                return

        print("\n" + "=" * 80)
        print(f"🔧 AVAILABLE TOOLS ({len(tools)} total)")
        print("=" * 80)
        print(f"{'ID':<40} {'Title':<40}")
        print("-" * 80)

        for tool in tools[:limit]:
            tool_id = (tool.get("id") or "N/A")[:38]
            title = (tool.get("title") or "Untitled")[:38]
            print(f"{tool_id:<40} {title:<40}")

        if len(tools) > limit:
            print(f"... and {len(tools) - limit} more")

        print("=" * 80)
        self._verbose_print("display_tools", "EXIT")

    def display_apis(
        self, apis: Optional[List[Dict[str, Any]]] = None, limit: Optional[int] = None
    ) -> None:
        """
        Display APIs in a formatted table.

        Args:
            apis: List of APIs to display (uses cache if None)
            limit: Maximum APIs to display (None for no limit, displays all)
        """
        self._verbose_print("display_apis", "ENTER", f"limit={limit}")
        if apis is None:
            success, apis, msg = self.fetch_apis()
            if not success:
                print(f"❌ {msg}")
                self._verbose_print("display_apis", "EXIT", "fetch failed")
                return

        print("\n" + "=" * 80)
        print(f"🔗 AVAILABLE APIs ({len(apis)} total)")
        print("=" * 80)
        print(f"{'ID':<40} {'Title':<40}")
        print("-" * 80)

        # Display all APIs if limit is None, otherwise slice
        if limit is None:
            display_apis = apis
        else:
            display_apis = apis[:limit]
            
        for api in display_apis:
            api_id = (api.get("id") or "N/A")[:38]
            title = (api.get("title") or api.get("name") or "Untitled")[:38]
            print(f"{api_id:<40} {title:<40}")

        # Only show truncation message if limit is set and there are more APIs
        if limit is not None and len(apis) > limit:
            print(f"... and {len(apis) - limit} more")

        print("=" * 80)
        self._verbose_print("display_apis", "EXIT")

    # =========================================================================
    # Export for WDL Generation Context
    # =========================================================================

    def get_tool_context(self, tool_id: str) -> Optional[str]:
        """
        Get tool context formatted for WDL generation.

        Returns a markdown string with tool info for LLM/Cursor.
        """
        self._verbose_print("get_tool_context", "ENTER", f"tool_id={tool_id}")
        success, tool, msg = self.get_tool_details(tool_id)
        if not success or not tool:
            self._verbose_print("get_tool_context", "EXIT", "tool not found")
            return None

        lines = []
        lines.append(f"## Tool: {tool.get('title', 'Untitled')}")
        lines.append(f"**ID**: `{tool_id}`")
        lines.append(f"**Description**: {tool.get('description', 'No description')}")

        # Add WDL if available
        wdl = tool.get("wdl", tool.get("widdle", []))
        if wdl:
            lines.append("\n**Current WDL**:")
            lines.append("```json")
            lines.append(json.dumps(wdl, indent=2))
            lines.append("```")

        # Add parameters/inputs
        params = tool.get("parameters", {})
        if params:
            lines.append("\n**Parameters**:")
            lines.append("```json")
            lines.append(json.dumps(params, indent=2))
            lines.append("```")

        self._verbose_print("get_tool_context", "EXIT")
        return "\n".join(lines)

    def get_api_context(self, api_id: str) -> Optional[str]:
        """
        Get API context formatted for WDL generation.

        Returns a markdown string with API info for LLM/Cursor.
        """
        self._verbose_print("get_api_context", "ENTER", f"api_id={api_id}")
        success, api, msg = self.get_api_details(api_id)
        if not success or not api:
            self._verbose_print("get_api_context", "EXIT", "api not found")
            return None

        lines = []
        lines.append(f"## API: {api.get('title', api.get('name', 'Untitled'))}")
        lines.append(f"**ID**: `{api_id}`")
        lines.append(f"**Base URL**: `{api.get('base_url', 'N/A')}`")
        lines.append(f"**Description**: {api.get('description', 'No description')}")

        # Add endpoints
        endpoints = api.get("endpoints", [])
        if endpoints:
            lines.append(f"\n**Endpoints** ({len(endpoints)} total):")
            for ep in endpoints[:10]:  # Limit to first 10
                method = ep.get("method", "GET")
                path = ep.get("path", "")
                desc = ep.get("description", "")
                lines.append(f"- `{method} {path}`: {desc}")

                # Add parameters
                params = ep.get("parameters", [])
                if params:
                    for p in params[:5]:  # Limit params
                        pname = p.get("name", "")
                        ptype = p.get("type", "")
                        preq = "required" if p.get("required") else "optional"
                        lines.append(f"  - `{pname}` ({ptype}, {preq})")

            if len(endpoints) > 10:
                lines.append(f"- ... and {len(endpoints) - 10} more endpoints")

        self._verbose_print("get_api_context", "EXIT")
        return "\n".join(lines)

