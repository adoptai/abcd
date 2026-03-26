#!/usr/bin/env python3
"""
Chrome Extension (CE) Agent Testing - Production validation via the Adopt Chrome Extension.

Tests agents as end users experience them: natural language query -> copilot -> agent
routing -> action execution -> response rendering.

This is Step 9 in the agent development workflow, after publishing.

Subcommands:
    setup     - Check prerequisites and guide first-time setup
    status    - Check if Chrome and the extension are reachable
    configure - Set playground profile and target URL for an agent (one-time)
    start     - Start Chrome session for an agent (run in a separate terminal)
    run       - Run CE test cases for an agent
    send      - Send an ad-hoc query to the copilot
    generate  - Generate CE test cases from agent metadata

Workflow per agent:
    # One-time setup:
    python cli/ce_test.py configure my-agent
    python cli/ce_test.py generate my-agent

    # Every test run:
    python cli/ce_test.py start my-agent      # in a separate terminal
    python cli/ce_test.py run my-agent

    # Ad-hoc query
    python cli/ce_test.py send my-agent "What can you do?"
"""

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.ce_browser import (
    boot_extension,
    find_chrome_executable,
    launch_chrome,
    run_python_runner,
    wait_for_chrome,
)
from cli.wdl_common.api_client import get_api_client_for_env
from cli.wdl_common.workspace_manager import get_workspace_manager

# Paths
CLI_DIR = Path(__file__).parent
PROJECT_ROOT = CLI_DIR.parent

CDP_PORT = 9222
CDP_URL = f"http://localhost:{CDP_PORT}"
CE_CONFIG_FILE = "ce_config.json"


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
            "Please run this in a separate terminal:\n"
            "  python cli/ce_test.py start <agent>\n"
            "Then click the Adopt extension icon on the target site if not injected automatically."
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


def load_ce_config(agent_path: Path) -> dict[str, Any] | None:
    """Load ce_config.json if it exists."""
    config_path = get_ce_test_dir(agent_path) / CE_CONFIG_FILE
    if config_path.exists():
        return json.loads(config_path.read_text())
    return None


def save_ce_config(agent_path: Path, config: dict[str, Any]) -> Path:
    """Save ce_config.json, creating ce_test_cases/ dir if needed."""
    test_dir = get_ce_test_dir(agent_path)
    test_dir.mkdir(parents=True, exist_ok=True)
    config_path = test_dir / CE_CONFIG_FILE
    config_path.write_text(json.dumps(config, indent=2))
    return config_path


def get_effective_target_url(agent_path: Path) -> str | None:
    """Get target URL: ce_config.json takes precedence over ce_test_suite.json."""
    config = load_ce_config(agent_path)
    if config:
        return config.get("target_url") or config.get("app_base_url")
    return get_target_url(agent_path)


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
# Python runner invocation (wraps ce_browser.run_python_runner)
# ---------------------------------------------------------------------------


def _run_tests(
    queries: list[dict[str, Any]],
    results_path: Path,
    profile_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run test queries via the Playwright-based Python runner."""
    return run_python_runner(queries, results_path, profile_id=profile_id, cdp_port=CDP_PORT)


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

        result = {**r, "passed": passed, "checks": checks}

        # Attach LLM evaluation criteria from expected_output if available
        expected_output = tc.get("expected_output")
        if expected_output and isinstance(expected_output, dict):
            result["llm_eval_criteria"] = {
                k: v
                for k, v in {
                    "description": expected_output.get("description"),
                    "key_fields": expected_output.get("key_fields"),
                    "validation": expected_output.get("validation"),
                    "sample_output": expected_output.get("sample_output"),
                }.items()
                if v
            }

        evaluated.append(result)

    return evaluated


def print_results_for_llm(
    results: list[dict[str, Any]], agent_name: str, target_url: str | None
) -> None:
    """Print detailed LLM-friendly results with expected criteria, execution log, and response.

    Output is designed for a coding agent to evaluate whether the CE actually worked,
    mirroring the format of test_runner.py's print_results().
    """
    total = len(results)
    passed_count = sum(1 for r in results if r.get("passed") and not r.get("error"))
    failed_count = sum(1 for r in results if not r.get("passed") and not r.get("error"))
    error_count = sum(1 for r in results if r.get("error"))

    header = f"CE Test Results: {agent_name}"
    if target_url:
        header += f" ({target_url})"

    print(f"\n{'=' * 80}")
    print(header)
    print(f"{'=' * 80}")
    print(
        f"Total: {total} | Passed: {passed_count} | Failed: {failed_count} | Errors: {error_count}"
    )
    print(f"{'-' * 80}")

    for r in results:
        qid = r.get("id", "?")
        cat = r.get("category", "")
        query = r.get("query", "")
        response = r.get("response") or ""
        resp_time = r.get("response_time_s")
        error = r.get("error")

        if error:
            status = "ERROR"
        elif r.get("passed"):
            status = "PASS"
        else:
            status = "FAIL"

        print(f"\n{'─' * 80}")
        print(f"[{status}] #{qid} {cat}: {query[:70]}")
        print(f"{'─' * 80}")

        # Query and basic metrics
        print(f"  Query: {query}")
        time_str = f"{resp_time:.1f}s" if resp_time else "?"
        print(f"  Response Time: {time_str} | Length: {len(response)} chars")

        # LLM evaluation criteria (from imported T1/T2/T3 test cases)
        llm_criteria = r.get("llm_eval_criteria")
        if llm_criteria:
            print("\n  EXPECTED OUTPUT (for LLM evaluation):")
            if llm_criteria.get("description"):
                print(f"    Description: {llm_criteria['description']}")
            if llm_criteria.get("key_fields"):
                print(f"    Key Fields: {', '.join(llm_criteria['key_fields'])}")
            if llm_criteria.get("validation"):
                print(f"    Validation: {llm_criteria['validation']}")
            if llm_criteria.get("sample_output"):
                sample = json.dumps(llm_criteria["sample_output"], indent=4)
                if len(sample) > 400:
                    sample = sample[:400] + "..."
                print(f"    Sample Output: {sample}")

        # Automated checks
        checks = r.get("checks", {})
        if checks:
            print("\n  AUTOMATED CHECKS:")
            for check_name, check_result in checks.items():
                print(f"    {check_name}: {'PASS' if check_result else 'FAIL'}")

        # Execution log
        exec_log = r.get("execution_log", [])
        if exec_log:
            print("\n  EXECUTION LOG:")
            for event in exec_log:
                elapsed = event.get("elapsed_s", 0)
                evt = event.get("event", "")
                details = {k: v for k, v in event.items() if k not in ("elapsed_s", "event")}
                detail_str = (
                    f" ({', '.join(f'{k}={v}' for k, v in details.items())})" if details else ""
                )
                print(f"    [{elapsed}s] {evt}{detail_str}")

        # Error details
        if error:
            print(f"\n  ERROR: {error}")

        # Actual response
        if response:
            display = response[:800]
            if len(response) > 800:
                display += "\n    ... (truncated)"
            print("\n  ACTUAL RESPONSE:")
            for line in display.split("\n"):
                print(f"    {line}")

    # Summary line
    print(f"\n{'=' * 80}")
    print(f"Summary: {passed_count}/{total} passed", end="")
    if failed_count:
        print(f", {failed_count} failed", end="")
    if error_count:
        print(f", {error_count} error(s)", end="")
    print(f"\n{'=' * 80}")


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_setup(_args: argparse.Namespace) -> int:
    """Check prerequisites and guide first-time setup."""
    all_ok = True

    # playwright Python package
    if importlib.util.find_spec("playwright"):
        print("[OK] playwright is installed")
    else:
        print("[MISSING] playwright is not installed")
        print("  Run: poetry install")
        all_ok = False

    # Playwright browser binaries
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        with sync_playwright() as p:
            # check_for_updates=False avoids network calls
            _ = p.chromium  # access the chromium launcher; will raise if binaries missing
        print("[OK] Playwright browser binaries present")
    except Exception:
        print("[MISSING] Playwright browser binaries not installed")
        print("  Run: playwright install chromium")
        all_ok = False

    # System Chrome (needed for extension loading)
    chrome = find_chrome_executable()
    if chrome:
        print(f"[OK] Google Chrome found at {chrome}")
    else:
        print("[MISSING] Google Chrome not found")
        print("  Install Chrome from https://www.google.com/chrome/")
        all_ok = False

    print()
    if all_ok:
        print("All prerequisites met. Workflow per agent:")
        print(
            "  1. python cli/ce_test.py configure <agent>   # one-time: pick profile + target URL"
        )
        print("  2. python cli/ce_test.py generate <agent>    # generate test cases")
        print("  3. python cli/ce_test.py start <agent>       # in a separate terminal")
        print("  4. python cli/ce_test.py run <agent>         # run tests")
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

    # Load CE config (profile + target URL)
    ce_config = load_ce_config(agent_path)
    profile_id = ce_config.get("profile_id") if ce_config else None
    profile_name = ce_config.get("profile_name") if ce_config else None
    target_url = get_effective_target_url(agent_path)

    # Check Chrome readiness
    chrome_ok, chrome_msg = check_chrome_ready()
    if not chrome_ok:
        start_hint = f"python cli/ce_test.py start {agent_name}"
        if not ce_config:
            start_hint = f"python cli/ce_test.py configure {agent_name}  # then: python cli/ce_test.py start {agent_name}"
        print(
            f"Chrome is not running on port {CDP_PORT}.\n"
            f"Please run this in a separate terminal:\n"
            f"  {start_hint}"
        )
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

    info = f"profile: {profile_name}" if profile_name else "no profile configured"
    print(f"Running {len(test_cases)} CE test(s) for '{agent_name}' ({info})...")

    # Prepare results directory
    results_dir = get_ce_results_dir(agent_path)
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_path = results_dir / f"ce_run_{timestamp}.json"

    # Run via Python/Playwright
    raw_results = _run_tests(test_cases, results_path, profile_id=profile_id)

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

    # Print detailed results for LLM evaluation
    print_results_for_llm(evaluated, agent_name, target_url)
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

    raw_results = _run_tests(queries, results_path)
    results_path.unlink(missing_ok=True)

    if raw_results:
        result = raw_results[0]
        response = result.get("response", "")
        error = result.get("error")

        # Show execution log
        exec_log = result.get("execution_log", [])
        if exec_log:
            print("\n--- Execution Log ---")
            for event in exec_log:
                elapsed = event.get("elapsed_s", 0)
                evt = event.get("event", "")
                details = {k: v for k, v in event.items() if k not in ("elapsed_s", "event")}
                detail_str = (
                    f" ({', '.join(f'{k}={v}' for k, v in details.items())})" if details else ""
                )
                print(f"  [{elapsed}s] {evt}{detail_str}")

        if error:
            print(f"\nError: {error}")
            return 1
        print(f"\n--- Response ---\n{response}")
        return 0
    else:
        print("No response captured.")
        return 1


def _load_t123_test_cases(agent_path: Path) -> list[dict[str, Any]]:
    """Load existing T1/T2/T3 test cases from agent and sub-action directories.

    Scans both agent-level test_cases/ and actions/*/test_cases/ for test_*.json
    files. Converts them to CE test case format, carrying expected_output verbatim.
    Skips test cases with non-empty workflow_params (those need params the chat UI can't provide).
    """
    imported: list[dict[str, Any]] = []

    # Agent-level tests (T2/T3)
    agent_tests_dir = agent_path / "test_cases"
    if agent_tests_dir.exists():
        for f in sorted(agent_tests_dir.glob("test_*.json")):
            try:
                tc = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            if not tc.get("prompt"):
                continue
            # Skip tests that depend on workflow_params
            if tc.get("workflow_params"):
                continue
            rel_path = f.relative_to(agent_path)
            imported.append(
                {
                    "source_file": str(rel_path),
                    "source_level": "agent",
                    "prompt": tc["prompt"],
                    "expected_output": tc.get("expected_output", {}),
                }
            )

    # Sub-action-level tests (T1)
    actions_dir = agent_path / "actions"
    if actions_dir.exists():
        for action_dir in sorted(actions_dir.iterdir()):
            tests_dir = action_dir / "test_cases"
            if not tests_dir.exists():
                continue
            for f in sorted(tests_dir.glob("test_*.json")):
                try:
                    tc = json.loads(f.read_text())
                except json.JSONDecodeError:
                    continue
                if not tc.get("prompt"):
                    continue
                if tc.get("workflow_params"):
                    continue
                rel_path = f.relative_to(agent_path)
                imported.append(
                    {
                        "source_file": str(rel_path),
                        "source_level": "subaction",
                        "source_action": action_dir.name,
                        "prompt": tc["prompt"],
                        "expected_output": tc.get("expected_output", {}),
                    }
                )

    return imported


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate CE test cases from agent metadata and existing T1/T2/T3 test cases."""
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

    # Try to import existing T1/T2/T3 test cases first
    t123_cases = _load_t123_test_cases(agent_path)

    if t123_cases:
        print(f"  Importing {len(t123_cases)} test case(s) from existing T1/T2/T3 test files")
        for tc in t123_cases:
            test_cases.append(
                {
                    "id": test_id,
                    "category": "core",
                    "query": tc["prompt"],
                    "description": f"From {tc['source_file']}",
                    "source_file": tc["source_file"],
                    "expected_output": tc["expected_output"],
                    "expected_behavior": {
                        "min_response_length": 50,
                        "max_response_time_s": 120,
                    },
                }
            )
            test_id += 1
    else:
        # Fall back to generating from agent metadata
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
            for action_dir in sorted(actions_dir.iterdir()):
                meta_path = action_dir / "metadata.json"
                if not meta_path.exists():
                    continue
                meta = json.loads(meta_path.read_text())
                query_basis = (
                    meta.get("statement") or meta.get("description") or meta.get("title", "")
                )
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

    suite = {
        "generated_at": datetime.now().isoformat(),
        "test_cases": test_cases,
    }

    suite_path = test_dir / "ce_test_suite.json"
    suite_path.write_text(json.dumps(suite, indent=2))

    print(f"Generated {len(test_cases)} CE test case(s) for '{agent_name}'")
    print(f"  Suite: {suite_path}")
    for tc in test_cases:
        src = f" ({tc['source_file']})" if tc.get("source_file") else ""
        print(f"  #{tc['id']} [{tc['category']}] {tc['query'][:70]}{src}")

    if not load_ce_config(agent_path):
        print(
            f"\n  Tip: Run 'python cli/ce_test.py configure {agent_name}' to set the profile and target URL."
        )

    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    """Set the playground profile and target URL for an agent."""
    agent_name = args.agent
    agent_path, _ = resolve_agent(agent_name)
    if not agent_path:
        print(f"Error: Agent '{agent_name}' not found in active environment.")
        return 1

    # Fetch profiles from API
    try:
        client = get_api_client_for_env()
    except Exception as e:
        print(f"Error: Could not initialize API client: {e}")
        return 1

    success, profiles, msg = client.list_playground_profiles()
    if not success or not profiles:
        print(f"Error: Could not fetch playground profiles: {msg}")
        return 1

    # Resolve profile: --profile-id flag or interactive selection
    if args.profile_id:
        profile = next((p for p in profiles if p["id"] == args.profile_id), None)
        if not profile:
            print(f"Error: Profile '{args.profile_id}' not found.")
            return 1
    else:
        print("Available playground profiles:")
        for i, p in enumerate(profiles, 1):
            default_marker = " (default)" if p.get("is_default") else ""
            print(f"  {i}. {p['profile_name']}{default_marker}  [{p['id']}]")
            if p.get("app_base_url"):
                print(f"       URL: {p['app_base_url']}")

        choice = input("\nEnter number or profile ID: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if idx < 0 or idx >= len(profiles):
                print("Invalid selection.")
                return 1
            profile = profiles[idx]
        else:
            profile = next((p for p in profiles if p["id"] == choice), None)
            if not profile:
                print(f"Profile '{choice}' not found.")
                return 1

    # Resolve target URL
    app_base_url = profile.get("app_base_url", "")
    if args.target_url:
        target_url = args.target_url
    elif args.profile_id:
        # Non-interactive: use profile's app_base_url directly
        target_url = app_base_url or None
    else:
        # Interactive: allow override, defaulting to app_base_url
        entered = input(f"Target URL [{app_base_url}]: ").strip()
        target_url = entered if entered else (app_base_url or None)

    # Save config
    config: dict[str, Any] = {
        "profile_id": profile["id"],
        "profile_name": profile["profile_name"],
        "app_base_url": app_base_url,
        "configured_at": datetime.now().isoformat(),
    }
    if target_url and target_url != app_base_url:
        config["target_url"] = target_url

    config_path = save_ce_config(agent_path, config)
    print(f"\nConfigured '{agent_name}':")
    print(f"  Profile: {profile['profile_name']} ({profile['id']})")
    print(f"  Target URL: {target_url or app_base_url or '(not set)'}")
    print(f"  Config: {config_path}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    """Start a Chrome session for an agent (blocking — run in a separate terminal)."""
    agent_name = args.agent
    agent_path, _ = resolve_agent(agent_name)
    if not agent_path:
        print(f"Error: Agent '{agent_name}' not found in active environment.")
        return 1

    # Resolve target URL
    if args.target_url:
        target_url = args.target_url
    else:
        target_url = get_effective_target_url(agent_path)

    if not target_url:
        print(
            f"Error: No target URL configured for '{agent_name}'.\n"
            f"  Run: python cli/ce_test.py configure {agent_name}\n"
            f"  Or:  python cli/ce_test.py start {agent_name} --target-url <url>"
        )
        return 1

    extension_path = os.environ.get("ADOPT_EXTENSION_PATH", str(PROJECT_ROOT / "../adoptce/dist"))
    profile_dir = PROJECT_ROOT / "user-profile"

    print("=== AdoptAI Extension Test Browser ===")
    print(f"Starting Chrome for '{agent_name}' at {target_url}")
    print("Close the browser window or press Ctrl+C to stop.\n")

    try:
        process = launch_chrome(target_url, extension_path, profile_dir, CDP_PORT)
    except RuntimeError as e:
        print(f"Error: {e}")
        return 1

    print("[boot] Waiting for Chrome to start...")
    if not wait_for_chrome(CDP_PORT):
        print("Error: Chrome did not start in time.")
        process.terminate()
        return 1

    boot_extension(CDP_PORT, target_url)

    print("\nChrome ready. Press Ctrl+C to stop.")
    try:
        process.wait()
    except KeyboardInterrupt:
        print("\nShutting down Chrome...")
        process.terminate()
        process.wait()

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
    gen_parser.set_defaults(func=cmd_generate)

    # configure
    configure_parser = subparsers.add_parser(
        "configure", help="Set playground profile and target URL for an agent (one-time setup)"
    )
    configure_parser.add_argument("agent", help="Agent name")
    configure_parser.add_argument("--profile-id", help="Profile ID (non-interactive)")
    configure_parser.add_argument(
        "--target-url", help="Override target URL (default: profile's app_base_url)"
    )
    configure_parser.set_defaults(func=cmd_configure)

    # start
    start_parser = subparsers.add_parser(
        "start", help="Start Chrome session for an agent (run in a separate terminal)"
    )
    start_parser.add_argument("agent", help="Agent name")
    start_parser.add_argument("--target-url", help="Override target URL from config")
    start_parser.set_defaults(func=cmd_start)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
