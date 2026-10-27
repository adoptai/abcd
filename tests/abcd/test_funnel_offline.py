"""The whole NoUI -> harness funnel up to the network boundary, through the real CLIs.

capture bundle (real Playwright recording of the toy app)
  -> compile (cli/noui_workspace.py compile_workflow) into a workspace
  -> generalize draft / confirm / apply (through the bridge)
  -> harness audit + content secret scan + the exact push payload (harness_skill.py)

Only the final HTTP upload is not exercised here; it is covered live against the dev
platform in the PR's test evidence.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from cli import harness_skill as hs  # noqa: E402

BUNDLE = _ROOT / "tests" / "noui" / "fixtures" / "toyapp_notes_bundle.json"


def bridge(workspaces: Path, *args: str) -> subprocess.CompletedProcess[str]:
    code = (
        f"import sys; sys.path.insert(0, {str(_ROOT)!r})\n"
        "from pathlib import Path\n"
        "from cli.wdl_common import workspace_manager as wm\n"
        f"wm.WORKSPACES_DIR = Path({str(workspaces)!r})\n"
        "from cli import noui_workspace\n"
        "sys.exit(noui_workspace.main(sys.argv[1:]))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )


def test_offline_funnel(tmp_path: Path) -> None:
    ws = tmp_path / "workspaces"
    (ws / "acme-dev").mkdir(parents=True)
    (ws / "acme-dev" / "env.json").write_text(json.dumps({"env_id": "acme-dev"}))
    (ws / "acme-dev" / ".env").write_text("ADOPT_CLIENT_ID=wdl\nADOPT_CLIENT_SECRET=wdl\n")

    r = bridge(
        ws,
        "--env",
        "acme-dev",
        "compile_workflow",
        str(BUNDLE),
        "--as",
        "skill",
        "--name",
        "toyapp-notes",
        "--execution-mode",
        "harness",
        "--profile-slug",
        "toyapp",
        "--allow-unbound-profile",
    )
    assert r.returncode == 0, r.stderr
    skill = ws / "acme-dev" / "harness" / "skills" / "toyapp-notes"

    for step in (
        ["generalize", "draft", "ws:skills/toyapp-notes"],
        ["generalize", "confirm", "ws:skills/toyapp-notes", "--by", "member"],
        ["generalize", "apply", "ws:skills/toyapp-notes"],
    ):
        r = bridge(ws, "--env", "acme-dev", *step)
        assert r.returncode == 0, (step, r.stdout, r.stderr)

    ops = json.loads((skill / "operations.json").read_text())["operations"]
    assert [o["name"] for o in ops] == ["list_notes", "get_note", "delete_note"]

    inventory = bridge(ws, "--env", "acme-dev", "list")
    listed = json.loads(inventory.stdout)["skills"]
    assert listed[0]["name"] == "toyapp-notes" and listed[0]["operations"] == 3

    # Harness side, offline: audit is clean, nothing secret-shaped is staged, and the
    # payload push would send is exactly SKILL.md + the skill's own files.
    code, output = hs.run_skill_audit(skill)
    assert code == 0, output
    skill_md = (skill / "SKILL.md").read_text()
    hs._lint_frontmatter(skill_md, skill / "SKILL.md")
    aux, skipped = hs._collect_aux_files(skill)
    assert hs.scan_upload(skill_md, aux) == []
    assert sorted(a["path"] for a in aux) == [
        "API.md",
        "auth_plan.json",
        "generalize_plan.json",
        "manifest.json",
        "operations.json",
    ]
    assert skipped == []
    uploaded_ops = json.loads(
        base64.b64decode(next(a for a in aux if a["path"] == "operations.json")["content_b64"])
    )
    assert uploaded_ops["operations"][0]["url_template"] == "https://toyapp.example.test/api/notes"
