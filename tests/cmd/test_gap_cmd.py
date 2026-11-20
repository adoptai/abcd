"""``abcd gap``, ``instructions`` and ``build-exit``: the context-gap loop end to end."""

from __future__ import annotations

import json

from tests.builder.evals_fakes import AGENT

from .quality_fixtures import CLIENT, SENTINEL_ENTITY, QEnv, qenv  # noqa: F401

GOOD_PAGE = "# Round totals\n\nAlways round the total tax to whole dollars before filing.\n"


def _blind(qenv: QEnv) -> str:  # noqa: F811
    return str(
        qenv.ok("eval", "run", AGENT, "--dataset", "returns", "--blind", "--staged")["run_id"]
    )


def _open_gap(qenv: QEnv) -> tuple[str, str]:  # noqa: F811
    """Dataset of 2, c2 missed for lack of context: returns (run id, gap key)."""
    qenv.make_dataset(2)
    qenv.answers["c2"] = None
    run1 = _blind(qenv)
    cls = qenv.file("cls.json", {"c2": "missing_context"})
    d = qenv.ok("gap", "open", "--from-run", run1, "--classify", cls)
    assert len(d["opened"]) == 1 and d["build_plan"] == []
    return run1, d["opened"][0]


def test_gap_lifecycle_to_verified_by_rerun(qenv: QEnv) -> None:  # noqa: F811
    run1, gap = _open_gap(qenv)
    # the SME-facing gap file shows the golden output by path and hash only
    text = qenv.store.files[f"builder-sme-{CLIENT}/gaps/{gap}.md"].decode()
    assert "ds-returns/expected/c2/" in text and SENTINEL_ENTITY not in text
    assert qenv.rows("bld_context_gaps")[0]["case_key"] == "returns:c2"
    listed = qenv.ok("gap", "list", "--agent", AGENT)
    assert [g["gap_key"] for g in listed["gaps"]] == [gap] and listed["open_by_owner"] == {"fde": 1}

    assert qenv.ok("gap", "ask", gap, "--to", "sme", "--by", "fde1")["status"] == "asked"
    assert qenv.ok("gap", "list", "--owner", "sme")["open_by_owner"] == {"sme": 1}
    answer = qenv.file("answer.md", "Totals are rounded to whole dollars, always.")
    a = qenv.ok("gap", "answer", gap, "--file", answer, "--by", "sme1")
    assert a["answer_path"].startswith(f"builder-sme-{CLIENT}/answers/{gap}/")
    assert qenv.rows("bld_context_gaps")[0]["answer_sha256"]

    # a page that quotes an expected value or names a case is refused; the gap stays answered
    bad = qenv.file("bad.md", f"# Rule\n\nThe entity {SENTINEL_ENTITY} is always c2.\n")
    code, doc = qenv.run("gap", "encode", gap, "--page", bad, "--name", "leaky")
    assert code == 1 and "leak check flagged" in doc["error"]
    assert {f["kind"] for f in doc["data"]["findings"]} >= {"case_id", "expected_value"}
    assert SENTINEL_ENTITY not in json.dumps(doc)
    assert qenv.ok("gap", "list")["gaps"][0]["status"] == "answered"

    # fixing the page under the same name makes a new revision that replaces the flagged one
    page = qenv.file("good.md", GOOD_PAGE)
    e = qenv.ok("gap", "encode", gap, "--page", page, "--name", "leaky", "--vetted-by", "sme1")
    assert (e["status"], e["instruction_status"]) == ("encoded", "vetted")
    assert e["page_path"].startswith(f"builder-context/kg/instructions/{AGENT}/")
    inst = qenv.rows("bld_instructions")[-1]
    assert (
        inst["leak_check"] == "pass" and inst["status"] == "vetted" and inst["source_gaps"] == [gap]
    )

    c = qenv.ok("instructions", "compile", AGENT)
    skill = qenv.ws / "harness/skills" / f"{AGENT}-instructions"
    assert (skill / "SKILL.md").exists() and (skill / "references/leaky.md").exists()
    assert c["included"] == [f"{AGENT}:leaky"] and c["commit"]
    assert qenv.rows("bld_instructions")[-1]["compiled_into_version"] == c["skill_version"]

    # build exit is not ready while the gap is encoded; verification needs a passing blind rerun
    code, doc = qenv.run("build-exit", AGENT, "--run", run1)
    assert code == 1 and doc["data"]["ready"] is False
    qenv.answers.clear()
    run2 = _blind(qenv)
    v = qenv.ok("gap", "verify", gap, "--run", run2)
    assert v["status"] == "verified"
    assert qenv.rows("bld_context_gaps")[0]["verified_by_run"] == run2
    d = qenv.ok("build-exit", AGENT)  # the latest complete blind run
    assert d["ready"] is True and d["run_id"] == run2


def test_verify_reopens_when_the_rerun_still_fails(qenv: QEnv) -> None:  # noqa: F811
    run1, gap = _open_gap(qenv)
    qenv.ok("gap", "answer", gap, "--file", qenv.file("a.md", "an answer"))
    qenv.ok("gap", "encode", gap, "--page", qenv.file("p.md", GOOD_PAGE), "--name", "r1")
    run2 = _blind(qenv)  # c2 is still missed
    code, doc = qenv.run("gap", "verify", gap, "--run", run2)
    assert code == 1 and doc["data"]["status"] == "open"
    assert qenv.rows("bld_context_gaps")[0]["status"] == "open"
    # verifying a gap that is not encoded is refused; a non-blind run cannot verify
    code, doc = qenv.run("gap", "verify", gap, "--run", run2)
    assert code == 1 and "only an encoded gap" in doc["error"]
    # a second round closes it
    qenv.ok("gap", "answer", gap, "--file", qenv.file("a2.md", "a better answer"))
    qenv.ok(
        "gap",
        "encode",
        gap,
        "--page",
        qenv.file("p2.md", GOOD_PAGE + "\nAnd keep cents out.\n"),
        "--name",
        "r2",
    )
    qenv.answers.clear()
    assert qenv.ok("gap", "verify", gap, "--run", _blind(qenv))["status"] == "verified"


def test_waive_needs_lead_and_reason_and_counts_for_build_exit(qenv: QEnv) -> None:  # noqa: F811
    run1, gap = _open_gap(qenv)
    code, _ = qenv.run("gap", "waive", gap, "--lead", " ", "--reason", "client accepts")
    assert code == 1
    w = qenv.ok("gap", "waive", gap, "--lead", "lead1", "--reason", "client accepts the variance")
    assert w["status"] == "waived"
    row = qenv.rows("bld_context_gaps")[0]
    assert row["status"] == "waived" and row["waive_reason"] == "client accepts the variance"
    # a waived gap is neither open nor blocking; its case is exempt from the reproduction check
    code, doc = qenv.run("build-exit", AGENT, "--run", run1, "--repro-target", "0.5")
    assert code == 0 and doc["data"]["waived"] == ["c2"]
    code, doc = qenv.run("build-exit", AGENT, "--run", run1)  # default target 1.0 not met
    assert code == 1 and doc["data"]["checks"][3]["passed"] is False
    code, _ = qenv.run("gap", "ask", gap)
    assert code == 1  # waived is terminal


def test_build_exit_without_a_blind_run_is_not_ready(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(1)
    code, doc = qenv.run("build-exit", AGENT)
    assert code == 1 and doc["data"]["ready"] is False
    assert doc["data"]["checks"][0]["check"] == "a completed blind run exists"
    open_run = qenv.ok("eval", "run", AGENT, "--dataset", "returns")["run_id"]
    code, doc = qenv.run("build-exit", AGENT, "--run", open_run)
    assert code == 1 and "not blind" in doc["error"]


def test_compile_refuses_when_an_instruction_now_leaks(qenv: QEnv) -> None:  # noqa: F811
    run1, gap = _open_gap(qenv)
    qenv.ok("gap", "answer", gap, "--file", qenv.file("a.md", "an answer"))
    page = qenv.file("p.md", "# Special handling\n\nTreat acme-special filings as extensions.\n")
    qenv.ok("gap", "encode", gap, "--page", page, "--name", "special", "--vetted-by", "fde1")
    # a case with that id arrives later, so the page now names a case id
    qenv.ok("dataset", "add-case", "returns", "acme-special", "--label", "a=b")
    code, doc = qenv.run("instructions", "compile", AGENT)
    assert code == 1 and "leak check flagged" in doc["error"]
    assert not (qenv.ws / "harness/skills" / f"{AGENT}-instructions").exists()
    code, doc = qenv.run("instructions", "compile", "no-such-agent")
    assert code == 2 and "no vetted" in doc["error"]


def test_instructions_sync_client_scope(qenv: QEnv) -> None:  # noqa: F811
    run1, gap = _open_gap(qenv)
    qenv.ok("gap", "answer", gap, "--file", qenv.file("a.md", "an answer"))
    qenv.ok("gap", "encode", gap, "--page", qenv.file("p.md", GOOD_PAGE), "--name", "client-rule",
            "--scope", "client", "--vetted-by", "fde1")  # fmt: skip
    code, doc = qenv.run("instructions", "compile", AGENT)
    assert code == 2, "client scope is synced, not compiled"
    d = qenv.ok("instructions", "sync", CLIENT, "--agent", AGENT, "--workstream", "acme-staging")
    assert "acme-staging/_builder/instructions/client-rule.md" in d["written"]
    assert "ds:returns:c1/_builder/instructions/client-rule.md" in d["written"]
    assert qenv.store.files["acme-staging/_builder/instructions/client-rule.md"]
    dry = qenv.ok("instructions", "sync", CLIENT, "--dry-run")
    assert dry["dry_run"] is True and dry["plan"]
    assert qenv.ok("instructions", "sync", "unrelated-client")["written"] == []
