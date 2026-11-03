"""Scenario (b): audit, push and verify a skill; push a plugin; tiers, conflicts and secrets."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from tests.integration.conftest import run_subprocess, write_env_file
from tests.integration.fake_bff import DEFAULT_CLIENT_ID, DEFAULT_SECRET, FakeBFFServer

pytestmark = pytest.mark.integration


def test_audit_is_local_and_free(cli, bff: FakeBFFServer, fixture_skill: Path) -> None:
    res = cli("skill", "audit", str(fixture_skill))
    assert res.code == 0, res.text
    assert "findings: 0" in res.out
    assert bff.requests == []  # the audit never touches the platform


def test_push_then_verify_round_trips_the_skill(
    cli, bff: FakeBFFServer, fixture_skill: Path
) -> None:
    res = cli("skill", "push", str(fixture_skill))
    assert res.code == 0, res.text
    assert "Live skill matches local intent" in res.out
    stored = bff.state.org_skills["invoice-triage"]
    assert stored["md"] == (fixture_skill / "SKILL.md").read_bytes()
    assert set(stored["aux"]) == {"references/rules.md", "scripts/summarize.py"}

    verify = cli("skill", "verify", "invoice-triage", "--json")
    assert verify.code == 0, verify.text
    assert verify.json["ok"] is True
    assert bff.forbidden_calls() == []
    # push = upload + verify (GET); verify = one more GET. Nothing else was needed.
    assert len(bff.state.calls("POST", "/v1/end-user/agent-harness/skills")) == 1


def test_files_route_serves_text_and_refuses_binary(
    cli, bff: FakeBFFServer, fixture_skill: Path
) -> None:
    (fixture_skill / "references" / "logo.png").write_bytes(b"\x89PNG\r\n\x00\x01binary")
    assert cli("skill", "push", str(fixture_skill)).code == 0
    from cli.builder_common.platform import BuilderPlatformClient
    from cli.harness_common.api_client import get_harness_client_for_env

    client = BuilderPlatformClient.wrap(get_harness_client_for_env("acme"))
    snap = client.get_skill_files("invoice-triage")
    assert snap.aux["references/rules.md"].state == "text"
    assert snap.aux["references/logo.png"].state == "binary"  # 415 from the BFF
    assert "references/logo.png" in snap.partial_paths


def test_large_text_file_is_truncated_with_marker(
    cli, bff: FakeBFFServer, fixture_skill: Path
) -> None:
    (fixture_skill / "references" / "big.md").write_text("line of text\n" * 40_000)  # ~520 KB
    assert cli("skill", "push", str(fixture_skill)).code == 0
    from cli.builder_common.platform import BuilderPlatformClient
    from cli.harness_common.api_client import get_harness_client_for_env

    client = BuilderPlatformClient.wrap(get_harness_client_for_env("acme"))
    got = client.read_skill_file("invoice-triage", "references/big.md", size=520_000)
    assert got.state == "text"
    assert got.truncated is True
    assert "[... truncated: only the first" in got.content


def test_second_push_conflicts_until_replace(cli, bff: FakeBFFServer, fixture_skill: Path) -> None:
    assert cli("skill", "push", str(fixture_skill)).code == 0
    again = cli("skill", "push", str(fixture_skill))
    assert again.code == 1
    assert "409" in again.text and "--replace" in again.text
    (fixture_skill / "references" / "rules.md").write_text("Approve under 900.\n")
    replaced = cli("skill", "push", str(fixture_skill), "--replace")
    assert replaced.code == 0, replaced.text
    assert (
        bff.state.org_skills["invoice-triage"]["aux"]["references/rules.md"]
        == b"Approve under 900.\n"
    )


def test_org_skill_hides_a_default_skill_of_the_same_name(
    cli, bff: FakeBFFServer, fixture_skill: Path
) -> None:
    bff.state.add_default_skill("invoice-triage", "The built-in version")
    clash = cli("skill", "push", str(fixture_skill))
    assert clash.code == 1 and "default skill" in clash.text  # 409: a default of that name exists
    assert (
        bff.state.default_skills["invoice-triage"]["meta"]["description"] == "The built-in version"
    )

    assert cli("skill", "push", str(fixture_skill), "--replace").code == 0
    from cli.harness_common.api_client import get_harness_client_for_env

    items = get_harness_client_for_env("acme").list_skills()["items"]
    mine = [i for i in items if i["name"] == "invoice-triage"]
    assert len(mine) == 1 and mine[0]["source"] == "org"  # org wins; the default is hidden


def test_delete_needs_yes_and_only_touches_the_org_tier(
    cli, bff: FakeBFFServer, fixture_skill: Path
) -> None:
    bff.state.add_default_skill("builtin-skill")
    assert cli("skill", "push", str(fixture_skill)).code == 0
    refused = cli("skill", "delete", "invoice-triage")
    assert refused.code == 1 and "invoice-triage" in bff.state.org_skills
    gone = cli("skill", "delete", "invoice-triage", "--yes")
    assert gone.code == 0 and "invoice-triage" not in bff.state.org_skills
    assert (
        cli("skill", "delete", "builtin-skill", "--yes").code == 0
    )  # 0 objects: nothing to remove
    assert "builtin-skill" in bff.state.default_skills


def test_secret_in_a_skill_never_reaches_the_platform(
    cli, bff: FakeBFFServer, fixture_skill: Path
) -> None:
    (fixture_skill / "references" / "creds.md").write_text("key: AKIA" + "ABCDEFGHIJKLMNOP\n")
    res = cli("skill", "push", str(fixture_skill))
    assert res.code == 1
    assert "Refusing to upload" in res.text
    assert bff.state.org_skills == {}
    assert not [r for r in bff.requests if r["method"] == "POST" and r["path"].endswith("/skills")]


def test_bad_frontmatter_is_rejected_locally_and_by_the_platform(
    cli, bff: FakeBFFServer, tmp_path: Path
) -> None:
    bad = tmp_path / "bad-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("# no frontmatter at all\n")
    assert cli("skill", "push", str(bad)).code == 1
    assert bff.state.org_skills == {}
    from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env

    client = get_harness_client_for_env("acme")
    with pytest.raises(HarnessAPIError) as exc:  # the BFF enforces it too (422)
        client.upload_skill("bad-skill", base64.b64encode(b"# nope\n").decode())
    assert exc.value.status_code == 422


def test_plugin_push_is_multipart_and_visible_in_the_catalog(
    cli, bff: FakeBFFServer, fixture_plugin: Path
) -> None:
    res = cli("plugin", "push", str(fixture_plugin))
    assert res.code == 0, res.text
    assert "Plugin is live" in res.out
    plug = bff.state.plugins["acme-finance"]
    assert set(plug["skills"]) == {"ledger-check"}
    from cli.harness_common.api_client import get_harness_client_for_env

    client = get_harness_client_for_env("acme")
    cat = {i["name"]: i for i in client.list_skills()["items"]}
    assert cat["ledger-check"]["plugin"] == "acme-finance"
    assert client.list_plugins()["items"][0]["skill_count"] == 1
    clash = cli("plugin", "push", str(fixture_plugin))
    assert clash.code == 1 and "--replace" in clash.text
    assert cli("plugin", "push", str(fixture_plugin), "--replace").code == 0


def test_deploy_default_needs_an_adopt_email(
    cli, bff: FakeBFFServer, fixture_skill: Path, acme: Path
) -> None:
    denied = cli("skill", "deploy-default", str(fixture_skill))
    assert denied.code == 1 and "403" in denied.text
    assert bff.state.default_skills == {}

    bff.state.add_pat("staff-client", "staff-secret", email="builder@adopt.ai")
    write_env_file(acme, bff.url, "staff-client", "staff-secret")
    ok = cli("skill", "deploy-default", str(fixture_skill), "--bundle-version", "7")
    assert ok.code == 0, ok.text
    assert bff.state.default_skills["invoice-triage"]["bundle_version"] == "7"


def test_non_admin_pat_cannot_write(
    cli, bff: FakeBFFServer, fixture_skill: Path, acme: Path
) -> None:
    bff.state.add_pat("reviewer-client", "reviewer-secret", roles=["Reviewer"])
    write_env_file(acme, bff.url, "reviewer-client", "reviewer-secret")
    res = cli("skill", "push", str(fixture_skill))
    assert res.code == 1 and "403" in res.text
    assert bff.state.org_skills == {}
    created = cli("agent", "create", "tri", "Triage")
    assert created.code == 1 and "Admin role" in created.text
    assert cli("agent", "list").code == 0  # reads work for any JWT


def test_dry_run_changes_nothing(cli, bff: FakeBFFServer, fixture_skill: Path) -> None:
    res = cli("skill", "push", str(fixture_skill), "--dry-run")
    assert res.code == 0 and "DRY RUN" in res.out
    assert bff.requests == []


def test_push_in_a_real_subprocess(bff: FakeBFFServer, fixture_skill: Path, tmp_path: Path) -> None:
    """The same flow through ``python -m cli.abcd`` with only ``ABCD_WORKSPACES_DIR`` set."""
    root = tmp_path / "sub-workspaces"
    root.mkdir()
    created = run_subprocess(root, "ws", "create", "--id", "acme", "--name", "Acme")
    assert created.code == 0, created.text
    write_env_file(root / "acme", bff.url, DEFAULT_CLIENT_ID, DEFAULT_SECRET)
    pushed = run_subprocess(root, "skill", "push", str(fixture_skill), "--env", "acme", "--json")
    assert pushed.code == 0, pushed.text
    doc = json.loads(pushed.out.strip().splitlines()[-1])
    assert doc["ok"] is True and doc["command"] == "skill push"
    assert "invoice-triage" in bff.state.org_skills
    assert bff.forbidden_calls() == []
    assert DEFAULT_SECRET not in pushed.text
