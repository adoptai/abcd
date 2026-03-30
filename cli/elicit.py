#!/usr/bin/env python3
"""
Elicitation tool integration — start/stop the elicitation backend and import
captured workspace bundles into the abcd workspace hierarchy.

The elicitation backend records browser sessions (HAR network traffic, clicks,
voice narration, screenshots) and generates draft WDL workspaces from them.
Use it as Step 0 in the agent development workflow when target APIs are not yet
registered in the platform's discovery index.

Subcommands:
    backend start   - Install deps (if needed) and start the elicitation backend
    backend stop    - Stop the running backend
    status          - Check backend health and list recent projects
    import <id>     - Import an elicitation process as an abcd workspace bundle

Typical session:
    python cli/elicit.py backend start
    # Open Chrome → Load unpacked → abcd/elicitation/extension/
    # Create Project → Process → record session → stop capture
    python cli/elicit.py status            # find the process_id
    python cli/elicit.py import <process_id>
    python cli/elicit.py backend stop
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import signal
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CLI_DIR = Path(__file__).parent
PROJECT_ROOT = CLI_DIR.parent
ELICITATION_DIR = PROJECT_ROOT / "elicitation"
BACKEND_DIR = ELICITATION_DIR / "backend"
EXTENSION_DIR = ELICITATION_DIR / "extension"
PID_FILE = BACKEND_DIR / ".elicit.pid"
ENV_FILE = ELICITATION_DIR / ".env"  # loaded by backend/app/config.py from elicitation/
ENV_EXAMPLE_FILE = ELICITATION_DIR / ".env.example"

_BACKEND_PORT = os.environ.get("ELIC_PORT", "8000")
BACKEND_HOST = f"http://localhost:{_BACKEND_PORT}"
PING_PATH = "/sessions/ping"
PROJECTS_PATH = "/projects"

# ---------------------------------------------------------------------------
# Colours (re-use colorama if available, otherwise plain)
# ---------------------------------------------------------------------------

try:
    from colorama import Fore, Style
    from colorama import init as _colorama_init

    _colorama_init()

    def _green(s: str) -> str:
        return f"{Fore.GREEN}{s}{Style.RESET_ALL}"

    def _red(s: str) -> str:
        return f"{Fore.RED}{s}{Style.RESET_ALL}"

    def _yellow(s: str) -> str:
        return f"{Fore.YELLOW}{s}{Style.RESET_ALL}"

    def _cyan(s: str) -> str:
        return f"{Fore.CYAN}{s}{Style.RESET_ALL}"

    def _bold(s: str) -> str:
        return f"{Style.BRIGHT}{s}{Style.RESET_ALL}"

except ImportError:

    def _green(s: str) -> str:
        return s

    def _red(s: str) -> str:
        return s

    def _yellow(s: str) -> str:
        return s

    def _cyan(s: str) -> str:
        return s

    def _bold(s: str) -> str:
        return s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ping() -> bool:
    """Return True if the backend is reachable."""
    try:
        req = urllib.request.Request(
            BACKEND_HOST + PING_PATH,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception:
        return False


def _get(path: str) -> dict | list | None:
    """GET JSON from the backend; return None on error."""
    try:
        with urllib.request.urlopen(BACKEND_HOST + path, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _post_bytes(path: str) -> bytes | None:
    """POST to the backend and return raw response bytes; None on error."""
    try:
        req = urllib.request.Request(
            BACKEND_HOST + path,
            data=b"",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(_red(f"Backend error {exc.code}: {body}"))
        return None
    except Exception as exc:
        print(_red(f"Request failed: {exc}"))
        return None


def _read_pid() -> int | None:
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except Exception:
            pass
    return None


def _write_pid(pid: int) -> None:
    PID_FILE.write_text(str(pid))


def _clear_pid() -> None:
    if PID_FILE.exists():
        PID_FILE.unlink()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_backend_start(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Start the elicitation backend using the abcd Poetry venv."""
    import subprocess

    if _ping():
        print(_yellow("Backend is already running at " + BACKEND_HOST))
        print(f"  Extension: {_cyan(str(EXTENSION_DIR))}")
        return 0

    if not BACKEND_DIR.exists():
        print(_red(f"Elicitation backend not found at: {BACKEND_DIR}"))
        print("Run 'git pull' or re-clone the repo to restore it.")
        return 1

    # Ensure .env exists
    if not ENV_FILE.exists():
        if ENV_EXAMPLE_FILE.exists():
            shutil.copy(ENV_EXAMPLE_FILE, ENV_FILE)
            print(_yellow(f"Created {ENV_FILE} from template."))
            print(_yellow("Set ANTHROPIC_API_KEY in that file before using the chat feature."))
        else:
            print(_yellow(f"No .env found at {ENV_FILE} — backend will start without it."))

    # Start uvicorn using the same Python interpreter (abcd's Poetry venv)
    log_file = BACKEND_DIR / "elicit.log"
    log_fh = open(log_file, "a")  # noqa: SIM115
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            _BACKEND_PORT,
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND_DIR),
        env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)},
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )

    _write_pid(proc.pid)
    print(f"Starting elicitation backend (PID {proc.pid}) … logs → {log_file}", end="", flush=True)

    # Poll until ready (up to 10s)
    for _ in range(20):
        time.sleep(0.5)
        print(".", end="", flush=True)
        if _ping():
            break
    else:
        print()
        print(_red("Backend did not become ready in time. Check for port conflicts."))
        _clear_pid()
        return 1

    print()
    print(_green("✓ Elicitation backend running at " + BACKEND_HOST))
    print()
    print("  Next steps:")
    print("    1. Open Chrome → chrome://extensions/ → Enable Developer mode")
    print(f"    2. Load unpacked → {_cyan(str(EXTENSION_DIR))}")
    print("    3. Open the extension, create a Project → Process, and start recording")
    print(f"    4. When done: {_bold('python cli/elicit.py status')}  (to find your process_id)")
    print(f"    5. Import:    {_bold('python cli/elicit.py import <process_id>')}")
    return 0


def cmd_backend_stop(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Stop the running backend."""
    pid = _read_pid()
    if pid is None:
        if _ping():
            print(_yellow("Backend is running but PID file not found."))
            print("Stop it manually: find the uvicorn process and kill it.")
            return 1
        print(_yellow("Backend is not running."))
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
        # Wait a moment to confirm
        for _ in range(10):
            time.sleep(0.3)
            try:
                os.kill(pid, 0)  # check if still alive
            except ProcessLookupError:
                break
        _clear_pid()
        print(_green(f"✓ Backend (PID {pid}) stopped."))
        return 0
    except ProcessLookupError:
        _clear_pid()
        print(_yellow(f"Process {pid} was not running — cleared stale PID file."))
        return 0
    except Exception as exc:
        print(_red(f"Failed to stop backend: {exc}"))
        return 1


def cmd_status(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Check backend health and list recent projects."""
    running = _ping()
    pid = _read_pid()

    if running:
        pid_label = f" (PID {pid})" if pid else ""
        print(_green(f"✓ Backend running{pid_label} → {BACKEND_HOST}"))
    else:
        print(_red("✗ Backend is not running"))
        print(f"  Start it with: {_bold('python cli/elicit.py backend start')}")
        return 1

    projects = _get(PROJECTS_PATH)
    if not projects:
        print("  No projects yet — create one in the extension.")
        return 0

    print()
    print(_bold("Projects:"))
    for proj in projects:
        proj_id = proj.get("id", "?")
        proj_name = proj.get("name", "Unnamed")
        print(f"  {_cyan(proj_id)}  {proj_name}")

        # Fetch processes for this project
        processes = _get(f"/projects/{proj_id}/processes")
        if processes:
            for proc in processes:
                proc_id = proc.get("id", "?")
                proc_name = proc.get("name", "Unnamed")
                base_url = proc.get("base_url") or ""
                base_label = f"  [{base_url}]" if base_url else ""
                print(f"      process {_bold(proc_id)}  {proc_name}{_yellow(base_label)}")

    print()
    print(f"Import a process: {_bold('python cli/elicit.py import <process_id>')}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Download and unzip an elicitation workspace bundle into abcd workspaces."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from cli.wdl_common.workspace_manager import WORKSPACES_DIR, get_workspace_manager

    process_id: str = args.process_id
    env: str | None = args.env
    agent_id: str | None = args.agent

    if not _ping():
        print(_red("Backend is not running."))
        print(f"  Start it with: {_bold('python cli/elicit.py backend start')}")
        return 1

    # Fetch the zip bundle from the elicitation backend
    print(f"Exporting process {_cyan(process_id)} from elicitation backend …")
    raw = _post_bytes(f"/abcd/processes/{process_id}/export")
    if raw is None:
        return 1

    # Peek into the zip to get the action_id from metadata.json
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        print(_red("Backend returned invalid zip data."))
        return 1

    # Find metadata.json inside the zip (it's at <action_dir>/metadata.json)
    meta_entry = next((n for n in zf.namelist() if n.endswith("/metadata.json")), None)
    if not meta_entry:
        print(_red("metadata.json not found in export bundle."))
        return 1

    metadata = json.loads(zf.read(meta_entry).decode())
    action_id: str = metadata.get("action_id", "")
    if not action_id:
        print(_red("metadata.json does not contain an action_id."))
        return 1

    # Resolve workspace manager + active env
    manager = get_workspace_manager()
    resolved_env = env or manager.active_env
    if not resolved_env:
        print(
            _red("No active environment found. Create one with: python cli/workspace.py env create")
        )
        return 1

    if not manager.env_exists(resolved_env):
        print(_red(f"Environment not found: {resolved_env}"))
        return 1

    # Determine destination path
    if agent_id:
        if not manager.agent_exists(agent_id, resolved_env):
            print(_red(f"Agent not found: {agent_id} in env '{resolved_env}'"))
            return 1
        dest = WORKSPACES_DIR / resolved_env / "agents" / agent_id / "actions" / action_id
    else:
        dest = WORKSPACES_DIR / resolved_env / "actions" / action_id

    if dest.exists():
        print(_red(f"Destination already exists: {dest}"))
        print("Choose a different process or delete the existing action first.")
        return 1

    # Extract zip — strip the leading <action_dir>/ prefix so files land directly in dest
    prefix = meta_entry.rsplit("/metadata.json", 1)[0] + "/"
    dest.mkdir(parents=True)
    extracted: list[str] = []
    try:
        for name in zf.namelist():
            if not name.startswith(prefix) or name == prefix:
                continue
            rel = name[len(prefix) :]
            target = dest / rel
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
                extracted.append(rel)

        # Ensure expected directories exist (in case the bundle omitted empty ones)
        for d in ("test_cases", "versions", "traces", "apis", "tools"):
            (dest / d).mkdir(exist_ok=True)
    except Exception as exc:
        shutil.rmtree(dest, ignore_errors=True)
        print(_red(f"Extraction failed: {exc}"))
        return 1
    finally:
        zf.close()

    print(_green(f"✓ Imported → {dest}"))
    print(f"  Files: {', '.join(f for f in extracted if not f.startswith('.'))}")
    print()
    print("  Next steps:")
    print(f"    1. Review draft WDL:  {_cyan(str(dest / 'widdle.json'))}")
    print(f"    2. Compile:           {_bold(f'python cli/test_runner.py {action_id} --compile')}")
    print(f"    3. Test:              {_bold(f'python cli/test_runner.py {action_id}')}")
    print(
        f"    4. Save draft:        {_bold(f'python cli/save_wdl_draft.py --workflow-id {action_id}')}"
    )
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python cli/elicit.py",
        description="Elicitation tool integration for abcd agent development.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # backend
    backend_p = sub.add_parser("backend", help="Manage the elicitation backend process")
    backend_sub = backend_p.add_subparsers(dest="backend_action", required=True)
    backend_sub.add_parser("start", help="Start the backend")
    backend_sub.add_parser("stop", help="Stop the backend")

    # status
    sub.add_parser("status", help="Check backend status and list projects")

    # import
    import_p = sub.add_parser("import", help="Import a captured process as an abcd workspace")
    import_p.add_argument("process_id", help="Elicitation process ID to import")
    import_p.add_argument(
        "--env",
        metavar="ENV",
        default=None,
        help="Target environment (default: active env)",
    )
    import_p.add_argument(
        "--agent",
        metavar="AGENT_ID",
        default=None,
        help="Place imported action under this agent",
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "backend":
        if args.backend_action == "start":
            return cmd_backend_start(args)
        if args.backend_action == "stop":
            return cmd_backend_stop(args)

    if args.command == "status":
        return cmd_status(args)

    if args.command == "import":
        return cmd_import(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
