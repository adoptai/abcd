"""Regression tests for the review findings on release integrity, approvals and branches.

1. release deploys exactly what ``stage`` froze;  2. no self-asserted approvals in the cloud;
3. the release branch is cut from ``origin/<default>``;  7. an ``-rc`` tag is never reused.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from cli import builder_release
from cli.builder_common import repo as rp
from cli.builder_common.cloudmode import write_marker
from cli.builder_common.errors import RefusedError
from cli.builder_common.release_approval import (
    approved_releases,
    release_approval_token,
)
from cli.builder_common.versions import VersionLedger

from .release_fixtures import AGENT, SKILL_MD, WRITES, Env, build_env, cycle, git

Capsys = pytest.CaptureFixture[str]
CONV = "conv-cloud-1"


def error(doc: dict) -> str:
    return str(doc.get("error", ""))


def ready(env: Env, capsys: Capsys) -> str:
    """Staged, tested, merged. Returns the staged sha."""
    cycle(env, capsys)
    env.merge_to_main()
    return str(VersionLedger(env.rows).latest("agent", AGENT, "staged").git_sha)


# -- 1. release deploys exactly what stage froze ------------------------------------------------


def test_release_refuses_a_newer_staged_push_and_uploads_nothing(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    ready(env, capsys)
    # a change after `stage`: committed and pushed to the shadows (draft v2), never staged
    env.write("harness/skills/tax-calc/SKILL.md", SKILL_MD + "\nNot staged.\n")
    env.commit("change after stage")
    env.merge_to_main()
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    ledger = VersionLedger(env.rows)
    assert ledger.latest("skill", "tax-calc").state == "draft"
    assert ledger.latest("skill", "tax-calc", "staged").number == 1
    writes_before = list(env.client.calls)
    live_before = dict(env.client.org_skills)

    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1, doc
    assert "a newer staged push exists; run `abcd stage" in error(doc)
    assert "tax-calc" not in env.client.org_skills  # nothing live
    assert env.client.org_skills.keys() == live_before.keys()
    assert [c for c in env.client.calls[len(writes_before) :] if c[0] in WRITES] == []
    assert env.client.find_process_by_name(AGENT) is None
    assert env.gh.releases == [] and env.gh.prs == []


def test_release_refuses_a_newer_agent_draft(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    ready(env, capsys)
    p = env.ws / f"harness/agents/{AGENT}/instructions.md"
    env.write(f"harness/agents/{AGENT}/instructions.md", p.read_text() + "Also this.\n")
    env.commit("agent change")
    env.merge_to_main()
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    assert VersionLedger(env.rows).latest("agent", AGENT).state == "draft"
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "a newer staged push exists" in error(doc)
    assert env.client.find_process_by_name(AGENT) is None


def test_release_refuses_a_tree_that_differs_from_the_staged_commit(
    tmp_path, monkeypatch, capsys
) -> None:
    """Even a commit never pushed to the shadows (no draft record) cannot ride along."""
    env = build_env(tmp_path, monkeypatch)
    ready(env, capsys)
    env.write("harness/skills/tax-calc/scripts/calc.py", "def calc(x):\n    return x * 3\n")
    env.commit("sneaky")
    env.merge_to_main()
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1, doc
    assert "tax-calc" not in env.client.org_skills
    assert env.client.find_process_by_name(AGENT) is None


def test_promote_only_takes_the_staged_record(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    cycle(env, capsys)
    ledger = VersionLedger(env.rows)
    staged = ledger.latest("skill", "tax-calc", "staged")
    # a later push leaves a draft: it is not what the staged version stands on
    env.write("harness/skills/tax-calc/SKILL.md", SKILL_MD + "\nMore.\n")
    env.commit("more")
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    draft = ledger.latest("skill", "tax-calc")
    assert draft.state == "draft" and draft.version_id != staged.version_id

    class W:
        svc = type("S", (), {"rows": env.rows})()
        ledger = VersionLedger(env.rows)

    hashed = {"bundle_sha256": "x" * 64, "skill_md_sha256": "y" * 64, "aux": []}
    with pytest.raises(RefusedError, match="only the staged record"):
        builder_release.promote_skill(W, "tax-calc", hashed, "abc", draft)  # type: ignore[arg-type]
    rec = builder_release.promote_skill(W, "tax-calc", hashed, "abc", staged)  # type: ignore[arg-type]
    assert rec.version_id == staged.version_id and rec.state == "live"
    assert ledger.get(draft.version_id).state == "draft"  # untouched


# -- 2. no self-asserted approvals in the cloud -------------------------------------------------


def _cloud(monkeypatch: pytest.MonkeyPatch, env: Env | None = None) -> None:
    """A cloud run: the variable, and (given ``env``) the marker ``cloud bootstrap`` writes."""
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", CONV)
    if env is not None:
        write_marker(env.ws, CONV)


def human(text: str, author: str = "fde-human") -> dict[str, str]:
    """A user_message the platform attributes to ``author`` (shared conversation transcript)."""
    return {"kind": "user_message", "text": text, "author_user_id": author}


@pytest.mark.parametrize(
    "flags",
    [
        ("--force-reason", "because", "--lead", "lead-a"),
        ("--lead", "lead-a"),
        ("--accept-regression", "c1=fine"),
        ("--local-approval",),
    ],
)
def test_cloud_refuses_self_asserted_approvals(tmp_path, monkeypatch, capsys, flags) -> None:
    env = build_env(tmp_path, monkeypatch)
    ready(env, capsys)
    _cloud(monkeypatch)
    before = list(env.client.calls)
    code, doc = env.run("release", AGENT, "--yes", *flags, capsys=capsys)
    assert code == 1, doc
    assert (
        "forced releases, accepted regressions and local approvals are made by a human "
        "locally, never in a cloud run" in error(doc)
    )
    assert env.client.calls == before and env.client.find_process_by_name(AGENT) is None


def test_cloud_refuses_local_approval_on_push_and_redeploy(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    _cloud(monkeypatch)
    code, doc = env.run(
        "push", AGENT, "--staged", "--local-approval", "--approver", "x", capsys=capsys
    )
    assert code == 1 and "never in a cloud run" in error(doc)
    code, doc = env.run(
        "release", "--redeploy", f"{AGENT}@v1", "--lead", "me", "--yes", capsys=capsys
    )
    assert code == 1 and "never in a cloud run" in error(doc)


def test_cloud_release_needs_the_users_token_not_just_yes(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    sha = ready(env, capsys)
    _cloud(monkeypatch, env)
    good = release_approval_token(f"{AGENT}@v1", sha)
    # no token at all; a token only in assistant/tool output; a token for another sha or release
    env.client.conversations[CONV] = [
        {"kind": "assistant_message", "text": good},
        {"kind": "tool_result", "text": good},
        human(release_approval_token(f"{AGENT}@v1", "0" * 12)),
        human(release_approval_token("other-agent@v1", sha)),
        human("yes, go ahead"),
    ]
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "no release approval from a human" in error(doc)
    assert "a human sends approvals from the chat UI, not the agent" in error(doc)
    assert (
        env.client.find_process_by_name(AGENT) is None and "tax-calc" not in env.client.org_skills
    )
    # the dry run needs no token
    assert env.run("release", AGENT, "--dry-run", capsys=capsys)[0] == 0

    env.client.conversations[CONV].append(human(f"approved {good}"))
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    assert env.client.find_process_by_name(AGENT) is not None


def _refused_release(env: Env, capsys: Any) -> str:
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1, doc
    assert env.client.find_process_by_name(AGENT) is None
    return error(doc)


def test_cloud_token_must_come_from_a_human_not_the_pat(tmp_path, monkeypatch, capsys) -> None:
    """The platform lets a PAT post into a shared run, so authorship decides (release_approval
    docstring): the PAT's own turn, an unattributed turn and the PAT's own chat are refused."""
    env = build_env(tmp_path, monkeypatch)
    sha = ready(env, capsys)
    _cloud(monkeypatch, env)
    tok = release_approval_token(f"{AGENT}@v1", sha)
    # the agent (the PAT's user) posted the token itself
    env.client.conversations[CONV] = [human(tok, author="svc-pat")]
    assert "no release approval from a human" in _refused_release(env, capsys)
    # unattributed: cannot be told from the PAT's own turn
    env.client.conversations[CONV] = [{"kind": "user_message", "text": tok}]
    assert "no release approval from a human" in _refused_release(env, capsys)
    # a private chat (the transcript is not is_shared): the platform attributes no human turns
    env.client.conversations[CONV] = [human(tok)]
    env.client.shared[CONV] = False
    assert (
        "cloud approvals need a workstream-shared run; the platform attributes human turns "
        "only there" in _refused_release(env, capsys)
    )
    env.client.shared[CONV] = True
    # the PAT identity is unknown
    env.client.pat_ids = set()
    assert "cannot tell which user the PAT" in _refused_release(env, capsys)
    # ... and with everything determinable the human's token is accepted
    env.client.pat_ids = {"svc-pat"}
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)


def test_cloud_approval_conversation_comes_from_the_marker(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    sha = ready(env, capsys)
    tok = release_approval_token(f"{AGENT}@v1", sha)
    env.client.conversations[CONV] = [human(tok)]
    # no marker: the sandbox's conversation is unknown -> refuse (not read from the env alone)
    _cloud(monkeypatch)
    assert "no bootstrap marker" in _refused_release(env, capsys)
    # marker and ADOPT_CONVERSATION_ID disagree -> refuse
    write_marker(env.ws, CONV)
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "conv-other")
    env.client.conversations["conv-other"] = [human(tok)]
    assert "differs from the conversation recorded at bootstrap" in _refused_release(env, capsys)
    # the variable unset: the marker still makes this a cloud run, and its conversation is used
    monkeypatch.delenv("ADOPT_CONVERSATION_ID")
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)


def test_cloud_rollback_needs_the_humans_token_and_live_agent_push_is_refused(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    sha1 = ready(env, capsys)
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0  # local: v1 live
    cycle(env, capsys, skill_text=SKILL_MD + "\nSecond version.\n")
    env.merge_to_main()
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0  # local: v2 live
    _cloud(monkeypatch, env)
    # a direct live push is refused outright in the cloud
    code, doc = env.run("agent", "push", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "in a cloud run live changes go through `abcd release`" in error(doc)
    # a rollback is a live change: it needs the human's token naming the agent and target version
    live = list(env.client.calls)
    env.client.conversations[CONV] = [human("please roll back")]
    code, doc = env.run("rollback", AGENT, "--yes", capsys=capsys)
    assert code == 1 and f"{AGENT}@v1: no rollback approval from a human" in error(doc)
    assert [c for c, _ in env.client.calls[len(live) :] if c in WRITES] == []
    env.client.conversations[CONV].append(
        human(release_approval_token(f"{AGENT}@v1", sha1), author="svc-pat")  # the agent's own
    )
    assert env.run("rollback", AGENT, "--yes", capsys=capsys)[0] == 1
    # a human's RELEASE token for the same version and sha never authorises a rollback
    env.client.conversations[CONV].append(human(release_approval_token(f"{AGENT}@v1", sha1)))
    code, doc = env.run("rollback", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "no rollback approval from a human" in error(doc)
    env.client.conversations[CONV].append(
        human(release_approval_token(f"{AGENT}@v1", sha1, "rollback"))
    )
    code, doc = env.run("rollback", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)


def test_rollback_to_a_version_without_a_git_sha_is_refused_only_in_the_cloud(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    ready(env, capsys)
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0
    cycle(env, capsys, skill_text=SKILL_MD + "\nSecond version.\n")
    env.merge_to_main()
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0
    orig = VersionLedger.get

    def no_sha(self, vid):  # type: ignore[no-untyped-def]
        v = orig(self, vid)
        return dataclasses.replace(v, git_sha=None) if v and vid.endswith("@v1") else v

    monkeypatch.setattr(VersionLedger, "get", no_sha)
    _cloud(monkeypatch, env)
    code, doc = env.run("rollback", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "records no git sha; a cloud rollback" in error(doc)
    monkeypatch.delenv("ADOPT_CONVERSATION_ID")
    (env.ws / ".builder" / "cloud.json").unlink(missing_ok=True)
    code, doc = env.run("rollback", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)  # local: works as before


def test_cloud_release_without_yes_is_still_refused(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    sha = ready(env, capsys)
    _cloud(monkeypatch, env)
    env.client.conversations[CONV] = [human(release_approval_token(f"{AGENT}@v1", sha))]
    code, doc = env.run("release", AGENT, capsys=capsys)
    assert code == 1 and "--yes" in error(doc)


def test_local_release_keeps_the_flags(tmp_path, monkeypatch, capsys) -> None:
    """Local mode: no token needed, and --force-reason/--lead still work."""
    env = build_env(tmp_path, monkeypatch)
    ready(env, capsys)
    monkeypatch.delenv("ADOPT_CONVERSATION_ID", raising=False)
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)


def test_approval_token_parsing_counts_only_user_messages() -> None:
    tok = release_approval_token("a-b@v3", "0123456789abcdef")
    assert tok == (
        '<!--adopt:release-approval v="1" action="release" release="a-b@v3" sha="0123456789ab"-->'
    )
    events = [
        {"kind": "assistant_message", "text": tok},
        {"kind": "user_message", "text": "ok " + tok},
    ]
    assert approved_releases(events) == {("a-b@v3", "0123456789ab")}
    assert approved_releases(events[:1]) == set()
    # the action is matched: a release token never authorises a rollback, nor the reverse
    assert approved_releases(events, "rollback") == set()
    rb = release_approval_token("a-b@v3", "0123456789ab", "rollback")
    assert 'action="rollback"' in rb
    assert approved_releases([{"kind": "user_message", "text": rb}]) == set()
    assert approved_releases([{"kind": "user_message", "text": rb}], "rollback") == {
        ("a-b@v3", "0123456789ab")
    }
    legacy = '<!--adopt:release-approval v="1" release="a-b@v3" sha="0123456789ab"-->'
    assert approved_releases([{"kind": "user_message", "text": legacy}]) == {
        ("a-b@v3", "0123456789ab")
    }
    assert approved_releases([{"kind": "user_message", "text": legacy}], "rollback") == set()


# -- 3. the release branch is cut from origin/<default> -----------------------------------------


def test_release_branch_does_not_carry_the_working_branch_commits(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    ready(env, capsys)
    # extra commits on the working branch, after the staged commit; they never reach main
    env.write("harness/notes/extra.md", "unrelated work in progress\n")
    env.commit("extra work", push=False)
    assert git(env.ws, "ls-files", "harness/notes/extra.md")
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    branch = f"release/{AGENT}@v1"
    assert git(env.bare, "rev-parse", f"{branch}~1") == env.bare_ref("main") or (
        git(env.bare, "merge-base", branch, "main") == env.bare_ref("main")
    )
    changed = set(git(env.bare, "diff", "--name-only", "main", branch).splitlines())
    assert changed == {
        f"harness/evals/reports/{AGENT}/v1.html",
        f"harness/agents/{AGENT}/docs/explainer.html",
        *{c for c in changed if c.startswith(f"harness/agents/{AGENT}/training/")},
        "ADOPT.md",
        "builder.json",  # the live process id the release records
    }
    assert "harness/notes/extra.md" not in changed
    assert git(env.bare, "log", "--format=%s", f"main..{branch}").count("extra work") == 0
    assert rp.current_branch(env.ws) == "feat/agent"


# -- 7. the -rc tag is never reused across commits ----------------------------------------------


def test_restage_after_a_squash_merge_gets_the_next_rc_tag(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    cycle(env, capsys)
    first = f"{AGENT}@v1-rc"
    sha1 = git(env.ws, "rev-parse", first + "^{commit}")
    ledger = VersionLedger(env.rows)
    assert ledger.latest("agent", AGENT, "staged").manifest["rc_tag"] == first
    # squash merge: the same content lands on main as a different commit; the FDE re-stages
    # from it (the same version row is staged again, at the new commit)
    rp.checkout(env.ws, "main")
    rp.run_git(env.ws, "merge", "-q", "--squash", "feat/agent")
    rp.commit(env.ws, "squash: add the agent")
    rp.push(env.ws, "main", allow_default=True)
    squashed = rp.current_sha(env.ws)
    assert squashed != sha1
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 0, error(doc)
    second = f"{AGENT}@v1-rc.1"
    assert doc["data"]["tag"] == second
    assert git(env.bare, "rev-parse", first + "^{commit}") == sha1  # never moved
    assert git(env.bare, "rev-parse", second + "^{commit}") == squashed
    assert ledger.latest("agent", AGENT, "staged").manifest["rc_tag"] == second
    # re-staging the same commit again reuses its own tag
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 0 and doc["data"]["tag"] == second
    # release finds the rc tag through the record and checks it points at the staged commit
    assert env.run("test-staged", AGENT, "--poll-interval", "0", capsys=capsys)[0] == 0
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    manifest = json.loads(env.docs.files[f"builder-ops/releases/{AGENT}/v1/manifest.json"])
    assert manifest["rc_tag"] == second
    assert git(env.bare, "rev-parse", f"{AGENT}@v1^{{commit}}") == squashed


def test_release_refuses_when_the_recorded_rc_tag_points_elsewhere(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    ready(env, capsys)
    rec = VersionLedger(env.rows).latest("agent", AGENT, "staged")
    env.rows.update_row(
        "bld_versions",
        "version_id",
        rec.version_id,
        {"manifest": {**rec.manifest, "rc_tag": f"{AGENT}@v1-rc.9"}},
    )
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "staged tag" in error(doc)
    assert env.client.find_process_by_name(AGENT) is None


def test_self_asserted_flags_are_refused_when_only_the_workspace_marker_says_cloud(
    tmp_path, monkeypatch, capsys
) -> None:
    """The workspace is passed to every cloud check: a marker alone (no variable, cwd elsewhere)
    is a cloud run for release, release --redeploy and push --local-approval."""
    env = build_env(tmp_path, monkeypatch)
    ready(env, capsys)
    for v in ("ADOPT_CONVERSATION_ID", "ABCD_PAT_CLIENT_ID", "ADOPT_APP_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    write_marker(env.ws, CONV)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    for argv in (
        ("release", AGENT, "--force-reason", "x", "--lead", "y", "--yes"),
        ("release", "--redeploy", f"{AGENT}@v1", "--lead", "me", "--yes"),
        ("push", AGENT, "--staged", "--local-approval", "--approver", "x"),
    ):
        code, doc = env.run(*argv, capsys=capsys)
        assert code == 1 and "never in a cloud run" in error(doc), argv
