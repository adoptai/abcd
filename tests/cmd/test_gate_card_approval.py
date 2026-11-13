"""Cloud release approval through the ``abcd-release-gate`` HITL card, and the testing-only
same-identity override (release_approval, gate_card; PLAN.md section 21). Fakes only."""

from __future__ import annotations

import json
from typing import Any

import pytest

from cli.builder_common import gate_card
from cli.builder_common.release_approval import (
    OVERRIDE_ENV,
    OVERRIDE_MODE,
    OVERRIDE_WARNING,
    release_approval_token,
    require_release_approval,
)

from .release_fixtures import AGENT, SKILL_MD, Env, build_env, cycle
from .test_review_release import CONV, _cloud, error, human, ready

REL = f"{AGENT}@v1"
BID = "builder-1"


def card(release: str, sha: str, action: str = "release", bid: str = BID) -> dict[str, Any]:
    spec = gate_card.card_spec(release, sha, action)
    return {
        "kind": "builder",
        "builder_id": bid,
        "step_id": spec["step_id"],
        "builder_kind": spec["builder_kind"],
        "blocks": [spec["block"]],
    }


def submit(
    release: str,
    sha: str,
    action: str = "release",
    decision: str = "approve",
    bid: str = BID,
    step: str = gate_card.STEP_ID,
    author: str = "fde-human",
) -> dict[str, str]:
    body = {
        "blocks": [
            {
                "id": gate_card.BLOCK_ID,
                "title": "t",
                "values": {
                    "release": release,
                    "action": action,
                    "sha": sha[:12],
                    "decision": decision,
                },
            }
        ],
        "step_id": step,
        "builder_id": bid,
    }
    return human("<builder_data>" + json.dumps(body) + "</builder_data>", author=author)


def approved(release: str, sha: str, action: str = "release") -> list[dict[str, Any]]:
    return [card(release, sha, action), submit(release, sha, action)]


def refused(env: Env, capsys: Any, *args: str) -> str:
    code, doc = env.run(*(args or ("release", AGENT, "--yes")), capsys=capsys)
    assert code == 1, doc
    assert env.client.find_process_by_name(AGENT) is None
    return error(doc)


def cloud_env(tmp_path, monkeypatch, capsys) -> tuple[Env, str]:  # type: ignore[no-untyped-def]
    monkeypatch.delenv(OVERRIDE_ENV, raising=False)
    env = build_env(tmp_path, monkeypatch)
    sha = ready(env, capsys)
    _cloud(monkeypatch, env)
    return env, sha


def test_an_approved_gate_card_releases(tmp_path, monkeypatch, capsys) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    env.client.conversations[CONV] = approved(REL, sha)
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    assert env.client.find_process_by_name(AGENT) is not None
    assert doc["data"]["approval_mode"] == "human"
    manifest = json.loads(env.docs.get(f"builder-ops/releases/{AGENT}/v1/manifest.json"))
    assert manifest["approval_mode"] == "human"


@pytest.mark.parametrize(
    "what", ["card-release", "card-sha", "card-action", "sub-release", "sub-sha", "sub-action"]
)
def test_a_card_or_submission_for_another_release_sha_or_action_is_refused(
    tmp_path, monkeypatch, capsys, what
) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    other = {"release": "other-agent@v1", "sha": "0" * 12, "action": "rollback"}
    key = what.split("-")[1]
    good = {"release": REL, "sha": sha, "action": "release"}
    bad = {**good, key: other[key]}
    if what.startswith("card"):
        events = [card(**bad), submit(**good)]
    else:
        events = [card(**good), submit(**bad)]
    env.client.conversations[CONV] = events
    assert "no release approval from a human" in refused(env, capsys)
    # the override never relaxes what is approved
    monkeypatch.setenv(OVERRIDE_ENV, "1")
    assert "no release approval from a human" in refused(env, capsys)


def test_a_card_without_a_builder_data_submission_is_refused(tmp_path, monkeypatch, capsys) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    env.client.conversations[CONV] = [card(REL, sha), human("approve")]
    assert "no release approval from a human" in refused(env, capsys)
    env.client.conversations[CONV] = [card(REL, sha)]
    assert "no release approval from a human" in refused(env, capsys)
    # a submission with nothing to submit it for
    env.client.conversations[CONV] = [submit(REL, sha)]
    assert "no release approval from a human" in refused(env, capsys)
    monkeypatch.setenv(OVERRIDE_ENV, "1")
    env.client.conversations[CONV] = [card(REL, sha)]
    assert "no release approval from a human" in refused(env, capsys)


def test_a_mismatched_builder_or_step_id_or_early_submission_is_refused(
    tmp_path, monkeypatch, capsys
) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    for events in (
        [card(REL, sha), submit(REL, sha, bid="forged")],
        [card(REL, sha), submit(REL, sha, step="release")],
        [submit(REL, sha), card(REL, sha)],  # the card came after the answer
    ):
        env.client.conversations[CONV] = events
        assert "no release approval from a human" in refused(env, capsys)


def test_a_reject_decision_is_refused(tmp_path, monkeypatch, capsys) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    for decision in ("reject", "no", ""):
        env.client.conversations[CONV] = [card(REL, sha), submit(REL, sha, decision=decision)]
        assert "no release approval from a human" in refused(env, capsys)
    monkeypatch.setenv(OVERRIDE_ENV, "1")
    env.client.conversations[CONV] = [card(REL, sha), submit(REL, sha, decision="reject")]
    assert "no release approval from a human" in refused(env, capsys)


def _process_state(status: str) -> dict[str, Any]:
    return {"kind": "process_state", "state": {"steps": [{"id": "approve", "status": status}]}}


def test_a_process_state_that_is_not_done_refuses_and_done_accepts(
    tmp_path, monkeypatch, capsys
) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    env.client.conversations[CONV] = [*approved(REL, sha), _process_state("review")]
    assert "no release approval from a human" in refused(env, capsys)
    env.client.conversations[CONV] = [*approved(REL, sha), _process_state("done")]
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0


def test_a_submission_by_the_pat_is_refused_without_the_flag(tmp_path, monkeypatch, capsys) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    env.client.conversations[CONV] = [card(REL, sha), submit(REL, sha, author="svc-pat")]
    assert "no release approval from a human" in refused(env, capsys)
    # an unattributed turn cannot be told from the PAT's own either
    unattributed = {"kind": "user_message", "text": submit(REL, sha)["text"]}
    env.client.conversations[CONV] = [card(REL, sha), unattributed]
    assert "no release approval from a human" in refused(env, capsys)


def test_the_flag_accepts_the_pat_author_with_a_warning_and_records_the_mode(
    tmp_path, monkeypatch, capsys
) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    env.client.conversations[CONV] = [card(REL, sha), submit(REL, sha, author="svc-pat")]
    monkeypatch.setenv(OVERRIDE_ENV, "1")
    # the warning, from the check itself
    mode = require_release_approval(env.client, REL, sha, env.ws)
    err = capsys.readouterr().err
    assert mode == OVERRIDE_MODE and OVERRIDE_WARNING in err
    assert "runner's own identity" in err and "service-account PAT" in err
    # the release record
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    assert doc["data"]["approval_mode"] == "same_identity_override"
    manifest = json.loads(env.docs.get(f"builder-ops/releases/{AGENT}/v1/manifest.json"))
    assert manifest["approval_mode"] == "same_identity_override"


def test_a_private_conversation_needs_the_flag(tmp_path, monkeypatch, capsys) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    env.client.conversations[CONV] = approved(REL, sha)
    env.client.shared[CONV] = False
    assert "not a workstream-shared run" in refused(env, capsys)
    monkeypatch.setenv(OVERRIDE_ENV, "1")
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    assert doc["data"]["approval_mode"] == "same_identity_override"


def test_the_flag_never_replaces_a_missing_approval(tmp_path, monkeypatch, capsys) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    monkeypatch.setenv(OVERRIDE_ENV, "1")
    env.client.shared[CONV] = False
    env.client.conversations[CONV] = [
        {"kind": "assistant_message", "text": release_approval_token(REL, sha)},
        human("yes, go ahead"),
    ]
    assert "no release approval from a human" in refused(env, capsys)
    # the agent's own card with nobody answering it
    env.client.conversations[CONV] = [card(REL, sha)]
    assert "no release approval from a human" in refused(env, capsys)


def test_a_human_approval_needs_no_flag_and_no_warning(tmp_path, monkeypatch, capsys) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    env.client.conversations[CONV] = approved(REL, sha)
    monkeypatch.setenv(OVERRIDE_ENV, "1")  # set, but not needed: the strict rules pass
    assert require_release_approval(env.client, REL, sha, env.ws) == "human"
    assert capsys.readouterr().err == ""


def test_the_token_path_still_works_and_the_flag_relaxes_its_author(
    tmp_path, monkeypatch, capsys
) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    tok = release_approval_token(REL, sha)
    env.client.conversations[CONV] = [human(tok, author="svc-pat")]
    assert "no release approval from a human" in refused(env, capsys)
    env.client.conversations[CONV] = [human(tok)]
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    assert doc["data"]["approval_mode"] == "human"


def test_a_pat_authored_token_is_accepted_with_the_flag(tmp_path, monkeypatch, capsys) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    env.client.conversations[CONV] = [human(release_approval_token(REL, sha), author="svc-pat")]
    monkeypatch.setenv(OVERRIDE_ENV, "1")
    assert require_release_approval(env.client, REL, sha, env.ws) == OVERRIDE_MODE


def _two_versions(env: Env, capsys: Any) -> str:
    sha1 = ready(env, capsys)
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0
    cycle(env, capsys, skill_text=SKILL_MD + "\nSecond version.\n")
    env.merge_to_main()
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0
    return sha1


def test_a_rollback_is_approved_by_a_rollback_card_only(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv(OVERRIDE_ENV, raising=False)
    env = build_env(tmp_path, monkeypatch)
    sha1 = _two_versions(env, capsys)
    _cloud(monkeypatch, env)
    # a release card for the same version and sha does not authorise a rollback
    env.client.conversations[CONV] = approved(REL, sha1, "release")
    code, doc = env.run("rollback", AGENT, "--yes", capsys=capsys)
    assert code == 1 and f"{REL}: no rollback approval from a human" in error(doc)
    env.client.conversations[CONV] = approved(REL, sha1, "rollback")
    code, doc = env.run("rollback", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    assert doc["data"]["approval_mode"] == "human"


def test_gate_card_output_is_stable(tmp_path, monkeypatch, capsys) -> None:
    env, sha = cloud_env(tmp_path, monkeypatch, capsys)
    code, doc = env.run("release", "gate-card", AGENT, capsys=capsys)
    assert code == 0, doc
    data = doc["data"]
    s12 = sha[:12]
    assert data["step_id"] == "approve" and data["action"] == "release"
    assert data["values"] == {"release": REL, "action": "release", "sha": s12}
    block = data["block"]
    assert block["id"] == "release-approval"
    assert block["context"] == f"release={REL} action=release sha={s12}"
    assert [(f["name"], f["field_type"]) for f in block["fields"]] == [
        ("release", "text"),
        ("action", "text"),
        ("sha", "text"),
        ("decision", "select"),
        ("note", "textarea"),
    ]
    assert block["fields"][3]["options"] == ["approve", "reject"]
    assert data["title"] == f"Approve release of {REL}"
    # what it prints is what the checker accepts
    env.client.conversations[CONV] = [
        {"kind": "builder", "builder_id": BID, "step_id": data["step_id"], "blocks": [block]},
        submit(REL, sha),
    ]
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0


def test_gate_card_for_a_rollback_names_the_target(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv(OVERRIDE_ENV, raising=False)
    env = build_env(tmp_path, monkeypatch)
    sha1 = _two_versions(env, capsys)
    code, doc = env.run("release", "gate-card", AGENT, "--action", "rollback", capsys=capsys)
    assert code == 0, doc
    assert doc["data"]["values"] == {"release": REL, "action": "rollback", "sha": sha1[:12]}
    assert doc["data"]["title"] == f"Approve rollback of {REL}"
    code, doc = env.run("release", "gate-card", capsys=capsys)
    assert code == 2
