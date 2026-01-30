#!/usr/bin/env python3
"""
Workspace Manager V2 - Hierarchical Workspace Management.

Supports three-level hierarchy:
1. Environment (Env) Level - Top-level workspace with shared config
2. Agent Level - Uber Agent containing sub-actions
3. Action Level - Individual action workspace

Directory Structure:
    workspaces/
    ├── .env                     # Root fallback .env
    ├── adopt_profile.json       # Root fallback profile
    ├── {env_name}/              # Environment workspace
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
    └── standalone/              # Global standalone actions
        └── {action_id}/
"""

import json
import os
import shutil
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

# Base directories
PROJECT_ROOT = Path(__file__).parent.parent.parent
WORKSPACES_DIR = PROJECT_ROOT / "workspaces"
LEGACY_ACTIONS_DIR = PROJECT_ROOT / "actions"
LEGACY_AGENTS_DIR = PROJECT_ROOT / "tool_builder_agents"


class WorkspaceType(Enum):
    """Type of workspace."""
    ENVIRONMENT = "environment"
    AGENT = "agent"
    ACTION = "action"
    STANDALONE = "standalone"


class HierarchicalWorkspaceManager:
    """
    Manages hierarchical workspaces with configuration inheritance.

    Supports:
    - Environment-level workspaces with shared .env and adopt_profile.json
    - Agent-level workspaces (Uber Agents) with sub-actions
    - Action-level workspaces (standalone or within agents)
    - Configuration inheritance (action -> agent -> env -> root)
    """

    def __init__(self) -> None:
        """Initialize workspace manager."""
        self._ensure_base_structure()
        self._active_env: str | None = None
        self._load_active_env()

    def _ensure_base_structure(self) -> None:
        """Ensure base workspace structure exists."""
        WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
        (WORKSPACES_DIR / "standalone").mkdir(exist_ok=True)

        # Create root fallback files if they don't exist
        root_profile = WORKSPACES_DIR / "adopt_profile.json"
        if not root_profile.exists():
            root_profile.write_text(json.dumps({
                "base_url": "",
                "application_base_url": "",
                "workflow_params": {},
                "security_params": {},
            }, indent=2))

        root_env = WORKSPACES_DIR / ".env"
        if not root_env.exists():
            root_env.write_text("# Root environment variables\n")

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
    ) -> tuple[bool, Path, str]:
        """
        Create a new environment workspace.

        Args:
            env_id: Unique environment identifier
            name: Human-readable name
            description: Environment description
            target: Target type (staging, production)
            client: Optional client identifier

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
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "agents": [],
                "standalone_actions": [],
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

            # Create default .env
            (env_path / ".env").write_text(f"# Environment: {name}\n# Target: {target}\n")

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
            env_name: Environment (None for standalone)
            agent_name: Agent to create under (None for standalone in env)
            template: Template to use

        Returns:
            Tuple of (success, path, message)
        """
        # Determine action path
        if agent_name:
            env = env_name or self._active_env
            if not env:
                return False, Path(), "No environment specified for agent action"
            if not self.agent_exists(agent_name, env):
                return False, Path(), f"Agent not found: {agent_name}"
            action_path = WORKSPACES_DIR / env / "agents" / agent_name / "actions" / action_id
        elif env_name or self._active_env:
            env = env_name or self._active_env
            action_path = WORKSPACES_DIR / env / "actions" / action_id
        else:
            action_path = WORKSPACES_DIR / "standalone" / action_id

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

    def find_action(self, action_id: str) -> dict[str, Any] | None:
        """
        Find an action by ID across all workspaces.

        Returns dict with path, env_name, agent_name, and metadata.
        """
        # Check standalone first
        standalone_path = WORKSPACES_DIR / "standalone" / action_id
        if standalone_path.exists():
            return self._load_action_info(standalone_path, None, None)

        # Check legacy actions directory
        legacy_path = LEGACY_ACTIONS_DIR / action_id
        if legacy_path.exists():
            return self._load_action_info(legacy_path, None, None)

        # Check all environments
        for env_item in WORKSPACES_DIR.iterdir():
            if not env_item.is_dir() or env_item.name in ("standalone", ".git"):
                continue
            if not (env_item / "env.json").exists():
                continue

            # Check env-level actions
            env_actions = env_item / "actions" / action_id
            if env_actions.exists():
                return self._load_action_info(env_actions, env_item.name, None)

            # Check agents
            agents_dir = env_item / "agents"
            if agents_dir.exists():
                for agent_item in agents_dir.iterdir():
                    if not agent_item.is_dir():
                        continue
                    agent_action = agent_item / "actions" / action_id
                    if agent_action.exists():
                        return self._load_action_info(agent_action, env_item.name, agent_item.name)

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
        """List actions based on scope."""
        actions = []

        if agent_name:
            # List actions in specific agent
            env = env_name or self._active_env
            if env:
                agent_actions_dir = WORKSPACES_DIR / env / "agents" / agent_name / "actions"
                if agent_actions_dir.exists():
                    for item in agent_actions_dir.iterdir():
                        if item.is_dir() and (item / "widdle.json").exists():
                            info = self._load_action_info(item, env, agent_name)
                            actions.append(info)
        elif env_name or self._active_env:
            # List actions in environment
            env = env_name or self._active_env
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
        else:
            # List standalone actions
            standalone_dir = WORKSPACES_DIR / "standalone"
            if standalone_dir.exists():
                for item in standalone_dir.iterdir():
                    if item.is_dir() and (item / "widdle.json").exists():
                        info = self._load_action_info(item, None, None)
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

        Priority: action -> agent -> env -> root
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

        # Environment level
        if env:
            env_profile = WORKSPACES_DIR / env / "adopt_profile.json"
            if env_profile.exists():
                profiles_to_check.append(env_profile)

        # Root level
        root_profile = WORKSPACES_DIR / "adopt_profile.json"
        if root_profile.exists():
            profiles_to_check.append(root_profile)

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
        """Deep merge override into base."""
        for key, value in override.items():
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
            # Check if it's under standalone
            if "standalone" in path.parts:
                return WorkspaceType.STANDALONE
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


