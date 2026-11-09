"""Cloud bootstrap: make a fresh harness sandbox a ready abcd workspace (PLAN.md §6).

A cloud run starts with nothing but the unzipped bundle, the Doc Store file
``abcd-workspace.json`` and the org's ``SANDBOX_*`` vault secrets (the platform
strips the prefix, so they arrive as plain env vars). ``bootstrap`` clones the
workspace repo at its pinned ref, writes ``workspaces/<env>/.env`` and checks the
PAT. It fails loudly, naming every missing variable, because the platform drops a
badly named or unfetchable secret without an error.

Git credentials: ``GITHUB_TOKEN`` (HTTPS) or ``GITHUB_DEPLOY_KEY`` (SSH, a repo-scoped deploy
key). Neither appears in a URL or in argv: a token reaches ``git`` through a credential helper that
reads an environment variable of the child process, and a deploy key is written to a 0600 file under
the sandbox home and reached through ``GIT_SSH_COMMAND``. A deploy key cannot call the GitHub REST
API, so pull requests and GitHub releases are flagged (not failed) when there is no token.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli.builder_common import deploykey
from cli.builder_common.cloudmode import write_marker
from cli.builder_common.errors import PlatformError, UsageError

WORKSPACE_FILE_NAME = "abcd-workspace.json"

# Variable names as the sandbox sees them (after the platform strips SANDBOX_).
VAR_PAT_CLIENT_ID = "ABCD_PAT_CLIENT_ID"
VAR_PAT_SECRET = "ABCD_PAT_SECRET"
VAR_GITHUB_TOKEN = "GITHUB_TOKEN"
VAR_GITHUB_DEPLOY_KEY = deploykey.VAR_DEPLOY_KEY
VAR_GIT_CREDENTIAL = f"{VAR_GITHUB_TOKEN} or {VAR_GITHUB_DEPLOY_KEY}"
VAR_WEBUI_ENDPOINT = "ABCD_WEBUI_ENDPOINT"
VAR_APP_BASE_URL = "ADOPT_APP_BASE_URL"
VAR_CONVERSATION_ID = "ADOPT_CONVERSATION_ID"

# The env var the git credential helper reads the token from (child process only).
_GIT_TOKEN_VAR = "ABCD_BOOTSTRAP_GIT_TOKEN"
_CREDENTIAL_HELPER = (
    "!f() { echo username=x-access-token; echo password=${" + _GIT_TOKEN_VAR + "}; }; f"
)
_REPO_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_ENV = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

ExchangePat = Callable[[str, str, str], None]
RunGit = Callable[[list[str], Path, Mapping[str, str]], None]


class MissingVariablesError(UsageError):
    """One or more required sandbox variables are absent; ``missing`` names them all."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(
            "cloud bootstrap: missing required environment variable(s): "
            + ", ".join(missing)
            + ". Each arrives from a vault secret named SANDBOX_<NAME> in the Builder home "
            "org; a secret that is misnamed or failed to fetch is dropped silently."
        )


def normalize_env_id(env: str) -> str:
    """Env id as it appears in secret names: uppercase, ``-`` to ``_``."""
    return env.strip().upper().replace("-", "_")


def sandbox_secret_name(base: str, env: str | None = None) -> str:
    """Vault secret name for ``base`` (``SANDBOX_`` prefix, optional env suffix)."""
    name = f"SANDBOX_{base}"
    return f"{name}_{normalize_env_id(env)}" if env else name


@dataclass(frozen=True)
class CloudWorkspace:
    env: str
    repo: str
    ref: str


@dataclass(frozen=True)
class CloudEnv:
    pat_client_id: str = ""
    pat_secret: str = ""
    github_token: str = ""
    github_deploy_key: str = field(default="", repr=False)
    webui_endpoint: str = ""
    conversation_id: str = ""

    def __repr__(self) -> str:  # never leak secrets through logs
        return f"CloudEnv(webui_endpoint={self.webui_endpoint!r}, secrets=<masked>)"


@dataclass(frozen=True)
class BootstrapResult:
    env: str
    repo: str
    ref: str
    workspace_dir: Path
    env_file: Path
    conversation_id: str
    webui_endpoint: str
    git_auth: str = "token"  # "token" or "deploy_key"
    flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "repo": self.repo,
            "ref": self.ref,
            "workspace_dir": str(self.workspace_dir),
            "env_file": str(self.env_file),
            "conversation_id": self.conversation_id,
            "webui_endpoint": self.webui_endpoint,
            "git_auth": self.git_auth,
            "github_api": "flagged" if self.flags else "available",
            "flags": list(self.flags),
        }


def read_workspace_file(path: Path) -> CloudWorkspace:
    """Parse ``abcd-workspace.json`` (``env``, ``repo``, ``ref``)."""
    try:
        data = json.loads(path.read_text())
    except OSError as e:
        raise UsageError(f"cannot read {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise UsageError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise UsageError(f"{path} must hold a JSON object with env, repo and ref")
    values: dict[str, str] = {}
    for key in ("env", "repo", "ref"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise UsageError(f"{path}: '{key}' is required and must be a non-empty string")
        values[key] = value.strip()
    if not _SAFE_ENV.match(values["env"]):
        raise UsageError(f"{path}: env {values['env']!r} is not a valid workspace name")
    return CloudWorkspace(**values)


def collect_environment(environ: Mapping[str, str]) -> CloudEnv:
    """Read the injected variables, or raise ``MissingVariablesError`` naming all gaps."""

    def get(name: str) -> str:
        return (environ.get(name) or "").strip()

    missing: list[str] = []
    for name in (VAR_PAT_CLIENT_ID, VAR_PAT_SECRET):
        if not get(name):
            missing.append(name)
    if not get(VAR_GITHUB_TOKEN) and not get(VAR_GITHUB_DEPLOY_KEY):
        missing.append(VAR_GIT_CREDENTIAL)
    endpoint = get(VAR_WEBUI_ENDPOINT) or get(VAR_APP_BASE_URL)
    if not endpoint:
        missing.append(f"{VAR_WEBUI_ENDPOINT} or {VAR_APP_BASE_URL}")
    if not get(VAR_CONVERSATION_ID):
        missing.append(VAR_CONVERSATION_ID)
    if missing:
        raise MissingVariablesError(missing)
    return CloudEnv(
        pat_client_id=get(VAR_PAT_CLIENT_ID),
        pat_secret=get(VAR_PAT_SECRET),
        github_token=get(VAR_GITHUB_TOKEN),
        github_deploy_key=get(VAR_GITHUB_DEPLOY_KEY),
        webui_endpoint=endpoint.rstrip("/"),
        conversation_id=get(VAR_CONVERSATION_ID),
    )


def repo_url(repo: str) -> str:
    """HTTPS clone URL for ``owner/name`` (or a credential-free https URL)."""
    if _REPO_SLUG.match(repo):
        return f"https://github.com/{repo}.git"
    if repo.startswith("https://") and "@" not in repo.split("/", 3)[2]:
        return repo
    raise UsageError(
        "workspace repo must be 'owner/name' or an https URL without credentials "
        "(tokens are passed through a credential helper, never in a URL)"
    )


def _run_git(args: list[str], cwd: Path, env: Mapping[str, str]) -> None:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=dict(env), capture_output=True, text=True, timeout=600
    )
    if proc.returncode != 0:
        # git's stderr never carries the token (it is not in argv or the URL).
        raise PlatformError(f"git failed: {proc.stderr.strip()[:500]}")


COMMIT_NAME = "abcd cloud runner"
COMMIT_EMAIL = "abcd-cloud@users.noreply.github.com"
INSTALL_GIT_HINT = "apt-get update && apt-get install -y git openssh-client"


def require_git_tools(
    deploy_key: bool, *, which: Callable[[str], str | None] | None = None
) -> None:
    """The slim sandbox image has no ``git`` (and maybe no ``ssh``): say how to install them
    instead of failing with ``[Errno 2] No such file or directory: 'git'``."""
    find = which or shutil.which
    missing = [t for t in ("git", "ssh") if (t == "git" or deploy_key) and not find(t)]
    if missing:
        raise UsageError(
            f"cloud bootstrap needs {' and '.join(missing)} in this sandbox, and it is not "
            f"installed. Install it, then run bootstrap again: {INSTALL_GIT_HINT}"
        )


def clone_workspace(
    repo: str,
    ref: str,
    dest: Path,
    token: str = "",
    *,
    deploy_key_file: Path | None = None,
    run_git: RunGit | None = None,
    base_env: Mapping[str, str] | None = None,
) -> None:
    """Fetch ``ref`` of ``repo`` into ``dest`` (detached at the pinned ref).

    With a ``token`` the remote stays HTTPS and git reads the token through a credential helper.
    Otherwise ``deploy_key_file`` is used: the remote becomes ``git@github.com:owner/repo.git``,
    ``GIT_SSH_COMMAND`` names the key, and the repo's own ``core.sshCommand`` is set to the same so
    later plain ``git fetch``/``git push`` in the workspace work too. Either way the repo gets a
    local committer identity (the sandbox has none). Neither secret is in argv.
    """
    if not token and deploy_key_file is None:
        raise UsageError(f"cloud bootstrap needs {VAR_GIT_CREDENTIAL}")
    runner = run_git or _run_git
    url = repo_url(repo)
    env = dict(os.environ if base_env is None else base_env)
    env["GIT_TERMINAL_PROMPT"] = "0"
    auth: list[str] = []
    if token:
        env[_GIT_TOKEN_VAR] = token
        # The empty helper resets any inherited helper list; then ours is the only one.
        auth = ["-c", "credential.helper=", "-c", f"credential.helper={_CREDENTIAL_HELPER}"]
    else:
        assert deploy_key_file is not None
        env.update(deploykey.git_ssh_env(deploy_key_file))
        url = deploykey.to_ssh_url(url)
    dest.mkdir(parents=True, exist_ok=True)
    runner(["init", "-q"], dest, env)
    runner(["remote", "add", "origin", url], dest, env)
    # The sandbox has no git identity: without one a plain `git commit` fails ("Author identity
    # unknown") and a following push sends an empty branch.
    runner(["config", "user.name", COMMIT_NAME], dest, env)
    runner(["config", "user.email", COMMIT_EMAIL], dest, env)
    if not token:
        assert deploy_key_file is not None
        runner(["config", "core.sshCommand", deploykey.ssh_command(deploy_key_file)], dest, env)
    runner([*auth, "fetch", "-q", "--depth", "1", "origin", ref], dest, env)
    runner(["checkout", "-q", "--detach", "FETCH_HEAD"], dest, env)


def write_env_file(path: Path, endpoint: str, client_id: str, secret: str) -> None:
    """Write the workspace ``.env`` with mode 0600 (created private, never widened)."""
    for value in (endpoint, client_id, secret):
        if any(c in value for c in "\r\n\0"):
            raise UsageError("refusing to write a .env value containing a line break")
    body = (
        f"ADOPT_WEBUI_ENDPOINT={endpoint}\n"
        f"ADOPT_HARNESS_PAT_CLIENT_ID={client_id}\n"
        f"ADOPT_HARNESS_PAT_SECRET={secret}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _exchange_token(endpoint: str, client_id: str, secret: str) -> str:
    """``POST /v1/users/api-token``; returns the access token. Raises ``PlatformError``."""
    import requests

    try:
        resp = requests.post(
            f"{endpoint.rstrip('/')}/v1/users/api-token",
            json={"client_id": client_id, "secret": secret},
            timeout=30,
        )
    except requests.RequestException as e:
        raise PlatformError(f"PAT check could not reach {endpoint}: {type(e).__name__}") from e
    if resp.status_code != 200:
        raise PlatformError(f"PAT exchange was refused by {endpoint} (HTTP {resp.status_code})")
    try:
        token = resp.json().get("access_token")
    except ValueError:
        token = None
    if not token or not isinstance(token, str):
        raise PlatformError("PAT exchange returned no access_token")
    return token


def exchange_pat_http(endpoint: str, client_id: str, secret: str) -> None:
    """Default PAT check: ``POST /v1/users/api-token``. Raises ``PlatformError`` on failure."""
    _exchange_token(endpoint, client_id, secret)


def exchange_pat_claims_http(endpoint: str, client_id: str, secret: str) -> dict[str, Any]:
    """PAT exchange for ``cloud doctor``: only the tenant and roles claims of the JWT, never the
    token itself (the payload is read without verifying it; it is a display value)."""
    import base64

    token = _exchange_token(endpoint, client_id, secret)
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        claims = {}
    if not isinstance(claims, dict):
        claims = {}
    roles = claims.get("roles")
    return {
        "tenant_id": str(claims.get("tenantId") or claims.get("tenant_id") or ""),
        "roles": [str(r) for r in roles] if isinstance(roles, list) else [],
    }


ExchangeClaims = Callable[[str, str, str], dict[str, Any]]


@dataclass(frozen=True)
class PlatformOnlyResult:
    env: str
    workspace_dir: Path
    env_file: Path
    conversation_id: str
    webui_endpoint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "platform_only": True,
            "workspace_dir": str(self.workspace_dir),
            "env_file": str(self.env_file),
            "conversation_id": self.conversation_id,
            "webui_endpoint": self.webui_endpoint,
            "git": "none (no repo cloned)",
        }


def collect_platform_environment(environ: Mapping[str, str]) -> CloudEnv:
    """Like ``collect_environment`` but only the platform variables (no git credential, no
    conversation id are required). Raises ``MissingVariablesError`` naming every gap."""

    def get(name: str) -> str:
        return (environ.get(name) or "").strip()

    missing = [n for n in (VAR_PAT_CLIENT_ID, VAR_PAT_SECRET) if not get(n)]
    endpoint = get(VAR_WEBUI_ENDPOINT) or get(VAR_APP_BASE_URL)
    if not endpoint:
        missing.append(f"{VAR_WEBUI_ENDPOINT} or {VAR_APP_BASE_URL}")
    if missing:
        raise MissingVariablesError(missing)
    return CloudEnv(
        pat_client_id=get(VAR_PAT_CLIENT_ID),
        pat_secret=get(VAR_PAT_SECRET),
        webui_endpoint=endpoint.rstrip("/"),
        conversation_id=get(VAR_CONVERSATION_ID),
    )


def bootstrap_platform_only(
    abcd_root: Path,
    env: str,
    *,
    environ: Mapping[str, str] | None = None,
    exchange_pat: ExchangePat | None = None,
) -> PlatformOnlyResult:
    """A minimal workspace for platform commands (``skill push``, ``agent create``, sessions)
    when there is no workstream repo or ``abcd-workspace.json``: ``workspaces/<env>/`` with
    ``env.json``, the 0600 ``.env`` and the cloud marker. No git call is made. The variables are
    checked first, so nothing is written when one is missing; the PAT is checked last."""
    if not _SAFE_ENV.match(env):
        raise UsageError(f"{env!r} is not a valid workspace name")
    cloud_env = collect_platform_environment(os.environ if environ is None else environ)
    dest = abcd_root / "workspaces" / env
    dest.mkdir(parents=True, exist_ok=True)
    env_json = dest / "env.json"
    if not env_json.exists():
        env_json.write_text(
            json.dumps({"env_id": env, "name": env, "platform_only": True}, indent=2)
        )
    env_file = dest / ".env"
    write_env_file(
        env_file, cloud_env.webui_endpoint, cloud_env.pat_client_id, cloud_env.pat_secret
    )
    write_marker(dest, cloud_env.conversation_id)
    (exchange_pat or exchange_pat_http)(
        cloud_env.webui_endpoint, cloud_env.pat_client_id, cloud_env.pat_secret
    )
    return PlatformOnlyResult(
        env=env,
        workspace_dir=dest,
        env_file=env_file,
        conversation_id=cloud_env.conversation_id,
        webui_endpoint=cloud_env.webui_endpoint,
    )


def bootstrap(
    workspace_file: Path,
    abcd_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    exchange_pat: ExchangePat | None = None,
    run_git: RunGit | None = None,
    replace: bool = False,
    home: Path | None = None,
) -> BootstrapResult:
    """Clone the workspace repo, write its ``.env`` and verify the PAT.

    ``abcd_root`` is the unzipped bundle (``/workspace/abcd``); the workspace lands in
    ``<abcd_root>/workspaces/<env>``. Variables are checked first, so nothing is cloned
    when any is missing. ``GITHUB_TOKEN`` wins when both it and ``GITHUB_DEPLOY_KEY`` are set;
    with only a deploy key the GitHub API steps (pull request, release) are flagged in the result.
    ``exchange_pat``, ``run_git`` and the sandbox ``home`` (key file location) are injectable.
    """
    ws = read_workspace_file(workspace_file)
    cloud_env = collect_environment(os.environ if environ is None else environ)
    dest = abcd_root / "workspaces" / ws.env
    if dest.exists() and any(dest.iterdir()):
        if not replace:
            raise UsageError(f"{dest} already exists and is not empty (pass replace to re-clone)")
        shutil.rmtree(dest)

    if run_git is None:  # a real clone: git (and ssh for a deploy key) must exist
        require_git_tools(not cloud_env.github_token)
    key_file: Path | None = None
    flags: tuple[str, ...] = ()
    if not cloud_env.github_token:
        key_file = deploykey.write_key_file(cloud_env.github_deploy_key, home)
        flags = (
            "GITHUB_TOKEN is not set (only a deploy key): git clone/fetch/push work, but pull "
            "requests and GitHub releases cannot be created and will be flagged, not failed",
        )
    clone_workspace(
        ws.repo,
        ws.ref,
        dest,
        cloud_env.github_token,
        deploy_key_file=key_file,
        run_git=run_git,
    )
    # Keep the credentials file out of any commit even if the repo's .gitignore lacks it.
    exclude = dest / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a") as fh:
        fh.write("\n.env\n")
    env_file = dest / ".env"
    write_env_file(
        env_file, cloud_env.webui_endpoint, cloud_env.pat_client_id, cloud_env.pat_secret
    )
    # The marker every later command reads: this is a cloud run, and this is its conversation.
    write_marker(dest, cloud_env.conversation_id)
    with exclude.open("a") as fh:
        fh.write(".builder/\n")
    (exchange_pat or exchange_pat_http)(
        cloud_env.webui_endpoint, cloud_env.pat_client_id, cloud_env.pat_secret
    )
    return BootstrapResult(
        env=ws.env,
        repo=ws.repo,
        ref=ws.ref,
        workspace_dir=dest,
        env_file=env_file,
        conversation_id=cloud_env.conversation_id,
        webui_endpoint=cloud_env.webui_endpoint,
        git_auth="token" if cloud_env.github_token else "deploy_key",
        flags=flags,
    )
