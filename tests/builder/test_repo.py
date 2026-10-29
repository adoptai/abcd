"""Tests for cli.builder_common.repo (local bare repos only, no network)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cli.builder_common import repo
from cli.builder_common import workspace as bws
from cli.builder_common.errors import PlatformError, RefusedError, UsageError

ID = ("Test User", "test@example.test")
SECRET = "ghp_" + "A1b2C3d4E5" * 4


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.test", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    p = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(p)], check=True)
    return p


def make_ws(root: Path, name: str = "acme") -> Path:
    d = root / name
    d.mkdir()
    (d / "env.json").write_text(json.dumps({"env_id": name, "name": "Acme", "target": "staging"}))
    return d


@pytest.fixture
def ws(tmp_path: Path, bare: Path) -> Path:
    d = make_ws(tmp_path)
    repo.init_repo(d, "main")
    bws.init_layout(d, repo_remote=str(bare))
    repo.add_remote(d, str(bare))
    repo.commit(d, "init", identity=ID)
    repo.push(d, "main", allow_default=True)
    return d


def test_init_repo_default_branch_and_refuse_default(tmp_path: Path) -> None:
    d = make_ws(tmp_path)
    repo.init_repo(d, "trunk")
    assert repo.current_branch(d) == "trunk"
    repo.init_repo(d, "other")  # idempotent
    assert repo.current_branch(d) == "trunk"
    default = tmp_path / "default"
    default.mkdir()
    with pytest.raises(RefusedError):
        repo.init_repo(default)


def test_commit_push_status(ws: Path, bare: Path) -> None:
    st = repo.status(ws)
    assert st.branch == "main" and st.upstream == "origin/main"
    assert (st.ahead, st.behind, st.dirty) == (0, 0, False)
    assert git(bare, "rev-parse", "main") == repo.current_sha(ws)

    (ws / "harness/skills/new").mkdir()
    (ws / "harness/skills/new/SKILL.md").write_text("x")
    (ws / "notes.txt").write_text("n")
    (ws / ".env").write_text("A=1")  # ignored, must not appear
    st = repo.status(ws)
    assert st.dirty and "harness/skills/new/SKILL.md" in st.untracked_builder
    assert "notes.txt" in st.untracked and "notes.txt" not in st.untracked_builder
    assert ".env" not in st.untracked

    (ws / "ADOPT.md").write_text("changed")
    assert "ADOPT.md" in repo.status(ws).changed


def test_ahead_behind(ws: Path, bare: Path, tmp_path: Path) -> None:
    (ws / "a.txt").write_text("a")
    repo.commit(ws, "local", identity=ID)
    st = repo.status(ws)
    assert (st.ahead, st.behind) == (1, 0)

    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    (other / "b.txt").write_text("b")
    git(other, "add", ".")
    git(other, "commit", "-q", "-m", "remote")
    git(other, "push", "-q", "origin", "main")
    repo.fetch(ws)
    st = repo.status(ws)
    assert (st.ahead, st.behind) == (1, 1)


def test_push_refuses_default_branch(ws: Path) -> None:
    (ws / "a.txt").write_text("a")
    repo.commit(ws, "x", identity=ID)
    with pytest.raises(RefusedError):
        repo.push(ws, "main")
    repo.create_branch(ws, "feat/x")
    repo.push(ws)
    assert repo.current_branch(ws) == "feat/x"


def test_default_branch_check_and_ensure_pushed(ws: Path, bare: Path) -> None:
    repo.create_branch(ws, "feat/x")
    (ws / "a.txt").write_text("a")
    sha = repo.commit(ws, "feature", identity=ID)
    with pytest.raises(RefusedError):
        repo.ensure_pushed(ws, sha)
    repo.push(ws)
    assert repo.ensure_pushed(ws, sha) == ["origin/feat/x"]
    assert not repo.is_on_default_branch(ws, sha)
    # merge on the remote side (simulating a reviewed PR)
    merger = ws.parent / "merger"
    subprocess.run(["git", "clone", "-q", str(bare), str(merger)], check=True)
    git(merger, "merge", "-q", "--ff-only", "origin/feat/x")
    git(merger, "push", "-q", "origin", "HEAD:main")
    assert repo.is_on_default_branch(ws, sha)
    assert repo.is_on_default_branch(ws, sha, default_branch="main", do_fetch=False)


def test_tags(ws: Path, bare: Path) -> None:
    sha = repo.current_sha(ws)
    assert repo.create_tag(ws, "acme-prep@v1-rc", sha, identity=ID) == sha
    assert repo.create_tag(ws, "acme-prep@v1-rc", sha, identity=ID) == sha  # same commit: ok
    (ws / "a.txt").write_text("a")
    new = repo.commit(ws, "more", identity=ID)
    with pytest.raises(RefusedError):
        repo.create_tag(ws, "acme-prep@v1-rc", new, identity=ID)
    repo.push_tag(ws, "acme-prep@v1-rc")
    assert git(bare, "tag", "--list") == "acme-prep@v1-rc"
    assert git(bare, "rev-list", "-n1", "acme-prep@v1-rc") == sha
    assert repo.list_tags(ws, "acme-prep@*") == ["acme-prep@v1-rc"]


def test_clone_writes_env_and_checks_env_json(tmp_path: Path, ws: Path, bare: Path) -> None:
    dest = tmp_path / "workspaces" / "acme"
    repo.clone(str(bare), dest)
    assert (dest / "builder.json").is_file()
    env_text = (dest / ".env").read_text()
    assert env_text == bws.env_template("Acme", "staging")
    # mismatched folder name -> removed and refused
    bad = tmp_path / "workspaces" / "zenith"
    with pytest.raises(UsageError):
        repo.clone(str(bare), bad)
    assert not bad.exists()
    with pytest.raises(UsageError):
        repo.clone(str(bare), dest)  # exists
    with pytest.raises(RefusedError):
        repo.clone(str(bare), tmp_path / "default")


def test_remote_url_parsing_and_credential_guard() -> None:
    assert repo.parse_remote("git@github.com:acme/ws-repo.git") == ("github.com", "acme", "ws-repo")
    assert repo.parse_remote("https://github.com/acme/ws.repo") == ("github.com", "acme", "ws.repo")
    assert repo.parse_remote("https://github.com/acme/ws.git/") == ("github.com", "acme", "ws")
    assert repo.parse_remote("ssh://git@github.com/acme/ws.git") == ("github.com", "acme", "ws")
    with pytest.raises(UsageError):
        repo.parse_remote("/tmp/some/path")
    with pytest.raises(RefusedError):
        repo.check_remote_url(f"https://x-access-token:{SECRET}@github.com/acme/ws.git")
    with pytest.raises(RefusedError):
        repo.check_remote_url(f"https://{SECRET}@github.com/acme/ws.git")
    repo.check_remote_url("git@github.com:acme/ws.git")
    repo.check_remote_url("https://github.com/acme/ws.git")


def test_add_remote_rejects_token_url(ws: Path) -> None:
    with pytest.raises(RefusedError):
        repo.add_remote(ws, f"https://u:{SECRET}@github.com/acme/ws.git", "upstream")
    repo.add_remote(ws, "https://github.com/acme/ws.git", "upstream")
    repo.add_remote(ws, "https://github.com/acme/other.git", "upstream")  # updates
    assert repo.remote_url(ws, "upstream").endswith("other.git")


def test_token_never_in_argv_or_remote(
    ws: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = repo.git_env_with_token(SECRET)
    assert SECRET not in " ".join(auth.config) and SECRET not in repr(auth)
    assert auth.env[repo.TOKEN_ENV_VAR] == SECRET
    assert auth.config[0] == "credential.https://github.com.helper="  # scoped to the host
    assert f"${repo.TOKEN_ENV_VAR}" in auth.config[1]

    calls: list[tuple[list[str], dict[str, str]]] = []
    real_run = subprocess.run

    def spy(cmd: Any, **kw: Any) -> Any:
        calls.append((list(cmd), dict(kw.get("env") or {})))
        return real_run(cmd, **kw)

    monkeypatch.setattr(repo.subprocess, "run", spy)
    repo.create_branch(ws, "feat/tok")
    (ws / "a.txt").write_text("a")
    repo.commit(ws, "t", identity=ID)
    repo.push(ws, auth=auth)
    repo.fetch(ws, auth=auth)
    repo.ensure_pushed(ws, "HEAD", auth=auth)

    assert calls
    assert all(SECRET not in a for argv, _ in calls for a in argv)
    pushes = [(argv, env) for argv, env in calls if "push" in argv]
    assert pushes and all(
        env.get(repo.TOKEN_ENV_VAR) == SECRET and "credential.https://github.com.helper=" in argv
        for argv, env in pushes
    )
    assert SECRET not in repo.remote_url(ws)
    assert SECRET not in (ws / ".git" / "config").read_text()


def test_credential_helper_hands_token_to_git(ws: Path) -> None:
    """The helper prints the token from the environment when git asks for credentials."""
    auth = repo.git_env_with_token(SECRET)
    proc = subprocess.run(
        ["git", *sum((["-c", c] for c in auth.config), []), "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, cwd=ws, env={**os.environ, **auth.env},
    )  # fmt: skip
    assert f"password={SECRET}" in proc.stdout and "username=x-access-token" in proc.stdout


def test_git_env_validation() -> None:
    with pytest.raises(UsageError):
        repo.git_env_with_token("")
    with pytest.raises(UsageError):
        repo.git_env_with_token("t", "bad name; rm")


def test_token_from_env() -> None:
    assert repo.token_from_env({}) is None
    cloud = {"ADOPT_CONVERSATION_ID": "c-1"}
    assert repo.token_from_env({**cloud, "GITHUB_TOKEN": "b", "SANDBOX_GITHUB_TOKEN": "a"}) == "a"
    assert repo.token_from_env({"GITHUB_TOKEN": "b", "GH_TOKEN": "g"}) is None  # local: ignored
    assert repo.token_from_env({"ABCD_GITHUB_TOKEN": "c", "SANDBOX_GITHUB_TOKEN": "a"}) == "c"


# ---------------------------------------------------------------------------
# GitHub REST (mocked) and gh (fake executable)
# ---------------------------------------------------------------------------


class FakeResp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload
        self.content = b"x" if payload is not None else b""

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResp]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kw: Any) -> FakeResp:
        self.calls.append({"method": method, "url": url, **kw})
        return self.responses.pop(0)


def test_github_client_pr_and_find() -> None:
    sess = FakeSession(
        [
            FakeResp(201, {"html_url": "https://gh.test/acme/ws/pull/7", "number": 7}),
            FakeResp(200, [{"html_url": "https://gh.test/acme/ws/pull/7", "number": 7}]),
            FakeResp(200, []),
        ]
    )
    c = repo.GitHubClient(SECRET, "acme", "ws", session=sess)
    pr = c.create_pr(head="release/a@v1", base="main", title="T", body="B")
    assert pr == {"url": "https://gh.test/acme/ws/pull/7", "number": 7}
    first = sess.calls[0]
    assert (
        first["method"] == "POST" and first["url"] == "https://api.github.com/repos/acme/ws/pulls"
    )
    assert first["json"]["head"] == "release/a@v1" and first["json"]["base"] == "main"
    assert first["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert c.find_pr("release/a@v1") == pr
    assert sess.calls[1]["params"] == {"head": "acme:release/a@v1", "state": "open"}
    assert c.find_pr("nope") is None


def test_github_client_release_with_asset(tmp_path: Path) -> None:
    report = tmp_path / "report.html"
    report.write_text("<html></html>")
    sess = FakeSession(
        [
            FakeResp(
                201,
                {
                    "html_url": "https://gh.test/acme/ws/releases/1",
                    "id": 1,
                    "upload_url": "https://uploads.gh.test/repos/acme/ws/releases/1/assets{?name,label}",
                },
            ),
            FakeResp(201, {"id": 2}),
        ]
    )
    c = repo.GitHubClient(SECRET, "acme", "ws", session=sess)
    rel = c.create_release("a@v1", body="notes", target="abc123", assets=[report])
    assert rel["url"].endswith("/releases/1") and rel["assets"] == ["report.html"]
    assert sess.calls[0]["json"] == {
        "tag_name": "a@v1", "name": "a@v1", "body": "notes", "prerelease": False,
        "target_commitish": "abc123",
    }  # fmt: skip
    up = sess.calls[1]
    assert up["url"] == "https://uploads.gh.test/repos/acme/ws/releases/1/assets?name=report.html"
    assert up["data"] == b"<html></html>" and up["headers"]["Content-Type"] == "text/html"


def test_github_client_errors_hide_token() -> None:
    sess = FakeSession([FakeResp(422, {"message": "Validation Failed"})])
    c = repo.GitHubClient(SECRET, "acme", "ws", session=sess)
    with pytest.raises(PlatformError) as ei:
        c.create_pr(head="h", base="b", title="t")
    assert "422" in str(ei.value) and SECRET not in str(ei.value)
    with pytest.raises(UsageError):
        repo.GitHubClient("", "a", "b")


def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, authed: bool = True) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    script = bin_dir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        'if [ "$1" = "auth" ]; then exit ' + ("0" if authed else "1") + " ; fi\n"
        'if [ "$1" = "pr" ] && [ "$2" = "create" ]; then echo https://gh.test/acme/ws/pull/12; exit 0; fi\n'
        'if [ "$1" = "pr" ] && [ "$2" = "list" ]; then echo \'[{"url":"https://gh.test/acme/ws/pull/3","number":3}]\'; exit 0; fi\n'
        'if [ "$1" = "release" ]; then echo https://gh.test/acme/ws/releases/tag/t; exit 0; fi\n'
        "exit 2\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


def test_gh_backend_selected_and_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = fake_gh(tmp_path, monkeypatch)
    monkeypatch.delenv("ABCD_GITHUB_TOKEN", raising=False)
    b = repo.github_backend("git@github.com:acme/ws.git")
    assert isinstance(b, repo.GhCli)
    pr = b.create_pr(head="release/a@v1", base="main", title="T", body="B", draft=True)
    assert pr == {"url": "https://gh.test/acme/ws/pull/12", "number": 12}
    assert b.find_pr("release/a@v1") == {"url": "https://gh.test/acme/ws/pull/3", "number": 3}
    asset = tmp_path / "r.html"
    asset.write_text("x")
    rel = b.create_release("a@v1", name="A v1", body="n", target="abc", assets=[asset])
    assert rel["url"].endswith("/releases/tag/t")
    lines = log.read_text().splitlines()
    assert any(
        ln.startswith("pr create --repo acme/ws --head release/a@v1 --base main")
        and "--draft" in ln
        for ln in lines
    )
    assert any(
        ln.startswith("release create a@v1 --repo acme/ws") and str(asset) in ln for ln in lines
    )


def test_rest_selected_when_gh_missing_or_unauthed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))  # no gh at all
    monkeypatch.delenv("ABCD_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("SANDBOX_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(UsageError):
        repo.github_backend("https://github.com/acme/ws.git")
    monkeypatch.setenv("ABCD_GITHUB_TOKEN", SECRET)
    b = repo.github_backend("https://github.com/acme/ws.git")
    assert isinstance(b, repo.GitHubClient) and (b.owner, b.repo) == ("acme", "ws")

    fake_gh(tmp_path, monkeypatch, authed=False)
    assert isinstance(repo.github_backend("https://github.com/acme/ws.git"), repo.GitHubClient)
    with pytest.raises(UsageError):
        repo.github_backend("https://github.com/acme/ws.git", prefer="gh")
    with pytest.raises(UsageError):
        repo.github_backend("https://github.com/acme/ws.git", prefer="svn")


def test_explicit_token_prefers_rest_even_with_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch)
    b = repo.github_backend("https://github.com/acme/ws.git", token=SECRET)
    assert isinstance(b, repo.GitHubClient)


# ---------------------------------------------------------------------------
# GITHUB_DEPLOY_KEY (cloud runs without a token)
# ---------------------------------------------------------------------------

KEY_TEXT = "-----BEGIN OPENSSH PRIVATE KEY-----\nQUJDREVGR0hJSktMTU5PUA\n-----END OPENSSH PRIVATE KEY-----\n"


def _cloud_key_env(home: Path) -> dict[str, str]:
    return {"ADOPT_CONVERSATION_ID": "c-1", "GITHUB_DEPLOY_KEY": KEY_TEXT, "HOME": str(home)}


def test_auth_from_env_deploy_key_in_cloud(tmp_path: Path) -> None:
    auth = repo.auth_from_env(_cloud_key_env(tmp_path), remote_url="git@github.com:acme/ws.git")
    assert auth is not None
    key_file = tmp_path / ".abcd" / "github_deploy_key"
    assert oct(key_file.stat().st_mode & 0o777) == "0o600"
    assert f"-i {key_file}" in auth.env["GIT_SSH_COMMAND"]
    assert "IdentitiesOnly=yes" in auth.env["GIT_SSH_COMMAND"]
    assert "url.git@github.com:.insteadOf=https://github.com/" in auth.config
    assert "PRIVATE KEY" not in " ".join(auth.config) + " ".join(auth.env.values())
    assert "PRIVATE KEY" not in repr(auth)


def test_auth_from_env_deploy_key_scoped_to_github_and_cloud(tmp_path: Path) -> None:
    env = _cloud_key_env(tmp_path)
    assert repo.auth_from_env(env, remote_url="https://example.test/a/b.git") is None
    local = {"GITHUB_DEPLOY_KEY": KEY_TEXT, "HOME": str(tmp_path)}  # not a cloud run
    assert repo.auth_from_env(local, remote_url="git@github.com:acme/ws.git") is None


def test_token_beats_deploy_key_in_auth(tmp_path: Path) -> None:
    env = {**_cloud_key_env(tmp_path), "GITHUB_TOKEN": "tok"}
    auth = repo.auth_from_env(env, remote_url="https://github.com/acme/ws.git")
    assert auth is not None and "GIT_SSH_COMMAND" not in auth.env


def test_push_uses_deploy_key_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(repo.subprocess, "run", fake_run)
    for k, v in _cloud_key_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    for k in ("ABCD_GITHUB_TOKEN", "SANDBOX_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    repo.fetch(tmp_path, "git@github.com:acme/ws.git")
    assert "-i " in seen["env"]["GIT_SSH_COMMAND"]
    assert "PRIVATE KEY" not in " ".join(seen["cmd"])


def test_github_api_flagged_with_deploy_key_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))  # no gh
    for k, v in _cloud_key_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    for k in ("ABCD_GITHUB_TOKEN", "SANDBOX_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(UsageError, match="deploy key can push branches"):
        repo.github_backend("git@github.com:acme/ws.git")
