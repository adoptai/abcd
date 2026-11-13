"""A full fake eval (dataset, blind run, gap loop, instructions, judging) writes only valid rows.

Every row any command writes into the Data Store is checked against its ``bld_*`` spec with
``tables.validate_row`` (the fake backend and ``OrgRowStore`` already refuse an invalid row; this
re-checks the final state of every table the scenario touched, and that no answer value is in it).
"""

from __future__ import annotations

import json

from cli.builder_common.tables import validate_row
from tests.builder.evals_fakes import AGENT

from .quality_fixtures import SENTINEL_ENTITY, SENTINEL_TAX, QEnv, qenv  # noqa: F401

TOUCHED = {
    "bld_datasets",
    "bld_dataset_versions",
    "bld_dataset_cases",
    "bld_eval_runs",
    "bld_eval_results",
    "bld_metric_values",
    "bld_generic_prompts",
    "bld_context_gaps",
    "bld_instructions",
    "bld_metrics",
    "bld_checks",
}


def test_full_fake_eval_writes_only_spec_valid_rows(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(2)
    qenv.answers["c2"] = None  # c2 is missed for lack of context
    run1 = str(
        qenv.ok("eval", "run", AGENT, "--dataset", "returns", "--blind", "--staged")["run_id"]
    )
    cls = qenv.file("cls.json", {"c2": "missing_context"})
    gap = qenv.ok("gap", "open", "--from-run", run1, "--classify", cls)["opened"][0]
    qenv.ok("gap", "ask", gap, "--to", "sme", "--by", "fde1")
    qenv.ok("gap", "answer", gap, "--file", qenv.file("a.md", "Round totals to whole dollars."))
    page = qenv.file("p.md", "# Round totals\n\nAlways round the total tax to whole dollars.\n")
    qenv.ok("gap", "encode", gap, "--page", page, "--name", "rounding", "--vetted-by", "sme1")
    qenv.ok("instructions", "compile", AGENT)
    qenv.answers.clear()
    run2 = str(
        qenv.ok("eval", "run", AGENT, "--dataset", "returns", "--blind", "--staged")["run_id"]
    )
    qenv.ok("gap", "verify", gap, "--run", run2)
    qenv.ok("metric", "define", AGENT, "accuracy", "--category", "quality", "--formula", "pass_rate",
            "--source", "check_results", "--target", "build=0.9")  # fmt: skip
    qenv.ok("check", "add", AGENT, "tax-range", "--title", "Tax within range", "--rule-type",
            "range", "--field", "total_tax", "--min", "0", "--max", "1000000")  # fmt: skip

    tables = {t: rows for t, rows in qenv.backend.tables.items() if t.startswith("bld_") and rows}
    assert set(tables) >= TOUCHED, sorted(set(TOUCHED) - set(tables))
    for table, rows in tables.items():
        for row in rows:
            validate_row(table, {k: v for k, v in row.items() if k != "id"})
    blob = json.dumps(tables, default=str)
    assert SENTINEL_ENTITY not in blob and str(SENTINEL_TAX) not in blob
