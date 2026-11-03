"""Runner tests: kinds, blind withholding, resume, target_env refusal, judging."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.builder_common import datasets as D  # noqa: N812
from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.evals import runner as R  # noqa: N812
from cli.builder_common.evals.models import STATUS_ASKED, STATUS_ERROR, STATUS_PENDING

from .evals_fakes import (
    AGENT,
    PROBE,
    PROCESS,
    SENTINEL_ENTITY,
    SENTINEL_NOTE,
    SENTINEL_TAX,
    Call,
    FakeDriver,
    MemRows,
    MemStore,
    Reply,
    add_case,
    correct_handler,
    jbytes,
    work_product_ws,
    write_check,
    write_prompts,
)


def _go(ws, store, driver, rows=None, **kw):
    kw.setdefault("dataset", "returns")
    return R.run_eval(
        ws,
        driver=driver,
        store=store,
        rows=rows,
        agent=AGENT,
        process_id=PROCESS,
        sleep=lambda s: None,
        **kw,
    )


# --------------------------------------------------------------------------- work_product


def test_work_product_all_pass_and_files_written(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path)
    rows = MemRows()
    res = _go(ws, store, FakeDriver(correct_handler), rows, blind=True, version="v3")
    assert [c.passed for c in res.cases] == [True, True, True]
    assert res.blind and res.dataset_version == 4
    c = res.cases[0]
    assert c.observed_path and c.observed_path.startswith("builder-ops/evals/")
    assert c.diff_path and c.diff_path.startswith("ds-returns/diffs/")
    assert c.steps == 4 and c.cost_usd == pytest.approx(0.02)
    assert f"builder-ops/evals/{res.run_id}/cases/c1/trace.ndjson" in store.files
    assert set(rows.tables["bld_eval_results"]) == {f"{res.run_id}:c{i}" for i in (1, 2, 3)}
    run_row = rows.tables["bld_eval_runs"][res.run_id]
    assert run_row["status"] == "done" and len(run_row["submitted_turns"]) == 3
    assert run_row["cost_usd"] == pytest.approx(0.06)
    assert any(k.startswith("builder-ops/evals/") and "summary" in k for k in store.files)


def test_failed_case_is_scored_and_rounding_classified(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 2)

    def handler(call: Call) -> Reply:
        i = int(call.workstream.rsplit(":", 1)[-1][1:])
        tax = SENTINEL_TAX + i + (0.4 if i == 1 else 500)
        return Reply(outputs={"return.json": jbytes({"entity": SENTINEL_ENTITY, "total_tax": tax})})

    res = _go(ws, store, FakeDriver(handler))
    by = {c.case_id: c for c in res.cases}
    assert by["c1"].passed is False and by["c1"].miss_cause == "rounding"
    assert by["c2"].passed is False and by["c2"].miss_cause is None


def test_turn_failure_is_an_error_result(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 1)
    res = _go(ws, store, FakeDriver(lambda c: Reply(state="failed", error="sandbox died")))
    assert res.cases[0].status == STATUS_ERROR and res.cases[0].error == "sandbox died"


def test_state_and_rows_hold_no_values(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path)
    rows = MemRows()
    # wrong tax so the diff would carry both numbers
    res = _go(
        ws,
        store,
        FakeDriver(
            lambda c: Reply(
                outputs={"return.json": jbytes({"entity": "Wrong Co", "total_tax": 1.5})}
            )
        ),
        rows,
    )
    state_text = R.state_path(ws, res.run_id).read_text()
    blob = state_text + rows.dump()
    for needle in (SENTINEL_ENTITY, str(SENTINEL_TAX + 1), "Wrong Co"):
        assert needle not in blob
    # the diff with the expected values exists only in the ds- store
    diff = store.files[res.cases[0].diff_path or ""].decode()
    assert SENTINEL_ENTITY in diff
    for p, data in store.files.items():
        if SENTINEL_ENTITY.encode() in data and "/expected/" not in p:
            assert p.startswith("ds-returns/diffs/")


def test_client_checks_run_in_every_eval(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 2)
    write_check(
        ws,
        "has-tax",
        severity="error",
        rule={"type": "range", "field": "total_tax", "min": 0, "max": 49000},
    )
    write_check(ws, "soft", severity="warning", rule={"type": "present", "field": "nothing"})

    def handler(call: Call) -> Reply:
        i = int(call.workstream.rsplit(":", 1)[-1][1:])
        return Reply(
            outputs={
                "return.json": jbytes({"entity": SENTINEL_ENTITY, "total_tax": SENTINEL_TAX + i})
            }
        )

    res = _go(ws, store, FakeDriver(handler))
    assert all(c.passed for c in res.cases)  # warning-severity failure does not fail a case

    write_check(
        ws,
        "has-tax",
        severity="error",
        rule={"type": "range", "field": "total_tax", "min": 0, "max": 10},
    )
    res2 = _go(ws, store, FakeDriver(handler))
    assert all(c.passed is False and c.failed_check_ids == ["has-tax"] for c in res2.cases)


def test_expected_hash_mismatch_refuses(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 1)
    store.files["ds-returns/expected/c1/return.json"] = b'{"tampered": 1}'
    with pytest.raises(RefusedError, match="sha256"):
        _go(ws, store, FakeDriver(correct_handler))


# --------------------------------------------------------------------------- resume


def test_resume_after_simulated_restart(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path)
    rows = MemRows()
    driver = FakeDriver(lambda c: Reply(polls=2, **{"outputs": correct_handler(c).outputs}))
    state = R.submit_run(
        ws,
        dataset="returns",
        agent=AGENT,
        process_id=PROCESS,
        driver=driver,
        store=store,
        rows=rows,
    )
    rid = state["run_id"]
    assert state["status"] == "running" and len(driver.calls) == 3
    assert [t["turn_id"] for t in rows.tables["bld_eval_runs"][rid]["submitted_turns"]] == [
        "t1",
        "t2",
        "t3",
    ]

    # new process: nothing in memory but the workspace, the platform and the stores
    driver2 = driver.restarted()
    st = R.advance(ws, rid, driver2, store, rows)
    assert st["status"] == "running"
    st = R.advance(ws, rid, driver2, store, rows)
    assert st["status"] == "running"
    st = R.advance(ws, rid, driver2, store, rows)
    assert st["status"] == "complete"
    assert len(driver2.calls) == 3, "resuming must not start new turns"
    res = R.collect(ws, rid, store)
    assert all(c.passed for c in res.cases)
    # advancing a finished run is a no-op
    assert R.advance(ws, rid, driver2, store)["status"] == "complete"


def test_collect_before_complete_is_refused(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 1)
    driver = FakeDriver(lambda c: Reply(polls=5))
    st = R.submit_run(
        ws, dataset="returns", agent=AGENT, process_id=PROCESS, driver=driver, store=store
    )
    with pytest.raises(UsageError, match="still running"):
        R.collect(ws, st["run_id"], store)


def test_run_eval_times_out_but_state_resumes(tmp_path: Path) -> None:
    from cli.builder_common.errors import PlatformError

    ws, store = work_product_ws(tmp_path, 1)
    driver = FakeDriver(lambda c: Reply(polls=100))
    with pytest.raises(PlatformError, match="resume"):
        _go(ws, store, driver, timeout=3, poll_interval=1, run_id="r-slow")
    assert R.load_state(ws, "r-slow")["status"] == "running"


def test_submit_twice_with_same_run_id_is_refused(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 1)
    d = FakeDriver(correct_handler)
    R.submit_run(
        ws, dataset="returns", agent=AGENT, process_id=PROCESS, driver=d, store=store, run_id="x"
    )
    with pytest.raises(UsageError, match="already exists"):
        R.submit_run(
            ws,
            dataset="returns",
            agent=AGENT,
            process_id=PROCESS,
            driver=d,
            store=store,
            run_id="x",
        )


# --------------------------------------------------------------------------- blind


def test_blind_run_sends_only_the_generic_prompt(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path)
    # a case note and a gap file exist in the SME store; a blind run must never touch them
    store.put("builder-sme-acme/notes/c1.md", SENTINEL_NOTE.encode())
    store.put("builder-sme-acme/gaps/gap-1.md", b"GAP-FILE-EVIDENCE")
    driver = FakeDriver(correct_handler)
    _go(ws, store, driver, blind=True)
    prompt = "Prepare the return for this client from the files in this workstream"
    assert len(driver.calls) == 3
    for call in driver.calls:
        assert call.message == prompt
        assert call.process_id == PROCESS
        blob = json.dumps(call.__dict__)
        for secret in (
            SENTINEL_ENTITY,
            str(SENTINEL_TAX),
            SENTINEL_NOTE,
            "GAP-FILE-EVIDENCE",
            "expected",
        ):
            assert secret not in blob
    assert not any(p.startswith("builder-sme") for p in store.gets)


def test_blind_question_becomes_gap_candidate_and_is_not_answered(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 2)

    def handler(call: Call) -> Reply:
        if call.workstream.endswith("c1"):
            return Reply(
                state="needs_input", question="Which depreciation method does the client use?"
            )
        return correct_handler(call)

    driver = FakeDriver(handler)
    res = _go(ws, store, driver, blind=True)
    by = {c.case_id: c for c in res.cases}
    assert by["c1"].status == STATUS_ASKED and by["c1"].passed is False
    assert by["c1"].miss_cause == "missing_context"
    assert by["c1"].question == "Which depreciation method does the client use?"
    assert res.meta["gap_candidates"] == [
        {"case_id": "c1", "question": "Which depreciation method does the client use?"}
    ]
    assert len(driver.calls) == 2, "no follow-up turn: the agent's question is never answered"


def test_blind_run_refuses_answers(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 1)
    with pytest.raises(UsageError, match="blind"):
        R.submit_run(
            ws,
            dataset="returns",
            agent=AGENT,
            process_id=PROCESS,
            driver=FakeDriver(correct_handler),
            store=store,
            blind=True,
            answers={"c1": "use straight line"},
        )


def test_non_blind_run_may_answer_in_same_conversation(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 1)
    seen: list[Call] = []

    def handler(call: Call) -> Reply:
        seen.append(call)
        if len(seen) == 1:
            return Reply(state="needs_input", question="Method?")
        return correct_handler(call)

    driver = FakeDriver(handler)
    res = _go(ws, store, driver, blind=False, answers={"c1": "straight line"})
    assert res.cases[0].passed is True and not res.blind
    assert driver.calls[1].message == "straight line"
    assert driver.calls[1].conversation_id == "conv-t1"
    assert res.meta["gap_candidates"] == []


def test_input_message_is_the_only_addition(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 0)
    add_case(ws, store, "returns", "m1", message="The client is zenith.", scorers=["field_match"])
    driver = FakeDriver(lambda c: Reply(outputs={"return.json": jbytes({"ok": "m1"})}))
    _go(ws, store, driver)
    assert driver.calls[0].message.endswith("\n\nThe client is zenith.")


def test_missing_generic_prompt_fails_before_any_turn(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 1)
    (ws / "harness/agents/acme-tax/prompts.yaml").unlink()
    driver = FakeDriver(correct_handler)
    with pytest.raises(UsageError, match="prompts.yaml"):
        _go(ws, store, driver)
    assert driver.calls == []


# --------------------------------------------------------------------------- labeled


def _labeled_ws(tmp_path: Path, run_mode: str) -> tuple[Path, MemStore]:
    ws, store = tmp_path / "ws", MemStore()
    write_prompts(ws)
    D.create_dataset(
        ws,
        "mail",
        "labeled",
        client="acme",
        run_mode=run_mode,
        default_scorers=["label_match", "boundary_match"],
    )
    for cid, lab in (("e1", "acme-tax"), ("e2", "acme-audit"), ("e3", "zenith")):
        add_case(
            ws,
            store,
            "mail",
            cid,
            expected={"labels.json": jbytes({"label": lab, "boundaries": [1, 4]})},
        )
    return ws, store


def test_labeled_batch_is_one_turn(tmp_path: Path) -> None:
    ws, store = _labeled_ws(tmp_path, "batch")
    labels = {"e1": "acme-tax", "e2": "acme-audit", "e3": "wrong"}
    out = {
        "results.json": jbytes({k: {"label": v, "boundaries": [1, 4]} for k, v in labels.items()})
    }
    driver = FakeDriver(lambda c: Reply(outputs=out, cost_usd=0.3, duration_s=30))
    res = _go(ws, store, driver, dataset="mail")
    assert len(driver.calls) == 1 and driver.calls[0].workstream == "ds:mail"
    by = {c.case_id: c for c in res.cases}
    assert by["e1"].passed and by["e2"].passed and by["e3"].passed is False
    assert sum(c.cost_usd for c in res.cases) == pytest.approx(0.3)
    assert by["e1"].steps == 4


def test_labeled_per_case_runs_one_turn_each(tmp_path: Path) -> None:
    ws, store = _labeled_ws(tmp_path, "per_case")
    driver = FakeDriver(
        lambda c: Reply(
            outputs={"labels.json": jbytes({"label": "acme-tax", "boundaries": [1, 4]})}
        )
    )
    res = _go(ws, store, driver, dataset="mail")
    assert len(driver.calls) == 3
    assert [c.passed for c in res.cases] == [True, False, False]


def test_batch_mode_only_for_labeled(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 1)
    m = json.loads((ws / "harness/datasets/returns/manifest.json").read_text())
    m["run_mode"] = "batch"
    (ws / "harness/datasets/returns/manifest.json").write_text(json.dumps(m))
    with pytest.raises(UsageError, match="labeled"):
        _go(ws, store, FakeDriver(correct_handler))


# --------------------------------------------------------------------------- read_your_writes


class Crm:
    """A tiny target system behind the probe and the agent."""

    def __init__(
        self, env: str = "CRM_TEST_DB", agent_code: int = 0, fail_teardown: bool = False
    ) -> None:
        self.env = env
        self.record = {"status": "new", "amount": 10}
        self.others = {"r2": {"amount": 5}}
        self.agent_code = agent_code
        self.fail_teardown = fail_teardown
        self.touch_others = False
        self.log: list[str] = []

    def handler(self, call: Call) -> Reply:
        if call.process_id == PROCESS:
            self.log.append("agent")
            self.record = {"status": "updated", "amount": 10}
            if self.touch_others:
                self.others = {"r2": {"amount": 6}}
            return Reply(outputs={"result.json": jbytes({"outcome_code": self.agent_code})})
        action = json.loads(call.message)["action"]
        self.log.append(action)
        if action == "describe_target":
            return Reply(outputs={"target.json": jbytes({"target_env": self.env})})
        if action in ("snapshot", "readback"):
            snap = {"target": dict(self.record), "others": json.loads(json.dumps(self.others))}
            return Reply(outputs={f"{action}.json": jbytes(snap)})
        if self.fail_teardown:
            return Reply(state="failed", error="winrm down")
        self._snapshot_restore(json.loads(call.message)["snapshot"])
        return Reply(text="restored")

    def _snapshot_restore(self, snap: dict) -> None:
        self.record, self.others = snap["target"], snap["others"]


def _ryw_ws(tmp_path: Path, n: int = 2) -> tuple[Path, MemStore]:
    ws, store = tmp_path / "ws", MemStore()
    write_prompts(ws)
    D.create_dataset(
        ws,
        "crm",
        "read_your_writes",
        client="acme",
        target_env="CRM_TEST_DB",
        probe_agent="acme-crm-probe",
        default_scorers=["outcome_match", "state_match", "collateral"],
    )
    for i in range(n):
        add_case(
            ws,
            store,
            "crm",
            f"w{i}",
            expected={
                "expected.json": jbytes(
                    {
                        "outcome_code": 0,
                        "state": {"status": "updated", "amount": 10},
                        "allowed_changes": [],
                    }
                )
            },
            setup={"record": f"R{i}"},
            teardown={"restore": True},
            message="Update the record.",
        )
    return ws, store


def test_read_your_writes_sequence_and_teardown(tmp_path: Path) -> None:
    ws, store = _ryw_ws(tmp_path)
    crm = Crm()
    driver = FakeDriver(crm.handler)
    res = _go(ws, store, driver, dataset="crm", probe_process_id=PROBE)
    assert [c.passed for c in res.cases] == [True, True]
    assert crm.log == [
        "describe_target",
        "snapshot", "agent", "readback", "teardown",
        "snapshot", "agent", "readback", "teardown",
    ]  # fmt: skip
    assert crm.record == {"status": "new", "amount": 10}, "teardown restored the snapshot"
    assert f"builder-ops/evals/{res.run_id}/cases/w0/snapshot.json" in store.files


def test_read_your_writes_detects_wrong_outcome_and_collateral(tmp_path: Path) -> None:
    ws, store = _ryw_ws(tmp_path, 1)
    crm = Crm(agent_code=-1)
    crm.touch_others = True
    res = _go(ws, store, FakeDriver(crm.handler), dataset="crm", probe_process_id=PROBE)
    c = res.cases[0]
    assert c.passed is False
    failed = {s["scorer"] for s in c.scorers if s["passed"] is False}
    assert failed == {"outcome_match", "collateral"}


def test_target_env_mismatch_refuses_before_any_agent_turn(tmp_path: Path) -> None:
    ws, store = _ryw_ws(tmp_path)
    crm = Crm(env="CRM_PROD_DB")
    driver = FakeDriver(crm.handler)
    rows = MemRows()
    with pytest.raises(RefusedError, match="target_env"):
        _go(ws, store, driver, rows, dataset="crm", probe_process_id=PROBE, run_id="r-env")
    assert crm.log == ["describe_target"]
    assert all(c.process_id == PROBE for c in driver.calls)
    st = R.load_state(ws, "r-env")
    assert st["status"] == "refused" and "CRM_TEST_DB" in st["refusal"]
    assert "CRM_PROD_DB" not in st["refusal"], "the real target is not echoed"


def test_target_env_dict_form(tmp_path: Path) -> None:
    ws, store = _ryw_ws(tmp_path, 1)
    m = json.loads((ws / "harness/datasets/crm/manifest.json").read_text())
    m["target_env"] = {"server": "crm-test", "database": "d1"}
    (ws / "harness/datasets/crm/manifest.json").write_text(json.dumps(m))
    crm = Crm()
    base = crm.handler

    def handler(call: Call) -> Reply:
        if call.process_id == PROBE and json.loads(call.message)["action"] == "describe_target":
            return Reply(outputs={"t.json": jbytes({"server": "crm-test", "database": "d1"})})
        return base(call)

    res = _go(ws, store, FakeDriver(handler), dataset="crm", probe_process_id=PROBE)
    assert res.cases[0].passed is True


def test_read_your_writes_needs_probe_process(tmp_path: Path) -> None:
    ws, store = _ryw_ws(tmp_path, 1)
    with pytest.raises(UsageError, match="probe"):
        _go(ws, store, FakeDriver(Crm().handler), dataset="crm")


def test_failed_teardown_is_loud(tmp_path: Path) -> None:
    ws, store = _ryw_ws(tmp_path, 2)
    crm = Crm(fail_teardown=True)
    res = _go(
        ws, store, FakeDriver(crm.handler), dataset="crm", probe_process_id=PROBE, run_id="r-td"
    )
    assert res.cases[0].status == STATUS_ERROR and "TEARDOWN FAILED" in (res.cases[0].error or "")
    assert R.load_state(ws, "r-td")["teardown_failed"] is True


def test_ryw_resume_midway(tmp_path: Path) -> None:
    ws, store = _ryw_ws(tmp_path, 1)
    crm = Crm()
    driver = FakeDriver(lambda c: Reply(polls=1, **{"outputs": crm.handler(c).outputs}))
    st = R.submit_run(
        ws,
        dataset="crm",
        agent=AGENT,
        process_id=PROCESS,
        driver=driver,
        store=store,
        probe_process_id=PROBE,
    )
    rid = st["run_id"]
    for _ in range(3):
        R.advance(ws, rid, driver.restarted(), store)
    mid = R.load_state(ws, rid)
    assert mid["status"] == "running" and mid["cases"]["w0"]["phase"] != "done"
    for _ in range(20):
        if R.advance(ws, rid, driver.restarted(), store)["status"] != "running":
            break
    assert R.load_state(ws, rid)["status"] == "complete"


# --------------------------------------------------------------------------- custom and judging


def test_custom_script_scorer(tmp_path: Path) -> None:
    ws, store = tmp_path / "ws", MemStore()
    write_prompts(ws)
    (ws / "harness/evals/scorers").mkdir(parents=True)
    (ws / "harness/evals/scorers/side-effect.py").write_text(
        "def score(case, observed, expected):\n"
        "    ok = observed['data'].get('r.json', {}).get('sent') is True\n"
        "    return {'passed': ok, 'details': {'sent': ok}}\n"
    )
    D.create_dataset(ws, "smb", "custom", default_scorers=["script:side-effect"])
    add_case(ws, store, "smb", "s1")
    res = _go(
        ws,
        store,
        FakeDriver(lambda c: Reply(outputs={"r.json": jbytes({"sent": True})})),
        dataset="smb",
    )
    assert res.cases[0].passed is True and res.cases[0].scorers[0]["scorer"] == "script:side-effect"


def test_rubric_judging_flow(tmp_path: Path) -> None:
    ws, store = tmp_path / "ws", MemStore()
    write_prompts(ws)
    D.create_dataset(
        ws,
        "memos",
        "work_product",
        default_scorers=[{"name": "rubric", "criteria": ["tone", "facts"]}],
    )
    add_case(ws, store, "memos", "m1", expected={"memo.json": jbytes({"gist": "the secret gist"})})
    add_case(
        ws, store, "memos", "m2", expected={"memo.json": jbytes({"gist": "other secret gist"})}
    )
    write_check(ws, "polite", kind="judged", rubric="Is the memo polite?")
    driver = FakeDriver(lambda c: Reply(outputs={"memo.json": jbytes({"gist": "x"})}))
    st = R.submit_run(
        ws,
        dataset="memos",
        agent=AGENT,
        process_id=PROCESS,
        driver=driver,
        store=store,
        run_id="r-j",
    )
    st = R.advance(ws, "r-j", driver, store)
    assert st["status"] == "complete"
    assert all(c["result"]["status"] == STATUS_PENDING for c in st["cases"].values())

    packet_p = R.build_packet(ws, "r-j", store)
    assert packet_p == "ds-memos/judging/r-j/packet.json"
    packet = json.loads(store.files[packet_p])
    assert {i["case_id"] for i in packet["items"]} == {"m1", "m2"}
    assert packet["items"][0]["criteria"] == ["tone", "facts"]
    assert "the secret gist" in json.dumps(packet), "the packet is the one place judged values go"
    assert "secret gist" not in R.state_path(ws, "r-j").read_text()

    with pytest.raises(UsageError, match="no verdict"):
        R.apply_judgements(ws, "r-j", {"m1": {"scores": {"tone": 1, "facts": 1}}})
    R.apply_judgements(
        ws,
        "r-j",
        {
            "m1": {"scores": {"tone": 1, "facts": 0.9}, "checks": {"polite": True}},
            "m2": {"scores": {"tone": 1, "facts": 0.1}, "checks": {"polite": True}},
        },
    )
    res = R.collect(ws, "r-j", store)
    assert {c.case_id: c.passed for c in res.cases} == {"m1": True, "m2": False}
    with pytest.raises(UsageError, match="not pending"):
        R.apply_judgements(ws, "r-j", {"m1": {"scores": {"tone": 1}}})


def test_build_packet_none_when_nothing_pending(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 1)
    res = _go(ws, store, FakeDriver(correct_handler))
    assert R.build_packet(ws, res.run_id, store) is None


def test_unknown_case_ids_refused(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 1)
    with pytest.raises(UsageError, match="unknown"):
        _go(ws, store, FakeDriver(correct_handler), case_ids=["nope"])


def test_case_subset_runs_only_those(tmp_path: Path) -> None:
    ws, store = work_product_ws(tmp_path, 3)
    d = FakeDriver(correct_handler)
    res = _go(ws, store, d, case_ids=["c2"])
    assert [c.case_id for c in res.cases] == ["c2"] and len(d.calls) == 1


# --------------------------------------------------------------------------- unscored datasets


def _unscored_ws(tmp_path: Path, **create: object):
    ws, store = tmp_path / "ws", MemStore()
    write_prompts(ws)
    D.create_dataset(ws, "returns", "work_product", client="acme", **create)  # type: ignore[arg-type]
    add_case(ws, store, "returns", "c1")
    add_case(ws, store, "returns", "c2", scorers=["field_match"])
    add_case(ws, store, "returns", "c3")
    return ws, store


def test_run_refuses_cases_with_no_scorer_and_no_checks(tmp_path: Path) -> None:
    ws, store = _unscored_ws(tmp_path)
    driver = FakeDriver(correct_handler)
    with pytest.raises(UsageError, match=r"case\(s\) c1, c3 have no scorer") as e:
        _go(ws, store, driver)
    assert "c2" not in str(e.value).split("have no scorer")[0]
    assert driver.calls == []  # refused before any turn started


def test_run_accepts_dataset_default_scorers_or_a_client_check(tmp_path: Path) -> None:
    ws, store = _unscored_ws(tmp_path, default_scorers=["field_match"])
    assert R.effective_scorers(D.load_dataset(ws, "returns"), {"scorers": []}) == ["field_match"]
    R.refuse_unscored(
        ws, D.load_dataset(ws, "returns"), D.load_dataset(ws, "returns").active_cases()
    )
    ws2, _ = _unscored_ws(tmp_path / "b")
    write_check(ws2, "any", severity="error", rule={"type": "present", "field": "x"})
    ds = D.load_dataset(ws2, "returns")
    R.refuse_unscored(ws2, ds, ds.active_cases())  # a client check scores every case
