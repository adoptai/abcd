"""Module to list and manage AdoptAI tools"""

import os
import requests
from typing import List, Dict, Any, Optional
import faiss
import numpy as np
import json


class ToolManager:
    """Manages tool listing, pagination, and FAISS storage"""
    
    def __init__(self):
        self.tools: List[Dict[str, Any]] = []
        self.faiss_index: Optional[faiss.Index] = None
        self.tool_embeddings: List[np.ndarray] = []
        
    def fetch_tools(self, bearer_token: str, api_endpoint: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch all tools from the AdoptAI API with execution_type=TOOL.
        
        Args:
            bearer_token: The authentication bearer token
            api_endpoint: The Adopt API endpoint (defaults to ADOPT_API_ENDPOINT env var)
            
        Returns:
            List of tools (capabilities)
            
        Raises:
            ValueError: If API request fails
        """
        api_endpoint = api_endpoint or os.getenv('ADOPT_API_ENDPOINT', 'https://connect.adopt.ai')
        
        url = f"{api_endpoint}/v1/actions/list"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }
        params = {"execution_type": "TOOL"}
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
                raise ValueError(
                    f"Failed to list tools. Status code: {response.status_code}\n"
                    f"Response: {response.text}"
                )
            
            json_response = response.json()
            
            if "capabilities" not in json_response:
                raise ValueError(f"Expected 'capabilities' in response, got: {json_response}")
            
            if not isinstance(json_response["capabilities"], list):
                raise ValueError(
                    f"Expected 'capabilities' to be a list, got: {type(json_response['capabilities'])}"
                )
            
            self.tools = json_response["capabilities"]
            return self.tools
            
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Network error while fetching tools: {e}")
    
    def display_tools_paginated(self, page_size: int = 10) -> None:
        """
        Display tools one page at a time with pagination.
        
        Args:
            page_size: Number of tools to display per page (default: 10)
        """
        if not self.tools:
            print("\n⚠️  No tools found. Please fetch tools first.")
            return
        
        total_tools = len(self.tools)
        total_pages = (total_tools + page_size - 1) // page_size
        current_page = 0
        
        while True:
            start_idx = current_page * page_size
            end_idx = min(start_idx + page_size, total_tools)
            page_tools = self.tools[start_idx:end_idx]
            
            # Clear screen-like effect with separator
            print("\n" + "=" * 80)
            print(f"📋 TOOLS LIST - Page {current_page + 1} of {total_pages}")
            print(f"   Showing tools {start_idx + 1}-{end_idx} of {total_tools}")
            print("=" * 80)
            
            # Display tools on current page
            for idx, tool in enumerate(page_tools, start=start_idx + 1):
                self._display_tool(idx, tool)
            
            print("=" * 80)
            
            # Navigation options
            nav_options = []
            if current_page > 0:
                nav_options.append("[P]revious")
            if current_page < total_pages - 1:
                nav_options.append("[N]ext")
            nav_options.append("[Q]uit")
            
            print(f"Navigation: {' | '.join(nav_options)}")
            print("=" * 80)
            
            # Get user input
            user_input = input("\n👉 Choose an option: ").strip().upper()
            
            if user_input == 'Q':
                break
            elif user_input == 'N' and current_page < total_pages - 1:
                current_page += 1
            elif user_input == 'P' and current_page > 0:
                current_page -= 1
            else:
                print("⚠️  Invalid option. Please try again.")
                input("Press Enter to continue...")
    
    def _display_tool(self, index: int, tool: Dict[str, Any]) -> None:
        """
        Display a single tool with formatted output.
        
        Args:
            index: The tool number
            tool: The tool data dictionary
        """
        print(f"\n🔧 Tool #{index}")
        print(f"   ID: {tool.get('id', 'N/A')}")
        print(f"   Name: {tool.get('title', 'N/A')}")
        print(f"   Description: {tool.get('description', 'N/A')}")
        
        # Display additional metadata if available
        if tool.get('tags'):
            print(f"   Tags: {', '.join(tool.get('tags', []))}")
        
        if tool.get('parameters'):
            params = tool.get('parameters', {})
            if isinstance(params, dict):
                print(f"   Parameters: {len(params.get('properties', {}))} parameter(s)")
        
        print("-" * 80)
    
    def store_in_faiss(self) -> None:
        """
        Store tools in FAISS index for semantic search.
        
        This creates a simple text-based representation of each tool
        and prepares it for FAISS indexing. The actual embedding generation
        would require a model, so this creates a placeholder structure.
        
        Note: This prepares the data structure. The actual embedding and
        indexing will be implemented when the search functionality is added.
        """
        if not self.tools:
            print("\n⚠️  No tools to store. Please fetch tools first.")
            return
        
        # Create text representations for each tool
        tool_texts = []
        for tool in self.tools:
            # Combine tool information into searchable text
            text_parts = [
                tool.get('name', ''),
                tool.get('description', ''),
                ' '.join(tool.get('tags', [])),
            ]
            tool_text = ' '.join(filter(None, text_parts))
            tool_texts.append(tool_text)
        
        # For now, we'll create a simple dimension-based placeholder
        # In a real implementation, you would use sentence transformers or similar
        # to create actual embeddings
        
        # Create a simple FAISS index (placeholder with random embeddings for now)
        dimension = 384  # Common dimension for sentence transformers
        self.faiss_index = faiss.IndexFlatL2(dimension)
        
        # Store the tool texts for later use in search
        # This would normally be converted to embeddings
        print(f"\n✅ Prepared {len(self.tools)} tools for FAISS storage")
        print(f"   Index dimension: {dimension}")
        print("   Ready for semantic search implementation")
    
    def save_tools_to_file(self, filename: str = "tools_cache.json") -> None:
        """
        Save fetched tools to a JSON file for caching.
        
        Args:
            filename: The filename to save to (default: tools_cache.json)
        """
        if not self.tools:
            print("\n⚠️  No tools to save.")
            return
        
        try:
            with open(filename, 'w') as f:
                json.dump(self.tools, f, indent=2)
            print(f"\n✅ Saved {len(self.tools)} tools to {filename}")
        except Exception as e:
            print(f"\n❌ Error saving tools: {e}")
    
    def load_tools_from_file(self, filename: str = "tools_cache.json") -> bool:
        """
        Load tools from a cached JSON file.
        
        Args:
            filename: The filename to load from (default: tools_cache.json)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with open(filename, 'r') as f:
                self.tools = json.load(f)
            print(f"\n✅ Loaded {len(self.tools)} tools from {filename}")
            return True
        except FileNotFoundError:
            print(f"\n⚠️  Cache file {filename} not found.")
            return False
        except Exception as e:
            print(f"\n❌ Error loading tools: {e}")
            return False


def list_tools_interactive(bearer_token: str) -> None:
    """
    Main function to list tools interactively with pagination and FAISS storage.
    
    Args:
        bearer_token: The authentication bearer token
    """
    print("\n" + "=" * 80)
    print("📋 FETCHING TOOLS FROM ADOPTAI")
    print("=" * 80)
    
    manager = ToolManager()
    
    try:
        # Fetch tools from API
        print("⏳ Fetching tools...")
        tools = manager.fetch_tools(bearer_token)
        print(f"✅ Successfully fetched {len(tools)} tool(s)")
        
        if not tools:
            print("\n⚠️  No tools found in your AdoptAI instance.")
            print("   Please create some tools first.")
            return
        
        # Store in FAISS for future search capability
        print("\n⏳ Preparing FAISS index for search...")
        manager.store_in_faiss()
        
        # Save to cache file
        manager.save_tools_to_file()
        
        # Display tools with pagination
        print("\n✅ Ready to display tools!")
        input("Press Enter to continue...")
        manager.display_tools_paginated(page_size=10)
        
    except ValueError as e:
        print(f"\n❌ Error: {e}")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        raise

