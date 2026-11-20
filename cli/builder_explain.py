"""Explainers and training kits (PLAN.md section 16).

* ``abcd explain <agent>``: ``harness/agents/<agent>/docs/explainer.html``.
* ``abcd train <agent> [--validate-on DATASET]``: ``harness/agents/<agent>/training/``.

Both write only under the agent's bundle folder. A failed generation is flagged in the result and
the exit code stays 0, so a release is never blocked by it. Rows from the platform enrich the
page when they can be read; when they cannot, the page is built from the bundle alone and the
result says so.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from cli.builder_common.errors import UsageError
from cli.builder_common.evals.protocols import TURN_COMPLETED, TURN_RUNNING
from cli.builder_common.explain import (
    AgentInfo,
    DatasetInfo,
    EvalInfo,
    GenerationResult,
    VersionRow,
    generate_explainer,
    info_from_bundle,
)
from cli.builder_common.train import FaqEntry, generate_training_kit, validate_training_prompts
from cli.builder_common.workspace import agent_dir, load_builder_config
from cli.builder_context import check_name, open_db
from cli.builder_status import build_criteria, latest_row, open_item_rows, version_number

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.output import Result
    from cli.core.registry import Registry

POLL_SECONDS = 3.0


def agent_extras(db: Any, agent: str) -> dict[str, Any]:
    """Keyword arguments for ``info_from_bundle`` taken from the tables."""
    releases = {
        str(r.get("version_id")): str(r.get("released_at") or "")[:10]
        for r in db.find_rows("bld_releases", "agent_key", agent)
    }
    versions = sorted(
        (
            r
            for r in db.find_rows("bld_versions", "object_name", agent)
            if r.get("object_kind") == "agent"
        ),
        key=lambda r: version_number(str(r.get("version_id"))),
    )
    crit_rows = db.find_rows("bld_release_criteria", "agent_key", agent)
    extras: dict[str, Any] = {
        "versions": tuple(
            VersionRow(
                version=str(v.get("version_id")).rsplit("@", 1)[-1],
                date=releases.get(str(v.get("version_id")), ""),
                note=str(v.get("reason_ref") or ""),
                live=v.get("state") == "live",
                staged=v.get("state") == "staged",
            )
            for v in versions
        ),
        "criteria": build_criteria(
            crit_rows,
            db.find_rows("bld_metrics", "agent_key", agent),
            db.find_rows("bld_metric_values", "agent_key", agent),
        ),
        "checks": tuple(
            str(c.get("title")) for c in db.find_rows("bld_checks", "agent_key", agent)
        ),
        "open_items": open_item_rows(db.find_rows("bld_open_items", "agent_key", agent)),
    }
    if crit_rows and crit_rows[0].get("dataset_key"):
        key = str(crit_rows[0]["dataset_key"])
        ds = (db.find_rows("bld_datasets", "dataset_key", key) or [{}])[0]
        extras["dataset"] = DatasetInfo(
            name=str(ds.get("name") or key),
            case_count=len(db.find_rows("bld_dataset_cases", "dataset_key", key)),
            kinds=(str(ds["kind"]),) if ds.get("kind") else (),
        )
    run = latest_row(db.find_rows("bld_eval_runs", "agent_key", agent), "started_at")
    if run:
        results = db.find_rows("bld_eval_results", "eval_run_id", str(run.get("eval_run_id")))
        passed = sum(1 for r in results if r.get("passed") is True)
        extras["last_eval"] = EvalInfo(
            date=str(run.get("started_at") or "")[:10],
            score=f"{passed}/{len(results)}" if results else "",
            passed=passed,
            failed=len(results) - passed,
            summary=f"{run.get('tier')} run, {run.get('status')}",
        )
    return extras


def load_info(ctx: Context, agent: str, offline: bool) -> tuple[AgentInfo, Path, list[str]]:
    ws = ctx.env_dir
    bundle = agent_dir(ws, check_name(agent, "agent"))
    if not bundle.is_dir():
        raise UsageError(f"no agent bundle at {bundle}")
    warnings: list[str] = []
    extras: dict[str, Any] = {}
    if not offline:
        try:
            extras = agent_extras(open_db(ctx), agent)
        except Exception as exc:
            warnings.append(
                f"platform data unavailable, built from the bundle only: {type(exc).__name__}: {exc}"
            )
    return info_from_bundle(bundle, **extras), bundle, warnings


def _generation_data(
    kind: str, res: GenerationResult, warnings: Sequence[str], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "kind": kind,
        "generated": res.ok,
        "flagged": not res.ok,
        "error": res.error,
        "files": [str(p) for p in res.paths],
        "warnings": list(warnings),
        **(extra or {}),
    }


def _text(kind: str, res: GenerationResult, warnings: Sequence[str]) -> str:
    lines = (
        [f"Wrote {len(res.paths)} {kind} file(s): " + ", ".join(str(p) for p in res.paths)]
        if res.ok
        else [f"FLAGGED: {res.error} (the release is not blocked)"]
    )
    lines += [f"warning: {w}" for w in warnings]
    return "\n".join(lines)


def _cmd_explain(ctx: Context) -> Result:
    from cli.core.output import Result

    agent = ctx.args.agent
    info, bundle, warnings = load_info(ctx, agent, ctx.args.offline)
    if ctx.dry_run_notice(f"would write {bundle / 'docs' / 'explainer.html'}"):
        return Result.done({"dry_run": True, "agent": agent})
    res = generate_explainer(info, bundle)
    return Result.done(
        _generation_data("explainer", res, warnings), _text("explainer", res, warnings)
    )


# -- train ------------------------------------------------------------------------------------


def read_lines(path: Path) -> list[str]:
    return [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def read_faq(path: Path) -> list[FaqEntry]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise UsageError(f"{path} must be a YAML list of {{question, answer}}")
    return [
        FaqEntry(str(i["question"]), str(i["answer"]))
        for i in raw
        if isinstance(i, dict) and "question" in i and "answer" in i
    ]


def make_driver(ctx: Context) -> Any:
    from cli.builder_common.datasets import recorded_workstream_ids
    from cli.builder_common.evals.bff_driver import BffHarnessDriver

    return BffHarnessDriver(
        ctx.harness_client(), workstream_ids=recorded_workstream_ids(ctx.env_dir)
    )


def dataset_runner(ctx: Context, agent: str, dataset: str, timeout: float) -> Callable[[str], bool]:
    """Run one prompt as a turn of the staged agent in the dataset's workstream; True if it completed."""
    cfg = load_builder_config(ctx.env_dir)
    workstream = cfg["workstreams"].get(f"ds:{dataset}")
    process = (cfg["agents"].get(agent) or {}).get("staged_process_id")
    if not workstream:
        raise UsageError(f"builder.json has no workstream 'ds:{dataset}'; create the dataset first")
    if not process:
        raise UsageError(f"agent {agent} has no staged process id; run `abcd stage` first")
    driver = make_driver(ctx)

    def run(prompt: str) -> bool:
        handle = driver.start_turn(workstream=workstream, process_id=process, message=prompt)
        deadline = time.monotonic() + timeout
        status = driver.turn_status(handle)
        while status.state == TURN_RUNNING and time.monotonic() < deadline:
            time.sleep(POLL_SECONDS)
            status = driver.turn_status(handle)
        if status.state == TURN_RUNNING:
            driver.stop_turn(handle)
        return bool(status.state == TURN_COMPLETED and not status.error)

    return run


def _cmd_train(ctx: Context) -> Result:
    from cli.core.output import Result

    a = ctx.args
    info, bundle, warnings = load_info(ctx, a.agent, a.offline)
    candidates: list[str] = []
    source = Path(a.prompts) if a.prompts else bundle / "training" / "candidate-prompts.txt"
    if source.is_file():
        candidates = read_lines(source)
    elif a.prompts:
        raise UsageError(f"{source} is not a file")
    faq = read_faq(Path(a.faq)) if a.faq else []
    if ctx.dry_run_notice(f"would write {bundle / 'training'}"):
        return Result.done({"dry_run": True, "agent": a.agent})
    shipped: list[str] = []
    dropped: list[str] = []
    if a.validate_on:
        validation = validate_training_prompts(
            candidates, dataset_runner(ctx, a.agent, a.validate_on, a.timeout)
        )
        shipped, dropped = validation.shipped, validation.dropped
    elif candidates:
        warnings.append(
            f"{len(candidates)} candidate prompt(s) not shipped: pass --validate-on DATASET to run them first"
        )
    res = generate_training_kit(
        info, bundle, prompts=shipped, faq=faq, whats_new=list(a.note or [])
    )
    data = _generation_data(
        "training kit", res, warnings, {"shipped_prompts": shipped, "dropped_prompts": dropped}
    )
    return Result.done(data, _text("training kit", res, warnings))


def register(registry: Registry) -> None:
    p = registry.command(
        "explain", _cmd_explain, help="Regenerate an agent's explainer page", writes=True
    )
    p.add_argument("agent")
    p.add_argument(
        "--offline", action="store_true", help="Build from the bundle only; read no tables"
    )
    p = registry.command(
        "train", _cmd_train, help="Regenerate an agent's training kit", writes=True
    )
    p.add_argument("agent")
    p.add_argument(
        "--validate-on",
        metavar="DATASET",
        help="Run candidate prompts on this dataset; only the ones that work ship",
    )
    p.add_argument(
        "--prompts",
        help="File of candidate prompts, one per line (default: training/candidate-prompts.txt)",
    )
    p.add_argument("--faq", help="YAML list of {question, answer}")
    p.add_argument("--note", action="append", help="A line for 'what's new' (repeatable)")
    p.add_argument(
        "--timeout", type=float, default=300.0, help="Seconds to wait for each validation turn"
    )
    p.add_argument("--offline", action="store_true")
