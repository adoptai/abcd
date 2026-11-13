"""The full happy path: push --staged, stage, test-staged, release, rollback, redeploy."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.builder_common import repo as rp
from cli.builder_common.versions import VersionLedger

from .release_fixtures import AGENT, SKILL_MD, build_env, cycle, git


def test_full_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = build_env(tmp_path, monkeypatch)
    ledger = VersionLedger(env.rows)
    main_before = env.bare_ref("main")

    # push --staged: shadows exist; the staged agent is only in staging/dataset workstreams
    code, doc = env.run("push", AGENT, "--staged", capsys=capsys)
    assert code == 0, doc.get("error")
    assert "tax-calc--staged" in env.client.org_skills
    assert "tax-calc" not in env.client.org_skills
    staged = env.client.find_process_by_name(f"{AGENT}-staged")
    assert staged is not None and AGENT not in {p["name"] for p in env.client.processes.values()}
    assert "ws-client" not in env.client.assigned[staged["id"]]
    assert set(env.client.assigned[staged["id"]]) == {"ws-staging", "ws-ds1", "ws-ds2", "ws-ds3"}
    assert env.client.grants[staged["id"]]["workstream_ids"] == []  # write grant cut away
    assert env.client.uploads == [("tax-calc--staged", False)]  # first upload: no replace
    assert staged["components"] == [{"type": "skill", "source": "org", "ref": "tax-calc--staged"}]
    assert ledger.latest("agent", AGENT).state == "draft"
    assert env.rows.find_rows("bld_agents", "agent_key", AGENT)[0]["staged_lock_holder"] == "fde-a"

    # stage: tags the rc, records staged
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 0, doc.get("error")
    assert doc["data"]["tag"] == f"{AGENT}@v1-rc"
    assert f"{AGENT}@v1-rc" in git(env.bare, "tag", "--list")
    assert ledger.latest("agent", AGENT).state == "staged"
    assert ledger.latest("skill", "tax-calc").state == "staged"

    # test-staged with fake driver results
    code, run = env.run("test-staged", AGENT, "--poll-interval", "0", capsys=capsys)
    assert code == 0, run.get("error")
    assert run["data"]["passed"] == 3 and run["data"]["gate"]["passed"]
    assert {c.process_id for c in env.driver.calls} == {staged["id"]}
    assert "ws-client" not in {c.workstream for c in env.driver.calls}

    # release: needs the merge, needs --yes
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 1 and "default branch" in doc["error"]
    env.merge_to_main()
    code, doc = env.run("release", AGENT, capsys=capsys)
    assert code == 1 and "--yes" in doc["error"]
    assert "tax-calc" not in env.client.org_skills
    main_after_merge = env.bare_ref("main")

    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, doc.get("error")
    # live objects, in place of the staged ones' names; first release goes without replace
    assert ("tax-calc", False) in env.client.uploads
    live = env.client.find_process_by_name(AGENT)
    assert live["components"] == [{"type": "skill", "source": "org", "ref": "tax-calc"}]
    assert env.client.assigned[live["id"]] == ["ws-client"]
    assert env.client.grants[live["id"]] == {
        "workstream_ids": ["ws-client"],
        "folder_prefix": "out/",
    }
    assert (
        env.client.org_skills["tax-calc"]["body"] == SKILL_MD
    )  # the source, with its process: block
    # versions, release row, tag, github release, evidence
    assert ledger.latest("agent", AGENT, "live").version_id == f"{AGENT}@v1"
    assert ledger.latest("skill", "tax-calc", "live") is not None
    rel = env.rows.find_rows("bld_releases", "agent_key", AGENT)[0]
    assert rel["gate_result"] == "pass" and rel["version_id"] == f"{AGENT}@v1"
    assert f"{AGENT}@v1" in git(env.bare, "tag", "--list")
    assert (
        env.gh.releases[0]["tag"] == f"{AGENT}@v1"
        and "<html" in env.gh.releases[0]["asset_text"][0]
    )
    for p in ("eval_report.html", "eval_report.json", "manifest.json"):
        assert f"builder-ops/releases/{AGENT}/v1/{p}" in env.docs.files
    assert f"builder-status-acme/agents/{AGENT}/releases/v1/eval_report.html" in env.docs.files
    # the release branch and PR; the default branch got nothing
    branch = f"release/{AGENT}@v1"
    assert env.bare_ref(f"refs/heads/{branch}")
    assert git(env.bare, "show", f"{branch}:harness/evals/reports/{AGENT}/v1.html").startswith("<")
    changed = git(env.bare, "diff", "--name-only", "main", branch).splitlines()
    assert f"harness/agents/{AGENT}/docs/explainer.html" in changed and "ADOPT.md" in changed
    assert env.gh.prs[0]["head"] == branch and env.gh.prs[0]["base"] == "main"
    assert env.bare_ref("main") == main_after_merge != main_before
    assert git(env.ws, "rev-parse", "main") == main_before  # local default branch untouched
    assert rp.current_branch(env.ws) == "feat/agent"  # worktree: the checkout never moved
    assert git(env.ws, "status", "--porcelain") == ""
    assert "[release/" not in git(env.ws, "worktree", "list")  # the release worktree is gone

    # a second release: replace is now true; then rollback returns to v1
    v1_body = env.client.org_skills["tax-calc"]["body"]
    v2_text = SKILL_MD + "\nSecond version.\n"
    cycle(env, capsys, skill_text=v2_text)
    env.merge_to_main()
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, doc.get("error")
    assert ("tax-calc", True) in env.client.uploads
    assert env.client.org_skills["tax-calc"]["body"] == v2_text
    assert ledger.latest("agent", AGENT, "live").version_id == f"{AGENT}@v2"
    assert ledger.get(f"{AGENT}@v1").state == "superseded"
    assert (
        env.rows.find_rows("bld_releases", "version_id", f"{AGENT}@v2")[0]["previous_version_id"]
        == f"{AGENT}@v1"
    )

    code, doc = env.run("rollback", AGENT, capsys=capsys)
    assert code == 1 and "--yes" in doc["error"]
    code, doc = env.run("rollback", AGENT, "--yes", capsys=capsys)
    assert code == 0, doc.get("error")
    assert env.client.org_skills["tax-calc"]["body"] == v1_body
    assert ledger.latest("agent", AGENT, "live").version_id == f"{AGENT}@v1"
    assert ledger.get(f"{AGENT}@v2").state == "superseded"
    rb = [r for r in env.rows.list_all("bld_releases") if "@rollback-" in r["release_id"]]
    assert (
        rb
        and rb[0]["version_id"] == f"{AGENT}@v1"
        and rb[0]["previous_version_id"] == f"{AGENT}@v2"
    )

    # drift is clean after a rollback; a hand edit in the UI is caught
    code, doc = env.run("audit", "drift", "--agent", AGENT, capsys=capsys)
    assert code == 0, doc["data"]["items"]
    env.client.org_skills["tax-calc"]["body"] += "edited in the UI"
    code, doc = env.run("audit", "drift", "--agent", AGENT, capsys=capsys)
    assert code == 1
    assert any(i["kind"] == "skill_md_changed" for i in doc["data"]["items"])

    # redeploy restores v2 under the live names, approved by a lead
    code, doc = env.run("release", "--redeploy", f"{AGENT}@v2", "--yes", capsys=capsys)
    assert code == 2 and "--lead" in doc["error"]
    code, doc = env.run(
        "release", "--redeploy", f"{AGENT}@v2", "--yes", "--lead", "lead-a", capsys=capsys
    )
    assert code == 0, doc.get("error")
    assert env.client.org_skills["tax-calc"]["body"] == v2_text
    assert ledger.latest("agent", AGENT, "live").version_id == f"{AGENT}@v2"
    rd = [r for r in env.rows.list_all("bld_releases") if "@redeploy-" in r["release_id"]]
    assert rd and rd[0]["approved_by"] == "lead-a"
    assert env.bare_ref("main") != main_before  # only the test's own "merges" moved it
    for r in (
        env.bare_ref(f"refs/heads/release/{AGENT}@v1"),
        env.bare_ref(f"refs/heads/release/{AGENT}@v2"),
    ):
        assert r != env.bare_ref("main")
    assert doc["ok"] is True


def test_release_without_a_github_remote_flags_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Live: a local bare remote makes the GitHub steps raise; the release is still complete."""
    from cli.builder_common.errors import UsageError

    env = build_env(tmp_path, monkeypatch)

    def no_github(*_a: object, **_k: object) -> None:
        raise UsageError("Cannot parse owner/repo from remote URL: /tmp/x.git")

    env.gh.create_release = no_github  # type: ignore[method-assign]
    env.gh.create_pr = no_github  # type: ignore[method-assign]
    cycle(env, capsys)
    env.merge_to_main()
    code, doc = env.run("release", AGENT, "--yes", capsys=capsys)
    assert code == 0, doc.get("error")
    assert doc["data"]["failures"] == []
    assert any("GitHub release not created" in f for f in doc["data"]["flags"])
    assert any(f.startswith("step 9") for f in doc["data"]["flags"])
    assert f"{AGENT}@v1" in git(env.bare, "tag", "--list")  # the tag itself still landed
