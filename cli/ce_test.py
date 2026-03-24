#!/usr/bin/env python3
"""
Chrome Extension (CE) Agent Testing - Production validation via the Adopt Chrome Extension.

Tests agents as end users experience them: natural language query -> copilot -> agent
routing -> action execution -> response rendering.

This is Step 9 in the agent development workflow, after publishing.

Subcommands:
    setup     - Check prerequisites and guide first-time setup
    status    - Check if Chrome and the extension are reachable
    run       - Run CE test cases for an agent
    send      - Send an ad-hoc query to the copilot
    generate  - Generate CE test cases from agent metadata

Usage:
    # Check prerequisites
    python cli/ce_test.py setup

    # Check Chrome + extension readiness
    python cli/ce_test.py status

    # Run all CE tests for an agent
    python cli/ce_test.py run my-agent

    # Run specific test(s)
    python cli/ce_test.py run my-agent --test 1
    python cli/ce_test.py run my-agent --test 1,3,5

    # Send ad-hoc query
    python cli/ce_test.py send my-agent "What can you do?"

    # Generate test cases from agent metadata
    python cli/ce_test.py generate my-agent
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.workspace_manager import get_workspace_manager

# Paths
CLI_DIR = Path(__file__).parent
PROJECT_ROOT = CLI_DIR.parent
CE_HARNESS_DIR = CLI_DIR / "ce_harness"
RUN_TESTS_SCRIPT = CE_HARNESS_DIR / "run-tests.mjs"

CDP_PORT = 9222
CDP_URL = f"http://localhost:{CDP_PORT}"


# ---------------------------------------------------------------------------
# Chrome / Extension readiness checks
# ---------------------------------------------------------------------------


def check_chrome_ready() -> tuple[bool, str]:
    """Check if Chrome is running with CDP on the expected port."""
    try:
        req = urllib.request.Request(f"{CDP_URL}/json", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            targets = json.loads(resp.read().decode())
            tab_count = sum(1 for t in targets if t.get("type") == "page")
            return True, f"Chrome is running ({tab_count} tab(s) open)"
    except (urllib.error.URLError, OSError):
        return False, (
            f"Chrome is not running on port {CDP_PORT}.\n"
            "Please run this in your terminal:\n"
            f"  {CE_HARNESS_DIR / 'start.sh'} <target-url>\n"
            "Then click the Adopt extension icon on the target site."
        )


def check_extension_visible() -> tuple[bool, str]:
    """Check if the extension iframe target exists in CDP targets."""
    try:
        req = urllib.request.Request(f"{CDP_URL}/json", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            targets = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError):
        return False, "Chrome is not reachable."

    ext = next(
        (
            t
            for t in targets
            if "chrome-extension://" in t.get("url", "")
            and "index.html" in t.get("url", "")
            and t.get("type") in ("iframe", "page")
        ),
        None,
    )
    if ext:
        return True, "Extension popup is visible and reachable."
    return False, (
        "Extension popup not found.\nPlease click the Adopt extension icon on the target site tab."
    )


# ---------------------------------------------------------------------------
# Agent / test case resolution
# ---------------------------------------------------------------------------


def resolve_agent(agent_name: str) -> tuple[Path | None, dict[str, Any] | None]:
    """Resolve agent path and metadata from workspace."""
    manager = get_workspace_manager()
    agent = manager.get_agent(agent_name)
    if not agent:
        return None, None
    return Path(agent["path"]), agent


def get_ce_test_dir(agent_path: Path) -> Path:
    return agent_path / "ce_test_cases"


def get_ce_results_dir(agent_path: Path) -> Path:
    return agent_path / "traces" / "ce_results"


def load_ce_test_cases(agent_path: Path) -> list[dict[str, Any]]:
    """Load CE test cases from agent workspace."""
    test_dir = get_ce_test_dir(agent_path)

    # Try suite file first
    suite_path = test_dir / "ce_test_suite.json"
    if suite_path.exists():
        data = json.loads(suite_path.read_text())
        if isinstance(data, dict) and "test_cases" in data:
            return data["test_cases"]
        if isinstance(data, list):
            return data

    # Fall back to individual ce_test_*.json files
    cases = []
    if test_dir.exists():
        for f in sorted(test_dir.glob("ce_test_*.json")):
            if f.name == "ce_test_suite.json":
                continue
            try:
                cases.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                print(f"  Warning: skipping invalid JSON in {f.name}")
    return cases


def get_target_url(agent_path: Path) -> str | None:
    """Get target URL from ce_test_suite.json if available."""
    suite_path = get_ce_test_dir(agent_path) / "ce_test_suite.json"
    if suite_path.exists():
        data = json.loads(suite_path.read_text())
        if isinstance(data, dict):
            return data.get("target_url")
    return None


# ---------------------------------------------------------------------------
# Node.js runner invocation
# ---------------------------------------------------------------------------


def run_node_runner(
    queries: list[dict[str, Any]],
    results_path: Path,
    target_id: int | None = None,
) -> list[dict[str, Any]]:
    """Invoke the Node.js test runner and return parsed results."""
    if not RUN_TESTS_SCRIPT.exists():
        print(f"Error: {RUN_TESTS_SCRIPT} not found. Run 'python cli/ce_test.py setup' first.")
        sys.exit(1)

    # Write queries to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, dir=str(CE_HARNESS_DIR)
    ) as f:
        json.dump(queries, f)
        test_file = f.name

    try:
        cmd = [
            "node",
            str(RUN_TESTS_SCRIPT),
            f"--test-file={test_file}",
            f"--results-file={results_path}",
        ]
        if target_id is not None:
            cmd.append(str(target_id))

        result = subprocess.run(
            cmd,
            cwd=str(CE_HARNESS_DIR),
            capture_output=False,
            timeout=600,
        )

        if result.returncode != 0:
            print(f"\nNode runner exited with code {result.returncode}")
            return []

        if results_path.exists():
            return json.loads(results_path.read_text())
        return []
    except subprocess.TimeoutExpired:
        print("\nNode runner timed out after 600 seconds.")
        return []
    finally:
        Path(test_file).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Result evaluation
# ---------------------------------------------------------------------------


def evaluate_results(
    results: list[dict[str, Any]], test_cases: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply expected_behavior checks to results."""
    # Index test cases by id for lookup
    cases_by_id = {tc["id"]: tc for tc in test_cases}

    evaluated = []
    for r in results:
        tc = cases_by_id.get(r.get("id", -1), {})
        expected = tc.get("expected_behavior", {})
        response = r.get("response") or ""
        checks: dict[str, Any] = {}
        passed = True

        if r.get("error"):
            passed = False
        elif not expected:
            # Smoke test: any response > 20 chars is a pass
            passed = len(response) > 20
        else:
            # should_contain
            for keyword in expected.get("should_contain", []):
                found = keyword.lower() in response.lower()
                checks[f"contains '{keyword}'"] = found
                if not found:
                    passed = False

            # should_not_contain
            for keyword in expected.get("should_not_contain", []):
                found = keyword.lower() in response.lower()
                checks[f"excludes '{keyword}'"] = not found
                if found:
                    passed = False

            # min_response_length
            min_len = expected.get("min_response_length")
            if min_len is not None:
                ok = len(response) >= min_len
                checks["min_response_length"] = ok
                if not ok:
                    passed = False

            # max_response_time_s
            max_time = expected.get("max_response_time_s")
            resp_time = r.get("response_time_s")
            if max_time is not None and resp_time is not None:
                ok = resp_time <= max_time
                checks["max_response_time_s"] = ok
                if not ok:
                    passed = False

        evaluated.append({**r, "passed": passed, "checks": checks})

    return evaluated


def print_summary(results: list[dict[str, Any]], agent_name: str, target_url: str | None) -> None:
    """Print LLM-friendly summary."""
    header = f"CE Test Results: {agent_name}"
    if target_url:
        header += f" ({target_url})"
    print(f"\n{header}")
    print("=" * len(header))

    total = len(results)
    passed_count = 0
    failed_count = 0
    error_count = 0

    for r in results:
        qid = r.get("id", "?")
        cat = r.get("category", "")
        query = r.get("query", "")
        resp_time = r.get("response_time_s")
        resp_len = len(r.get("response") or "")
        time_str = f"{resp_time:.1f}s" if resp_time else "?"
        error = r.get("error")

        if error:
            error_count += 1
            print(f"[ERROR] #{qid} {cat}: {query[:60]} -- {error}")
        elif r.get("passed"):
            passed_count += 1
            print(f"[PASS]  #{qid} {cat}: {query[:60]} ({time_str}, {resp_len} chars)")
        else:
            failed_count += 1
            # Find first failing check
            failing = [k for k, v in r.get("checks", {}).items() if not v]
            reason = failing[0] if failing else "response too short"
            print(f"[FAIL]  #{qid} {cat}: {query[:60]} -- {reason}")

    print(f"\nSummary: {passed_count}/{total} passed", end="")
    if failed_count:
        print(f", {failed_count} failed", end="")
    if error_count:
        print(f", {error_count} error(s)", end="")
    print()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_setup(_args: argparse.Namespace) -> int:
    """Check prerequisites and guide first-time setup."""
    all_ok = True

    # Node.js
    if shutil.which("node"):
        print("[OK] Node.js is installed")
    else:
        print("[MISSING] Node.js is not installed. Install it from https://nodejs.org/")
        all_ok = False

    # Chrome
    if shutil.which("google-chrome"):
        print("[OK] Google Chrome is installed")
    else:
        print("[MISSING] Google Chrome is not installed")
        all_ok = False

    # npm dependencies
    node_modules = CE_HARNESS_DIR / "node_modules"
    if (node_modules / "ws").exists() and (node_modules / "playwright").exists():
        print("[OK] npm dependencies installed (playwright, ws)")
    else:
        print("[MISSING] npm dependencies not installed")
        print(f"  Run: cd {CE_HARNESS_DIR} && npm install playwright ws")
        all_ok = False

    # Test harness scripts
    if RUN_TESTS_SCRIPT.exists():
        print("[OK] Test harness scripts present")
    else:
        print("[MISSING] Test harness scripts not found")
        print(f"  Expected: {RUN_TESTS_SCRIPT}")
        all_ok = False

    # start.sh
    start_sh = CE_HARNESS_DIR / "start.sh"
    if start_sh.exists():
        print("[OK] start.sh present")
    else:
        print("[MISSING] start.sh not found")
        all_ok = False

    print()
    if all_ok:
        print("All prerequisites met. To start testing:")
        print(f"  1. Run in your terminal: {start_sh} <target-url>")
        print("  2. Click the Adopt extension icon on the target site")
        print("  3. Run: python cli/ce_test.py status")
    else:
        print("Some prerequisites are missing. Fix the issues above and re-run setup.")

    return 0 if all_ok else 1


def cmd_status(_args: argparse.Namespace) -> int:
    """Check Chrome and extension readiness."""
    chrome_ok, chrome_msg = check_chrome_ready()
    print(f"Chrome: {chrome_msg}")

    if not chrome_ok:
        return 1

    ext_ok, ext_msg = check_extension_visible()
    print(f"Extension: {ext_msg}")

    return 0 if ext_ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    """Run CE test cases for an agent."""
    agent_name = args.agent
    agent_path, agent_meta = resolve_agent(agent_name)

    if not agent_path:
        print(f"Error: Agent '{agent_name}' not found in active environment.")
        return 1

    # Check Chrome readiness
    chrome_ok, chrome_msg = check_chrome_ready()
    if not chrome_ok:
        print(chrome_msg)
        return 1
    ext_ok, ext_msg = check_extension_visible()
    if not ext_ok:
        print(ext_msg)
        return 1

    # Load test cases
    test_cases = load_ce_test_cases(agent_path)
    if not test_cases:
        print(f"No CE test cases found for '{agent_name}'.")
        print(f"  Generate them: python cli/ce_test.py generate {agent_name}")
        print(f"  Or create: {get_ce_test_dir(agent_path) / 'ce_test_suite.json'}")
        return 1

    # Filter by --test
    if args.test:
        test_ids = {int(x.strip()) for x in args.test.split(",")}
        test_cases = [tc for tc in test_cases if tc.get("id") in test_ids]
        if not test_cases:
            print(f"No test cases matching IDs: {args.test}")
            return 1

    target_url = get_target_url(agent_path)
    print(f"Running {len(test_cases)} CE test(s) for '{agent_name}'...")

    # Prepare results directory
    results_dir = get_ce_results_dir(agent_path)
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_path = results_dir / f"ce_run_{timestamp}.json"

    # Run via Node
    raw_results = run_node_runner(test_cases, results_path)

    if not raw_results:
        print("No results captured. Check Chrome/extension status.")
        return 1

    # Evaluate
    evaluated = evaluate_results(raw_results, test_cases)

    # Save evaluated results
    run_data = {
        "agent": agent_name,
        "run_at": datetime.now().isoformat(),
        "target_url": target_url,
        "summary": {
            "total": len(evaluated),
            "passed": sum(1 for r in evaluated if r.get("passed")),
            "failed": sum(1 for r in evaluated if not r.get("passed") and not r.get("error")),
            "errored": sum(1 for r in evaluated if r.get("error")),
        },
        "results": evaluated,
    }
    results_path.write_text(json.dumps(run_data, indent=2))

    # Print summary
    print_summary(evaluated, agent_name, target_url)
    print(f"Results: {results_path}")

    failed = sum(1 for r in evaluated if not r.get("passed"))
    return 1 if failed > 0 else 0


def cmd_send(args: argparse.Namespace) -> int:
    """Send an ad-hoc query to the copilot."""
    agent_name = args.agent
    query = args.query

    agent_path, _ = resolve_agent(agent_name)
    if not agent_path:
        print(f"Error: Agent '{agent_name}' not found in active environment.")
        return 1

    # Check readiness
    chrome_ok, chrome_msg = check_chrome_ready()
    if not chrome_ok:
        print(chrome_msg)
        return 1
    ext_ok, ext_msg = check_extension_visible()
    if not ext_ok:
        print(ext_msg)
        return 1

    # Create a single-query test case
    queries = [{"id": 1, "category": "ad-hoc", "query": query}]

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        results_path = Path(f.name)

    raw_results = run_node_runner(queries, results_path)
    results_path.unlink(missing_ok=True)

    if raw_results:
        response = raw_results[0].get("response", "")
        error = raw_results[0].get("error")
        if error:
            print(f"\nError: {error}")
            return 1
        print(f"\n--- Response ---\n{response}")
        return 0
    else:
        print("No response captured.")
        return 1


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate CE test cases from agent metadata."""
    agent_name = args.agent
    agent_path, agent_meta = resolve_agent(agent_name)

    if not agent_path:
        print(f"Error: Agent '{agent_name}' not found in active environment.")
        return 1

    test_cases: list[dict[str, Any]] = []
    test_id = 1

    # Meta query: what can you do?
    test_cases.append(
        {
            "id": test_id,
            "category": "meta",
            "query": "What can you do?",
            "description": "Tests agent capability listing",
            "expected_behavior": {
                "min_response_length": 50,
            },
        }
    )
    test_id += 1

    # Read sub-actions from agent metadata
    sub_actions = agent_meta.get("sub_actions", []) if agent_meta else []
    actions_dir = agent_path / "actions"

    if sub_actions:
        for sa in sub_actions:
            sa_id = sa.get("action_id") or sa.get("id", "")
            sa_title = sa.get("title", sa_id)
            sa_desc = sa.get("description", "")
            sa_statement = sa.get("statement", "")

            # Try to load from local metadata for richer info
            sa_path = actions_dir / sa_id / "metadata.json"
            if sa_path.exists():
                local_meta = json.loads(sa_path.read_text())
                sa_title = local_meta.get("title", sa_title)
                sa_desc = local_meta.get("description", sa_desc)
                sa_statement = local_meta.get("statement", sa_statement)

            # Use statement or description as basis for a test query
            query_basis = sa_statement or sa_desc or sa_title
            if query_basis:
                test_cases.append(
                    {
                        "id": test_id,
                        "category": "core",
                        "query": query_basis,
                        "description": f"Tests sub-action: {sa_title}",
                        "expected_behavior": {
                            "min_response_length": 50,
                            "max_response_time_s": 120,
                        },
                    }
                )
                test_id += 1
    elif actions_dir.exists():
        # Fall back to scanning actions directory
        for action_dir in sorted(actions_dir.iterdir()):
            meta_path = action_dir / "metadata.json"
            if not meta_path.exists():
                continue
            meta = json.loads(meta_path.read_text())
            query_basis = meta.get("statement") or meta.get("description") or meta.get("title", "")
            if query_basis:
                test_cases.append(
                    {
                        "id": test_id,
                        "category": "core",
                        "query": query_basis,
                        "description": f"Tests action: {meta.get('title', action_dir.name)}",
                        "expected_behavior": {
                            "min_response_length": 50,
                            "max_response_time_s": 120,
                        },
                    }
                )
                test_id += 1

    # Meta query: show all actions
    test_cases.append(
        {
            "id": test_id,
            "category": "meta",
            "query": "Show me all available actions",
            "description": "Tests action listing",
            "expected_behavior": {
                "min_response_length": 30,
            },
        }
    )

    # Write suite file
    test_dir = get_ce_test_dir(agent_path)
    test_dir.mkdir(parents=True, exist_ok=True)

    target_url = args.target_url if hasattr(args, "target_url") and args.target_url else None

    suite = {
        "target_url": target_url,
        "generated_at": datetime.now().isoformat(),
        "test_cases": test_cases,
    }

    suite_path = test_dir / "ce_test_suite.json"
    suite_path.write_text(json.dumps(suite, indent=2))

    print(f"Generated {len(test_cases)} CE test case(s) for '{agent_name}'")
    print(f"  Suite: {suite_path}")
    for tc in test_cases:
        print(f"  #{tc['id']} [{tc['category']}] {tc['query'][:70]}")

    if target_url:
        print(f"\n  Target URL: {target_url}")
    else:
        print("\n  Note: No target URL set. Edit the suite file to add one, or pass --target-url")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ce_test",
        description="Chrome Extension (CE) agent testing - production validation via the Adopt copilot.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # setup
    setup_parser = subparsers.add_parser(
        "setup", help="Check prerequisites and guide first-time setup"
    )
    setup_parser.set_defaults(func=cmd_setup)

    # status
    status_parser = subparsers.add_parser("status", help="Check Chrome and extension readiness")
    status_parser.set_defaults(func=cmd_status)

    # run
    run_parser = subparsers.add_parser("run", help="Run CE test cases for an agent")
    run_parser.add_argument("agent", help="Agent name")
    run_parser.add_argument("--test", help="Test ID(s) to run, comma-separated (e.g., 1,3,5)")
    run_parser.set_defaults(func=cmd_run)

    # send
    send_parser = subparsers.add_parser("send", help="Send an ad-hoc query to the copilot")
    send_parser.add_argument("agent", help="Agent name (for workspace resolution)")
    send_parser.add_argument("query", help="Query to send")
    send_parser.set_defaults(func=cmd_send)

    # generate
    gen_parser = subparsers.add_parser(
        "generate", help="Generate CE test cases from agent metadata"
    )
    gen_parser.add_argument("agent", help="Agent name")
    gen_parser.add_argument("--target-url", help="Target URL where the copilot runs")
    gen_parser.set_defaults(func=cmd_generate)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
