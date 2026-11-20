"""``abcd eval``: blind runs with resume, local tier, judging, reports and the gate."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.builder.evals_fakes import AGENT, PROCESS, jbytes

from .quality_fixtures import SENTINEL_ENTITY, SENTINEL_TAX, QEnv, qenv  # noqa: F401

PROMPT = "Prepare the return for this client from the files in this workstream"


def _secrets_absent(qenv: QEnv, *texts: str) -> None:  # noqa: F811
    for s in (SENTINEL_ENTITY, str(SENTINEL_TAX + 1)[:7], "48214.77"):
        for t in texts:
            assert s not in t, s


def test_blind_run_submit_only_then_resume_in_a_fresh_driver(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(3)
    qenv.polls = 1
    d = qenv.ok("eval", "run", AGENT, "--dataset", "returns", "--blind", "--submit-only")
    run_id = d["run_id"]
    assert d["status"] == "running" and len(d["turns"]) == 3
    driver = qenv.services.driver
    assert len(driver.calls) == 3
    # a blind turn carries only the generic prompt: no expected value, no case id, no FDE hint
    assert {c.message for c in driver.calls} == {PROMPT}
    assert {c.workstream for c in driver.calls} == {f"ds:returns:c{i}" for i in (1, 2, 3)}
    assert {c.process_id for c in driver.calls} == {"live-1"}
    runs = qenv.rows("bld_eval_runs")
    assert runs[0]["status"] == "running" and len(runs[0]["submitted_turns"]) == 3
    assert runs[0]["blind"] is True and runs[0]["dataset_version_key"] == "returns@4"

    # a fresh process: new driver object against the same platform, nothing in memory
    qenv.services.driver = driver.restarted()
    d = qenv.ok("eval", "run", AGENT, "--resume", run_id)
    assert (d["run_id"], d["cases"], d["passed"], d["blind"]) == (run_id, 3, 3, True)
    assert len(qenv.services.driver.calls) == 3, "resume never starts a turn"
    runs = qenv.rows("bld_eval_runs")
    assert len(runs) == 1 and runs[0]["status"] == "done"
    results = qenv.rows("bld_eval_results")
    assert {r["case_key"] for r in results} == {"returns:c1", "returns:c2", "returns:c3"}
    assert all(
        r["passed"] is True and r["observed_path"].startswith("builder-ops/evals/") for r in results
    )
    assert {r["metric_key"] for r in qenv.rows("bld_metric_values")} >= {
        "pass_rate",
        "blind_repro_rate",
    }
    # generic prompt mirrored, expected values not in state, rows or the workspace
    assert qenv.rows("bld_generic_prompts")[0]["prompt"] == PROMPT
    state = (qenv.ws / ".builder/evals" / run_id / "state.json").read_text()
    _secrets_absent(qenv, state, json.dumps(qenv.backend.tables, default=str))


def test_resume_guards_and_one_shot_run(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(2)
    qenv.answers["c2"] = {"entity": "someone else", "total_tax": 1}
    d = qenv.ok("eval", "run", AGENT, "--dataset", "returns", "--blind", "--staged")
    assert d["tier"] == "staged" and d["passed"] == 1 and d["failed"] == ["c2"]
    assert {c.process_id for c in qenv.services.driver.calls} == {PROCESS}
    code, doc = qenv.run("eval", "run", "other-agent", "--resume", d["run_id"])
    assert code == 2 and "belongs to agent" in doc["error"]
    code, _ = qenv.run("eval", "run", AGENT, "--resume", d["run_id"], "--submit-only")
    assert code == 2
    code, _ = qenv.run("eval", "run", AGENT)
    assert code == 2
    code, doc = qenv.run("eval", "run", AGENT, "--dataset", "returns", "--dry-run")
    assert doc["data"] == {"dry_run": True}
    code, doc = qenv.run(
        "eval", "run", AGENT, "--dataset", "returns", "--timeout", "0", "--poll", "1"
    )
    assert code == 0  # completes at once, no polling needed


def test_timeout_leaves_a_resumable_run(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(1)
    qenv.polls = 50
    code, doc = qenv.run(
        "eval", "run", AGENT, "--dataset", "returns", "--blind", "--timeout", "3", "--poll", "1"
    )
    assert code == 3 and "--resume" in doc["error"]
    rid = qenv.rows("bld_eval_runs")[0]["eval_run_id"]
    assert qenv.rows("bld_eval_runs")[0]["status"] == "running"
    qenv.polls = 0
    qenv.services.driver.platform.polls.clear()
    qenv.services.driver.platform.turns = {
        k: (c, type(r)(**{**r.__dict__, "polls": 0}))
        for k, (c, r) in qenv.services.driver.platform.turns.items()
    }
    assert qenv.ok("eval", "run", AGENT, "--resume", rid)["passed"] == 1


def test_local_tier_scores_outputs_already_on_disk(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(2)
    out = qenv.ws / ".builder/local-outputs/returns"
    (out / "c1").mkdir(parents=True)
    (out / "c1" / "return.json").write_bytes(
        jbytes({"entity": SENTINEL_ENTITY, "total_tax": SENTINEL_TAX + 1})
    )
    d = qenv.ok("eval", "run", AGENT, "--dataset", "returns", "--tier", "local")
    assert d["tier"] == "local" and d["passed"] == 1 and d["errors"] == 1  # c2 has no local output
    assert qenv.services.driver.calls == [], "the platform harness is never called"
    assert qenv.rows("bld_eval_runs")[0]["ran_in"] == "local"
    other = qenv.root / "elsewhere/c2"
    other.mkdir(parents=True)
    (other / "return.json").write_bytes(
        jbytes({"entity": SENTINEL_ENTITY, "total_tax": SENTINEL_TAX + 2})
    )
    (qenv.root / "elsewhere/c1").mkdir()
    (qenv.root / "elsewhere/c1/return.json").write_bytes(
        jbytes({"entity": SENTINEL_ENTITY, "total_tax": SENTINEL_TAX + 1})
    )
    d = qenv.ok(
        "eval",
        "run",
        AGENT,
        "--dataset",
        "returns",
        "--tier",
        "local",
        "--outputs",
        str(qenv.root / "elsewhere"),
    )
    assert d["passed"] == 2
    qenv.ok("dataset", "create", "inbox", "--kind", "labeled", "--client", "acme")
    code, doc = qenv.run("eval", "run", AGENT, "--dataset", "inbox", "--tier", "local")
    assert code == 2 and "per-case work_product" in doc["error"]


def test_judge_flow(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(2)
    qenv.ok("check", "add", AGENT, "polite", "--kind", "judged", "--rubric", "Is the memo polite?")
    d = qenv.ok("eval", "run", AGENT, "--dataset", "returns", "--blind")
    run_id = d["run_id"]
    assert d["pending_judgement"] == 2 and d["passed"] == 0 and d["packet_path"]
    assert qenv.rows("bld_eval_results")[0]["passed"] is None
    code, doc = qenv.run("eval", "gate", AGENT, "--run", run_id)
    assert code == 1  # not trusted: nothing to evaluate yet / pending

    p = qenv.ok("eval", "judge-packet", run_id)
    local = Path(p["local_path"])
    packet = json.loads(local.read_text())
    assert set(packet["items"][0]["judged_checks"]) == {"polite"}
    assert SENTINEL_ENTITY in local.read_text(), "the packet is the one place judged values go"
    assert p["packet_path"].startswith("ds-returns/judging/"), "and it lives in the ds- store"
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(local.relative_to(qenv.ws))], cwd=qenv.ws
    )
    assert ignored.returncode == 0, "the local copy is git-ignored"

    scores = qenv.file("scores.json", {"c1": {"checks": {}}})
    code, doc = qenv.run("eval", "judge-apply", run_id, "--scores", scores)
    assert code == 2 and "no verdict for judged checks" in doc["error"]
    scores = qenv.file("scores.json", {c: {"checks": {"polite": c == "c1"}} for c in ("c1", "c2")})
    d = qenv.ok("eval", "judge-apply", run_id, "--scores", scores)
    assert d["passed"] == 1 and d["pending_judgement"] == 0
    results = {r["case_key"]: r for r in qenv.rows("bld_eval_results")}
    assert results["returns:c1"]["passed"] is True and results["returns:c2"]["passed"] is False
    assert qenv.ok("eval", "judge-packet", run_id)["packet_path"] is None


def _gated(qenv: QEnv, *extra: str) -> str:  # noqa: F811
    d = qenv.ok("eval", "run", AGENT, "--dataset", "returns", "--blind", "--staged", *extra)
    return str(d["run_id"])


def test_report_files(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(3)
    qenv.answers["c3"] = {
        "entity": SENTINEL_ENTITY,
        "total_tax": SENTINEL_TAX + 3.4,
    }  # rounding miss
    qenv.ok(
        "criteria",
        "set",
        AGENT,
        "--dataset",
        "returns",
        "--metric",
        "pass_rate",
        "--op",
        ">=",
        "--target",
        "0.5",
    )
    run_id = _gated(qenv)
    d = qenv.ok("eval", "report", run_id)
    html = Path(d["files"]["html"])
    js = Path(d["files"]["json"])
    assert html.parent == qenv.ws / ".builder/evals" / run_id and html.name == f"{run_id}.html"
    report = json.loads(js.read_text())
    assert report["header"]["agent"] == AGENT and report["header"]["blind"] is True
    assert report["header"]["verdict"] in ("pass", "fail") and report["criteria"]
    assert "Misses" in html.read_text() or "misses" in html.read_text().lower()
    assert "http://" not in html.read_text() and "https://" not in html.read_text()
    _secrets_absent(qenv, html.read_text(), js.read_text())
    assert d["store"]["html"] in qenv.store.files and d["store"]["json"] in qenv.store.files
    assert qenv.rows("bld_eval_runs")[0]["report_path"] == d["store"]["html"]
    outdir = qenv.root / "out"
    d2 = qenv.ok("eval", "report", run_id, "--out", str(outdir))
    assert (outdir / f"{run_id}.html").exists() and d2["store"]["html"] in qenv.store.files


def test_gate_decision(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(2)
    code, doc = qenv.run("eval", "gate", AGENT, "--run", "nope")
    assert code == 2
    qenv.ok(
        "criteria",
        "set",
        AGENT,
        "--dataset",
        "returns",
        "--metric",
        "pass_rate",
        "--op",
        ">=",
        "--target",
        "0.9",
    )
    qenv.ok("criteria", "set", AGENT, "--dataset", "returns", "--op", "no_regression")

    good = _gated(qenv)
    live = _gated(qenv)
    code, doc = qenv.run("eval", "gate", AGENT, "--run", good, "--live", live)
    assert code == 0 and doc["data"]["passed"] is True
    assert {c["criteria_key"].split(":")[-1] for c in doc["data"]["criteria"]} == {
        "pass_rate", "blind_repro_rate", "open_gaps", "no_regression"}  # fmt: skip

    qenv.answers["c2"] = {"entity": "x", "total_tax": 0}
    bad = _gated(qenv)
    code, doc = qenv.run("eval", "gate", AGENT, "--run", bad, "--live", live)
    assert code == 1 and doc["data"]["passed"] is False
    failed = {c["criteria_key"].split(":")[-1] for c in doc["data"]["criteria"] if not c["passed"]}
    assert failed == {"pass_rate", "blind_repro_rate", "no_regression"}
    assert "gate failed" in doc["error"]

    # the gate refuses on any error: a non-blind run, or a missing live run for no_regression
    open_run = qenv.ok("eval", "run", AGENT, "--dataset", "returns")["run_id"]
    code, doc = qenv.run("eval", "gate", AGENT, "--run", open_run, "--live", live)
    assert code == 1 and any("not blind" in r for r in doc["data"]["reasons"])
    code, doc = qenv.run("eval", "gate", AGENT, "--run", good)
    assert code == 1 and any("needs a live run" in r for r in doc["data"]["reasons"])


def test_gate_counts_open_gaps_and_needs_criteria(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(1)
    run_id = _gated(qenv)
    code, doc = qenv.run("eval", "gate", AGENT, "--run", run_id)
    assert code == 1 and "no release criteria" in doc["error"]
    qenv.ok(
        "criteria",
        "set",
        AGENT,
        "--dataset",
        "returns",
        "--metric",
        "pass_rate",
        "--op",
        ">=",
        "--target",
        "1",
    )
    assert qenv.run("eval", "gate", AGENT, "--run", run_id)[0] == 0
    qenv.answers["c1"] = None  # no output: the agent produced nothing
    bad = _gated(qenv)
    qenv.ok(
        "gap",
        "open",
        "--from-run",
        bad,
        "--classify",
        qenv.file("cls.json", {"c1": "missing_context"}),
    )
    code, doc = qenv.run("eval", "gate", AGENT, "--run", run_id)
    failed = [c["criteria_key"] for c in doc["data"]["criteria"] if not c["passed"]]
    assert code == 1 and failed == [f"{AGENT}:returns:open_gaps"]
