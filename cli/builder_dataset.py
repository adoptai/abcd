"""``abcd dataset ...``: create, extend, search and retire datasets (PLAN.md section 10).

Every write goes through ``cli.builder_common.datasets`` and then:

* writes the workspace case files and commits them (references only: path + sha256);
* uploads inputs to the dataset workstream stores and every expected value, label and state
  ONLY to the ``ds-<dataset>`` store (linked to builder-ops, never to a workstream an agent
  under test can read);
* creates the dataset workstreams per the layout and assigns the staged agent and the probe;
* mirrors rows (paths and hashes only, validated against the table specs).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from cli import builder_eval as be
from cli.builder_common import datasets as D  # noqa: N812
from cli.builder_common import workspace as bws
from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.release_approval import refuse_self_asserted_in_cloud
from cli.core.context import Context
from cli.core.output import Result
from cli.core.registry import Registry

_VERSION_ROWS = "bld_dataset_versions"


# --------------------------------------------------------------------------- helpers


def _read_files(paths: list[str] | None) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for raw in paths or []:
        p = Path(raw)
        if not p.is_file():
            raise UsageError(f"file not found: {p}")
        if p.name in out:
            raise UsageError(f"two files named {p.name!r}")
        out[p.name] = p.read_bytes()
    return out


def _labels(ctx: Context) -> dict[str, bytes]:
    pairs: list[str] | None = ctx.args.label
    if not pairs:
        return {}
    # Labels are answers. The command event log records every argument (and may be mirrored to
    # a table), so keep only the keys there.
    ctx.args.label = [f"{p.partition('=')[0]}=***" for p in pairs]
    labels: dict[str, str] = {}
    for pair in pairs:
        k, sep, v = pair.partition("=")
        if not sep or not k:
            raise UsageError(f"--label expects key=value, got {pair!r}")
        labels[k] = v
    return {"labels.json": json.dumps(labels, indent=2, sort_keys=True).encode()}


def _state(path: str | None) -> dict[str, bytes]:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        raise UsageError(f"file not found: {p}")
    return {"state.json": p.read_bytes()}


def _load_spec(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        raise UsageError(f"file not found: {p}")
    data = yaml.safe_load(p.read_text()) or {}
    if not isinstance(data, dict):
        raise UsageError(f"{p} must hold a mapping")
    return data


def _process_ids(ws: Path, agents: list[str], rows: Any = None) -> list[str]:
    """builder.json first, then the ``bld_agents`` row a ``push --staged`` leaves behind."""
    agents_cfg = bws.load_builder_config(ws)["agents"]
    ids = []
    for a in agents:
        entry = agents_cfg.get(a) or {}
        pid = entry.get("staged_process_id") or entry.get("live_process_id")
        if not pid and rows is not None:
            found = rows.query("bld_agents", {"agent_key": a})
            row = found[0] if found else {}
            pid = row.get("staged_process_id") or row.get("process_id")
        if not pid:
            raise UsageError(
                f"agent {a!r} has no process id yet (run `abcd push {a} --staged` first)"
            )
        ids.append(str(pid))
    return ids


def _ensure_workstream(svc: be.Services, ws: Path, ds: D.Dataset, case_id: str) -> str:
    """Create the dataset workstream for a case (or the shared one) and assign the agents."""
    name = ds.workstream(case_id)
    existing = svc.ops.workstream_id(name)
    wsid = svc.ops.ensure_workstream(name, f"Dataset {ds.name} (inputs only)", {"kind": "dataset"})
    if existing is None:
        agents = list(ds.manifest.get("agents", []))
        probe = ds.manifest.get("probe_agent")
        if probe:
            agents.append(probe)
        for pid in _process_ids(ws, agents, svc.rows):
            svc.ops.assign_process(pid, [wsid])
    ids = ds.manifest.setdefault("workstream_ids", {})
    if ids.get(name) != wsid:
        ids[name] = wsid  # the platform needs ids; the runner only knows names
        _write_manifest(ws, ds)
    return name


def _write_manifest(ws: Path, ds: D.Dataset) -> None:
    p = D.dataset_dir(ws, ds.name) / "manifest.json"
    p.write_text(json.dumps(ds.manifest, indent=2) + "\n")


def _finish(
    ctx: Context,
    svc: be.Services,
    ws: Path,
    dataset: str,
    message: str,
    *,
    changed: list[str],
    publish_version: bool = True,
) -> dict[str, Any]:
    """Upload the manifest snapshot, mirror rows, commit. Returns the result data."""
    ds = D.load_dataset(ws, dataset)
    if publish_version:
        snap = (D.dataset_dir(ws, dataset) / "versions" / f"v{ds.version}.json").read_bytes()
        svc.store.put(D.manifest_doc_path(dataset, ds.version), snap)
    rows = D.to_rows(ws, dataset)
    be.mirror_rows(svc.rows, "bld_datasets", rows["bld_datasets"], "dataset_key")
    for r in rows["bld_dataset_versions"][-1:]:
        svc.rows.insert(_VERSION_ROWS, r)
    be.mirror_rows(
        svc.rows,
        "bld_dataset_cases",
        [r for r in rows["bld_dataset_cases"] if r["case_id"] in changed],
        "case_key",
    )
    done = be.commit_work(
        ws, message, [f"harness/datasets/{dataset}", "builder.json"], topic=f"dataset-{dataset}"
    )
    return {
        "branch": done.branch,
        "note": done.note,
        "dataset": dataset,
        "kind": ds.kind,
        "version": ds.version,
        "cases": len(ds.active_case_ids()),
        "changed": changed,
        "commit": done.sha,
    }


def _case_files(
    svc: be.Services,
    dataset: str,
    case_id: str,
    inputs: dict[str, bytes],
    expected: dict[str, bytes],
    prefix: str = "",
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Inputs go to the dataset workstream; expected values go only to ds-<dataset>."""
    ins = [D.put_input(svc.store, dataset, case_id, prefix + n, b) for n, b in inputs.items()]
    exp = [D.put_expected(svc.store, dataset, case_id, prefix + n, b) for n, b in expected.items()]
    return ins, exp


def _scorers(values: list[str] | None) -> list[Any]:
    out = list(values or [])
    for s in out:
        D.normalize_scorer(s)
    return out


# --------------------------------------------------------------------------- handlers


def _cmd_create(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    bws.load_builder_config(ws)
    if a.kind not in D.KINDS:
        raise UsageError(f"kind must be one of {D.KINDS}")
    if (D.dataset_dir(ws, a.name) / "manifest.json").exists():
        raise UsageError(f"dataset {a.name!r} already exists")
    if a.kind == "read_your_writes" and not (a.target_env and a.probe):
        raise UsageError("read_your_writes datasets need --target-env and --probe")
    target: str | dict[str, str] | None = None
    if a.target_env:
        pairs = [t.partition("=") for t in a.target_env]
        target = (
            a.target_env[0] if len(pairs) == 1 and not pairs[0][1] else {k: v for k, _, v in pairs}
        )
    scorers = _scorers(a.scorer)
    agents = list(a.agent or [])
    svc = be.get_services(ctx)
    _process_ids(ws, agents + ([a.probe] if a.probe else []), svc.rows)
    if ctx.dry_run_notice(f"create dataset {a.name} ({a.kind})"):
        return Result.done({"dry_run": True})
    svc.ops.store_id(D.expected_store(a.name))
    ds = D.create_dataset(
        ws,
        a.name,
        a.kind,
        client=a.client or "",
        layout=a.layout,
        run_mode=a.run_mode,
        target_env=target,
        probe_agent=a.probe,
        default_scorers=scorers,
    )
    ds.manifest["agents"] = agents
    _write_manifest(ws, ds)
    workstreams: list[str] = []
    if ds.manifest["layout"] == "shared":
        workstreams.append(_ensure_workstream(svc, ws, ds, ""))
    data = _finish(ctx, svc, ws, a.name, f"dataset {a.name}: create ({a.kind})", changed=[])
    data.update({"expected_store": D.expected_store(a.name), "workstreams": workstreams})
    return Result.done(
        data,
        f"Created dataset {a.name} v1 ({a.kind}); expected values go to {D.expected_store(a.name)}."
        + data["note"],
    )


def _cmd_add_case(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    ds = D.load_dataset(ws, a.dataset)
    if ds.manifest.get("archived"):
        raise RefusedError(f"dataset {a.dataset!r} is archived (frozen)")
    meta = ds.manifest["cases"].get(a.case_id)
    if meta and meta.get("retired_in") is None:
        raise UsageError(f"case {a.case_id!r} already exists; use update-case")
    inputs = _read_files(a.input)
    expected = {
        **_read_files(a.expected),
        **_labels(ctx),
        **_state(a.expect_state),
    }
    if not expected:
        raise RefusedError(
            "a case needs an expectation: --expected FILE, --label k=v or --expect-state FILE"
        )
    scorers = _scorers(a.scorer)
    if ctx.dry_run_notice(f"add case {a.case_id} to {a.dataset}"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    wsname = _ensure_workstream(svc, ws, ds, a.case_id)
    ins, exp = _case_files(svc, a.dataset, a.case_id, inputs, expected)
    case: dict[str, Any] = {
        "case_id": a.case_id,
        "inputs": {"files": ins, **({"message": a.message} if a.message else {})},
        "setup": _load_spec(a.setup),
        "expected": exp,
        "teardown": _load_spec(a.teardown),
        "source": a.source,
        "tags": a.tag or [],
    }
    if scorers:
        case["scorers"] = scorers
    D.add_case(ws, a.dataset, case, f"added {a.case_id}")
    data = _finish(
        ctx, svc, ws, a.dataset, f"dataset {a.dataset}: add case {a.case_id}", changed=[a.case_id]
    )
    data["workstream"] = wsname
    return Result.done(
        data,
        f"Added {a.case_id} to {a.dataset}: now v{data['version']}." + data["note"],
    )


def _read_templates(path: str) -> tuple[list[dict[str, Any]], Any]:
    p = Path(path)
    if not p.is_file():
        raise UsageError(f"file not found: {p}")
    raw = yaml.safe_load(p.read_text())
    if isinstance(raw, list):
        return raw, None
    if isinstance(raw, dict) and isinstance(raw.get("templates"), list):
        return raw["templates"], raw.get("clients")
    raise UsageError(f"{p} must be a list of templates or {{templates, clients}}")


def _cmd_synth(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    ds = D.load_dataset(ws, a.dataset)
    if ds.kind not in ("labeled", "custom"):
        raise UsageError("synthetic cases are for labeled or custom datasets")
    templates, clients = _read_templates(a.templates)
    if a.clients:
        clients = yaml.safe_load(Path(a.clients).read_text())
    if not clients:
        clients = ds.manifest.get("known_clients")
    if not clients:
        raise UsageError("no known clients: put them in the templates file or pass --clients FILE")
    synthetic = D.synth(templates, a.count, clients, a.seed)
    if ctx.dry_run_notice(f"add {len(synthetic)} synthetic case(s) to {a.dataset}"):
        return Result.done({"dry_run": True, "count": len(synthetic)})
    svc = be.get_services(ctx)
    if ds.manifest["layout"] == "per_case":
        for s in synthetic:
            _ensure_workstream(svc, ws, ds, s.case_id)
    D.add_synth_cases(
        ws, a.dataset, synthetic, svc.store, f"synth {len(synthetic)} case(s) seed {a.seed}"
    )
    ids = [s.case_id for s in synthetic]
    data = _finish(
        ctx, svc, ws, a.dataset, f"dataset {a.dataset}: synth {len(ids)} case(s)", changed=ids
    )
    return Result.done(
        data,
        f"Added {len(ids)} synthetic case(s) to {a.dataset}: now v{data['version']}."
        + data["note"],
    )


def _cmd_show(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    if not a.dataset:
        items = []
        for name in D.list_datasets(ws):
            ds = D.load_dataset(ws, name)
            items.append(
                {
                    "dataset": name,
                    "kind": ds.kind,
                    "version": ds.version,
                    "cases": len(ds.active_case_ids()),
                    "archived": bool(ds.manifest.get("archived")),
                }
            )
        text = "\n".join(
            f"{i['dataset']}  {i['kind']}  v{i['version']}  {i['cases']} case(s)"
            + ("  archived" if i["archived"] else "")
            for i in items
        )
        return Result.done({"datasets": items}, text or "No datasets.")
    ds = D.load_dataset(ws, a.dataset)
    version = a.version or ds.version
    ids = ds.active_case_ids(version)
    if a.retired:
        ids = sorted(ds.manifest["cases"])
    cases = []
    for c in (
        ds.active_cases(version, source=a.source, tag=a.tag)
        if not a.retired
        else [ds.cases[i] for i in ids if i in ds.cases]
    ):
        if a.retired and (
            (a.source and c["source"] != a.source) or (a.tag and a.tag not in c["tags"])
        ):
            continue
        meta = ds.manifest["cases"][c["case_id"]]
        cases.append(
            {
                "case_id": c["case_id"],
                "source": c["source"],
                "tags": c["tags"],
                "workstream": ds.workstream(c["case_id"]),
                "expected": c["expected"],
                "added_in": meta["added_in"],
                "retired_in": meta.get("retired_in"),
            }
        )
    data = {
        "dataset": ds.name,
        "kind": ds.kind,
        "version": version,
        "layout": ds.manifest["layout"],
        "run_mode": ds.manifest["run_mode"],
        "archived": bool(ds.manifest.get("archived")),
        "history": ds.manifest["history"],
        "cases": cases,
    }
    lines = [f"{ds.name}: {ds.kind}, v{version}, {len(cases)} case(s)"]
    lines += [
        f"  {c['case_id']}  {c['source']}  [{', '.join(c['tags'])}]"
        + (f"  retired in v{c['retired_in']}" if c["retired_in"] else "")
        for c in cases
    ]
    return Result.done(data, "\n".join(lines))


def _cmd_update_case(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    refuse_self_asserted_in_cloud(ws, approved_by=a.approved_by)
    ds = D.load_dataset(ws, a.dataset)
    if ds.manifest.get("archived"):
        raise RefusedError(f"dataset {a.dataset!r} is archived (frozen)")
    meta = ds.manifest["cases"].get(a.case_id)
    if not meta or meta.get("retired_in") is not None or a.case_id not in ds.cases:
        raise UsageError(f"no active case {a.case_id!r}")
    expected = {**_read_files(a.expected), **_labels(ctx), **_state(a.expect_state)}
    inputs = _read_files(a.input)
    scorers = _scorers(a.scorer)
    loosens = bool(expected or scorers or a.loosens)
    if loosens and not (a.approved_by and a.approved_by.strip()):
        raise RefusedError(
            "changing an expectation or scorers can loosen a gate; "
            "pass --approved-by with the lead's name"
        )
    if not (expected or inputs or scorers or a.tag or a.source or a.message):
        raise UsageError("nothing to update")
    if ctx.dry_run_notice(f"update case {a.case_id} in {a.dataset}"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    case = json.loads(json.dumps(ds.cases[a.case_id]))
    prefix = f"v{ds.version + 1}/"  # new path: files are never overwritten
    ins, exp = _case_files(svc, a.dataset, a.case_id, inputs, expected, prefix)
    if ins:
        case["inputs"]["files"] = ins
    if a.message:
        case["inputs"]["message"] = a.message
    if exp:
        case["expected"] = exp
    if scorers:
        case["scorers"] = scorers
    if a.source:
        case["source"] = a.source
    if a.tag:
        case["tags"] = sorted(set(case["tags"]) | set(a.tag))
    D.update_case(
        ws,
        a.dataset,
        case,
        a.note or f"updated {a.case_id}",
        loosens_gate=loosens,
        approved_by=a.approved_by,
    )
    data = _finish(
        ctx,
        svc,
        ws,
        a.dataset,
        f"dataset {a.dataset}: update case {a.case_id}",
        changed=[a.case_id],
    )
    data["approved_by"] = a.approved_by
    return Result.done(
        data,
        f"Updated {a.case_id}: {a.dataset} is now v{data['version']}." + data["note"],
    )


def _cmd_retire_case(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    ds = D.load_dataset(ws, a.dataset)
    meta = ds.manifest["cases"].get(a.case_id)
    if not meta or meta.get("retired_in") is not None:
        raise UsageError(f"no active case {a.case_id!r}")
    if not ctx.yes:
        raise RefusedError("retiring a case needs --yes (files are kept)")
    if ctx.dry_run_notice(f"retire case {a.case_id} from {a.dataset}"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    D.retire_case(ws, a.dataset, a.case_id, a.note or f"retired {a.case_id}")
    data = _finish(
        ctx,
        svc,
        ws,
        a.dataset,
        f"dataset {a.dataset}: retire case {a.case_id}",
        changed=[a.case_id],
    )
    return Result.done(
        data,
        f"Retired {a.case_id}: {a.dataset} is now v{data['version']}." + data["note"],
    )


def _cmd_archive(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    ds = D.load_dataset(ws, a.dataset)
    refs = be.criteria_referencing(ws, a.dataset)
    if refs:
        raise RefusedError(f"dataset {a.dataset!r} is referenced by criteria: {refs}")
    if not ctx.yes:
        raise RefusedError("archiving a dataset needs --yes")
    if ctx.dry_run_notice(f"archive dataset {a.dataset}"):
        return Result.done({"dry_run": True})
    svc = be.get_services(ctx)
    names = (
        [ds.workstream("")]
        if ds.manifest["layout"] == "shared"
        else [ds.workstream(c) for c in ds.manifest["cases"]]
    )
    for n in names:
        svc.ops.archive_workstream(n)
    D.archive_dataset(ws, a.dataset, referenced_by=refs)
    data = _finish(
        ctx, svc, ws, a.dataset, f"dataset {a.dataset}: archive", changed=[], publish_version=False
    )
    data["archived_workstreams"] = names
    return Result.done(data, f"Archived {a.dataset} (frozen at v{ds.version})." + data["note"])


# --------------------------------------------------------------------------- registration


def register(registry: Registry) -> None:
    g = registry.group("dataset", "Datasets: what correct means for an agent")

    p = g.command("create", _cmd_create, help="Create a dataset (v1)", writes=True)
    p.add_argument("name")
    p.add_argument("--kind", required=True, choices=list(D.KINDS))
    p.add_argument("--client", default="")
    p.add_argument(
        "--target-env", action="append", help="read_your_writes: NAME or key=value (repeat)"
    )
    p.add_argument("--probe", help="read_your_writes: the probe agent")
    p.add_argument("--scorer", action="append", help="Default scorer (repeat)")
    p.add_argument("--layout", choices=list(D.LAYOUTS))
    p.add_argument("--run-mode", choices=list(D.RUN_MODES))
    p.add_argument("--agent", action="append", help="Agent whose staged process runs this dataset")

    p = g.command("add-case", _cmd_add_case, help="Add a case (new version)", writes=True)
    p.add_argument("dataset")
    p.add_argument("case_id")
    p.add_argument("--input", action="append", help="Input file (repeat)")
    p.add_argument("--expected", action="append", help="Expected file (repeat)")
    p.add_argument("--label", action="append", help="Expected label key=value (repeat)")
    p.add_argument("--expect-state", help="Expected state file (read_your_writes)")
    p.add_argument("--message", help="Instruction text for the case")
    p.add_argument("--setup", help="YAML/JSON setup file")
    p.add_argument("--teardown", help="YAML/JSON teardown file")
    p.add_argument("--scorer", action="append")
    p.add_argument("--source", default="known", choices=list(D.SOURCES))
    p.add_argument("--tag", action="append")

    p = g.command("synth", _cmd_synth, help="Generate synthetic labelled cases", writes=True)
    p.add_argument("dataset")
    p.add_argument("--from", dest="templates", required=True, help="Templates file (yaml/json)")
    p.add_argument("--count", type=int, required=True)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--clients", help="Known clients file (name list or name: {workstream})")

    p = g.command("show", _cmd_show, help="List datasets, or the cases of one")
    p.add_argument("dataset", nargs="?")
    p.add_argument("--source", choices=list(D.SOURCES))
    p.add_argument("--tag")
    p.add_argument("--version", type=int)
    p.add_argument("--retired", action="store_true", help="Include retired cases")

    p = g.command("update-case", _cmd_update_case, help="Correct a case (new version)", writes=True)
    p.add_argument("dataset")
    p.add_argument("case_id")
    p.add_argument("--input", action="append")
    p.add_argument("--expected", action="append")
    p.add_argument("--label", action="append")
    p.add_argument("--expect-state")
    p.add_argument("--message")
    p.add_argument("--scorer", action="append")
    p.add_argument("--source", choices=list(D.SOURCES))
    p.add_argument("--tag", action="append")
    p.add_argument("--note")
    p.add_argument("--loosens", action="store_true", help="This change loosens a gate")
    p.add_argument("--approved-by", help="Lead who approved a loosening change")

    p = g.command("retire-case", _cmd_retire_case, help="Retire a case (files kept)", writes=True)
    p.add_argument("dataset")
    p.add_argument("case_id")
    p.add_argument("--note")

    p = g.command("archive", _cmd_archive, help="Freeze a dataset", writes=True)
    p.add_argument("dataset")
