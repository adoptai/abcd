#!/usr/bin/env python3
"""
Roaming Instructions Builder for Cursor.

Creates instructions that tell Cursor HOW to roam the documentation,
rather than preloading specific docs. Cursor navigates autonomously.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .trace_analyzer import TraceAnalyzer
from .wdl_documentation import WDLDocumentationProvider


class RoamingInstructionsBuilder:
    """Builds roaming instructions for Cursor to navigate WDL docs."""

    def __init__(
        self, docs_provider: Optional[WDLDocumentationProvider] = None
    ) -> None:
        """
        Initialize builder.

        Args:
            docs_provider: WDL documentation provider. Creates one if not provided.
        """
        self.docs_provider = docs_provider or WDLDocumentationProvider()
        self.trace_analyzer = TraceAnalyzer()

    def build_generation_instructions(
        self,
        workspace: Path,
        title: str,
        api_details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Build roaming instructions for WDL generation.

        Tells Cursor where docs are and how to use them - does NOT
        preload any operation docs. Cursor decides what to load.

        Args:
            workspace: Path to action workspace
            title: Action title

        Returns:
            Roaming instructions for Cursor
        """
        docs_path = self.docs_provider.docs_path
        system_index_path = self.docs_provider.system_index_path
        widdle_json_path = workspace / "widdle.json"
        requirements_path = workspace / "requirements.md"

        lines: list[str] = []
        lines.append("# WDL Generation Task - Roaming RAG Instructions\n")
        lines.append(f"**Action**: {title}")
        lines.append(f"**Workspace**: `{workspace}`")
        lines.append(f"**Generated**: {datetime.now().isoformat()}\n")

        lines.append("---\n")
        lines.append("## 📚 Documentation Navigation\n")
        lines.append("WDL operation documentation is available for you to roam:\n")
        lines.append(f"**Documentation Root**: `{docs_path}`\n")

        lines.append("### Step 1: Read the Index")
        lines.append(
            f"Start by reading `{system_index_path}` to see all available operations."
        )
        lines.append("This index contains brief descriptions of each operation.\n")

        lines.append("### Step 2: Load What You Need")
        lines.append(
            "Based on the requirements, open and read the specific operation docs:"
        )
        lines.append("- Each operation has a `*_OPERATION_DESCRIPTION.md` file")
        lines.append("- Files contain: syntax, parameters, examples")
        lines.append("- **Only load what's relevant** - don't read everything\n")

        lines.append("### Step 3: Generate WDL")
        lines.append(f"Save your generated WDL to: `{widdle_json_path}`\n")

        lines.append("---\n")
        lines.append("## 📋 Requirements\n")
        lines.append(f"Read requirements from: `{requirements_path}`\n")

        lines.append("---\n")
        lines.append("## 🔍 Discovery & Context Building\n")
        lines.append("**IMPORTANT**: Before generating WDL, search for relevant APIs and tools!\n")
        lines.append("\n### Step 1: Search for Building Blocks\n")
        lines.append("Use semantic search to find relevant APIs and tools:\n")
        lines.append("```bash")
        lines.append("# Auto-discover based on requirements")
        lines.append(f"python cli/discover.py --requirements {requirements_path} --top 5 --json")
        lines.append("```\n")
        lines.append("This will return JSON with both `actions` and `apis` arrays.\n")
        lines.append("\n### Step 2: Add Context to Workspace\n")
        lines.append("If you find relevant APIs or tools, add them to the workspace using:\n")
        lines.append("```bash")
        lines.append("# For new workflows:")
        lines.append(f"python cli/manage_wdl_action.py --create -r {requirements_path} -t \"{title}\" \\")
        lines.append("  --use-api api-id-1 --use-api api-id-2 \\")
        lines.append("  --use-tool tool-id-1 --use-tool tool-id-2")
        lines.append("```\n")
        lines.append("Or for existing workflows:\n")
        lines.append("```bash")
        lines.append("python cli/manage_wdl_action.py --update --workflow-id <workflow-id> \\")
        lines.append("  --use-api api-id-1 --use-api api-id-2 \\")
        lines.append("  --use-tool tool-id-1 --use-tool tool-id-2")
        lines.append("```\n")
        lines.append("This will:")
        lines.append("- Save full API specs to `apis/` directory as JSON files")
        lines.append("- Create `apis/manifest.json` listing all API IDs")
        lines.append("- Save full tool specs to `tools/` directory as JSON files")
        lines.append("- Create `tools/manifest.json` listing all tool IDs")
        lines.append("- Create `tool_context.md` with existing tool WDLs (for LLM reference)\n")
        lines.append("\n### Step 3: Use Context Files\n")
        lines.append("**CRITICAL**: When generating WDL, you MUST use ONLY the API folder for API schema information!\n")
        lines.append("When generating WDL:")
        lines.append("- **APIs**: **ONLY** read JSON files from `apis/` directory:")
        lines.append("  - **DO NOT** fetch API details from any other source")
        lines.append("  - **DO NOT** use cached API information")
        lines.append("  - Check `apis/manifest.json` for list of API IDs")
        lines.append("  - Read `apis/{api_id}.json` for full API specifications")
        lines.append("  - Each JSON contains: endpoints, parameters, schemas, base_url, etc.")
        lines.append("  - **These files are the single source of truth for API schema**")
        lines.append("- **Tools**: Read JSON files from `tools/` directory for full tool definitions:")
        lines.append("  - Check `tools/manifest.json` for list of tool IDs")
        lines.append("  - Read `tools/{tool_id}.json` for full tool specifications (WDL, parameters, etc.)")
        lines.append("  - Also reference `tool_context.md` for markdown-formatted tool WDLs")
        lines.append("- Use these as building blocks in your workflow\n")

        # Check for API specs in apis/ directory
        apis_dir = workspace / "apis"
        if apis_dir.exists():
            manifest_file = apis_dir / "manifest.json"
            if manifest_file.exists():
                try:
                    manifest = json.loads(manifest_file.read_text())
                    api_ids = manifest.get("api_ids", [])
                    if api_ids:
                        lines.append("---\n")
                        lines.append("## 🔗 API Specifications\n")
                        lines.append(f"Full API specifications are stored in `{apis_dir}/`:\n")
                        lines.append(f"- **Manifest**: `{apis_dir}/manifest.json` - Lists all API IDs\n")
                        for api_id in api_ids:
                            api_file = apis_dir / f"{api_id}.json"
                            if api_file.exists():
                                lines.append(f"- `{api_file.name}` - Full API spec for `{api_id}`")
                        lines.append("\n**CRITICAL: To use these APIs** - You MUST use ONLY the API folder:")
                        lines.append("1. **ONLY** read `apis/manifest.json` to see all available API IDs")
                        lines.append("2. **ONLY** read the relevant API spec JSON files: `apis/{api_id}.json`")
                        lines.append("3. **DO NOT** fetch API details from any other endpoint or source")
                        lines.append("4. **DO NOT** use cached or previously fetched API information")
                        lines.append("5. Each JSON contains: title, description, base_url, endpoints, parameters, schemas")
                        lines.append("6. Use endpoint definitions and parameters from these JSON files when creating REST operations")
                        lines.append("7. Reference the API ID when needed")
                        lines.append("8. **The `apis/` directory is the single source of truth for all API schema information**\n")
                except Exception:
                    pass
        
        # Check for tool specs in tools/ directory
        tools_dir = workspace / "tools"
        if tools_dir.exists():
            manifest_file = tools_dir / "manifest.json"
            if manifest_file.exists():
                try:
                    manifest = json.loads(manifest_file.read_text())
                    tool_ids = manifest.get("tool_ids", [])
                    if tool_ids:
                        lines.append("---\n")
                        lines.append("## 🔧 Tool Specifications\n")
                        lines.append(f"Full tool specifications are stored in `{tools_dir}/`:\n")
                        lines.append(f"- **Manifest**: `{tools_dir}/manifest.json` - Lists all tool IDs\n")
                        for tool_id in tool_ids:
                            tool_file = tools_dir / f"{tool_id}.json"
                            if tool_file.exists():
                                lines.append(f"- `{tool_file.name}` - Full tool spec for `{tool_id}`")
                        lines.append("\n**To use these tools** - Reference the tool definitions:")
                        lines.append("1. Read `tools/manifest.json` to see all available tool IDs")
                        lines.append("2. Read the relevant tool spec JSON files: `tools/{tool_id}.json`")
                        lines.append("3. Each JSON contains: title, description, WDL (widdle), parameters, etc.")
                        lines.append("4. Use tool WDLs as building blocks when creating composite workflows")
                        lines.append("5. Reference `tool_context.md` for markdown-formatted tool WDLs")
                        lines.append("\n")
                except Exception:
                    pass

        lines.append("---\n")
        lines.append("## ✅ Output Checklist\n")
        lines.append("Your generated WDL must:")
        lines.append("- [ ] Be valid JSON array of operations")
        lines.append("- [ ] Have unique `id` for each operation")
        lines.append("- [ ] Use `{workflow_arguments.param}` for dynamic values")
        lines.append("- [ ] Include `required_inputs` block")
        lines.append("- [ ] End with an OUTPUT operation")
        lines.append(f"\nSave to: `{widdle_json_path}`")

        return "\n".join(lines)

    def build_fix_instructions(
        self,
        workspace: Path,
        trace_path: Path,
    ) -> str:
        """
        Build roaming instructions for fixing failed WDL.

        Points Cursor to trace file and docs - Cursor investigates.

        Args:
            workspace: Path to action workspace
            trace_path: Path to the execution trace file

        Returns:
            Roaming instructions for Cursor
        """
        docs_path = self.docs_provider.docs_path
        system_index_path = self.docs_provider.system_index_path
        widdle_json_path = workspace / "widdle.json"
        requirements_path = workspace / "requirements.md"
        test_cases_path = workspace / "test_cases"

        lines: list[str] = []
        lines.append("# WDL Fix Task - Roaming RAG Instructions\n")
        lines.append(f"**Workspace**: `{workspace}`")
        lines.append(f"**Generated**: {datetime.now().isoformat()}\n")

        lines.append("---\n")
        lines.append("## ❌ Test Failed\n")
        lines.append("The WDL failed during testing. Your task: diagnose and fix.\n")

        lines.append("### Files to Examine\n")
        lines.append(f"1. **Execution Trace**: `{trace_path}`")
        lines.append(f"2. **Current WDL**: `{widdle_json_path}`")
        lines.append(f"3. **Requirements**: `{requirements_path}`")
        lines.append(f"4. **Test Case**: `{test_cases_path}/`\n")

        lines.append("### Diagnosis Steps\n")
        lines.append("1. Read the trace file to understand what failed")
        lines.append("2. Identify which operation(s) caused the issue")
        lines.append(f"3. Read relevant docs from `{docs_path}`")
        lines.append("4. Fix the WDL accordingly\n")

        lines.append("---\n")
        lines.append("## 📚 Documentation Navigation\n")
        lines.append(f"**Index**: `{system_index_path}`")
        lines.append(f"**Operations**: `{docs_path}/*_DESCRIPTION.md`\n")

        lines.append("### Common Issues\n")
        lines.append("- **HTTP 401/403**: Check security_params in adopt_profile.json")
        lines.append("- **HTTP 404**: Verify URL path in REST operation")
        lines.append("- **Missing input**: Add to required_inputs")
        lines.append("- **JQ error**: Fix filter syntax\n")

        lines.append("---\n")
        lines.append("## ✅ After Fixing\n")
        lines.append(f"1. Save updated WDL to: `{widdle_json_path}`")
        lines.append(
            "2. Run test again: `python cli/test_wdl_action.py {action_id}`"
        )

        return "\n".join(lines)

    def save_instructions(self, instructions: str, output_path: Path) -> None:
        """Save roaming instructions to file for Cursor."""
        output_path.write_text(instructions)
