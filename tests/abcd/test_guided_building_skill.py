"""The "Guided building" section of harness-skills/abcd/SKILL.md: payloads and commands.

The ``render_*`` / ``render_builder`` tool schemas live in the platform repos, so this keeps a
small offline copy of what each one REQUIRES (taken from the harness ``render_builder`` schema and
``genui/tool-definitions.json``) and checks every payload the skill shows against it. If the
platform changes a required key, update ``REQUIRED`` and the skill together.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[2] / "harness-skills" / "abcd" / "SKILL.md"
TEXT = SKILL.read_text()

FENCE = re.compile(r"^```json (\w+)\n(.*?)^```$", re.S | re.M)

FIELD_TYPES = {
    "text", "textarea", "number", "select", "multi_select", "date", "checkbox", "file_upload",
    "entity_picker", "file_map", "fact",
}  # fmt: skip
STEP_STATUS = {"done", "active", "running", "review", "pending", "blocked", "failed"}

#: tool -> (component ``type`` value, required top-level keys)
REQUIRED: dict[str, tuple[str | None, set[str]]] = {
    "render_action_card": ("action-card", {"type", "title", "action"}),
    "render_key_value_list": ("key-value-list", {"type"}),
    "render_document_preview": (
        "document-preview", {"type", "documentType", "source", "pages"}
    ),
    "render_workflow_stepper": ("workflow-stepper", {"type", "title", "steps"}),
    "render_status_card": ("status-card", {"type", "title", "fields"}),
    "render_decision_card": ("decision-card", {"type", "title", "question", "options"}),
    "render_download_card": ("download-card", {"type", "files"}),
    "render_builder": (None, {"title", "blocks"}),
}  # fmt: skip


def payloads() -> list[tuple[str, dict[str, Any]]]:
    out = []
    for tool, body in FENCE.findall(TEXT):
        out.append((tool, json.loads(body)))
    return out


def section(title: str) -> str:
    """From ``title`` to the next level-1/2 heading (headings inside code fences don't count)."""
    lines = TEXT[TEXT.index(title) :].splitlines()
    out = [lines[0]]
    fenced = False
    for line in lines[1:]:
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(r"#{1,2} ", line):
            break
        out.append(line)
    return "\n".join(out)


def test_there_is_a_payload_for_every_step_of_the_flow() -> None:
    tools = [t for t, _ in payloads()]
    for needed in (
        "render_action_card", "render_builder", "render_workflow_stepper", "render_key_value_list",
        "render_status_card", "render_decision_card", "render_document_preview",
    ):  # fmt: skip
        assert needed in tools, needed
    assert tools.count("render_action_card") == 3  # the three starter tiles
    assert all(t in REQUIRED for t in tools), "a payload for a tool this test does not know"


@pytest.mark.parametrize(("tool", "payload"), payloads())
def test_every_payload_is_valid_json_with_the_required_keys(
    tool: str, payload: dict[str, Any]
) -> None:
    comp_type, required = REQUIRED[tool]
    assert required <= set(payload), f"{tool} lacks {required - set(payload)}"
    if comp_type:
        assert payload["type"] == comp_type


def test_action_card_actions_are_message_kind_with_a_value() -> None:
    for tool, p in payloads():
        if tool == "render_action_card":
            a = p["action"]
            assert a["kind"] == "message" and a["label"] and a["value"]


def test_the_tiles_are_the_three_in_the_plan() -> None:
    titles = [p["title"] for t, p in payloads() if t == "render_action_card"]
    assert titles == [
        "Skill from a website (record it)",
        "Skill from an API or docs",
        "Agent from a process description",
    ]


def test_builder_forms_follow_the_builder_tool_schema() -> None:
    forms = [p for t, p in payloads() if t == "render_builder"]
    assert len(forms) == 2
    for form in forms:
        assert form.get("builder_kind", "form") in ("form", "stepper", "escalation")
        assert "step_id" not in form  # no process step is bound to a builder chat
        for block in form["blocks"]:
            assert {"id", "title", "fields"} <= set(block)
            for f in block["fields"]:
                assert {"name", "label", "field_type"} <= set(f), f
                assert f["field_type"] in FIELD_TYPES
                if f["field_type"] in ("select", "multi_select"):
                    assert all({"value", "label"} <= set(o) for o in f["options"])
                if f["field_type"] == "entity_picker":
                    assert f["source"]
                    assert "default_value" not in f  # takes an id, never a name
                    for cf in f.get("create_fields", []):
                        assert {"name", "label"} <= set(cf)
    scope = forms[0]["blocks"][0]["fields"]
    picker = next(f for f in scope if f["field_type"] == "entity_picker")
    assert picker["source"] == "workstreams" and picker["allow_create"] is True
    assert {"name", "goal", "inputs_outputs", "workstream"} <= {f["name"] for f in scope}
    process = {f["name"] for f in forms[1]["blocks"][0]["fields"]}
    assert {"steps", "gates", "skills"} <= process


def test_stepper_and_decision_card_details() -> None:
    for tool, p in payloads():
        if tool == "render_workflow_stepper":
            assert 2 <= len(p["steps"]) <= 12 and p["id"]
            for s in p["steps"]:
                assert {"id", "title", "status"} <= set(s) and s["status"] in STEP_STATUS
            assert p["active_step"] in {s["id"] for s in p["steps"]}
        if tool == "render_decision_card":
            assert 1 <= len(p["options"]) <= 5 and all("label" in o for o in p["options"])
        if tool == "render_status_card":
            assert all({"label", "value"} <= set(f) for f in p["fields"])
            assert p.get("status", "default") in {
                "default",
                "success",
                "warning",
                "danger",
                "primary",
            }
        if tool == "render_document_preview":
            assert p["documentType"] in ("pdf", "excel") and "filename" in p["source"]
            assert all("label" in page for page in p["pages"])
        if tool == "render_key_value_list":
            for g in p.get("groups", []):
                assert {"label", "items"} <= set(g)
                assert all({"key", "value"} <= set(i) for i in g["items"])


def test_publish_is_gated_by_a_decision_card_and_agents_stay_disabled() -> None:
    publish = [p for t, p in payloads() if t == "render_decision_card" and p["id"] == "publish"]
    assert len(publish) == 1 and publish[0]["title"] == "Publish to the org?"
    guided = section("## Guided building")
    assert "abcd skill push" in guided and "abcd agent push" in guided
    # tested in the Builder workstream, published with --publish, a person enables it
    assert "Builder workstream" in guided and "--publish" in guided
    assert "--test-in-builder" in guided and "agent update <process_id> --enable" in guided
    assert "never enable an agent for real use" in guided.replace("\n", " ")
    card = guided.index('"id": "publish"')
    assert card < guided.index(
        "abcd agent push <name> --yes --publish"
    )  # publish only after the card
    assert card < guided.rindex("abcd skill push workspaces/cloud")  # the real push follows it


def test_every_abcd_command_in_the_section_exists() -> None:
    import cli.abcd as abcd_main
    from cli.core.registry import Registry  # noqa: F401

    guided = section("## Guided building")
    found = re.findall(r"^\s*abcd ([^\n]+)", guided, re.M) + re.findall(r"`abcd ([^`]+)`", guided)
    cmds = set()
    for text in found:
        words: list[str] = []
        for w in text.split():
            if not re.fullmatch(r"[a-z][a-z-]*", w) or len(words) == 2:
                break
            words.append(w)
        cmds.add(" ".join(words))
    assert {"skill new", "agent new", "skill push", "agent push", "session start"} <= cmds
    for c in sorted(cmds):
        words = c.split()
        with pytest.raises(SystemExit) as exc:
            abcd_main.main([*words, "--help"])
        assert exc.value.code == 0, c


def test_guided_section_is_first_and_the_existing_content_is_intact() -> None:
    assert TEXT.index("## Guided building") < TEXT.index("## Step 0: install abcd")
    assert TEXT.index("## Rules") < TEXT.index("## Guided building")
    for kept in (
        "## Build and publish a skill or agent",
        "# Capture a website into a skill (NoUI)",
        "cd /workspace/noui && python scripts/<script>.py",
        "## Step N0",
        "scripts/capture_record.py",
        "--execution-mode harness",
    ):
        assert kept in TEXT, kept


def test_the_section_names_no_secret_url_or_tenant() -> None:
    guided = section("## Guided building")
    assert not re.search(r"https?://", guided)
    assert "ADOPT_HARNESS_BEARER_TOKEN" in guided
