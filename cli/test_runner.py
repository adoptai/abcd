#!/usr/bin/env python3
"""
Enhanced Test Runner - Parallel and batch test execution.

Features:
- Parallel test execution for multiple actions
- Batch testing (--workspace, --agent flags)
- Via-agent testing for sub-actions through uber agents
- Multiple action arguments support

Usage:
    # Test single action
    python test_runner.py my-action

    # Test multiple actions in parallel
    python test_runner.py action1 action2 action3 --parallel 3

    # Test all actions in environment workspace
    python test_runner.py --workspace production-client-a

    # Test all sub-actions in agent
    python test_runner.py --agent inventory-orchestrator --all-subactions

    # Test sub-action through agent (via-agent testing)
    python test_runner.py my-agent --via-agent --subaction get-orderpoints
"""

import argparse
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.workspace_manager import get_workspace_manager, HierarchicalWorkspaceManager
from cli.wdl_common.api_client import AdoptAPIClient, get_api_client_for_env


@dataclass
class TestResult:
    """Result of a single test run."""
    action_id: str
    success: bool
    message: str
    duration_ms: int
    test_name: str = ""
    output: Any = None
    error: str | None = None


def run_single_test(
    action_id: str,
    manager: HierarchicalWorkspaceManager,
    local_only: bool = False,
    test_file: str | None = None,
) -> TestResult:
    """
    Run test for a single action.

    Args:
        action_id: Action/workflow ID
        manager: Workspace manager instance
        local_only: Only validate WDL locally
        test_file: Specific test file to run

    Returns:
        TestResult with outcome
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
                error=str(e),
            )

        # Basic validation
        if not isinstance(wdl, list) or len(wdl) == 0:
            return TestResult(
                action_id=action_id,
                success=False,
                message="Invalid WDL structure",
                duration_ms=int((time.time() - start_time) * 1000),
                error="WDL must be a non-empty list",
            )

        if local_only:
            return TestResult(
                action_id=action_id,
                success=True,
                message="WDL valid (local only)",
                duration_ms=int((time.time() - start_time) * 1000),
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
                error="No remote action_id in metadata. Save draft first.",
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
                error=f"Test file not found: {test_path.name}",
            )

        test_case = json.loads(test_path.read_text())
        prompt = test_case.get("prompt", "test")
        workflow_params = test_case.get("workflow_params", {})

        # Load profile with inheritance
        resolved_profile = manager.resolve_adopt_profile(
            action_path=workspace,
            agent_name=action_info.get("agent_name"),
            env_name=action_info.get("env_name"),
        )

        # Run test
        client = get_api_client_for_env()  # Uses active environment
        success, response, msg = client.run_action(
            action_id=remote_action_id,
            user_input=prompt,
            profile=resolved_profile,
            workflow_params=workflow_params,
            allow_draft=True,
        )

        return TestResult(
            action_id=action_id,
            success=success,
            message="Test passed" if success else msg[:100],
            duration_ms=int((time.time() - start_time) * 1000),
            test_name=test_path.name,
            output=response.get("data", {}).get("output") if isinstance(response, dict) else None,
            error=None if success else msg,
        )

    except Exception as e:
        import time
        return TestResult(
            action_id=action_id,
            success=False,
            message="Error",
            duration_ms=int((time.time() - start_time) * 1000),
            error=str(e),
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
        expected_tool_calls = test_case.get("expected_behavior", {}).get("expected_tool_calls", [])

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
            data = response.get("data", {})
            actual_tool_calls = data.get("tool_calls", [])
            if actual_tool_calls:
                called_tools = [tc.get("tool") or tc.get("name") for tc in actual_tool_calls]
                for expected in expected_tool_calls:
                    if expected not in called_tools:
                        tool_call_verified = False
                        break

        final_success = success and tool_call_verified

        return TestResult(
            action_id=f"{agent_id}:{subaction_id}",
            success=final_success,
            message="Via-agent test passed" if final_success else msg[:100] if not success else "Expected tool not called",
            duration_ms=int((time.time() - start_time) * 1000),
            test_name=test_path.name,
            output=response.get("data", {}).get("output") if isinstance(response, dict) else None,
            error=None if final_success else (msg if not success else f"Expected tools: {expected_tool_calls}"),
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
    local_only: bool = False,
) -> list[TestResult]:
    """
    Run tests for multiple actions in parallel.

    Args:
        action_ids: List of action IDs to test
        manager: Workspace manager
        max_workers: Maximum parallel workers
        local_only: Only validate locally

    Returns:
        List of TestResults
    """
    results: list[TestResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_action = {
            executor.submit(run_single_test, aid, manager, local_only): aid
            for aid in action_ids
        }

        for future in as_completed(future_to_action):
            action_id = future_to_action[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append(TestResult(
                    action_id=action_id,
                    success=False,
                    message="Executor error",
                    duration_ms=0,
                    error=str(e),
                ))

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


def print_results(results: list[TestResult], start_time: datetime) -> None:
    """Print test results summary."""
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

    if passed:
        print("\n✅ PASSED:")
        for r in passed:
            print(f"   {r.action_id} ({r.duration_ms}ms) - {r.message}")

    if failed:
        print("\n❌ FAILED:")
        for r in failed:
            print(f"   {r.action_id} ({r.duration_ms}ms) - {r.message}")
            if r.error:
                print(f"      Error: {r.error[:200]}")

    print("=" * 80)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Enhanced test runner with parallel and batch support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test single action
  %(prog)s my-action

  # Test multiple actions in parallel
  %(prog)s action1 action2 action3 --parallel 3

  # Test all actions in environment
  %(prog)s --workspace production-env

  # Test all sub-actions in agent
  %(prog)s --agent my-agent --all-subactions

  # Test sub-action through agent (via-agent)
  %(prog)s my-agent --via-agent --subaction get-orderpoints
        """,
    )

    # Positional arguments (multiple actions)
    parser.add_argument("actions", nargs="*", help="Action ID(s) to test")

    # Batch testing options
    parser.add_argument("--workspace", "-w", help="Test all actions in environment workspace")
    parser.add_argument("--agent", "-a", help="Agent ID for batch testing or via-agent")
    parser.add_argument("--all-subactions", action="store_true", help="Test all sub-actions in agent")

    # Via-agent testing
    parser.add_argument("--via-agent", action="store_true", help="Test sub-action through agent")
    parser.add_argument("--subaction", help="Sub-action to test via agent")

    # Parallel execution
    parser.add_argument("--parallel", "-p", type=int, default=5, help="Max parallel workers (default: 5)")

    # Test options
    parser.add_argument("--local-only", "-l", action="store_true", help="Only validate WDL locally")
    parser.add_argument("--test", "-t", help="Specific test file to run")
    parser.add_argument("--env", "-e", help="Environment to use")

    args = parser.parse_args()

    manager = get_workspace_manager()

    # Set environment if provided
    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"❌ Environment not found: {args.env}")
            return 1

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
        print(f"   Found {len(action_ids)} actions")
        results = run_parallel_tests(action_ids, manager, args.parallel, args.local_only)

    elif args.agent and args.all_subactions:
        # Batch test all sub-actions in agent
        print(f"\n🧪 Testing all sub-actions in agent: {args.agent}")
        action_ids = collect_actions_from_agent(args.agent, manager, args.env)
        if not action_ids:
            print("❌ No sub-actions found in agent")
            return 1
        print(f"   Found {len(action_ids)} sub-actions")
        results = run_parallel_tests(action_ids, manager, args.parallel, args.local_only)

    elif args.actions:
        # Test specific action(s)
        action_ids = args.actions
        if len(action_ids) == 1:
            # Single action - use simple test
            print(f"\n🧪 Testing: {action_ids[0]}")
            result = run_single_test(action_ids[0], manager, args.local_only, args.test)
            results = [result]
        else:
            # Multiple actions - parallel test
            print(f"\n🧪 Testing {len(action_ids)} actions in parallel")
            results = run_parallel_tests(action_ids, manager, args.parallel, args.local_only)

    else:
        parser.print_help()
        return 0

    # Print results
    print_results(results, start_time)

    # Return 0 if all passed, 1 otherwise
    all_passed = all(r.success for r in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())


