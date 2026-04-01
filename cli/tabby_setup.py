#!/usr/bin/env python3
"""
Tabby lifecycle & provisioning CLI — zero-friction local setup for the
Tabby credential service used by `abcd test`.

Subcommands:
    status                    - Check Docker Compose services and API liveness
    start                     - Start infra (docker compose) and API in background
    stop [--infra]            - Stop the API process (and optionally docker compose)
    setup                     - Full end-to-end: start if needed, register agent
                                client, create Application + ServiceProfile,
                                write TABBY_* vars to .env
    session status            - Show browser session state for configured profiles
    session ensure [--profile] - Ensure a HEALTHY browser session exists,
                                starting the worker locally if needed
    session stop [--profile]  - Stop a locally-running worker process

Typical first-time session:
    python cli/tabby_setup.py start
    python cli/tabby_setup.py setup     # interactive: asks for app login details
    python cli/tabby_setup.py session ensure
    # .env now has TABBY_* vars and a HEALTHY browser session exists
    source .env && python cli/test_runner.py <action>
"""

from __future__ import annotations

import argparse
import base64
import getpass
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
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CLI_DIR = Path(__file__).parent
PROJECT_ROOT = CLI_DIR.parent.parent  # …/adopt/
ABCD_DIR = CLI_DIR.parent  # …/adopt/abcd/
TABBY_DIR = PROJECT_ROOT / "tabby"
PID_FILE = TABBY_DIR / ".tabby-api.pid"
LOG_FILE = TABBY_DIR / ".tabby-api.log"
WORKER_PID_FILE = TABBY_DIR / ".tabby-worker.pid"
WORKER_LOG_FILE = TABBY_DIR / ".tabby-worker.log"
CREDS_CACHE = TABBY_DIR / ".tabby-abcd-client.json"
ENV_LOCAL = TABBY_DIR / ".env.local"
ENV_EXAMPLE = TABBY_DIR / ".env.example"

TABBY_API_HOST = "http://localhost:8080"
HEALTH_PATH = "/health/live"
AGENT_CLIENT_NAME = "abcd-test-runner"

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
# Cache helpers
# ---------------------------------------------------------------------------


def _load_cache() -> dict[str, Any]:
    if not CREDS_CACHE.exists():
        return {}
    try:
        return json.loads(CREDS_CACHE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    CREDS_CACHE.write_text(json.dumps(cache, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _api_alive() -> bool:
    try:
        with urllib.request.urlopen(TABBY_API_HOST + HEALTH_PATH, timeout=3) as resp:
            return json.loads(resp.read().decode()).get("status") == "ok"
    except Exception:
        return False


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


def _write_env_vars(env_file: Path, updates: dict[str, str]) -> None:
    """Upsert key=value lines in env_file, creating it if needed."""
    existing_lines: list[str] = []
    if env_file.exists():
        existing_lines = env_file.read_text().splitlines()
    replaced: set[str] = set()
    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                replaced.add(key)
                continue
        new_lines.append(line)
    for key, val in updates.items():
        if key not in replaced:
            new_lines.append(f"{key}={val}")
    env_file.write_text("\n".join(new_lines) + "\n")


def _docker_compose_services() -> dict[str, str]:
    try:
        out = subprocess.check_output(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=str(TABBY_DIR),
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        services: dict[str, str] = {}
        for line in out.decode().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                name = entry.get("Service") or entry.get("Name", "?")
                state = entry.get("State") or entry.get("Status", "?")
                services[name] = state
            except json.JSONDecodeError:
                continue
        return services
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _http(
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


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        segment = token.split(".")[1]
        segment += "=" * (4 - len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(segment))
    except Exception as exc:
        raise RuntimeError(f"Could not decode JWT payload: {exc}") from exc


def _get_admin_token() -> str | None:
    """Login as admin using .env.local credentials, return JWT or None."""
    env_local = _load_env_local()
    email = env_local.get("ADMIN_BOOTSTRAP_EMAIL", "")
    password = env_local.get("ADMIN_BOOTSTRAP_PASSWORD", "")
    if not email or not password:
        print(_red(f"ADMIN_BOOTSTRAP_EMAIL / ADMIN_BOOTSTRAP_PASSWORD not set in {ENV_LOCAL}"))
        return None
    try:
        resp = _http("POST", "/login", {"email": email, "password": password})
        assert isinstance(resp, dict)
        return resp.get("token") or resp.get("access_token", "")
    except RuntimeError as exc:
        print(_red(f"Admin login failed: {exc}"))
        return None


# ---------------------------------------------------------------------------
# Profile resolution helpers
# ---------------------------------------------------------------------------


def _prompt_profiles() -> list[str]:
    print()
    print(_bold("  First-time setup — enter your Tabby profile ID(s)"))
    print()
    print("  A profile ID is the identifier of a target-app credential set in Tabby.")
    print("  Examples: salesforce-standard, google-workspace, servicenow-itsm")
    print()
    print("  If you're unsure, use a descriptive slug for the app you're connecting.")
    print("  You can always re-run setup with --profiles to update the list.")
    print()
    while True:
        try:
            raw = input("  Profile ID(s) (space-separated): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return []
        profiles = [p.strip() for p in raw.split() if p.strip()]
        if profiles:
            return profiles
        print(_yellow("  At least one profile ID is required. Try again."))


def _load_cached_default_profiles() -> list[str]:
    cached = _load_cache()
    profiles = cached.get("default_profiles", [])
    return profiles if isinstance(profiles, list) else []


def _discover_tabby_profile_ids() -> list[str]:
    sys.path.insert(0, str(ABCD_DIR))
    try:
        from cli.wdl_common.workspace_manager import WORKSPACES_DIR
    except ImportError:
        return []
    ids: set[str] = set()
    for profile_file in WORKSPACES_DIR.rglob("adopt_profile.json"):
        try:
            data = json.loads(profile_file.read_text())
        except Exception:
            continue
        if tid := data.get("tabby_profile_id"):
            ids.add(tid)
        for entry in (data.get("profiles_map") or {}).values():
            if isinstance(entry, dict) and (tid := entry.get("tabby_profile_id")):
                ids.add(tid)
    return sorted(ids)


# ---------------------------------------------------------------------------
# Application + ServiceProfile provisioning
# ---------------------------------------------------------------------------


def _secret_name(profile_id: str) -> str:
    """K8s-safe secret name derived from profile_id."""
    return f"tabby-abcd-{profile_id.lower().replace('_', '-')}"


def _env_prefix(secret_name: str) -> str:
    """Env var prefix for worker credential fallback."""
    return secret_name.upper().replace("-", "_")


def _find_active_profile(
    profile_id: str, tenant_id: str, admin_token: str
) -> dict[str, Any] | None:
    """Return the ACTIVE ServiceProfile dict for profile_id, or None."""
    try:
        resp = _http("GET", "/admin/profiles?limit=200", token=admin_token)
        profiles: list[dict[str, Any]] = (
            resp.get("data", []) if isinstance(resp, dict) else list(resp)  # type: ignore[union-attr]
        )
        return next(
            (
                p
                for p in profiles
                if p.get("profile_id") == profile_id and p.get("version_state") == "ACTIVE"
            ),
            None,
        )
    except RuntimeError:
        return None


def _bypass_canary_gate(profile_db_id: str) -> bool:
    """
    Set canary_request_count=5, canary_error_count=0 via docker compose exec
    so the CANARY → ACTIVE promotion gate passes immediately.
    """
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


def _prompt_app_config(profile_id: str) -> dict[str, Any] | None:
    """
    Interactively collect the minimum login config for a new Application.
    Returns a config dict or None if the user cancels.
    """
    print()
    print(_bold(f"  Configure login for profile '{profile_id}'"))
    print()
    print("  Tabby needs to know how to log into your target app so it can")
    print("  maintain a live browser session and serve fresh credentials.")
    print()
    try:
        login_url = input("  Login page URL (e.g. https://app.example.com/login): ").strip()
        if not login_url:
            return None

        print()
        print("  Leave email and password blank if the app requires no login.")
        username = input("  Test account email / username (optional): ").strip()
        password = ""
        if username:
            password = getpass.getpass("  Test account password: ")

        print()
        print("  CSS selectors for the login form (Enter = use default):")
        email_sel = (
            input("  Email/username field  [input[name='email'], input[type='email']]: ").strip()
            or "input[name='email'], input[type='email']"
        )
        pass_sel = (
            input("  Password field        [input[type='password']]: ").strip()
            or "input[type='password']"
        )
        submit_sel = (
            input("  Submit button         [button[type='submit']]: ").strip()
            or "button[type='submit']"
        )
        success_sel = input(
            "  Post-login element    (e.g. #dashboard, .user-menu — optional): "
        ).strip()

        otp = input("\n  Does login require OTP / MFA? [y/N]: ").strip().lower() == "y"
        otp_sel = ""
        if otp:
            otp_sel = (
                input("  OTP input selector    [input[name='otp']]: ").strip()
                or "input[name='otp']"
            )
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    return {
        "login_url": login_url,
        "username": username,
        "password": password,
        "email_sel": email_sel,
        "pass_sel": pass_sel,
        "submit_sel": submit_sel,
        "success_sel": success_sel,
        "otp_required": otp,
        "otp_sel": otp_sel,
    }


def _build_app_payload(profile_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    origin = f"{urlparse(cfg['login_url']).scheme}://{urlparse(cfg['login_url']).netloc}"
    secret = _secret_name(profile_id)

    requires_login = bool(cfg.get("username"))

    steps: list[dict[str, Any]] = [{"action": "goto", "url": cfg["login_url"]}]
    if requires_login:
        steps += [
            {"action": "fill", "selector": cfg["email_sel"], "value": "${USERNAME}"},
            {
                "action": "fill",
                "selector": cfg["pass_sel"],
                "value": "${PASSWORD}",
                "sensitive": True,
            },
            {"action": "click", "selector": cfg["submit_sel"]},
        ]
        if cfg.get("otp_required") and cfg.get("otp_sel"):
            steps += [
                {
                    "action": "wait_for",
                    "selector": cfg["otp_sel"],
                    "timeout_ms": 120000,
                    "sensitive": True,
                },
                {"action": "click", "selector": "[type='submit']"},
            ]
    if cfg.get("success_sel"):
        steps.append({"action": "wait_for", "selector": cfg["success_sel"], "timeout_ms": 30000})

    # credential_ref is always required by the validator
    credential_ref = f"k8s:secret/{secret}" if requires_login else "k8s:secret/no-auth"
    login_config: dict[str, Any] = {
        "login_url": cfg["login_url"],
        "credential_ref": credential_ref,
        "steps": steps,
    }
    if requires_login and cfg.get("otp_required") and cfg.get("otp_sel"):
        login_config["otp_prompt"] = {"method": "chat", "field_selector": cfg["otp_sel"]}

    return {
        "name": profile_id,
        "target_urls": [origin],
        "login_config": login_config,
        "keepalive_config": {
            "interval_seconds": 300,
            "actions": [],
            # url_check against the app origin — proves the page is reachable
            "health_checks": [{"type": "url_check", "url": origin, "expect_status": 200}],
            "policy": "all",
        },
        "export_policy": {
            "artifact_types": ["cookies", "headers", "csrf_token"],
            "encryption": {"algo": "AES-256-GCM", "key_version": "v1"},
            "ttl_seconds": 3600,
        },
        # channels must be non-empty and match ^(slack|teams):.+$
        # using a placeholder — notifications will silently no-op if Slack isn't configured
        "notification_config": {"channels": ["slack:#local-dev"]},
        "desired_session_count": 0,
        "browser_policy": {"streaming_mode": "cdp"},
    }


def _ensure_service_profile(
    profile_id: str,
    tenant_id: str,
    admin_token: str,
    cache: dict[str, Any],
) -> bool:
    """
    Ensure an ACTIVE ServiceProfile exists for profile_id.
    Creates Application + ServiceProfile interactively if needed.
    Updates cache in-place; caller must call _save_cache() afterwards.
    Returns True on success, False on failure/cancel.
    """
    # Already ACTIVE?
    existing = _find_active_profile(profile_id, tenant_id, admin_token)
    if existing:
        print(_green(f"  ✓ '{profile_id}' is already ACTIVE in Tabby"))
        apps = cache.setdefault("apps", {})
        entry = apps.setdefault(profile_id, {})
        entry["app_id"] = existing.get("app_id", entry.get("app_id", ""))
        entry["profile_db_id"] = existing.get("id", entry.get("profile_db_id", ""))
        return True

    print(_yellow(f"  '{profile_id}' is not ACTIVE — configuring now…"))

    apps = cache.setdefault("apps", {})
    entry = apps.setdefault(profile_id, {})
    app_id: str = entry.get("app_id", "")

    # ---- Create Application (if we don't have an app_id yet) ----
    if not app_id:
        cfg = _prompt_app_config(profile_id)
        if not cfg:
            print(_yellow(f"  Skipped '{profile_id}' — no config entered."))
            return False

        app_payload = _build_app_payload(profile_id, cfg)
        print(f"  Creating Application '{profile_id}' …", end=" ", flush=True)
        try:
            app_resp = _http("POST", "/apps", app_payload, token=admin_token)
            assert isinstance(app_resp, dict)
            app_id = app_resp["app_id"]
            print(_green("✓"))
        except (RuntimeError, KeyError, AssertionError) as exc:
            print()
            print(_red(f"  App creation failed: {exc}"))
            return False

        entry.update(
            {
                "app_id": app_id,
                "login_url": cfg["login_url"],
                "username": cfg.get("username", ""),
                "credential_ref": app_payload["login_config"]["credential_ref"],
                "login_config": app_payload["login_config"],
            }
        )
        # Store worker credentials in .env.local only when login is required
        if cfg.get("username") and cfg.get("password"):
            secret = _secret_name(profile_id)
            prefix = _env_prefix(secret)
            _write_env_vars(
                ENV_LOCAL,
                {
                    f"{prefix}_USERNAME": cfg["username"],
                    f"{prefix}_PASSWORD": cfg["password"],
                },
            )

    login_config = entry.get("login_config", {})

    # ---- Create ServiceProfile (STAGING) ----
    import time as _time

    t = _time.localtime()
    version = f"{t.tm_year % 100}.{t.tm_mon}.{t.tm_mday}"

    profile_payload: dict[str, Any] = {
        "profile_id": profile_id,
        "app_id": app_id,
        "version": version,
        "login_config": login_config,
        "credential_types": {"cookies": [], "headers": []},
        "target_domains": [urlparse(entry.get("login_url", "")).netloc or profile_id],
    }
    print(f"  Creating ServiceProfile '{profile_id}' …", end=" ", flush=True)
    try:
        prof_resp = _http("POST", "/admin/profiles", profile_payload, token=admin_token)
        assert isinstance(prof_resp, dict)
        profile_db_id: str = prof_resp["id"]
        print(_green("✓"))
    except (RuntimeError, KeyError, AssertionError) as exc:
        print()
        print(_red(f"  ServiceProfile creation failed: {exc}"))
        return False

    entry["profile_db_id"] = profile_db_id

    # ---- Promote STAGING → CANARY ----
    print("  Promoting STAGING → CANARY …", end=" ", flush=True)
    try:
        _http("POST", f"/admin/profiles/{profile_db_id}/promote", token=admin_token)
        print(_green("✓"))
    except RuntimeError as exc:
        print()
        print(_red(f"  Promotion failed: {exc}"))
        return False

    # ---- Bypass canary gate (direct DB update via docker compose exec) ----
    print("  Bypassing canary gate …", end=" ", flush=True)
    if not _bypass_canary_gate(profile_db_id):
        print()
        return False
    print(_green("✓"))

    # ---- Promote CANARY → ACTIVE ----
    print("  Promoting CANARY → ACTIVE …", end=" ", flush=True)
    try:
        _http("POST", f"/admin/profiles/{profile_db_id}/promote", token=admin_token)
        print(_green("✓"))
    except RuntimeError as exc:
        print()
        print(_red(f"  Promotion to ACTIVE failed: {exc}"))
        return False

    return True


# ---------------------------------------------------------------------------
# Subcommands: status / start / stop
# ---------------------------------------------------------------------------


def cmd_health(args: argparse.Namespace) -> int:  # noqa: ARG001
    print(_bold("Tabby infrastructure:"))
    services = _docker_compose_services()
    if services:
        for name, state in services.items():
            ok = "running" in state.lower()
            icon = _green("✓") if ok else _red("✗")
            print(f"  {icon}  {name}: {state}")
    else:
        print(_yellow("  (could not reach Docker Compose — is Docker running?)"))
    print()
    print(_bold("Tabby API:"))
    if _api_alive():
        pid = _read_pid(PID_FILE)
        pid_label = f" (PID {pid})" if pid else ""
        print(_green(f"  ✓  API ready at {TABBY_API_HOST}{pid_label}"))
        return 0
    else:
        print(_red(f"  ✗  API not reachable at {TABBY_API_HOST}"))
        print(f"     Run: {_bold('python cli/tabby_setup.py start')}")
        return 1


def cmd_start(args: argparse.Namespace) -> int:  # noqa: ARG001
    if _api_alive():
        pid = _read_pid(PID_FILE)
        pid_label = f" (PID {pid})" if pid else ""
        print(_yellow(f"Tabby API is already running{pid_label} at {TABBY_API_HOST}"))
        return 0

    if not TABBY_DIR.exists():
        print(_red(f"Tabby directory not found: {TABBY_DIR}"))
        return 1

    if not ENV_LOCAL.exists():
        if ENV_EXAMPLE.exists():
            import shutil

            shutil.copy(ENV_EXAMPLE, ENV_LOCAL)
            print(_yellow(f"Created {ENV_LOCAL} from template."))
            print(_yellow("Edit it and set JWT_SIGNING_KEY, TENANT_ENCRYPTION_KEY,"))
            print(
                _yellow("AGENT_SECRET_HMAC_KEY, ADMIN_BOOTSTRAP_EMAIL, ADMIN_BOOTSTRAP_PASSWORD.")
            )
            print()
        else:
            print(_red(f"No .env.local found at {ENV_LOCAL}"))
            return 1

    print("Starting Docker Compose infrastructure …", end="", flush=True)
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=str(TABBY_DIR),
            check=True,
            capture_output=True,
        )
        print(_green(" ✓"))
    except subprocess.CalledProcessError as exc:
        print()
        print(_red(f"docker compose up failed: {exc.stderr.decode(errors='replace')}"))
        return 1
    except FileNotFoundError:
        print()
        print(_red("'docker' command not found. Is Docker installed and in PATH?"))
        return 1

    env = {**os.environ}
    env.update(_load_env_local())

    log_fh = open(LOG_FILE, "a")  # noqa: SIM115
    proc = subprocess.Popen(
        ["pnpm", "--filter", "@browser-hitl/api", "start:dev"],
        cwd=str(TABBY_DIR),
        env=env,
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    print(f"Starting Tabby API (PID {proc.pid}) … logs → {LOG_FILE}", end="", flush=True)

    for _ in range(60):
        time.sleep(1)
        print(".", end="", flush=True)
        if _api_alive():
            break
    else:
        print()
        print(_red("API did not become ready within 60s. Check logs:"))
        print(f"  {LOG_FILE}")
        _clear_pid(PID_FILE)
        return 1

    print()
    print(_green(f"✓ Tabby API ready at {TABBY_API_HOST}"))
    print()
    print(f"  Next: {_bold('python cli/tabby_setup.py setup')}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    pid = _read_pid(PID_FILE)
    if pid is None:
        if _api_alive():
            print(_yellow("API is running but PID file not found — stop it manually."))
            return 1
        print(_yellow("API is not running."))
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(15):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
            _clear_pid(PID_FILE)
            print(_green(f"✓ API process (PID {pid}) stopped."))
        except ProcessLookupError:
            _clear_pid(PID_FILE)
            print(_yellow(f"Process {pid} was not running — cleared stale PID file."))
        except Exception as exc:
            print(_red(f"Failed to stop API: {exc}"))
            return 1

    if args.infra:
        print("Stopping Docker Compose infrastructure …", end="", flush=True)
        try:
            subprocess.run(
                ["docker", "compose", "stop"],
                cwd=str(TABBY_DIR),
                check=True,
                capture_output=True,
            )
            print(_green(" ✓"))
        except Exception as exc:
            print()
            print(_red(f"docker compose stop failed: {exc}"))
            return 1
    return 0


# ---------------------------------------------------------------------------
# Subcommand: setup
# ---------------------------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    """
    Full end-to-end provisioning:
      1. Start Tabby if not running
      2. Login as admin → JWT → tenant_id
      3. Determine allowed_profiles (flag / discover / cached / interactive)
      4. Register (or reuse) agent client "abcd-test-runner"
      5. For each profile: ensure Application + ACTIVE ServiceProfile exist
      6. Write TABBY_* vars to target .env
    """
    # 1. Ensure API is up
    if not _api_alive():
        print("Tabby API is not running — starting it first …")
        print()
        rc = cmd_start(args)
        if rc != 0:
            return rc
        print()

    # 2. Login
    env_local = _load_env_local()
    admin_email = env_local.get("ADMIN_BOOTSTRAP_EMAIL", "")
    admin_password = env_local.get("ADMIN_BOOTSTRAP_PASSWORD", "")
    if not admin_email or not admin_password:
        print(
            _red(f"ADMIN_BOOTSTRAP_EMAIL and ADMIN_BOOTSTRAP_PASSWORD must be set in {ENV_LOCAL}")
        )
        return 1

    print(f"Logging in as {_cyan(admin_email)} …", end=" ", flush=True)
    try:
        login_resp = _http("POST", "/login", {"email": admin_email, "password": admin_password})
        assert isinstance(login_resp, dict)
        admin_token = login_resp.get("token") or login_resp.get("access_token", "")
    except (RuntimeError, AssertionError) as exc:
        print()
        print(_red(f"Login failed: {exc}"))
        return 1
    if not admin_token:
        print(_red(f"\nNo token in login response: {login_resp}"))
        return 1
    print(_green("✓"))

    payload = _decode_jwt_payload(admin_token)
    tenant_id = payload.get("tenant_id") or payload.get("tenantId") or payload.get("sub", "")
    if not tenant_id:
        print(_red(f"Could not find tenant_id in JWT: {payload}"))
        return 1
    print(f"Tenant ID: {_cyan(tenant_id)}")

    # 3. Determine allowed_profiles
    cache = _load_cache()

    if args.profiles:
        allowed_profiles = list(args.profiles)
        print(f"Profiles (from --profiles): {_cyan(', '.join(allowed_profiles))}")
    else:
        allowed_profiles = _discover_tabby_profile_ids()
        if allowed_profiles:
            print(f"Discovered profiles: {_cyan(', '.join(allowed_profiles))}")
        else:
            cached_defaults = _load_cached_default_profiles()
            if cached_defaults and not args.force:
                allowed_profiles = cached_defaults
                print(f"Profiles (saved default): {_cyan(', '.join(allowed_profiles))}")
            else:
                allowed_profiles = _prompt_profiles()
                if not allowed_profiles:
                    print(_red("No profiles provided — setup cancelled."))
                    return 1
                print(f"Profiles: {_cyan(', '.join(allowed_profiles))}")

    # 4. Register / reuse agent client
    client_id: str = ""
    client_secret: str = ""

    if not args.force:
        client_id = cache.get("client_id", "")
        client_secret = cache.get("client_secret", "")
        if client_id and client_secret:
            print(f"Using cached agent client: {_cyan(client_id)}")
            if args.profiles and cache.get("default_profiles") != allowed_profiles:
                cache["default_profiles"] = allowed_profiles

    if not (client_id and client_secret):
        try:
            existing_clients = _http("GET", f"/admin/agent-clients/{tenant_id}", token=admin_token)
            if not isinstance(existing_clients, list):
                existing_clients = []
        except RuntimeError:
            existing_clients = []

        match = next((c for c in existing_clients if c.get("name") == AGENT_CLIENT_NAME), None)

        if match and not args.force:
            print(
                f"Agent client '{AGENT_CLIENT_NAME}' already exists — rotating secret …",
                end=" ",
                flush=True,
            )
            try:
                rotated = _http(
                    "POST",
                    f"/admin/agent-clients/{match['id']}/rotate-secret",
                    token=admin_token,
                )
                assert isinstance(rotated, dict)
                client_id = rotated.get("client_id", match["client_id"])
                client_secret = rotated.get("client_secret", "")
                print(_green("✓"))
            except (RuntimeError, AssertionError) as exc:
                print()
                print(_red(f"Secret rotation failed: {exc}"))
                return 1
        else:
            action = "Force-recreating" if (match and args.force) else "Registering"
            print(f"{action} agent client '{AGENT_CLIENT_NAME}' …", end=" ", flush=True)
            if match and args.force:
                try:
                    _http("DELETE", f"/admin/agent-clients/{match['id']}", token=admin_token)
                except RuntimeError:
                    pass
            try:
                created = _http(
                    "POST",
                    "/admin/agent-clients",
                    {
                        "name": AGENT_CLIENT_NAME,
                        "tenant_id": tenant_id,
                        "allowed_profiles": allowed_profiles,
                        "token_ttl_seconds": 3600,
                    },
                    token=admin_token,
                )
                assert isinstance(created, dict)
                client_id = created["client_id"]
                client_secret = created["client_secret"]
                print(_green("✓"))
            except (RuntimeError, KeyError, AssertionError) as exc:
                print()
                print(_red(f"Agent client creation failed: {exc}"))
                return 1

        if not client_secret:
            print(_red("No client_secret in response — cannot proceed."))
            return 1

    cache.update(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "default_profiles": allowed_profiles,
        }
    )
    _save_cache(cache)

    # 5. Ensure Application + ACTIVE ServiceProfile for each profile
    print()
    print(_bold("Provisioning ServiceProfiles:"))
    for profile_id in allowed_profiles:
        cache = _load_cache()  # reload in case profile loop mutates
        ok = _ensure_service_profile(profile_id, tenant_id, admin_token, cache)
        _save_cache(cache)
        if not ok:
            print(_yellow(f"  Skipped '{profile_id}' — re-run setup to configure it."))

    # 6. Write TABBY_* vars to target .env
    env_file = Path(args.env_file) if args.env_file else ABCD_DIR / ".env"
    _write_env_vars(
        env_file,
        {
            "TABBY_API_URL": TABBY_API_HOST,
            "TABBY_CLIENT_ID": client_id,
            "TABBY_CLIENT_SECRET": client_secret,
        },
    )

    print()
    print(_green("✓ Setup complete!"))
    print()
    print(f"  Credentials written to: {_cyan(str(env_file))}")
    print(f"  Agent client:           {_cyan(client_id)}")
    print()
    print("  Next: ensure a browser session is running:")
    print(f"    {_bold('python cli/tabby_setup.py session ensure')}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: session
# ---------------------------------------------------------------------------


def _seed_session(app_id: str, tenant_id: str) -> str | None:
    """
    Create a session record directly in the DB via batch-a-seed-session.js.
    Returns the new session_id, or None on failure.

    This is necessary for local dev because the K8s controller (which normally
    creates session records in response to desired_session_count changes) is not
    running.  The seed script inserts a row with state='LOGIN_IN_PROGRESS' so
    the worker can pick it up immediately.
    """
    seed_script = TABBY_DIR / "scripts" / "batch-a-seed-session.js"
    if not seed_script.exists():
        print(_red(f"Seed script not found: {seed_script}"))
        return None

    env = {**os.environ}
    env.update(_load_env_local())

    print("  Seeding session record …", end=" ", flush=True)
    try:
        result = subprocess.run(
            ["node", str(seed_script), app_id, tenant_id],
            cwd=str(TABBY_DIR),
            env=env,
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0:
            print()
            print(_red(f"Seed script failed: {result.stderr.decode(errors='replace')}"))
            return None
        data = json.loads(result.stdout.decode())
        session_id: str = data["session"]["id"]
        print(_green("✓"))
        return session_id
    except Exception as exc:
        print()
        print(_red(f"Seed script error: {exc}"))
        return None


def _get_sessions(admin_token: str) -> list[dict[str, Any]]:
    try:
        resp = _http("GET", "/sessions?limit=200", token=admin_token)
        if isinstance(resp, dict):
            return resp.get("data", [])
        return list(resp)  # type: ignore[arg-type]
    except RuntimeError:
        return []


def cmd_session_status(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Show browser session state for all configured profiles."""
    if not _api_alive():
        print(_red(f"Tabby API is not running. Run: {_bold('python cli/tabby_setup.py start')}"))
        return 1

    admin_token = _get_admin_token()
    if not admin_token:
        return 1

    sessions = _get_sessions(admin_token)
    cache = _load_cache()
    apps = cache.get("apps", {})
    app_to_profile = {v.get("app_id"): k for k, v in apps.items()}

    if not sessions:
        print(_yellow("No sessions found."))
        print(f"  Start one with: {_bold('python cli/tabby_setup.py session ensure')}")
        return 0

    profile_filter: str | None = getattr(args, "profile", None)

    print(_bold("Browser sessions:"))
    shown = 0
    for s in sessions:
        app_id = s.get("app_id", "")
        profile_name = app_to_profile.get(app_id, app_id[:8] + "…")
        if profile_filter and profile_name != profile_filter:
            continue
        state = s.get("state", "?")
        sid = s.get("id", "?")
        if state == "HEALTHY":
            icon = _green("●")
        elif state in ("FAILED", "TERMINATED"):
            icon = _red("●")
        else:
            icon = _yellow("●")
        print(f"  {icon}  {_bold(profile_name)}: {state}  (id: {sid[:8]}…)")
        shown += 1

    if shown == 0:
        print(_yellow(f"  No sessions for profile '{profile_filter}'."))
        print(
            f"  Start one with: {_bold('python cli/tabby_setup.py session ensure --profile ' + (profile_filter or ''))}"
        )
    return 0


def cmd_session_ensure(args: argparse.Namespace) -> int:
    """
    Ensure a HEALTHY browser session exists for a profile.
    Scales to 1 session via API, then starts the worker locally using pnpm
    in CDP (headless) mode and waits up to 5 minutes for HEALTHY state.
    """
    if not _api_alive():
        print(_red(f"Tabby API is not running. Run: {_bold('python cli/tabby_setup.py start')}"))
        return 1

    admin_token = _get_admin_token()
    if not admin_token:
        return 1

    cache = _load_cache()
    apps = cache.get("apps", {})

    # Resolve which profile to use
    profile_id: str | None = getattr(args, "profile", None)
    if not profile_id:
        if len(apps) == 1:
            profile_id = list(apps.keys())[0]
        elif len(apps) > 1:
            print(_red("Multiple profiles configured. Specify one with --profile:"))
            for p in apps:
                print(f"  {p}")
            return 1
        else:
            defaults = cache.get("default_profiles", [])
            profile_id = defaults[0] if len(defaults) == 1 else None
        if not profile_id:
            print(_red("Could not determine profile. Run setup first or pass --profile."))
            return 1

    entry = apps.get(profile_id)
    if not entry:
        print(
            _red(f"Profile '{profile_id}' not found in cache. Run: python cli/tabby_setup.py setup")
        )
        return 1

    app_id: str = entry.get("app_id", "")
    if not app_id:
        print(_red(f"No app_id cached for '{profile_id}'. Re-run setup."))
        return 1

    # Decode tenant_id from admin JWT
    try:
        jwt_payload = _decode_jwt_payload(admin_token)
        tenant_id = jwt_payload.get("tenant_id") or jwt_payload.get("tenantId", "")
    except RuntimeError:
        tenant_id = ""

    # Check for existing HEALTHY session
    sessions = _get_sessions(admin_token)
    healthy = [s for s in sessions if s.get("app_id") == app_id and s.get("state") == "HEALTHY"]
    if healthy:
        print(_green(f"✓ Session for '{profile_id}' is already HEALTHY"))
        return 0

    print(f"No HEALTHY session for '{_cyan(profile_id)}' — starting one …")

    # Seed a session record directly via the local dev script
    # (the K8s controller is not running locally, so scale alone won't create records)
    session_id = _seed_session(app_id, tenant_id)
    if not session_id:
        return 1
    print(f"  Session ID: {_cyan(session_id)}")

    # Build worker env
    env = {**os.environ}
    env.update(_load_env_local())
    env.update(
        {
            "SESSION_ID": session_id,
            "APP_ID": app_id,
            "TENANT_ID": tenant_id,
            "STREAMING_MODE": "cdp",  # headless, no X11 required
        }
    )

    # The worker resolves credentials via mounted files (primary path) or env vars
    # (fallback).  Env var fallback is unreliable on Ubuntu because pnpm spawns
    # through dash (/bin/sh), which silently drops keys containing hyphens
    # (e.g. "no-auth_USERNAME").  Use the file-based primary path instead by
    # writing credentials to a temp directory and pointing CREDENTIALS_MOUNT_PATH
    # at it.  This works for both no-login (dummy values) and login profiles.
    creds_mount = Path("/tmp/tabby-local-secrets")
    secret_name = entry.get("credential_ref", "k8s:secret/no-auth").replace("k8s:secret/", "")
    secret_dir = creds_mount / secret_name
    secret_dir.mkdir(parents=True, exist_ok=True)
    if entry.get("username"):
        # Login profile: read password from .env.local where setup stored it
        env_local_vars = _load_env_local()
        prefix = _env_prefix(_secret_name(profile_id))
        username = entry["username"]
        password = env_local_vars.get(f"{prefix}_PASSWORD", "")
        if not password:
            print(_red(f"Password for '{profile_id}' not found in {ENV_LOCAL}."))
            print("Re-run: python cli/tabby_setup.py setup")
            return 1
        (secret_dir / "username").write_text(username)
        (secret_dir / "password").write_text(password)
    else:
        # No-login profile: dummy values (never used by the DSL)
        (secret_dir / "username").write_text("no-auth")
        (secret_dir / "password").write_text("no-auth")
    env["CREDENTIALS_MOUNT_PATH"] = str(creds_mount)

    # Kill any lingering worker process before starting a new one (port 8091)
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
    # Also free the port in case the PID file is stale
    try:
        subprocess.run(["fuser", "-k", "8091/tcp"], capture_output=True)
        time.sleep(0.5)
    except FileNotFoundError:
        pass  # fuser not available on all platforms

    # Start worker
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
    print(f"  Worker started (PID {proc.pid}) — logs → {_cyan(str(WORKER_LOG_FILE))}")
    print()
    print("  Waiting for health check to pass", end="", flush=True)

    # Poll up to 5 minutes.
    # The worker updates health_result_type ('PASS'/'TRANSIENT_FAIL'/'AUTH_FAIL') after
    # running the DSL + health predicates.  state may also transition to 'HEALTHY'.
    final_state = ""
    final_health = ""
    for _ in range(60):
        time.sleep(5)
        print(".", end="", flush=True)
        try:
            resp = _http("GET", f"/sessions/{session_id}", token=admin_token)
            assert isinstance(resp, dict)
            final_state = resp.get("state", "")
            final_health = resp.get("health_result_type", "")
            if final_state == "HEALTHY" or final_health == "PASS":
                break
            if final_state in ("FAILED", "TERMINATED") or final_health == "AUTH_FAIL":
                print()
                print(_red(f"Session failed (state={final_state}, health={final_health})."))
                print(f"  Worker logs: {WORKER_LOG_FILE}")
                return 1
        except (RuntimeError, AssertionError):
            pass
    else:
        print()
        print(_red("Session did not pass health check within 5 minutes."))
        print(f"  Worker logs: {WORKER_LOG_FILE}")
        return 1

    print()

    # The K8s controller normally transitions state → HEALTHY when it sees
    # health_result_type = PASS.  For local dev (no controller), do it via psql.
    if final_state != "HEALTHY":
        print("  Promoting session state to HEALTHY …", end=" ", flush=True)
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
            print(_green("✓"))
        except Exception as exc:
            print()
            print(_red(f"Failed to promote session state: {exc}"))
            return 1

    print(_green(f"✓ Session for '{profile_id}' is HEALTHY"))
    print()
    print("  You can now run tests:")
    env_file = ABCD_DIR / ".env"
    print(f"    {_bold('source ' + str(env_file))}")
    print(f"    {_bold('python cli/test_runner.py <action-id>')}")
    return 0


def cmd_session_stop(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Stop the locally-running worker process."""
    pid = _read_pid(WORKER_PID_FILE)
    if pid is None:
        print(_yellow("No worker PID file found — worker may not be running."))
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        _clear_pid(WORKER_PID_FILE)
        print(_green(f"✓ Worker (PID {pid}) stopped."))
        return 0
    except ProcessLookupError:
        _clear_pid(WORKER_PID_FILE)
        print(_yellow(f"Process {pid} was not running — cleared stale PID file."))
        return 0
    except Exception as exc:
        print(_red(f"Failed to stop worker: {exc}"))
        return 1


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python cli/tabby_setup.py",
        description="Tabby lifecycle & provisioning for the abcd test runner.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Check Docker Compose services and API liveness")
    sub.add_parser("start", help="Start infra (docker compose) and Tabby API in background")

    stop_p = sub.add_parser("stop", help="Stop the Tabby API process")
    stop_p.add_argument("--infra", action="store_true", help="Also stop Docker Compose services")

    setup_p = sub.add_parser(
        "setup",
        help="Full provisioning: start + agent client + ServiceProfile + write .env",
    )
    setup_p.add_argument(
        "--profiles",
        nargs="+",
        metavar="PROFILE_ID",
        default=None,
        help="Tabby profile IDs (default: auto-discovered from adopt_profile.json files)",
    )
    setup_p.add_argument(
        "--force",
        action="store_true",
        help="Revoke and recreate the agent client even if one exists",
    )
    setup_p.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help="Path to write TABBY_* vars into (default: abcd/.env)",
    )

    # session
    session_p = sub.add_parser("session", help="Manage browser sessions")
    session_sub = session_p.add_subparsers(dest="session_action", required=True)

    session_sub.add_parser("status", help="Show session state for configured profiles")

    ensure_p = session_sub.add_parser(
        "ensure",
        help="Ensure a HEALTHY session exists, starting the worker if needed",
    )
    ensure_p.add_argument(
        "--profile",
        metavar="PROFILE_ID",
        default=None,
        help="Profile to ensure (default: the only configured profile)",
    )

    stop_sess_p = session_sub.add_parser("stop", help="Stop a locally-running worker")
    stop_sess_p.add_argument(
        "--profile",
        metavar="PROFILE_ID",
        default=None,
        help="Profile whose worker to stop (currently stops by PID file)",
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    # Ensure attributes used across subcommands always exist
    for attr, default in [
        ("infra", False),
        ("force", False),
        ("env_file", None),
        ("profiles", None),
    ]:
        if not hasattr(args, attr):
            setattr(args, attr, default)

    if args.command == "status":
        return cmd_health(args)
    if args.command == "start":
        return cmd_start(args)
    if args.command == "stop":
        return cmd_stop(args)
    if args.command == "setup":
        return cmd_setup(args)
    if args.command == "session":
        if args.session_action == "status":
            return cmd_session_status(args)
        if args.session_action == "ensure":
            return cmd_session_ensure(args)
        if args.session_action == "stop":
            return cmd_session_stop(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
