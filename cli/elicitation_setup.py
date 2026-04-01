#!/usr/bin/env python3
"""
Elicitation backend CLI — standalone setup and login recording workflow.

Can be used independently of Tabby for recording and reviewing login flows.
The register / validate / promote subcommands additionally require Tabby.

Subcommands:
    start                          - Start the elicitation backend
    stop                           - Stop the elicitation backend
    status                         - Show backend status and port assignments
    profile record <app> <url>     - Create a login recording session
    profile list                   - List recording sessions
    profile export <session_id>    - Analyze session and write bundle files
    profile review <bundle>        - Print review items from a bundle
    profile register <bundle>      - Provision Application + STAGING ServiceProfile (needs Tabby)
    profile validate <bundle>      - Scale app and wait for HEALTHY (needs Tabby)
    profile import <session_id>    - export + review + register [+ validate]
    profile promote <profile_db_id>- Promote STAGING → CANARY → ACTIVE (needs Tabby)

Typical first-time flow (elicitation only):
    python cli/elicitation_setup.py start
    python cli/elicitation_setup.py profile record "My App" https://app.example.com/login
    # ... record login in browser ...
    python cli/elicitation_setup.py profile export <session_id>
    python cli/elicitation_setup.py profile review workspaces/login-recordings/tabby-<id>-bundle.json

With Tabby also running:
    python cli/elicitation_setup.py profile register workspaces/login-recordings/tabby-<id>-bundle.json
    python cli/elicitation_setup.py profile validate workspaces/login-recordings/tabby-<id>-bundle.json
    python cli/elicitation_setup.py profile promote <profile_db_id>
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CLI_DIR = Path(__file__).parent
ABCD_DIR = CLI_DIR.parent
PROJECT_ROOT = ABCD_DIR.parent

LOGIN_RECORDINGS_DIR = ABCD_DIR / "workspaces" / "login-recordings"

ELIC_DIR = ABCD_DIR / "elicitation"
ELIC_BACKEND_DIR = ELIC_DIR / "backend"
ELIC_VENV_DIR = ABCD_DIR / ".venv"  # shared root venv
ELIC_PID_FILE = ELIC_BACKEND_DIR / ".elic-backend.pid"
ELIC_LOG_FILE = ELIC_BACKEND_DIR / ".elic-backend.log"
ELIC_PORT = int(os.environ.get("ELIC_PORT", "8000"))  # must match extension BACKEND_URL

# Tabby paths — only needed for register / validate / promote
TABBY_DIR = PROJECT_ROOT / "tabby"
TABBY_API_HOST = "http://localhost:8080"
WORKER_PID_FILE = TABBY_DIR / ".tabby-worker.pid"
WORKER_LOG_FILE = TABBY_DIR / ".tabby-worker.log"
CREDS_CACHE = TABBY_DIR / ".tabby-abcd-client.json"
ENV_LOCAL = TABBY_DIR / ".env.local"

ELICITATION_URL = os.environ.get("ELICITATION_URL", f"http://localhost:{ELIC_PORT}")

# ---------------------------------------------------------------------------
# Colours
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
# PID helpers
# ---------------------------------------------------------------------------


def _read_pid(pid_file: Path) -> int | None:
    if pid_file.exists():
        try:
            return int(pid_file.read_text().strip())
        except Exception:
            pass
    return None


def _clear_pid(pid_file: Path) -> None:
    if pid_file.exists():
        pid_file.unlink()


# ---------------------------------------------------------------------------
# Elicitation HTTP helpers
# ---------------------------------------------------------------------------


def _elic_http(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any] | list[Any]:
    url = ELICITATION_URL + path
    data = json.dumps(body).encode() if body is not None else b""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {method} {path}: {body_text}") from exc


def _elic_alive() -> bool:
    try:
        with urllib.request.urlopen(ELICITATION_URL + "/health", timeout=3) as resp:
            return json.loads(resp.read().decode()).get("status") == "ok"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tabby HTTP helpers (used only by register / validate / promote)
# ---------------------------------------------------------------------------


def _tabby_http(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: int = 15,
) -> dict[str, Any] | list[Any]:
    url = TABBY_API_HOST + path
    data = json.dumps(body).encode() if body is not None else b""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {method} {path}: {body_text}") from exc


def _tabby_alive() -> bool:
    try:
        with urllib.request.urlopen(TABBY_API_HOST + "/health/live", timeout=3) as resp:
            return json.loads(resp.read().decode()).get("status") == "ok"
    except Exception:
        return False


def _load_env_local() -> dict[str, str]:
    result: dict[str, str] = {}
    if not ENV_LOCAL.exists():
        return result
    for raw_line in ENV_LOCAL.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip()
    return result


def _get_admin_token() -> str | None:
    env_local = _load_env_local()
    email = env_local.get("ADMIN_BOOTSTRAP_EMAIL", "")
    password = env_local.get("ADMIN_BOOTSTRAP_PASSWORD", "")
    if not email or not password:
        print(_red(f"ADMIN_BOOTSTRAP_EMAIL / ADMIN_BOOTSTRAP_PASSWORD not set in {ENV_LOCAL}"))
        return None
    try:
        resp = _tabby_http("POST", "/login", {"email": email, "password": password})
        assert isinstance(resp, dict)
        return resp.get("token") or resp.get("access_token", "")
    except (RuntimeError, AssertionError) as exc:
        print(_red(f"Admin login failed: {exc}"))
        return None


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        segment = token.split(".")[1]
        segment += "=" * (4 - len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(segment))
    except Exception as exc:
        raise RuntimeError(f"Could not decode JWT payload: {exc}") from exc


def _load_cache() -> dict[str, Any]:
    if not CREDS_CACHE.exists():
        return {}
    try:
        return json.loads(CREDS_CACHE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    CREDS_CACHE.write_text(json.dumps(cache, indent=2) + "\n")


def _bypass_canary_gate(profile_db_id: str) -> bool:
    sql = (
        f"UPDATE service_profiles "
        f"SET canary_request_count=5, canary_error_count=0 "
        f"WHERE id='{profile_db_id}'"
    )
    try:
        subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "browser_hitl",
                "-d",
                "browser_hitl",
                "-c",
                sql,
            ],
            cwd=str(TABBY_DIR),
            check=True,
            capture_output=True,
        )
        return True
    except Exception as exc:
        print(_red(f"Canary bypass failed: {exc}"))
        return False


# ---------------------------------------------------------------------------
# Subcommands: start / stop / status
# ---------------------------------------------------------------------------


def cmd_start(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Start the elicitation backend in the background."""
    if _elic_alive():
        pid = _read_pid(ELIC_PID_FILE)
        pid_label = f" (PID {pid})" if pid else ""
        print(_yellow(f"Elicitation backend is already running{pid_label} at {ELICITATION_URL}"))
        return 0

    uvicorn_bin = ELIC_VENV_DIR / "bin" / "uvicorn"
    if not uvicorn_bin.exists():
        print(_red(f"uvicorn not found at {uvicorn_bin}"))
        print(
            f"  Install dependencies: {ELIC_VENV_DIR}/bin/pip install -r {ELIC_BACKEND_DIR}/requirements.txt"
        )
        return 1

    log_fh = open(ELIC_LOG_FILE, "a")  # noqa: SIM115
    proc = subprocess.Popen(
        [
            str(uvicorn_bin),
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(ELIC_PORT),
            "--reload",
        ],
        cwd=str(ELIC_BACKEND_DIR),
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )
    ELIC_PID_FILE.write_text(str(proc.pid))
    print(
        f"Starting elicitation backend (PID {proc.pid}) … logs → {_cyan(str(ELIC_LOG_FILE))}",
        end="",
        flush=True,
    )

    for _ in range(30):
        time.sleep(1)
        print(".", end="", flush=True)
        if _elic_alive():
            break
    else:
        print()
        print(_red(f"Backend did not become ready within 30s. Check logs: {ELIC_LOG_FILE}"))
        _clear_pid(ELIC_PID_FILE)
        return 1

    print()
    print(_green(f"✓ Elicitation backend ready at {ELICITATION_URL}"))
    print()
    print("  Extension connects to this backend for click/HAR capture.")
    print(
        f"  Profile recording:  {_bold('python cli/elicitation_setup.py profile record <app> <url>')}"
    )
    return 0


def cmd_stop(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Stop the elicitation backend."""
    pid = _read_pid(ELIC_PID_FILE)
    if pid is None:
        if _elic_alive():
            print(_yellow("Backend is running but PID file not found — stop it manually."))
            return 1
        print(_yellow("Elicitation backend is not running."))
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(15):
            time.sleep(0.3)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        _clear_pid(ELIC_PID_FILE)
        print(_green(f"✓ Elicitation backend (PID {pid}) stopped."))
        return 0
    except ProcessLookupError:
        _clear_pid(ELIC_PID_FILE)
        print(_yellow(f"Process {pid} was not running — cleared stale PID file."))
        return 0
    except Exception as exc:
        print(_red(f"Failed to stop backend: {exc}"))
        return 1


def cmd_status(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Show elicitation backend status and port assignments."""
    pid = _read_pid(ELIC_PID_FILE)
    alive = _elic_alive()

    print(_bold("Elicitation backend:"))
    if alive:
        pid_label = f" (PID {pid})" if pid else ""
        print(_green(f"  ✓  Running at {ELICITATION_URL}{pid_label}"))
    else:
        print(_red(f"  ✗  Not reachable at {ELICITATION_URL}"))
        if pid:
            print(_yellow(f"     (stale PID file: {pid})"))
        print(f"     Run: {_bold('python cli/elicitation_setup.py start')}")

    print()
    print(_bold("Port assignments:"))
    print(f"  Elicitation backend : {ELIC_PORT}  (extension BACKEND_URL must match)")
    print("  Tabby API           : 8080  (only needed for register/validate/promote)")

    print()
    print(_bold("Tabby API:"))
    if _tabby_alive():
        print(_green("  ✓  Running at http://localhost:8080"))
    else:
        print(_yellow("  –  Not running  (only needed for register/validate/promote)"))

    return 0 if alive else 1


# ---------------------------------------------------------------------------
# Subcommands: profile record / list / export / review
# (elicitation-only — no Tabby required)
# ---------------------------------------------------------------------------


def cmd_profile_record(args: argparse.Namespace) -> int:
    """Create a login recording session and print extension instructions."""
    if not _elic_alive():
        print(_red(f"Elicitation backend not reachable at {ELICITATION_URL}"))
        print(f"  Start it with: {_bold('python cli/elicitation_setup.py start')}")
        return 1

    app_name = args.app_name
    login_url = args.login_url

    print(f"Creating login recording session for {_cyan(app_name)} …", end=" ", flush=True)
    try:
        resp = _elic_http("POST", "/login-sessions", {"app_name": app_name, "login_url": login_url})
        assert isinstance(resp, dict)
    except (RuntimeError, AssertionError) as exc:
        print()
        print(_red(f"Failed to create login session: {exc}"))
        return 1

    session_id = resp.get("id", "")
    print(_green("✓"))
    print()
    print(_bold("Login recording session created:"))
    print(f"  Session ID : {_cyan(session_id)}")
    print(f"  App name   : {app_name}")
    print(f"  Login URL  : {login_url}")
    print()
    print(_bold("Next steps:"))
    print("  1. Open Chrome with the Adopt.ai extension (abcd/elicitation/extension/)")
    print("  2. Enable Login Recording Mode in the extension")
    print("  3. Navigate to the login page and log in normally")
    print("  4. Click 'Stop Recording' in the extension when done")
    print()
    print("  Start the recording session manually if needed:")
    print(f"    PUT {ELICITATION_URL}/login-sessions/{session_id}/start")
    print()
    print("  When finished:")
    print(f"    python cli/elicitation_setup.py profile export {session_id}")
    print()
    return 0


def cmd_profile_list(args: argparse.Namespace) -> int:  # noqa: ARG001
    """List login recording sessions."""
    if not _elic_alive():
        print(_red(f"Elicitation backend not reachable at {ELICITATION_URL}"))
        return 1

    try:
        sessions = _elic_http("GET", "/login-sessions")
        assert isinstance(sessions, list)
    except (RuntimeError, AssertionError) as exc:
        print(_red(f"Failed to list sessions: {exc}"))
        return 1

    if not sessions:
        print(_yellow("No login recording sessions found."))
        return 0

    print(_bold("Login recording sessions:"))
    print()
    for s in sessions:
        sid = s.get("id", "?")[:8]
        app = s.get("app_name") or "?"
        url = s.get("login_url") or "?"
        status = s.get("status") or "?"
        color = _green if status == "stopped" else _yellow if status == "capturing" else str
        print(f"  {_cyan(sid)}  {_bold(app)}  {color(status)}  {url}")

    print()
    return 0


def cmd_profile_export(args: argparse.Namespace) -> int:
    """Analyze session and write bundle files."""
    session_id = args.session_id
    output_dir = (
        Path(args.output_dir) if getattr(args, "output_dir", None) else LOGIN_RECORDINGS_DIR
    )

    if not _elic_alive():
        print(_red(f"Elicitation backend not reachable at {ELICITATION_URL}"))
        return 1

    print(f"Analyzing session {_cyan(session_id)} …", end=" ", flush=True)
    try:
        bundle = _elic_http("POST", f"/login-sessions/{session_id}/analyze")
        assert isinstance(bundle, dict)
        print(_green("✓"))
    except (RuntimeError, AssertionError) as exc:
        print()
        print(_red(f"Analysis failed: {exc}"))
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"tabby-{session_id[:8]}"

    bundle_path = output_dir / f"{prefix}-bundle.json"
    app_path = output_dir / f"{prefix}-application.json"
    profile_path = output_dir / f"{prefix}-service-profile.json"
    review_path = output_dir / f"{prefix}-review.md"

    bundle_path.write_text(json.dumps(bundle, indent=2) + "\n")
    app_path.write_text(json.dumps(bundle.get("application_draft", {}), indent=2) + "\n")
    profile_path.write_text(json.dumps(bundle.get("service_profile_draft", {}), indent=2) + "\n")

    review_items = bundle.get("review_items", [])
    validation = bundle.get("validation", {})
    review_lines = [
        "# Login Profile Review Report",
        "",
        f"Session: `{session_id}`",
        f"Generator valid: {'Yes' if validation.get('generator_valid') else 'No'}",
        "",
    ]
    if validation.get("issues"):
        review_lines += ["## Errors", ""]
        for issue in validation["issues"]:
            review_lines.append(f"- **ERROR**: {issue}")
        review_lines.append("")

    errors = [r for r in review_items if r.get("severity") == "error"]
    warnings = [r for r in review_items if r.get("severity") == "warning"]
    infos = [r for r in review_items if r.get("severity") == "info"]

    if errors:
        review_lines += ["## Errors", ""]
        for r in errors:
            review_lines.append(f"- [{r.get('type', '?')}] {r.get('message', '')}")
        review_lines.append("")
    if warnings:
        review_lines += ["## Warnings", ""]
        for r in warnings:
            review_lines.append(f"- [{r.get('type', '?')}] {r.get('message', '')}")
        review_lines.append("")
    if infos:
        review_lines += ["## Info", ""]
        for r in infos:
            review_lines.append(f"- [{r.get('type', '?')}] {r.get('message', '')}")
        review_lines.append("")

    review_path.write_text("\n".join(review_lines))

    print()
    print(_bold("Exported:"))
    print(f"  Bundle    : {bundle_path}")
    print(f"  App draft : {app_path}")
    print(f"  Profile   : {profile_path}")
    print(f"  Review    : {review_path}")
    print()

    if errors:
        print(_red(f"  {len(errors)} error(s) — fix before provisioning"))
    if warnings:
        print(_yellow(f"  {len(warnings)} warning(s) — review selector confidence"))
    if not errors and not warnings:
        print(_green("  No issues — ready to register"))
    print()

    return 0


def cmd_profile_review(args: argparse.Namespace) -> int:
    """Print review items from a bundle file."""
    bundle_path = Path(args.bundle_path)
    if not bundle_path.exists():
        print(_red(f"Bundle file not found: {bundle_path}"))
        return 1

    try:
        bundle = json.loads(bundle_path.read_text())
    except Exception as exc:
        print(_red(f"Failed to parse bundle: {exc}"))
        return 1

    review_items = bundle.get("review_items", [])
    validation = bundle.get("validation", {})
    recording = bundle.get("recording", {})

    print(_bold("Review Report"))
    print(f"  Session: {recording.get('session_id', '?')}")
    print(f"  Generator valid: {'Yes' if validation.get('generator_valid') else _red('No')}")
    print()

    if validation.get("issues"):
        print(_bold("Generator issues:"))
        for issue in validation["issues"]:
            print(f"  {_red('✗')} {issue}")
        print()

    errors = [r for r in review_items if r.get("severity") == "error"]
    warnings = [r for r in review_items if r.get("severity") == "warning"]
    infos = [r for r in review_items if r.get("severity") == "info"]

    if errors:
        print(_bold("Errors:"))
        for r in errors:
            print(f"  {_red('✗')} [{r.get('type')}] {r.get('message')}")
        print()
    if warnings:
        print(_bold("Warnings:"))
        for r in warnings:
            print(f"  {_yellow('!')} [{r.get('type')}] {r.get('message')}")
        print()
    if infos:
        print(_bold("Info:"))
        for r in infos:
            print(f"  {_cyan('i')} [{r.get('type')}] {r.get('message')}")
        print()

    app_draft = bundle.get("application_draft", {})
    steps = app_draft.get("login_config", {}).get("steps", [])
    if steps:
        print(_bold("Generated login steps:"))
        for i, step in enumerate(steps, 1):
            action = step.get("action", "?")
            sel = step.get("selector", "")
            val = step.get("value", "")
            url = step.get("url", "")
            sensitive = " [sensitive]" if step.get("sensitive") else ""
            if action == "goto":
                print(f"  {i}. goto {url}")
            elif action == "fill":
                print(f"  {i}. fill {sel!r} = {val!r}{sensitive}")
            elif action == "click":
                print(f"  {i}. click {sel!r}")
            elif action in ("wait_for", "wait_for_url"):
                target = sel or step.get("url_pattern", "")
                print(f"  {i}. {action} {target!r}{sensitive}")
            else:
                print(f"  {i}. {action} {sel or url!r}")
        print()

    if not errors and not warnings:
        print(_green("✓ No issues — ready to register"))
    return 0


# ---------------------------------------------------------------------------
# Subcommands: profile register / validate / promote / import
# (require Tabby API to be running)
# ---------------------------------------------------------------------------


def _require_tabby() -> bool:
    if not _tabby_alive():
        print(_red("Tabby API is not running at http://localhost:8080"))
        print("  Start it with: python cli/tabby_setup.py start")
        return False
    return True


def cmd_profile_register(args: argparse.Namespace) -> int:
    """Provision a Tabby Application + STAGING ServiceProfile from a bundle file."""
    if not _require_tabby():
        return 1

    bundle_path = Path(args.bundle_path)
    if not bundle_path.exists():
        print(_red(f"Bundle file not found: {bundle_path}"))
        return 1

    try:
        bundle = json.loads(bundle_path.read_text())
    except Exception as exc:
        print(_red(f"Failed to parse bundle: {exc}"))
        return 1

    validation = bundle.get("validation", {})
    if not validation.get("generator_valid", True):
        print(_red("Bundle has generator errors — fix them before registering"))
        print("  Run: python cli/elicitation_setup.py profile review <bundle>")
        return 1

    app_draft = bundle.get("application_draft", {})
    profile_draft = bundle.get("service_profile_draft", {})
    profile_id = profile_draft.get("profile_id", "")

    if not profile_id:
        print(_red("Bundle is missing service_profile_draft.profile_id"))
        return 1

    admin_token = _get_admin_token()
    if not admin_token:
        return 1

    cache = _load_cache()
    apps = cache.setdefault("apps", {})
    entry = apps.setdefault(profile_id, {})

    app_id: str = entry.get("app_id", "")
    if app_id and not getattr(args, "force", False):
        print(_green(f"✓ Reusing existing Application '{app_id}' for profile '{profile_id}'"))
    else:
        print(f"Creating Application '{profile_id}' …", end=" ", flush=True)
        try:
            # Rewrite http://localhost target_urls to https://localhost for
            # Tabby validation — local test sites use HTTP but Tabby requires HTTPS.
            patched_draft = dict(app_draft)
            patched_urls = [
                u.replace("http://localhost", "https://localhost", 1)
                if u.startswith("http://localhost")
                else u
                for u in (app_draft.get("target_urls") or [])
            ]
            if patched_urls:
                patched_draft = {**app_draft, "target_urls": patched_urls}
            app_resp = _tabby_http("POST", "/apps", patched_draft, token=admin_token)
            assert isinstance(app_resp, dict)
            app_id = app_resp["app_id"]
            print(_green("✓"))
        except (RuntimeError, KeyError, AssertionError) as exc:
            print()
            print(_red(f"Application creation failed: {exc}"))
            return 1

    entry["app_id"] = app_id

    profile_payload = {**profile_draft, "app_id": app_id}
    t = time.localtime()
    profile_payload["version"] = f"{t.tm_year % 100}.{t.tm_mon}.{t.tm_mday}"

    print(f"Creating STAGING ServiceProfile '{profile_id}' …", end=" ", flush=True)
    try:
        prof_resp = _tabby_http("POST", "/admin/profiles", profile_payload, token=admin_token)
        assert isinstance(prof_resp, dict)
        profile_db_id: str = prof_resp["id"]
        print(_green("✓"))
    except (RuntimeError, KeyError, AssertionError) as exc:
        print()
        print(_red(f"ServiceProfile creation failed: {exc}"))
        return 1

    entry["profile_db_id"] = profile_db_id
    entry["login_url"] = app_draft.get("login_config", {}).get("login_url", "")
    entry["login_config"] = app_draft.get("login_config", {})
    entry["credential_ref"] = profile_draft.get("login_config", {}).get(
        "credential_ref", "k8s:secret/no-auth"
    )
    _save_cache(cache)

    bundle["_provisioned"] = {
        "app_id": app_id,
        "profile_db_id": profile_db_id,
        "profile_id": profile_id,
        "version_state": "STAGING",
    }
    bundle_path.write_text(json.dumps(bundle, indent=2) + "\n")

    print()
    print(_green(f"✓ Registered profile '{profile_id}'"))
    print(f"  Application ID       : {_cyan(app_id)}")
    print(f"  ServiceProfile DB ID : {_cyan(profile_db_id)}")
    print("  Version state        : STAGING")
    print()
    print("  Next steps:")
    print(f"    python cli/elicitation_setup.py profile validate {bundle_path}")
    print(f"    python cli/elicitation_setup.py profile promote {profile_db_id}")
    print()
    return 0


def cmd_profile_validate(args: argparse.Namespace) -> int:
    """Scale the application to one session and wait for HEALTHY."""
    if not _require_tabby():
        return 1

    bundle_path = Path(args.bundle_path)
    if not bundle_path.exists():
        print(_red(f"Bundle file not found: {bundle_path}"))
        return 1

    try:
        bundle = json.loads(bundle_path.read_text())
    except Exception as exc:
        print(_red(f"Failed to parse bundle: {exc}"))
        return 1

    provisioned = bundle.get("_provisioned", {})
    app_id = provisioned.get("app_id", "")
    profile_id = provisioned.get("profile_id", "")

    if not app_id:
        if profile_id:
            cache = _load_cache()
            app_id = cache.get("apps", {}).get(profile_id, {}).get("app_id", "")
        if not app_id:
            print(_red("No app_id found in bundle — run `register` first"))
            return 1

    admin_token = _get_admin_token()
    if not admin_token:
        return 1

    payload = _decode_jwt_payload(admin_token)
    tenant_id = payload.get("tenant_id") or payload.get("tenantId", "")

    # In local dev the K8s controller is not running so the scale-sessions
    # endpoint does not exist.  Skip it silently and seed the session directly.
    print("Seeding session record …", end=" ", flush=True)
    seed_script = TABBY_DIR / "scripts" / "batch-a-seed-session.js"
    session_id = None
    if not seed_script.exists():
        print()
        print(_yellow(f"Seed script not found at {seed_script} — skipping"))
    else:
        try:
            seed_env = {**os.environ}
            seed_env.update(_load_env_local())
            seed_out = subprocess.check_output(
                ["node", str(seed_script), app_id, tenant_id],
                cwd=str(TABBY_DIR),
                env=seed_env,
                timeout=30,
            )
            seed_data = json.loads(seed_out.decode())
            session_id = seed_data.get("session", {}).get("id", "")
            print(_green("✓"))
        except Exception as exc:
            print()
            print(_yellow(f"Could not seed session: {exc}"))

    if not session_id:
        try:
            sessions_resp = _tabby_http("GET", "/sessions?limit=200", token=admin_token)
            assert isinstance(sessions_resp, dict)
            all_sessions = sessions_resp.get("data", [])
            app_sessions = [s for s in all_sessions if s.get("app_id") == app_id]
            if app_sessions:
                session_id = app_sessions[0]["id"]
                print(f"  Using existing session: {session_id}")
        except Exception:
            pass

    if not session_id:
        print(_red("Could not determine session ID — cannot poll for health"))
        return 1

    # Write credentials to temp mount so the worker can resolve them via files
    # (env var path is unreliable when pnpm spawns through dash).
    profile_draft = bundle.get("service_profile_draft", {})
    credential_ref = profile_draft.get("login_config", {}).get(
        "credential_ref", "k8s:secret/no-auth"
    )
    secret_name = credential_ref.replace("k8s:secret/", "")
    creds_mount = Path("/tmp/tabby-local-secrets")
    secret_dir = creds_mount / secret_name
    secret_dir.mkdir(parents=True, exist_ok=True)

    app_draft = bundle.get("application_draft", {})
    steps = app_draft.get("login_config", {}).get("steps", [])
    has_login = any(s.get("value") in ("${USERNAME}", "${PASSWORD}") for s in steps)
    if has_login:
        print("  Enter credentials for the login flow:")
        username = input("    Username: ").strip()
        import getpass as _getpass

        password = _getpass.getpass("    Password: ")
        (secret_dir / "username").write_text(username)
        (secret_dir / "password").write_text(password)
    else:
        (secret_dir / "username").write_text("no-auth")
        (secret_dir / "password").write_text("no-auth")

    env = {**os.environ}
    env.update(_load_env_local())
    env["SESSION_ID"] = session_id
    env["APP_ID"] = app_id
    env["TENANT_ID"] = tenant_id
    env["CREDENTIALS_MOUNT_PATH"] = str(creds_mount)
    env["STREAMING_MODE"] = "cdp"

    old_pid = _read_pid(WORKER_PID_FILE)
    if old_pid:
        try:
            os.kill(old_pid, signal.SIGTERM)
            for _ in range(10):
                time.sleep(0.3)
                try:
                    os.kill(old_pid, 0)
                except ProcessLookupError:
                    break
        except ProcessLookupError:
            pass
        _clear_pid(WORKER_PID_FILE)
    # Free the port even if PID file was stale
    try:
        subprocess.run(["fuser", "-k", "8091/tcp"], capture_output=True)
        time.sleep(0.5)
    except FileNotFoundError:
        pass

    print(f"Starting worker for session {_cyan(session_id)} …", end=" ", flush=True)
    worker_log_fh = open(WORKER_LOG_FILE, "a")  # noqa: SIM115
    proc = subprocess.Popen(
        ["pnpm", "--filter", "@browser-hitl/worker", "start"],
        cwd=str(TABBY_DIR),
        env=env,
        stdout=worker_log_fh,
        stderr=worker_log_fh,
        start_new_session=True,
    )
    WORKER_PID_FILE.write_text(str(proc.pid))
    print(_green(f"✓ (PID {proc.pid})"))
    print()
    print("  Polling for HEALTHY (up to 5 min)", end="", flush=True)

    final_state = ""
    final_health = ""
    for _ in range(60):
        time.sleep(5)
        print(".", end="", flush=True)
        try:
            resp = _tabby_http("GET", f"/sessions/{session_id}", token=admin_token)
            assert isinstance(resp, dict)
            final_state = resp.get("state", "")
            final_health = resp.get("health_result_type", "")
            if final_state == "HEALTHY" or final_health == "PASS":
                break
            if final_state in ("FAILED", "TERMINATED") or final_health == "AUTH_FAIL":
                print()
                print(_red(f"Session failed (state={final_state}, health={final_health})"))
                print(f"  Worker logs: {WORKER_LOG_FILE}")
                _write_validation_report(
                    bundle_path, session_id, "FAILED", final_state, final_health
                )
                return 1
        except (RuntimeError, AssertionError):
            pass
    else:
        print()
        print(_red("Session did not reach HEALTHY within 5 minutes"))
        print(f"  Worker logs: {WORKER_LOG_FILE}")
        _write_validation_report(bundle_path, session_id, "TIMEOUT", final_state, final_health)
        return 1

    print()

    if final_state != "HEALTHY":
        sql = f"UPDATE sessions SET state='HEALTHY' WHERE id='{session_id}'"
        try:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    "browser_hitl",
                    "-d",
                    "browser_hitl",
                    "-c",
                    sql,
                ],
                cwd=str(TABBY_DIR),
                check=True,
                capture_output=True,
            )
        except Exception:
            pass

    _write_validation_report(bundle_path, session_id, "HEALTHY", final_state, final_health)

    print(_green(f"✓ Session HEALTHY — profile '{profile_id}' validated"))
    print()
    print("  Profile remains STAGING — promote when ready:")
    profile_db_id = provisioned.get("profile_db_id", "")
    if profile_db_id:
        print(f"    python cli/elicitation_setup.py profile promote {profile_db_id}")
    print()
    return 0


def _write_validation_report(
    bundle_path: Path, session_id: str, outcome: str, state: str, health: str
) -> None:
    import datetime

    report_path = bundle_path.with_suffix(".validation.json")
    report = {
        "session_id": session_id,
        "outcome": outcome,
        "final_state": state,
        "final_health_result": health,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"  Validation report: {report_path}")


def cmd_profile_promote(args: argparse.Namespace) -> int:
    """Promote a ServiceProfile STAGING → CANARY → ACTIVE."""
    if not _require_tabby():
        return 1

    profile_db_id = args.profile_db_id

    admin_token = _get_admin_token()
    if not admin_token:
        return 1

    print(f"Promoting profile {_cyan(profile_db_id)}: STAGING → CANARY …", end=" ", flush=True)
    try:
        _tabby_http("POST", f"/admin/profiles/{profile_db_id}/promote", token=admin_token)
        print(_green("✓"))
    except RuntimeError as exc:
        print()
        print(_red(f"Promotion failed: {exc}"))
        return 1

    print("Bypassing canary gate …", end=" ", flush=True)
    if not _bypass_canary_gate(profile_db_id):
        return 1
    print(_green("✓"))

    print(f"Promoting profile {_cyan(profile_db_id)}: CANARY → ACTIVE …", end=" ", flush=True)
    try:
        _tabby_http("POST", f"/admin/profiles/{profile_db_id}/promote", token=admin_token)
        print(_green("✓"))
    except RuntimeError as exc:
        print()
        print(_red(f"Promotion to ACTIVE failed: {exc}"))
        return 1

    print()
    print(_green(f"✓ Profile '{profile_db_id}' is now ACTIVE"))
    print()
    return 0


def cmd_profile_import(args: argparse.Namespace) -> int:
    """Convenience wrapper: export + review + register [+ validate]."""
    session_id = args.session_id
    output_dir = (
        Path(args.output_dir) if getattr(args, "output_dir", None) else LOGIN_RECORDINGS_DIR
    )

    export_args = argparse.Namespace(session_id=session_id, output_dir=str(output_dir))
    rc = cmd_profile_export(export_args)
    if rc != 0:
        return rc

    prefix = f"tabby-{session_id[:8]}"
    bundle_path = output_dir / f"{prefix}-bundle.json"

    review_args = argparse.Namespace(bundle_path=str(bundle_path))
    cmd_profile_review(review_args)

    register_args = argparse.Namespace(bundle_path=str(bundle_path), force=False)
    rc = cmd_profile_register(register_args)
    if rc != 0:
        return rc

    if getattr(args, "validate", False):
        validate_args = argparse.Namespace(bundle_path=str(bundle_path))
        rc = cmd_profile_validate(validate_args)
        if rc != 0:
            return rc

    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python cli/elicitation_setup.py",
        description="Elicitation backend lifecycle and login recording workflow.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help=f"Start the elicitation backend (port {ELIC_PORT})")
    sub.add_parser("stop", help="Stop the elicitation backend")
    sub.add_parser("status", help="Show backend status and port assignments")

    profile_p = sub.add_parser(
        "profile",
        help="Login recording: record → export → review → register → validate → promote",
    )
    profile_sub = profile_p.add_subparsers(dest="profile_action", required=True)

    record_p = profile_sub.add_parser("record", help="Create a login recording session")
    record_p.add_argument("app_name", help="Human-readable name for the target app")
    record_p.add_argument("login_url", help="URL of the login page to record")

    profile_sub.add_parser("list", help="List login recording sessions")

    export_p = profile_sub.add_parser("export", help="Analyze session and write bundle files")
    export_p.add_argument("session_id", help="Login recording session ID")
    export_p.add_argument(
        "--output-dir",
        dest="output_dir",
        default=".",
        metavar="DIR",
        help="Directory to write bundle files into (default: workspaces/login-recordings/)",
    )

    review_p = profile_sub.add_parser("review", help="Print review items from a bundle file")
    review_p.add_argument("bundle_path", help="Path to bundle JSON file")

    register_p = profile_sub.add_parser(
        "register",
        help="Provision Application + STAGING ServiceProfile (needs Tabby)",
    )
    register_p.add_argument("bundle_path", help="Path to bundle JSON file")
    register_p.add_argument(
        "--force",
        action="store_true",
        help="Force recreate Application even if one exists in cache",
    )

    validate_p = profile_sub.add_parser(
        "validate",
        help="Scale app to 1 session and wait for HEALTHY (needs Tabby)",
    )
    validate_p.add_argument("bundle_path", help="Path to bundle JSON file")

    import_p = profile_sub.add_parser(
        "import",
        help="Convenience: export + review + register [+ validate]",
    )
    import_p.add_argument("session_id", help="Login recording session ID")
    import_p.add_argument(
        "--output-dir",
        dest="output_dir",
        default=".",
        metavar="DIR",
        help="Directory to write bundle files into (default: workspaces/login-recordings/)",
    )
    import_p.add_argument(
        "--validate",
        action="store_true",
        help="Also run validate after register",
    )

    promote_p = profile_sub.add_parser(
        "promote",
        help="Promote a ServiceProfile STAGING → CANARY → ACTIVE (needs Tabby)",
    )
    promote_p.add_argument("profile_db_id", help="ServiceProfile database ID (UUID)")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    for attr, default in [
        ("output_dir", "."),
        ("validate", False),
        ("force", False),
    ]:
        if not hasattr(args, attr):
            setattr(args, attr, default)

    if args.command == "start":
        return cmd_start(args)
    if args.command == "stop":
        return cmd_stop(args)
    if args.command == "status":
        return cmd_status(args)

    if args.command == "profile":
        if args.profile_action == "record":
            return cmd_profile_record(args)
        if args.profile_action == "list":
            return cmd_profile_list(args)
        if args.profile_action == "export":
            return cmd_profile_export(args)
        if args.profile_action == "review":
            return cmd_profile_review(args)
        if args.profile_action == "register":
            return cmd_profile_register(args)
        if args.profile_action == "validate":
            return cmd_profile_validate(args)
        if args.profile_action == "import":
            return cmd_profile_import(args)
        if args.profile_action == "promote":
            return cmd_profile_promote(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
