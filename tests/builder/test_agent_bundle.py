"""Agent bundles <-> process API, and the platform client's new routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli.builder_common.agent_bundle import (
    AgentBundle,
    BundleError,
    diff_bundle_vs_platform,
    from_platform,
    load_bundle,
    staged_bundle,
    to_create_payload,
    to_patch_payload,
    workstream_ids,
    write_bundle,
    write_grants_payload,
)
from cli.builder_common.platform import BuilderPlatformClient
from cli.harness_common.api_client import HarnessAPIError

NAMES = {"client-ws": "ws-1", "staging": "ws-2", "dataset-acme": "ws-3", "notes": "ws-4"}
IDS = {v: k for k, v in NAMES.items()}

BUNDLE = AgentBundle(
    name="acme-prep",
    display_name="Return prep",
    description="Prepares returns",
    components=(("skill", "org", "tb-reconcile"), ("plugin", "default", "office-kit")),
    workstreams=("client-ws",),
    write_workstreams=("notes", "staging"),
    write_folder_prefix="outputs",
    is_enabled=True,
    instructions="Run tb-reconcile first.",
    flow={"spine": [{"ref": "tb-reconcile"}]},
)


def platform_process(**over: Any) -> dict[str, Any]:
    base = {
        "id": "p1",
        "name": "acme-prep",
        "display_name": "Return prep",
        "description": "Prepares returns",
        "components": [
            {"type": "skill", "source": "org", "ref": "tb-reconcile"},
            {"type": "plugin", "source": "default", "ref": "office-kit"},
        ],
        "agent_instructions": "Run tb-reconcile first.\n",
        "is_enabled": True,
        "compiled_flow": {"spine": [{"ref": "tb-reconcile"}]},
    }
    base.update(over)
    return base


GRANTS = {
    "grants": [],
    "workstream_ids": ["ws-4", "ws-2"],
    "folder_prefix": "outputs",
}


def test_write_and_load_round_trip(tmp_path: Path) -> None:
    write_bundle(BUNDLE, tmp_path / "a")
    assert (tmp_path / "a" / "instructions.md").read_text() == "Run tb-reconcile first.\n"
    assert load_bundle(tmp_path / "a") == BUNDLE
    bare = AgentBundle(name="x", display_name="X")
    write_bundle(bare, tmp_path / "b")
    assert not (tmp_path / "b" / "flow.json").exists()
    assert load_bundle(tmp_path / "b") == bare


@pytest.mark.parametrize(
    "yaml_text",
    [
        "name: Bad Name\ndisplay_name: X\n",
        "name: a\ndisplay_name: X\ncomponents: [{type: tool, source: org, ref: a}]\n",
        "name: a\ndisplay_name: X\ncomponents: [{type: skill, source: cloud, ref: a}]\n",
        "name: a\ndisplay_name: X\nbogus: 1\n",
        "name: a\ndisplay_name: X\nwrite_workstreams: [w]\n",
        "name: a\ndisplay_name: X\nworkstreams: w\n",
        "name: a\ndisplay_name: X\nis_enabled: yes please\n",
        "- list\n",
        "name: a\n",
    ],
)
def test_validation_errors(tmp_path: Path, yaml_text: str) -> None:
    (tmp_path / "process.yaml").write_text(yaml_text)
    with pytest.raises(BundleError):
        load_bundle(tmp_path)


def test_missing_process_yaml(tmp_path: Path) -> None:
    with pytest.raises(BundleError):
        load_bundle(tmp_path)


def test_payloads() -> None:
    create = to_create_payload(BUNDLE)
    assert create["name"] == "acme-prep" and create["compiled_flow"] == BUNDLE.flow
    assert create["components"][1] == {"type": "plugin", "source": "default", "ref": "office-kit"}
    assert "name" not in to_patch_payload(BUNDLE)
    assert workstream_ids(BUNDLE, NAMES) == ["ws-1"]
    assert write_grants_payload(BUNDLE, NAMES) == {
        "workstream_ids": ["ws-4", "ws-2"],
        "folder_prefix": "outputs",
    }
    with pytest.raises(BundleError):
        workstream_ids(AgentBundle(name="a", display_name="A", workstreams=("nope",)), NAMES)


def test_patch_payload_only_differences() -> None:
    assert to_patch_payload(BUNDLE, platform_process()) == {}
    changed = to_patch_payload(BUNDLE, platform_process(display_name="Old", is_enabled=False))
    assert changed == {"display_name": "Return prep", "is_enabled": True}
    cleared = to_patch_payload(
        AgentBundle(
            name="acme-prep",
            display_name="Return prep",
            description="Prepares returns",
            components=BUNDLE.components,
            is_enabled=True,
        ),
        platform_process(),
    )
    assert cleared == {"agent_instructions": None, "compiled_flow": None}


def test_from_platform_round_trip() -> None:
    b = from_platform(
        platform_process(), IDS, assigned_workstream_ids=["ws-1"], write_grants=GRANTS
    )
    assert b.name == "acme-prep" and b.workstreams == ("client-ws",)
    assert set(b.write_workstreams) == {"notes", "staging"} and b.write_folder_prefix == "outputs"
    assert b.instructions == "Run tb-reconcile first." and b.flow == BUNDLE.flow
    assert b.components == BUNDLE.components
    with pytest.raises(BundleError):
        from_platform(platform_process(), {}, assigned_workstream_ids=["ws-1"])


def test_from_platform_defaults_source_to_org() -> None:
    p = platform_process(components=[{"type": "skill", "ref": "x"}])
    assert from_platform(p, IDS).components == (("skill", "org", "x"),)


def test_diff() -> None:
    kw: dict[str, Any] = {"assigned_workstream_ids": ["ws-1"], "write_grants": GRANTS}
    assert diff_bundle_vs_platform(BUNDLE, platform_process(), NAMES, **kw) == []
    diffs = diff_bundle_vs_platform(
        BUNDLE,
        platform_process(description="other", agent_instructions=None),
        NAMES,
        assigned_workstream_ids=["ws-1", "ws-3"],
        write_grants={**GRANTS, "workstream_ids": ["ws-4"], "folder_prefix": "x"},
    )
    assert {d.field for d in diffs} == {
        "description",
        "agent_instructions",
        "workstreams",
        "write_workstreams",
    }
    prefix_only = diff_bundle_vs_platform(
        BUNDLE,
        platform_process(),
        NAMES,
        assigned_workstream_ids=["ws-1"],
        write_grants={**GRANTS, "folder_prefix": "x"},
    )
    assert [d.field for d in prefix_only] == ["write_folder_prefix"]


def test_staged_bundle() -> None:
    rename = {"tb-reconcile": "tb-reconcile--staged", "acme-prep": "ignored"}
    s = staged_bundle(BUNDLE, rename, ["staging", "dataset-acme"])
    assert s.name == "acme-prep-staged" and s.display_name == "Return prep (staged)"
    assert s.components == (
        ("skill", "org", "tb-reconcile--staged"),
        ("plugin", "default", "office-kit"),
    )
    assert s.workstreams == ("staging", "dataset-acme")
    assert s.write_workstreams == ("staging",) and s.write_folder_prefix == "outputs"
    assert s.is_enabled is True
    assert s.instructions == "Run tb-reconcile--staged first."
    assert s.flow == {"spine": [{"ref": "tb-reconcile--staged"}]}
    assert BUNDLE.workstreams == ("client-ws",)  # original untouched


def test_staged_bundle_never_inherits_wildcard_or_client_writes() -> None:
    b = AgentBundle(
        name="a",
        display_name="A",
        write_workstreams=("*",),
        write_folder_prefix="o",
        is_enabled=False,
    )
    s = staged_bundle(b, {}, ["staging"])
    assert s.write_workstreams == () and s.write_folder_prefix == "" and s.is_enabled
    assert write_grants_payload(b, NAMES) == {"workstream_ids": ["*"], "folder_prefix": "o"}


# -- platform client routes ------------------------------------------------


class Recorder(BuilderPlatformClient):
    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        super().__init__("tok", "https://webui.example")
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _request(self, method: str, path: str, timeout: int = 60, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        r = self.responses[(method, path)]
        if isinstance(r, Exception):
            raise r
        return r


def test_platform_client_routes() -> None:
    c = Recorder(
        {
            ("GET", "/v1/end-user/agent-harness/skills"): {
                "items": [{"name": "noui", "source": "default"}, {"name": "x", "source": "org"}]
            },
            ("PUT", "/v1/org/processes/p1/write-workstreams"): {"ok": True},
            ("GET", "/v1/end-user/agent-harness/conversations/c1/transcript"): {
                "events": [{"kind": "user_message", "text": "hi"}, "junk"]
            },
            ("GET", "/v1/end-user/agent-harness/skills/x"): {
                "name": "x",
                "source": "org",
                "description": "d",
                "body": "---\nname: x\n---\n",
                "aux_files": [
                    {"path": "a.md", "size": 5},
                    {"path": "b.bin", "size": 9},
                    {"path": "c.md", "size": 2},
                ],
            },
            ("GET", "/v1/end-user/agent-harness/skills/x/files"): {"path": "a.md", "content": "ab"},
        }
    )
    assert c.default_skill_names() == {"noui"}
    c.set_write_workstreams("p1", ["ws-1"], "out")
    assert c.calls[-1][2]["json"] == {"workstream_ids": ["ws-1"], "folder_prefix": "out"}
    assert c.transcript_events("c1") == [{"kind": "user_message", "text": "hi"}]
    snap = c.get_skill_files("x")  # every file returns the same fake body ("ab")
    assert snap.aux["a.md"].truncated and snap.aux["a.md"].state == "text"
    assert snap.aux["c.md"].truncated is False
    assert snap.partial_paths == ["a.md", "b.bin"]


def test_read_skill_file_states_and_default_version() -> None:
    path = "/v1/end-user/agent-harness/skills/x/files"
    c = Recorder({("GET", path): HarnessAPIError("GET", "u", 415, "binary")})
    assert c.read_skill_file("x", "a.zip", 10).state == "binary"
    c.responses[("GET", path)] = HarnessAPIError("GET", "u", 404, "")
    assert c.read_skill_file("x", "a.zip").state == "missing"
    c.responses[("GET", path)] = HarnessAPIError("GET", "u", 500, "")
    with pytest.raises(HarnessAPIError):
        c.read_skill_file("x", "a.zip")
    c.responses[("GET", "/v1/end-user/agent-harness/default-skills/noui")] = HarnessAPIError(
        "GET", "u", 403, ""
    )
    assert c.default_skill_version("noui") is None


def test_upload_first_sends_replace_false() -> None:
    c = Recorder({("POST", "/v1/end-user/agent-harness/skills"): {}})
    c.upload_skill_first("n", "QQ==")
    assert c.calls[0][2]["json"]["replace"] is False


def test_find_process_and_wrap() -> None:
    c = Recorder({("GET", "/v1/org/processes"): {"processes": [{"name": "a", "id": "1"}]}})
    assert c.find_process_by_name("a") == {"name": "a", "id": "1"}
    assert c.find_process_by_name("b") is None
    w = BuilderPlatformClient.wrap(c)
    assert w.webui_endpoint == "https://webui.example"
