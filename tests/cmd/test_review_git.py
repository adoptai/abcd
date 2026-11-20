"""Regression tests for the review findings on git: 4 (commits), 5 (cloud auth), 6 (ws init)."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cli.builder_common import repo
from tests.cmd.quality_fixtures import QEnv, qenv  # noqa: F401
from tests.cmd.release_fixtures import AGENT, build_env, cycle
from tests.cmd.ws_fixtures import bare_repo, git, make_workspace, run, workspaces_env

Capsys = pytest.CaptureFixture[str]
TOKEN = "ghp_" + "Zq9" * 10


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


# -- 4. nothing commits on the default branch, and only its own paths --------------------------


def test_a_command_on_the_default_branch_works_on_a_new_builder_branch(qenv: QEnv) -> None:  # noqa: F811
    ws = qenv.ws
    assert repo.current_branch(ws) == "main"
    main_before = sh(ws, "rev-parse", "main")
    (ws / "unrelated.txt").write_text("someone else's staged work\n")
    sh(ws, "add", "unrelated.txt")

    d = qenv.ok("metric", "define", AGENT, "accuracy", "--category", "quality",
                "--formula", "pass_rate", "--source", "check_results")  # fmt: skip
    branch = d["branch"]
    assert re.fullmatch(r"builder/[a-z0-9-]+-\d{8}-[0-9a-f]{7}", branch), branch
    assert repo.current_branch(ws) == branch
    assert sh(ws, "rev-parse", "main") == main_before  # no commit landed on main
    assert d["commit"] and sh(ws, "rev-parse", "HEAD") == d["commit"]
    # the unrelated staged file was not swept into the commit and is still staged
    assert "unrelated.txt" not in sh(ws, "show", "--name-only", "--format=", "HEAD")
    assert sh(ws, "diff", "--cached", "--name-only") == "unrelated.txt"

    # the next command is already on a work branch: it stays there and creates no other
    d2 = qenv.ok("metric", "define", AGENT, "recall", "--category", "quality",
                 "--formula", "pass_rate", "--source", "check_results")  # fmt: skip
    assert d2["branch"] is None and repo.current_branch(ws) == branch
    assert sh(ws, "rev-parse", "main") == main_before


def test_the_output_says_the_work_moved_to_a_branch(qenv: QEnv, capsys: Capsys) -> None:  # noqa: F811
    from cli import abcd

    capsys.readouterr()
    abcd.main(["metric", "define", AGENT, "m1", "--category", "quality", "--formula",
               "pass_rate", "--source", "check_results", "--env", "acme"])  # fmt: skip
    out = capsys.readouterr().out
    assert "Committed on new branch builder/" in out and "never commits there" in out


@pytest.mark.parametrize(
    "argv",
    [
        ("check", "add", AGENT, "tax-range", "--rule-type", "range", "--field", "total_tax",
         "--min", "0", "--max", "9"),
        ("criteria", "set", AGENT, "--dataset", "returns", "--metric", "pass_rate", "--op", ">=",
         "--target", "0.9"),
        ("dataset", "add-case", "returns", "c9", "--label", "a=b"),
    ],
)  # fmt: skip
def test_every_committing_command_avoids_the_default_branch(
    qenv: QEnv,  # noqa: F811
    argv: tuple[str, ...],
) -> None:
    qenv.make_dataset(2)
    # put the workspace back on the default branch with everything so far merged into it
    work = repo.current_branch(qenv.ws)
    sh(qenv.ws, "checkout", "-q", "main")
    sh(qenv.ws, "merge", "-q", "--ff-only", work)
    main_before = sh(qenv.ws, "rev-parse", "main")
    d = qenv.ok(*argv)
    assert d["branch"] and d["branch"].startswith("builder/")
    assert repo.current_branch(qenv.ws) == d["branch"]
    assert sh(qenv.ws, "rev-parse", "main") == main_before
    assert d["commit"] and sh(qenv.ws, "rev-parse", "HEAD") == d["commit"]


def test_repo_commit_with_paths_never_sweeps_the_index(tmp_path: Path) -> None:
    d = tmp_path / "w"
    d.mkdir()
    sh(d, "init", "-q", "-b", "main") if False else subprocess.run(
        ["git", "init", "-q", "-b", "main", str(d)], check=True
    )
    for k, v in (("user.name", "T"), ("user.email", "t@example.test")):
        sh(d, "config", k, v)
    (d / "a.txt").write_text("a\n")
    (d / "b.txt").write_text("b\n")
    repo.commit(d, "init")
    (d / "a.txt").write_text("a2\n")
    (d / "b.txt").write_text("b2\n")
    (d / "new.txt").write_text("n\n")
    sh(d, "add", "b.txt")  # pre-staged and unrelated
    repo.commit(d, "only a and new", paths=["a.txt", "new.txt"])
    assert sorted(sh(d, "show", "--name-only", "--format=", "HEAD").splitlines()) == [
        "a.txt",
        "new.txt",
    ]
    assert sh(d, "diff", "--cached", "--name-only") == "b.txt"


def test_ensure_work_branch_only_acts_on_the_default_branch(tmp_path: Path) -> None:
    d = tmp_path / "w"
    d.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True)
    for k, v in (("user.name", "T"), ("user.email", "t@example.test")):
        sh(d, "config", k, v)
    (d / "a").write_text("a")
    repo.commit(d, "init")
    (d / "wip.txt").write_text("carried over\n")
    name = repo.ensure_work_branch(d, "Dataset: Q3 returns!")
    assert name and re.fullmatch(r"builder/dataset-q3-returns-\d{8}-[0-9a-f]{7}", name)
    assert (d / "wip.txt").exists()  # the working tree came along
    assert repo.ensure_work_branch(d, "again") is None  # already off the default branch


# -- 5. cloud git calls carry the credential helper, never the token ---------------------------

NETWORK = {"fetch", "push", "ls-remote", "clone"}


@pytest.fixture
def git_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    real = subprocess.run

    def spy(cmd: Any, *a: Any, **kw: Any) -> Any:
        if isinstance(cmd, list) and cmd and cmd[0] == "git":
            words = [w for i, w in enumerate(cmd[1:]) if not w.startswith("-")
                     and (i == 0 or cmd[i] != "-c")]  # fmt: skip
            calls.append({"argv": list(cmd), "sub": words[0] if words else "",
                          "env": dict(kw.get("env") or {})})  # fmt: skip
        return real(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    yield calls


HELPER_KEY = "credential.https://github.com.helper"
GITHUB_URL = "https://github.com/acme/ws.git"


def github_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bare: Path) -> str:
    """Make a local bare repo answer to a github.com URL (git's ``url.<base>.insteadOf``)."""
    cfg = tmp_path / "alias.gitconfig"
    cfg.write_text(f'[url "{bare}"]\n\tinsteadOf = {GITHUB_URL}\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    return GITHUB_URL


def _assert_authenticated(calls: list[dict[str, Any]]) -> None:
    net = [c for c in calls if c["sub"] in NETWORK]
    assert net, "no network git call was made"
    for c in calls:
        assert TOKEN not in " ".join(c["argv"]), c["argv"]  # never in argv
    for c in net:
        assert any(w.startswith(f"{HELPER_KEY}=!f()") for w in c["argv"]), c["argv"]
        assert TOKEN in c["env"].values()  # the token lives in the child environment only


ALL_TOKENS = ("ABCD_GITHUB_TOKEN", "SANDBOX_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")
CLOUD_VARS = ("ADOPT_CONVERSATION_ID", "ABCD_PAT_CLIENT_ID", "ADOPT_APP_BASE_URL")


def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for v in (*ALL_TOKENS, *CLOUD_VARS):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.chdir(tmp_path)  # no .builder/cloud.json above


def test_auth_is_scoped_to_github_and_to_an_explicit_token_or_the_cloud(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clean_env(monkeypatch, tmp_path)
    assert repo.auth_from_env(remote_url=GITHUB_URL) is None  # no token at all
    # a plain GH_TOKEN / GITHUB_TOKEN in a local shell does not switch auth
    monkeypatch.setenv("GH_TOKEN", "gh-token-1")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token-2")
    assert repo.auth_from_env(remote_url=GITHUB_URL) is None
    # ABCD_GITHUB_TOKEN is explicit: used, scoped to github.com, the user's helpers are kept
    monkeypatch.setenv("ABCD_GITHUB_TOKEN", "abcd-token-3")
    a = repo.auth_from_env(remote_url=GITHUB_URL)
    assert a is not None and "abcd-token-3" in a.env.values()
    assert all("abcd-token-3" not in item for item in a.config)
    # an explicit token wins: the machine's helpers are reset for that host
    assert [c.split("=", 1)[0] for c in a.config] == [HELPER_KEY, HELPER_KEY]
    assert a.config[0] == f"{HELPER_KEY}="
    # never another host, an ssh remote on another host, or a local path
    for other in (
        "https://gitlab.com/acme/ws.git", "https://github.com.evil.test/acme/ws.git",
        "git@bitbucket.org:acme/ws.git", "/tmp/some/bare.git", "", None,
    ):  # fmt: skip
        assert repo.auth_from_env(remote_url=other) is None, other
    assert repo.auth_from_env({}, remote_url=GITHUB_URL) is None


def test_cloud_uses_the_sandbox_token_and_resets_the_helper_for_github_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "sandbox-token-1")
    assert repo.auth_from_env(remote_url=GITHUB_URL) is None  # still local
    for var in CLOUD_VARS:  # any one of them makes it a cloud run
        monkeypatch.setenv(var, "x")
        a = repo.auth_from_env(remote_url=GITHUB_URL)
        assert a is not None and "sandbox-token-1" in a.env.values(), var
        assert [c.split("=", 1)[0] for c in a.config] == [HELPER_KEY, HELPER_KEY]
        assert a.config[0] == f"{HELPER_KEY}="  # the machine's helpers are reset for github.com
        assert repo.auth_from_env(remote_url="https://example.test/a/b.git") is None
        monkeypatch.delenv(var)
    # the marker file alone is a cloud run too
    (tmp_path / ".builder").mkdir()
    (tmp_path / ".builder" / "cloud.json").write_text("{}")
    assert repo.auth_from_env(remote_url=GITHUB_URL) is not None


def test_a_helper_is_never_offered_for_another_host(tmp_path: Path) -> None:
    """git itself: the host-scoped helper answers for github.com and stays silent elsewhere."""
    auth = repo.git_env_with_token(TOKEN)
    d = tmp_path / "w"
    d.mkdir()
    sh(d, "init", "-q")

    def fill(host: str) -> str:
        return subprocess.run(
            ["git", *sum((["-c", c] for c in auth.config), []), "credential", "fill"],
            input=f"protocol=https\nhost={host}\n\n", capture_output=True, text=True, cwd=d,
            env={**__import__("os").environ, **auth.env}, timeout=30,
        ).stdout  # fmt: skip

    assert f"password={TOKEN}" in fill("github.com")
    assert TOKEN not in fill("gitlab.com") and TOKEN not in fill("github.com.evil.test")


def test_stage_and_release_use_the_credential_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capsys, git_calls: list[dict[str, Any]]
) -> None:
    env = build_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ABCD_GITHUB_TOKEN", TOKEN)
    url = github_alias(monkeypatch, tmp_path, env.bare)
    repo.run_git(env.ws, "remote", "set-url", "origin", url)
    git_calls.clear()  # the fixture's own setup pushes are not abcd's
    cycle(env, capsys)  # stage: fetch, ls-remote-free; push_tag
    n = len(git_calls)
    env.merge_to_main()  # the test's stand-in for the reviewed pull request, not abcd's call
    del git_calls[n:]
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, doc.get("error")
    subs = {c["sub"] for c in git_calls if c["sub"] in NETWORK}
    assert {"fetch", "push"} <= subs
    _assert_authenticated(git_calls)
    # every single network call, not just some
    assert all(
        any(w.startswith(f"{HELPER_KEY}=") for w in c["argv"])
        for c in git_calls
        if c["sub"] in NETWORK
    )


def test_clone_and_ws_init_use_the_credential_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capsys, git_calls: list[dict[str, Any]]
) -> None:
    gen = workspaces_env(tmp_path, monkeypatch)
    ws_dir = next(gen)
    monkeypatch.setenv("ABCD_GITHUB_TOKEN", TOKEN)
    bare = bare_repo(tmp_path)
    ws = make_workspace(ws_dir)
    url = github_alias(monkeypatch, tmp_path, bare)
    git_calls.clear()
    code, doc = run(capsys, "ws", "init", "--env", "acme", "--remote", url)
    assert code == 0, doc
    shutil.rmtree(ws)
    code, doc = run(capsys, "ws", "clone", url, "--env", "acme")
    assert code == 0, doc
    subs = {c["sub"] for c in git_calls if c["sub"] in NETWORK}
    assert {"push", "clone"} <= subs
    _assert_authenticated(git_calls)
    gen.close()


# -- 6. ws init --remote never pushes the default branch on a re-run ---------------------------


@pytest.fixture
def ws_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    yield from workspaces_env(tmp_path, monkeypatch)


def test_ws_init_rerun_does_not_push_the_default_branch(
    ws_dir: Path, tmp_path: Path, capsys: Capsys
) -> None:
    bare = bare_repo(tmp_path)
    ws = make_workspace(ws_dir)
    code, doc = run(capsys, "ws", "init", "--env", "acme", "--remote", str(bare))
    assert code == 0 and doc["data"]["pushed"] is True  # the run that made the first commit
    remote_main = git(bare, "rev-parse", "main")
    # a local default-branch commit that was never reviewed
    (ws / "harness" / "sneaky.txt").write_text("not reviewed\n")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "local commit on main")
    code, doc = run(capsys, "ws", "init", "--env", "acme", "--remote", str(bare))
    assert code == 0, doc
    assert doc["data"]["pushed"] is False and doc["data"]["push_refused"] is True
    assert "push a branch and open a pull request" in doc["message"] if "message" in doc else True
    assert git(bare, "rev-parse", "main") == remote_main  # unchanged


def test_ws_init_pushes_when_the_remote_has_no_default_branch_yet(
    ws_dir: Path, tmp_path: Path, capsys: Capsys
) -> None:
    ws = make_workspace(ws_dir)
    bare1 = bare_repo(tmp_path, "one.git")
    assert run(capsys, "ws", "init", "--env", "acme", "--remote", str(bare1))[0] == 0
    sha = git(ws, "rev-parse", "HEAD")
    bare2 = bare_repo(tmp_path, "two.git")  # a new, empty remote: no first commit this run
    code, doc = run(capsys, "ws", "init", "--env", "acme", "--remote", str(bare2))
    assert code == 0, doc
    assert doc["data"]["committed"] is False and doc["data"]["pushed"] is True
    assert git(bare2, "rev-parse", "main") == sha


def test_explicit_token_beats_the_machine_helper_but_only_for_its_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git itself: with the token set the machine helper is not consulted for github.com (nor a
    GHE host in ABCD_GITHUB_HOSTS); other hosts still get it; without a token it is untouched."""
    _clean_env(monkeypatch, tmp_path)
    d = tmp_path / "w"
    d.mkdir()
    sh(d, "init", "-q")
    machine = "!f() { echo username=machine; echo password=MACHINE-SECRET; }; f"
    monkeypatch.setenv("ABCD_GITHUB_HOSTS", "ghe.example.test, other.ghe.test")
    monkeypatch.setenv("ABCD_GITHUB_TOKEN", TOKEN)

    def fill(host: str, remote: str | None) -> str:
        auth = repo.auth_from_env(remote_url=remote)
        cfg = ["-c", "credential.helper=" + machine]  # a machine-wide helper
        if auth:
            cfg += sum((["-c", c] for c in auth.config), [])
        return subprocess.run(
            ["git", *cfg, "credential", "fill"],
            input=f"protocol=https\nhost={host}\n\n", capture_output=True, text=True, cwd=d,
            env={
                **__import__("os").environ, **(auth.env if auth else {}),
                "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",  # only ours
            }, timeout=30,
        ).stdout  # fmt: skip

    for host in ("github.com", "www.github.com", "ghe.example.test"):
        out = fill(host, f"https://{host}/acme/ws.git")
        assert f"password={TOKEN}" in out and "MACHINE-SECRET" not in out, host
    out = fill("gitlab.com", "https://gitlab.com/acme/ws.git")  # no auth: machine helper answers
    assert "password=MACHINE-SECRET" in out and TOKEN not in out
    assert repo.auth_from_env(remote_url="https://nothere.example.test/a/b.git") is None
    monkeypatch.delenv("ABCD_GITHUB_HOSTS")
    assert repo.auth_from_env(remote_url="https://ghe.example.test/a/b.git") is None
    monkeypatch.delenv("ABCD_GITHUB_TOKEN")  # a local run keeps the machine's helper untouched
    assert repo.auth_from_env(remote_url=GITHUB_URL) is None
    assert "password=MACHINE-SECRET" in fill("github.com", GITHUB_URL)
