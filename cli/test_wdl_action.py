#!/usr/bin/env python3
"""
Test a WDL workflow action and generate Cursor fix suggestions.

Integrates with the Tool Builder infrastructure:
- Uses tool_builder_agents/ for workspace discovery
- Captures execution traces for debugging
- Generates roaming RAG instructions for Cursor to fix issues
- AUTO-DETECTS version and draft status from metadata (no flags needed)
- RECOVERS action_id automatically if lost

Usage:
    # Test workflow (auto-detects everything)
    python test_wdl_action.py workflow-id

    # Just validate WDL structure (no remote execution)
    python test_wdl_action.py workflow-id --local-only

    # Run all test cases
    python test_wdl_action.py workflow-id --all

    # Auto-fix validation issues
    python test_wdl_action.py workflow-id --auto-fix

TRANSPARENT BEHAVIOR (default):
- Version: Auto-detected from checked-out/working version
- Draft flag: Auto-determined from version status
- Agent/Standalone: Auto-detected from workspace location
- Action ID: Auto-recovered by title search if missing

USE FLAGS ONLY when automatic behavior doesn't work.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import AdoptAPIClient
from cli.wdl_common.cursor_prompt_builder import RoamingInstructionsBuilder
from cli.wdl_common.workspace_manager import WorkspaceManager
from cli.wdl_common.metadata_manager import MetadataManager
from cli.wdl_common.validator import validate_wdl_file, WDLValidator
from cli.wdl_common.error_patterns import enhance_error_message
from cli.wdl_common.version_tracker import (
    get_checked_out_version_info,
    get_current_version_info,
    read_metadata,
)


def save_trace(workspace: Path, trace: Dict[str, Any], test_name: str) -> Path:
    """Save execution trace to file."""
    traces_dir = workspace / "traces"
    traces_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    trace_path = traces_dir / f"trace_{test_name}_{timestamp}.json"

    trace_path.write_text(json.dumps(trace, indent=2))
    return trace_path


def validate_output(actual_output: Any, expected_output: Dict[str, Any]) -> tuple[bool, str]:
    """
    Validate actual output against expected output.
    
    Args:
        actual_output: The actual output from the workflow
        expected_output: Expected output specification with validation rules
        
    Returns:
        Tuple of (is_valid, message)
    """
    if not expected_output:
        return True, "No expected output specified"
    
    validation_type = expected_output.get("validation", "similarity")
    key_fields = expected_output.get("key_fields", [])
    sample_output = expected_output.get("sample_output", "")
    
    # Convert actual output to string for comparison if needed
    actual_str = json.dumps(actual_output, indent=2) if isinstance(actual_output, (dict, list)) else str(actual_output)
    
    if validation_type == "exact":
        expected_str = json.dumps(sample_output, indent=2) if isinstance(sample_output, (dict, list)) else str(sample_output)
        if actual_str.strip() == expected_str.strip():
            return True, "Output matches exactly"
        else:
            return False, f"Output does not match exactly. Expected: {expected_str[:200]}..."
    
    elif validation_type == "contains":
        # Check if key fields are present
        if key_fields:
            missing_fields = []
            if isinstance(actual_output, dict):
                for field in key_fields:
                    if field not in actual_output:
                        missing_fields.append(field)
            else:
                # For non-dict outputs, check if field names appear in string representation
                for field in key_fields:
                    if field not in actual_str:
                        missing_fields.append(field)
            
            if missing_fields:
                return False, f"Missing expected fields: {', '.join(missing_fields)}"
            return True, f"All expected fields present: {', '.join(key_fields)}"
    
    elif validation_type == "similarity":
        # For similarity validation, LLM (agent) will judge - just provide context
        # Return a message indicating LLM should review
        return None, "LLM_REVIEW_REQUIRED"
    
    return True, "Validation passed"


def run_test(
    workflow_id: str,
    agent_name: Optional[str] = None,
    test_file: Optional[str] = None,
    local_only: bool = False,
    no_auto_save: bool = False,
    run_all: bool = False,
) -> Dict[str, Any]:
    """
    Run test for a WDL workflow action.

    Args:
        workflow_id: Workflow ID / workspace name
        agent_name: Agent name (None for standalone/auto-detect)
        test_file: Specific test file to run (default: test_1.json, or all if run_all=True)
        local_only: Skip remote execution, just validate WDL
        no_auto_save: Don't auto-save draft on success
        run_all: Run all test cases in test_cases/ directory

    Returns:
        Test results dictionary
    """
    print("\n" + "=" * 80)
    print("🧪 TEST WDL WORKFLOW ACTION")
    print("=" * 80)
    print(f"Workflow ID: {workflow_id}")

    # Initialize workspace manager
    workspace_manager = WorkspaceManager(use_agents=True)

    # Try to load workspace (auto-detect agent vs standalone)
    success, data, msg = workspace_manager.load_workspace(workflow_id, agent_name)

    if not success:
        # Try standalone mode
        workspace_manager = WorkspaceManager(use_agents=False)
        success, data, msg = workspace_manager.load_workspace(workflow_id)

        if not success:
            # Check if workspace path exists but loading failed (likely JSON error)
            workspace_path = data.get("workspace_path")
            if workspace_path and workspace_path.exists():
                # Workspace exists but failed to load - likely JSON error
                wdl_path = workspace_path / "widdle.json"
                if wdl_path.exists():
                    # Try to parse JSON to get better error message
                    try:
                        with open(wdl_path, "r") as f:
                            json.load(f)
                    except json.JSONDecodeError as json_err:
                        print("❌ Invalid JSON in widdle.json")
                        print(
                            f"   Error: {json_err.msg} at line {json_err.lineno}, column {json_err.colno}"
                        )
                        print(f"   File: {wdl_path}")
                        print("\n💡 Fix JSON syntax errors before proceeding")
                        print("=" * 80)
                        return {
                            "success": False,
                            "error": f"JSON syntax error: {json_err.msg} at line {json_err.lineno}, column {json_err.colno}",
                        }
                    except Exception as e:
                        # Some other error reading the file
                        print(f"❌ Error reading widdle.json: {str(e)}")
                        print(f"   File: {wdl_path}")
                        print("=" * 80)
                        return {
                            "success": False,
                            "error": f"Error reading widdle.json: {str(e)}",
                        }

            # Workspace doesn't exist or other error
            print(f"❌ Workspace not found: {workflow_id}")
            print("   Try specifying --agent if the workflow is in an agent.")
            if "JSON" in msg or "json" in msg.lower():
                print(f"\n⚠️  Note: {msg}")
            return {"success": False, "error": msg}

    workspace: Path = data.get("workspace_path", Path())
    wdl = data.get("wdl", [])
    profile = data.get("profile", {})
    metadata = data.get("metadata", {})

    agent_from_meta = metadata.get("agent_name")
    mode = f"Agent: {agent_from_meta}" if agent_from_meta else "Standalone"

    print(f"📁 Workspace: {workspace}")
    print(f"📁 Mode: {mode}")
    print(f"📝 WDL operations: {len(wdl)}")

    # Determine which test cases to run
    test_cases_dir = workspace / "test_cases"
    if not test_cases_dir.exists():
        print(f"❌ Test cases directory not found: {test_cases_dir}")
        return {"success": False, "error": f"Test cases directory not found: {test_cases_dir}"}
    
    if run_all:
        # Find all JSON test files
        test_files = sorted([f.name for f in test_cases_dir.glob("test_*.json")])
        if not test_files:
            print(f"❌ No test files found in {test_cases_dir}")
            return {"success": False, "error": f"No test files found in {test_cases_dir}"}
        print(f"\n📋 Running all test cases: {len(test_files)} test(s)")
    else:
        test_file = test_file or "test_1.json"
        test_files = [test_file]
    
    # Run tests
    all_results = []
    overall_success = True
    
    for test_file in test_files:
        test_path = test_cases_dir / test_file
        
        if not test_path.exists():
            print(f"❌ Test file not found: {test_path}")
            all_results.append({
                "test_file": test_file,
                "success": False,
                "error": f"Test file not found: {test_path}"
            })
            overall_success = False
            continue
        
        test_case = json.loads(test_path.read_text())
        prompt = test_case.get("prompt", "")
        workflow_params = test_case.get("workflow_params", {})
        expected_output = test_case.get("expected_output", {})
        
        print(f"\n{'='*80}")
        print(f"📋 Test: {test_file}")
        print(f"   Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        if expected_output:
            print(f"   Expected: {expected_output.get('description', 'Output validation specified')}")

        test_results: Dict[str, Any] = {
            "success": False,
            "workflow_id": workflow_id,
            "test_file": test_file,
            "prompt": prompt,
            "expected_output": expected_output,
            "wdl": wdl,
        }

        if local_only:
            # Step 1: Validate JSON structure first
            print("\n📋 Step 1: Validating JSON syntax...")
        json_errors = []

        # Check if widdle.json file exists and is valid JSON
        wdl_path = workspace / "widdle.json"
        if not wdl_path.exists():
            json_errors.append(f"widdle.json file not found at {wdl_path}")
        else:
            try:
                # Try to parse the JSON file directly
                with open(wdl_path, "r") as f:
                    json.load(f)
                print("✅ JSON syntax is valid")
            except json.JSONDecodeError as e:
                json_errors.append(
                    f"Invalid JSON syntax in widdle.json: {e.msg} at line {e.lineno}, column {e.colno}"
                )
            except Exception as e:
                json_errors.append(f"Error reading widdle.json: {str(e)}")

        if json_errors:
            test_results["json_validation_errors"] = json_errors
            print("❌ JSON validation errors:")
            for err in json_errors:
                print(f"   - {err}")
            print(
                "\n💡 Fix JSON syntax errors before proceeding with structure validation"
            )
            print("=" * 80)
            all_results.append(test_results)
            overall_success = False
            continue

        # Step 2: Validate WDL structure locally
        print("\n🔍 Step 2: Validating WDL structure...")

        errors = []
        warnings = []
        seen_ids = set()

        # Check if WDL is a list
        if not isinstance(wdl, list):
            errors.append("WDL must be a JSON array/list")
            test_results["validation_errors"] = errors
            print("❌ Validation errors:")
            for err in errors:
                print(f"   - {err}")
            print("=" * 80)
            all_results.append(test_results)
            overall_success = False
            continue

        # Validate each operation
        for i, op in enumerate(wdl):
            if not isinstance(op, dict):
                errors.append(f"Operation {i} must be a JSON object")
                continue

            # Check for required fields based on operation type
            op_id = op.get("id")
            operation = op.get("operation")

            # Skip metadata blocks (they don't need id/operation)
            if (
                "metadata" in op
                or "required_inputs" in op
                or "suggestions" in op
                or "statement" in op
            ):
                continue

            # Check for id field
            if not op_id:
                errors.append(f"Operation {i} missing required 'id' field")
            elif not isinstance(op_id, str):
                errors.append(f"Operation {i} has invalid 'id' field (must be string)")
            elif op_id in seen_ids:
                errors.append(f"Operation {i} has duplicate 'id': '{op_id}'")
            elif op_id:
                seen_ids.add(op_id)

            # Track output_key for reference validation
            output_key = op.get("output_key")
            if output_key and isinstance(output_key, str):
                seen_ids.add(output_key)

            # Check for operation field
            if not operation:
                errors.append(f"Operation {i} missing required 'operation' field")
            elif not isinstance(operation, str):
                errors.append(
                    f"Operation {i} has invalid 'operation' field (must be string)"
                )

            # Check for common operation-specific requirements
            if operation == "REST":
                if "url" not in op:
                    errors.append(f"Operation {i} (REST) missing required 'url' field")
                if "method" not in op:
                    errors.append(
                        f"Operation {i} (REST) missing required 'method' field"
                    )
            elif operation == "JQ_FILTER":
                if "input" not in op and "inputs" not in op:
                    errors.append(
                        f"Operation {i} (JQ_FILTER) missing required 'input' or 'inputs' field"
                    )
                if "filter" not in op:
                    errors.append(
                        f"Operation {i} (JQ_FILTER) missing required 'filter' field"
                    )
            elif operation == "EXTRACT":
                if "input" not in op:
                    errors.append(
                        f"Operation {i} (EXTRACT) missing required 'input' field"
                    )
            elif operation == "PROJECT":
                if "input" not in op:
                    errors.append(
                        f"Operation {i} (PROJECT) missing required 'input' field"
                    )
                if "fields" not in op:
                    errors.append(
                        f"Operation {i} (PROJECT) missing required 'fields' field"
                    )
            elif operation == "OUTPUT_TEXT":
                # OUTPUT_TEXT can use either 'input' or 'format_string' + 'values'
                if "input" not in op and "format_string" not in op:
                    warnings.append(
                        f"Operation {i} (OUTPUT_TEXT) needs 'input' or 'format_string' field"
                    )

            # Check input references (basic check - just that they reference something)
            if "input" in op and isinstance(op["input"], str):
                input_ref = op["input"].strip()
                # Check if it references a valid id or workflow_arguments
                if input_ref.startswith("{") and input_ref.endswith("}"):
                    ref_content = input_ref.strip("{}")
                    parts = ref_content.split(".")
                    if len(parts) > 0:
                        first_part = parts[0]
                        if first_part not in seen_ids and first_part not in {
                            "workflow_arguments",
                            "security_params",
                            "status_codes",
                        }:
                            warnings.append(
                                f"Operation {i} references '{first_part}' which may not be defined yet"
                            )

        # Report results
        if errors:
            print("❌ Validation errors:")
            for err in errors:
                print(f"   - {err}")
            test_results["validation_errors"] = errors
            test_results["success"] = False
        else:
            print("✅ WDL structure is valid")
            test_results["success"] = True

        if warnings:
            print("\n⚠️  Validation warnings:")
            for warn in warnings:
                print(f"   - {warn}")
            test_results["validation_warnings"] = warnings

            print("=" * 80)
            all_results.append(test_results)
            
            # If running all tests in local-only mode, continue to next test
            if run_all:
                continue
            else:
                return test_results

        else:
            # Execute test remotely
            print("\n▶️  Running test...")

        # Check if this is a remote action by looking at metadata
        # Use MetadataManager for enhanced action_id handling with recovery
        meta_manager = MetadataManager(workspace)
        action_id = meta_manager.get_action_id()

        if not action_id:
            # Try to recover by searching for action by title
            meta_data = meta_manager.load()
            title = meta_data.title or metadata.get("title")
            if title:
                print(f"🔍 No action_id found. Searching for action by title: {title}")
                client = AdoptAPIClient()
                success_list, tools, msg_list = client.list_tools()
                if success_list and tools:
                    for tool in tools:
                        if tool.get("title") == title:
                            action_id = tool.get("action_id") or tool.get("id")
                            print(f"✅ Found and recovered action_id: {action_id}")
                            meta_manager.set_action_id(action_id)
                            break

        if not action_id:
            print("⚠️  No action_id found in metadata and could not recover.")
            print("   To create remote action and save draft:")
            print(f"   python cli/save_wdl_draft.py --workflow-id {workflow_id}")
            print("   Or use --local-only to validate structure only")
            test_results["success"] = False
            test_results["error"] = "Cannot test local workflow remotely - no action_id found"
            all_results.append(test_results)
            overall_success = False
            continue

        print(f"🔑 Action ID: {action_id}")
        client = AdoptAPIClient()

        # Determine which version to test using MetadataManager (TRANSPARENT)
        # This auto-detects the correct version and draft status
        test_target = meta_manager.determine_test_version()
        version_to_test = test_target.version
        allow_draft_flag = test_target.allow_draft
        print(f"🧪 {test_target.reason}")

        try:
            success, response, msg = client.run_action(
                action_id=action_id,
                user_input=prompt,
                profile=profile,
                workflow_params=workflow_params,
                version_number=version_to_test,
                allow_draft=allow_draft_flag,
            )

            test_results["success"] = success
            test_results["message"] = msg

            # Extract execution trace from response (works for both success and failure)
            # Check for both 'debug_tracing' (new name) and 'execution_trace' (legacy name)
            execution_trace = None
            response_clean = None

            if response:
                # Make a copy to avoid modifying the original
                response_clean = (
                    json.loads(json.dumps(response))
                    if isinstance(response, dict)
                    else response
                )

                if isinstance(response_clean, dict):
                    # Check if debug_tracing or execution_trace is in data (success case)
                    data = response_clean.get("data", {})
                    if isinstance(data, dict):
                        # Try debug_tracing first (new name)
                        execution_trace = data.get("debug_tracing")
                        if execution_trace is not None:
                            data.pop("debug_tracing", None)
                        # Fallback to execution_trace (legacy name)
                        if execution_trace is None:
                            execution_trace = data.get("execution_trace")
                            if execution_trace is not None:
                                data.pop("execution_trace", None)

                    # Also check direct debug_tracing/execution_trace in response
                    if execution_trace is None:
                        execution_trace = response_clean.get("debug_tracing")
                        if execution_trace is not None:
                            response_clean.pop("debug_tracing", None)
                    if execution_trace is None:
                        execution_trace = response_clean.get("execution_trace")
                        if execution_trace is not None:
                            response_clean.pop("execution_trace", None)

            # If not found in response, try to extract from error message (error case where error_message contains JSON string)
            if execution_trace is None and not success and msg:
                try:
                    # Check for both debug_tracing and execution_trace in error message
                    if isinstance(msg, str) and ("debug_tracing" in msg or "execution_trace" in msg):
                        # Try to extract JSON from error message (format: "Failed: XXX - {json}")
                        json_part = None
                        if " - {" in msg:
                            json_part = msg.split(" - ", 1)[1]
                        if json_part:
                            try:
                                error_json = json.loads(json_part)
                                # Try debug_tracing first (new name)
                                execution_trace = error_json.get("debug_tracing")
                                if execution_trace is None:
                                    execution_trace = error_json.get("data", {}).get("debug_tracing")
                                # Fallback to execution_trace (legacy name)
                                if execution_trace is None:
                                    execution_trace = error_json.get("execution_trace")
                                if execution_trace is None:
                                    execution_trace = error_json.get("data", {}).get(
                                        "execution_trace"
                                    )
                            except json.JSONDecodeError:
                                # If parsing fails, try to find debug_tracing or execution_trace using regex (handle nested JSON)
                                import re

                                # Find the debug_tracing value by matching braces (try new name first)
                                trace_pattern = r'"debug_tracing"\s*:\s*(\{(?:[^{}]|(?:\{[^{}]*\}))*\})'
                                trace_match = re.search(trace_pattern, msg)
                                if trace_match:
                                    try:
                                        execution_trace = json.loads(trace_match.group(1))
                                    except json.JSONDecodeError:
                                        pass
                                
                                # Fallback to execution_trace pattern (legacy name)
                                if execution_trace is None:
                                    trace_pattern = r'"execution_trace"\s*:\s*(\{(?:[^{}]|(?:\{[^{}]*\}))*\})'
                                    trace_match = re.search(trace_pattern, msg)
                                    if trace_match:
                                        try:
                                            execution_trace = json.loads(trace_match.group(1))
                                        except json.JSONDecodeError:
                                            pass
                            
                            # Last resort: try to find and parse the full outer JSON object
                            if execution_trace is None:
                                json_start = msg.find("{")
                                if json_start != -1:
                                    json_str = msg[json_start:]
                                    # Try to find matching closing brace
                                    brace_count = 0
                                    end_pos = -1
                                    for i, char in enumerate(json_str):
                                        if char == "{":
                                            brace_count += 1
                                        elif char == "}":
                                            brace_count -= 1
                                            if brace_count == 0:
                                                end_pos = i + 1
                                                break
                                    if end_pos > 0:
                                        try:
                                            error_json = json.loads(
                                                json_str[:end_pos]
                                            )
                                            # Try debug_tracing first (new name)
                                            execution_trace = error_json.get("debug_tracing")
                                            if execution_trace is None:
                                                execution_trace = error_json.get("data", {}).get("debug_tracing")
                                            # Fallback to execution_trace (legacy name)
                                            if execution_trace is None:
                                                execution_trace = error_json.get("execution_trace")
                                            if execution_trace is None:
                                                execution_trace = error_json.get("data", {}).get("execution_trace")
                                        except json.JSONDecodeError:
                                                pass
                except (json.JSONDecodeError, AttributeError, ValueError, IndexError):
                    pass

            # Only include response if success, otherwise set to null
            test_results["response"] = response_clean if success else None
            
            # Validate output against expected output if specified
            output_validation = None
            if success and expected_output:
                actual_output = response_clean
                if isinstance(response_clean, dict):
                    actual_output = response_clean.get("data") or response_clean.get("result") or response_clean.get("output") or response_clean
                
                validation_type = expected_output.get("validation", "similarity")
                is_valid, validation_msg = validate_output(actual_output, expected_output)
                
                if validation_type == "similarity":
                    # LLM (agent) will judge similarity - provide context for review
                    output_validation = {
                        "validation_type": "similarity",
                        "requires_llm_review": True,
                        "actual_output": actual_output,
                        "expected_output": expected_output,
                        "message": "Agent (LLM) should review output similarity"
                    }
                    print("\n" + "=" * 80)
                    print("🔍 OUTPUT VALIDATION - LLM REVIEW REQUIRED")
                    print("=" * 80)
                    print(f"Validation Type: similarity")
                    print(f"\nExpected Output:")
                    print(f"  Description: {expected_output.get('description', 'N/A')}")
                    if expected_output.get('key_fields'):
                        print(f"  Key Fields: {', '.join(expected_output.get('key_fields', []))}")
                    if expected_output.get('sample_output'):
                        print(f"  Sample Output: {json.dumps(expected_output.get('sample_output'), indent=2)[:500]}...")
                    print(f"\nActual Output:")
                    print(json.dumps(actual_output, indent=2)[:1000] + ("..." if len(json.dumps(actual_output, indent=2)) > 1000 else ""))
                    print("\n" + "=" * 80)
                    print("💡 AGENT INSTRUCTIONS:")
                    print("   Review the actual output above and compare it to the expected output.")
                    print("   Consider:")
                    print("   - Is the output structurally similar to what was expected?")
                    print("   - Does it contain the expected key fields and information?")
                    print("   - Is the data valid and non-hallucinated?")
                    print("   - Does it match the expected format/type?")
                    print("   If similar and valid, mark test as passed. If not, note issues.")
                    print("=" * 80)
                else:
                    output_validation = {
                        "valid": is_valid,
                        "message": validation_msg
                    }
                    test_results["output_validation"] = output_validation
                    
                    if is_valid:
                        print(f"\n✅ Output validation passed: {validation_msg}")
                    else:
                        print(f"\n⚠️  Output validation warning: {validation_msg}")
                        # Don't fail the test, but note the validation issue
                        if expected_output.get("validation") == "exact":
                            test_results["success"] = False
                            overall_success = False
                
                test_results["output_validation"] = output_validation

            # Log full result
            print("\n📊 FULL API RESPONSE:")
            print("=" * 80)
            print(
                json.dumps(response_clean if success else None, indent=2)
                if response_clean
                else "None"
            )
            print("=" * 80)
            print(f"\nStatus: {'✅ SUCCESS' if success else '❌ FAILED'}")
            print(f"Message: {msg}")

            # Save trace with execution_trace at top level, response only if success
            trace = {
                "timestamp": datetime.now().isoformat(),
                "workflow_id": workflow_id,
                "test_file": test_file,
                "prompt": prompt,
                "success": success,
                "response": response_clean if success else None,
                "error_message": msg if not success else None,
                "execution_trace": execution_trace,
                "expected_output": expected_output,
                "output_validation": output_validation,
            }

            trace_path = save_trace(workspace, trace, test_file.replace(".json", ""))
            test_results["trace_path"] = str(trace_path)

            if success:
                print("\n✅ TEST PASSED!")
                if response:
                    # Try to extract a readable summary from response
                    response_summary = (
                        response.get("result")
                        or response.get("output")
                        or response.get("data")
                    )
                    if response_summary:
                        print(f"   Result: {str(response_summary)[:200]}...")
                    else:
                        print(
                            f"   Response keys: {list(response.keys()) if isinstance(response, dict) else 'N/A'}"
                        )

                # Auto-save draft on success (only for last test if running all)
                if not no_auto_save and action_id and (not run_all or test_file == test_files[-1]):
                    print("\n💾 Auto-saving draft to persist changes...")
                    from cli.save_wdl_draft import save_wdl_draft

                    # Determine if standalone or agent mode
                    is_standalone = not agent_from_meta
                    draft_success, version = save_wdl_draft(
                        workflow_id=workflow_id,
                        standalone=is_standalone,
                        agent_name=agent_from_meta,
                    )

                    if draft_success:
                        print(f"   ✅ Draft saved: version {version}")
                        print("\n📌 NEXT STEPS:")
                        print("   - Continue iterating, or")
                        print(
                            f"   - Publish when ready: python cli/publish_wdl_action.py {action_id}"
                        )
                    else:
                        print("   ⚠️  Draft save failed (changes are local only)")
            else:
                print(f"\n❌ TEST FAILED: {msg}")
                overall_success = False

                # Generate roaming fix instructions (only for last failed test if running all)
                if not run_all or test_file == test_files[-1]:
                    print("\n📝 Generating fix instructions...")

                    try:
                        instructions_builder = RoamingInstructionsBuilder()
                        fix_instructions = instructions_builder.build_fix_instructions(
                            workspace=workspace,
                            trace_path=trace_path,
                        )

                        # Add API/tool context references if they exist
                        apis_dir = workspace / "apis"
                        tool_ctx_path = workspace / "tool_context.md"

                        if apis_dir.exists() or tool_ctx_path.exists():
                            fix_instructions += "\n\n---\n\n## 🔧 Available Context\n\n"
                            if apis_dir.exists():
                                manifest_file = apis_dir / "manifest.json"
                                if manifest_file.exists():
                                    try:
                                        manifest = json.loads(manifest_file.read_text())
                                        api_ids = manifest.get("api_ids", [])
                                        if api_ids:
                                            fix_instructions += f"- **API Specs**: `apis/` directory ({len(api_ids)} APIs)\n"
                                            fix_instructions += (
                                                "  - Full JSON specs: `apis/{api_id}.json`\n"
                                            )
                                            fix_instructions += (
                                                "  - Manifest: `apis/manifest.json`\n"
                                            )
                                    except Exception:
                                        pass
                            if tool_ctx_path.exists():
                                fix_instructions += "- **Tool WDLs**: `tool_context.md`\n"

                        fix_path = workspace / "cursor_fix_instructions.md"
                        fix_path.write_text(fix_instructions)

                        print(f"   Fix instructions saved: {fix_path}")
                        print("\n📌 NEXT STEPS:")
                        print(f"   1. Open in Cursor: cursor {workspace}")
                        print("   2. Read cursor_fix_instructions.md")
                        print("   3. Fetch WDL docs from https://adoptai.github.io/widdle_docs/operations/")
                        print("   4. Apply fixes to widdle.json")
                        print(f"   5. Re-run: python cli/test_wdl_action.py {workflow_id}")
                    except FileNotFoundError:
                        print("   ⚠️  Could not generate fix instructions (docs not found)")
                        print("\n📌 NEXT STEPS:")
                        print(f"   1. Check trace: {trace_path}")
                        print("   2. Fix widdle.json manually")
                        print(f"   3. Re-run: python cli/test_wdl_action.py {workflow_id}")

        except Exception as e:
            print(f"\n❌ Error during test: {e}")
            test_results["error"] = str(e)
            test_results["success"] = False
            overall_success = False
        
        all_results.append(test_results)
    
    # Summary for multiple tests
    if run_all or len(all_results) > 1:
        print("\n" + "=" * 80)
        print("📊 TEST SUMMARY")
        print("=" * 80)
        passed = sum(1 for r in all_results if r.get("success"))
        total = len(all_results)
        print(f"Passed: {passed}/{total}")
        for result in all_results:
            status = "✅" if result.get("success") else "❌"
            print(f"  {status} {result.get('test_file', 'unknown')}")
        print("=" * 80)
    
    # Return single result if not running all, otherwise return summary
    if not run_all and len(all_results) == 1:
        return all_results[0]
    
    return {
        "success": overall_success,
        "workflow_id": workflow_id,
        "test_count": len(all_results),
        "passed": sum(1 for r in all_results if r.get("success")),
        "results": all_results
    }


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test a WDL workflow action with transparent version detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test workflow (auto-detects version, draft status, agent)
  python test_wdl_action.py my-workflow-id

  # Run all test cases
  python test_wdl_action.py my-workflow-id --all

  # Just validate WDL (no remote execution)
  python test_wdl_action.py my-workflow-id --local-only

  # Auto-fix validation issues
  python test_wdl_action.py my-workflow-id --local-only --auto-fix

TRANSPARENT BEHAVIOR (default):
  - Version: Auto-detected from metadata (working/checked-out version)
  - Draft flag: Auto-determined from version publish status
  - Agent: Auto-detected from workspace location
  - Action ID: Auto-recovered by title search if missing

USE THESE FLAGS ONLY when automatic behavior doesn't work:
  --agent NAME      Override agent detection
  --version NUM     Override version detection
  --allow-draft     Force allow draft flag
""",
    )

    parser.add_argument("workflow_id", help="Workflow ID / workspace name")
    parser.add_argument(
        "--agent", help="Agent name (override auto-detection)"
    )
    parser.add_argument("--test", "-t", help="Test file to run (default: test_1.json)")
    parser.add_argument(
        "--all", action="store_true", help="Run all test cases in test_cases/ directory"
    )
    parser.add_argument(
        "--local-only",
        "-l",
        action="store_true",
        help="Validate WDL only, no remote test",
    )
    parser.add_argument(
        "--auto-fix",
        action="store_true",
        help="Auto-fix validation issues where possible",
    )
    # Hidden/advanced flags for when auto-detection doesn't work
    parser.add_argument(
        "--version", type=int,
        help="Force specific version (override auto-detection)"
    )
    parser.add_argument(
        "--allow-draft", action="store_true",
        help="Force allow draft flag (override auto-detection)"
    )
    # Backward compatibility
    parser.add_argument(
        "--no-auto-save", action="store_true", default=True,
        help=argparse.SUPPRESS  # Hidden, always True
    )

    args = parser.parse_args()

    results = run_test(
        workflow_id=args.workflow_id,
        agent_name=args.agent,
        test_file=args.test,
        local_only=args.local_only,
        no_auto_save=True,
        run_all=args.all,
    )

    sys.exit(0 if results.get("success") else 1)


if __name__ == "__main__":
    main()
