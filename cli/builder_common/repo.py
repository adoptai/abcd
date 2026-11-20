"""git for workspace repos, plus GitHub pull requests and releases (PLAN.md sections 5-7).

git runs through ``subprocess`` (no GitPython). Credentials for the cloud are passed to git
through a ``-c credential.helper=...`` that reads the token from an environment variable, so
a token never appears in argv or in a remote URL. Pull requests and releases use the ``gh``
CLI when it is installed and authenticated, and the GitHub REST API otherwise.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import requests

from cli.builder_common import deploykey
from cli.builder_common import workspace as bws
from cli.builder_common.cloudmode import CLOUD_ENV_VARS, cloud_mode
from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.identity import slug

TOKEN_ENV_VAR = "ABCD_GITHUB_TOKEN"
# Only ABCD_GITHUB_TOKEN is honoured on a developer machine. The generic names are honoured in a
# cloud run (where the sandbox injects them): a stray GH_TOKEN/GITHUB_TOKEN in a local shell must
# not override the user's own credential helper.
_CLOUD_TOKEN_FALLBACKS = ("SANDBOX_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")
GITHUB_API = "https://api.github.com"
GITHUB_HOST = "github.com"
GITHUB_HOSTS_ENV = "ABCD_GITHUB_HOSTS"  # comma-separated GitHub Enterprise hosts
_BUILDER_PREFIXES = ("harness/", "builder.json", "ADOPT.md", ".gitignore")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitAuth:
    """``config`` goes in argv as ``-c`` pairs (no secret); ``env`` carries the token."""

    config: tuple[str, ...]
    env: Mapping[str, str] = field(repr=False)


def git_env_with_token(
    token: str,
    env_var: str = TOKEN_ENV_VAR,
    *,
    host: str = GITHUB_HOST,
    clear_machine_helpers: bool = True,
) -> GitAuth:
    """Credential helper config plus the environment that feeds it.

    The helper is an inline shell function that prints ``$<env_var>``; the token only ever
    lives in the child process environment. It is scoped to ``https://<host>`` through
    ``credential.https://<host>.helper``, so git never offers the token to another host.
    ``clear_machine_helpers`` puts an empty ``credential.https://<host>.helper=`` first, which
    resets the helpers configured on the machine for that host (cloud runs: the sandbox has none
    of its own); a local run keeps the user's own helpers and adds this one after them.
    """
    if not token:
        raise UsageError("No GitHub token provided")
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env_var):
        raise UsageError(f"Invalid environment variable name: {env_var!r}")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise UsageError(f"Invalid host: {host!r}")
    key = f"credential.https://{host}.helper"
    helper = f'!f() {{ echo username=x-access-token; echo "password=${env_var}"; }}; f'
    config = (f"{key}=", f"{key}={helper}") if clear_machine_helpers else (f"{key}={helper}",)
    return GitAuth(config=config, env={env_var: token, "GIT_TERMINAL_PROMPT": "0"})


def token_from_env(environ: Mapping[str, str] | None = None, ws: Path | None = None) -> str | None:
    """``ABCD_GITHUB_TOKEN`` always; ``SANDBOX_GITHUB_TOKEN``/``GITHUB_TOKEN``/``GH_TOKEN`` only
    in a cloud run (``environ`` None: this process; a mapping is judged by its own variables)."""
    env = os.environ if environ is None else environ
    if env.get(TOKEN_ENV_VAR):
        return env[TOKEN_ENV_VAR]
    in_cloud = cloud_mode(ws) if environ is None else _cloud_env(env)
    if in_cloud:
        for name in _CLOUD_TOKEN_FALLBACKS:
            if env.get(name):
                return env[name]
    return None


def _cloud_env(env: Mapping[str, str]) -> bool:
    return any((env.get(v) or "").strip() for v in CLOUD_ENV_VARS)


def github_hosts(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """Hosts that may receive the token: github.com, www.github.com and ``ABCD_GITHUB_HOSTS``."""
    env = os.environ if environ is None else environ
    extra = {h.strip().lower() for h in (env.get(GITHUB_HOSTS_ENV) or "").split(",") if h.strip()}
    return frozenset({GITHUB_HOST, f"www.{GITHUB_HOST}", *extra})


def auth_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    remote_url: str | None = None,
    ws: Path | None = None,
) -> GitAuth | None:
    """The credential-helper auth for a GitHub remote, else None (the machine's own git login).

    None unless ``remote_url`` is an ``https``/``ssh`` URL on ``github.com``, ``www.github.com`` or a
    host listed in ``ABCD_GITHUB_HOSTS`` (GitHub Enterprise): the token is never
    handed to another host. Used when ``ABCD_GITHUB_TOKEN`` is set, or in a cloud run for the
    sandbox's ``SANDBOX_GITHUB_TOKEN``/``GITHUB_TOKEN``/``GH_TOKEN``; a cloud run with only
    ``GITHUB_DEPLOY_KEY`` gets SSH auth instead (see :func:`_deploy_key_auth`). Without ``ABCD_GITHUB_TOKEN`` (a local run) the
    user's own helpers stay in place; with it, or in the cloud, they are reset for the host. Every network git call (fetch,
    push, push_tag, ls-remote, clone) resolves this from the remote's URL.
    """
    if not remote_url:
        return None
    try:
        host = parse_remote(remote_url)[0].lower()
    except UsageError:
        return None
    if host not in github_hosts(environ):
        return None
    token = token_from_env(environ, ws)
    env = os.environ if environ is None else environ
    in_cloud = cloud_mode(ws) if environ is None else _cloud_env(environ)
    if not token:
        return _deploy_key_auth(env) if in_cloud else None
    # an explicit ABCD_GITHUB_TOKEN, or a cloud run: the token helper is the only one asked
    return git_env_with_token(
        token, host=host, clear_machine_helpers=in_cloud or bool(env.get(TOKEN_ENV_VAR))
    )


def _deploy_key_auth(env: Mapping[str, str]) -> GitAuth | None:
    """A cloud run with only ``GITHUB_DEPLOY_KEY``: git over SSH with that key (written to a 0600
    file under the sandbox home on first use). Any https GitHub remote is rewritten to ssh for the
    call. The key is never in argv; ``GIT_SSH_COMMAND`` carries only the key file's path."""
    key = deploykey.deploy_key_from_env(env)
    if not key:
        return None
    path = deploykey.write_key_file(key, Path(env["HOME"]) if env.get("HOME") else None)
    rewrite = f"url.git@{GITHUB_HOST}:.insteadOf=https://{GITHUB_HOST}/"
    return GitAuth(config=(rewrite,), env=deploykey.git_ssh_env(path))


def _auth_for(
    ws: Path | None, remote: str, auth: GitAuth | None, environ: Mapping[str, str] | None = None
) -> GitAuth | None:
    """``auth`` as given, else the environment's token for the remote's URL (a name or a URL)."""
    if auth is not None:
        return auth
    url = remote
    if "://" not in remote and "@" not in remote and ws is not None:
        # the URL as configured (``remote get-url`` would show a ``url.<base>.insteadOf`` rewrite)
        proc = run_git(ws, "config", "--get", f"remote.{remote}.url", check=False)
        url = proc.stdout.strip() if proc.returncode == 0 else ""
    return auth_from_env(environ, remote_url=url, ws=ws)


def check_remote_url(url: str) -> None:
    """Refuse a remote URL that embeds credentials."""
    if not url:
        raise UsageError("Remote URL is empty")
    if "://" in url:
        parts = urlsplit(url)
        if parts.password or (parts.username and parts.scheme in ("http", "https")):
            raise RefusedError(
                "Remote URL embeds credentials; use a credential helper instead "
                "(tokens never go in a remote URL)"
            )


def parse_remote(url: str) -> tuple[str, str, str]:
    """``(host, owner, repo)`` from an ssh or https GitHub-style remote URL."""
    m = re.fullmatch(r"[\w.-]+@([\w.-]+):([\w.-]+)/([\w.-]+?)(?:\.git)?/?", url)
    if not m:
        m = re.fullmatch(
            r"(?:https?|ssh|git)://(?:[^@/]+@)?([\w.-]+)(?::\d+)?/([\w.-]+)/([\w.-]+?)(?:\.git)?/?",
            url,
        )
    if not m:
        raise UsageError(f"Cannot parse owner/repo from remote URL: {url}")
    return m.group(1), m.group(2), m.group(3)


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------


def run_git(
    cwd: Path | None,
    *args: str,
    auth: GitAuth | None = None,
    identity: tuple[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run git. ``cwd`` None runs in the current directory (for clone)."""
    cmd = ["git"]
    if identity:
        cmd += ["-c", f"user.name={identity[0]}", "-c", f"user.email={identity[1]}"]
    if auth:
        for item in auth.config:
            cmd += ["-c", item]
    cmd += list(args)
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if auth:
        env.update(auth.env)
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise UsageError(f"git {args[0] if args else ''} failed: {proc.stderr.strip()}")
    return proc


def _out(ws: Path, *args: str, **kw: Any) -> str:
    return run_git(ws, *args, **kw).stdout.strip()


def init_repo(ws: Path, default_branch: str = "main") -> None:
    """``git init`` with the default branch name (idempotent; refuses ``default``)."""
    bws.assert_not_default(ws)
    if not ws.is_dir():
        raise UsageError(f"Workspace not found: {ws}")
    if (ws / ".git").exists():
        return
    run_git(ws, "init", "-q")
    run_git(ws, "symbolic-ref", "HEAD", f"refs/heads/{default_branch}")


def clone(
    url: str,
    dest: Path,
    *,
    branch: str | None = None,
    auth: GitAuth | None = None,
) -> Path:
    """Clone a client repo into ``dest`` (``workspaces/<env>/``), check ``env.json``, write ``.env``.

    On an ``env.json`` mismatch the fresh clone is removed and ``UsageError`` raised.
    """
    bws.assert_not_default(dest)
    check_remote_url(url)
    if dest.exists():
        raise UsageError(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "-q"]
    if branch:
        args += ["--branch", branch]
    run_git(None, *args, url, str(dest), auth=auth or auth_from_env(remote_url=url, ws=dest))
    try:
        bws.require_env_json_match(dest)
    except UsageError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    bws.write_env_template(dest)
    return dest


def add_remote(ws: Path, url: str, name: str = "origin") -> None:
    check_remote_url(url)
    existing = run_git(ws, "remote", "get-url", name, check=False)
    if existing.returncode == 0:
        if existing.stdout.strip() == url:
            return
        run_git(ws, "remote", "set-url", name, url)
    else:
        run_git(ws, "remote", "add", name, url)


def remote_url(ws: Path, name: str = "origin") -> str:
    return _out(ws, "remote", "get-url", name)


def current_branch(ws: Path) -> str | None:
    """The checked-out branch, or None on a detached HEAD (``cloud bootstrap`` leaves one)."""
    proc = run_git(ws, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def current_sha(ws: Path, ref: str = "HEAD") -> str:
    return _out(ws, "rev-parse", "--verify", f"{ref}^{{commit}}")


def create_branch(ws: Path, name: str, start: str | None = None, *, checkout: bool = True) -> None:
    if checkout:
        run_git(ws, "checkout", "-q", "-b", name, *([start] if start else []))
    else:
        run_git(ws, "branch", name, *([start] if start else []))


def checkout(ws: Path, ref: str) -> None:
    run_git(ws, "checkout", "-q", ref)


def fetch(ws: Path, remote: str = "origin", *, auth: GitAuth | None = None) -> None:
    run_git(ws, "fetch", "-q", "--tags", "--prune", remote, auth=_auth_for(ws, remote, auth))


def ls_remote_heads(ws: Path, remote: str = "origin", *, auth: GitAuth | None = None) -> list[str]:
    """Branch names on the remote (``git ls-remote --heads``)."""
    out = run_git(ws, "ls-remote", "--heads", remote, auth=_auth_for(ws, remote, auth)).stdout
    return [ln.split("\trefs/heads/", 1)[1] for ln in out.splitlines() if "\trefs/heads/" in ln]


@dataclass
class RepoStatus:
    branch: str
    sha: str
    upstream: str | None
    ahead: int
    behind: int
    dirty: bool
    changed: list[str]
    untracked: list[str]
    untracked_builder: list[str]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def status(ws: Path) -> RepoStatus:
    """Branch, ahead/behind of the upstream, dirty state and untracked Builder files."""
    branch = current_branch(ws) or ""  # "" on a detached HEAD
    has_commit = run_git(ws, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0
    sha = current_sha(ws) if has_commit else ""
    upstream: str | None = None
    ahead = behind = 0
    up = run_git(ws, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    if up.returncode == 0:
        upstream = up.stdout.strip()
        counts = _out(ws, "rev-list", "--left-right", "--count", f"HEAD...{upstream}").split()
        ahead, behind = int(counts[0]), int(counts[1])
    porcelain = run_git(ws, "status", "--porcelain=v1", "-z", "-uall").stdout
    changed: list[str] = []
    untracked: list[str] = []
    for entry in (e for e in porcelain.split("\0") if e):
        code, path = entry[:2], entry[3:]
        (untracked if code == "??" else changed).append(path)
    return RepoStatus(
        branch=branch,
        sha=sha,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        dirty=bool(changed or untracked),
        changed=changed,
        untracked=untracked,
        untracked_builder=[p for p in untracked if p.startswith(_BUILDER_PREFIXES)],
    )


def commit(
    ws: Path,
    message: str,
    *,
    paths: Sequence[str] | None = None,
    identity: tuple[str, str] | None = None,
    allow_empty: bool = False,
) -> str:
    """Commit and return the new sha. With ``paths`` ONLY those paths are committed
    (``git commit --only``): anything else already staged in the index stays staged and out of
    the commit. Without ``paths`` everything not ignored is committed.

    The workspace pre-commit hook, if installed, runs and can refuse. This never switches
    branches: a caller that may be on the default branch calls :func:`ensure_work_branch` first.
    """
    run_git(ws, "add", "--", *(paths or ["."]))
    args = ["commit", "-q", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    if paths:
        args += ["--only", "--", *paths]
    proc = run_git(ws, *args, identity=identity, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RefusedError(f"git commit refused: {detail}")
    return current_sha(ws)


def ensure_work_branch(ws: Path, topic: str, *, default_branch: str | None = None) -> str | None:
    """No abcd command commits on the default branch (PLAN.md section 5). On the default branch
    (or a detached HEAD) create and switch to ``builder/<topic>-<yyyymmdd>-<short>`` and return
    its name; on any other branch do nothing and return None. Working-tree changes carry over."""
    current = current_branch(ws)
    if current and current != default_branch_of(ws, default_branch):
        return None
    has_head = run_git(ws, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0
    short = current_sha(ws)[:7] if has_head else "init"
    base = f"builder/{slug(topic, strict=True, maxlen=48) or 'change'}-{datetime.now(UTC):%Y%m%d}-{short}"
    name, n = base, 1
    while run_git(ws, "rev-parse", "--verify", "-q", f"refs/heads/{name}", check=False).stdout:
        n += 1
        name = f"{base}-{n}"
    create_branch(ws, name)
    return name


def default_branch_of(ws: Path, override: str | None = None) -> str:
    if override:
        return override
    if bws.builder_json_path(ws).is_file():
        return str(bws.load_builder_config(ws)["default_branch"])
    return "main"


def push(
    ws: Path,
    branch: str | None = None,
    *,
    remote: str = "origin",
    auth: GitAuth | None = None,
    default_branch: str | None = None,
    allow_default: bool = False,
) -> None:
    """Push a branch (sets upstream). Refuses the default branch unless ``allow_default``.

    Only ``abcd ws init`` (the first commit) passes ``allow_default``: no other command
    changes the default branch (PLAN section 5).
    """
    branch = branch or current_branch(ws)
    if not branch:
        raise UsageError("Detached HEAD: name a branch to push")
    if branch == default_branch_of(ws, default_branch) and not allow_default:
        raise RefusedError(
            f"Refusing to push the default branch '{branch}'; push a branch and open a pull request"
        )
    run_git(ws, "push", "-q", "-u", remote, f"{branch}:{branch}", auth=_auth_for(ws, remote, auth))


def create_tag(
    ws: Path,
    name: str,
    sha: str | None = None,
    *,
    message: str | None = None,
    identity: tuple[str, str] | None = None,
) -> str:
    """Annotated tag at ``sha`` (default HEAD). Refuses to move an existing tag."""
    target = current_sha(ws, sha or "HEAD")
    if run_git(ws, "rev-parse", "-q", "--verify", f"refs/tags/{name}", check=False).returncode == 0:
        if current_sha(ws, f"refs/tags/{name}") == target:
            return target
        raise RefusedError(f"Tag {name} already exists at a different commit")
    run_git(ws, "tag", "-a", name, "-m", message or name, target, identity=identity)
    return target


def push_tag(ws: Path, name: str, *, remote: str = "origin", auth: GitAuth | None = None) -> None:
    run_git(
        ws,
        "push",
        "-q",
        remote,
        f"refs/tags/{name}:refs/tags/{name}",
        auth=_auth_for(ws, remote, auth),
    )


def list_tags(ws: Path, pattern: str = "*") -> list[str]:
    out = _out(ws, "tag", "--list", pattern, "--sort=version:refname")
    return out.splitlines()


def is_on_default_branch(
    ws: Path,
    sha: str,
    *,
    default_branch: str | None = None,
    remote: str = "origin",
    do_fetch: bool = True,
    auth: GitAuth | None = None,
) -> bool:
    """Whether ``sha`` is merged into ``<remote>/<default>`` (a release has been through a PR)."""
    branch = default_branch_of(ws, default_branch)
    if do_fetch:
        fetch(ws, remote, auth=auth)
    target = current_sha(ws, sha)
    proc = run_git(
        ws, "merge-base", "--is-ancestor", target, f"refs/remotes/{remote}/{branch}", check=False
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise UsageError(f"Cannot compare with {remote}/{branch}: {proc.stderr.strip()}")


def ensure_pushed(
    ws: Path,
    sha: str,
    *,
    remote: str = "origin",
    do_fetch: bool = True,
    auth: GitAuth | None = None,
) -> list[str]:
    """Return the remote branches containing ``sha``; refuse when there are none."""
    if do_fetch:
        fetch(ws, remote, auth=auth)
    target = current_sha(ws, sha)
    out = _out(ws, "branch", "-r", "--contains", target)
    branches = [
        b.strip() for b in out.splitlines() if b.strip().startswith(f"{remote}/") and "->" not in b
    ]
    if not branches:
        raise RefusedError(f"Commit {target[:12]} is not pushed to {remote}; push the branch first")
    return branches


# ---------------------------------------------------------------------------
# GitHub: REST client
# ---------------------------------------------------------------------------


class GitHubClient:
    """Minimal GitHub REST client (pull requests and releases) for one repo."""

    def __init__(
        self,
        token: str,
        owner: str,
        repo: str,
        *,
        api_url: str = GITHUB_API,
        session: Any = None,
        timeout: float = 30.0,
    ) -> None:
        if not token:
            raise UsageError("No GitHub token provided")
        self._token = token
        self.owner = owner
        self.repo = repo
        self.api_url = api_url.rstrip("/")
        self._session = session or requests.Session()
        self._timeout = timeout

    def _request(self, method: str, url: str, **kw: Any) -> Any:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **kw.pop("headers", {}),
        }
        try:
            resp = self._session.request(method, url, headers=headers, timeout=self._timeout, **kw)
        except requests.RequestException as e:
            raise PlatformError(f"GitHub request failed: {type(e).__name__}") from e
        if resp.status_code >= 400:
            message = ""
            try:
                message = str(resp.json().get("message", ""))
            except Exception:
                pass
            raise PlatformError(
                f"GitHub {method} {url.split('?')[0]} -> {resp.status_code} {message}"
            )
        return resp.json() if resp.content else {}

    @property
    def _repo_url(self) -> str:
        return f"{self.api_url}/repos/{self.owner}/{self.repo}"

    def create_pr(
        self, *, head: str, base: str, title: str, body: str = "", draft: bool = False
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            f"{self._repo_url}/pulls",
            json={"head": head, "base": base, "title": title, "body": body, "draft": draft},
        )
        return {"url": data["html_url"], "number": data["number"]}

    def find_pr(self, head: str, *, state: str = "open") -> dict[str, Any] | None:
        qualified = head if ":" in head else f"{self.owner}:{head}"
        data = self._request(
            "GET", f"{self._repo_url}/pulls", params={"head": qualified, "state": state}
        )
        if not data:
            return None
        return {"url": data[0]["html_url"], "number": data[0]["number"]}

    def create_release(
        self,
        tag: str,
        *,
        name: str | None = None,
        body: str = "",
        target: str | None = None,
        assets: Sequence[Path] = (),
        prerelease: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tag_name": tag,
            "name": name or tag,
            "body": body,
            "prerelease": prerelease,
        }
        if target:
            payload["target_commitish"] = target
        rel = self._request("POST", f"{self._repo_url}/releases", json=payload)
        for asset in assets:
            self._upload_asset(rel["upload_url"], asset)
        return {"url": rel["html_url"], "id": rel["id"], "assets": [a.name for a in assets]}

    def _upload_asset(self, upload_url: str, path: Path) -> None:
        base = upload_url.split("{")[0]
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._request(
            "POST",
            f"{base}?name={quote(path.name)}",
            data=path.read_bytes(),
            headers={"Content-Type": ctype},
        )


# ---------------------------------------------------------------------------
# GitHub: gh CLI
# ---------------------------------------------------------------------------


def gh_authenticated() -> bool:
    """``gh`` is on PATH and logged in."""
    if shutil.which("gh") is None:
        return False
    proc = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, check=False)
    return proc.returncode == 0


class GhCli:
    """The same operations as :class:`GitHubClient`, through the local ``gh`` CLI."""

    def __init__(self, owner: str, repo: str) -> None:
        self.owner = owner
        self.repo = repo

    def _gh(self, *args: str) -> str:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL
        )
        if proc.returncode != 0:
            raise PlatformError(
                f"gh {args[0]} {args[1] if len(args) > 1 else ''} failed: {proc.stderr.strip()}"
            )
        return proc.stdout.strip()

    @property
    def _slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    def create_pr(
        self, *, head: str, base: str, title: str, body: str = "", draft: bool = False
    ) -> dict[str, Any]:
        args = [
            "pr", "create", "--repo", self._slug, "--head", head, "--base", base,
            "--title", title, "--body", body,
        ]  # fmt: skip
        if draft:
            args.append("--draft")
        url = self._gh(*args).splitlines()[-1].strip()
        m = re.search(r"/pull/(\d+)$", url)
        return {"url": url, "number": int(m.group(1)) if m else None}

    def find_pr(self, head: str, *, state: str = "open") -> dict[str, Any] | None:
        out = self._gh(
            "pr", "list", "--repo", self._slug, "--head", head.split(":")[-1],
            "--state", state, "--json", "url,number",
        )  # fmt: skip
        data = json.loads(out or "[]")
        return {"url": data[0]["url"], "number": data[0]["number"]} if data else None

    def create_release(
        self,
        tag: str,
        *,
        name: str | None = None,
        body: str = "",
        target: str | None = None,
        assets: Sequence[Path] = (),
        prerelease: bool = False,
    ) -> dict[str, Any]:
        args = [
            "release", "create", tag, "--repo", self._slug,
            "--title", name or tag, "--notes", body,
        ]  # fmt: skip
        if target:
            args += ["--target", target]
        if prerelease:
            args.append("--prerelease")
        args += [str(a) for a in assets]
        url = self._gh(*args).splitlines()[-1].strip()
        return {"url": url, "id": None, "assets": [a.name for a in assets]}


def github_backend(
    remote: str,
    *,
    token: str | None = None,
    prefer: str | None = None,
    api_url: str = GITHUB_API,
    session: Any = None,
    ws: Path | None = None,
) -> GitHubClient | GhCli:
    """Pick ``gh`` (present and authenticated) or the REST API (token) for a remote URL.

    A deploy key (``GITHUB_DEPLOY_KEY``) only does git: with no token and no ``gh`` this raises
    ``UsageError``, which callers record as a flag ("pull request / release not created").

    ``prefer`` is ``"gh"`` or ``"rest"`` to force one. The REST client reads the token
    from ``token`` or the environment (``ABCD_GITHUB_TOKEN`` and the usual fallbacks).
    """
    _host, owner, repo = parse_remote(remote)
    if prefer not in (None, "gh", "rest"):
        raise UsageError(f"Unknown GitHub backend: {prefer}")
    if prefer == "gh" or (prefer is None and token is None and gh_authenticated()):
        if not gh_authenticated():
            raise UsageError("gh is not installed or not logged in")
        return GhCli(owner, repo)
    tok = token or token_from_env(ws=ws)
    if not tok:
        raise UsageError(
            "No way to reach the GitHub API: gh is unavailable and no token is set "
            f"({TOKEN_ENV_VAR}, or GITHUB_TOKEN in a cloud run). A deploy key can push branches "
            "but cannot open pull requests or releases"
        )
    return GitHubClient(tok, owner, repo, api_url=api_url, session=session)
