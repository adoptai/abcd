"""cli/noui_workspace.py -- running the NoUI toolkit inside an abcd workspace."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from cli import noui_workspace as nw  # noqa: E402

FIXTURE = _ROOT / "tests" / "noui" / "fixtures" / "toyapp_notes_bundle.json"


def make_ws(root: Path, name: str = "acme-dev", dotenv: str = "") -> Path:
    env = root / name
    env.mkdir(parents=True)
    (env / "env.json").write_text(json.dumps({"env_id": name, "name": name}))
    if dotenv:
        (env / ".env").write_text(dotenv)
    return root


def resolve(root: Path, name: str = "acme-dev", base: dict[str, str] | None = None) -> nw.NouiEnv:
    return nw.resolve_noui_env(name, workspaces_dir=root, base_env=base or {})


# --- resolution -------------------------------------------------------------


def test_missing_workspace_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(nw.NouiWorkspaceError, match="not found"):
        resolve(tmp_path, "nope")


def test_workbench_is_the_workspace_harness_root(tmp_path: Path) -> None:
    ne = resolve(make_ws(tmp_path))
    assert ne.workbench_dir == tmp_path / "acme-dev" / "harness"
    assert str(nw.NOUI_ROOT) in ne.child_env["PYTHONPATH"].split(":")


def test_wdl_credentials_never_reach_noui(tmp_path: Path) -> None:
    """abcd's ADOPT_CLIENT_ID/SECRET are WDL client creds; NoUI reads the same
    names as a platform PAT and would silently switch to platform_jwt."""
    root = make_ws(tmp_path, dotenv="ADOPT_CLIENT_ID=wdl-id\nADOPT_CLIENT_SECRET=wdl-secret\n")
    ne = resolve(root, base={"ADOPT_CLIENT_ID": "inherited", "ADOPT_CLIENT_SECRET": "inherited"})
    assert "ADOPT_CLIENT_ID" not in ne.child_env
    assert "ADOPT_CLIENT_SECRET" not in ne.child_env
    assert ne.auth_mode == "agent_token"
    assert ne.auth_mode_source == "default (local Tabby)"


def test_harness_pat_maps_to_platform_jwt(tmp_path: Path) -> None:
    root = make_ws(
        tmp_path,
        dotenv=(
            "ADOPT_CLIENT_ID=wdl-id\nADOPT_CLIENT_SECRET=wdl-secret\n"
            "ADOPT_WEBUI_ENDPOINT=https://webui.example.test\n"
            "ADOPT_HARNESS_PAT_CLIENT_ID=pat-id\nADOPT_HARNESS_PAT_SECRET=pat-secret\n"
            "TABBY_API_URL=https://tabby.example.test\n"
        ),
    )
    ne = resolve(root)
    assert ne.auth_mode == "platform_jwt"
    assert ne.child_env["NOUI_TABBY_AUTH_MODE"] == "platform_jwt"
    assert ne.child_env["ADOPT_CLIENT_ID"] == "pat-id"
    assert ne.child_env["ADOPT_CLIENT_SECRET"] == "pat-secret"
    assert ne.child_env["ADOPT_API_URL"] == "https://webui.example.test"
    assert ne.notes == []


def test_noui_specific_pat_wins_over_harness_pat(tmp_path: Path) -> None:
    root = make_ws(
        tmp_path,
        dotenv=(
            "ADOPT_HARNESS_PAT_CLIENT_ID=pat-id\nADOPT_HARNESS_PAT_SECRET=pat-secret\n"
            "NOUI_ADOPT_CLIENT_ID=noui-id\nNOUI_ADOPT_CLIENT_SECRET=noui-secret\n"
            "ADOPT_API_URL=https://api.example.test\nADOPT_WEBUI_ENDPOINT=https://webui.example.test\n"
        ),
    )
    ne = resolve(root)
    assert ne.child_env["ADOPT_CLIENT_ID"] == "noui-id"
    assert ne.child_env["ADOPT_API_URL"] == "https://api.example.test"


def test_tabby_agent_credentials_select_agent_token(tmp_path: Path) -> None:
    root = make_ws(
        tmp_path,
        dotenv=(
            "TABBY_CLIENT_ID=t-id\nTABBY_CLIENT_SECRET=t-secret\n"
            "ADOPT_HARNESS_PAT_CLIENT_ID=pat-id\nADOPT_HARNESS_PAT_SECRET=pat-secret\n"
            "TABBY_API_URL=http://localhost:8000\n"
        ),
    )
    ne = resolve(root)
    assert ne.auth_mode == "agent_token"
    assert "TABBY_CLIENT_ID" in ne.auth_mode_source
    assert ne.child_env["TABBY_CLIENT_ID"] == "t-id"


def test_declared_mode_wins_and_is_validated(tmp_path: Path) -> None:
    root = make_ws(
        tmp_path, dotenv="NOUI_TABBY_AUTH_MODE=broker\nTABBY_CLIENT_ID=a\nTABBY_CLIENT_SECRET=b\n"
    )
    ne = resolve(root)
    assert ne.auth_mode == "broker" and ne.auth_mode_source == "workspace .env"
    assert any("broker mode only works inside" in n for n in ne.notes)

    bad = make_ws(tmp_path / "other", dotenv="NOUI_TABBY_AUTH_MODE=agent-tokn\n")
    with pytest.raises(nw.NouiWorkspaceError, match="not one of"):
        resolve(bad)


def test_process_env_mode_is_used_when_workspace_is_silent(tmp_path: Path) -> None:
    ne = resolve(make_ws(tmp_path), base={"NOUI_TABBY_AUTH_MODE": "platform_jwt"})
    assert ne.auth_mode == "platform_jwt" and ne.auth_mode_source == "process environment"
    assert any("platform_jwt needs" in n for n in ne.notes)


def test_template_placeholders_are_ignored(tmp_path: Path) -> None:
    root = make_ws(
        tmp_path,
        dotenv=(
            "ADOPT_HARNESS_PAT_CLIENT_ID=your-harness-pat-client-id\n"
            "ADOPT_HARNESS_PAT_SECRET=your-harness-pat-secret\n"
            "TABBY_API_URL=https://tabby.example.test\n"
        ),
    )
    ne = resolve(root)
    assert "ADOPT_CLIENT_ID" not in ne.child_env
    assert ne.auth_mode == "agent_token"


def test_workspace_can_override_the_workbench(tmp_path: Path) -> None:
    custom = tmp_path / "elsewhere"
    root = make_ws(tmp_path / "ws", dotenv=f"NOUI_WORKBENCH_DIR={custom}\n")
    assert resolve(root).workbench_dir == custom
    # An inherited NOUI_WORKBENCH_DIR (e.g. an old shell export) does not.
    root2 = make_ws(tmp_path / "ws2")
    ne = resolve(root2, base={"NOUI_WORKBENCH_DIR": "/tmp/stale"})
    assert ne.workbench_dir == tmp_path / "ws2" / "acme-dev" / "harness"


def test_secrets_are_masked_in_describe(tmp_path: Path) -> None:
    root = make_ws(
        tmp_path,
        dotenv="TABBY_CLIENT_ID=t-id\nTABBY_CLIENT_SECRET=supersecret\nTABBY_ADMIN_TOKEN=admintok\n",
    )
    text = json.dumps(nw.describe(resolve(root)))
    assert "supersecret" not in text and "admintok" not in text
    assert "<set>" in text


# --- scripts / args ----------------------------------------------------------


def test_script_lookup_and_ws_expansion(tmp_path: Path) -> None:
    ne = resolve(make_ws(tmp_path))
    assert nw.script_path("compile_workflow").name == "compile_workflow.py"
    assert nw.script_path("generalize.py").name == "generalize.py"
    with pytest.raises(nw.NouiWorkspaceError, match="Unknown NoUI script"):
        nw.script_path("_bootstrap")
    with pytest.raises(nw.NouiWorkspaceError, match="Unknown NoUI script"):
        nw.script_path("rm")
    assert nw.expand_ws_args(["ws:skills/x", "--flag", "plain"], ne) == [
        str(ne.workbench_dir / "skills/x"),
        "--flag",
        "plain",
    ]
    assert "capture_record" in nw.list_scripts() and "generalize" in nw.list_scripts()


def test_compile_through_the_bridge_lands_in_the_workspace(tmp_path: Path) -> None:
    """AC6: a real compile, run through the bridge, writes the skill where
    harness_skill.py push reads it -- and the NoUI child saw the pinned config."""
    root = make_ws(tmp_path, dotenv="ADOPT_CLIENT_ID=wdl-id\nADOPT_CLIENT_SECRET=wdl-secret\n")
    ne = resolve(root, base={"PATH": "/usr/bin:/bin"})
    rc = nw.run_script(
        ne,
        "compile_workflow",
        [
            str(FIXTURE),
            "--as",
            "skill",
            "--name",
            "toyapp-notes",
            "--execution-mode",
            "harness",
            "--profile-slug",
            "toyapp",
            "--allow-unbound-profile",
        ],
    )
    assert rc == 0
    skill = ne.workbench_dir / "skills" / "toyapp-notes"
    assert (skill / "SKILL.md").exists() and (skill / "operations.json").exists()
    inv = nw.inventory(ne)
    assert [s["name"] for s in inv["skills"]] == ["toyapp-notes"]  # type: ignore[index]

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from noui_core.config import settings as s; import os; "
            "print(s.workbench_dir, s.tabby_auth_mode, os.environ.get('ADOPT_CLIENT_ID', '-'))",
        ],
        env=ne.child_env,
        capture_output=True,
        text=True,
        check=True,
    )
    workbench, mode, client = probe.stdout.split()
    assert Path(workbench) == ne.workbench_dir
    assert mode == "agent_token"
    assert client == "-"


def test_cli_env_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cli.wdl_common import workspace_manager

    root = make_ws(tmp_path, dotenv="TABBY_CLIENT_ID=a\nTABBY_CLIENT_SECRET=b\n")
    monkeypatch.setattr(workspace_manager, "WORKSPACES_DIR", root)
    assert nw.main(["--env", "acme-dev", "env"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["auth_mode"] == "agent_token"
    assert nw.main(["--env", "acme-dev", "not-a-script"]) == 2
    assert nw.main(["--env", "missing", "env"]) == 2
