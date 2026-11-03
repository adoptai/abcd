"""Every refusal of push, stage and release, and the safety properties around them."""

from __future__ import annotations

import json

import pytest

from cli import builder_agent
from cli.builder_common import repo as rp
from cli.builder_common.browser_gate import current_fingerprint
from cli.builder_common.versions import VersionLedger
from tests.builder.evals_fakes import Call, Reply, correct_handler, jbytes
from tests.builder.test_browser_gate import CONV, token, user

from .release_fixtures import AGENT, SKILL_MD, WRITES, Env, build_env, cycle, git

Capsys = pytest.CaptureFixture[str]


def error(doc: dict) -> str:
    return str(doc.get("error", ""))


def ready(env: Env, capsys: Capsys) -> None:
    """Staged, tested and merged: the next step is the release itself."""
    cycle(env, capsys)
    env.merge_to_main()


def snapshot(env: Env) -> tuple:
    return (
        [c for c in env.client.calls if c[0] in WRITES],
        list(env.rows.writes),
        list(env.docs.writes),
        len(env.gh.prs),
        len(env.gh.releases),
        git(env.bare, "for-each-ref"),
        git(env.ws, "worktree", "list"),
    )


# -- push ---------------------------------------------------------------------------------------


def test_push_without_staged_is_refused(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    code, doc = env.run("push", AGENT, capsys=capsys)
    assert code == 1 and "--staged" in error(doc)
    assert env.client.writes() == []


def test_audit_critical_refuses_push_and_any_finding_refuses_stage(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    monkeypatch.setattr(builder_agent, "audit_skill_dir", lambda d: (2, "critical: x"))
    code, doc = env.run("push", AGENT, "--staged", capsys=capsys)
    assert code == 1 and "audit" in error(doc)
    assert env.client.writes() == []
    monkeypatch.setattr(builder_agent, "audit_skill_dir", lambda d: (1, ""))
    code, doc = env.run("push", AGENT, "--staged", capsys=capsys)  # medium: advisory for push
    assert code == 0, error(doc)
    code, doc = env.run("stage", AGENT, capsys=capsys)  # but stage is stricter
    assert code == 1 and "audit exited 1" in error(doc)
    assert not git(env.bare, "tag", "--list")


def test_secret_scan_refuses_push_and_stage(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    env.write("harness/skills/tax-calc/scripts/calc.py", 'KEY = "AKIA' + 'ABCDEFGHIJKLMNOP"\n')
    env.commit("oops")
    code, doc = env.run("push", AGENT, "--staged", capsys=capsys)
    assert code == 1 and "secret scan" in error(doc)
    assert env.client.writes() == []
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 1 and "secret scan" in error(doc)


def test_staged_agent_needs_a_staging_workstream(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    cfg = json.loads((env.ws / "builder.json").read_text())
    cfg["workstreams"] = {"acme-client": "ws-client"}
    (env.ws / "builder.json").write_text(json.dumps(cfg))
    env.commit("no staging")
    code, doc = env.run("push", AGENT, "--staged", capsys=capsys)
    assert code == 2 and "staging" in error(doc)
    assert not any(p["name"].endswith("-staged") for p in env.client.processes.values())


def test_a_client_workstream_is_never_a_staging_target(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    text = (env.ws / f"harness/agents/{AGENT}/process.yaml").read_text()
    (env.ws / f"harness/agents/{AGENT}/process.yaml").write_text(
        text.replace("workstreams: [acme-client]", "workstreams: [acme-client, acme-staging]", 1)
    )
    env.commit("overlap")
    code, doc = env.run("push", AGENT, "--staged", capsys=capsys)
    assert code == 1 and "also client workstream" in error(doc)


def test_staged_agent_is_never_assigned_to_a_client_workstream(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    cycle(env, capsys)
    env.merge_to_main()
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    staged_id = env.client.find_process_by_name(f"{AGENT}-staged")["id"]
    for name, args in env.client.calls:
        if name == "set_process_workstreams" and args[0] == staged_id:
            assert "ws-client" not in args[1]
        if name == "set_write_workstreams" and args[0] == staged_id:
            assert "ws-client" not in args[1] and args[2] == ""
    # re-pushing the staged shadow keeps it out of the client workstream too
    env.write("harness/skills/tax-calc/SKILL.md", SKILL_MD + "more\n")
    env.commit("again")
    code, doc = env.run("push", AGENT, "--staged", capsys=capsys)
    assert code == 0, error(doc)
    assert env.client.assigned[staged_id] and "ws-client" not in env.client.assigned[staged_id]


# -- stage --------------------------------------------------------------------------------------


def test_stage_refuses_a_dirty_tree(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    env.write("harness/skills/tax-calc/notes.md", "wip\n")
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 1 and "uncommitted" in error(doc)
    assert not git(env.bare, "tag", "--list")


def test_stage_refuses_an_unpushed_commit(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    env.write("harness/skills/tax-calc/notes.md", "wip\n")
    env.commit("local only", push=False)
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 1 and "not pushed" in error(doc)


def test_stage_refuses_when_the_staged_push_is_stale(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    env.write("harness/skills/tax-calc/SKILL.md", SKILL_MD + "newer\n")
    env.commit("edit after push")
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 1 and "push" in error(doc) and "again" in error(doc)


def test_stage_refuses_when_another_holder_has_the_lock(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    env.rows.update_row("bld_agents", "agent_key", AGENT, {"staged_lock_holder": "fde-b"})
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 1 and "fde-b" in error(doc)
    code, doc = env.run("push", AGENT, "--staged", capsys=capsys)
    assert code == 1 and "fde-b" in error(doc) and "--take" in error(doc)
    code, doc = env.run("push", AGENT, "--staged", "--take", capsys=capsys)
    assert code == 0, error(doc)
    assert "fde-b" in doc["data"]["agents"][0]["lock_notice"]
    assert env.rows.find_rows("bld_agents", "agent_key", AGENT)[0]["staged_lock_holder"] == "fde-a"


def test_stage_refuses_when_the_shadow_was_edited_on_the_platform(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    env.client.org_skills["tax-calc--staged"]["body"] += "hand edit"
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 1 and "shadows changed" in error(doc)


# -- the browser gate: on every push, staged or live ------------------------------------------------


def browser_env(tmp_path, monkeypatch, *, approve: bool = True) -> Env:
    env = build_env(tmp_path, monkeypatch, browser=True)
    if approve:
        fp = current_fingerprint(env.ws / "harness/skills/bank-statement")
        env.client.conversations[CONV] = [user(f"Approve {token(fp)}")]
    return env


def test_browser_skill_with_a_member_token_passes_everywhere(tmp_path, monkeypatch, capsys) -> None:
    env = browser_env(tmp_path, monkeypatch)
    cycle(env, capsys)
    assert not any(
        "recording_bundle" in p for p in env.client.org_skills["bank-statement--staged"]["aux"]
    )
    env.merge_to_main()
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0
    assert "bank-statement" in env.client.org_skills


def test_browser_gate_refuses_a_missing_token_on_push(tmp_path, monkeypatch, capsys) -> None:
    env = browser_env(tmp_path, monkeypatch, approve=False)
    code, doc = env.run("push", AGENT, "--staged", capsys=capsys)
    assert code == 1 and "token" in error(doc)
    assert env.client.writes() == []  # nothing uploaded, not even the other skill


def test_browser_gate_refuses_a_stale_fingerprint(tmp_path, monkeypatch, capsys) -> None:
    env = browser_env(tmp_path, monkeypatch)
    ops = env.ws / "harness/skills/bank-statement/operations.json"
    ops.write_text(ops.read_text().replace("bank.example", "bank2.example"))
    env.commit("changed the operations without a new replay")
    code, doc = env.run("push", AGENT, "--staged", capsys=capsys)
    assert code == 1 and error(doc)
    assert env.client.writes() == []
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 1


def test_browser_gate_refuses_at_stage_and_at_release(tmp_path, monkeypatch, capsys) -> None:
    env = browser_env(tmp_path, monkeypatch)
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    env.client.conversations[CONV] = []  # the approval conversation is gone
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 1 and "token" in error(doc)
    env.client.conversations[CONV] = [
        user(f"Approve {token(current_fingerprint(env.ws / 'harness/skills/bank-statement'))}")
    ]
    assert env.run("stage", AGENT, capsys=capsys)[0] == 0
    assert env.run("test-staged", AGENT, "--poll-interval", "0", capsys=capsys)[0] == 0
    env.merge_to_main()
    env.client.conversations[CONV] = []
    before = list(env.client.uploads)
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "token" in error(doc)
    assert env.client.uploads == before  # nothing went live
    assert env.client.find_process_by_name(AGENT) is None


def test_local_approval_is_recorded_with_the_approver(tmp_path, monkeypatch, capsys) -> None:
    env = browser_env(tmp_path, monkeypatch, approve=False)
    code, doc = env.run("push", AGENT, "--staged", "--local-approval", capsys=capsys)
    assert code == 2 and "approver" in error(doc)
    code, doc = env.run(
        "push", AGENT, "--staged", "--local-approval", "--approver", "fde-a", capsys=capsys
    )
    assert code == 0, error(doc)
    rec = VersionLedger(env.rows).latest("skill", "bank-statement")
    assert (
        rec.manifest["approval"]["mode"] == "local"
        and rec.manifest["approval"]["approver"] == "fde-a"
    )
    assert rec.replay_fingerprint


# -- release ------------------------------------------------------------------------------------


def test_release_refuses_a_commit_not_on_the_default_branch(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    cycle(env, capsys)
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "default branch" in error(doc)
    assert env.client.find_process_by_name(AGENT) is None
    env.rows.insert_rows(
        "bld_decisions",
        [
            {
                "decision_key": f"release-branch:{AGENT}@v1",
                "status": "confirmed",
                "decided_by": "lead-a",
            }
        ],
    )
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)  # a lead recorded the exception
    assert code == 0, error(doc)


def test_release_refuses_an_unstaged_agent(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "not staged" in error(doc)


def failing_c2(call: Call) -> Reply:
    if call.workstream.endswith("c2"):
        return Reply(outputs={"return.json": jbytes({"entity": "wrong", "total_tax": 1})})
    return correct_handler(call)


def test_release_refuses_a_failed_gate_and_a_force_is_recorded_and_flagged(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    env.set_driver(failing_c2)
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    assert env.run("stage", AGENT, capsys=capsys)[0] == 0
    code, run = env.run("test-staged", AGENT, "--poll-interval", "0", capsys=capsys)
    assert code == 0 and run["data"]["gate"]["passed"] is False
    env.merge_to_main()
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "gate failed" in error(doc)
    assert any("pass_rate" in r for r in doc["data"]["gate"][0]["reasons"])
    assert (
        env.client.find_process_by_name(AGENT) is None and "tax-calc" not in env.client.org_skills
    )
    assert VersionLedger(env.rows).latest("agent", AGENT).state == "staged"
    assert not env.rows.find_rows("bld_releases", "agent_key", AGENT)

    code, doc = env.run("release", AGENT, "--yes", "--force-reason", "client waived", capsys=capsys)
    assert code == 2 and "--lead" in error(doc)
    code, doc = env.run(
        "release",
        AGENT,
        "--yes",
        "--force-reason",
        "client waived",
        "--lead",
        "lead-a",
        capsys=capsys,
    )
    assert code == 0, error(doc)
    rel = env.rows.find_rows("bld_releases", "agent_key", AGENT)[0]
    assert (rel["gate_result"], rel["forced_reason"], rel["approved_by"]) == (
        "forced",
        "client waived",
        "lead-a",
    )
    assert "FORCED" in env.gh.prs[0]["title"] and "client waived" in env.gh.prs[0]["body"]
    assert env.gh.releases[0]["prerelease"] is True
    assert (
        "client waived"
        in env.docs.files[f"builder-ops/releases/{AGENT}/v1/eval_report.html"].decode()
    )


def test_release_refuses_a_run_on_other_hashes(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    cycle(env, capsys)
    env.write("harness/skills/tax-calc/SKILL.md", SKILL_MD + "new\n")
    env.commit("edit")
    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    assert env.run("stage", AGENT, capsys=capsys)[0] == 0  # v2 staged, but only v1 was tested
    env.merge_to_main()
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "no completed blind staged run" in error(doc)


def test_release_refuses_a_default_tier_name_collision_without_a_decision(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    env.client.default_skills.add("tax-calc")
    ready(env, capsys)
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "collide" in error(doc) and "tax-calc" in error(doc)
    assert "tax-calc" not in env.client.org_skills
    env.rows.insert_rows(
        "bld_decisions",
        [{"decision_key": "collision:tax-calc", "status": "confirmed", "decided_by": "lead-a"}],
    )
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    assert ("tax-calc", True) in env.client.uploads  # replace: the first upload would 409


def test_release_without_yes_changes_nothing(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    ready(env, capsys)
    before = snapshot(env)
    code, doc = env.run("release", AGENT, capsys=capsys)
    assert code == 1 and "--yes" in error(doc)
    assert snapshot(env)[:5] == before[:5]


def test_release_refuses_drift_on_live_objects(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    cycle(env, capsys)
    env.merge_to_main()
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0
    env.write("harness/skills/tax-calc/SKILL.md", SKILL_MD + "v2\n")
    env.commit("v2")
    cycle(env, capsys)
    env.merge_to_main()
    env.client.org_skills["tax-calc"]["body"] += "edited in the UI"
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "drift" in error(doc) and "tax-calc" in error(doc)
    assert VersionLedger(env.rows).latest("agent", AGENT, "live").version_id == f"{AGENT}@v1"


def test_release_refuses_a_shadow_changed_since_staging(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    ready(env, capsys)
    env.client.org_skills["tax-calc--staged"]["body"] += "x"
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "drift" in error(doc)


def test_a_shared_changed_skill_holds_up_the_other_agent(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    cycle(env, capsys)
    env.merge_to_main()
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0
    # a second live agent shares tax-calc
    other = env.ws / "harness/agents/acme-audit"
    other.mkdir(parents=True)
    (other / "process.yaml").write_text(
        "name: acme-audit\ndisplay_name: Audit\ncomponents:\n  - {type: skill, source: org, ref: tax-calc}\n"
        "workstreams: [acme-client]\nis_enabled: true\n"
    )
    (other / "instructions.md").write_text("Audit with tax-calc.\n")
    (other / "release.yaml").write_text(
        "agent: acme-audit\ndataset: returns\ncriteria:\n  - {metric: pass_rate, comparator: '>=', target: 0.9}\n"
    )
    (other / "prompts.yaml").write_text(
        "blind: Audit the return from the files in this workstream\n"
    )
    env.commit("second agent")
    assert env.run("push", "acme-audit", "--staged", capsys=capsys)[0] == 0
    assert env.run("stage", "acme-audit", capsys=capsys)[0] == 0
    # pretend it went live earlier, so it depends on the shared skill
    rec = VersionLedger(env.rows).latest("agent", "acme-audit")
    env.rows.update_row("bld_versions", "version_id", rec.version_id, {"state": "live"})
    env.write("harness/skills/tax-calc/SKILL.md", SKILL_MD + "shared change\n")
    env.commit("change the shared skill")
    cycle(env, capsys)
    assert env.run("push", "acme-audit", "--staged", capsys=capsys)[0] == 0
    assert env.run("stage", "acme-audit", capsys=capsys)[0] == 0
    env.merge_to_main()
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "acme-audit" in error(doc)  # no blind staged run on acme-audit yet
    assert env.run("test-staged", "acme-audit", "--poll-interval", "0", capsys=capsys)[0] == 0
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, error(doc)
    assert [g["agent"] for g in doc["data"]["gate"]] == [AGENT, "acme-audit"]


# -- dry-run and the default branch -------------------------------------------------------------------


def test_dry_run_makes_no_write_calls(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch)
    before = snapshot(env)
    code, doc = env.run("push", AGENT, "--staged", "--dry-run", capsys=capsys)
    assert code == 0 and doc["data"]["dry_run"] is True
    assert snapshot(env) == before and env.client.writes() == []

    assert env.run("push", AGENT, "--staged", capsys=capsys)[0] == 0
    before = snapshot(env)
    code, doc = env.run("stage", AGENT, "--dry-run", capsys=capsys)
    assert code == 0 and doc["data"]["tag"] == f"{AGENT}@v1-rc"
    assert snapshot(env)[1:] == before[1:] and git(env.ws, "tag", "--list") == ""
    assert env.run("stage", AGENT, capsys=capsys)[0] == 0
    assert env.run("test-staged", AGENT, "--poll-interval", "0", capsys=capsys)[0] == 0
    env.merge_to_main()
    before = snapshot(env)
    code, doc = env.run("release", AGENT, "--dry-run", capsys=capsys)
    assert code == 0, error(doc)
    assert [s.split(".")[0] for s in doc["data"]["steps"]] == [str(n) for n in range(1, 10)]
    assert snapshot(env)[1:] == before[1:]
    assert env.client.find_process_by_name(AGENT) is None
    assert VersionLedger(env.rows).latest("agent", AGENT).state == "staged"

    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0
    before = snapshot(env)
    for argv in (("rollback", AGENT), ("release", "--redeploy", f"{AGENT}@v1", "--lead", "lead-a")):
        code, doc = env.run(*argv, "--dry-run", capsys=capsys)
        assert code == 0 or "previous version" in error(doc)
        assert snapshot(env)[1:] == before[1:]


def test_the_default_branch_never_gets_a_commit_even_when_releasing_from_it(
    tmp_path, monkeypatch, capsys
) -> None:
    env = build_env(tmp_path, monkeypatch)
    cycle(env, capsys)
    env.merge_to_main()
    rp.checkout(env.ws, "main")
    rp.run_git(env.ws, "merge", "-q", "--ff-only", "origin/main")
    main_sha = git(env.ws, "rev-parse", "main")
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0
    assert git(env.ws, "rev-parse", "main") == main_sha
    assert env.bare_ref("main") == main_sha
    assert rp.current_branch(env.ws) == "main"
    assert git(env.ws, "status", "--porcelain") == ""
    branch = f"release/{AGENT}@v1"
    assert (
        git(env.bare, "rev-list", "--count", f"main..{branch}") != "0"
    )  # the work is on the branch
    assert git(env.bare, "merge-base", "--is-ancestor", "main", branch) == ""
    authors = git(env.bare, "log", "--format=%s", f"main..{branch}")
    assert "release" in authors


# -- plugins --------------------------------------------------------------------------------------


def test_a_plugin_is_staged_and_released_under_its_names(tmp_path, monkeypatch, capsys) -> None:
    env = build_env(tmp_path, monkeypatch, plugin=True)
    code, doc = env.run("push", "tax-plugin", "--staged", capsys=capsys)
    assert code == 0, error(doc)
    assert "tax-plugin--staged" in env.client.plugins and "helper--staged" in env.client.org_skills
    cycle(env, capsys)
    env.merge_to_main()
    assert env.run("release", AGENT, "--yes", capsys=capsys)[0] == 0
    assert (
        "tax-plugin" in env.client.plugins
        and env.client.org_skills["helper"]["plugin"] == "tax-plugin"
    )
    live = env.client.find_process_by_name(AGENT)
    assert {"type": "plugin", "source": "org", "ref": "tax-plugin"} in live["components"]
    assert VersionLedger(env.rows).latest("skill", "helper", "live") is not None
    code, doc = env.run("audit", "drift", "--agent", AGENT, capsys=capsys)
    assert code == 0, doc["data"]["items"]
