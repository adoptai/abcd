"""harness-skills/abcd: SKILL.md lint, bundle contents, determinism, secret scan."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from cli import harness_skill as hs  # noqa: E402
from cli.harness_common.secret_scan import scan_bytes, scan_text  # noqa: E402

SKILL_DIR = _ROOT / "harness-skills" / "abcd"


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "abcd_build_bundle", SKILL_DIR / "build_bundle.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bb = _load_builder()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    files = {
        "pyproject.toml": "[tool.poetry]\nname='abcd'\n",
        "README.md": "readme\n",
        "cli/abcd.py": "print('x')\n",
        "cli/noui/pyproject.toml": "x\n",
        "cli/noui/.env.example": "A=1\n",
        "cli/noui/key.pem": "k\n",
        "cli/__pycache__/m.pyc": "b\n",
        "abcd-builder/skills/a/SKILL.md": "s\n",
        "adopt-skill-review/README.md": "r\n",
        "workspaces/acme/.env": "SECRET=1\n",
        "tabby/server.py": "t\n",
        "tests/test_x.py": "t\n",
        "docs/plan.md": "d\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(root, "init", "-q")
    _git(root, "add", "-f", "--", *files)
    _git(root, "commit", "-q", "-m", "init")
    (root / "cli" / "untracked.py").write_text("u\n")
    (root / "cli" / ".env").write_text("TOKEN=1\n")
    return root


def test_skill_frontmatter_lints() -> None:
    text = (SKILL_DIR / "SKILL.md").read_text()
    meta = hs._lint_frontmatter(text, SKILL_DIR / "SKILL.md")
    assert meta["name"] == "abcd"
    assert meta["description"]


def test_skill_md_covers_required_steps() -> None:
    text = (SKILL_DIR / "SKILL.md").read_text()
    for needle in (
        'read_skill_file(skill_name="abcd", path="abcd-bundle.zip")',
        "zipfile",
        "pip install /workspace/abcd",
        "abcd-workspace.json",
        "abcd cloud bootstrap",
        "--json",
        "cloud/",
        "NoUI capture and replay are not available",
    ):
        assert needle in text, needle


def test_skill_md_secret_scan_clean() -> None:
    assert scan_text("SKILL.md", (SKILL_DIR / "SKILL.md").read_text()) == []


def test_bundle_contents(fake_repo: Path, tmp_path: Path) -> None:
    out = bb.build(root=fake_repo, out=tmp_path / "b.zip")
    names = set(zipfile.ZipFile(out).namelist())
    assert names == {
        "pyproject.toml",
        "README.md",
        "cli/abcd.py",
        "cli/noui/pyproject.toml",
        "abcd-builder/skills/a/SKILL.md",
        "adopt-skill-review/README.md",
    }
    assert not any(n.split("/")[-1].startswith(".env") for n in names)


def test_bundle_is_deterministic(fake_repo: Path, tmp_path: Path) -> None:
    a = bb.build(root=fake_repo, out=tmp_path / "a.zip").read_bytes()
    b = bb.build(root=fake_repo, out=tmp_path / "b.zip").read_bytes()
    assert a == b
    names = zipfile.ZipFile(tmp_path / "a.zip").namelist()
    assert names == sorted(names)


def test_bundle_refuses_non_git_dir(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("x")
    with pytest.raises(SystemExit):
        bb.build(root=tmp_path, out=tmp_path / "o.zip")


def test_real_bundle_members_and_secret_scan(tmp_path: Path) -> None:
    out = bb.build(root=_ROOT, out=tmp_path / "abcd-bundle.zip")
    names = zipfile.ZipFile(out).namelist()
    assert "pyproject.toml" in names
    assert any(n.startswith("cli/noui/") for n in names)
    assert any(n.startswith("abcd-builder/") for n in names)
    forbidden = ("workspaces/", "tabby/")
    assert not [n for n in names if n.startswith(forbidden)]
    assert not [n for n in names if Path(n).name.startswith(".env")]
    findings = scan_bytes("abcd-bundle.zip", out.read_bytes())
    assert findings == [], [str(f) for f in findings]
