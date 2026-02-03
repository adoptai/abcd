#!/usr/bin/env python3
"""
Workspace Manager - Hierarchical Workspace Management.

**IMPORTANT**: All operations require an environment. The repo ships with a
'default' environment that is active by default. Create additional environments
for different targets (staging, production) or clients.

Supports three-level hierarchy:
1. Environment (Env) Level - Top-level workspace with shared config (REQUIRED)
2. Agent Level - Uber Agent containing sub-actions
3. Action Level - Individual action workspace

Directory Structure:
    workspaces/
    ├── .active_env              # Currently active environment
    ├── default/                 # Default environment (ships with repo)
    │   ├── .env
    │   ├── adopt_profile.json
    │   ├── env.json
    │   ├── agents/
    │   │   └── {agent_name}/
    │   │       ├── agent.json
    │   │       ├── widdle.json
    │   │       ├── adopt_profile.json (optional)
    │   │       └── actions/
    │   │           └── {action_id}/
    │   └── actions/             # Standalone actions in env
    │       └── {action_id}/
    └── {other_env}/             # Additional environments
        └── ...
"""

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Set

from dotenv import load_dotenv

# Base directories
PROJECT_ROOT = Path(__file__).parent.parent.parent
WORKSPACES_DIR = PROJECT_ROOT / "workspaces"


@dataclass
class ActionContext:
    """
    Complete context for an action, ready to use.
    
    This is the primary way to access action data. It includes:
    - Action metadata and paths
    - Environment info
    - Resolved adopt_profile.json with inheritance
    """
    action_id: str
    path: Path
    env_name: str
    agent_name: Optional[str]
    metadata: dict[str, Any]
    resolved_profile: dict[str, Any]
    
    @property
    def wdl_path(self) -> Path:
        """Path to widdle.json."""
        return self.path / "widdle.json"
    
    @property
    def metadata_path(self) -> Path:
        """Path to metadata.json."""
        return self.path / "metadata.json"
    
    @property
    def apis_dir(self) -> Path:
        """Path to apis/ directory."""
        return self.path / "apis"
    
    @property
    def tools_dir(self) -> Path:
        """Path to tools/ directory."""
        return self.path / "tools"
    
    @property
    def test_cases_dir(self) -> Path:
        """Path to test_cases/ directory."""
        return self.path / "test_cases"
    
    @property
    def traces_dir(self) -> Path:
        """Path to traces/ directory."""
        return self.path / "traces"
    
    @property
    def adopt_profile_path(self) -> Path:
        """Path to adopt_profile.json."""
        return self.path / "adopt_profile.json"


class WorkspaceType(Enum):
    """Type of workspace."""
    ENVIRONMENT = "environment"
    AGENT = "agent"
    ACTION = "action"

# Default environment name
DEFAULT_ENV = "default"


class HierarchicalWorkspaceManager:
    """
    Manages hierarchical workspaces with configuration inheritance.

    **IMPORTANT**: All operations require an environment. Use 'default' env
    or create additional environments for different targets/clients.

    Supports:
    - Environment-level workspaces with shared .env and adopt_profile.json (REQUIRED)
    - Agent-level workspaces (Uber Agents) with sub-actions
    - Action-level workspaces within environments or agents
    - Configuration inheritance (action -> agent -> env)
    """

    def __init__(self) -> None:
        """Initialize workspace manager."""
        self._ensure_base_structure()
        self._active_env: str | None = None
        self._loaded_envs: Set[str] = set()
        self._load_active_env()
        
        # Auto-activate default env if no active env
        if not self._active_env and self.env_exists(DEFAULT_ENV):
            self._active_env = DEFAULT_ENV
            self._save_active_env()

    # =========================================================================
    # Active Environment & Context Methods
    # =========================================================================

    def ensure_env_loaded(self) -> str:
        """
        Ensure active environment's .env is loaded. Returns environment name.
        
        This is idempotent - safe to call multiple times.
        
        Returns:
            The active environment name
            
        Raises:
            ValueError: If no active environment
        """
        if not self._active_env:
            raise ValueError(
                "No active environment. Set one with: "
                "python cli/workspace.py env use <env-id>"
            )
        
        if self._active_env not in self._loaded_envs:
            self.load_env_vars(self._active_env)
            self._loaded_envs.add(self._active_env)
        
        return self._active_env

    def get_env_path(self) -> Path | None:
        """
        Get path to active environment.
        
        Returns:
            Path to active environment directory, or None if no active env
        """
        if not self._active_env:
            return None
        return WORKSPACES_DIR / self._active_env

    def get_action_context(self, action_id: str) -> ActionContext | None:
        """
        Get complete action context with environment loaded.
        
        This is the PRIMARY way to access an action. It:
        1. Finds the action in the active environment
        2. Loads the environment's .env file
        3. Resolves the adopt_profile.json with inheritance
        4. Returns everything needed to work with the action
        
        Args:
            action_id: Action identifier to find
            
        Returns:
            ActionContext with all resolved data, or None if not found
        """
        # Always use active environment
        if not self._active_env:
            return None
        
        action_info = self.find_action(action_id, env_name=self._active_env)
        if not action_info:
            return None
        
        env_name = action_info.get("env_name")
        agent_name = action_info.get("agent_name")
        path = Path(action_info["path"])
        
        # Load environment credentials
        self.load_env_vars(env_name)
        
        # Resolve profile with inheritance
        resolved_profile = self.resolve_adopt_profile(
            action_path=path,
            agent_name=agent_name,
            env_name=env_name,
        )
        
        return ActionContext(
            action_id=action_id,
            path=path,
            env_name=env_name,
            agent_name=agent_name,
            metadata=action_info.get("metadata", {}),
            resolved_profile=resolved_profile,
        )

    def select_agent_interactive(self) -> str | None:
        """
        Interactively select an agent from the active environment.
        
        Returns:
            Selected agent ID, or None if no agents or user cancels
        """
        if not self._active_env:
            return None
        
        agents = self.list_agents()
        
        if not agents:
            return None
        
        print("\n📦 Available agents:")
        for i, agent in enumerate(agents, 1):
            name = agent.get("name", agent.get("agent_id", "Unknown"))
            agent_id = agent.get("agent_id", name)
            desc = agent.get("description", "")[:50]
            print(f"  {i}. {agent_id}")
            if desc:
                print(f"     {desc}...")
        
        print("  0. Cancel / Use standalone mode")
        
        try:
            choice = input("\nSelect agent (0 to cancel): ").strip()
            if not choice or choice == "0":
                return None
            
            idx = int(choice) - 1
            if 0 <= idx < len(agents):
                return agents[idx].get("agent_id")
        except (ValueError, KeyboardInterrupt):
            pass
        
        return None

    # =========================================================================
    # Base Structure
    # =========================================================================

    def _ensure_base_structure(self) -> None:
        """Ensure base workspace structure exists with default environment."""
        WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

        # Ensure default environment exists
        default_env_path = WORKSPACES_DIR / DEFAULT_ENV
        if not default_env_path.exists():
            self.create_env(
                env_id=DEFAULT_ENV,
                name="Default Environment",
                description="Default development environment for ABCD",
                target="development",
            )

    def _load_active_env(self) -> None:
        """Load active environment from config."""
        config_file = WORKSPACES_DIR / ".active_env"
        if config_file.exists():
            self._active_env = config_file.read_text().strip() or None

    def _save_active_env(self) -> None:
        """Save active environment to config."""
        config_file = WORKSPACES_DIR / ".active_env"
        if self._active_env:
            config_file.write_text(self._active_env)
        elif config_file.exists():
            config_file.unlink()

    @property
    def active_env(self) -> str | None:
        """Get the active environment name."""
        return self._active_env

    @active_env.setter
    def active_env(self, env_name: str | None) -> None:
        """Set the active environment."""
        if env_name and not self.env_exists(env_name):
            raise ValueError(f"Environment not found: {env_name}")
        self._active_env = env_name
        self._save_active_env()

    def get_active_env_info(self) -> dict[str, Any] | None:
        """
        Get full info about the active environment.
        
        Returns:
            Dict with env_id, name, description, domain, allowed_domains, etc.
            Returns None if no active environment.
        """
        if not self._active_env:
            return None
        return self.get_env(self._active_env)

    def get_active_env_description(self) -> str:
        """
        Get the description of the active environment.
        
        Useful for agents to validate if a request matches the environment's purpose.
        
        Returns:
            Environment description string, or empty string if no active env.
        """
        info = self.get_active_env_info()
        return info.get("description", "") if info else ""

    # =========================================================================
    # Environment Management
    # =========================================================================

    def env_exists(self, env_name: str) -> bool:
        """Check if environment exists."""
        env_path = WORKSPACES_DIR / env_name
        return env_path.exists() and (env_path / "env.json").exists()

    def create_env(
        self,
        env_id: str,
        name: str,
        description: str = "",
        target: str = "staging",
        client: str = "",
        domain: str = "",
        allowed_domains: list[str] | None = None,
    ) -> tuple[bool, Path, str]:
        """
        Create a new environment workspace.

        Args:
            env_id: Unique environment identifier
            name: Human-readable name
            description: Environment description (IMPORTANT: describes what kind of 
                        actions this env is for - helps agents validate requests)
            target: Target type (staging, production, development)
            client: Optional client identifier
            domain: Primary business domain (e.g., "inventory", "marketing", "ecommerce")
            allowed_domains: List of allowed business domains for this environment

        Returns:
            Tuple of (success, path, message)
        """
        env_path = WORKSPACES_DIR / env_id

        if env_path.exists():
            return False, env_path, f"Environment already exists: {env_id}"

        try:
            # Create directory structure
            env_path.mkdir(parents=True)
            (env_path / "agents").mkdir()
            (env_path / "actions").mkdir()

            # Create env.json
            env_meta = {
                "env_id": env_id,
                "name": name,
                "description": description,
                "target": target,
                "client": client,
                "domain": domain,
                "allowed_domains": allowed_domains or [],
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "agents": [],
                "actions": [],
            }
            (env_path / "env.json").write_text(json.dumps(env_meta, indent=2))

            # Create default adopt_profile.json
            profile = {
                "base_url": "",
                "application_base_url": "",
                "workflow_params": {},
                "security_params": {},
            }
            (env_path / "adopt_profile.json").write_text(json.dumps(profile, indent=2))

            # Create default .env with required variables
            env_content = f"""# Environment: {name}
# Target: {target}

# Required: AdoptAI API Authentication
# Configure these before using discovery or API features
ADOPT_CLIENT_ID=your-client-id-here
ADOPT_CLIENT_SECRET=your-client-secret-here

# API Endpoints (defaults to production, uncomment to override)
# ADOPT_API_ENDPOINT=https://connect.adopt.ai
# ADOPT_ACTIONS_ENDPOINT=https://api.adopt.ai
"""
            (env_path / ".env").write_text(env_content)

            return True, env_path, f"Environment created: {env_id}"

        except Exception as e:
            if env_path.exists():
                shutil.rmtree(env_path)
            return False, env_path, f"Failed to create environment: {e}"

    def list_envs(self) -> list[dict[str, Any]]:
        """List all environments."""
        envs = []
        for item in WORKSPACES_DIR.iterdir():
            if item.is_dir() and (item / "env.json").exists():
                try:
                    env_data = json.loads((item / "env.json").read_text())
                    env_data["path"] = str(item)
                    env_data["is_active"] = item.name == self._active_env
                    envs.append(env_data)
                except Exception:
                    envs.append({
                        "env_id": item.name,
                        "path": str(item),
                        "is_active": item.name == self._active_env,
                    })
        return envs

    def get_env(self, env_name: str) -> dict[str, Any] | None:
        """Get environment details."""
        env_path = WORKSPACES_DIR / env_name
        if not self.env_exists(env_name):
            return None

        try:
            env_data = json.loads((env_path / "env.json").read_text())
            env_data["path"] = str(env_path)
            env_data["is_active"] = env_name == self._active_env
            return env_data
        except Exception:
            return None

    def delete_env(self, env_name: str, force: bool = False) -> tuple[bool, str]:
        """Delete an environment."""
        if not self.env_exists(env_name):
            return False, f"Environment not found: {env_name}"

        env_path = WORKSPACES_DIR / env_name

        # Check if env has contents
        agents_count = len(list((env_path / "agents").iterdir())) if (env_path / "agents").exists() else 0
        actions_count = len(list((env_path / "actions").iterdir())) if (env_path / "actions").exists() else 0

        if (agents_count > 0 or actions_count > 0) and not force:
            return False, f"Environment has {agents_count} agents and {actions_count} actions. Use --force to delete."

        try:
            shutil.rmtree(env_path)
            if self._active_env == env_name:
                self._active_env = None
                self._save_active_env()
            return True, f"Environment deleted: {env_name}"
        except Exception as e:
            return False, f"Failed to delete environment: {e}"

    # =========================================================================
    # Agent Management
    # =========================================================================

    def agent_exists(self, agent_name: str, env_name: str | None = None) -> bool:
        """Check if agent exists."""
        env = env_name or self._active_env
        if not env:
            return False
        agent_path = WORKSPACES_DIR / env / "agents" / agent_name
        return agent_path.exists() and (agent_path / "agent.json").exists()

    def create_agent(
        self,
        agent_id: str,
        name: str,
        description: str = "",
        env_name: str | None = None,
        template: str = "uber_agent",
    ) -> tuple[bool, Path, str]:
        """
        Create a new agent (Uber Agent) workspace.

        Args:
            agent_id: Unique agent identifier
            name: Human-readable name
            description: Agent description
            env_name: Environment to create in (uses active if not specified)
            template: Template to use (uber_agent, simple)

        Returns:
            Tuple of (success, path, message)
        """
        env = env_name or self._active_env
        if not env:
            return False, Path(), "No environment specified. Create or select an environment first."

        if not self.env_exists(env):
            return False, Path(), f"Environment not found: {env}"

        agent_path = WORKSPACES_DIR / env / "agents" / agent_id

        if agent_path.exists():
            return False, agent_path, f"Agent already exists: {agent_id}"

        try:
            # Create directory structure
            agent_path.mkdir(parents=True)
            (agent_path / "actions").mkdir()
            (agent_path / "test_cases").mkdir()
            (agent_path / "test_cases" / "subaction_tests").mkdir()
            (agent_path / "traces").mkdir()
            (agent_path / "versions").mkdir()

            # Create agent.json
            agent_meta = {
                "agent_id": agent_id,
                "name": name,
                "description": description,
                "type": "uber_agent",
                "remote_action_id": None,
                "sub_actions": [],
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            (agent_path / "agent.json").write_text(json.dumps(agent_meta, indent=2))

            # Create uber agent WDL template
            wdl = self._get_uber_agent_template(name, description)
            (agent_path / "widdle.json").write_text(json.dumps(wdl, indent=2))

            # Create metadata.json
            metadata = {
                "workflow_id": agent_id,
                "title": name,
                "type": "uber_agent",
                "env_name": env,
                "action_id": None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            (agent_path / "metadata.json").write_text(json.dumps(metadata, indent=2))

            # Update env.json
            self._add_agent_to_env(env, agent_id)

            return True, agent_path, f"Agent created: {agent_id} in {env}"

        except Exception as e:
            if agent_path.exists():
                shutil.rmtree(agent_path)
            return False, agent_path, f"Failed to create agent: {e}"

    def _get_uber_agent_template(self, name: str, description: str) -> list[dict[str, Any]]:
        """Get the uber agent WDL template."""
        return [
            {
                "id": "uberAgent",
                "operation": "PROMPT_AND_TOOLS_AGENT",
                "model_string": "claude-4-5-sonnet",
                "action_ids": [],
                "system_prompt": f"You are {name}. {description}\n\nYou have access to the following tools:\n\n{{TOOL_DESCRIPTIONS}}\n\nBased on the user's request:\n1. Determine which tool(s) to call\n2. Ask the user for any missing required inputs\n3. Execute the tools deterministically\n4. Present the results clearly\n5. Ask what the user wants to do next\n\nDo not add reasoning steps - execute tools directly when you have all required inputs."
            },
            {
                "id": "extractAgentMessage",
                "operation": "EXTRACT",
                "input": "uberAgent",
                "field": "message"
            },
            {
                "id": "outputAgentResponse",
                "operation": "OUTPUT_TEXT",
                "format_string": "{}",
                "values": ["extractAgentMessage"],
                "raw": True
            },
            {
                "required_inputs": []
            }
        ]

    def _add_agent_to_env(self, env_name: str, agent_id: str) -> None:
        """Add agent to environment metadata."""
        env_path = WORKSPACES_DIR / env_name / "env.json"
        if env_path.exists():
            env_data = json.loads(env_path.read_text())
            if agent_id not in env_data.get("agents", []):
                env_data.setdefault("agents", []).append(agent_id)
                env_data["updated_at"] = datetime.now().isoformat()
                env_path.write_text(json.dumps(env_data, indent=2))

    def list_agents(self, env_name: str | None = None) -> list[dict[str, Any]]:
        """List all agents in an environment."""
        env = env_name or self._active_env
        if not env or not self.env_exists(env):
            return []

        agents_dir = WORKSPACES_DIR / env / "agents"
        if not agents_dir.exists():
            return []

        agents = []
        for item in agents_dir.iterdir():
            if item.is_dir() and (item / "agent.json").exists():
                try:
                    agent_data = json.loads((item / "agent.json").read_text())
                    agent_data["path"] = str(item)
                    agent_data["env_name"] = env
                    agents.append(agent_data)
                except Exception:
                    agents.append({
                        "agent_id": item.name,
                        "path": str(item),
                        "env_name": env,
                    })
        return agents

    def get_agent(self, agent_name: str, env_name: str | None = None) -> dict[str, Any] | None:
        """Get agent details."""
        env = env_name or self._active_env
        if not env:
            return None

        agent_path = WORKSPACES_DIR / env / "agents" / agent_name
        if not agent_path.exists():
            return None

        try:
            agent_data = json.loads((agent_path / "agent.json").read_text())
            agent_data["path"] = str(agent_path)
            agent_data["env_name"] = env
            return agent_data
        except Exception:
            return None

    def add_subaction(
        self,
        agent_name: str,
        action_id: str,
        remote_action_id: str,
        title: str,
        description: str = "",
        env_name: str | None = None,
    ) -> tuple[bool, str]:
        """
        Add a sub-action to an agent.

        Args:
            agent_name: Agent to add to
            action_id: Local action identifier
            remote_action_id: Remote action ID
            title: Action title (must be valid for orchestrator)
            description: Action description
            env_name: Environment name

        Returns:
            Tuple of (success, message)
        """
        env = env_name or self._active_env
        if not env:
            return False, "No environment specified"

        if not self.agent_exists(agent_name, env):
            return False, f"Agent not found: {agent_name}"

        # Validate title format
        import re
        if not re.match(r'^[a-zA-Z0-9_-]{1,128}$', title):
            return False, f"Invalid title format: {title}. Must match ^[a-zA-Z0-9_-]{{1,128}}$"

        agent_path = WORKSPACES_DIR / env / "agents" / agent_name

        try:
            # Update agent.json
            agent_file = agent_path / "agent.json"
            agent_data = json.loads(agent_file.read_text())

            # Check if action already exists
            existing = [a for a in agent_data.get("sub_actions", []) if a["action_id"] == action_id]
            if existing:
                return False, f"Action {action_id} already in agent"

            agent_data.setdefault("sub_actions", []).append({
                "action_id": action_id,
                "remote_action_id": remote_action_id,
                "title": title,
                "description": description,
                "required": True,
            })
            agent_data["updated_at"] = datetime.now().isoformat()
            agent_file.write_text(json.dumps(agent_data, indent=2))

            # Update WDL with new action_id
            wdl_file = agent_path / "widdle.json"
            if wdl_file.exists():
                wdl = json.loads(wdl_file.read_text())
                for step in wdl:
                    if step.get("operation") == "PROMPT_AND_TOOLS_AGENT":
                        if remote_action_id not in step.get("action_ids", []):
                            step.setdefault("action_ids", []).append(remote_action_id)
                        break
                wdl_file.write_text(json.dumps(wdl, indent=2))

            return True, f"Added {action_id} to {agent_name}"

        except Exception as e:
            return False, f"Failed to add sub-action: {e}"

    def move_action_to_agent(
        self,
        action_id: str,
        agent_name: str,
        env_name: str | None = None,
        update_wdl: bool = True,
    ) -> tuple[bool, str]:
        """
        Move a standalone action to become a sub-action of an agent.
        
        Args:
            action_id: The action to move
            agent_name: Target agent to move action into
            env_name: Environment (uses active if not specified)
            update_wdl: If True, also update agent's WDL to include the action
            
        Returns:
            Tuple of (success, message)
        """
        import shutil
        
        env = env_name or self._active_env
        if not env:
            return False, "No environment specified"
        
        if not self.agent_exists(agent_name, env):
            return False, f"Agent not found: {agent_name}"
        
        # Check for action in standalone actions
        standalone_path = WORKSPACES_DIR / env / "actions" / action_id
        if not standalone_path.exists():
            return False, f"Standalone action not found: {action_id}"
        
        agent_path = WORKSPACES_DIR / env / "agents" / agent_name
        target_path = agent_path / "actions" / action_id
        
        if target_path.exists():
            return False, f"Action already exists in agent: {action_id}"
        
        try:
            # Read action metadata for title and remote ID
            metadata_file = standalone_path / "metadata.json"
            action_title = action_id
            remote_action_id = None
            
            if metadata_file.exists():
                metadata = json.loads(metadata_file.read_text())
                action_title = metadata.get("title", action_id)
                remote_action_id = metadata.get("action_id") or metadata.get("remote_action_id")
            
            # Move the action folder
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(standalone_path), str(target_path))
            
            # Update action metadata to reflect new location
            new_metadata_file = target_path / "metadata.json"
            if new_metadata_file.exists():
                metadata = json.loads(new_metadata_file.read_text())
                metadata["agent_name"] = agent_name
                metadata["env_name"] = env
                metadata["moved_to_agent_at"] = datetime.now().isoformat()
                new_metadata_file.write_text(json.dumps(metadata, indent=2))
            
            # Add to agent's sub_actions list
            agent_file = agent_path / "agent.json"
            agent_data = json.loads(agent_file.read_text())
            
            agent_data.setdefault("sub_actions", []).append({
                "action_id": action_id,
                "remote_action_id": remote_action_id,
                "title": action_title,
            })
            agent_data["updated_at"] = datetime.now().isoformat()
            agent_file.write_text(json.dumps(agent_data, indent=2))
            
            # Update agent's WDL if requested and remote ID is available
            if update_wdl and remote_action_id:
                wdl_file = agent_path / "widdle.json"
                if wdl_file.exists():
                    wdl = json.loads(wdl_file.read_text())
                    for step in wdl:
                        if step.get("operation") == "PROMPT_AND_TOOLS_AGENT":
                            action_ids = step.get("action_ids", [])
                            if remote_action_id not in action_ids:
                                action_ids.append(remote_action_id)
                            break
                    wdl_file.write_text(json.dumps(wdl, indent=2))
            
            return True, f"Moved {action_id} to agent {agent_name}"
            
        except Exception as e:
            return False, f"Failed to move action: {e}"

    def remove_subaction(
        self,
        agent_name: str,
        action_id: str,
        env_name: str | None = None,
    ) -> tuple[bool, str]:
        """Remove a sub-action from an agent."""
        env = env_name or self._active_env
        if not env:
            return False, "No environment specified"

        if not self.agent_exists(agent_name, env):
            return False, f"Agent not found: {agent_name}"

        agent_path = WORKSPACES_DIR / env / "agents" / agent_name

        try:
            agent_file = agent_path / "agent.json"
            agent_data = json.loads(agent_file.read_text())

            # Find and remove action
            sub_actions = agent_data.get("sub_actions", [])
            removed = None
            for i, action in enumerate(sub_actions):
                if action["action_id"] == action_id:
                    removed = sub_actions.pop(i)
                    break

            if not removed:
                return False, f"Action {action_id} not found in agent"

            agent_data["updated_at"] = datetime.now().isoformat()
            agent_file.write_text(json.dumps(agent_data, indent=2))

            # Update WDL
            wdl_file = agent_path / "widdle.json"
            if wdl_file.exists() and removed.get("remote_action_id"):
                wdl = json.loads(wdl_file.read_text())
                for step in wdl:
                    if step.get("operation") == "PROMPT_AND_TOOLS_AGENT":
                        action_ids = step.get("action_ids", [])
                        if removed["remote_action_id"] in action_ids:
                            action_ids.remove(removed["remote_action_id"])
                        break
                wdl_file.write_text(json.dumps(wdl, indent=2))

            return True, f"Removed {action_id} from {agent_name}"

        except Exception as e:
            return False, f"Failed to remove sub-action: {e}"

    # =========================================================================
    # Action Management
    # =========================================================================

    def create_action(
        self,
        action_id: str,
        title: str,
        description: str = "",
        requirements: str = "",
        env_name: str | None = None,
        agent_name: str | None = None,
        template: str = "simple",
    ) -> tuple[bool, Path, str]:
        """
        Create a new action workspace.

        Args:
            action_id: Unique action identifier
            title: Action title
            description: Action description
            requirements: Requirements content
            env_name: Environment (uses active env if None)
            agent_name: Agent to create under (None for standalone in env)
            template: Template to use

        Returns:
            Tuple of (success, path, message)
        """
        # Require an environment
        env = env_name or self._active_env
        if not env:
            return False, Path(), "No environment specified. Use --env or set active environment with 'workspace.py env use'"
        
        if not self.env_exists(env):
            return False, Path(), f"Environment not found: {env}"
        
        # Determine action path
        if agent_name:
            if not self.agent_exists(agent_name, env):
                return False, Path(), f"Agent not found: {agent_name}"
            action_path = WORKSPACES_DIR / env / "agents" / agent_name / "actions" / action_id
        else:
            action_path = WORKSPACES_DIR / env / "actions" / action_id

        if action_path.exists():
            return False, action_path, f"Action already exists: {action_id}"

        try:
            # Create directory structure
            action_path.mkdir(parents=True)
            (action_path / "test_cases").mkdir()
            (action_path / "traces").mkdir()
            (action_path / "versions").mkdir()
            (action_path / "apis").mkdir()
            (action_path / "tools").mkdir()

            # Create requirements.md
            (action_path / "requirements.md").write_text(requirements or f"# {title}\n\n{description}\n")

            # Create description.txt
            (action_path / "description.txt").write_text(f"Workflow: {title}\n\n{description}")

            # Create initial WDL based on template
            wdl = self._get_action_template(template, title)
            (action_path / "widdle.json").write_text(json.dumps(wdl, indent=2))

            # Create adopt_profile.json
            profile = {
                "base_url": "",
                "application_base_url": "",
                "workflow_params": {},
                "security_params": {},
            }
            (action_path / "adopt_profile.json").write_text(json.dumps(profile, indent=2))

            # Create metadata.json
            metadata = {
                "workflow_id": action_id,
                "title": title,
                "description": description,
                "type": "action",
                "env_name": env_name or self._active_env,
                "agent_name": agent_name,
                "action_id": None,
                "is_tool_mode": False,
                "is_visible_in_list": True,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            (action_path / "metadata.json").write_text(json.dumps(metadata, indent=2))

            # Create default test case
            test_case = {
                "prompt": "Test input",
                "workflow_params": {},
                "expected_output": {
                    "description": "Expected output description",
                    "validation": "similarity",
                }
            }
            (action_path / "test_cases" / "test_1.json").write_text(json.dumps(test_case, indent=2))

            return True, action_path, f"Action created: {action_id}"

        except Exception as e:
            if action_path.exists():
                shutil.rmtree(action_path)
            return False, action_path, f"Failed to create action: {e}"

    def _get_action_template(self, template: str, title: str) -> list[dict[str, Any]]:
        """Get action WDL template."""
        if template == "complex":
            return [
                {"metadata": {"title": title, "version": "1.0"}},
                {"id": "apiCall", "operation": "REST", "method": "GET", "url": "", "headers": {}, "body": ""},
                {"id": "extractData", "operation": "EXTRACT", "input": "apiCall", "field": "data"},
                {"id": "smartOutput", "operation": "INTELLIGENT_OUTPUT", "input": "extractData", "preferred_llm": "claude-sonnet-4-5", "instructions": "Present the data clearly."},
                {"required_inputs": []},
            ]
        else:  # simple
            return [
                {"metadata": {"title": title, "version": "1.0"}},
                {"id": "apiCall", "operation": "REST", "method": "GET", "url": "", "headers": {}, "body": ""},
                {"id": "extractData", "operation": "EXTRACT", "input": "apiCall", "field": "data"},
                {"id": "output", "operation": "OUTPUT_TEXT", "format_string": "{}", "values": ["extractData"], "raw": True},
                {"required_inputs": []},
            ]

    def find_action(self, action_id: str, env_name: str | None = None) -> dict[str, Any] | None:
        """
        Find an action by ID within environments.

        Args:
            action_id: Action identifier to find
            env_name: Specific environment to search (None = search all)

        Returns dict with path, env_name, agent_name, and metadata.
        """
        envs_to_search = []
        
        if env_name:
            envs_to_search = [WORKSPACES_DIR / env_name]
        else:
            # Search all environments, prioritizing active env
            if self._active_env:
                active_path = WORKSPACES_DIR / self._active_env
                if active_path.exists():
                    envs_to_search.append(active_path)
            
            for env_item in WORKSPACES_DIR.iterdir():
                if env_item.is_dir() and (env_item / "env.json").exists():
                    if env_item not in envs_to_search:
                        envs_to_search.append(env_item)
        
        # Search environments
        for env_path in envs_to_search:
            if not env_path.exists() or not (env_path / "env.json").exists():
                continue

            # Check env-level actions
            env_actions = env_path / "actions" / action_id
            if env_actions.exists():
                return self._load_action_info(env_actions, env_path.name, None)

            # Check agents
            agents_dir = env_path / "agents"
            if agents_dir.exists():
                for agent_item in agents_dir.iterdir():
                    if not agent_item.is_dir():
                        continue
                    agent_action = agent_item / "actions" / action_id
                    if agent_action.exists():
                        return self._load_action_info(agent_action, env_path.name, agent_item.name)

        return None

    def _load_action_info(
        self,
        action_path: Path,
        env_name: str | None,
        agent_name: str | None,
    ) -> dict[str, Any]:
        """Load action info from path."""
        info: dict[str, Any] = {
            "path": action_path,
            "env_name": env_name,
            "agent_name": agent_name,
            "action_id": action_path.name,
        }

        # Load metadata if exists
        meta_path = action_path / "metadata.json"
        if meta_path.exists():
            try:
                info["metadata"] = json.loads(meta_path.read_text())
            except Exception:
                pass

        # Load WDL if exists
        wdl_path = action_path / "widdle.json"
        if wdl_path.exists():
            try:
                info["wdl"] = json.loads(wdl_path.read_text())
            except Exception:
                pass

        return info

    def list_actions(
        self,
        env_name: str | None = None,
        agent_name: str | None = None,
        include_subactions: bool = True,
    ) -> list[dict[str, Any]]:
        """
        List actions within an environment.
        
        Args:
            env_name: Environment to list from (uses active env if None)
            agent_name: Specific agent to list from
            include_subactions: Include sub-actions from agents
        
        Returns:
            List of action info dicts
        """
        actions = []
        env = env_name or self._active_env
        
        if not env:
            # No environment - return empty list
            return actions

        if agent_name:
            # List actions in specific agent
            agent_actions_dir = WORKSPACES_DIR / env / "agents" / agent_name / "actions"
            if agent_actions_dir.exists():
                for item in agent_actions_dir.iterdir():
                    if item.is_dir() and (item / "widdle.json").exists():
                        info = self._load_action_info(item, env, agent_name)
                        actions.append(info)
        else:
            # List actions in environment
            env_actions_dir = WORKSPACES_DIR / env / "actions"
            if env_actions_dir.exists():
                for item in env_actions_dir.iterdir():
                    if item.is_dir() and (item / "widdle.json").exists():
                        info = self._load_action_info(item, env, None)
                        actions.append(info)

            # Include agent sub-actions if requested
            if include_subactions:
                agents_dir = WORKSPACES_DIR / env / "agents"
                if agents_dir.exists():
                    for agent_item in agents_dir.iterdir():
                        if agent_item.is_dir():
                            agent_actions_dir = agent_item / "actions"
                            if agent_actions_dir.exists():
                                for item in agent_actions_dir.iterdir():
                                    if item.is_dir() and (item / "widdle.json").exists():
                                        info = self._load_action_info(item, env, agent_item.name)
                                        actions.append(info)

        return actions

    # =========================================================================
    # Configuration Inheritance
    # =========================================================================

    def resolve_adopt_profile(
        self,
        action_path: Path | None = None,
        agent_name: str | None = None,
        env_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Resolve adopt_profile.json with inheritance.

        Priority: action -> agent -> env
        """
        profiles_to_check: list[Path] = []

        # Action level
        if action_path and (action_path / "adopt_profile.json").exists():
            profiles_to_check.append(action_path / "adopt_profile.json")

        # Agent level
        env = env_name or self._active_env
        if agent_name and env:
            agent_profile = WORKSPACES_DIR / env / "agents" / agent_name / "adopt_profile.json"
            if agent_profile.exists():
                profiles_to_check.append(agent_profile)

        # Environment level (required - this is where config lives)
        if env:
            env_profile = WORKSPACES_DIR / env / "adopt_profile.json"
            if env_profile.exists():
                profiles_to_check.append(env_profile)

        # Merge profiles (later ones provide defaults for missing keys)
        merged: dict[str, Any] = {}
        for profile_path in reversed(profiles_to_check):
            try:
                profile_data = json.loads(profile_path.read_text())
                self._deep_merge(merged, profile_data)
            except Exception:
                continue

        return merged

    def _deep_merge(self, base: dict, override: dict) -> None:
        """
        Deep merge override into base, skipping empty values.
        
        Empty values (empty string, empty dict, empty list, None) in override
        do NOT overwrite existing values in base. This enables fallback:
        action -> agent -> env (only non-empty values override).
        """
        for key, value in override.items():
            # Skip empty values - let base value remain
            if value is None:
                continue
            if isinstance(value, str) and value == "":
                continue
            if isinstance(value, dict) and len(value) == 0:
                continue
            if isinstance(value, list) and len(value) == 0:
                continue
            
            # Deep merge nested dicts
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def resolve_env_file(
        self,
        env_name: str | None = None,
    ) -> Path | None:
        """
        Resolve .env file with inheritance.

        Priority: env -> root
        """
        env = env_name or self._active_env

        # Environment level
        if env:
            env_file = WORKSPACES_DIR / env / ".env"
            if env_file.exists():
                return env_file

        # Root level
        root_env = WORKSPACES_DIR / ".env"
        if root_env.exists():
            return root_env

        return None

    def load_env_vars(self, env_name: str | None = None) -> None:
        """Load environment variables from resolved .env file."""
        env_file = self.resolve_env_file(env_name)
        if env_file:
            load_dotenv(env_file, override=True)

    # =========================================================================
    # Workspace Detection
    # =========================================================================

    def detect_workspace_type(self, path: Path) -> WorkspaceType | None:
        """Detect the type of workspace at the given path."""
        if (path / "env.json").exists():
            return WorkspaceType.ENVIRONMENT
        elif (path / "agent.json").exists():
            return WorkspaceType.AGENT
        elif (path / "widdle.json").exists():
            return WorkspaceType.ACTION
        return None

    def get_workspace_context(self, action_id: str) -> dict[str, Any]:
        """
        Get full workspace context for an action.

        Returns env_name, agent_name, path, resolved profile, etc.
        """
        action_info = self.find_action(action_id)
        if not action_info:
            return {}

        context = {
            "action_id": action_id,
            "path": action_info["path"],
            "env_name": action_info.get("env_name"),
            "agent_name": action_info.get("agent_name"),
            "metadata": action_info.get("metadata", {}),
        }

        # Resolve profile
        context["resolved_profile"] = self.resolve_adopt_profile(
            action_path=action_info["path"],
            agent_name=action_info.get("agent_name"),
            env_name=action_info.get("env_name"),
        )

        # Resolve env file
        env_file = self.resolve_env_file(action_info.get("env_name"))
        context["env_file"] = str(env_file) if env_file else None

        return context


# Singleton instance for convenience
_manager: HierarchicalWorkspaceManager | None = None


def get_workspace_manager() -> HierarchicalWorkspaceManager:
    """Get or create the workspace manager singleton."""
    global _manager
    if _manager is None:
        _manager = HierarchicalWorkspaceManager()
    return _manager


# Alias for convenience
WorkspaceManager = HierarchicalWorkspaceManager


