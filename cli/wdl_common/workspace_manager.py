#!/usr/bin/env python3
"""
Workspace Manager - Integrates with tool_builder_agents/ structure.

Provides unified workspace management for WDL actions that integrates
with the existing Tool Builder agent structure.

Workspace Structure (integrated):
    tool_builder_agents/
    └── {agent_name}/
        ├── agent.json           # Agent WDL config
        ├── tools/               # Existing tools
        ├── tests/               # Test cases
        ├── docs/                # Documentation
        └── workflows/           # NEW: Complex WDL workflows
            └── {workflow_id}/
                ├── requirements.md
                ├── widdle.json
                ├── description.txt
                ├── adopt_profile.json
                ├── test_cases/
                ├── traces/
                ├── versions/
                └── cursor_instructions.md
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Tool Builder agents directory
AGENTS_BASE_DIR = Path(__file__).parent.parent.parent / "tool_builder_agents"

# Standalone actions directory (outside cli/)
STANDALONE_ACTIONS_DIR = Path(__file__).parent.parent.parent / "actions"


class WorkspaceManager:
    """
    Manages workspaces for WDL workflow development.

    Integrates with existing tool_builder_agents/ structure while
    supporting standalone mode for quick local development.
    """

    def __init__(self, use_agents: bool = True) -> None:
        """
        Initialize workspace manager.

        Args:
            use_agents: If True, use tool_builder_agents/ structure.
                       If False, use standalone cli/actions/ structure.
        """
        self.use_agents = use_agents and AGENTS_BASE_DIR.exists()
        self.base_dir = AGENTS_BASE_DIR if self.use_agents else STANDALONE_ACTIONS_DIR

    # =========================================================================
    # Agent Management (when using agents)
    # =========================================================================

    def list_agents(self) -> List[Dict[str, Any]]:
        """
        List all available agents.

        Returns:
            List of agent info dictionaries
        """
        if not self.use_agents:
            return []

        agents_config = AGENTS_BASE_DIR / "agents.json"
        if not agents_config.exists():
            return []

        try:
            with open(agents_config, "r") as f:
                agents = json.load(f)
            return [{"name": k, **v} for k, v in agents.items()]
        except Exception:
            return []

    def get_agent_path(self, agent_name: str) -> Optional[Path]:
        """
        Get path to an agent's directory.

        Args:
            agent_name: Name of the agent

        Returns:
            Path to agent directory or None if not found
        """
        if not self.use_agents:
            return None

        agent_path = AGENTS_BASE_DIR / agent_name
        return agent_path if agent_path.exists() else None

    def select_agent_interactive(self) -> Optional[str]:
        """
        Interactive agent selection.

        Returns:
            Selected agent name or None
        """
        agents = self.list_agents()

        if not agents:
            print("\n⚠️  No agents found. Create one using tool_builder.py first.")
            print("   Or use --standalone mode for local-only development.")
            return None

        print("\n" + "=" * 60)
        print("📁 SELECT AGENT")
        print("=" * 60)
        for i, agent in enumerate(agents, 1):
            print(f"   {i}. {agent['name']}")
        print("=" * 60)

        while True:
            choice = input(f"👉 Select (1-{len(agents)}) or 'cancel': ").strip()

            if choice.lower() == "cancel":
                return None

            try:
                idx = int(choice) - 1
                if 0 <= idx < len(agents):
                    return agents[idx]["name"]
            except ValueError:
                pass

            print("⚠️  Invalid choice.")

        return None

    # =========================================================================
    # Workflow Workspace Management
    # =========================================================================

    def get_workflows_dir(self, agent_name: Optional[str] = None) -> Path:
        """
        Get the workflows directory for an agent or standalone mode.

        Args:
            agent_name: Name of the agent (None for standalone)

        Returns:
            Path to workflows directory
        """
        if self.use_agents and agent_name:
            workflows_dir = AGENTS_BASE_DIR / agent_name / "workflows"
        else:
            workflows_dir = STANDALONE_ACTIONS_DIR

        workflows_dir.mkdir(parents=True, exist_ok=True)
        return workflows_dir

    def create_workspace(
        self,
        workflow_id: str,
        title: str,
        requirements: str,
        agent_name: Optional[str] = None,
        wdl: Optional[List[Dict[str, Any]]] = None,
        tool_context: Optional[str] = None,
        api_specs: Optional[List[Dict[str, Any]]] = None,
        tool_specs: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[bool, Path, str]:
        """
        Create a new workflow workspace.

        Args:
            workflow_id: Unique ID for the workflow
            title: Workflow title
            requirements: Requirements document content
            agent_name: Agent name (None for standalone)
            wdl: Optional initial WDL
            tool_context: Optional tool context for generation
            api_specs: Optional list of full API specification dictionaries
            tool_specs: Optional list of full tool specification dictionaries

        Returns:
            Tuple of (success, workspace_path, message)
        """
        workflows_dir = self.get_workflows_dir(agent_name)
        workspace = workflows_dir / workflow_id

        if workspace.exists():
            return False, workspace, f"Workspace already exists: {workspace}"

        try:
            # Create workspace structure
            workspace.mkdir(parents=True)
            (workspace / "test_cases").mkdir()
            (workspace / "traces").mkdir()
            (workspace / "versions").mkdir()
            (workspace / "apis").mkdir()
            (workspace / "tools").mkdir()

            # Save requirements
            (workspace / "requirements.md").write_text(requirements)

            # Save description
            (workspace / "description.txt").write_text(
                f"Workflow: {title}\n\nGenerated from requirements."
            )

            # Save initial WDL
            initial_wdl = wdl or [
                {
                    "id": "placeholder",
                    "operation": "OUTPUT_TEXT",
                    "format_string": f"TODO: Implement WDL for {title}",
                    "values": [],
                    "raw": True,
                }
            ]
            (workspace / "widdle.json").write_text(
                json.dumps(initial_wdl, indent=2)
            )

            # Create adopt profile template
            profile = {
                "base_url": "",
                "application_base_url": "",
                "workflow_params": {},
                "security_params": {},
            }
            (workspace / "adopt_profile.json").write_text(
                json.dumps(profile, indent=2)
            )

            # Create default test case template
            test_case = {
                "prompt": "Test input for the workflow",
                "workflow_params": {},
                "expected_output": {
                    "description": "What the output should contain or look like",
                    "validation": "similarity",
                    "key_fields": ["field1", "field2"],
                    "sample_output": "Example of expected output format"
                }
            }
            (workspace / "test_cases" / "test_1.json").write_text(
                json.dumps(test_case, indent=2)
            )

            # Save context files if provided
            if tool_context:
                (workspace / "tool_context.md").write_text(tool_context)
            
            # Save full API specs as JSON files
            if api_specs:
                api_ids = []
                for api_spec in api_specs:
                    api_id = api_spec.get("id") or api_spec.get("api_id")
                    if api_id:
                        api_ids.append(api_id)
                        api_file = workspace / "apis" / f"{api_id}.json"
                        api_file.write_text(json.dumps(api_spec, indent=2))
                
                # Create manifest file listing all API IDs
                manifest = {
                    "api_ids": api_ids,
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
                (workspace / "apis" / "manifest.json").write_text(
                    json.dumps(manifest, indent=2)
                )

            # Save full tool specs as JSON files
            if tool_specs:
                tool_ids = []
                for tool_spec in tool_specs:
                    tool_id = tool_spec.get("id") or tool_spec.get("action_id")
                    if tool_id:
                        tool_ids.append(tool_id)
                        tool_file = workspace / "tools" / f"{tool_id}.json"
                        tool_file.write_text(json.dumps(tool_spec, indent=2))
                
                # Create manifest file listing all tool IDs
                manifest = {
                    "tool_ids": tool_ids,
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
                (workspace / "tools" / "manifest.json").write_text(
                    json.dumps(manifest, indent=2)
                )

            # Track metadata
            metadata = {
                "workflow_id": workflow_id,
                "title": title,
                "agent_name": agent_name,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            (workspace / "metadata.json").write_text(json.dumps(metadata, indent=2))

            return True, workspace, f"Workspace created: {workspace}"

        except Exception as e:
            # Cleanup on failure
            import shutil

            if workspace.exists():
                shutil.rmtree(workspace)
            return False, workspace, f"Failed to create workspace: {e}"

    def load_workspace(
        self, workflow_id: str, agent_name: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Load workspace data.

        Args:
            workflow_id: Workflow ID
            agent_name: Agent name (None for standalone)

        Returns:
            Tuple of (success, workspace_data, message)
        """
        workflows_dir = self.get_workflows_dir(agent_name)
        workspace = workflows_dir / workflow_id

        if not workspace.exists():
            # Try standalone if agent mode didn't find it
            if self.use_agents and agent_name:
                workspace = STANDALONE_ACTIONS_DIR / workflow_id
                if not workspace.exists():
                    return False, {}, f"Workspace not found: {workflow_id}"

        data: Dict[str, Any] = {"workspace_path": workspace}

        try:
            # Load WDL
            wdl_path = workspace / "widdle.json"
            if wdl_path.exists():
                data["wdl"] = json.loads(wdl_path.read_text())

            # Load profile
            profile_path = workspace / "adopt_profile.json"
            if profile_path.exists():
                data["profile"] = json.loads(profile_path.read_text())

            # Load requirements
            req_path = workspace / "requirements.md"
            if req_path.exists():
                data["requirements"] = req_path.read_text()

            # Load metadata
            meta_path = workspace / "metadata.json"
            if meta_path.exists():
                data["metadata"] = json.loads(meta_path.read_text())

            # Load tool specs if exists
            tools_dir = workspace / "tools"
            if tools_dir.exists():
                tools = []
                for tool_file in tools_dir.glob("*.json"):
                    if tool_file.name == "manifest.json":
                        continue
                    try:
                        tool_spec = json.loads(tool_file.read_text())
                        tools.append(tool_spec)
                    except Exception:
                        continue
                data["tools"] = tools

            # Load tool context if exists
            tool_ctx_path = workspace / "tool_context.md"
            if tool_ctx_path.exists():
                data["tool_context"] = tool_ctx_path.read_text()

            return True, data, "Workspace loaded"

        except Exception as e:
            return False, {"workspace_path": workspace}, f"Error loading workspace: {e}"

    def list_workspaces(
        self, agent_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List all workspaces for an agent or standalone.

        Args:
            agent_name: Agent name (None for standalone)

        Returns:
            List of workspace info dictionaries
        """
        workflows_dir = self.get_workflows_dir(agent_name)
        workspaces = []

        for item in workflows_dir.iterdir():
            if item.is_dir() and (item / "widdle.json").exists():
                meta_path = item / "metadata.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text())
                        meta["path"] = str(item)
                        workspaces.append(meta)
                    except Exception:
                        workspaces.append(
                            {"workflow_id": item.name, "path": str(item)}
                        )
                else:
                    workspaces.append({"workflow_id": item.name, "path": str(item)})

        return workspaces

    def display_workspaces(self, agent_name: Optional[str] = None) -> None:
        """Display workspaces in a formatted table."""
        workspaces = self.list_workspaces(agent_name)

        if not workspaces:
            print("\n⚠️  No workspaces found.")
            return

        print("\n" + "=" * 70)
        mode = f"Agent: {agent_name}" if agent_name else "Standalone"
        print(f"📁 WORKSPACES ({mode})")
        print("=" * 70)
        print(f"{'ID':<30} {'Title':<30} {'Created'}")
        print("-" * 70)

        for ws in workspaces:
            wid = ws.get("workflow_id", "N/A")[:28]
            title = ws.get("title", "Untitled")[:28]
            created = ws.get("created_at", "N/A")[:10]
            print(f"{wid:<30} {title:<30} {created}")

        print("=" * 70)

    # =========================================================================
    # API Management
    # =========================================================================

    def add_api_to_workspace(
        self,
        workflow_id: str,
        api_id: str,
        api_spec: Dict[str, Any],
        agent_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Add an API specification to a workspace.

        Args:
            workflow_id: Workflow ID
            api_id: API ID
            api_spec: Full API specification dictionary
            agent_name: Agent name (None for standalone)

        Returns:
            Tuple of (success, message)
        """
        workflows_dir = self.get_workflows_dir(agent_name)
        workspace = workflows_dir / workflow_id

        if not workspace.exists():
            return False, f"Workspace not found: {workflow_id}"

        try:
            # Ensure apis directory exists
            apis_dir = workspace / "apis"
            apis_dir.mkdir(exist_ok=True)

            # Save API spec
            api_file = apis_dir / f"{api_id}.json"
            api_file.write_text(json.dumps(api_spec, indent=2))

            # Update manifest
            manifest_file = apis_dir / "manifest.json"
            if manifest_file.exists():
                manifest = json.loads(manifest_file.read_text())
            else:
                manifest = {
                    "api_ids": [],
                    "created_at": datetime.now().isoformat(),
                }

            if api_id not in manifest["api_ids"]:
                manifest["api_ids"].append(api_id)
            manifest["updated_at"] = datetime.now().isoformat()

            manifest_file.write_text(json.dumps(manifest, indent=2))

            return True, f"API {api_id} added to workspace"

        except Exception as e:
            return False, f"Failed to add API: {e}"

    def remove_api_from_workspace(
        self,
        workflow_id: str,
        api_id: str,
        agent_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Remove an API specification from a workspace.

        Args:
            workflow_id: Workflow ID
            api_id: API ID to remove
            agent_name: Agent name (None for standalone)

        Returns:
            Tuple of (success, message)
        """
        workflows_dir = self.get_workflows_dir(agent_name)
        workspace = workflows_dir / workflow_id

        if not workspace.exists():
            return False, f"Workspace not found: {workflow_id}"

        try:
            apis_dir = workspace / "apis"
            api_file = apis_dir / f"{api_id}.json"

            if not api_file.exists():
                return False, f"API {api_id} not found in workspace"

            # Remove API file
            api_file.unlink()

            # Update manifest
            manifest_file = apis_dir / "manifest.json"
            if manifest_file.exists():
                manifest = json.loads(manifest_file.read_text())
                if api_id in manifest["api_ids"]:
                    manifest["api_ids"].remove(api_id)
                manifest["updated_at"] = datetime.now().isoformat()
                manifest_file.write_text(json.dumps(manifest, indent=2))

            return True, f"API {api_id} removed from workspace"

        except Exception as e:
            return False, f"Failed to remove API: {e}"

    def list_workspace_apis(
        self,
        workflow_id: str,
        agent_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List all APIs associated with a workspace.

        Args:
            workflow_id: Workflow ID
            agent_name: Agent name (None for standalone)

        Returns:
            List of API dictionaries
        """
        workflows_dir = self.get_workflows_dir(agent_name)
        workspace = workflows_dir / workflow_id

        if not workspace.exists():
            return []

        apis_dir = workspace / "apis"
        if not apis_dir.exists():
            return []

        apis = []
        for api_file in apis_dir.glob("*.json"):
            if api_file.name == "manifest.json":
                continue

            try:
                api_spec = json.loads(api_file.read_text())
                apis.append(api_spec)
            except Exception:
                continue

        return apis

    # =========================================================================
    # Tool Management
    # =========================================================================

    def add_tool_to_workspace(
        self,
        workflow_id: str,
        tool_id: str,
        tool_spec: Dict[str, Any],
        agent_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Add a tool specification to a workspace.

        Args:
            workflow_id: Workflow ID
            tool_id: Tool ID
            tool_spec: Full tool specification dictionary
            agent_name: Agent name (None for standalone)

        Returns:
            Tuple of (success, message)
        """
        workflows_dir = self.get_workflows_dir(agent_name)
        workspace = workflows_dir / workflow_id

        if not workspace.exists():
            return False, f"Workspace not found: {workflow_id}"

        try:
            # Ensure tools directory exists
            tools_dir = workspace / "tools"
            tools_dir.mkdir(exist_ok=True)

            # Save tool spec
            tool_file = tools_dir / f"{tool_id}.json"
            tool_file.write_text(json.dumps(tool_spec, indent=2))

            # Update manifest
            manifest_file = tools_dir / "manifest.json"
            if manifest_file.exists():
                manifest = json.loads(manifest_file.read_text())
            else:
                manifest = {
                    "tool_ids": [],
                    "created_at": datetime.now().isoformat(),
                }

            if tool_id not in manifest["tool_ids"]:
                manifest["tool_ids"].append(tool_id)
            manifest["updated_at"] = datetime.now().isoformat()

            manifest_file.write_text(json.dumps(manifest, indent=2))

            return True, f"Tool {tool_id} added to workspace"

        except Exception as e:
            return False, f"Failed to add tool: {e}"

    def remove_tool_from_workspace(
        self,
        workflow_id: str,
        tool_id: str,
        agent_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Remove a tool specification from a workspace.

        Args:
            workflow_id: Workflow ID
            tool_id: Tool ID to remove
            agent_name: Agent name (None for standalone)

        Returns:
            Tuple of (success, message)
        """
        workflows_dir = self.get_workflows_dir(agent_name)
        workspace = workflows_dir / workflow_id

        if not workspace.exists():
            return False, f"Workspace not found: {workflow_id}"

        try:
            tools_dir = workspace / "tools"
            tool_file = tools_dir / f"{tool_id}.json"

            if not tool_file.exists():
                return False, f"Tool {tool_id} not found in workspace"

            # Remove tool file
            tool_file.unlink()

            # Update manifest
            manifest_file = tools_dir / "manifest.json"
            if manifest_file.exists():
                manifest = json.loads(manifest_file.read_text())
                if tool_id in manifest["tool_ids"]:
                    manifest["tool_ids"].remove(tool_id)
                manifest["updated_at"] = datetime.now().isoformat()
                manifest_file.write_text(json.dumps(manifest, indent=2))

            return True, f"Tool {tool_id} removed from workspace"

        except Exception as e:
            return False, f"Failed to remove tool: {e}"

    def list_workspace_tools(
        self,
        workflow_id: str,
        agent_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List all tools associated with a workspace.

        Args:
            workflow_id: Workflow ID
            agent_name: Agent name (None for standalone)

        Returns:
            List of tool dictionaries
        """
        workflows_dir = self.get_workflows_dir(agent_name)
        workspace = workflows_dir / workflow_id

        if not workspace.exists():
            return []

        tools_dir = workspace / "tools"
        if not tools_dir.exists():
            return []

        tools = []
        for tool_file in tools_dir.glob("*.json"):
            if tool_file.name == "manifest.json":
                continue

            try:
                tool_spec = json.loads(tool_file.read_text())
                tools.append(tool_spec)
            except Exception:
                continue

        return tools


