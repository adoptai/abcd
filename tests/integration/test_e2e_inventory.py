"""Read-only inventory commands against the fake BFF."""

from __future__ import annotations

import pytest

from tests.integration.fake_bff import FakeBFFServer

pytestmark = pytest.mark.integration


def test_inventory_commands_list_what_the_platform_has(cli, bff: FakeBFFServer) -> None:
    for argv, key in (
        (("skill", "list"), "items"),
        (("plugin", "list"), "items"),
        (("workstream", "list"), "items"),
        (("session", "models"), "items"),
    ):
        res = cli(*argv, "--json")
        assert res.code == 0, res.text
        assert isinstance(res.json["data"][key], list)
    models = cli("session", "models", "--json").json["data"]["items"]
    assert any(m["model_id"] == "claude-haiku-4-5" for m in models)
    assert bff.forbidden_calls() == []


def test_skill_list_filters_by_tier(cli, bff: FakeBFFServer, fixture_skill) -> None:
    assert cli("skill", "push", str(fixture_skill)).code == 0
    org = cli("skill", "list", "--source", "org", "--json").json["data"]["items"]
    assert [r["name"] for r in org] == ["invoice-triage"]
    default = cli("skill", "list", "--source", "default", "--json").json["data"]["items"]
    assert all(r["source"] == "default" for r in default)


def test_workstream_ensure_reuses_the_default_docstore(cli, bff: FakeBFFServer) -> None:
    """The BFF links a 'Workstream Files' store to every new workstream; ensure must not add a 2nd."""
    assert cli("workstream", "ensure", "abcd-t").code == 0
    wid = next(w["id"] for w in bff.state.workstreams.values() if w["name"] == "abcd-t")
    linked = [s for s in bff.state.stores.values() if wid in s["workstream_ids"]]
    assert [s["name"] for s in linked] == ["Workstream Files"]
    assert cli("workstream", "ensure", "abcd-t").code == 0  # cached: still one
    assert len([s for s in bff.state.stores.values() if wid in s["workstream_ids"]]) == 1
