"""``abcd metric``, ``check`` and ``criteria``: files in the bundle, mirrored rows, approvals."""

from __future__ import annotations

import yaml

from tests.builder.evals_fakes import AGENT

from .quality_fixtures import QEnv, qenv  # noqa: F401


def _release(qenv: QEnv) -> dict:  # noqa: F811
    return yaml.safe_load((qenv.ws / "harness/agents" / AGENT / "release.yaml").read_text())


def test_metric_define_list_and_versioning(qenv: QEnv) -> None:  # noqa: F811
    d = qenv.ok("metric", "define", AGENT, "accuracy", "--category", "quality", "--unit", "%",
                "--formula", "pass_rate", "--source", "check_results", "--baseline", "0.6",
                "--target", "build=0.9", "--target", "pilot=0.95", "--gate",
                "--decision-ref", "dec-1")  # fmt: skip
    assert d["metric_key"] == f"{AGENT}:accuracy@1"
    path = qenv.ws / "harness/agents" / AGENT / "metrics.yaml"
    assert yaml.safe_load(path.read_text())["metrics"][0]["targets"] == {
        "build": 0.9,
        "pilot": 0.95,
    }
    d = qenv.ok("metric", "define", AGENT, "accuracy", "--category", "quality",
                "--formula", "pass_rate", "--source", "check_results")  # fmt: skip
    assert d["metric_key"].endswith("@2")
    listed = qenv.ok("metric", "list", AGENT)["metrics"]
    assert len(listed) == 1 and listed[0]["version"] == 2
    keys = {r["metric_key"] for r in qenv.rows("bld_metrics")}
    assert keys == {f"{AGENT}:accuracy@1", f"{AGENT}:accuracy@2"}
    assert any(s.startswith(f"{AGENT}: metric accuracy v2") for s in qenv.commits())
    code, doc = qenv.run("metric", "define", AGENT, "x", "--category", "quality", "--source", "check_results",
                         "--target", "novalue")  # fmt: skip
    assert code == 2


def test_check_add_list_and_rows(qenv: QEnv) -> None:  # noqa: F811
    qenv.ok("check", "add", AGENT, "tax-range", "--title", "Tax within range", "--rule-type", "range",
            "--field", "total_tax", "--min", "0", "--max", "1000000", "--decision", "dec-7")  # fmt: skip
    qenv.ok("check", "add", AGENT, "polite", "--kind", "judged", "--rubric", "Is the memo polite?",
            "--severity", "warning")  # fmt: skip
    y = yaml.safe_load((qenv.ws / "harness/checks/tax-range.yaml").read_text())
    assert y["severity"] == "error" and y["rule"] == {
        "type": "range",
        "field": "total_tax",
        "min": 0.0,
        "max": 1000000.0,
    }
    rows = {r["check_key"]: r for r in qenv.rows("bld_checks")}
    assert rows["tax-range"]["severity"] == "blocking" and rows["polite"]["severity"] == "warning"
    assert (
        rows["polite"]["rule"] == {"rubric": "Is the memo polite?"}
        and rows["tax-range"]["active_from"]
    )
    assert [c["check_id"] for c in qenv.ok("check", "list", AGENT)["checks"]] == [
        "polite",
        "tax-range",
    ]
    code, doc = qenv.run(
        "check", "add", AGENT, "tax-range", "--rule-type", "present", "--field", "x"
    )
    assert code == 1 and "--replace" in doc["error"]
    qenv.ok(
        "check",
        "add",
        AGENT,
        "tax-range",
        "--rule-type",
        "present",
        "--field",
        "total_tax",
        "--replace",
    )
    assert qenv.rows("bld_checks")[0]["rule"]["type"] == "present"
    code, _ = qenv.run("check", "add", AGENT, "bad", "--kind", "judged")
    assert code == 2
    code, _ = qenv.run("check", "add", AGENT, "bad2", "--rule-type", "equals", "--field", "f")
    assert code == 2


def test_criteria_set_show_and_required_criteria(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(2)
    d = qenv.ok("criteria", "set", AGENT, "--dataset", "returns", "--metric", "pass_rate",
                "--op", ">=", "--target", "0.9", "--decision-ref", "dec-3")  # fmt: skip
    keys = [c["criteria_key"] for c in d["criteria"]]
    assert keys[0] == f"{AGENT}:returns:pass_rate"
    assert {c["metric"] for c in d["criteria"]} == {"pass_rate", "blind_repro_rate", "open_gaps"}
    rel = _release(qenv)
    assert {c["dataset_version"] for c in rel["criteria"]} == {3}
    rows = {r["criteria_key"]: r for r in qenv.rows("bld_release_criteria")}
    assert rows[keys[0]]["comparator"] == ">=" and rows[keys[0]]["target"] == 0.9
    assert rows[f"{AGENT}:returns:open_gaps"]["comparator"] == "<="
    shown = qenv.ok("criteria", "show", AGENT)
    assert shown["missing_required"] == [] and shown["stale"] == []
    qenv.ok("dataset", "add-case", "returns", "c9", "--label", "a=b")
    assert "pinned v3" in qenv.ok("criteria", "show", AGENT)["stale"][0]
    # setting a criterion again re-pins the automatic ones to the dataset's current version
    qenv.ok("criteria", "set", AGENT, "--dataset", "returns", "--metric", "pass_rate",
            "--op", ">=", "--target", "0.9", "--decision-ref", "dec-3")  # fmt: skip
    assert {c["dataset_version"] for c in _release(qenv)["criteria"]} == {4}
    assert qenv.ok("criteria", "show", AGENT)["stale"] == []
    qenv.ok("criteria", "set", AGENT, "--dataset", "returns", "--op", "no_regression")
    assert f"{AGENT}:returns:no_regression" in {
        r["criteria_key"] for r in qenv.rows("bld_release_criteria")
    }
    code, doc = qenv.run("criteria", "set", AGENT, "--dataset", "returns", "--metric", "nonsense",
                         "--op", ">=", "--target", "1")  # fmt: skip
    assert code == 2 and "unknown metric" in doc["error"]


def test_criteria_loosening_needs_approval(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(1)
    args = ("criteria", "set", AGENT, "--dataset", "returns", "--metric", "pass_rate", "--op", ">=")
    qenv.ok(*args, "--target", "0.9")
    # raising is fine, lowering is refused, lowering with an approver is recorded
    qenv.ok(*args, "--target", "0.95")
    code, doc = qenv.run(*args, "--target", "0.8")
    assert code == 1 and "--approved-by" in doc["error"]
    assert (
        next(c for c in _release(qenv)["criteria"] if c["metric"] == "pass_rate")["target"] == 0.95
    )
    d = qenv.ok(*args, "--target", "0.8", "--approved-by", "lead", "--decision-ref", "dec-9")
    entry = d["criteria"][0]
    assert entry["target"] == 0.8 and entry["history"][-1]["approved_by"] == "lead"
    assert entry["set_by"] == "lead"
    # weakening the required gates is loosening too; replacing with no_regression as well
    code, _ = qenv.run("criteria", "set", AGENT, "--dataset", "returns", "--metric", "open_gaps",
                       "--op", "<=", "--target", "2")  # fmt: skip
    assert code == 1
    code, _ = qenv.run("criteria", "set", AGENT, "--dataset", "returns", "--op", "no_regression")
    assert code == 0
    code, _ = qenv.run("criteria", "set", AGENT, "--dataset", "returns", "--metric", "pass_rate",
                       "--op", "<=", "--target", "1")  # fmt: skip
    assert code == 1  # an op change cannot be compared, so it needs an approver


def test_release_yaml_is_one_schema_for_criteria_and_release(qenv: QEnv) -> None:  # noqa: F811
    import pytest

    from cli.builder_common.errors import RefusedError
    from cli.builder_release import load_release

    qenv.make_dataset(2)
    qenv.ok("criteria", "set", AGENT, "--dataset", "returns", "--metric", "pass_rate",
            "--op", ">=", "--target", "0.9")  # fmt: skip
    qenv.ok("criteria", "set", AGENT, "--dataset", "returns", "--op", "no_regression")
    rel = _release(qenv)
    assert rel["dataset"] == "returns@3" and rel["agent"] == AGENT
    assert all("op" not in c and "dataset" not in c for c in rel["criteria"])
    by = {c["metric"]: c for c in rel["criteria"]}
    assert (by["pass_rate"]["comparator"], by["pass_rate"]["target"]) == (">=", 0.9)
    assert (by["blind_repro_rate"]["comparator"], by["blind_repro_rate"]["target"]) == (">=", 1.0)
    assert (by["open_gaps"]["comparator"], by["open_gaps"]["target"]) == ("<=", 0.0)
    assert by["no_regression"]["comparator"] == "no_regression"
    # the release step reads exactly what criteria set wrote
    crit = load_release(qenv.ws / "harness/agents" / AGENT)
    assert (crit.dataset, crit.dataset_version) == ("returns", 3)
    assert {c["metric"] for c in crit.criteria} == set(by)
    # and the mirrored rows carry the same comparator and dataset
    rows = {r["metric_key"]: r for r in qenv.rows("bld_release_criteria")}
    assert rows["pass_rate"]["comparator"] == ">=" and rows["pass_rate"]["dataset_key"] == "returns"
    assert rows["no_regression"]["comparator"] == "no_regression"
    # one dataset per agent
    qenv.make_dataset(1, name="other")
    code, doc = qenv.run("criteria", "set", AGENT, "--dataset", "other", "--metric", "pass_rate",
                         "--op", ">=", "--target", "0.5")  # fmt: skip
    assert code == 1 and "one dataset" in doc["error"]
    with pytest.raises(RefusedError):
        load_release(qenv.ws / "harness/agents/none")


def test_the_first_release_yaml_form_is_rewritten_once(qenv: QEnv) -> None:  # noqa: F811
    qenv.make_dataset(1)
    legacy = {
        "criteria": [
            {"criteria_key": f"{AGENT}:returns:pass_rate", "dataset": "returns",
             "dataset_version": 2, "metric": "pass_rate", "op": ">=", "target": 0.9,
             "history": [{"at": "t", "by": "x", "approved_by": None, "op": ">=", "target": 0.9}]},
        ]
    }  # fmt: skip
    (qenv.ws / "harness/agents" / AGENT / "release.yaml").write_text(yaml.safe_dump(legacy))
    shown = qenv.ok("criteria", "show", AGENT)
    assert shown["criteria"][0]["comparator"] == ">=" and "op" not in shown["criteria"][0]
    qenv.ok("criteria", "set", AGENT, "--dataset", "returns", "--metric", "pass_rate",
            "--op", ">=", "--target", "0.95")  # fmt: skip
    rel = _release(qenv)
    assert rel["dataset"].startswith("returns@")
    entry = next(c for c in rel["criteria"] if c["metric"] == "pass_rate")
    assert entry["comparator"] == ">=" and entry["history"][0]["comparator"] == ">="
