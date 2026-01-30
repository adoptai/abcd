#!/usr/bin/env python3
"""
Manage WDL actions - unified CLI for creating both simple tools and complex workflows.

This is the main entry point for:
- Simple tools (single REST API call → OUTPUT)
- Complex workflows (multi-step with data transformations, AI, etc.)

Templates:
- simple: Single API wrapper tool (REST → OUTPUT_TEXT)
- workflow: Complex multi-step workflow

Usage:
    # Create simple tool from API
    python manage_wdl_action.py --create --template simple --use-api <api-id> -t "My Tool"
    
    # Create complex workflow
    python manage_wdl_action.py --create --template workflow -r requirements.md -t "My Workflow"
    
    # List available APIs/tools/actions/workflows
    python manage_wdl_action.py --list-apis
    python manage_wdl_action.py --list-tools
    python manage_wdl_action.py --list-all-actions  # execution_type=DEFAULT
    python manage_wdl_action.py --list-workflows    # execution_type=WORKFLOW
    
    # Update existing (add APIs/tools)
    python manage_wdl_action.py --update --workflow-id abc123 --use-api api-1
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import AdoptAPIClient
from cli.wdl_common.cursor_prompt_builder import RoamingInstructionsBuilder
from cli.wdl_common.tool_discovery import ToolDiscovery
from cli.wdl_common.wdl_documentation import WDLDocumentationProvider
from cli.wdl_common.workspace_manager import WorkspaceManager

# Global verbose flag
_verbose = False


def _verbose_print(func_name: str, stage: str, extra: str = "") -> None:
    """Print verbose message if verbose mode is enabled."""
    if _verbose:
        msg = f"[VERBOSE] {func_name}: {stage}"
        if extra:
            msg += f" - {extra}"
        print(msg, file=sys.stderr)


def _save_draft_for_workspace(
    workspace: Path,
    action_id: str,
    workflow_id: str,
) -> Tuple[bool, str]:
    """
    Helper function to save draft for a workspace.
    
    Args:
        workspace: Workspace path
        action_id: Remote action ID
        workflow_id: Workflow ID (for logging)
    
    Returns:
        Tuple of (success, version_number)
    """
    _verbose_print("_save_draft_for_workspace", "ENTER", f"action_id={action_id}")
    import json
    
    wdl_path = workspace / "widdle.json"
    if not wdl_path.exists():
        _verbose_print("_save_draft_for_workspace", "EXIT", "widdle.json not found")
        return False, ""
    
    _verbose_print("_save_draft_for_workspace", "loading WDL")
    wdl = json.loads(wdl_path.read_text())
    client = AdoptAPIClient()
    
    # Get current state
    _verbose_print("_save_draft_for_workspace", "getting current action state")
    success, current_data, msg = client.get_action(action_id)
    if not success:
        _verbose_print("_save_draft_for_workspace", "EXIT", "get_action failed")
        return False, ""
    
    # Publish WDL
    _verbose_print("_save_draft_for_workspace", "publishing WDL")
    success, msg = client.publish_wdl(action_id, wdl)
    if not success:
        _verbose_print("_save_draft_for_workspace", "EXIT", "publish_wdl failed")
        return False, ""
    
    # Wait for update
    _verbose_print("_save_draft_for_workspace", "waiting for update")
    current_updated_at = current_data.get("updated_at", "") if current_data else ""
    if current_updated_at:
        client.wait_for_update(action_id, current_updated_at, max_retries=10, poll_interval=3)
    
    # Populate instructions
    _verbose_print("_save_draft_for_workspace", "populating instructions")
    client.populate_instructions(action_id)
    
    # Get draft ID
    _verbose_print("_save_draft_for_workspace", "getting draft ID")
    success, data, msg = client.get_action(action_id)
    if not success or not data:
        _verbose_print("_save_draft_for_workspace", "EXIT", "get draft ID failed")
        return False, ""
    
    draft_id = data.get("id", data.get("draft_id", ""))
    
    # Save draft
    _verbose_print("_save_draft_for_workspace", "saving draft")
    success, version, msg = client.save_draft(action_id, draft_id)
    if not success:
        _verbose_print("_save_draft_for_workspace", "EXIT", "save_draft failed")
        return False, ""
    
    # Save version locally
    _verbose_print("_save_draft_for_workspace", "saving version locally")
    (workspace / "current_version.txt").write_text(f"version: {version}\nstatus: draft\n")
    
    # Update metadata
    _verbose_print("_save_draft_for_workspace", "updating metadata")
    metadata_path = workspace / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
    else:
        metadata = {}
    metadata["current_version"] = version
    metadata["status"] = "draft"
    metadata_path.write_text(json.dumps(metadata, indent=2))
    
    _verbose_print("_save_draft_for_workspace", "EXIT", f"success, version={version}")
    return True, version or ""


def list_tools_command(json_output: bool = False, execution_type: str = "TOOL") -> int:
    """
    List available tools/actions from the platform.
    
    Args:
        json_output: Output in JSON format
        execution_type: Type of actions to fetch:
            - "TOOL" (default): Regular tools
            - "DEFAULT": All actions with default execution mode
            - "WORKFLOW": Workflow-type actions
    """
    _verbose_print("list_tools_command", "ENTER", f"json_output={json_output}, execution_type={execution_type}")
    
    # Determine label based on execution_type
    if execution_type == "WORKFLOW":
        label = "workflows"
    elif execution_type == "ALL":
        label = "actions"
    else:
        label = "tools"
    
    print(f"\n⏳ Fetching available {label}...", file=sys.stderr)

    try:
        _verbose_print("list_tools_command", "creating ToolDiscovery")
        discovery = ToolDiscovery(verbose=_verbose)
        _verbose_print("list_tools_command", "calling fetch_tools", f"execution_type={execution_type}")
        success, tools, msg = discovery.fetch_tools(execution_type=execution_type, force_refresh=True)

        if not success:
            print(f"❌ {msg}", file=sys.stderr)
            _verbose_print("list_tools_command", "EXIT", "fetch failed")
            return 1

        if json_output:
            # Machine-readable output for Cursor
            _verbose_print("list_tools_command", "exporting JSON")
            print(discovery.export_search_results_json(tools))
        else:
            _verbose_print("list_tools_command", "displaying tools")
            discovery.display_tools(tools)
            print(f"\n💡 Use --use-tool <ID> to include a tool's WDL as context")
        _verbose_print("list_tools_command", "EXIT", "success")
        return 0

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        _verbose_print("list_tools_command", "EXIT", f"exception: {e}")
        return 1


def list_apis_command(json_output: bool = False) -> int:
    """List available APIs from the platform."""
    _verbose_print("list_apis_command", "ENTER", f"json_output={json_output}")
    print("\n⏳ Fetching available APIs...", file=sys.stderr)

    try:
        _verbose_print("list_apis_command", "creating ToolDiscovery")
        discovery = ToolDiscovery(verbose=_verbose)
        _verbose_print("list_apis_command", "calling fetch_apis")
        success, apis, msg = discovery.fetch_apis()

        if not success:
            print(f"❌ {msg}", file=sys.stderr)
            _verbose_print("list_apis_command", "EXIT", "fetch failed")
            return 1

        if json_output:
            # Machine-readable output for Cursor
            import json
            _verbose_print("list_apis_command", "outputting JSON")
            print(json.dumps({"count": len(apis), "apis": apis}, indent=2))
        else:
            # Display all APIs without pagination limit (limit defaults to None)
            _verbose_print("list_apis_command", "displaying APIs")
            discovery.display_apis(apis)
            print("\n💡 Use --use-api <ID> to include an API's spec as context")
        _verbose_print("list_apis_command", "EXIT", "success")
        return 0

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        _verbose_print("list_apis_command", "EXIT", f"exception: {e}")
        return 1


def search_tools_command(query: str, top_k: int = 5, json_output: bool = False) -> int:
    """Search for relevant tools using semantic search."""
    _verbose_print("search_tools_command", "ENTER", f"query={query}, top_k={top_k}")
    print(f"\n🔍 Searching for tools: '{query}'...", file=sys.stderr)

    try:
        _verbose_print("search_tools_command", "creating ToolDiscovery")
        discovery = ToolDiscovery(verbose=_verbose)
        _verbose_print("search_tools_command", "calling semantic_search_tools")
        success, results, msg = discovery.semantic_search_tools(query, top_k)

        if not success:
            print(f"❌ {msg}", file=sys.stderr)
            _verbose_print("search_tools_command", "EXIT", "search failed")
            return 1

        if json_output:
            # Machine-readable output for Cursor
            # Wrap results with type markers for export
            _verbose_print("search_tools_command", "exporting JSON")
            results_with_type = [dict(r, _type="tool") for r in results]
            print(discovery.export_search_results_json(results_with_type))
        else:
            print(f"\n✅ {msg}")
            for i, tool in enumerate(results, 1):
                score = tool.get("_similarity_score", 0)
                print(f"\n{i}. [{score}%] {tool.get('title', 'Untitled')}")
                print(f"   ID: {tool.get('id')}")
                print(f"   {tool.get('description', '')[:100]}...")
            print("\n💡 Use --use-tool <ID> to include a tool's WDL as context")
        _verbose_print("search_tools_command", "EXIT", "success")
        return 0

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        _verbose_print("search_tools_command", "EXIT", f"exception: {e}")
        return 1


def search_apis_command(query: str, top_k: int = 5, json_output: bool = False) -> int:
    """Search for relevant APIs using semantic search."""
    _verbose_print("search_apis_command", "ENTER", f"query={query}, top_k={top_k}")
    print(f"\n🔍 Searching for APIs: '{query}'...", file=sys.stderr)

    try:
        _verbose_print("search_apis_command", "creating ToolDiscovery")
        discovery = ToolDiscovery(verbose=_verbose)
        _verbose_print("search_apis_command", "calling semantic_search_apis")
        success, results, msg = discovery.semantic_search_apis(query, top_k)

        if not success:
            print(f"❌ {msg}", file=sys.stderr)
            _verbose_print("search_apis_command", "EXIT", "search failed")
            return 1

        if json_output:
            # Machine-readable output for Cursor
            # Wrap results with type markers for export
            _verbose_print("search_apis_command", "exporting JSON")
            results_with_type = [dict(r, _type="api") for r in results]
            print(discovery.export_search_results_json(results_with_type))
        else:
            print(f"\n✅ {msg}")
            for i, api in enumerate(results, 1):
                score = api.get("_similarity_score", 0)
                print(f"\n{i}. [{score}%] {api.get('title', api.get('name', 'Untitled'))}")
                print(f"   ID: {api.get('id')}")
                print(f"   {api.get('description', '')[:100]}...")
            print("\n💡 Use --use-api <ID> to include an API's spec as context")
        _verbose_print("search_apis_command", "EXIT", "success")
        return 0

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        _verbose_print("search_apis_command", "EXIT", f"exception: {e}")
        return 1


def auto_discover_command(requirements_path: str, top_k: int = 5) -> int:
    """
    Auto-discover relevant tools AND APIs based on requirements.
    
    Designed for Cursor to call autonomously.
    Outputs JSON for easy parsing.
    After discovery, fetches detailed API information and outputs to console.
    """
    _verbose_print("auto_discover_command", "ENTER", f"requirements_path={requirements_path}, top_k={top_k}")
    print(f"🔍 Auto-discovering tools and APIs for: {requirements_path}", file=sys.stderr)

    try:
        _verbose_print("auto_discover_command", "reading requirements file")
        requirements = Path(requirements_path).read_text()
        _verbose_print("auto_discover_command", "creating ToolDiscovery")
        discovery = ToolDiscovery(verbose=_verbose)
        _verbose_print("auto_discover_command", "calling discover_tools_for_requirements")
        success, results, msg = discovery.discover_tools_for_requirements(requirements, top_k)

        if not success:
            print(f"❌ {msg}", file=sys.stderr)
            _verbose_print("auto_discover_command", "EXIT", "discovery failed")
            return 1

        # Output JSON for Cursor to parse (includes both tools and APIs)
        _verbose_print("auto_discover_command", "exporting JSON results")
        json_output = discovery.export_search_results_json(results)
        print(json_output)
        print(f"\n✅ {msg}", file=sys.stderr)
        
        # Fetch and output detailed API information for discovered APIs
        import json
        api_results = [r for r in results if r.get("_type") == "api"]
        if api_results:
            _verbose_print("auto_discover_command", "fetching API details", f"count={len(api_results)}")
            print(f"\n📥 Fetching detailed API information for {len(api_results)} APIs...", file=sys.stderr)
            for api_result in api_results:
                api_id = api_result.get("id")
                if api_id:
                    _verbose_print("auto_discover_command", "fetching API details", f"api_id={api_id}")
                    success, api_details, detail_msg = discovery.get_api_details(api_id)
                    if success and api_details:
                        print(f"\n📋 Detailed API Information for {api_id}:", file=sys.stderr)
                        print("=" * 80, file=sys.stderr)
                        print(json.dumps(api_details, indent=2), file=sys.stderr)
                        print("=" * 80, file=sys.stderr)
                    else:
                        print(f"⚠️  Could not fetch details for {api_id}: {detail_msg}", file=sys.stderr)
        
        _verbose_print("auto_discover_command", "EXIT", "success")
        return 0

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        _verbose_print("auto_discover_command", "EXIT", f"exception: {e}")
        return 1


def update_workspace_context(
    workflow_id: str,
    workspace: Path,
    discovery: ToolDiscovery,
    workspace_manager: WorkspaceManager,
    use_api_ids: Optional[List[str]] = None,
    use_tool_ids: Optional[List[str]] = None,
    agent_name: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Update an existing workspace with new APIs and/or tools.
    
    Args:
        workflow_id: Workflow ID
        workspace: Workspace path
        discovery: ToolDiscovery instance
        workspace_manager: WorkspaceManager instance
        use_api_ids: List of API IDs to add
        use_tool_ids: List of tool IDs to add
        agent_name: Agent name (None for standalone)
    
    Returns:
        Tuple of (success, message)
    """
    _verbose_print("update_workspace_context", "ENTER", f"workflow_id={workflow_id}")
    added_apis = []
    added_tools = []
    
    # Add APIs
    if use_api_ids:
        _verbose_print("update_workspace_context", "adding APIs", f"count={len(use_api_ids)}")
        print(f"\n🔗 Adding API specs: {', '.join(use_api_ids)}")
        for api_id in use_api_ids:
            success, api_spec, msg = discovery.get_api_details(api_id)
            
            if success and api_spec:
                # Add to workspace
                add_success, add_msg = workspace_manager.add_api_to_workspace(
                    workflow_id=workflow_id,
                    api_id=api_id,
                    api_spec=api_spec,
                    agent_name=agent_name,
                )
                if add_success:
                    added_apis.append(api_id)
                    print(f"   ✅ {api_id}")
                    # Output API details to console
                    print(f"\n   📋 API Details for {api_id}:")
                    print("   " + "=" * 76)
                    print(json.dumps(api_spec, indent=2))
                    print("   " + "=" * 76)
                else:
                    print(f"   ⚠️  Could not add {api_id}: {add_msg}")
            else:
                print(f"   ⚠️  Could not fetch API details for {api_id}: {msg}")
    
    # Add tools
    if use_tool_ids:
        print(f"\n🔧 Adding tool specs: {', '.join(use_tool_ids)}")
        contexts = []
        for tool_id in use_tool_ids:
            # Fetch full tool details
            success, tool_spec, msg = discovery.get_tool_details(tool_id)
            
            if success and tool_spec:
                # Add to workspace using new method
                add_success, add_msg = workspace_manager.add_tool_to_workspace(
                    workflow_id=workflow_id,
                    tool_id=tool_id,
                    tool_spec=tool_spec,
                    agent_name=agent_name,
                )
                if add_success:
                    added_tools.append(tool_id)
                    print(f"   ✅ {tool_id}")
                    # Also get context for markdown reference
                    ctx = discovery.get_tool_context(tool_id)
                    if ctx:
                        contexts.append(ctx)
                else:
                    print(f"   ⚠️  Could not add {tool_id}: {add_msg}")
            else:
                print(f"   ⚠️  Could not fetch tool details for {tool_id}: {msg}")
        
        # Update tool_context.md for backward compatibility
        if contexts:
            tool_context_path = workspace / "tool_context.md"
            existing_context = ""
            if tool_context_path.exists():
                existing_context = tool_context_path.read_text()
            
            new_context = "\n\n---\n\n".join(contexts)
            if existing_context:
                updated_context = existing_context + "\n\n---\n\n" + new_context
            else:
                updated_context = new_context
            
            tool_context_path.write_text(updated_context)
    
    # Update roaming instructions if they exist
    instructions_path = workspace / "cursor_roaming_instructions.md"
    if instructions_path.exists():
        try:
            docs_provider = WDLDocumentationProvider()
            instructions_builder = RoamingInstructionsBuilder(docs_provider)
            
            # Load existing workspace data
            success, workspace_data, msg = workspace_manager.load_workspace(workflow_id, agent_name)
            if success:
                title = workspace_data.get("metadata", {}).get("title", "WDL Workflow")
                
                # Regenerate instructions with updated context
                roaming_instructions = instructions_builder.build_generation_instructions(
                    workspace=workspace,
                    title=title,
                )
                
                # Add tool context references if tools were added
                if added_tools:
                    roaming_instructions += "\n\n---\n\n## 🔧 Building Blocks\n\n"
                    roaming_instructions += "**Tool Context**: Read `tool_context.md` for existing tool WDLs to reference.\n"
                    roaming_instructions += "\nUse these as building blocks for your workflow.\n"
                
                instructions_path.write_text(roaming_instructions)
        except Exception as e:
            print(f"⚠️  Could not update roaming instructions: {e}")
    
    summary = []
    if added_apis:
        summary.append(f"{len(added_apis)} API(s)")
    if added_tools:
        summary.append(f"{len(added_tools)} tool(s)")
    
    if summary:
        _verbose_print("update_workspace_context", "EXIT", f"added {', '.join(summary)}")
        return True, f"Successfully added {', '.join(summary)} to workspace"
    else:
        _verbose_print("update_workspace_context", "EXIT", "nothing added")
        return False, "No APIs or tools were added"


def _generate_simple_tool_placeholder_wdl(api_details: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Generate a placeholder WDL structure for a simple tool.
    
    This creates an initial WDL that the agent will refine based on the API spec.
    
    Args:
        api_details: API details dictionary
        
    Returns:
        List of WDL blocks (placeholder structure)
    """
    _verbose_print("_generate_simple_tool_placeholder_wdl", "ENTER")
    # Extract API info
    canonical_path = api_details.get("canonical_api_endpoint", api_details.get("path", "/api/endpoint"))
    method = api_details.get("method", "GET")
    
    # Build placeholder required_inputs from API parameters
    required_inputs: Dict[str, Any] = {}
    
    # Try to extract parameters from API spec
    params = api_details.get("parameters", api_details.get("request_parameters", []))
    if isinstance(params, list):
        for param in params[:5]:  # Limit to first 5 for placeholder
            param_name = param.get("name", "")
            param_type = param.get("type", "string")
            param_required = param.get("required", False)
            param_desc = param.get("description", f"{param_name} parameter")
            
            if param_name:
                required_inputs[param_name] = {
                    "type": param_type,
                    "definition": f"{param_desc} ({'mandatory' if param_required else 'optional'})"
                }
    
    # Default placeholder if no params found
    if not required_inputs:
        required_inputs["example_param"] = {
            "type": "string",
            "definition": "TODO: Define based on API spec (mandatory)"
        }
    
    # Build URL with workflow_arguments placeholders
    url = canonical_path
    for param_name in required_inputs.keys():
        if f"{{{param_name}}}" in canonical_path or f":{param_name}" in canonical_path:
            url = url.replace(f"{{{param_name}}}", f"{{workflow_arguments.{param_name}}}")
            url = url.replace(f":{param_name}", f"{{workflow_arguments.{param_name}}}")
    
    _verbose_print("_generate_simple_tool_placeholder_wdl", "EXIT")
    return [
        {
            "required_inputs": required_inputs
        },
        {
            "id": "call_api",
            "operation": "REST",
            "method": method,
            "canonical_api_endpoint": canonical_path,
            "url": url,
            "output_key": "api_response"
        },
        {
            "id": "output",
            "operation": "OUTPUT_TEXT",
            "raw": True,
            "inputs": {
                "content": "{api_response}"
            }
        }
    ]


def _generate_simple_tool_instructions(
    api_details: Dict[str, Any],
    title: str,
    workspace: Path,
) -> str:
    """
    Generate roaming instructions for the agent to create a simple tool.
    
    Args:
        api_details: API details dictionary
        title: Tool title
        workspace: Workspace path
        
    Returns:
        Markdown instructions for the agent
    """
    _verbose_print("_generate_simple_tool_instructions", "ENTER", f"title={title}")
    # Load the simple tool template
    template_path = Path(__file__).parent.parent / "prompts" / "templates" / "simple_tool_template.md"
    template_content = ""
    if template_path.exists():
        template_content = template_path.read_text()
    
    instructions = f"""# Simple Tool Creation Instructions

## Tool: {title}

You are creating a **simple API wrapper tool** that follows the REST → OUTPUT pattern.

## Your Task

1. **Review** the API specification in `apis/{api_details.get('id', 'api')}.json`
2. **Refine** the placeholder WDL in `widdle.json` based on the API spec
3. **Ensure** all parameters from the API are properly mapped

## Workspace Contents

- `widdle.json` - Placeholder WDL (refine this!)
- `apis/` - API specification files
- `metadata.json` - Tool metadata

## API Summary

- **ID**: {api_details.get('id', 'N/A')}
- **Title**: {api_details.get('title', api_details.get('name', 'API'))}
- **Method**: {api_details.get('method', 'GET')}
- **Endpoint**: `{api_details.get('canonical_api_endpoint', '/api/endpoint')}`
- **Description**: {api_details.get('description', 'No description')[:500]}

## What to Do

1. Open `widdle.json` and review the placeholder structure
2. Update `required_inputs`:
   - Add ALL parameters from the API spec
   - Use correct types (string, number, boolean, array, object)
   - Mark as (mandatory) or (optional) in definitions
3. Update the REST block:
   - Ensure `canonical_api_endpoint` matches the API spec exactly
   - Update `url` with `{{workflow_arguments.X}}` for path parameters
   - Add `query_params` if the API has query parameters
   - Add `body` if it's a POST/PUT/PATCH request
4. Optionally add transformation blocks (JQ_FILTER, EXTRACT, PROJECT)
5. Ensure OUTPUT_TEXT is last with `raw: true`

---

{template_content}

---

## Full API Specification

See `apis/{api_details.get('id', 'api')}.json` for complete details.
"""
    _verbose_print("_generate_simple_tool_instructions", "EXIT")
    return instructions


def create_simple_tool(
    api_id: str,
    title: str = "New Tool",
    standalone: bool = False,
    agent_name: Optional[str] = None,
    create_remote: bool = False,
) -> int:
    """
    Create a simple tool workspace from an API (agentic pattern).
    
    This creates a workspace with:
    - Placeholder WDL for the agent to refine
    - API specification files
    - Roaming instructions for the agent
    
    The agent (Cursor) then refines the WDL based on the template and API spec.
    
    Args:
        api_id: API ID to create tool from
        title: Tool title (defaults to API title)
        standalone: Use standalone mode
        agent_name: Specific agent to use
        create_remote: Create action on Adopt immediately (usually False)
        
    Returns:
        Exit code (0 for success)
    """
    _verbose_print("create_simple_tool", "ENTER", f"api_id={api_id}, title={title}")
    print("\n" + "=" * 80)
    print("🔧 CREATE SIMPLE TOOL WORKSPACE")
    print("=" * 80)
    print("This creates a workspace for you (the agent) to build a simple API tool.")
    print("=" * 80)
    
    # Initialize components
    _verbose_print("create_simple_tool", "creating ToolDiscovery")
    discovery = ToolDiscovery(verbose=_verbose)
    _verbose_print("create_simple_tool", "creating WorkspaceManager")
    workspace_manager = WorkspaceManager(use_agents=not standalone)
    
    # Fetch API details
    print(f"\n📡 Fetching API details for: {api_id}")
    success, api_details, msg = discovery.get_api_details(api_id)
    
    if not success or not api_details:
        print(f"❌ Failed to fetch API: {msg}")
        return 1
    
    api_title = api_details.get("title", api_details.get("name", "API Tool"))
    final_title = title if title != "New Tool" else api_title
    
    print(f"   ✅ API: {api_title}")
    print(f"   Title: {final_title}")
    
    # Generate workflow ID
    workflow_id = f"{api_id[:8]}-simple"
    
    # Create workspace
    print(f"\n📁 Creating workspace: {workflow_id}")
    
    success, workspace, msg = workspace_manager.create_workspace(
        workflow_id=workflow_id,
        title=final_title,
        requirements=f"Simple API wrapper for: {api_title}\n\nAPI ID: {api_id}",
        agent_name=agent_name,
        api_specs=[api_details],
    )
    
    if not success:
        print(f"❌ Failed to create workspace: {msg}")
        return 1
    
    print(f"   ✅ Workspace created: {workspace}")
    
    # Generate placeholder WDL
    print("\n📝 Generating placeholder WDL...")
    wdl_blocks = _generate_simple_tool_placeholder_wdl(api_details)
    
    wdl_path = workspace / "widdle.json"
    wdl_path.write_text(json.dumps(wdl_blocks, indent=2))
    print(f"   ✅ Placeholder WDL saved to: widdle.json")
    
    # Generate roaming instructions
    print("\n📋 Generating agent instructions...")
    instructions = _generate_simple_tool_instructions(api_details, final_title, workspace)
    
    instructions_path = workspace / "cursor_roaming_instructions.md"
    instructions_path.write_text(instructions)
    print(f"   ✅ Instructions saved to: cursor_roaming_instructions.md")
    
    # Save metadata
    metadata_path = workspace / "metadata.json"
    metadata = {
        "type": "simple_tool",
        "template": "simple",
        "api_id": api_id,
        "title": final_title,
        "workflow_id": workflow_id,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2))
    
    # Create remote action if requested
    action_id: Optional[str] = None
    if create_remote:
        print("\n🌐 Creating remote action...")
        client = AdoptAPIClient()
        
        description = api_details.get("description", f"Tool for {api_title}")[:300]
        
        success, data, msg = client.create_action(
            title=final_title,
            description=description,
            api_ids=[api_id],
        )
        
        if success and data:
            action_id = data.get("action_id")
            print(f"   ✅ Action created: {action_id}")
            
            # Update metadata
            metadata["action_id"] = action_id
            metadata_path.write_text(json.dumps(metadata, indent=2))
            
            # Set deployment rules
            client.set_deployment_rules(action_id)
        else:
            print(f"   ⚠️  Could not create remote action: {msg}")
    
    # Summary
    print("\n" + "=" * 80)
    print("✅ SIMPLE TOOL WORKSPACE READY")
    print("=" * 80)
    print(f"   Workspace: {workspace}")
    print(f"   API: {api_title}")
    print(f"   Title: {final_title}")
    if action_id:
        print(f"   Action ID: {action_id}")
    
    print("\n📌 YOUR NEXT STEPS (as the agent):")
    print(f"   1. 📖 Read: {workspace}/cursor_roaming_instructions.md")
    print(f"   2. 📋 Review API spec: {workspace}/apis/")
    print(f"   3. ✏️  Refine the placeholder WDL in: {workspace}/widdle.json")
    print("   4. 🧪 Test locally: python cli/test_wdl_action.py {workflow_id} --local-only")
    print("   5. 💾 Save draft: python cli/save_wdl_draft.py --workflow-id {workflow_id}")
    print("=" * 80)
    
    _verbose_print("create_simple_tool", "EXIT", "success")
    return 0


def create_wdl_action(
    requirements_path: Optional[str] = None,
    requirements_text: Optional[str] = None,
    title: str = "New WDL Workflow",
    standalone: bool = False,
    agent_name: Optional[str] = None,
    create_remote: bool = False,
    save_draft: bool = False,
    use_tool_ids: Optional[List[str]] = None,
    use_api_ids: Optional[List[str]] = None,
    generate_only: bool = False,
    template: str = "workflow",
) -> int:
    """
    Create a WDL workflow action.

    Args:
        requirements_path: Path to requirements .md file
        requirements_text: Requirements as string
        title: Workflow title
        standalone: Use standalone mode (no agents)
        agent_name: Specific agent to use
        create_remote: Create action on Adopt
        use_tool_ids: Existing tool IDs to include as context
        use_api_ids: API IDs to include as context
        generate_only: Only output Cursor prompt

    Returns:
        Exit code (0 for success)
    """
    _verbose_print("create_wdl_action", "ENTER", f"title={title}, template={template}")
    print("\n" + "=" * 80)
    print("🚀 CREATE WDL WORKFLOW ACTION")
    print("=" * 80)

    # Load requirements
    if requirements_path:
        requirements = Path(requirements_path).read_text()
        print(f"📄 Loaded requirements from: {requirements_path}")
    elif requirements_text:
        requirements = requirements_text
        print("📄 Using provided requirements text")
    else:
        print("Enter requirements (end with Ctrl+D or Ctrl+Z):")
        requirements = sys.stdin.read()

    print(f"\n📋 Requirements preview:\n{requirements[:300]}...")

    # Initialize components
    workspace_manager = WorkspaceManager(use_agents=not standalone)
    discovery = ToolDiscovery()

    # Select agent if using agents
    selected_agent: Optional[str] = None
    if not standalone:
        if agent_name:
            selected_agent = agent_name
        else:
            selected_agent = workspace_manager.select_agent_interactive()
            if selected_agent is None:
                print("\n💡 Tip: Use --standalone for local-only development")
                print("   Or create an agent using: python tool_builder.py")
                # Fall back to standalone
                standalone = True
                workspace_manager = WorkspaceManager(use_agents=False)

    mode = f"Agent: {selected_agent}" if selected_agent else "Standalone"
    print(f"\n📁 Mode: {mode}")

    # Gather tool/API context
    _verbose_print("create_wdl_action", "gathering tool/API context")
    tool_context: Optional[str] = None
    tool_specs: List[Dict[str, Any]] = []  # Full tool specs to save
    api_specs: List[Dict[str, Any]] = []  # Full API specs to save

    if use_api_ids:
        _verbose_print("create_wdl_action", "fetching API specs", f"count={len(use_api_ids)}")
        print(f"\n🔗 Fetching detailed API specs for: {', '.join(use_api_ids)}")
        import json
        
        for api_id in use_api_ids:
            success, api_spec, msg = discovery.get_api_details(api_id)
            
            if success and api_spec:
                api_specs.append(api_spec)
                print(f"   ✅ {api_id}")
                # Output API details to console for Cursor
                print(f"\n   📋 API Details for {api_id}:")
                print("   " + "=" * 76)
                print(json.dumps(api_spec, indent=2))
                print("   " + "=" * 76)
            else:
                print(f"   ⚠️  Could not fetch API details for {api_id}: {msg}")

    if use_tool_ids:
        _verbose_print("create_wdl_action", "fetching tool specs", f"count={len(use_tool_ids)}")
        print(f"\n🔧 Fetching tool specs for: {', '.join(use_tool_ids)}")
        contexts = []
        for tool_id in use_tool_ids:
            # Fetch full tool details (not just context)
            success, tool_spec, msg = discovery.get_tool_details(tool_id)
            if success and tool_spec:
                tool_specs.append(tool_spec)
                # Also get context for markdown reference
                ctx = discovery.get_tool_context(tool_id)
                if ctx:
                    contexts.append(ctx)
                print(f"   ✅ {tool_id}")
            else:
                print(f"   ⚠️  Could not fetch tool details for {tool_id}: {msg}")
        
        if contexts:
            tool_context = "\n\n---\n\n".join(contexts)

    # Initialize documentation provider
    _verbose_print("create_wdl_action", "initializing documentation provider")
    try:
        docs_provider = WDLDocumentationProvider()
        instructions_builder = RoamingInstructionsBuilder(docs_provider)
        print(f"📚 WDL docs found at: {docs_provider.docs_path}")
    except FileNotFoundError as e:
        print(f"⚠️  {e}")
        docs_provider = None
        instructions_builder = None

    # Generate workflow ID
    _verbose_print("create_wdl_action", "generating workflow ID")
    workflow_id: str
    action_id: Optional[str] = None

    if create_remote:
        _verbose_print("create_wdl_action", "creating remote action")
        print("\n🔧 Creating action on Adopt...")
        client = AdoptAPIClient()
        
        # Extract API IDs from api_specs
        api_ids = [spec.get("id") for spec in api_specs if spec.get("id")]

        success, data, msg = client.create_action(
            title=title,
            description=f"Generated from requirements: {requirements[:200]}...",
            api_ids=api_ids if api_ids else None,
        )

        if not success:
            print(f"❌ Failed to create action: {msg}")
            return 1

        action_id = data.get("action_id") if data else None
        workflow_id = action_id or str(uuid4())[:8]
        print(f"✅ Action created: {action_id}")
        if api_ids:
            print(f"   Associated {len(api_ids)} API(s)")

        if action_id:
            client.set_deployment_rules(action_id)
    else:
        workflow_id = str(uuid4())[:8] + ("-local" if standalone else "")
        print(f"\n📁 Creating workspace with ID: {workflow_id}")

    # Create workspace
    _verbose_print("create_wdl_action", "creating workspace")
    success, workspace, msg = workspace_manager.create_workspace(
        workflow_id=workflow_id,
        title=title,
        requirements=requirements,
        agent_name=selected_agent,
        tool_context=tool_context,
        tool_specs=tool_specs,
        api_specs=api_specs,
    )

    if not success:
        print(f"❌ {msg}")
        _verbose_print("create_wdl_action", "EXIT", "workspace creation failed")
        return 1

    print(f"✅ {msg}")

    # Save action_id to metadata if created remotely
    if action_id:
        metadata_path = workspace / "metadata.json"
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text())
        else:
            metadata = {}
        metadata["action_id"] = action_id
        metadata_path.write_text(json.dumps(metadata, indent=2))

    # Generate roaming instructions
    _verbose_print("create_wdl_action", "generating roaming instructions")
    if instructions_builder:
        # Build enhanced instructions with tool/API context
        roaming_instructions = instructions_builder.build_generation_instructions(
            workspace=workspace,
            title=title,
        )

        # Add tool context references
        if tool_context:
            roaming_instructions += "\n\n---\n\n## 🔧 Building Blocks\n\n"
            roaming_instructions += "**Tool Context**: Read `tool_context.md` for existing tool WDLs to reference.\n"
            roaming_instructions += "\nUse these as building blocks for your workflow.\n"

        instructions_path = workspace / "cursor_roaming_instructions.md"
        instructions_path.write_text(roaming_instructions)

    if generate_only:
        print("\n" + "=" * 80)
        print("📝 ROAMING INSTRUCTIONS (copy to Cursor)")
        print("=" * 80)
        if instructions_builder:
            print(roaming_instructions)
        _verbose_print("create_wdl_action", "EXIT", "generate_only mode")
        return 0

    # Save draft if requested (requires action_id and widdle.json)
    _verbose_print("create_wdl_action", "checking save_draft option")
    if save_draft and action_id:
        wdl_path = workspace / "widdle.json"
        if wdl_path.exists():
            print("\n💾 Saving draft to remote...")
            success, version = _save_draft_for_workspace(
                workspace=workspace,
                action_id=action_id,
                workflow_id=workflow_id,
            )
            if success:
                print(f"✅ Draft saved: Version {version}")
            else:
                print("⚠️  Failed to save draft, but workspace is ready")
        else:
            print("\n⚠️  --save-draft requires widdle.json to exist")
            print("   Generate WDL first, then use: python cli/save_wdl_draft.py <action_id>")

    # Display next steps
    print("\n" + "=" * 80)
    print("✅ WORKSPACE READY")
    print("=" * 80)
    print(f"   Workspace: {workspace}")
    if selected_agent:
        print(f"   Agent: {selected_agent}")
    if action_id:
        print(f"   Action ID: {action_id}")
    print("\n📌 NEXT STEPS:")
    print(f"   1. Open in Cursor: cursor {workspace}")
    print("   2. Read cursor_roaming_instructions.md")
    if api_specs:
        print(f"   3. Review apis/ directory for full API specifications ({len(api_specs)} APIs)")
        print("      - Full JSON specs: apis/{api_id}.json")
        print("      - Manifest: apis/manifest.json")
    if tool_specs:
        print(f"   4. Review tools/ directory for full tool specifications ({len(tool_specs)} tools)")
        print("      - Full JSON specs: tools/{tool_id}.json")
        print("      - Manifest: tools/manifest.json")
    if tool_context:
        print("   5. Review tool_context.md for markdown-formatted tool WDLs")
    print("   6. Generate WDL and save to widdle.json")
    if action_id and not save_draft:
        print(f"   7. Save draft: python cli/save_wdl_draft.py {action_id}")
    elif not action_id:
        print("   7. Configure adopt_profile.json with auth")
        print(f"   8. Test: python cli/test_wdl_action.py {workflow_id}")
    print("=" * 80)

    _verbose_print("create_wdl_action", "EXIT", "success")
    return 0


def update_wdl_action(
    workflow_id: str,
    standalone: bool = False,
    agent_name: Optional[str] = None,
    use_tool_ids: Optional[List[str]] = None,
    use_api_ids: Optional[List[str]] = None,
) -> int:
    """
    Update an existing WDL workflow action by adding APIs and/or tools.

    Args:
        workflow_id: Workflow ID to update
        standalone: Use standalone mode (no agents)
        agent_name: Specific agent to use
        use_tool_ids: Tool IDs to add to workspace
        use_api_ids: API IDs to add to workspace

    Returns:
        Exit code (0 for success)
    """
    _verbose_print("update_wdl_action", "ENTER", f"workflow_id={workflow_id}")
    print("\n" + "=" * 80)
    print("🔄 UPDATE WDL WORKFLOW ACTION")
    print("=" * 80)
    print(f"   Workflow ID: {workflow_id}")

    # Initialize components
    _verbose_print("update_wdl_action", "creating WorkspaceManager")
    workspace_manager = WorkspaceManager(use_agents=not standalone)
    _verbose_print("update_wdl_action", "creating ToolDiscovery")
    discovery = ToolDiscovery(verbose=_verbose)

    # Determine agent
    selected_agent: Optional[str] = None
    if not standalone:
        if agent_name:
            selected_agent = agent_name
        else:
            # Try to load workspace to determine agent
            success, workspace_data, msg = workspace_manager.load_workspace(workflow_id, None)
            if success:
                metadata = workspace_data.get("metadata", {})
                selected_agent = metadata.get("agent_name")
                if selected_agent:
                    print(f"   Found agent: {selected_agent}")
            else:
                # Try standalone
                standalone = True
                workspace_manager = WorkspaceManager(use_agents=False)

    mode = f"Agent: {selected_agent}" if selected_agent else "Standalone"
    print(f"   Mode: {mode}")

    # Load workspace
    _verbose_print("update_wdl_action", "loading workspace")
    success, workspace_data, msg = workspace_manager.load_workspace(workflow_id, selected_agent)
    if not success:
        print(f"❌ {msg}")
        print("\n💡 Tip: Use --create to create a new workflow")
        _verbose_print("update_wdl_action", "EXIT", "workspace not found")
        return 1

    workspace = workspace_data["workspace_path"]
    print(f"✅ Found workspace: {workspace}")

    # Check if we have anything to add
    if not use_api_ids and not use_tool_ids:
        print("\n⚠️  No APIs or tools specified to add")
        print("   Use --use-api <ID> and/or --use-tool <ID> to add resources")
        _verbose_print("update_wdl_action", "EXIT", "nothing to add")
        return 1

    # Update workspace
    _verbose_print("update_wdl_action", "updating workspace context")
    success, update_msg = update_workspace_context(
        workflow_id=workflow_id,
        workspace=workspace,
        discovery=discovery,
        workspace_manager=workspace_manager,
        use_api_ids=use_api_ids,
        use_tool_ids=use_tool_ids,
        agent_name=selected_agent,
    )

    if success:
        print(f"\n✅ {update_msg}")
        print("\n📌 NEXT STEPS:")
        print("   1. Review updated apis/ directory")
        if use_tool_ids:
            print("   2. Review updated tools/ directory")
            print("   3. Review updated tool_context.md")
        print("   4. Continue working on widdle.json")
        print("=" * 80)
        _verbose_print("update_wdl_action", "EXIT", "success")
        return 0
    else:
        print(f"\n❌ {update_msg}")
        _verbose_print("update_wdl_action", "EXIT", "update failed")
        return 1


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Manage WDL workflow actions - create, update, and manage workflows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # === SIMPLE TOOL (single API wrapper) ===
  python manage_wdl_action.py --create --template simple --use-api <api-id> -t "My Tool"
  python manage_wdl_action.py --create --template simple --use-api <api-id> --publish
  
  # === COMPLEX WORKFLOW (multi-step) ===
  python manage_wdl_action.py --create --template workflow -r requirements.md -t "My Workflow"
  python manage_wdl_action.py --create -r req.md -t "My Workflow" --use-api abc-123
  python manage_wdl_action.py --create -r req.md -t "My Workflow" --standalone
  
  # === DISCOVERY ===
  python manage_wdl_action.py --list-tools
  python manage_wdl_action.py --list-all-actions  # execution_type=DEFAULT
  python manage_wdl_action.py --list-workflows    # execution_type=WORKFLOW
  python manage_wdl_action.py --list-apis
  python manage_wdl_action.py --search "user profile"
  python manage_wdl_action.py --search-apis "authentication"
  python manage_wdl_action.py --auto-discover -r requirements.md

  # === UPDATE EXISTING ===
  python manage_wdl_action.py --update --workflow-id abc123 --use-api api-1
  python manage_wdl_action.py --workflow-id abc123 --use-tool tool-1
""",
    )

    # Action mode flags
    parser.add_argument(
        "--create", action="store_true",
        help="Create a new workflow workspace"
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Update an existing workflow workspace"
    )
    parser.add_argument(
        "--workflow-id", "-w",
        help="Workflow ID (required for --update or when using --use-api/--use-tool independently)"
    )

    # Discovery commands
    parser.add_argument(
        "--list-tools", action="store_true", help="List available tools from platform"
    )
    parser.add_argument(
        "--list-all-actions", action="store_true", 
        help="List all actions with execution_type=DEFAULT"
    )
    parser.add_argument(
        "--list-workflows", action="store_true", 
        help="List workflow actions with execution_type=WORKFLOW"
    )
    parser.add_argument(
        "--list-apis", action="store_true", help="List available APIs from platform"
    )
    parser.add_argument(
        "--search", metavar="QUERY",
        help="Semantic search for tools matching a query"
    )
    parser.add_argument(
        "--search-apis", metavar="QUERY",
        help="Semantic search for APIs matching a query"
    )
    parser.add_argument(
        "--auto-discover", action="store_true",
        help="Auto-discover relevant tools AND APIs based on requirements (for Cursor)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output in JSON format (for Cursor/machine parsing)"
    )
    parser.add_argument(
        "--top-k", type=int, default=5,
        help="Number of results for search/discovery (default: 5)"
    )

    # Template option
    parser.add_argument(
        "--template", choices=["simple", "workflow"], default="workflow",
        help="Template type: 'simple' for single-API tool, 'workflow' for complex multi-step"
    )
    
    # Creation options
    parser.add_argument(
        "--requirements", "-r", help="Path to requirements .md file (required for workflow template)"
    )
    parser.add_argument(
        "--title", "-t", default="New Tool", help="Tool/workflow title"
    )
    parser.add_argument(
        "--agent", "-a", help="Specific agent to use"
    )
    parser.add_argument(
        "--standalone", "-s", action="store_true",
        help="Standalone mode (no agent structure)"
    )
    parser.add_argument(
        "--create-remote", action="store_true",
        help="Create action on Adopt immediately (default: workspace only)"
    )
    parser.add_argument(
        "--save-draft", action="store_true",
        help="Save as draft after creating (requires --create-remote or existing action)"
    )

    # Context options (can be used independently)
    parser.add_argument(
        "--use-tool", action="append", dest="use_tools",
        help="Include existing tool WDL as context (can repeat). Can be used with --update or independently with --workflow-id"
    )
    parser.add_argument(
        "--use-api", action="append", dest="use_apis",
        help="Include API spec as context (can repeat). Can be used with --update or independently with --workflow-id"
    )

    # Output options
    parser.add_argument(
        "--generate-only", action="store_true", help="Only generate Cursor prompt"
    )
    
    # Debug options
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose mode with function entry/exit logging for debugging"
    )

    args = parser.parse_args()
    
    # Set global verbose flag
    global _verbose
    _verbose = args.verbose
    if _verbose:
        print("[VERBOSE] Verbose mode enabled", file=sys.stderr)

    # Handle discovery commands (work independently)
    if args.list_tools:
        sys.exit(list_tools_command(json_output=args.json, execution_type="TOOL"))

    if args.list_all_actions:
        sys.exit(list_tools_command(json_output=args.json, execution_type="ALL"))

    if args.list_workflows:
        sys.exit(list_tools_command(json_output=args.json, execution_type="WORKFLOW"))

    if args.list_apis:
        sys.exit(list_apis_command(json_output=args.json))

    if args.search:
        sys.exit(search_tools_command(args.search, args.top_k, json_output=args.json))

    if args.search_apis:
        sys.exit(search_apis_command(args.search_apis, args.top_k, json_output=args.json))

    if args.auto_discover:
        if not args.requirements:
            print("❌ --auto-discover requires --requirements", file=sys.stderr)
            sys.exit(1)
        sys.exit(auto_discover_command(args.requirements, args.top_k))

    # Handle update mode
    if args.update:
        if not args.workflow_id:
            print("❌ --update requires --workflow-id", file=sys.stderr)
            sys.exit(1)
        
        if not args.use_apis and not args.use_tools:
            print("❌ --update requires at least one --use-api or --use-tool", file=sys.stderr)
            sys.exit(1)
        
        sys.exit(update_wdl_action(
            workflow_id=args.workflow_id,
            standalone=args.standalone,
            agent_name=args.agent,
            use_tool_ids=args.use_tools,
            use_api_ids=args.use_apis,
        ))

    # Handle save-draft for existing workflow
    if args.save_draft and args.workflow_id and not args.create:
        # Import here to avoid circular dependency
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from cli.save_wdl_draft import save_wdl_draft
        success, version = save_wdl_draft(
            workflow_id=args.workflow_id,
            standalone=args.standalone,
            agent_name=args.agent,
        )
        sys.exit(0 if success else 1)

    # Handle independent --use-api/--use-tool (without --create or --update)
    if (args.use_apis or args.use_tools) and not args.create:
        if not args.workflow_id:
            print("❌ --use-api/--use-tool requires --workflow-id when not using --create", file=sys.stderr)
            print("   Use: python manage_wdl_action.py --workflow-id <id> --use-api <api-id>", file=sys.stderr)
            sys.exit(1)
        
        # Treat as update
        sys.exit(update_wdl_action(
            workflow_id=args.workflow_id,
            standalone=args.standalone,
            agent_name=args.agent,
            use_tool_ids=args.use_tools,
            use_api_ids=args.use_apis,
        ))

    # Handle creation mode
    if args.create:
        # Simple template: requires --use-api
        if args.template == "simple":
            if not args.use_apis:
                print("❌ Error: --template simple requires --use-api <api-id>", file=sys.stderr)
                print("   Example: python manage_wdl_action.py --create --template simple --use-api abc-123", file=sys.stderr)
                sys.exit(1)
            
            exit_code = create_simple_tool(
                api_id=args.use_apis[0],  # Use first API for simple tool
                title=args.title,
                standalone=args.standalone,
                agent_name=args.agent,
                create_remote=args.create_remote,
            )
            sys.exit(exit_code)
        
        # Workflow template: requires --requirements
        if not args.requirements and not args.generate_only:
            parser.print_help()
            print("\n❌ Error: --create with --template workflow requires --requirements", file=sys.stderr)
            print("   Or use --template simple --use-api <id> for single-API tools", file=sys.stderr)
            sys.exit(1)

        exit_code = create_wdl_action(
            requirements_path=args.requirements,
            title=args.title,
            standalone=args.standalone,
            agent_name=args.agent,
            create_remote=args.create_remote,
            save_draft=args.save_draft,
            use_tool_ids=args.use_tools,
            use_api_ids=args.use_apis,
            generate_only=args.generate_only,
            template=args.template,
        )
        sys.exit(exit_code)

    # Default: show help if no action specified
    if not args.workflow_id:
        parser.print_help()
        print("\n💡 Use --create to create a new workflow, --update to update existing, or discovery commands")
        sys.exit(0)


if __name__ == "__main__":
    main()

