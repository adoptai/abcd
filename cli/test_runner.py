#!/usr/bin/env python3
"""
Enhanced Test Runner - Parallel and batch test execution with LLM-integrated output.

Features:
- Remote compilation check via --compile (MANDATORY before remote testing)
- Parallel test execution for multiple actions
- Batch testing (--workspace, --agent flags)
- Via-agent testing for sub-actions through uber agents
- Multiple action arguments support
- Always uses allow_draft=True (simpler, avoids metadata issues)
- Robust trace extraction from success and error responses
- Direct LLM/Cursor-friendly output for automated evaluation

Usage:
    # Compile WDL (MANDATORY before remote testing)
    python test_runner.py my-action --compile

    # Test single action (remote execution)
    python test_runner.py my-action

    # Test multiple actions in parallel
    python test_runner.py action1 action2 action3 --parallel 3

    # Test all actions in environment workspace
    python test_runner.py --workspace production-client-a

    # Test all sub-actions in agent
    python test_runner.py --agent inventory-orchestrator --all-subactions

    # Test sub-action through agent (via-agent testing)
    python test_runner.py my-agent --via-agent --subaction get-orderpoints

    # Run all test cases for an action
    python test_runner.py my-action --all

    # Verbose mode for Cursor/LLM integration
    python test_runner.py my-action --verbose
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import get_api_client_for_env
from cli.wdl_common.workspace_manager import HierarchicalWorkspaceManager, get_workspace_manager


@dataclass
class TestResult:
    """Result of a single test run with full context for LLM evaluation."""

    action_id: str
    success: bool
    message: str
    duration_ms: int
    test_name: str = ""
    prompt: str = ""
    output: Any = None
    error: str | None = None
    execution_trace: dict | None = None
    test_criteria: dict | None = None  # From test file (description, key_fields, etc.)
    wdl_operations: list | None = None  # For context on failure
    workspace_path: str = ""
    failed_operation: str | None = None  # Which operation failed
    trace_path: str | None = None  # Path to saved trace file


def extract_execution_trace(response: dict | None, error_msg: str | None) -> dict | None:
    """
    Extract execution trace from response or error message.

    Handles both:
    - Success case: trace in response.data.debug_tracing
    - Error case: trace embedded in JSON error message
    """
    execution_trace = None

    # Try extracting from response first
    if response and isinstance(response, dict):
        # Check data.debug_tracing (primary location)
        data = response.get("data", {})
        if isinstance(data, dict):
            execution_trace = data.get("debug_tracing") or data.get("execution_trace")

        # Check direct response fields
        if not execution_trace:
            execution_trace = response.get("debug_tracing") or response.get("execution_trace")

    # Try extracting from error message if not found
    if not execution_trace and error_msg:
        # Check if error message contains debug_tracing or execution_trace
        if "debug_tracing" in error_msg or "execution_trace" in error_msg:
            try:
                # Try to extract JSON from error message
                # Format: "Failed: XXX - {json}"
                if " - {" in error_msg:
                    json_part = error_msg.split(" - ", 1)[1]
                    try:
                        error_json = json.loads(json_part)
                        _ej_data = error_json.get("data") or {}
                        execution_trace = (
                            error_json.get("debug_tracing")
                            or _ej_data.get("debug_tracing")
                            or error_json.get("execution_trace")
                            or _ej_data.get("execution_trace")
                        )
                    except json.JSONDecodeError:
                        pass

                # Fallback: regex extraction
                if not execution_trace:
                    # Try debug_tracing first
                    for pattern_name in ["debug_tracing", "execution_trace"]:
                        pattern = rf'"{pattern_name}"\s*:\s*(\{{(?:[^{{}}]|(?:\{{[^{{}}]*\}}))*\}})'
                        match = re.search(pattern, error_msg)
                        if match:
                            try:
                                execution_trace = json.loads(match.group(1))
                                break
                            except json.JSONDecodeError:
                                pass

                # Last resort: find full JSON object
                if not execution_trace:
                    json_start = error_msg.find("{")
                    if json_start != -1:
                        json_str = error_msg[json_start:]
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
                                error_json = json.loads(json_str[:end_pos])
                                _ej2_data = error_json.get("data") or {}
                                execution_trace = (
                                    error_json.get("debug_tracing")
                                    or _ej2_data.get("debug_tracing")
                                    or error_json.get("execution_trace")
                                    or _ej2_data.get("execution_trace")
                                )
                            except json.JSONDecodeError:
                                pass
            except Exception:
                pass

    return execution_trace


def identify_failed_operation(trace: dict | None, error_msg: str | None) -> str | None:
    """Identify which operation failed from trace or error message."""
    if trace and isinstance(trace, dict):
        # Look for failed operation in trace
        for op_id, op_data in trace.items():
            if isinstance(op_data, dict):
                if op_data.get("error") or op_data.get("status") == "failed":
                    return op_id

    # Try to extract from error message
    if error_msg:
        # Common patterns: "Operation X failed", "Error in X", etc.
        patterns = [
            r"Operation\s+['\"]?(\w+)['\"]?\s+failed",
            r"Error\s+in\s+['\"]?(\w+)['\"]?",
            r"['\"]?(\w+)['\"]?\s+operation\s+failed",
            r"Input\s+['\"]?(\w+)['\"]?\s+is\s+not",
        ]
        for pattern in patterns:
            match = re.search(pattern, error_msg, re.IGNORECASE)
            if match:
                return match.group(1)

    return None


def save_trace(workspace: Path, result: "TestResult") -> Path | None:
    """
    Save execution trace to file for debugging.

    Args:
        workspace: Workspace path
        result: TestResult with trace data

    Returns:
        Path to saved trace file, or None if nothing to save
    """
    if not result.execution_trace and not result.error:
        return None

    traces_dir = workspace / "traces"
    traces_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    test_name = result.test_name.replace(".json", "") if result.test_name else "test"
    trace_path = traces_dir / f"trace_{test_name}_{timestamp}.json"

    trace_data = {
        "timestamp": datetime.now().isoformat(),
        "action_id": result.action_id,
        "test_file": result.test_name,
        "prompt": result.prompt,
        "success": result.success,
        "duration_ms": result.duration_ms,
        "output": result.output if result.success else None,
        "error_message": result.error if not result.success else None,
        "execution_trace": result.execution_trace,
        "test_criteria": result.test_criteria,
        "failed_operation": result.failed_operation,
        "wdl_operations": result.wdl_operations,
    }

    trace_path.write_text(json.dumps(trace_data, indent=2, default=str))
    return trace_path


def get_all_test_files(test_cases_dir: Path) -> list[str]:
    """Get all test JSON files in the test_cases directory."""
    if not test_cases_dir.exists():
        return []
    return sorted([f.name for f in test_cases_dir.glob("test_*.json")])


def run_single_test(
    action_id: str,
    manager: HierarchicalWorkspaceManager,
    compile_only: bool = False,
    test_file: str | None = None,
    verbose: bool = False,
    validate: bool = False,
) -> TestResult:
    """
    Run test for a single action with full context for LLM evaluation.

    Modes (mutually exclusive):
    - compile_only: Compile WDL via remote compiler API (no remote execution)
    - validate: Run compiler validation before remote execution
    - default: Skip validation, go straight to remote execution (remote already compiles)

    Args:
        action_id: Action/workflow ID
        manager: Workspace manager instance
        compile_only: Compile WDL via remote compiler (no remote execution)
        test_file: Specific test file to run
        verbose: Include WDL operations in output for debugging
        validate: Run compiler validation before remote execution

    Returns:
        TestResult with full context for LLM/Cursor evaluation
    """
    import time

    start_time = time.time()

    try:
        # Find action
        action_info = manager.find_action(action_id)
        if not action_info:
            return TestResult(
                action_id=action_id,
                success=False,
                message="Action not found",
                duration_ms=0,
                error="Action workspace not found",
            )

        workspace = action_info["path"]
        wdl_path = workspace / "widdle.json"

        if not wdl_path.exists():
            return TestResult(
                action_id=action_id,
                success=False,
                message="No WDL file",
                duration_ms=0,
                error="widdle.json not found",
                workspace_path=str(workspace),
            )

        # Load and validate WDL
        try:
            wdl = json.loads(wdl_path.read_text())
        except json.JSONDecodeError as e:
            return TestResult(
                action_id=action_id,
                success=False,
                message="Invalid JSON",
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"JSON syntax error in widdle.json: {e.msg} at line {e.lineno}, column {e.colno}",
                workspace_path=str(workspace),
            )

        # Basic validation
        if not isinstance(wdl, list) or len(wdl) == 0:
            return TestResult(
                action_id=action_id,
                success=False,
                message="Invalid WDL structure",
                duration_ms=int((time.time() - start_time) * 1000),
                error="WDL must be a non-empty list",
                workspace_path=str(workspace),
            )

        # Extract WDL operation IDs for context
        wdl_operations = []
        for op in wdl:
            if isinstance(op, dict) and op.get("id"):
                wdl_operations.append(
                    {
                        "id": op.get("id"),
                        "operation": op.get("operation", "METADATA"),
                    }
                )

        # Compiler validation via API (only when --compile or --validate is set)
        # Skipped by default since remote execution already compiles the WDL.
        if compile_only or validate:
            try:
                api_client = get_api_client_for_env()
                success_val, val_data, val_msg = api_client.validate_wdl(wdl)
                if success_val and val_data and val_data.get("status") == "FAILURE":
                    error_lines = []
                    for err in val_data.get("errors", []):
                        line = f"[{err['error_code']}] {err['error_msg']}"
                        if err.get("block_id"):
                            line += f" (block: {err['block_id']})"
                        if err.get("suggestion"):
                            line += f" | Suggestion: {err['suggestion']}"
                        error_lines.append(line)
                    error_summary = "\n".join(error_lines) if error_lines else "Compilation failed"
                    return TestResult(
                        action_id=action_id,
                        success=False,
                        message="WDL compilation failed",
                        duration_ms=int((time.time() - start_time) * 1000),
                        error=error_summary,
                        workspace_path=str(workspace),
                    )
            except Exception:
                if compile_only:
                    return TestResult(
                        action_id=action_id,
                        success=False,
                        message="Compilation endpoint unreachable",
                        duration_ms=int((time.time() - start_time) * 1000),
                        error="Could not reach WDL compilation endpoint. Check ADOPT_API_ENDPOINT.",
                        workspace_path=str(workspace),
                    )
                # --validate mode: log warning but continue to remote execution
                pass

        if compile_only:
            return TestResult(
                action_id=action_id,
                success=True,
                message="Compilation successful",
                duration_ms=int((time.time() - start_time) * 1000),
                wdl_operations=wdl_operations if verbose else None,
                workspace_path=str(workspace),
            )

        # Get remote action ID for testing
        metadata = action_info.get("metadata", {})
        remote_action_id = metadata.get("action_id") or metadata.get("remote_action_id")

        if not remote_action_id:
            return TestResult(
                action_id=action_id,
                success=False,
                message="Not linked to remote",
                duration_ms=int((time.time() - start_time) * 1000),
                error="No remote action_id in metadata. Run: python cli/save_wdl_draft.py --workflow-id "
                + action_id,
                workspace_path=str(workspace),
            )

        # Load test case
        test_cases_dir = workspace / "test_cases"
        if test_file:
            test_path = test_cases_dir / test_file
        else:
            test_path = test_cases_dir / "test_1.json"

        if not test_path.exists():
            return TestResult(
                action_id=action_id,
                success=False,
                message="No test case",
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"Test file not found: {test_path.name}. Create test_cases/test_1.json with prompt and workflow_params.",
                workspace_path=str(workspace),
            )

        test_case = json.loads(test_path.read_text())
        prompt = test_case.get("prompt", "test")
        workflow_params = test_case.get("workflow_params", {})

        # Extract test criteria for LLM evaluation (full expected_output from test file)
        expected_output = test_case.get("expected_output", {})
        test_criteria = {
            "description": test_case.get("description") or expected_output.get("description", ""),
            "expected_behavior": test_case.get("expected_behavior", {}),
            "expected_output": expected_output,
            "validation_type": expected_output.get("validation", "similarity"),
            "key_fields": expected_output.get("key_fields", []),
            "sample_output": expected_output.get("sample_output"),
        }
        # Clean up empty/None criteria
        test_criteria = {k: v for k, v in test_criteria.items() if v}

        # Load profile with inheritance
        resolved_profile = manager.resolve_adopt_profile(
            action_path=workspace,
            agent_name=action_info.get("agent_name"),
            env_name=action_info.get("env_name"),
        )

        # Run test (always allow_draft=True for simpler testing)
        client = get_api_client_for_env()
        success, response, msg = client.run_action(
            action_id=remote_action_id,
            user_input=prompt,
            profile=resolved_profile,
            workflow_params=workflow_params,
            allow_draft=True,
        )

        # Extract execution trace
        execution_trace = extract_execution_trace(response, msg if not success else None)

        # Identify failed operation
        failed_op = identify_failed_operation(execution_trace, msg if not success else None)

        # Extract clean output
        output = None
        if response and isinstance(response, dict):
            data = response.get("data", {})
            if isinstance(data, dict):
                # Remove trace from output for cleaner display
                output = {
                    k: v for k, v in data.items() if k not in ("debug_tracing", "execution_trace")
                }
            else:
                output = data

        result = TestResult(
            action_id=action_id,
            success=success,
            message="Test passed" if success else msg[:200] if msg else "Unknown error",
            duration_ms=int((time.time() - start_time) * 1000),
            test_name=test_path.name,
            prompt=prompt,
            output=output,
            error=msg if not success else None,
            execution_trace=execution_trace,
            test_criteria=test_criteria if test_criteria else None,
            wdl_operations=wdl_operations if (verbose or not success) else None,
            workspace_path=str(workspace),
            failed_operation=failed_op,
        )

        # Save trace to file (especially useful on failure)
        trace_file = save_trace(workspace, result)
        if trace_file:
            result.trace_path = str(trace_file)

        return result

    except Exception as e:
        import traceback

        return TestResult(
            action_id=action_id,
            success=False,
            message="Exception during test",
            duration_ms=int((time.time() - start_time) * 1000),
            error=f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()[:500]}",
        )


def run_via_agent_test(
    agent_id: str,
    subaction_id: str,
    manager: HierarchicalWorkspaceManager,
    test_file: str | None = None,
) -> TestResult:
    """
    Run test for a sub-action through its parent agent (via-agent testing).

    This tests that the agent correctly routes to the sub-action.
    """
    import time

    start_time = time.time()

    try:
        # Find agent
        env = manager.active_env
        if not env:
            return TestResult(
                action_id=f"{agent_id}:{subaction_id}",
                success=False,
                message="No active environment",
                duration_ms=0,
                error="No active environment set",
            )
        agent = manager.get_agent(agent_id, env)

        if not agent:
            return TestResult(
                action_id=f"{agent_id}:{subaction_id}",
                success=False,
                message="Agent not found",
                duration_ms=0,
                error=f"Agent {agent_id} not found",
            )

        remote_agent_id = agent.get("remote_action_id")
        if not remote_agent_id:
            return TestResult(
                action_id=f"{agent_id}:{subaction_id}",
                success=False,
                message="Agent not linked",
                duration_ms=0,
                error="Agent has no remote_action_id",
            )

        # Find sub-action info
        sub_action = None
        for sa in agent.get("sub_actions", []):
            if sa.get("action_id") == subaction_id:
                sub_action = sa
                break

        if not sub_action:
            return TestResult(
                action_id=f"{agent_id}:{subaction_id}",
                success=False,
                message="Sub-action not in agent",
                duration_ms=0,
                error=f"Sub-action {subaction_id} not found in agent {agent_id}",
            )

        # Load test case for sub-action (via-agent version)
        from cli.wdl_common.workspace_manager import WORKSPACES_DIR

        agent_path = WORKSPACES_DIR / env / "agents" / agent_id
        via_tests_dir = agent_path / "test_cases" / "subaction_tests"

        if test_file:
            test_path = via_tests_dir / test_file
        else:
            # Look for test file matching sub-action
            test_path = via_tests_dir / f"test_{subaction_id}_via_agent.json"

        if not test_path.exists():
            # Fallback to regular sub-action test case
            subaction_path = agent_path / "actions" / subaction_id
            test_path = subaction_path / "test_cases" / "test_1.json"

        if not test_path.exists():
            return TestResult(
                action_id=f"{agent_id}:{subaction_id}",
                success=False,
                message="No test case",
                duration_ms=int((time.time() - start_time) * 1000),
                error="No via-agent test case found",
            )

        test_case = json.loads(test_path.read_text())
        prompt = test_case.get("prompt", f"Use {subaction_id}")
        workflow_params = test_case.get("workflow_params", {})
        expected_tool_calls = (test_case.get("expected_behavior") or {}).get(
            "expected_tool_calls", []
        )

        # Resolve profile
        resolved_profile = manager.resolve_adopt_profile(
            action_path=agent_path,
            agent_name=agent_id,
            env_name=env,
        )

        # Run agent with prompt designed to trigger sub-action
        client = get_api_client_for_env()  # Uses active environment
        success, response, msg = client.run_action(
            action_id=remote_agent_id,
            user_input=prompt,
            profile=resolved_profile,
            workflow_params=workflow_params,
            allow_draft=True,
        )

        # Check if expected tool was called (if we can extract tool calls from response)
        tool_call_verified = True
        if expected_tool_calls and isinstance(response, dict):
            # Try to extract tool calls from response
            # Use `or {}` to guard against `"data": null` in the response JSON
            data = response.get("data") or {}
            actual_tool_calls = data.get("tool_calls", [])
            if actual_tool_calls:
                called_tools = [tc.get("tool") or tc.get("name") for tc in actual_tool_calls]
                for expected in expected_tool_calls:
                    if expected not in called_tools:
                        tool_call_verified = False
                        break

        final_success = success and tool_call_verified

        _resp_data = response.get("data") if isinstance(response, dict) else None
        return TestResult(
            action_id=f"{agent_id}:{subaction_id}",
            success=final_success,
            message="Via-agent test passed"
            if final_success
            else msg[:100]
            if not success
            else "Expected tool not called",
            duration_ms=int((time.time() - start_time) * 1000),
            test_name=test_path.name,
            output=_resp_data.get("output") if isinstance(_resp_data, dict) else None,
            error=None
            if final_success
            else (msg if not success else f"Expected tools: {expected_tool_calls}"),
        )

    except Exception as e:
        import time

        return TestResult(
            action_id=f"{agent_id}:{subaction_id}",
            success=False,
            message="Error",
            duration_ms=int((time.time() - start_time) * 1000),
            error=str(e),
        )


def run_parallel_tests(
    action_ids: list[str],
    manager: HierarchicalWorkspaceManager,
    max_workers: int = 5,
    compile_only: bool = False,
    verbose: bool = False,
    validate: bool = False,
) -> list[TestResult]:
    """
    Run tests for multiple actions in parallel.

    Args:
        action_ids: List of action IDs to test
        manager: Workspace manager
        max_workers: Maximum parallel workers
        compile_only: Only compile via remote compiler (no remote execution)
        verbose: Include WDL operations for debugging
        validate: Run compiler validation before remote execution

    Returns:
        List of TestResults
    """
    results: list[TestResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_action = {
            executor.submit(
                run_single_test, aid, manager, compile_only, None, verbose, validate
            ): aid
            for aid in action_ids
        }

        for future in as_completed(future_to_action):
            action_id = future_to_action[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append(
                    TestResult(
                        action_id=action_id,
                        success=False,
                        message="Executor error",
                        duration_ms=0,
                        error=str(e),
                    )
                )

    return results


def collect_actions_from_workspace(
    workspace_name: str,
    manager: HierarchicalWorkspaceManager,
) -> list[str]:
    """Collect all action IDs from an environment workspace."""
    actions = manager.list_actions(env_name=workspace_name, include_subactions=True)
    return [a["action_id"] for a in actions]


def collect_actions_from_agent(
    agent_name: str,
    manager: HierarchicalWorkspaceManager,
    env_name: str | None = None,
) -> list[str]:
    """Collect all sub-action IDs from an agent."""
    agent = manager.get_agent(agent_name, env_name)
    if not agent:
        return []
    return [sa["action_id"] for sa in agent.get("sub_actions", [])]


def print_results(results: list[TestResult], start_time: datetime, verbose: bool = False) -> None:
    """
    Print test results with full context for LLM/Cursor evaluation.

    Output is designed to be consumed by Cursor agent for automated
    decision-making about next steps (fix WDL, iterate, etc.)
    """
    end_time = datetime.now()
    total_time = (end_time - start_time).total_seconds()

    passed = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    print("\n" + "=" * 80)
    print("📊 TEST RESULTS SUMMARY")
    print("=" * 80)
    print(f"Total: {len(results)} | Passed: {len(passed)} ✅ | Failed: {len(failed)} ❌")
    print(f"Time: {total_time:.2f}s")
    print("-" * 80)

    # Print passed tests with output for LLM evaluation
    if passed:
        print("\n✅ PASSED:")
        for r in passed:
            suffix = f" - {r.test_name}" if r.test_name else ""
            print(f"\n   ✅ {r.action_id} ({r.duration_ms}ms){suffix}")
            if r.message:
                print(f"      💬 {r.message}")
            if r.workspace_path:
                print(f"      📁 Workspace: {r.workspace_path}")

            # Show WDL operations when verbose
            if verbose and r.wdl_operations:
                print(f"      📦 WDL Operations ({len(r.wdl_operations)} blocks):")
                for op in r.wdl_operations:
                    print(f"         • {op.get('id')}: {op.get('operation')}")

            # Show test criteria (what was expected) - always show for LLM to verify
            if r.test_criteria:
                if r.test_criteria.get("description"):
                    print(f"      📝 Expected: {r.test_criteria['description']}")
                if r.test_criteria.get("key_fields"):
                    print(f"      🔑 Key Fields: {', '.join(r.test_criteria['key_fields'])}")

            # Show actual output for LLM verification
            if r.output:
                output_str = (
                    json.dumps(r.output, indent=2)
                    if isinstance(r.output, dict | list)
                    else str(r.output)
                )
                if len(output_str) > 800:
                    output_str = output_str[:800] + "\n      ... (truncated)"
                print("      📤 Actual Output:")
                for line in output_str.split("\n"):
                    print(f"         {line}")

            # Show trace path if saved
            if r.trace_path:
                print(f"      📁 Trace: {r.trace_path}")

    # Print failed tests with full context for LLM evaluation
    if failed:
        print("\n" + "=" * 80)
        print("❌ FAILED TESTS - DETAILED FOR LLM EVALUATION")
        print("=" * 80)

        for r in failed:
            print(f"\n{'─' * 80}")
            print(f"🔴 FAILED: {r.action_id}")
            print(f"{'─' * 80}")
            print(f"Test: {r.test_name}")
            print(f"Duration: {r.duration_ms}ms")
            if r.workspace_path:
                print(f"Workspace: {r.workspace_path}")

            # Show prompt used
            if r.prompt:
                print("\n📝 PROMPT USED:")
                print(f"   {r.prompt[:300]}{'...' if len(r.prompt) > 300 else ''}")

            # Show test criteria for LLM evaluation (prominently display expected output)
            if r.test_criteria:
                print("\n📋 EXPECTED OUTPUT (for LLM evaluation):")

                # Show description first (most important for LLM judgment)
                if r.test_criteria.get("description"):
                    print(f"   📝 Description: {r.test_criteria['description']}")

                # Show key fields that should be present
                if r.test_criteria.get("key_fields"):
                    print(f"   🔑 Key Fields: {', '.join(r.test_criteria['key_fields'])}")

                # Show validation type
                if r.test_criteria.get("validation_type"):
                    print(f"   📊 Validation: {r.test_criteria['validation_type']}")

                # Show sample output if provided
                if r.test_criteria.get("sample_output"):
                    sample_str = json.dumps(r.test_criteria["sample_output"], indent=4)
                    if len(sample_str) > 500:
                        sample_str = sample_str[:500] + "..."
                    print(f"   📄 Sample Output: {sample_str}")

                # Show expected behavior if any
                if r.test_criteria.get("expected_behavior"):
                    behavior_str = json.dumps(r.test_criteria["expected_behavior"], indent=4)
                    print(f"   🎯 Expected Behavior: {behavior_str[:300]}")

            # Show error
            print("\n❌ ERROR:")
            if r.error:
                # Pretty-print JSON errors if possible
                try:
                    if "{" in r.error:
                        json_start = r.error.find("{")
                        prefix = r.error[:json_start]
                        json_part = r.error[json_start:]
                        parsed = json.loads(json_part)
                        print(f"   {prefix}")
                        print(json.dumps(parsed, indent=4)[:1000])
                    else:
                        print(f"   {r.error[:1000]}")
                except Exception:
                    print(f"   {r.error[:1000]}")

            # Show failed operation
            if r.failed_operation:
                print(f"\n🎯 FAILED OPERATION: {r.failed_operation}")

            # Show execution trace
            if r.execution_trace:
                print("\n📍 EXECUTION TRACE:")
                trace_str = json.dumps(r.execution_trace, indent=2)
                if len(trace_str) > 2000:
                    trace_str = trace_str[:2000] + "\n   ... (truncated)"
                print(f"   {trace_str}")

            # Show WDL operations for context
            if r.wdl_operations:
                print(f"\n📦 WDL OPERATIONS ({len(r.wdl_operations)} total):")
                for op in r.wdl_operations:
                    marker = "→" if op.get("id") == r.failed_operation else " "
                    print(f"   {marker} {op.get('id')}: {op.get('operation')}")

            # Provide actionable instructions for Cursor
            print("\n🔧 CURSOR INSTRUCTIONS:")
            print("   1. Load debugging prompts for thorough instructions:")
            print("      → prompts/system/TESTING_PROMPT.md")
            print("      → prompts/guidelines/WDL_ISSUE_PATTERNS.md")
            print("      → prompts/system/DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md")
            print("   2. Read the error message and trace above")
            print(f"   3. Open widdle.json at: {r.workspace_path}/widdle.json")
            if r.failed_operation:
                print(f"   4. Find and fix operation: {r.failed_operation}")
            else:
                print("   4. Analyze which operation is causing the issue")
            print("   5. Common issues to check:")
            print("      - JQ_FILTER: Is extract_all set correctly? (default: true wraps in array)")
            print("      - EXTRACT: Is input an object (not array)?")
            print("      - REST: Is URL correct? Check auth params?")
            print("      - required_inputs: Is it a list of JSON strings?")
            print(f"   6. After fixing, re-run: python cli/test_runner.py {r.action_id}")

            # Show trace file location
            if r.trace_path:
                print(f"\n📁 Trace saved: {r.trace_path}")

    print("\n" + "=" * 80)

    # Final summary for Cursor decision
    if failed:
        print("\n🤖 LLM EVALUATION SUMMARY:")
        print(f"   {len(failed)} test(s) failed. Review the detailed output above.")
        print("   Cursor should analyze errors and fix the WDL before proceeding.")
        print("   After fixes, re-run tests to verify.")
    else:
        print("\n🤖 LLM EVALUATION REQUIRED:")
        print("   All tests executed successfully. Agent should verify:")
        print("   1. Does the actual output match the expected description?")
        print("   2. Are the expected key_fields present in the output?")
        print("   3. Is the data valid and non-hallucinated?")
        print("   4. Does the output format match what was expected?")
        print("")
        print(
            "   If output is valid → proceed with: python cli/save_wdl_draft.py --workflow-id <action-id>"
        )
        print("   If output needs fixes → modify widdle.json and re-run tests")

    print("=" * 80)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Enhanced test runner with parallel execution and LLM-integrated output",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Compile WDL (MANDATORY before remote testing)
  %(prog)s my-action --compile

  # Test single action (remote execution)
  %(prog)s my-action

  # Test with verbose output (shows WDL operations, full traces)
  %(prog)s my-action --verbose

  # Test multiple actions in parallel
  %(prog)s action1 action2 action3 --parallel 3

  # Test all actions in environment
  %(prog)s --workspace production-env

  # Test all sub-actions in agent
  %(prog)s --agent my-agent --all-subactions

  # Test sub-action through agent (via-agent)
  %(prog)s my-agent --via-agent --subaction get-orderpoints

Key Features:
  - Remote compilation check via --compile (MANDATORY before remote testing)
  - Always uses allow_draft=True (no metadata version tracking issues)
  - Robust trace extraction from success and error responses
  - LLM/Cursor-friendly output for automated evaluation
  - Parallel execution for multiple actions
        """,
    )

    # Positional arguments (multiple actions)
    parser.add_argument("actions", nargs="*", help="Action ID(s) to test")

    # Batch testing options
    parser.add_argument("--workspace", "-w", help="Test all actions in environment workspace")
    parser.add_argument("--agent", "-a", help="Agent ID for batch testing or via-agent")
    parser.add_argument(
        "--all-subactions", action="store_true", help="Test all sub-actions in agent"
    )

    # Via-agent testing
    parser.add_argument("--via-agent", action="store_true", help="Test sub-action through agent")
    parser.add_argument("--subaction", help="Sub-action to test via agent")

    # Parallel execution
    parser.add_argument(
        "--parallel", "-p", type=int, default=5, help="Max parallel workers (default: 5)"
    )

    # Test options
    parser.add_argument(
        "--compile",
        "-c",
        action="store_true",
        help="Compile WDL via remote compiler (no remote execution). MANDATORY before remote testing.",
    )
    parser.add_argument(
        "--validate", action="store_true", help="Run compiler validation before remote execution"
    )
    parser.add_argument("--test", "-t", help="Specific test file to run")
    parser.add_argument(
        "--all", action="store_true", help="Run all test cases in test_cases/ directory"
    )
    parser.add_argument("--env", "-e", help="Environment to use")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output with WDL operations and full traces",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which tests would be executed without making any API calls or saving traces",
    )

    args = parser.parse_args()

    manager = get_workspace_manager()

    # Set environment if provided
    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"❌ Environment not found: {args.env}")
            return 1

    dry_run: bool = getattr(args, "dry_run", False)
    start_time = datetime.now()
    results: list[TestResult] = []

    # Determine what to test
    action_ids: list[str] = []

    if args.via_agent:
        # Via-agent testing
        if not args.agent:
            print("❌ --via-agent requires --agent")
            return 1
        if not args.subaction and not args.actions:
            print("❌ --via-agent requires --subaction or action argument")
            return 1

        subaction = args.subaction or args.actions[0]
        if dry_run:
            print(f"\n🔍 [DRY-RUN] Would run via-agent test: {args.agent} → {subaction}")
            agent_info = manager.find_action(args.agent)
            if agent_info:
                workspace = agent_info["path"]
                test_cases_dir = workspace / "test_cases"
                test_file = args.test or "test_1.json"
                test_path = test_cases_dir / test_file
                tc = json.loads(test_path.read_text()) if test_path.exists() else {}
                print(f"   Agent workspace : {workspace}")
                print(f"   Subaction       : {subaction}")
                print(
                    f"   Test file       : {test_file} ({'found' if test_path.exists() else 'NOT FOUND'})"
                )
                if tc.get("prompt"):
                    print(f"   Prompt preview  : {tc['prompt'][:120]}...")
                print("\n   ✅ Dry-run complete — no API calls made")
            else:
                print(f"   ❌ Agent workspace not found: {args.agent}")
                return 1
            return 0

        print(f"\n🧪 Via-Agent Test: {args.agent} → {subaction}")
        result = run_via_agent_test(args.agent, subaction, manager, args.test)
        results = [result]

    elif args.workspace:
        # Batch test all actions in workspace
        print(f"\n🧪 Testing all actions in workspace: {args.workspace}")
        action_ids = collect_actions_from_workspace(args.workspace, manager)
        if not action_ids:
            print("❌ No actions found in workspace")
            return 1
        if dry_run:
            print(
                f"\n🔍 [DRY-RUN] Would test {len(action_ids)} action(s) from workspace: {args.workspace}"
            )
            for aid in action_ids:
                info = manager.find_action(aid)
                if info:
                    tc_dir = info["path"] / "test_cases"
                    tfiles = get_all_test_files(tc_dir) if tc_dir.exists() else []
                    print(f"   • {aid}: {len(tfiles)} test case(s)")
            print("\n   ✅ Dry-run complete — no API calls made")
            return 0
        print(f"   Found {len(action_ids)} actions")
        results = run_parallel_tests(
            action_ids, manager, args.parallel, args.compile, args.verbose, args.validate
        )

    elif args.agent and args.all_subactions:
        # Batch test all sub-actions in agent
        print(f"\n🧪 Testing all sub-actions in agent: {args.agent}")
        action_ids = collect_actions_from_agent(args.agent, manager, args.env)
        if not action_ids:
            print("❌ No sub-actions found in agent")
            return 1
        if dry_run:
            print(
                f"\n🔍 [DRY-RUN] Would test {len(action_ids)} sub-action(s) in agent: {args.agent}"
            )
            for aid in action_ids:
                info = manager.find_action(aid)
                if info:
                    tc_dir = info["path"] / "test_cases"
                    tfiles = get_all_test_files(tc_dir) if tc_dir.exists() else []
                    print(f"   • {aid}: {len(tfiles)} test case(s)")
            print("\n   ✅ Dry-run complete — no API calls made")
            return 0
        print(f"   Found {len(action_ids)} sub-actions")
        results = run_parallel_tests(
            action_ids, manager, args.parallel, args.compile, args.verbose, args.validate
        )

    elif args.actions:
        # Test specific action(s)
        action_ids = args.actions
        if dry_run:
            print(f"\n🔍 [DRY-RUN] Would test {len(action_ids)} action(s):")
            for aid in action_ids:
                info = manager.find_action(aid)
                if not info:
                    print(f"   • {aid}: ❌ workspace NOT FOUND")
                    continue
                workspace = info["path"]
                tc_dir = workspace / "test_cases"
                if args.all:
                    tfiles = get_all_test_files(tc_dir) if tc_dir.exists() else []
                    print(f"   • {aid}: {len(tfiles)} test case(s) — {', '.join(tfiles) or 'none'}")
                else:
                    test_file = args.test or "test_1.json"
                    test_path = tc_dir / test_file
                    tc = json.loads(test_path.read_text()) if test_path.exists() else {}
                    print(
                        f"   • {aid}: {test_file} ({'found' if test_path.exists() else 'NOT FOUND'})"
                    )
                    if tc.get("prompt"):
                        print(f"     Prompt: {tc['prompt'][:100]}...")
            print("\n   ✅ Dry-run complete — no API calls made")
            return 0

        if len(action_ids) == 1:
            # Single action - check if running all test cases
            action_id = action_ids[0]
            print(f"\n🧪 Testing: {action_id}")

            if args.all:
                # Run all test cases for this action
                action_info = manager.find_action(action_id)
                if action_info:
                    workspace = action_info["path"]
                    test_cases_dir = workspace / "test_cases"
                    test_files = get_all_test_files(test_cases_dir)

                    if test_files:
                        print(f"   Running {len(test_files)} test cases: {', '.join(test_files)}")
                        for test_file in test_files:
                            result = run_single_test(
                                action_id,
                                manager,
                                args.compile,
                                test_file,
                                args.verbose,
                                args.validate,
                            )
                            results.append(result)
                    else:
                        print(f"   ⚠️ No test files found in {test_cases_dir}")
                        results = [
                            TestResult(
                                action_id=action_id,
                                success=False,
                                message="No test files found",
                                duration_ms=0,
                                error=f"No test_*.json files in {test_cases_dir}",
                            )
                        ]
                else:
                    results = [
                        TestResult(
                            action_id=action_id,
                            success=False,
                            message="Action not found",
                            duration_ms=0,
                            error="Action workspace not found",
                        )
                    ]
            else:
                # Single test case
                result = run_single_test(
                    action_id, manager, args.compile, args.test, args.verbose, args.validate
                )
                results = [result]
        else:
            # Multiple actions - parallel test
            print(f"\n🧪 Testing {len(action_ids)} actions in parallel")
            results = run_parallel_tests(
                action_ids, manager, args.parallel, args.compile, args.verbose, args.validate
            )

    else:
        parser.print_help()
        return 0

    # Print results with LLM-friendly output
    print_results(results, start_time, args.verbose)

    # Return 0 if all passed, 1 otherwise
    all_passed = all(r.success for r in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
