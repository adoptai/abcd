"""``skill push|plugin push --dry-run`` validate locally; ``deploy-default`` is cloud-refused."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cli.abcd as abcd_main

GOOD = "---\nname: demo-skill\ndescription: Does a demo thing\n---\n# Demo\nBody\n"


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = abcd_main.main([*argv, "--env", "nowhere"])
    cap = capsys.readouterr()
    return code, cap.out + cap.err


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for k in ("ADOPT_CONVERSATION_ID", "ABCD_PAT_CLIENT_ID", "ADOPT_APP_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ABCD_WORKSPACES_DIR", str(tmp_path / "ws"))
    monkeypatch.chdir(tmp_path)

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("dry run touched the platform")

    monkeypatch.setattr("cli.harness_skill.get_harness_client_for_env", boom)


def _skill(tmp_path: Path, text: str = GOOD) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "SKILL.md").write_text(text)
    (d / "notes.md").write_text("aux\n")
    return d


def test_dry_run_lists_what_would_be_uploaded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = _skill(tmp_path)
    code, out = _run(capsys, "skill", "push", str(d), "--dry-run", "--skip-audit")
    assert code == 0, out
    assert "DRY RUN OK" in out and "demo-skill" in out
    assert "SKILL.md, notes.md" in out and "new skill" in out
    code, out = _run(capsys, "skill", "push", str(d), "--dry-run", "--skip-audit", "--replace")
    assert code == 0 and "replace an existing skill" in out


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda t: t / "does-not-exist", id="missing-dir"),
        pytest.param(lambda t: (t / "empty").mkdir() or t / "empty", id="no-skill-md"),
        pytest.param(lambda t: _skill(t, "# no frontmatter\n"), id="no-frontmatter"),
        pytest.param(
            lambda t: _skill(t, "---\nname: x\ndescription: a: b\n---\nbody\n"), id="unquoted-colon"
        ),
        pytest.param(
            lambda t: _skill(t, "---\nname: Bad_Name\ndescription: d\n---\nbody\n"), id="bad-slug"
        ),
    ],
)
def test_dry_run_fails_on_a_bad_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    make,  # type: ignore[no-untyped-def]
) -> None:
    code, out = _run(capsys, "skill", "push", str(make(tmp_path)), "--dry-run", "--skip-audit")
    assert code != 0 and "DRY RUN OK" not in out


def test_dry_run_rejects_a_bad_name_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(
        capsys, "skill", "push", str(_skill(tmp_path)), "--dry-run", "--skip-audit", "--name", "A b"
    )
    assert code != 0 and "valid slug" in out


def test_dry_run_runs_the_audit_unless_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(
        "cli.harness_skill.run_skill_audit",
        lambda d: calls.append(d) or (2, "CRITICAL: bad"),
    )
    d = _skill(tmp_path)
    code, out = _run(capsys, "skill", "push", str(d), "--dry-run")
    assert code == 1 and calls == [d] and "Critical" in out
    code, out = _run(capsys, "skill", "push", str(d), "--dry-run", "--skip-audit")
    assert code == 0 and calls == [d]


def test_plugin_dry_run_validates_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(capsys, "plugin", "push", str(tmp_path / "nope"), "--dry-run")
    assert code != 0
    p = tmp_path / "plug"
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "demo-plug"}))
    (p / "skills" / "demo-skill").mkdir(parents=True)
    (p / "skills" / "demo-skill" / "SKILL.md").write_text(GOOD)
    code, out = _run(capsys, "plugin", "push", str(p), "--dry-run", "--skip-audit")
    assert code == 0 and "DRY RUN OK" in out and "demo-plug" in out and "demo-skill" in out
    (p / "skills" / "demo-skill" / "SKILL.md").write_text("# nothing\n")
    code, out = _run(capsys, "plugin", "push", str(p), "--dry-run", "--skip-audit")
    assert code != 0


@pytest.mark.parametrize("var", ["ADOPT_CONVERSATION_ID", "ABCD_PAT_CLIENT_ID"])
def test_deploy_default_is_refused_in_cloud_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    monkeypatch.setenv(var, "x")
    d = _skill(tmp_path)
    for extra in ([], ["--dry-run"]):
        code, out = _run(capsys, "skill", "deploy-default", str(d), *extra)
        assert code == 1 and "refused in a cloud run" in out and "Built-in" in out


def test_deploy_default_still_dry_runs_locally(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(capsys, "skill", "deploy-default", str(_skill(tmp_path)), "--dry-run")
    assert code == 0 and "DRY RUN OK" in out
