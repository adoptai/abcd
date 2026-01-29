"""Module for semantic search of AdoptAI APIs using FAISS"""

import json
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from difflib import SequenceMatcher

# Try to import FAISS, fall back gracefully
try:
    import faiss
    import numpy as np
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    faiss = None  # type: ignore
    np = None  # type: ignore

# Try to import sentence transformers, fall back gracefully
try:
    from sentence_transformers import SentenceTransformer  # type: ignore
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    SentenceTransformer = None  # type: ignore

from cli.create_tools import APIManager


class APISearcher:
    """Manages semantic search for APIs using FAISS"""
    
    def __init__(self, cache_file: str = "apis_cache.json"):
        self.cache_file = cache_file
        self.faiss_index: Optional[faiss.Index] = None
        self.apis: List[Dict[str, Any]] = []
        self.model: Optional[Any] = None
        self.dimension: int = 384  # Default dimension for all-MiniLM-L6-v2
        
    def _initialize_model(self) -> None:
        """Initialize the sentence transformer model"""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise ImportError(
                "sentence-transformers is not installed. "
                "Please install it with: poetry add sentence-transformers"
            )
        if not FAISS_AVAILABLE:
            raise ImportError(
                "faiss-cpu is not installed. "
                "Please install it with: poetry add faiss-cpu"
            )
        
        if self.model is None:
            print("⏳ Loading embedding model (first time may take a moment)...")
            # Using a lightweight, efficient model
            self.model = SentenceTransformer('all-MiniLM-L6-v2')  # type: ignore
            self.dimension = self.model.get_sentence_embedding_dimension()  # type: ignore
            print(f"✅ Model loaded (dimension: {self.dimension})")
    
    def _build_api_text(self, api: Dict[str, Any]) -> str:
        """
        Build a searchable text representation of an API.
        Includes title, description, and API paths.
        
        Args:
            api: API dictionary
            
        Returns:
            Combined text for embedding
        """
        text_parts = []
        
        # Add title/name with higher weight (repeat it)
        name = api.get('title', api.get('name', ''))
        if name:
            text_parts.extend([name, name])  # Duplicate for emphasis
        
        # Add description
        description = api.get('description', '')
        if description:
            text_parts.append(description)
        
        # Add base URL
        base_url = api.get('base_url', '')
        if base_url:
            text_parts.append(base_url)
        
        # Add endpoint information - including paths for search
        endpoints = api.get('endpoints', [])
        if endpoints:
            for endpoint in endpoints[:10]:  # Limit to first 10 endpoints
                method = endpoint.get('method', '')
                path = endpoint.get('path', '')
                desc = endpoint.get('description', '')
                if method and path:
                    # Add path multiple times for emphasis in search
                    text_parts.append(f"{method} {path}")
                    text_parts.append(path)  # Add path alone too
                if desc:
                    text_parts.append(desc)
                
                # Add parameter names
                params = endpoint.get('parameters', [])
                if params:
                    for param in params[:5]:  # Limit params
                        param_name = param.get('name', '')
                        param_desc = param.get('description', '')
                        if param_name:
                            text_parts.append(param_name)
                        if param_desc:
                            text_parts.append(param_desc)
        
        return ' '.join(filter(None, text_parts))
    
    def _fuzzy_match_score(self, query: str, text: str) -> float:
        """
        Calculate fuzzy matching score between query and text.
        
        Args:
            query: Search query
            text: Text to match against
            
        Returns:
            Score between 0.0 and 1.0
        """
        query_lower = query.lower()
        text_lower = text.lower()
        
        # Use SequenceMatcher for fuzzy matching
        ratio = SequenceMatcher(None, query_lower, text_lower).ratio()
        
        # Also check for word-level matches
        query_words = query_lower.split()
        text_words = text_lower.split()
        
        word_matches = 0
        for q_word in query_words:
            if len(q_word) > 2:  # Only consider words longer than 2 chars
                for t_word in text_words:
                    if q_word in t_word or t_word in q_word:
                        word_matches += 1
                        break
        
        word_score = word_matches / len(query_words) if query_words else 0.0
        
        # Combine ratio and word score
        return max(ratio, word_score * 0.8)
    
    def _load_or_fetch_apis(self, bearer_token: Optional[str] = None) -> bool:
        """
        Load APIs from cache or fetch from API if needed.
        
        Args:
            bearer_token: Optional bearer token for API authentication
            
        Returns:
            True if APIs were loaded successfully
        """
        # Try to load from cache first
        cache_path = Path(self.cache_file)
        if cache_path.exists():
            try:
                with open(cache_path, 'r') as f:
                    self.apis = json.load(f)
                print(f"✅ Loaded {len(self.apis)} APIs from cache")
                return True
            except Exception as e:
                print(f"⚠️  Error loading cache: {e}")
        
        # If cache doesn't exist or failed, fetch from API
        if bearer_token:
            print("⏳ Cache not found, fetching APIs from API...")
            manager = APIManager(bearer_token)
            try:
                if manager.fetch_apis():
                    self.apis = manager.apis
                    # Save to cache
                    with open(cache_path, 'w') as f:
                        json.dump(self.apis, f, indent=2)
                    print(f"✅ Fetched and cached {len(self.apis)} APIs")
                    return True
                else:
                    return False
            except Exception as e:
                print(f"❌ Error fetching APIs: {e}")
                return False
        else:
            print("⚠️  No cached APIs found and no bearer token provided")
            print("   Please run 'List APIs' first to fetch and cache APIs")
            return False
    
    def build_index(self, bearer_token: Optional[str] = None, force_rebuild: bool = False) -> bool:
        """
        Build or load the FAISS index for API search.
        
        Args:
            bearer_token: Optional bearer token for API authentication
            force_rebuild: If True, rebuild the index even if it exists
            
        Returns:
            True if index was built successfully
        """
        # Check if index already exists and we're not forcing a rebuild
        if self.faiss_index is not None and not force_rebuild:
            print("✅ Using existing FAISS index")
            return True
        
        # Load or fetch APIs
        if not self.apis or force_rebuild:
            if not self._load_or_fetch_apis(bearer_token):
                return False
        
        if not self.apis:
            print("❌ No APIs available to index")
            return False
        
        # Initialize the embedding model
        self._initialize_model()
        
        # Build text representations and generate embeddings
        print(f"⏳ Generating embeddings for {len(self.apis)} APIs...")
        api_texts = [self._build_api_text(api) for api in self.apis]
        
        # Generate embeddings using the model
        embeddings = self.model.encode(api_texts, show_progress_bar=True)  # type: ignore
        embeddings = np.array(embeddings).astype('float32')  # type: ignore
        
        # Normalize embeddings for cosine similarity
        faiss.normalize_L2(embeddings)  # type: ignore
        
        # Create FAISS index
        self.faiss_index = faiss.IndexFlatIP(self.dimension)  # type: ignore  # Inner product for cosine similarity
        self.faiss_index.add(embeddings)  # type: ignore
        
        print(f"✅ FAISS index built with {self.faiss_index.ntotal} APIs")
        return True
    
    def search(
        self, 
        query: str, 
        top_k: int = 10,
        bearer_token: Optional[str] = None,
        use_hybrid: bool = True
    ) -> List[Tuple[Dict[str, Any], float]]:
        """
        Search for APIs using hybrid search (semantic + fuzzy matching).
        
        Args:
            query: Search query
            top_k: Number of results to return
            bearer_token: Optional bearer token (for building index if needed)
            use_hybrid: If True, combine semantic and fuzzy search
            
        Returns:
            List of (api, similarity_score) tuples
        """
        # Build index if not already built
        if self.faiss_index is None:
            print("🔧 FAISS index not found, building it now...")
            if not self.build_index(bearer_token):
                return []
        
        if not query.strip():
            print("⚠️  Empty query provided")
            return []
        
        if use_hybrid and FAISS_AVAILABLE and SENTENCE_TRANSFORMERS_AVAILABLE:
            # Hybrid search: combine semantic and fuzzy matching
            # Initialize model if needed
            self._initialize_model()
            
            # Generate query embedding
            query_embedding = self.model.encode([query])  # type: ignore
            query_embedding = np.array(query_embedding).astype('float32')  # type: ignore
            
            # Normalize for cosine similarity
            faiss.normalize_L2(query_embedding)  # type: ignore
            
            # Search in FAISS - get more results than needed for hybrid scoring
            search_k = min(top_k * 3, len(self.apis))  # Get 3x results for hybrid scoring
            scores, indices = self.faiss_index.search(query_embedding, search_k)  # type: ignore
            
            # Combine semantic scores with fuzzy matching scores
            hybrid_results = []
            for idx, semantic_score in zip(indices[0], scores[0]):
                if idx < len(self.apis):
                    api = self.apis[idx]
                    
                    # Build searchable text for fuzzy matching
                    searchable_text = self._build_api_text(api)
                    
                    # Calculate fuzzy match score
                    fuzzy_score = self._fuzzy_match_score(query, searchable_text)
                    
                    # Also check individual fields (title, description, paths)
                    title = api.get('title', api.get('name', ''))
                    description = api.get('description', '')
                    
                    # Get all paths from endpoints
                    paths = []
                    for endpoint in api.get('endpoints', []):
                        path = endpoint.get('path', '')
                        if path:
                            paths.append(path)
                    paths_text = ' '.join(paths)
                    
                    # Calculate fuzzy scores for individual fields
                    title_fuzzy = self._fuzzy_match_score(query, title) if title else 0.0
                    desc_fuzzy = self._fuzzy_match_score(query, description) if description else 0.0
                    paths_fuzzy = self._fuzzy_match_score(query, paths_text) if paths_text else 0.0
                    
                    # Weighted fuzzy score (title and paths are more important)
                    weighted_fuzzy = (title_fuzzy * 0.4 + desc_fuzzy * 0.3 + paths_fuzzy * 0.3)
                    
                    # Combine semantic and fuzzy scores
                    # Normalize semantic score (cosine similarity is typically -1 to 1, but normalized to 0-1)
                    normalized_semantic = max(0.0, float(semantic_score))
                    
                    # Hybrid score: 60% semantic, 40% fuzzy
                    hybrid_score = (normalized_semantic * 0.6) + (weighted_fuzzy * 0.4)
                    
                    hybrid_results.append((api, hybrid_score))
            
            # Sort by hybrid score and return top_k
            hybrid_results.sort(key=lambda x: x[1], reverse=True)
            return hybrid_results[:top_k]
        else:
            # Fallback to fuzzy-only search if semantic search not available
            results = []
            for api in self.apis:
                searchable_text = self._build_api_text(api)
                fuzzy_score = self._fuzzy_match_score(query, searchable_text)
                
                if fuzzy_score > 0.1:  # Threshold for relevance
                    results.append((api, fuzzy_score))
            
            # Sort by score and return top_k
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_k]
    
    def display_search_results(self, results: List[Tuple[Dict[str, Any], float]]) -> None:
        """
        Display search results in a formatted way.
        
        Args:
            results: List of (api, score) tuples
        """
        if not results:
            print("\n❌ No results found")
            return
        
        print("\n" + "=" * 80)
        print(f"🔍 SEARCH RESULTS - Found {len(results)} matching API(s)")
        print("=" * 80)
        
        for idx, (api, score) in enumerate(results, 1):
            # Convert score to percentage (0-100)
            similarity_pct = score * 100
            
            # Visual indicator based on similarity
            if similarity_pct >= 80:
                indicator = "🟢"
            elif similarity_pct >= 60:
                indicator = "🟡"
            else:
                indicator = "🔴"
            
            print(f"\n{indicator} Result #{idx} - Similarity: {similarity_pct:.1f}%")
            print(f"   ID: {api.get('id', 'N/A')}")
            print(f"   Name: {api.get('title', api.get('name', 'N/A'))}")
            print(f"   Description: {api.get('description', 'N/A')[:100]}...")
            print(f"   Base URL: {api.get('base_url', 'N/A')}")
            
            # Display endpoint count
            endpoints = api.get('endpoints', [])
            if endpoints:
                print(f"   Endpoints: {len(endpoints)} endpoint(s)")
            
            print("-" * 80)
        
        print("=" * 80)


def search_apis_interactive(bearer_token: str) -> None:
    """
    Interactive API search with semantic similarity.
    
    Args:
        bearer_token: The authentication bearer token
    """
    print("\n" + "=" * 80)
    print("🔍 SEMANTIC API SEARCH")
    print("=" * 80)
    
    searcher = APISearcher()
    
    try:
        # Build the FAISS index (will use cache if available)
        print("\n⏳ Preparing search index...")
        if not searcher.build_index(bearer_token):
            print("\n❌ Failed to build search index")
            print("   Please ensure you have APIs available or check your API credentials")
            return
        
        # Search loop
        while True:
            print("\n" + "=" * 80)
            query = input("🔎 Enter search query (or 'quit' to exit): ").strip()
            
            if query.lower() in ('quit', 'q', 'exit'):
                print("\n👋 Exiting search...")
                break
            
            if not query:
                print("⚠️  Please enter a search query")
                continue
            
            # Get number of results
            try:
                top_k_input = input("   How many results? (default: 10): ").strip()
                top_k = int(top_k_input) if top_k_input else 10
                top_k = max(1, min(top_k, 50))  # Limit between 1 and 50
            except ValueError:
                print("⚠️  Invalid number, using default (10)")
                top_k = 10
            
            # Perform search
            print(f"\n⏳ Searching for: '{query}'...")
            results = searcher.search(query, top_k=top_k, bearer_token=bearer_token)
            
            # Display results
            searcher.display_search_results(results)
            
            # Continue or exit
            continue_search = input("\n🔄 Search again? (y/n): ").strip().lower()
            if continue_search not in ('y', 'yes', ''):
                print("\n👋 Exiting search...")
                break
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Search interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        raise


if __name__ == "__main__":
    # For testing purposes
    print("This module provides semantic search functionality for AdoptAI APIs.")
    print("It is designed to be imported and used by tool_builder.py")
    print("\nTo use this feature, run: poetry run tool-builder")

