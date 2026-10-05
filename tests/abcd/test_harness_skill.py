"""cli/harness_skill.py -- offline gates (exclusions, secret scan, plugin zip, default tier)."""

from __future__ import annotations

import base64
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from cli import harness_skill as hs  # noqa: E402

LEAK = "AKIA" + "ABCDEFGHIJKLMNOP"


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def upload_skill(self, **kw: Any) -> dict:
        self.calls.append(("upload_skill", kw))
        return {}

    def get_skill(self, name: str) -> dict:
        self.calls.append(("get_skill", name))
        return {"name": name}

    def upload_plugin(self, zip_bytes: bytes, filename: str, replace: bool = False) -> dict:
        self.calls.append(("upload_plugin", (zip_bytes, filename, replace)))
        return {}

    def get_plugin(self, name: str) -> dict:
        return {"name": name, "skills": []}

    def upload_default_skill(self, **kw: Any) -> dict:
        self.calls.append(("upload_default_skill", kw))
        return {}

    def get_default_skill(self, name: str) -> dict:
        return {"published": True, "bundle_version": "v1", "content_dir": "x"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    fake = FakeClient()
    monkeypatch.setattr(hs, "get_harness_client_for_env", lambda env=None: fake)
    return fake


def write_skill(d: Path, name: str = "demo-skill", body: str = "Do the thing.\n") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f'---\nname: {name}\ndescription: "Demo."\n---\n\n{body}')
    return d


def test_vendored_audit_script_is_present() -> None:
    """AC14: push's audit gate fails OPEN when adopt-skill-review/ is missing, so
    moving it would silently disable the gate. Pin its location here."""
    assert hs._AUDIT_SCRIPT.is_file(), hs._AUDIT_SCRIPT


@pytest.mark.parametrize(
    ("parts", "excluded"),
    [
        ((".env.example",), False),
        ((".env",), True),
        (("dev_only", ".env.graph"), True),
        (("config.env",), True),
        (("key.pem",), True),
        (("tests", "test_x.py"), True),
        (("scripts", "run.py"), False),
        ((".hidden", "x.md"), True),
    ],
)
def test_exclusions(parts: tuple[str, ...], excluded: bool) -> None:
    assert hs._is_excluded(parts) is excluded


def test_push_blocks_on_secret_before_any_network(tmp_path: Path, client: FakeClient) -> None:
    skill = write_skill(tmp_path / "s")
    (skill / "run.py").write_text(f"KEY = '{LEAK}'\n")
    assert hs.push(skill, None, False, False, True, None) == 1
    assert client.calls == []
    # An explicit override uploads anyway.
    assert hs.push(skill, None, False, False, True, None, allow_secret_findings=True) == 0
    assert client.calls[0][0] == "upload_skill"


def test_push_with_explicit_aux_uploads_only_those(tmp_path: Path, client: FakeClient) -> None:
    skill = write_skill(tmp_path / "s")
    (skill / "build.py").write_text("print('dev tool')\n")
    blob = tmp_path / "bundle.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pyproject.toml", "[project]\nname='x'\n")
    blob.write_bytes(buf.getvalue())
    assert (
        hs.push(skill, None, False, False, True, None, aux_specs=[f"noui-bundle.zip={blob}"]) == 0
    )
    aux = client.calls[0][1]["aux_files"]
    assert [a["path"] for a in aux] == ["noui-bundle.zip"]


def make_plugin(root: Path, name: str = "acme-tools") -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0"})
    )
    write_skill(root / "skills" / "list-orders", "list-orders")
    (root / "skills" / "list-orders" / "references").mkdir()
    (root / "skills" / "list-orders" / "references" / "api.md").write_text("# API\n")
    (root / "skills" / "list-orders" / "tests").mkdir()
    (root / "skills" / "list-orders" / "tests" / "t.py").write_text("assert True\n")
    (root / "skills" / "list-orders" / ".env").write_text("X=1\n")
    return root


def test_build_plugin_zip_layout_and_exclusions(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path / "p")
    data, meta, skills, findings = hs.build_plugin_zip(plugin)
    names = sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert names == [
        ".claude-plugin/plugin.json",
        "skills/list-orders/SKILL.md",
        "skills/list-orders/references/api.md",
    ]
    assert meta["name"] == "acme-tools" and skills == ["list-orders"] and findings == []


def test_build_plugin_zip_rejects_bad_layouts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="plugin.json"):
        hs.build_plugin_zip(tmp_path)
    plugin = make_plugin(tmp_path / "p", name="Bad Name")
    with pytest.raises(ValueError, match="must match"):
        hs.build_plugin_zip(plugin)
    plugin2 = make_plugin(tmp_path / "p2")
    write_skill(plugin2 / "skills" / "list-orders", "other-name")
    with pytest.raises(ValueError, match="must equal its directory"):
        hs.build_plugin_zip(plugin2)


def test_push_plugin_uploads_zip(tmp_path: Path, client: FakeClient) -> None:
    plugin = make_plugin(tmp_path / "p")
    assert hs.push_plugin(plugin, True, False, True, None) == 0
    kind, (zip_bytes, filename, replace) = client.calls[0]
    assert kind == "upload_plugin" and filename == "acme-tools.zip" and replace is True
    assert "skills/list-orders/SKILL.md" in zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()


def test_push_plugin_blocks_on_secret(tmp_path: Path, client: FakeClient) -> None:
    plugin = make_plugin(tmp_path / "p")
    (plugin / "skills" / "list-orders" / "references" / "api.md").write_text(f"key {LEAK}\n")
    assert hs.push_plugin(plugin, False, False, True, None) == 1
    assert client.calls == []


def test_deploy_default_dry_run_and_publish(tmp_path: Path, client: FakeClient) -> None:
    skill = write_skill(tmp_path / "s", "noui")
    bundle = tmp_path / "noui-bundle.zip"
    bundle.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    aux = [f"noui-bundle.zip={bundle}"]
    assert hs.deploy_default(skill, None, "abc123", 1, aux, True, None) == 0
    assert client.calls == []
    assert hs.deploy_default(skill, None, "abc123", 1, aux, False, None) == 0
    kind, kw = client.calls[0]
    assert kind == "upload_default_skill"
    assert kw["skill_name"] == "noui" and kw["bundle_version"] == "abc123"
    assert base64.b64decode(kw["aux_files"][0]["content_b64"]) == bundle.read_bytes()


def test_delete_requires_yes(client: FakeClient) -> None:
    assert hs.delete("x", None, yes=False) == 1
