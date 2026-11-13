"""The shared default-tier deploy script and the abcd deploy workflow (offline: DRY_RUN only)."""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SHARED = REPO / "scripts" / "deploy_default_skill.py"
NOUI_WRAPPER = REPO / "harness-skills" / "noui" / "deploy_default_skill.py"
GATE_MD = (
    REPO
    / "cli"
    / "builder_common"
    / "templates"
    / "meta"
    / "skills"
    / "abcd-release-gate"
    / "SKILL.md"
)
WORKFLOW = REPO / ".github" / "workflows" / "deploy-abcd-skill.yml"
NOUI_WORKFLOW = REPO / ".github" / "workflows" / "deploy-skill.yml"


def _run(script: Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k not in ("AUX", "SKILL_MD", "SKILL_NAME")}
    return subprocess.run(
        [sys.executable, str(script)],
        env={**base, "DRY_RUN": "1", "BUNDLE_VERSION": "test-sha", **env},
        capture_output=True,
        text=True,
        cwd=REPO,
    )


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    path = tmp_path / "abcd-bundle.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("pyproject.toml", "[project]\nname = 'abcd'\n")
    return path


def test_abcd_dry_run_assembles(bundle: Path) -> None:
    r = _run(
        SHARED,
        SKILL_NAME="abcd",
        SKILL_MD=str(REPO / "harness-skills" / "abcd" / "SKILL.md"),
        AUX=f"abcd-bundle.zip={bundle}",
    )
    assert r.returncode == 0, r.stderr
    assert "skill='abcd' bundle_version=test-sha" in r.stdout
    assert "abcd-bundle.zip" in r.stdout and "DRY RUN OK" in r.stdout


def test_release_gate_dry_run_has_no_aux() -> None:
    r = _run(SHARED, SKILL_NAME="abcd-release-gate", SKILL_MD=str(GATE_MD), AUX="")
    assert r.returncode == 0, r.stderr
    assert "aux=[]" in r.stdout and "DRY RUN OK" in r.stdout


def test_empty_aux_file_fails(tmp_path: Path) -> None:
    empty = tmp_path / "abcd-bundle.zip"
    empty.write_bytes(b"")
    r = _run(
        SHARED,
        SKILL_NAME="abcd",
        SKILL_MD=str(REPO / "harness-skills" / "abcd" / "SKILL.md"),
        AUX=f"abcd-bundle.zip={empty}",
    )
    assert r.returncode != 0 and "is empty" in r.stderr


def test_skill_without_frontmatter_is_refused(tmp_path: Path) -> None:
    md = tmp_path / "SKILL.md"
    md.write_text("# no frontmatter\n")
    r = _run(SHARED, SKILL_NAME="abcd", SKILL_MD=str(md), AUX="")
    assert r.returncode != 0 and "frontmatter" in r.stderr


def test_noui_wrapper_keeps_its_defaults(tmp_path: Path) -> None:
    built = REPO / "harness-skills" / "noui" / "noui-bundle.zip"
    made = not built.exists()
    if made:
        with zipfile.ZipFile(built, "w") as z:
            z.writestr("x.txt", "x")
    try:
        r = _run(NOUI_WRAPPER)
    finally:
        if made:
            built.unlink()
    assert r.returncode == 0, r.stderr
    assert "skill='noui'" in r.stdout and "noui-bundle.zip" in r.stdout
    assert "DRY RUN OK" in r.stdout


# -- workflow ------------------------------------------------------------------------------


def _wf() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text())
    doc["on"] = doc.pop(True)  # YAML 1.1 reads the key `on` as a boolean
    return doc


def test_workflow_triggers() -> None:
    wf = _wf()
    assert wf["name"] == "Deploy abcd Skill"
    on = wf["on"]
    assert set(on) == {"pull_request", "push", "release", "workflow_dispatch"}
    assert on["push"]["branches"] == ["dev"]
    assert on["release"]["types"] == ["published"]
    assert on["workflow_dispatch"]["inputs"]["environment"]["options"] == ["dev", "prod"]
    want = {
        "harness-skills/abcd/**",
        "cli/**",
        "abcd-builder/**",
        "adopt-skill-review/**",
        "pyproject.toml",
        "cli/builder_common/templates/meta/skills/abcd-release-gate/**",
        "scripts/deploy_default_skill.py",
        ".github/workflows/deploy-abcd-skill.yml",
    }
    assert set(on["pull_request"]["paths"]) == want == set(on["push"]["paths"])
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_workflow_jobs() -> None:
    jobs = _wf()["jobs"]
    assert set(jobs) == {"dry-run", "deploy"}
    dry, dep = jobs["dry-run"], jobs["deploy"]
    assert dry["if"] == "github.event_name == 'pull_request'"
    assert "environment" not in dry
    dry_text = yaml.dump(dry)
    assert "secrets." not in dry_text and "DRY_RUN" in dry_text
    assert "abcd-release-gate" in dry_text and "harness-skills/abcd/abcd-bundle.zip" in dry_text
    assert "deploy-default" in dry_text
    assert "environment" in dep and "release" in dep["environment"]
    dep_text = yaml.dump(dep)
    assert (
        dep["env"]["BUNDLE_VERSION"].count("tag_name") == 1
        and "github.sha" in dep["env"]["BUNDLE_VERSION"]
    )
    names = [s.get("name") for s in dep["steps"]]
    assert names.index("Secret scan of SKILL.md + every bundle member") < names.index("Deploy abcd")
    assert names.index("Deploy abcd") < names.index("Deploy abcd-release-gate")
    for secret in ("ADOPT_CLIENT_ID", "ADOPT_CLIENT_SECRET"):
        assert f"secrets.{secret}" in dep_text
    assert "vars.ADOPT_BASE_URL" in dep_text


def test_noui_workflow_triggers_are_untouched() -> None:
    doc = yaml.safe_load(NOUI_WORKFLOW.read_text())
    on = doc[True]
    assert set(on) == {"pull_request", "workflow_dispatch"}  # manual-only: no push, no release
