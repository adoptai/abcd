#!/usr/bin/env python3
"""
Bootstrap a local Tabby for the NoUI toolkit, from the pinned `tabby/` submodule.

NoUI talks to whatever Tabby `TABBY_API_URL` names. In the cloud (platform_jwt)
or inside the Agent Harness (broker) that is somebody else's Tabby and there is
nothing to bootstrap. For local development (agent_token) this script stands one
up from the submodule pinned in this repo, so the toolkit and the Tabby contract
it was tested against move together.

Two tiers, because Tabby has two:

  up     API tier -- Docker Compose infra (Postgres, Redis, NATS, MinIO) + the
         Tabby API on the host. Enough for auth, App Templates, profiles and
         the registration half of NoUI. NOT enough to record or /execute: those
         need browser workers, which only run on Kubernetes.
  full   Full tier -- delegates to Tabby's own Kind targets (kind-create +
         kind-deploy), which build the worker images and deploy everything.

Usage:
    python cli/tabby_bootstrap.py status [--env ENV]          # submodule, prereqs, reachability
    python cli/tabby_bootstrap.py init                         # git submodule update --init tabby
    python cli/tabby_bootstrap.py up   [--env ENV] [--port 8000] [--port-offset N] [--write-env]
    python cli/tabby_bootstrap.py provision --url URL [--env ENV] [--write-env]
    python cli/tabby_bootstrap.py down
    python cli/tabby_bootstrap.py full                         # Kind deploy (workers included)

`provision` mints local agent credentials on a running Tabby: bootstrap-admin
login -> tenant -> agent client (unrestricted profiles). With --write-env it
writes TABBY_API_URL / TABBY_CLIENT_ID / TABBY_CLIENT_SECRET / TABBY_ADMIN_TOKEN
and NOUI_TABBY_AUTH_MODE=agent_token into workspaces/<env>/.env.

Local state (generated keys, logs, pid) lives in .tabby-local/ (gitignored).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TABBY_DIR = PROJECT_ROOT / "tabby"
STATE_DIR = PROJECT_ROOT / ".tabby-local"
SCHEMA_FILE = Path("packages/shared/src/recording.types.ts")
MIN_RECORDING_SCHEMA = 5  # NoUI browser skills need locator candidates/outcomes (v5)
DEFAULT_PORT = 8000
COMPOSE_PROJECT = "abcd-tabby"
DEAD_MINIO_IMAGES = {"minio/minio", "docker.io/minio/minio", "quay.io/minio/minio"}
DEFAULT_MINIO_IMAGE = "cgr.dev/chainguard/minio:latest"
# Host ports of Tabby's docker-compose services (postgres, redis, nats, nats
# monitoring, minio, minio console); shifted together by --port-offset.
INFRA_PORTS = {
    "postgres": 5432,
    "redis": 6379,
    "nats": 4222,
    "nats_http": 8222,
    "minio": 9000,
    "minio_console": 9001,
}
ADMIN_EMAIL = "admin@tabby.local"
TENANT_NAME = "abcd-local"
AGENT_NAME = "abcd-noui-local"

API_TIER_PREREQS = ("git", "docker", "node", "pnpm")
FULL_TIER_PREREQS = (*API_TIER_PREREQS, "kind", "kubectl", "helm", "make")


class BootstrapError(Exception):
    """A step cannot proceed; the message says what to do."""


# ---------------------------------------------------------------------------
# Submodule
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path = PROJECT_ROOT) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise BootstrapError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def pinned_commit(project_root: Path = PROJECT_ROOT) -> str:
    """The commit the superproject pins `tabby` to (the gitlink in the index)."""
    line = _git("ls-files", "--stage", "tabby", cwd=project_root)
    parts = line.split()
    if len(parts) < 2 or parts[0] != "160000":
        raise BootstrapError("tabby is not registered as a submodule in this checkout")
    return parts[1]


def submodule_initialized(tabby_dir: Path = TABBY_DIR) -> bool:
    return (tabby_dir / "package.json").exists()


def recording_schema_version(tabby_dir: Path = TABBY_DIR) -> int | None:
    path = tabby_dir / SCHEMA_FILE
    if not path.exists():
        return None
    m = re.search(r"RECORDING_SCHEMA_VERSION\s*=\s*(\d+)", path.read_text(encoding="utf-8"))
    return int(m.group(1)) if m else None


def init_submodule() -> None:
    subprocess.run(["git", "submodule", "update", "--init", "tabby"], cwd=PROJECT_ROOT, check=True)
    version = recording_schema_version()
    if version is None or version < MIN_RECORDING_SCHEMA:
        raise BootstrapError(
            f"tabby submodule records schema v{version}; NoUI needs >= v{MIN_RECORDING_SCHEMA}. "
            "Bump the pin to a tabby main commit that has it."
        )


# ---------------------------------------------------------------------------
# Prereqs / reachability
# ---------------------------------------------------------------------------


def missing_prereqs(tools: tuple[str, ...]) -> list[str]:
    return [t for t in tools if shutil.which(t) is None]


def infra_ports(offset: int) -> dict[str, int]:
    return {k: v + offset for k, v in INFRA_PORTS.items()}


def busy_ports(ports: list[int]) -> list[int]:
    import socket

    busy = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                busy.append(port)
    return busy


def write_compose(state: LocalState, offset: int, tabby_dir: Path = TABBY_DIR) -> Path:
    """Tabby's compose file with every host port shifted by ``offset``.

    A standalone copy rather than an override file: compose merges ``ports``
    lists, so an override cannot take the original 5432/6379/... mappings away.
    """
    import yaml

    doc = yaml.safe_load((tabby_dir / "docker-compose.yml").read_text(encoding="utf-8"))
    minio_image = os.environ.get("TABBY_BOOTSTRAP_MINIO_IMAGE", DEFAULT_MINIO_IMAGE)
    for svc in (doc.get("services") or {}).values():
        if str(svc.get("image", "")).split(":")[0] in DEAD_MINIO_IMAGES:
            # minio/minio is no longer pullable from Docker Hub (nor quay.io), so
            # Tabby's compose file cannot start as written. Swap in a maintained
            # build; its image is distroless (no `mc`), so the mc-based
            # healthcheck goes too -- compose then waits for "running".
            svc["image"] = minio_image
            svc.pop("healthcheck", None)
        mapped = []
        for entry in svc.get("ports") or []:
            host, _, container = str(entry).rpartition(":")
            mapped.append(f"{int(host) + offset}:{container}" if host else str(entry))
        if mapped:
            svc["ports"] = mapped
    state.root.mkdir(parents=True, exist_ok=True)
    path = state.root / "docker-compose.yml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def is_alive(url: str, *, client: httpx.Client | None = None, timeout: float = 3.0) -> bool:
    c = client or httpx.Client(timeout=timeout)
    try:
        r = c.get(f"{url.rstrip('/')}/health/live")
        return r.status_code == 200
    except httpx.HTTPError:
        return False
    finally:
        if client is None:
            c.close()


def wait_alive(url: str, *, attempts: int = 90, delay: float = 2.0) -> bool:
    for _ in range(attempts):
        if is_alive(url):
            return True
        time.sleep(delay)
    return False


# ---------------------------------------------------------------------------
# Local state
# ---------------------------------------------------------------------------


@dataclass
class LocalState:
    root: Path

    @property
    def env_file(self) -> Path:
        return self.root / "api.env"

    @property
    def pid_file(self) -> Path:
        return self.root / "api.pid"

    @property
    def log_file(self) -> Path:
        return self.root / "api.log"

    @property
    def creds_file(self) -> Path:
        return self.root / "credentials.json"

    @property
    def compose_file(self) -> Path:
        return self.root / "docker-compose.yml"


_PERSISTENT_SECRETS = (
    "JWT_SIGNING_KEY",
    "TENANT_ENCRYPTION_KEY",
    "AGENT_SECRET_HMAC_KEY",
    "ADMIN_BOOTSTRAP_PASSWORD",
)


def api_environment(state: LocalState, port: int, offset: int = 0) -> dict[str, str]:
    """The API's env. Secrets are generated once and always reused -- the Postgres
    volume outlives a port change, and Tabby only creates the bootstrap admin on an
    empty database, so rotating them would lock out the admin and orphan every
    agent client and encrypted profile. Only the port-derived values are recomputed."""
    state.root.mkdir(parents=True, exist_ok=True)
    ports = infra_ports(offset)
    saved: dict[str, str] = {}
    if state.env_file.exists():
        saved = {str(k): str(v) for k, v in json.loads(state.env_file.read_text()).items()}
    env = {
        "API_PORT": str(port),
        "NODE_ENV": "development",
        "DATABASE_URL": f"postgresql://browser_hitl:localdev@localhost:{ports['postgres']}/browser_hitl",
        "REDIS_URL": f"redis://localhost:{ports['redis']}",
        "NATS_URL": f"nats://localhost:{ports['nats']}",
        "MINIO_ENDPOINT": "localhost",
        "MINIO_PORT": str(ports["minio"]),
        "MINIO_ACCESS_KEY": "minioadmin",
        "MINIO_SECRET_KEY": "minioadmin",
        "JWT_SIGNING_KEY": saved.get("JWT_SIGNING_KEY") or secrets.token_hex(32),
        "TENANT_ENCRYPTION_KEY": saved.get("TENANT_ENCRYPTION_KEY") or secrets.token_hex(32),
        "AGENT_SECRET_HMAC_KEY": saved.get("AGENT_SECRET_HMAC_KEY") or secrets.token_hex(32),
        "ADMIN_BOOTSTRAP_EMAIL": saved.get("ADMIN_BOOTSTRAP_EMAIL") or ADMIN_EMAIL,
        "ADMIN_BOOTSTRAP_PASSWORD": saved.get("ADMIN_BOOTSTRAP_PASSWORD")
        or f"Local-{secrets.token_urlsafe(12)}!9a",
    }
    state.env_file.write_text(json.dumps(env, indent=2))
    os.chmod(state.env_file, 0o600)
    return env


# ---------------------------------------------------------------------------
# Provisioning (talks to any running Tabby with a bootstrap admin)
# ---------------------------------------------------------------------------


def _unwrap(body: Any) -> Any:
    """Tabby wraps most responses as {"data": ...}; accept both shapes."""
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def provision(
    url: str,
    *,
    admin_email: str,
    admin_password: str,
    client: httpx.Client | None = None,
    tenant_name: str = TENANT_NAME,
    agent_name: str = AGENT_NAME,
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    """Mint local agent credentials: admin login -> tenant -> agent client.

    ``existing`` (a previous provision result) is reused when its agent client still
    authenticates, so repeated `up` runs do not pile up unrestricted agent clients.
    """
    c = client or httpx.Client(base_url=url.rstrip("/"), timeout=30.0)
    try:
        r = c.post("/login", json={"email": admin_email, "password": admin_password})
        if r.status_code >= 400:
            raise BootstrapError(f"admin login failed ({r.status_code}): {r.text[:200]}")
        admin_token = str(_unwrap(r.json())["token"])
        auth = {"Authorization": f"Bearer {admin_token}"}

        r = c.get("/tenants", headers=auth)
        if r.status_code >= 400:
            raise BootstrapError(f"listing tenants failed ({r.status_code}): {r.text[:200]}")
        tenants = _unwrap(r.json())
        tenant = next((t for t in tenants or [] if t.get("name") == tenant_name), None)
        if tenant is None:
            r = c.post("/tenants", json={"name": tenant_name}, headers=auth)
            if r.status_code >= 400:
                raise BootstrapError(f"creating tenant failed ({r.status_code}): {r.text[:200]}")
            tenant = _unwrap(r.json())
        tenant_id = str(tenant["id"])

        if existing and existing.get("TABBY_CLIENT_ID") and existing.get("TABBY_CLIENT_SECRET"):
            probe = c.post(
                "/auth/agent-token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": existing["TABBY_CLIENT_ID"],
                    "client_secret": existing["TABBY_CLIENT_SECRET"],
                },
            )
            if probe.status_code < 400:
                return {
                    **existing,
                    "TABBY_API_URL": url.rstrip("/"),
                    "TABBY_ADMIN_TOKEN": admin_token,
                    "TABBY_TENANT_ID": tenant_id,
                    "NOUI_TABBY_AUTH_MODE": "agent_token",
                }

        r = c.post(
            "/admin/agent-clients",
            json={"name": agent_name, "tenant_id": tenant_id, "unrestricted_profiles": True},
            headers=auth,
        )
        if r.status_code >= 400:
            raise BootstrapError(
                f"registering agent client failed ({r.status_code}): {r.text[:200]}"
            )
        agent = _unwrap(r.json())
        return {
            "TABBY_API_URL": url.rstrip("/"),
            "TABBY_CLIENT_ID": str(agent["client_id"]),
            "TABBY_CLIENT_SECRET": str(agent["client_secret"]),
            "TABBY_ADMIN_TOKEN": admin_token,
            "TABBY_TENANT_ID": tenant_id,
            "NOUI_TABBY_AUTH_MODE": "agent_token",
        }
    finally:
        if client is None:
            c.close()


def upsert_env_file(path: Path, values: dict[str, str]) -> None:
    """Set keys in a .env file, replacing existing lines and keeping the rest."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        key = (
            line.split("=", 1)[0].strip()
            if "=" in line and not line.lstrip().startswith("#")
            else ""
        )
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# Local Tabby (written by cli/tabby_bootstrap.py)")
        out.extend(f"{k}={v}" for k, v in remaining.items())
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def workspace_env_file(env_name: str | None) -> Path:
    from cli.wdl_common.workspace_manager import DEFAULT_ENV, WORKSPACES_DIR, get_workspace_manager

    name = env_name or get_workspace_manager().active_env or DEFAULT_ENV
    env_dir = WORKSPACES_DIR / name
    if not (env_dir / "env.json").exists():
        raise BootstrapError(f"workspace '{name}' does not exist ({env_dir})")
    return env_dir / ".env"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def cmd_status(env_name: str | None) -> int:
    info: dict[str, Any] = {"tabby_dir": str(TABBY_DIR)}
    try:
        info["pinned_commit"] = pinned_commit()
    except BootstrapError as e:
        info["pinned_commit"] = f"error: {e}"
    info["initialized"] = submodule_initialized()
    if info["initialized"]:
        try:
            info["checked_out"] = _git("rev-parse", "HEAD", cwd=TABBY_DIR)
        except BootstrapError:
            info["checked_out"] = None
        info["recording_schema"] = recording_schema_version()
    info["missing_api_tier"] = missing_prereqs(API_TIER_PREREQS)
    info["missing_full_tier"] = missing_prereqs(FULL_TIER_PREREQS)
    url = None
    try:
        from dotenv import dotenv_values

        dotenv = workspace_env_file(env_name)
        url = (dotenv_values(dotenv) if dotenv.exists() else {}).get("TABBY_API_URL")
    except BootstrapError as e:
        info["workspace"] = str(e)
    url = url or f"http://localhost:{DEFAULT_PORT}"
    info["tabby_api_url"] = url
    info["reachable"] = is_alive(url)
    state = LocalState(STATE_DIR)
    info["local_api_pid"] = state.pid_file.read_text().strip() if state.pid_file.exists() else None
    print(json.dumps(info, indent=2))
    return 0


def cmd_up(env_name: str | None, port: int, write_env: bool, offset: int = 0) -> int:
    missing = missing_prereqs(API_TIER_PREREQS)
    if missing:
        raise BootstrapError(f"missing prerequisites for the API tier: {', '.join(missing)}")
    if not submodule_initialized():
        init_submodule()
    state = LocalState(STATE_DIR)
    url = f"http://localhost:{port}"
    env = api_environment(state, port, offset)

    if not is_alive(url):
        compose_running = (
            subprocess.run(
                ["docker", "compose", "-p", COMPOSE_PROJECT, "ps", "-q"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            != ""
        )
        wanted = [port] + ([] if compose_running else list(infra_ports(offset).values()))
        busy = busy_ports(wanted)
        if busy:
            raise BootstrapError(
                f"ports already in use: {', '.join(map(str, busy))}. Another service owns them; "
                "re-run with --port <free port> and/or --port-offset 20000 to shift Tabby's infra."
            )
        compose = write_compose(state, offset)
        _run(
            ["docker", "compose", "-p", COMPOSE_PROJECT, "-f", str(compose), "up", "-d", "--wait"],
            cwd=TABBY_DIR,
        )
        _run(["pnpm", "install", "--frozen-lockfile"], cwd=TABBY_DIR)
        _run(["pnpm", "--filter", "@browser-hitl/shared", "build"], cwd=TABBY_DIR)
        _run(["pnpm", "--filter", "@browser-hitl/api", "build"], cwd=TABBY_DIR)
        log = state.log_file.open("ab")
        proc = subprocess.Popen(
            ["node", "dist/main.js"],
            cwd=TABBY_DIR / "apps" / "api",
            env={**os.environ, **env},
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        state.pid_file.write_text(str(proc.pid))
        print(f"Tabby API starting (pid {proc.pid}, log {state.log_file}) ...", flush=True)
        if not wait_alive(url):
            raise BootstrapError(f"Tabby API did not become healthy at {url}; see {state.log_file}")
    print(f"Tabby API is up at {url}")

    previous = json.loads(state.creds_file.read_text()) if state.creds_file.exists() else None
    creds = provision(
        url,
        admin_email=env["ADMIN_BOOTSTRAP_EMAIL"],
        admin_password=env["ADMIN_BOOTSTRAP_PASSWORD"],
        existing=previous,
    )
    state.creds_file.write_text(json.dumps(creds, indent=2))
    os.chmod(state.creds_file, 0o600)
    _report_creds(creds, env_name, write_env)
    print(
        "\nNote: this is the API tier -- auth, App Templates and profiles work; recording "
        "and /execute need browser workers (`python cli/tabby_bootstrap.py full`)."
    )
    return 0


def _report_creds(creds: dict[str, str], env_name: str | None, write_env: bool) -> None:
    if write_env:
        path = workspace_env_file(env_name)
        upsert_env_file(path, creds)
        print(f"Wrote Tabby settings to {path}")
    else:
        print("Add to your workspace .env (or re-run with --write-env):")
        for k, v in creds.items():
            shown = "<secret>" if ("SECRET" in k or "TOKEN" in k) else v
            print(f"  {k}={shown}")


def cmd_provision(url: str, env_name: str | None, write_env: bool) -> int:
    email = os.environ.get("TABBY_ADMIN_EMAIL", "")
    password = os.environ.get("TABBY_ADMIN_PASSWORD", "")
    state = LocalState(STATE_DIR)
    if (not email or not password) and state.env_file.exists():
        saved = json.loads(state.env_file.read_text())
        email = email or saved.get("ADMIN_BOOTSTRAP_EMAIL", "")
        password = password or saved.get("ADMIN_BOOTSTRAP_PASSWORD", "")
    if not email or not password:
        raise BootstrapError("set TABBY_ADMIN_EMAIL and TABBY_ADMIN_PASSWORD (a Tabby Admin user)")
    creds = provision(url, admin_email=email, admin_password=password)
    _report_creds(creds, env_name, write_env)
    return 0


def cmd_down() -> int:
    state = LocalState(STATE_DIR)
    if state.pid_file.exists():
        pid = int(state.pid_file.read_text().strip())
        try:
            os.killpg(pid, signal.SIGTERM)
            print(f"stopped Tabby API (pid {pid})")
        except ProcessLookupError:
            print(f"Tabby API (pid {pid}) was not running")
        state.pid_file.unlink()
    if state.compose_file.exists() and shutil.which("docker"):
        _run(
            ["docker", "compose", "-p", COMPOSE_PROJECT, "-f", str(state.compose_file), "down"],
            cwd=TABBY_DIR,
        )
    return 0


def cmd_full() -> int:
    missing = missing_prereqs(FULL_TIER_PREREQS)
    if missing:
        raise BootstrapError(f"missing prerequisites for the full tier: {', '.join(missing)}")
    if not submodule_initialized():
        init_submodule()
    _run(["make", "kind-create"], cwd=TABBY_DIR)
    _run(["make", "build", "docker-build", "kind-deploy"], cwd=TABBY_DIR)
    print(
        "Kind deploy finished. Point TABBY_API_URL at the API service (see tabby/RUNBOOK.md for "
        "the port-forward), then run `python cli/tabby_bootstrap.py provision --url <url> "
        "--write-env` with TABBY_ADMIN_EMAIL/TABBY_ADMIN_PASSWORD set."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Bootstrap a local Tabby from the pinned submodule for the NoUI toolkit."
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="submodule, prerequisites and reachability")
    s.add_argument("--env")
    sub.add_parser("init", help="initialize the pinned tabby submodule")
    u = sub.add_parser("up", help="start the API tier locally and mint agent credentials")
    u.add_argument("--env")
    u.add_argument("--port", type=int, default=DEFAULT_PORT, help="Tabby API port")
    u.add_argument(
        "--port-offset",
        type=int,
        default=0,
        help="shift Tabby's infra host ports (5432, 6379, 4222, 9000, ...) by this much",
    )
    u.add_argument("--write-env", action="store_true", help="write creds into the workspace .env")
    pr = sub.add_parser("provision", help="mint agent credentials on a running Tabby")
    pr.add_argument("--url", required=True)
    pr.add_argument("--env")
    pr.add_argument("--write-env", action="store_true")
    sub.add_parser("down", help="stop the local API and infra")
    sub.add_parser("full", help="deploy the full stack (workers) to Kind")
    args = p.parse_args(argv)
    try:
        if args.cmd == "status":
            return cmd_status(args.env)
        if args.cmd == "init":
            init_submodule()
            print(f"tabby initialized at {pinned_commit()} (schema v{recording_schema_version()})")
            return 0
        if args.cmd == "up":
            return cmd_up(args.env, args.port, args.write_env, args.port_offset)
        if args.cmd == "provision":
            return cmd_provision(args.url, args.env, args.write_env)
        if args.cmd == "down":
            return cmd_down()
        return cmd_full()
    except BootstrapError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(
            f"Error: command failed ({e.returncode}): {' '.join(map(str, e.cmd))}", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
