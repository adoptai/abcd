"""abcd cloud bootstrap, bundle and nightly."""

from __future__ import annotations

import json
import subprocess
import zipfile
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

import cli.abcd as abcd_main
import cli.builder_cloud as bc
from cli.core.registry import Registry
from tests.cmd.ops_fixtures import OpsEnv, ops_env, run

CLOUD_VARS = {
    "ABCD_PAT_CLIENT_ID": "client-id-1",
    "ABCD_PAT_SECRET": "s3cr3t-value-xyz",
    "GITHUB_TOKEN": "ghp_" + "a" * 30,
    "ABCD_WEBUI_ENDPOINT": "https://bff.test",
    "ADOPT_CONVERSATION_ID": "conv-1",
}


@pytest.fixture
def ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[OpsEnv]:
    with ops_env(tmp_path, monkeypatch) as e:
        yield e


def _git(cwd: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(cwd),
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def bare_repo(tmp_path: Path) -> tuple[Path, str]:
    work = tmp_path / "seed"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / "builder.json").write_text(json.dumps({"stores": {}}))
    (work / "README.md").write_text("workspace\n")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "init")
    sha = _git(work, "rev-parse", "HEAD")
    bare = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
    return bare, sha


def _workspace_file(tmp_path: Path, ref: str, env: str = "zenith") -> Path:
    f = tmp_path / "abcd-workspace.json"
    f.write_text(json.dumps({"env": env, "repo": "acme/zenith-workspace", "ref": ref}))
    return f


def test_bootstrap_missing_variables_exits_2_and_names_every_one(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (*CLOUD_VARS, "ADOPT_APP_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    wf = _workspace_file(tmp_path, "main")
    code, doc = run(
        "cloud", "bootstrap", "--workspace-file", str(wf), "--abcd-root", str(tmp_path / "root")
    )
    assert code == 2
    for name in (
        "ABCD_PAT_CLIENT_ID",
        "ABCD_PAT_SECRET",
        "GITHUB_TOKEN",
        "ABCD_WEBUI_ENDPOINT or ADOPT_APP_BASE_URL",
        "ADOPT_CONVERSATION_ID",
    ):
        assert name in doc["error"]
    assert len(doc["data"]["missing"]) == 5
    assert not (tmp_path / "root").exists()


def test_bootstrap_happy_path_with_local_bare_repo_and_fake_pat_exchange(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bare_repo: tuple[Path, str]
) -> None:
    bare, sha = bare_repo
    for k, v in CLOUD_VARS.items():
        monkeypatch.setenv(k, v)
    exchanged: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        bc, "EXCHANGE_PAT", lambda endpoint, cid, secret: exchanged.append((endpoint, cid, secret))
    )
    seen_args: list[list[str]] = []

    def run_git(args: list[str], cwd: Path, env: Mapping[str, str]) -> None:
        seen_args.append(list(args))
        # the real clone URL is https://github.com/...; point it at the local bare repo
        args = [f"file://{bare}" if a.startswith("https://github.com/") else a for a in args]
        subprocess.run(["git", *args], cwd=cwd, env=dict(env), check=True, capture_output=True)

    monkeypatch.setattr(bc, "RUN_GIT", run_git)
    root = tmp_path / "abcd-root"
    code, doc = run(
        "cloud",
        "bootstrap",
        "--workspace-file",
        str(_workspace_file(tmp_path, sha)),
        "--abcd-root",
        str(root),
    )
    assert code == 0, doc
    dest = root / "workspaces" / "zenith"
    assert (dest / "README.md").read_text() == "workspace\n"
    assert _git(dest, "rev-parse", "HEAD") == sha
    env_text = (dest / ".env").read_text()
    assert "ADOPT_HARNESS_PAT_CLIENT_ID=client-id-1" in env_text
    assert exchanged == [("https://bff.test", "client-id-1", "s3cr3t-value-xyz")]
    assert (dest / ".env").stat().st_mode & 0o777 == 0o600
    # no secret in the result, in argv or in a URL
    assert "s3cr3t" not in json.dumps(doc)
    assert not any("ghp_" in a for args in seen_args for a in args)
    assert doc["data"]["env"] == "zenith" and doc["data"]["conversation_id"] == "conv-1"


def test_bootstrap_with_deploy_key_flags_github_api_steps(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bare_repo: tuple[Path, str]
) -> None:
    bare, sha = bare_repo
    home = tmp_path / "sandbox-home"
    monkeypatch.setenv("HOME", str(home))
    for k, v in CLOUD_VARS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GITHUB_TOKEN")
    key = (
        "-----BEGIN "
        "OPENSSH PRIVATE KEY-----\nQUJDREVGR0hJSktMTU5PUA\n-----END OPENSSH PRIVATE KEY-----\n"
    )
    monkeypatch.setenv("GITHUB_DEPLOY_KEY", key)
    monkeypatch.setattr(bc, "EXCHANGE_PAT", lambda *a: None)
    urls: list[str] = []

    def run_git(args: list[str], cwd: Path, env: Mapping[str, str]) -> None:
        if args[:2] == ["remote", "add"]:
            urls.append(args[-1])
            args = [*args[:-1], f"file://{bare}"]
        assert "GIT_SSH_COMMAND" in env
        subprocess.run(["git", *args], cwd=cwd, env=dict(env), check=True, capture_output=True)

    monkeypatch.setattr(bc, "RUN_GIT", run_git)
    code, doc = run(
        "cloud",
        "bootstrap",
        "--workspace-file",
        str(_workspace_file(tmp_path, sha)),
        "--abcd-root",
        str(tmp_path / "root"),
    )
    assert code == 0, doc
    assert urls == ["git@github.com:acme/zenith-workspace.git"]
    assert doc["data"]["git_auth"] == "deploy_key"
    assert doc["data"]["github_api"] == "flagged"
    assert "pull requests" in doc["data"]["flags"][0]
    assert "PRIVATE KEY" not in json.dumps(doc) and "QUJDREVG" not in json.dumps(doc)
    assert (home / ".abcd" / "github_deploy_key").stat().st_mode & 0o777 == 0o600


def test_bootstrap_finds_the_workspace_file_and_reports_a_bad_one(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(bc.WORKSPACE_FILE_ENV, raising=False)
    monkeypatch.setattr(bc, "find_workspace_file", bc.find_workspace_file)
    with pytest.raises(Exception, match="abcd-workspace.json"):
        bc.find_workspace_file(None, search_roots=(tmp_path / "nowhere",))
    assert bc.find_workspace_file("x.json") == Path("x.json")
    (tmp_path / "abcd-workspace.json").write_text("{}")
    assert bc.find_workspace_file(None, search_roots=()).name == "abcd-workspace.json"
    code, doc = run("cloud", "bootstrap", "--workspace-file", str(tmp_path / "abcd-workspace.json"))
    assert code == 2 and "'env' is required" in doc["error"]


def test_bundle_builds_into_tmpdir(ops: OpsEnv, tmp_path: Path) -> None:
    out = tmp_path / "out" / "abcd-bundle.zip"
    code, doc = run("cloud", "bundle", "--out", str(out))
    assert code == 0, doc
    assert out.is_file() and doc["data"]["path"] == str(out)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "pyproject.toml" in names and "cli/abcd.py" in names
    assert not any(n.startswith(("workspaces/", "tabby/")) or n.endswith(".env") for n in names)


def test_bundle_dry_run_writes_nothing(ops: OpsEnv, tmp_path: Path) -> None:
    out = tmp_path / "x.zip"
    code, doc = run("cloud", "bundle", "--out", str(out), "--dry-run")
    assert code == 0 and doc["data"]["dry_run"] is True and not out.exists()


def _registry_with(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ws_pull: Callable[[Any], int] | None,
    audit: Callable[[Any], int] | None,
    calls: list[str],
) -> None:
    real = abcd_main.build_registry

    def build() -> Registry:
        reg = real()
        # The real `ws pull` and `audit drift` may already be registered (their parsers exist),
        # so swap in the fake handler instead of registering a second parser with the same name.
        for group, name, fake in (("ws", "pull", ws_pull), ("audit", "drift", audit)):
            g = reg.groups.get(group)
            spec = g.specs.get(name) if g else None
            if fake is None:
                if g is not None:
                    g.specs.pop(name, None)
            elif spec is not None:
                spec.handler = fake
            else:
                parser = reg.group(group).command(name, fake, help="fake")
                if name == "pull":
                    parser.add_argument("--check", action="store_true")
        return reg

    monkeypatch.setattr(abcd_main, "build_registry", build)


def test_nightly_runs_steps_in_order_with_a_fake_client(
    ops: OpsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cli.core.output import Result

    calls: list[str] = []

    def ws_pull(ctx: Any) -> Result:
        calls.append("ws pull --check")
        return Result.done({"in_sync": True}, "in sync")

    def audit(ctx: Any) -> Result:
        calls.append("audit drift")
        return Result.done({"has_drift": False}, "no drift")

    _registry_with(monkeypatch, ws_pull=ws_pull, audit=audit, calls=calls)
    order: list[str] = []
    real_run = bc.run_command

    def recording(argv: list[str]) -> tuple[int, Any, str]:
        order.append(" ".join(argv[:3]).split(" --")[0])
        return real_run(argv)

    monkeypatch.setattr(bc, "run_command", recording)
    ops.put_rows("bld_agents", {"agent_key": "prep", "name": "Prep", "live_version": "v1"})
    ops.put_rows(
        "bld_dev_events",
        {
            "event_id": "a",
            "workspace": "acme",
            "ran_in": "local",
            "actor": "dana",
            "ts": "2026-10-27T09:00:00+00:00",
            "step": 2,
            "kind": "eval",
            "result": "ok",
            "payload": {"command": "eval run", "duration_ms": 60000},
        },
    )
    monkeypatch.setattr("cli.builder_status.today", lambda: "2026-10-27")
    code, doc = run("cloud", "nightly")
    assert code == 0, doc
    assert calls == ["ws pull --check", "audit drift"]
    assert [s["step"] for s in doc["data"]["steps"]] == [
        "ws pull --check",
        "audit drift",
        "status publish",
        "report daily",
    ]
    assert [s["state"] for s in doc["data"]["steps"]] == ["ok"] * 4
    assert order[:2] == ["ws pull", "audit drift"]
    assert "scorecard.md" in ops.docs.paths("store-2")
    assert ops.docs.paths("store-1") == ["reports/daily/2026-10-27/dana.md"]


def test_nightly_tolerates_missing_optional_modules(
    ops: OpsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry_with(monkeypatch, ws_pull=None, audit=None, calls=[])
    code, doc = run("cloud", "nightly")
    assert code == 0, doc
    states = {s["step"]: s for s in doc["data"]["steps"]}
    assert states["ws pull --check"]["state"] == "skipped"
    assert "not available" in states["audit drift"]["message"]
    assert states["status publish"]["state"] == "ok"


def test_nightly_continues_after_a_failure_and_returns_the_worst_code(
    ops: OpsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cli.core.output import Result

    def ws_pull(ctx: Any) -> Result:
        return Result.platform_error("platform down")

    def audit(ctx: Any) -> Result:
        return Result.refused("drift found")

    _registry_with(monkeypatch, ws_pull=ws_pull, audit=audit, calls=[])
    code, doc = run("cloud", "nightly")
    assert code == 3
    assert doc["data"]["failed"] == ["ws pull --check", "audit drift"]
    states = {s["step"]: s["state"] for s in doc["data"]["steps"]}
    assert states["status publish"] == "ok"
    assert "platform down" in doc["data"]["steps"][0]["message"]


# -- platform-only mode, doctor, missing pointer ------------------------------------------------

PLATFORM_VARS = {
    "ABCD_PAT_CLIENT_ID": "client-id-1",
    "ABCD_PAT_SECRET": "s3cr3t-value-xyz",
    "ABCD_WEBUI_ENDPOINT": "https://bff.test",
}


def test_platform_only_bootstrap_writes_workspace_without_git(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for k, v in PLATFORM_VARS.items():
        monkeypatch.setenv(k, v)
    for k in ("GITHUB_TOKEN", "GITHUB_DEPLOY_KEY", "ADOPT_CONVERSATION_ID", "ADOPT_APP_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    exchanged: list[tuple[str, str, str]] = []
    monkeypatch.setattr(bc, "EXCHANGE_PAT", lambda *a: exchanged.append(a))

    def no_git(*a: Any, **k: Any) -> None:
        raise AssertionError("platform-only must not call git")

    monkeypatch.setattr(bc, "RUN_GIT", no_git)
    monkeypatch.setattr("cli.builder_common.cloud._run_git", no_git)
    monkeypatch.setattr(subprocess, "run", no_git)
    root = tmp_path / "abcd-root"
    # --env goes through the shared `run` helper's own --env acme, so call main directly
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = abcd_main.main(
            ["cloud", "bootstrap", "--platform-only", "--abcd-root", str(root), "--json"]
        )
    out = buf.getvalue()
    assert code == 0, out
    dest = root / "workspaces" / "cloud"
    assert json.loads((dest / "env.json").read_text())["env_id"] == "cloud"
    env_text = (dest / ".env").read_text()
    assert "ADOPT_HARNESS_PAT_CLIENT_ID=client-id-1" in env_text
    assert "ADOPT_HARNESS_PAT_SECRET=s3cr3t-value-xyz" in env_text
    assert "ADOPT_WEBUI_ENDPOINT=https://bff.test" in env_text
    assert (dest / ".env").stat().st_mode & 0o777 == 0o600
    assert (dest / ".builder" / "cloud.json").is_file()
    assert not (dest / ".git").exists()
    assert exchanged == [("https://bff.test", "client-id-1", "s3cr3t-value-xyz")]
    assert "s3cr3t" not in out and "s3cr3t" not in capsys.readouterr().err


def test_platform_only_bootstrap_names_missing_variables_and_writes_nothing(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k in (*PLATFORM_VARS, "ADOPT_APP_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = abcd_main.main(
            [
                "cloud",
                "bootstrap",
                "--platform-only",
                "--abcd-root",
                str(tmp_path / "r"),
                "--json",
            ]
        )
    assert code == 2
    assert "ABCD_PAT_SECRET" in buf.getvalue()
    assert not (tmp_path / "r").exists()


def test_bootstrap_without_a_pointer_file_names_both_options(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(bc.WORKSPACE_FILE_ENV, raising=False)
    monkeypatch.setattr(bc, "find_workspace_file", bc.find_workspace_file)
    with pytest.raises(Exception) as exc:
        bc.find_workspace_file(None, search_roots=(tmp_path / "nowhere",))
    msg = str(exc.value)
    assert "no abcd-workspace.json: run in the abcd-<env> workstream for repo work" in msg
    assert "abcd cloud bootstrap --platform-only" in msg


def test_doctor_lists_names_never_values(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(bc.WORKSPACE_FILE_ENV, raising=False)
    for k, v in PLATFORM_VARS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_DEPLOY_KEY", raising=False)
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "conv-77")
    monkeypatch.setattr(
        bc, "EXCHANGE_CLAIMS", lambda *a: {"tenant_id": "tenant-9", "roles": ["Admin"]}
    )
    code, doc = run("cloud", "doctor")
    assert code == 0, doc
    names = {v["name"]: v["present"] for v in doc["data"]["variables"]}
    assert names["ABCD_PAT_SECRET"] is True
    assert names["GITHUB_TOKEN or GITHUB_DEPLOY_KEY"] is False
    assert doc["data"]["pointer_file"] is None
    assert doc["data"]["pat"] == {
        "checked": True,
        "ok": True,
        "tenant_id": "tenant-9",
        "roles": ["Admin"],
    }
    blob = json.dumps(doc)
    for secret in ("s3cr3t-value-xyz", "client-id-1", "conv-77", "https://bff.test"):
        assert secret not in blob


def test_doctor_reports_a_failed_pat_exchange_without_raising(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for k, v in PLATFORM_VARS.items():
        monkeypatch.setenv(k, v)

    def refuse(*a: str) -> dict[str, Any]:
        raise RuntimeError("PAT exchange was refused (HTTP 401)")

    monkeypatch.setattr(bc, "EXCHANGE_CLAIMS", refuse)
    code, doc = run("cloud", "doctor")
    assert code == 1
    assert doc["data"]["pat"]["ok"] is False and "401" in doc["data"]["pat"]["error"]


def test_doctor_without_platform_variables_does_not_exchange(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for k in (*PLATFORM_VARS, "ADOPT_APP_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(bc, "EXCHANGE_CLAIMS", lambda *a: pytest.fail("must not be called"))
    code, doc = run("cloud", "doctor")
    assert code == 1 and doc["data"]["pat"] == {"checked": False}


def test_vault_variables_alone_authenticate_the_harness_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported failure: ABCD_* variables present, no workspaces/<env>/.env."""
    from cli.harness_common import auth

    for k, v in PLATFORM_VARS.items():
        monkeypatch.setenv(k, v)
    for k in ("ADOPT_HARNESS_PAT_CLIENT_ID", "ADOPT_HARNESS_PAT_SECRET", "ADOPT_WEBUI_ENDPOINT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("ADOPT_HARNESS_BEARER_TOKEN", raising=False)
    posted: list[Any] = []

    class Resp:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {"access_token": "tok", "expires_in": 60}

    monkeypatch.setattr(
        auth.requests, "post", lambda url, json, timeout: posted.append((url, json)) or Resp()
    )
    assert auth.get_harness_bearer_token() == "tok"
    assert posted == [
        (
            "https://bff.test/v1/users/api-token",
            {"client_id": "client-id-1", "secret": "s3cr3t-value-xyz"},
        )
    ]
