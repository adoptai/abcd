"""Module for semantic search of AdoptAI tools using FAISS"""

import json
from typing import List, Dict, Any, Optional, Tuple
import faiss
import numpy as np
from pathlib import Path
from difflib import SequenceMatcher

# Try to import sentence transformers, fall back gracefully
try:
    from sentence_transformers import SentenceTransformer  # type: ignore
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    SentenceTransformer = None  # type: ignore

from cli.list_tools import ToolManager


class ToolSearcher:
    """Manages semantic search for tools using FAISS"""
    
    def __init__(self, cache_file: str = "tools_cache.json"):
        self.cache_file = cache_file
        self.faiss_index: Optional[faiss.Index] = None
        self.tools: List[Dict[str, Any]] = []
        self.model: Optional[Any] = None
        self.dimension: int = 384  # Default dimension for all-MiniLM-L6-v2
        
    def _initialize_model(self) -> None:
        """Initialize the sentence transformer model"""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise ImportError(
                "sentence-transformers is not installed. "
                "Please install it with: poetry add sentence-transformers"
            )
        
        if self.model is None:
            print("⏳ Loading embedding model (first time may take a moment)...")
            # Using a lightweight, efficient model
            self.model = SentenceTransformer('all-MiniLM-L6-v2')  # type: ignore
            self.dimension = self.model.get_sentence_embedding_dimension()  # type: ignore
            print(f"✅ Model loaded (dimension: {self.dimension})")
    
    def _build_tool_text(self, tool: Dict[str, Any]) -> str:
        """
        Build a searchable text representation of a tool.
        Includes title, description, and tags.
        
        Args:
            tool: Tool dictionary
            
        Returns:
            Combined text for embedding
        """
        text_parts = []
        
        # Add title/name with higher weight (repeat it)
        name = tool.get('title', tool.get('name', ''))
        if name:
            text_parts.extend([name, name])  # Duplicate for emphasis
        
        # Add description
        description = tool.get('description', '')
        if description:
            text_parts.append(description)
        
        # Add tags
        tags = tool.get('tags', [])
        if tags:
            text_parts.extend(tags)
        
        # Add parameter names and descriptions
        parameters = tool.get('parameters', {})
        if isinstance(parameters, dict):
            properties = parameters.get('properties', {})
            for param_name, param_info in properties.items():
                text_parts.append(param_name)
                if isinstance(param_info, dict):
                    param_desc = param_info.get('description', '')
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
    
    def _load_or_fetch_tools(self, bearer_token: Optional[str] = None) -> bool:
        """
        Load tools from cache or fetch from API if needed.
        
        Args:
            bearer_token: Optional bearer token for API authentication
            
        Returns:
            True if tools were loaded successfully
        """
        # Try to load from cache first
        cache_path = Path(self.cache_file)
        if cache_path.exists():
            try:
                with open(cache_path, 'r') as f:
                    self.tools = json.load(f)
                print(f"✅ Loaded {len(self.tools)} tools from cache")
                return True
            except Exception as e:
                print(f"⚠️  Error loading cache: {e}")
        
        # If cache doesn't exist or failed, fetch from API
        if bearer_token:
            print("⏳ Cache not found, fetching tools from API...")
            # Use ToolManager to fetch tools
            manager = ToolManager()
            try:
                self.tools = manager.fetch_tools(bearer_token)
                manager.save_tools_to_file(self.cache_file)
                print(f"✅ Fetched and cached {len(self.tools)} tools")
                return True
            except Exception as e:
                print(f"❌ Error fetching tools: {e}")
                return False
        else:
            print("⚠️  No cached tools found and no bearer token provided")
            print("   Please run 'List Tools' first to fetch and cache tools")
            return False
    
    def build_index(self, bearer_token: Optional[str] = None, force_rebuild: bool = False) -> bool:
        """
        Build or load the FAISS index for tool search.
        
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
        
        # Load or fetch tools
        if not self.tools or force_rebuild:
            if not self._load_or_fetch_tools(bearer_token):
                return False
        
        if not self.tools:
            print("❌ No tools available to index")
            return False
        
        # Initialize the embedding model
        self._initialize_model()
        
        # Build text representations and generate embeddings
        print(f"⏳ Generating embeddings for {len(self.tools)} tools...")
        tool_texts = [self._build_tool_text(tool) for tool in self.tools]
        
        # Generate embeddings using the model
        embeddings = self.model.encode(tool_texts, show_progress_bar=True)  # type: ignore
        embeddings = np.array(embeddings).astype('float32')
        
        # Normalize embeddings for cosine similarity
        faiss.normalize_L2(embeddings)  # type: ignore
        
        # Create FAISS index
        self.faiss_index = faiss.IndexFlatIP(self.dimension)  # Inner product for cosine similarity
        self.faiss_index.add(embeddings)  # type: ignore
        
        print(f"✅ FAISS index built with {self.faiss_index.ntotal} tools")
        return True
    
    def search(
        self, 
        query: str, 
        top_k: int = 10,
        bearer_token: Optional[str] = None,
        use_hybrid: bool = True
    ) -> List[Tuple[Dict[str, Any], float]]:
        """
        Search for tools using hybrid search (semantic + fuzzy matching).
        
        Args:
            query: Search query
            top_k: Number of results to return
            bearer_token: Optional bearer token (for building index if needed)
            use_hybrid: If True, combine semantic and fuzzy search
            
        Returns:
            List of (tool, similarity_score) tuples
        """
        # Build index if not already built
        if self.faiss_index is None:
            print("🔧 FAISS index not found, building it now...")
            if not self.build_index(bearer_token):
                return []
        
        if not query.strip():
            print("⚠️  Empty query provided")
            return []
        
        if use_hybrid and SENTENCE_TRANSFORMERS_AVAILABLE:
            # Hybrid search: combine semantic and fuzzy matching
            # Initialize model if needed
            self._initialize_model()
            
            # Generate query embedding
            query_embedding = self.model.encode([query])  # type: ignore
            query_embedding = np.array(query_embedding).astype('float32')
            
            # Normalize for cosine similarity
            faiss.normalize_L2(query_embedding)  # type: ignore
            
            # Search in FAISS - get more results than needed for hybrid scoring
            search_k = min(top_k * 3, len(self.tools))  # Get 3x results for hybrid scoring
            scores, indices = self.faiss_index.search(query_embedding, search_k)  # type: ignore
            
            # Combine semantic scores with fuzzy matching scores
            hybrid_results = []
            for idx, semantic_score in zip(indices[0], scores[0]):
                if idx < len(self.tools):
                    tool = self.tools[idx]
                    
                    # Build searchable text for fuzzy matching
                    searchable_text = self._build_tool_text(tool)
                    
                    # Calculate fuzzy match score
                    fuzzy_score = self._fuzzy_match_score(query, searchable_text)
                    
                    # Also check individual fields (title, description)
                    title = tool.get('title', tool.get('name', ''))
                    description = tool.get('description', '')
                    
                    # Calculate fuzzy scores for individual fields
                    title_fuzzy = self._fuzzy_match_score(query, title) if title else 0.0
                    desc_fuzzy = self._fuzzy_match_score(query, description) if description else 0.0
                    
                    # Weighted fuzzy score (title is more important)
                    weighted_fuzzy = (title_fuzzy * 0.6 + desc_fuzzy * 0.4)
                    
                    # Combine semantic and fuzzy scores
                    # Normalize semantic score (cosine similarity is typically -1 to 1, but normalized to 0-1)
                    normalized_semantic = max(0.0, float(semantic_score))
                    
                    # Hybrid score: 60% semantic, 40% fuzzy
                    hybrid_score = (normalized_semantic * 0.6) + (weighted_fuzzy * 0.4)
                    
                    hybrid_results.append((tool, hybrid_score))
            
            # Sort by hybrid score and return top_k
            hybrid_results.sort(key=lambda x: x[1], reverse=True)
            return hybrid_results[:top_k]
        else:
            # Fallback to fuzzy-only search if semantic search not available
            results = []
            for tool in self.tools:
                searchable_text = self._build_tool_text(tool)
                fuzzy_score = self._fuzzy_match_score(query, searchable_text)
                
                if fuzzy_score > 0.1:  # Threshold for relevance
                    results.append((tool, fuzzy_score))
            
            # Sort by score and return top_k
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_k]
    
    def display_search_results(self, results: List[Tuple[Dict[str, Any], float]]) -> None:
        """
        Display search results in a formatted way.
        
        Args:
            results: List of (tool, score) tuples
        """
        if not results:
            print("\n❌ No results found")
            return
        
        print("\n" + "=" * 80)
        print(f"🔍 SEARCH RESULTS - Found {len(results)} matching tool(s)")
        print("=" * 80)
        
        for idx, (tool, score) in enumerate(results, 1):
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
            print(f"   ID: {tool.get('id', 'N/A')}")
            print(f"   Name: {tool.get('title', tool.get('name', 'N/A'))}")
            print(f"   Description: {tool.get('description', 'N/A')}")
            
            # Display tags if available
            if tool.get('tags'):
                print(f"   Tags: {', '.join(tool.get('tags', []))}")
            
            # Display parameter count
            if tool.get('parameters'):
                params = tool.get('parameters', {})
                if isinstance(params, dict):
                    print(f"   Parameters: {len(params.get('properties', {}))} parameter(s)")
            
            print("-" * 80)
        
        print("=" * 80)


def search_tools_interactive(bearer_token: str) -> None:
    """
    Interactive tool search with semantic similarity.
    
    Args:
        bearer_token: The authentication bearer token
    """
    print("\n" + "=" * 80)
    print("🔍 SEMANTIC TOOL SEARCH")
    print("=" * 80)
    
    searcher = ToolSearcher()
    
    try:
        # Build the FAISS index (will use cache if available)
        print("\n⏳ Preparing search index...")
        if not searcher.build_index(bearer_token):
            print("\n❌ Failed to build search index")
            print("   Please ensure you have tools available or check your API credentials")
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
    print("This module provides semantic search functionality for AdoptAI tools.")
    print("It is designed to be imported and used by tool_builder.py")
    print("\nTo use this feature, run: poetry run tool-builder")

