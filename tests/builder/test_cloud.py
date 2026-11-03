"""cli/builder_common/cloud.py: bootstrap with fakes, no network."""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from cli.builder_common import cloud, deploykey
from cli.builder_common.errors import PlatformError, UsageError

TOKEN = "tok-" + "A1b2C3d4E5f6G7h8"
PAT_SECRET = "pat-" + "Z9y8X7w6V5u4T3s2"


def _env(**over: str) -> dict[str, str]:
    base = {
        "ABCD_PAT_CLIENT_ID": "client-id-1",
        "ABCD_PAT_SECRET": PAT_SECRET,
        "GITHUB_TOKEN": TOKEN,
        "ADOPT_APP_BASE_URL": "https://app.example.test/",
        "ADOPT_CONVERSATION_ID": "conv-1",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v != ""}


@pytest.fixture
def ws_file(tmp_path: Path) -> Path:
    p = tmp_path / "abcd-workspace.json"
    p.write_text(json.dumps({"env": "acme-prod", "repo": "acme-org/acme-workspace", "ref": "main"}))
    return p


class GitRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def __call__(self, args: list[str], cwd: Path, env: Mapping[str, str]) -> None:
        self.calls.append((list(args), cwd, dict(env)))


def test_normalize_env_id() -> None:
    assert cloud.normalize_env_id("acme-prod") == "ACME_PROD"
    assert cloud.sandbox_secret_name("ABCD_PAT_SECRET", "acme-prod") == (
        "SANDBOX_ABCD_PAT_SECRET_ACME_PROD"
    )
    assert cloud.sandbox_secret_name("GITHUB_TOKEN") == "SANDBOX_GITHUB_TOKEN"


def test_missing_vars_all_named(ws_file: Path, tmp_path: Path) -> None:
    git = GitRecorder()
    with pytest.raises(cloud.MissingVariablesError) as exc:
        cloud.bootstrap(ws_file, tmp_path / "abcd", environ={}, run_git=git)
    msg = str(exc.value)
    for name in (
        "ABCD_PAT_CLIENT_ID",
        "ABCD_PAT_SECRET",
        "GITHUB_TOKEN or GITHUB_DEPLOY_KEY",
        "ABCD_WEBUI_ENDPOINT or ADOPT_APP_BASE_URL",
        "ADOPT_CONVERSATION_ID",
    ):
        assert name in msg
    assert exc.value.code == 2
    assert git.calls == []  # nothing cloned


def test_partial_missing_names_only_the_gaps(ws_file: Path, tmp_path: Path) -> None:
    with pytest.raises(cloud.MissingVariablesError) as exc:
        cloud.bootstrap(
            ws_file, tmp_path / "abcd", environ=_env(GITHUB_TOKEN="", ADOPT_CONVERSATION_ID="")
        )
    assert exc.value.missing == ["GITHUB_TOKEN or GITHUB_DEPLOY_KEY", "ADOPT_CONVERSATION_ID"]


def test_endpoint_override_wins() -> None:
    env = cloud.collect_environment(_env(ABCD_WEBUI_ENDPOINT="https://bff.example.test"))
    assert env.webui_endpoint == "https://bff.example.test"
    assert cloud.collect_environment(_env()).webui_endpoint == "https://app.example.test"


def test_repr_masks_secrets() -> None:
    text = repr(cloud.collect_environment(_env()))
    assert TOKEN not in text and PAT_SECRET not in text


def test_bootstrap_ok(ws_file: Path, tmp_path: Path) -> None:
    git = GitRecorder()
    exchanged: list[tuple[str, str, str]] = []
    root = tmp_path / "abcd"
    res = cloud.bootstrap(
        ws_file,
        root,
        environ=_env(),
        exchange_pat=lambda *a: exchanged.append(a),
        run_git=git,
    )
    assert res.workspace_dir == root / "workspaces" / "acme-prod"
    assert res.conversation_id == "conv-1"
    assert exchanged == [("https://app.example.test", "client-id-1", PAT_SECRET)]
    env_text = res.env_file.read_text()
    assert "ADOPT_WEBUI_ENDPOINT=https://app.example.test\n" in env_text
    assert "ADOPT_HARNESS_PAT_CLIENT_ID=client-id-1\n" in env_text
    assert f"ADOPT_HARNESS_PAT_SECRET={PAT_SECRET}\n" in env_text
    assert PAT_SECRET not in json.dumps(res.to_dict())


def test_env_file_mode_is_0600(ws_file: Path, tmp_path: Path) -> None:
    res = cloud.bootstrap(
        ws_file,
        tmp_path / "abcd",
        environ=_env(),
        exchange_pat=lambda *a: None,
        run_git=GitRecorder(),
    )
    assert stat.S_IMODE(res.env_file.stat().st_mode) == 0o600


def test_token_never_in_argv_or_url(ws_file: Path, tmp_path: Path) -> None:
    git = GitRecorder()
    cloud.bootstrap(
        ws_file, tmp_path / "abcd", environ=_env(), exchange_pat=lambda *a: None, run_git=git
    )
    assert git.calls
    for args, _cwd, env in git.calls:
        joined = " ".join(args)
        assert TOKEN not in joined and PAT_SECRET not in joined
        assert "@github.com" not in joined
        assert env["GIT_TERMINAL_PROMPT"] == "0"
    fetch = next(a for a, _, _ in git.calls if "fetch" in a)
    assert any(x.startswith("credential.helper=!") for x in fetch)
    assert git.calls[-1][2]["ABCD_BOOTSTRAP_GIT_TOKEN"] == TOKEN  # only in the child env
    remote = next(a for a, _, _ in git.calls if a[:2] == ["remote", "add"])
    assert remote[-1] == "https://github.com/acme-org/acme-workspace.git"
    assert fetch[-1] == "main"


def test_url_with_credentials_refused() -> None:
    with pytest.raises(UsageError):
        cloud.repo_url("https://user:pw@github.com/acme-org/x.git")
    with pytest.raises(UsageError):
        cloud.repo_url("git@github.com:acme-org/x.git")


def test_failed_pat_exchange_is_platform_error(ws_file: Path, tmp_path: Path) -> None:
    def bad(endpoint: str, cid: str, secret: str) -> None:
        raise PlatformError("PAT exchange was refused")

    with pytest.raises(PlatformError):
        cloud.bootstrap(
            ws_file, tmp_path / "abcd", environ=_env(), exchange_pat=bad, run_git=GitRecorder()
        )


def test_existing_workspace_needs_replace(ws_file: Path, tmp_path: Path) -> None:
    root = tmp_path / "abcd"
    (root / "workspaces" / "acme-prod").mkdir(parents=True)
    (root / "workspaces" / "acme-prod" / "x").write_text("1")
    with pytest.raises(UsageError):
        cloud.bootstrap(ws_file, root, environ=_env(), run_git=GitRecorder())
    cloud.bootstrap(
        ws_file,
        root,
        environ=_env(),
        exchange_pat=lambda *a: None,
        run_git=GitRecorder(),
        replace=True,
    )


def test_workspace_file_validation(tmp_path: Path) -> None:
    bad = tmp_path / "w.json"
    bad.write_text(json.dumps({"env": "acme", "repo": "o/r"}))
    with pytest.raises(UsageError, match="ref"):
        cloud.read_workspace_file(bad)
    bad.write_text("not json")
    with pytest.raises(UsageError):
        cloud.read_workspace_file(bad)
    bad.write_text(json.dumps({"env": "../x", "repo": "o/r", "ref": "main"}))
    with pytest.raises(UsageError):
        cloud.read_workspace_file(bad)


# -- deploy key (an alternative to GITHUB_TOKEN) ----------------------------------------------

KEY_BODY = "b3BlbnNzaC1rZXktdjEAAAAAdGVzdC1rZXktbWF0ZXJpYWw" + "Q" * 20
DEPLOY_KEY = (
    "-----BEGIN " + f"OPENSSH PRIVATE KEY-----\n{KEY_BODY}\n-----END OPENSSH PRIVATE KEY-----\n"
)


def _key_env(**over: str) -> dict[str, str]:
    return _env(GITHUB_TOKEN="", GITHUB_DEPLOY_KEY=DEPLOY_KEY, **over)


def _boot(ws_file: Path, tmp_path: Path, environ: dict[str, str]) -> tuple[Any, GitRecorder, Path]:
    git = GitRecorder()
    home = tmp_path / "home"
    res = cloud.bootstrap(
        ws_file,
        tmp_path / "abcd",
        environ=environ,
        exchange_pat=lambda *a: None,
        run_git=git,
        home=home,
    )
    return res, git, home


def test_missing_credential_message_names_both(ws_file: Path, tmp_path: Path) -> None:
    with pytest.raises(cloud.MissingVariablesError) as exc:
        cloud.bootstrap(ws_file, tmp_path / "abcd", environ=_env(GITHUB_TOKEN=""))
    assert exc.value.missing == ["GITHUB_TOKEN or GITHUB_DEPLOY_KEY"]
    assert "GITHUB_TOKEN or GITHUB_DEPLOY_KEY" in str(exc.value)


def test_deploy_key_alone_satisfies_the_check() -> None:
    env = cloud.collect_environment(_key_env())
    assert env.github_deploy_key == DEPLOY_KEY.strip()
    assert "PRIVATE KEY" not in repr(env)


def test_deploy_key_file_is_0600_under_home_not_workspace(ws_file: Path, tmp_path: Path) -> None:
    res, _git, home = _boot(ws_file, tmp_path, _key_env())
    key_file = home / ".abcd" / "github_deploy_key"
    assert key_file.read_text() == DEPLOY_KEY
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_file.parent.stat().st_mode) == 0o700
    assert res.workspace_dir not in key_file.parents  # not in the workspace


def test_deploy_key_ssh_url_and_git_ssh_command(ws_file: Path, tmp_path: Path) -> None:
    res, git, home = _boot(ws_file, tmp_path, _key_env())
    key_file = home / ".abcd" / "github_deploy_key"
    remote = next(a for a, _, _ in git.calls if a[:2] == ["remote", "add"])
    assert remote[-1] == "git@github.com:acme-org/acme-workspace.git"
    assert res.git_auth == "deploy_key"
    for _args, _cwd, env in git.calls:  # every network-capable call sees the ssh command
        cmd = env["GIT_SSH_COMMAND"]
        assert f"-i {key_file}" in cmd
        assert "-o IdentitiesOnly=yes" in cmd
        assert "-o StrictHostKeyChecking=accept-new" in cmd
        assert "ABCD_BOOTSTRAP_GIT_TOKEN" not in env
    fetch = next(a for a, _, _ in git.calls if "fetch" in a)
    assert not any("credential.helper" in x for x in fetch)  # no token helper
    # later plain `git push` in the workspace works too
    cfg = next(a for a, _, _ in git.calls if a[:2] == ["config", "core.sshCommand"])
    assert str(key_file) in cfg[-1]


def test_deploy_key_never_in_argv_env_values_or_output(ws_file: Path, tmp_path: Path) -> None:
    res, git, _home = _boot(ws_file, tmp_path, _key_env())
    for args, _cwd, env in git.calls:
        assert KEY_BODY not in " ".join(args) and "PRIVATE KEY" not in " ".join(args)
        assert all(KEY_BODY not in v for v in env.values())
    assert KEY_BODY not in json.dumps(res.to_dict())
    assert PAT_SECRET not in json.dumps(res.to_dict())


def test_deploy_key_only_flags_github_api_steps(ws_file: Path, tmp_path: Path) -> None:
    res, _git, _home = _boot(ws_file, tmp_path, _key_env())
    data = res.to_dict()
    assert data["github_api"] == "flagged"
    assert "pull requests" in data["flags"][0] and "flagged" in data["flags"][0]
    tok, _g, _h = _boot(ws_file, tmp_path / "t", _env())
    assert tok.to_dict()["github_api"] == "available" and tok.to_dict()["flags"] == []
    assert tok.git_auth == "token"


def test_token_wins_when_both_are_set(ws_file: Path, tmp_path: Path) -> None:
    res, git, home = _boot(ws_file, tmp_path, _env(GITHUB_DEPLOY_KEY=DEPLOY_KEY))
    assert res.git_auth == "token"
    assert not (home / ".abcd").exists()
    assert next(a for a, _, _ in git.calls if a[:2] == ["remote", "add"])[-1].startswith("https://")


def test_deploy_key_escaped_newlines_are_normalised(tmp_path: Path) -> None:
    flat = DEPLOY_KEY.strip().replace("\n", "\\n")
    path = deploykey.write_key_file(flat, tmp_path)
    assert path.read_text() == DEPLOY_KEY


def test_bad_deploy_key_is_refused_without_echo(tmp_path: Path) -> None:
    with pytest.raises(UsageError) as exc:
        deploykey.write_key_file("not-a-key-SECRETVALUE", tmp_path)
    assert "SECRETVALUE" not in str(exc.value)


def test_to_ssh_url() -> None:
    f = deploykey.to_ssh_url
    assert f("https://github.com/o/r.git") == "git@github.com:o/r.git"
    assert f("https://github.com/o/r") == "git@github.com:o/r.git"
    assert f("https://www.github.com/o/r.git/") == "git@github.com:o/r.git"
    assert f("git@github.com:o/r.git") == "git@github.com:o/r.git"
    assert f("https://gitlab.example/o/r.git") == "https://gitlab.example/o/r.git"


def test_require_git_tools_names_what_is_missing() -> None:
    with pytest.raises(UsageError, match="git and ssh.*apt-get install -y git openssh-client"):
        cloud.require_git_tools(True, which=lambda _t: None)
    with pytest.raises(UsageError, match="needs git in this sandbox"):
        cloud.require_git_tools(False, which=lambda _t: None)
    cloud.require_git_tools(
        False, which=lambda t: "/usr/bin/git" if t == "git" else None
    )  # no ssh needed
    cloud.require_git_tools(True, which=lambda t: f"/usr/bin/{t}")


def test_bootstrap_without_git_is_a_clear_error(
    ws_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cloud.shutil, "which", lambda _t: None)
    with pytest.raises(UsageError, match="apt-get install"):
        cloud.bootstrap(ws_file, tmp_path / "abcd", environ=_env(), exchange_pat=lambda *a: None)
    assert not (tmp_path / "abcd" / "workspaces").exists()


@pytest.mark.parametrize("environ", [_env(), _key_env()])
def test_bootstrap_sets_a_local_git_identity(
    ws_file: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    _res, git, _home = _boot(ws_file, tmp_path, environ)
    cfg = {a[1]: a[2] for a, _, _ in git.calls if a[0] == "config" and len(a) == 3}
    assert cfg["user.name"] == cloud.COMMIT_NAME
    assert cfg["user.email"] == cloud.COMMIT_EMAIL
