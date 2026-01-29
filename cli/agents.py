#!/usr/bin/env python3
"""
Tool Agents - Agent management for Tool Builder
"""

import json
from pathlib import Path
from datetime import datetime


# Base directory for all agents
AGENTS_BASE_DIR = Path("tool_builder_agents")


def get_agents_config_path():
    """Get the path to the agents configuration file"""
    return AGENTS_BASE_DIR / "agents.json"


def ensure_agents_directory():
    """Ensure the agents base directory exists"""
    AGENTS_BASE_DIR.mkdir(exist_ok=True)
    print(f"✅ Agents directory: {AGENTS_BASE_DIR.absolute()}")


def load_agents():
    """Load all agents from the configuration file"""
    config_path = get_agents_config_path()
    
    if not config_path.exists():
        return {}
    
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️  Error loading agents configuration: {e}")
        return {}


def save_agents(agents):
    """Save agents to the configuration file"""
    ensure_agents_directory()
    config_path = get_agents_config_path()
    
    try:
        with open(config_path, 'w') as f:
            json.dump(agents, f, indent=2)
        return True
    except Exception as e:
        print(f"❌ Error saving agents configuration: {e}")
        return False


def create_agent(agent_name):
    """
    Create a new agent with a unique name and associated folder
    
    Args:
        agent_name: Name of the agent (must be unique)
        
    Returns:
        tuple: (success: bool, message: str)
    """
    # Validate agent name
    if not agent_name or not agent_name.strip():
        return False, "Agent name cannot be empty"
    
    agent_name = agent_name.strip()
    
    # Check for invalid characters
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    if any(char in agent_name for char in invalid_chars):
        return False, f"Agent name contains invalid characters: {', '.join(invalid_chars)}"
    
    # Load existing agents
    agents = load_agents()
    
    # Check if agent already exists
    if agent_name in agents:
        return False, f"Agent '{agent_name}' already exists"
    
    # Create agent directory
    agent_dir = AGENTS_BASE_DIR / agent_name
    try:
        agent_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return False, f"Agent directory already exists: {agent_dir}"
    except Exception as e:
        return False, f"Error creating agent directory: {e}"
    
    # Create subdirectories for agent organization
    subdirs = ['tools', 'tests', 'docs']
    for subdir in subdirs:
        (agent_dir / subdir).mkdir(exist_ok=True)
    
    # Create agent.json with the Adopt agent configuration format
    agent_config = [
        {
            "action_ids": [],
            "id": agent_name,
            "model_string": "claude-4-5-sonnet",
            "operation": "PROMPT_AND_TOOLS_AGENT",
            "system_prompt": f"You are a helpful assistant for {agent_name}.\n\nConversation history is given to you. Use that to understand which step you are in and proceed accordingly. Stick very closely to the data from the tools provided to you. Do not make up any data. If the tools return no data, say so explicitly."
        },
        {
            "field": "message",
            "id": "extractAgentMessage",
            "input": agent_name,
            "operation": "EXTRACT"
        },
        {
            "format_string": "{}",
            "id": "outputAgentResponse",
            "operation": "OUTPUT_TEXT",
            "raw": True,
            "values": ["extractAgentMessage"]
        }
    ]
    
    try:
        agent_json_path = agent_dir / "agent.json"
        with open(agent_json_path, 'w') as f:
            json.dump(agent_config, f, indent=2)
    except Exception as e:
        # Rollback: remove the created directory
        try:
            import shutil
            shutil.rmtree(agent_dir)
        except:
            pass
        return False, f"Error creating agent.json: {e}"
    
    # Add agent to configuration
    agents[agent_name] = {
        'name': agent_name,
        'path': str(agent_dir.absolute()),
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat(),
        'description': '',
        'tools_count': 0
    }
    
    # Save agents configuration
    if save_agents(agents):
        return True, f"Agent '{agent_name}' created successfully at {agent_dir.absolute()}"
    else:
        # Rollback: remove the created directory
        try:
            import shutil
            shutil.rmtree(agent_dir)
        except:
            pass
        return False, "Error saving agent configuration"


def get_agent(agent_name):
    """
    Get agent information by name
    
    Args:
        agent_name: Name of the agent
        
    Returns:
        dict or None: Agent information or None if not found
    """
    agents = load_agents()
    return agents.get(agent_name)


def list_agents():
    """
    List all available agents
    
    Returns:
        dict: Dictionary of all agents
    """
    return load_agents()


def agent_exists(agent_name):
    """
    Check if an agent exists
    
    Args:
        agent_name: Name of the agent
        
    Returns:
        bool: True if agent exists, False otherwise
    """
    agents = load_agents()
    return agent_name in agents


def create_agent_interactive():
    """Interactive agent creation wizard"""
    print("\n" + "=" * 80)
    print("📁 CREATE NEW AGENT")
    print("=" * 80)
    print("Agents help you organize your tools in separate workspaces.")
    print("Each agent has its own folder structure for tools, tests, and documentation.")
    print("=" * 80)
    
    # Ensure agents directory exists
    ensure_agents_directory()
    
    # Show existing agents
    agents = list_agents()
    if agents:
        print(f"\n📋 Existing agents ({len(agents)}):")
        for i, (name, info) in enumerate(agents.items(), 1):
            print(f"   {i}. {name}")
        print()
    else:
        print("\n💡 No agents exist yet. This will be your first agent!\n")
    
    # Get agent name from user
    while True:
        agent_name = input("📝 Enter agent name (or 'cancel' to abort): ").strip()
        
        if agent_name.lower() == 'cancel':
            print("\n⚠️  Agent creation cancelled.\n")
            return
        
        if not agent_name:
            print("⚠️  Agent name cannot be empty. Please try again.\n")
            continue
        
        # Attempt to create agent
        success, message = create_agent(agent_name)
        
        if success:
            print("\n" + "=" * 80)
            print("✅ SUCCESS!")
            print("=" * 80)
            print(message)
            print("\n📂 Agent structure:")
            print(f"   {AGENTS_BASE_DIR / agent_name}/")
            print("   ├── tools/      (Store your tool definitions here)")
            print("   ├── tests/      (Store your test cases here)")
            print("   └── docs/       (Store your documentation here)")
            print("=" * 80 + "\n")
            break
        else:
            print(f"\n❌ Error: {message}")
            print("Please try a different name.\n")


def delete_agent(agent_name):
    """
    Delete an agent and all its contents
    
    Args:
        agent_name: Name of the agent to delete
        
    Returns:
        tuple: (success: bool, message: str)
    """
    # Load existing agents
    agents = load_agents()
    
    # Check if agent exists
    if agent_name not in agents:
        return False, f"Agent '{agent_name}' does not exist"
    
    agent_info = agents[agent_name]
    agent_dir = Path(agent_info['path'])
    
    # Delete agent directory and all its contents
    try:
        import shutil
        if agent_dir.exists():
            shutil.rmtree(agent_dir)
    except Exception as e:
        return False, f"Error deleting agent directory: {e}"
    
    # Remove agent from configuration
    del agents[agent_name]
    
    # Save updated agents configuration
    if save_agents(agents):
        return True, f"Agent '{agent_name}' deleted successfully"
    else:
        return False, "Error saving agents configuration after deletion"


def delete_agent_interactive():
    """Interactive agent deletion wizard"""
    print("\n" + "=" * 80)
    print("🗑️  DELETE AGENT")
    print("=" * 80)
    print("⚠️  WARNING: This will permanently delete the agent and all its contents!")
    print("   (tools, tests, documentation, and all files in the agent folder)")
    print("=" * 80)
    
    # Load agents
    agents = list_agents()
    
    if not agents:
        print("\n💡 No agents exist. Nothing to delete.\n")
        return
    
    # Show existing agents
    print(f"\n📋 Existing agents ({len(agents)}):")
    agent_list = list(agents.items())
    for i, (name, info) in enumerate(agent_list, 1):
        created = info.get('created_at', 'Unknown')
        tools_count = info.get('tools_count', 0)
        print(f"   {i}. {name}")
        print(f"      Created: {created[:10]}")
        print(f"      Tools: {tools_count}")
        print(f"      Path: {info.get('path', 'Unknown')}")
        print()
    
    print("=" * 80)
    
    # Get agent selection
    while True:
        choice = input(f"👉 Select agent number to delete (1-{len(agents)}) or 'cancel': ").strip()
        
        if choice.lower() == 'cancel':
            print("\n⚠️  Agent deletion cancelled.\n")
            return
        
        try:
            choice_num = int(choice)
            if 1 <= choice_num <= len(agents):
                selected_name, selected_info = agent_list[choice_num - 1]
                break
            else:
                print(f"⚠️  Invalid choice. Please enter a number between 1 and {len(agents)}.\n")
        except ValueError:
            print("⚠️  Invalid input. Please enter a number or 'cancel'.\n")
    
    # Confirm deletion
    print(f"\n⚠️  You are about to delete agent: '{selected_name}'")
    print(f"   Path: {selected_info.get('path', 'Unknown')}")
    print(f"   Tools: {selected_info.get('tools_count', 0)}")
    print("\n🚨 This action CANNOT be undone!")
    
    confirm = input(f"\n   Type the agent name '{selected_name}' to confirm deletion: ").strip()
    
    if confirm != selected_name:
        print("\n⚠️  Agent name does not match. Deletion cancelled.\n")
        return
    
    # Perform deletion
    success, message = delete_agent(selected_name)
    
    if success:
        print("\n" + "=" * 80)
        print("✅ SUCCESS!")
        print("=" * 80)
        print(message)
        print("=" * 80 + "\n")
    else:
        print("\n" + "=" * 80)
        print("❌ ERROR!")
        print("=" * 80)
        print(message)
        print("=" * 80 + "\n")


def select_agent_interactive(action="work with"):
    """
    Interactive agent selection
    
    Args:
        action: Description of what will be done with the selected agent
        
    Returns:
        tuple: (agent_name: str or None, agent_info: dict or None)
    """
    agents = list_agents()
    
    if not agents:
        print("\n" + "=" * 80)
        print("⚠️  NO AGENTS FOUND")
        print("=" * 80)
        print("You need to create an agent first before you can use this feature.")
        print("Please select 'Create agent' from the main menu.")
        print("=" * 80 + "\n")
        return None, None
    
    print("\n" + "=" * 80)
    print(f"📁 SELECT AGENT TO {action.upper()}")
    print("=" * 80)
    print(f"Available agents ({len(agents)}):\n")
    
    agent_list = list(agents.items())
    for i, (name, info) in enumerate(agent_list, 1):
        created = info.get('created_at', 'Unknown')
        tools_count = info.get('tools_count', 0)
        print(f"   {i}. {name}")
        print(f"      Created: {created[:10]}")
        print(f"      Tools: {tools_count}")
        print(f"      Path: {info.get('path', 'Unknown')}")
        print()
    
    print("=" * 80)
    
    while True:
        choice = input(f"👉 Select agent number (1-{len(agents)}) or 'cancel': ").strip()
        
        if choice.lower() == 'cancel':
            print("\n⚠️  Operation cancelled.\n")
            return None, None
        
        try:
            choice_num = int(choice)
            if 1 <= choice_num <= len(agents):
                selected_name, selected_info = agent_list[choice_num - 1]
                print(f"\n✅ Selected agent: '{selected_name}'\n")
                return selected_name, selected_info
            else:
                print(f"⚠️  Invalid choice. Please enter a number between 1 and {len(agents)}.\n")
        except ValueError:
            print("⚠️  Invalid input. Please enter a number or 'cancel'.\n")

