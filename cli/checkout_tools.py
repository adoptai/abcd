#!/usr/bin/env python3
"""
Checkout Tools - Download and store tool definitions to agents
"""

import os
import json
import requests
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

from cli.agents import select_agent_interactive, load_agents, save_agents


class ToolCheckoutManager:
    """Manages checking out tools from AdoptAI and storing them locally"""
    
    def __init__(self, bearer_token: str, actions_endpoint: Optional[str] = None):
        self.bearer_token = bearer_token
        # Use api.adopt.ai for fetching current tool details
        self.actions_endpoint = actions_endpoint or os.getenv('ADOPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    def fetch_tool_details(self, tool_id: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Fetch current tool details from the AdoptAI API.
        
        Args:
            tool_id: The tool/action ID to fetch
            
        Returns:
            Tuple of (success: bool, tool_data: dict or None, message: str)
        """
        url = f"{self.actions_endpoint}/v1/actions/{tool_id}/current/"
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json"
        }
        
        try:
            print(f"   ⏳ Fetching tool details for ID: {tool_id}")
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 404:
                return False, None, f"Tool not found: {tool_id}"
            elif response.status_code != 200:
                error_msg = f"Failed to fetch tool. Status code: {response.status_code}\n   Response: {response.text}"
                return False, None, error_msg
            
            tool_data = response.json()
            print("   ✅ Tool details fetched successfully")
            
            return True, tool_data, "Tool fetched successfully"
            
        except requests.exceptions.RequestException as e:
            return False, None, f"Network error while fetching tool: {e}"
    
    def save_tool_to_agent(
        self, 
        agent_name: str, 
        agent_path: str, 
        tool_data: Dict[str, Any],
        tool_id: str
    ) -> bool:
        """
        Save tool data to the agent folder.
        
        Args:
            agent_name: Name of the agent
            agent_path: Path to the agent directory
            tool_data: Tool data from API response
            tool_id: The tool ID (used for filename)
            
        Returns:
            True if saved successfully
        """
        try:
            # Create tools directory if it doesn't exist
            tools_dir = Path(agent_path) / "tools"
            tools_dir.mkdir(parents=True, exist_ok=True)
            
            # Use tool ID as filename
            safe_filename = f"{tool_id}.json"
            
            # Save the tool data
            tool_file = tools_dir / safe_filename
            with open(tool_file, 'w') as f:
                json.dump(tool_data, f, indent=2)
            
            print(f"   💾 Tool saved to: {tool_file}")
            
            # Update agent metadata
            agents = load_agents()
            if agent_name in agents:
                # Count tools in the agent
                tool_files = list(tools_dir.glob("*.json"))
                agents[agent_name]['tools_count'] = len(tool_files)
                from datetime import datetime
                agents[agent_name]['updated_at'] = datetime.now().isoformat()
                save_agents(agents)
            
            return True
            
        except Exception as e:
            print(f"   ⚠️  Warning: Could not save tool to agent: {e}")
            return False
    
    def checkout_tool(
        self, 
        tool_id: str, 
        agent_name: str, 
        agent_path: str
    ) -> bool:
        """
        Checkout a single tool to an agent.
        
        Args:
            tool_id: The tool ID to checkout
            agent_name: Name of the agent
            agent_path: Path to the agent directory
            
        Returns:
            True if successful
        """
        # Fetch tool details
        success, tool_data, message = self.fetch_tool_details(tool_id)
        
        if not success or tool_data is None:
            print(f"   ❌ Failed to checkout tool: {message}")
            return False
        
        # Save to agent
        if self.save_tool_to_agent(agent_name, agent_path, tool_data, tool_id):
            tool_title = tool_data.get('title', 'Unknown')
            print(f"   ✅ Successfully checked out: {tool_title}")
            return True
        else:
            return False
    
    def checkout_tools(
        self, 
        tool_ids: List[str], 
        agent_name: str, 
        agent_path: str
    ) -> Tuple[int, int]:
        """
        Checkout multiple tools to an agent.
        
        Args:
            tool_ids: List of tool IDs to checkout
            agent_name: Name of the agent
            agent_path: Path to the agent directory
            
        Returns:
            Tuple of (successful: int, failed: int)
        """
        successful = 0
        failed = 0
        
        print(f"\n📥 Checking out {len(tool_ids)} tool(s) to agent: {agent_name}")
        print("=" * 80)
        
        for idx, tool_id in enumerate(tool_ids, 1):
            print(f"\n[{idx}/{len(tool_ids)}] Checking out tool: {tool_id}")
            print("-" * 80)
            
            if self.checkout_tool(tool_id, agent_name, agent_path):
                successful += 1
            else:
                failed += 1
                
                # Ask if user wants to continue on failure
                if idx < len(tool_ids):
                    continue_checkout = input("\n   Continue with remaining tools? (y/n): ").strip().lower()
                    if continue_checkout not in ('y', 'yes', ''):
                        print("\n   ⚠️  Checkout cancelled by user")
                        # Count remaining as skipped
                        failed += len(tool_ids) - idx
                        break
        
        return successful, failed


def get_tool_ids_from_paste() -> Optional[List[str]]:
    """
    Get a list of tool IDs from user paste input.
    
    Returns:
        List of tool IDs or None if cancelled
    """
    print("\n" + "=" * 80)
    print("📋 PASTE TOOL IDs")
    print("=" * 80)
    print("Paste your tool IDs (UUIDs) below, one per line or comma-separated.")
    print("Press Enter twice when done, or type 'cancel' to stop.")
    print("=" * 80)
    
    lines = []
    empty_count = 0
    
    while True:
        line = input()
        
        if line.strip().lower() == 'cancel':
            return None
        
        if not line.strip():
            empty_count += 1
            if empty_count >= 2:
                break
        else:
            empty_count = 0
            lines.append(line)
    
    if not lines:
        print("⚠️  No tool IDs provided.")
        return None
    
    # Parse the input - handle both newline and comma-separated
    tool_ids = []
    for line in lines:
        # Split by comma, semicolon, or space
        parts = line.replace(',', ' ').replace(';', ' ').split()
        tool_ids.extend([part.strip() for part in parts if part.strip()])
    
    if not tool_ids:
        print("⚠️  No valid tool IDs found.")
        return None
    
    # Remove duplicates while preserving order
    seen = set()
    unique_tool_ids = []
    for tool_id in tool_ids:
        if tool_id not in seen:
            seen.add(tool_id)
            unique_tool_ids.append(tool_id)
    
    print(f"\n✅ Parsed {len(unique_tool_ids)} unique tool ID(s)")
    print("\nTool IDs to checkout:")
    for idx, tool_id in enumerate(unique_tool_ids, 1):
        print(f"   {idx}. {tool_id}")
    
    # Confirm
    confirm = input("\n   Checkout these tools? (y/n): ").strip().lower()
    if confirm in ('y', 'yes', ''):
        return unique_tool_ids
    else:
        print("   Cancelled.")
        return None


def get_tool_ids_from_cache() -> Optional[List[str]]:
    """
    Get tool IDs from the cached tools list with interactive selection.
    
    Returns:
        List of selected tool IDs or None if cancelled
    """
    try:
        from cli.list_tools import ToolManager
        
        manager = ToolManager()
        
        # Try to load from cache
        if not manager.load_tools_from_file():
            print("\n⚠️  No cached tools found. Please run 'List tools' first to cache them.")
            return None
        
        if not manager.tools:
            print("\n⚠️  No tools found in cache.")
            return None
        
        # Display tools for selection
        print("\n" + "=" * 80)
        print("📋 SELECT TOOLS TO CHECKOUT")
        print("=" * 80)
        print(f"Available tools: {len(manager.tools)}")
        print("\nEnter tool numbers (comma-separated) or 'all' for all tools")
        print("You can also type 'list' to browse the full list first")
        print("=" * 80)
        
        while True:
            choice = input("\n👉 Your choice (e.g., '1,3,5' or 'all' or 'list'): ").strip().lower()
            
            if choice == 'cancel':
                return None
            
            if choice == 'list':
                # Show paginated list
                manager.display_tools_paginated(page_size=10)
                continue
            
            if choice == 'all':
                # Filter out None values and ensure we have valid tool IDs
                tool_ids = [str(tool.get('id')) for tool in manager.tools if tool.get('id') is not None]
                print(f"\n✅ Selected all {len(tool_ids)} tools")
                return tool_ids
            
            # Parse comma-separated numbers
            try:
                indices = [int(x.strip()) for x in choice.split(',') if x.strip()]
                
                # Validate indices
                invalid = [i for i in indices if i < 1 or i > len(manager.tools)]
                if invalid:
                    print(f"⚠️  Invalid tool numbers: {invalid}")
                    print(f"   Please enter numbers between 1 and {len(manager.tools)}")
                    continue
                
                # Get tool IDs
                tool_ids: List[str] = []
                print(f"\n✅ Selected {len(indices)} tool(s):")
                for idx in indices:
                    tool = manager.tools[idx - 1]
                    tool_id = tool.get('id')
                    tool_title = tool.get('title', 'Unknown')
                    if tool_id is not None:
                        tool_ids.append(str(tool_id))
                        print(f"   {idx}. {tool_title} ({tool_id})")
                
                if not tool_ids:
                    print("⚠️  No valid tool IDs found in selection.")
                    continue
                
                # Confirm
                confirm = input("\n   Checkout these tools? (y/n): ").strip().lower()
                if confirm in ('y', 'yes', ''):
                    return tool_ids
                else:
                    print("   Selection cancelled. Try again or type 'cancel'.")
                    continue
                    
            except ValueError:
                print("⚠️  Invalid input. Please enter comma-separated numbers, 'all', 'list', or 'cancel'.")
                continue
    
    except ImportError:
        print("❌ Error: Could not import ToolManager from list_tools")
        return None
    except Exception as e:
        print(f"❌ Error loading tools from cache: {e}")
        return None


def checkout_tools_interactive(bearer_token: str) -> None:
    """
    Interactive tool checkout workflow.
    
    Args:
        bearer_token: The authentication bearer token
    """
    print("\n" + "=" * 80)
    print("📦 CHECKOUT TOOLS")
    print("=" * 80)
    print("Download tool definitions from AdoptAI and save them to your agent.")
    print("This allows you to have a local copy of the tool for reference or modification.")
    print("=" * 80)
    
    # Step 1: Select agent
    agent_name, agent_info = select_agent_interactive("checkout tools to")
    
    if not agent_name or not agent_info:
        print("\n⚠️  Checkout cancelled - no agent selected.\n")
        return
    
    agent_path = agent_info.get('path', '')
    
    # Step 2: Choose selection mode
    print("\n" + "=" * 80)
    print("📋 CHOOSE TOOL SELECTION MODE")
    print("=" * 80)
    print("1. Paste tool IDs     - Paste a list of tool IDs directly")
    print("2. Select from cache  - Choose from previously listed tools")
    print("=" * 80)
    
    tool_ids = None
    while True:
        mode = input("\n👉 Choose mode (1 or 2): ").strip()
        
        if mode == '1':
            # Mode 1: Paste tool IDs
            tool_ids = get_tool_ids_from_paste()
            break
        elif mode == '2':
            # Mode 2: Select from cached list
            tool_ids = get_tool_ids_from_cache()
            break
        else:
            print("⚠️  Invalid choice. Please enter 1 or 2.")
    
    if not tool_ids:
        print("\n⚠️  No tools selected. Checkout cancelled.\n")
        return
    
    # Step 3: Checkout tools
    checkout_manager = ToolCheckoutManager(bearer_token)
    successful, failed = checkout_manager.checkout_tools(tool_ids, agent_name, agent_path)
    
    # Summary
    print("\n" + "=" * 80)
    print("📊 CHECKOUT SUMMARY")
    print("=" * 80)
    print(f"Agent: {agent_name}")
    print(f"Tools checked out: {successful}")
    print(f"Failed: {failed}")
    print(f"Total tools requested: {len(tool_ids)}")
    
    # Show agent location
    tools_dir = Path(agent_path) / "tools"
    if tools_dir.exists():
        tool_files = list(tools_dir.glob("*.json"))
        print(f"\nTotal tools in agent: {len(tool_files)}")
        print(f"Agent location: {agent_path}")
    
    print("=" * 80 + "\n")


def checkout_tool_by_id(bearer_token: str, tool_id: str, agent_name: str, agent_path: str) -> bool:
    """
    Checkout a single tool by ID (useful for automatic checkout after tool creation).
    
    Args:
        bearer_token: The authentication bearer token
        tool_id: The tool ID to checkout
        agent_name: Name of the agent
        agent_path: Path to the agent directory
        
    Returns:
        True if successful
    """
    checkout_manager = ToolCheckoutManager(bearer_token)
    return checkout_manager.checkout_tool(tool_id, agent_name, agent_path)


if __name__ == "__main__":
    # For testing purposes
    print("This module provides tool checkout functionality for AdoptAI.")
    print("It is designed to be imported and used by tool_builder.py")
    print("\nTo use this feature, run: poetry run tool-builder")

