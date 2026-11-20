"""Datasets: manifest.json + cases/<case_id>.yaml in the workspace (PLAN.md section 10).

Layout (``ws`` is ``workspaces/<env>/``)::

    harness/datasets/<dataset>/manifest.json      current manifest (kind, layout, run mode, ...)
    harness/datasets/<dataset>/versions/v<n>.json frozen manifest snapshot per version
    harness/datasets/<dataset>/cases/<case_id>.yaml

Hard rule: no answer ever sits in the workspace or in a table. A case references its
expected output only by Doc Store path + sha256, and that path must live in the
``ds-<dataset>`` store (linked only to builder-ops), never in the dataset workstream.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.evals.protocols import ArtifactStore, sha256_hex

KINDS = ("work_product", "labeled", "read_your_writes", "custom")
SOURCES = ("real", "known", "synthetic", "production")
LAYOUTS = ("per_case", "shared")
RUN_MODES = ("per_case", "batch", "sequential")
SCORER_NAMES = (
    "field_match",
    "rubric",
    "label_match",
    "boundary_match",
    "outcome_match",
    "state_match",
    "collateral",
    "goal_reached",
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# Row fields that may ever carry case data into a table (answers are never among them).
CASE_ROW_FIELDS = (
    "case_key",
    "dataset",
    "case_id",
    "kind",
    "workstream",
    "inputs",
    "setup",
    "expected_refs",
    "teardown",
    "scorers",
    "source",
    "tags",
    "added_in_version",
    "retired_in_version",
)
_FORBIDDEN_EXPECTED_KEYS = {
    "value",
    "values",
    "label",
    "labels",
    "state",
    "content",
    "data",
    "text",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def datasets_dir(ws: Path) -> Path:
    return ws / "harness" / "datasets"


def dataset_dir(ws: Path, dataset: str) -> Path:
    _check_id(dataset, "dataset")
    return datasets_dir(ws) / dataset


def expected_store(dataset: str) -> str:
    return f"ds-{dataset}"


def expected_path(dataset: str, case_id: str, name: str) -> str:
    return f"{expected_store(dataset)}/expected/{case_id}/{name}"


def input_path(dataset: str, case_id: str, name: str) -> str:
    return f"ds:{dataset}/inputs/{case_id}/{name}"


def manifest_doc_path(dataset: str, version: int) -> str:
    return f"{expected_store(dataset)}/manifests/v{version}.json"


def case_workstream(dataset: str, layout: str, case_id: str) -> str:
    return f"ds:{dataset}:{case_id}" if layout == "per_case" else f"ds:{dataset}"


def _check_id(value: str, what: str) -> None:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise UsageError(f"invalid {what} id {value!r} (letters, digits, '_', '-', '.')")


# --------------------------------------------------------------------------- case validation


def validate_ref(ref: Any, *, dataset: str | None = None, expected: bool = False) -> dict[str, str]:
    """A file reference is exactly {path, sha256}. Anything else (a value, a label) is refused."""
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise RefusedError(
            "expected/input files are referenced by exactly {path, sha256}; "
            f"never by value (got {sorted(ref) if isinstance(ref, dict) else type(ref).__name__})"
        )
    path, sha = ref["path"], ref["sha256"]
    if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
        raise RefusedError(f"bad Doc Store path {path!r}")
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise RefusedError(f"bad sha256 for {path!r}")
    if expected and dataset is not None and not path.startswith(f"{expected_store(dataset)}/"):
        raise RefusedError(
            f"expected path {path!r} must live in the {expected_store(dataset)} store "
            "(never in the dataset workstream, which the agent under test can read)"
        )
    return {"path": path, "sha256": sha}


def normalize_scorer(spec: Any) -> tuple[str, dict[str, Any]]:
    """A scorer is 'name' or {name: ..., **config}. Returns (name, config)."""
    if isinstance(spec, str):
        name, cfg = spec, {}
    elif isinstance(spec, dict) and isinstance(spec.get("name"), str):
        name, cfg = spec["name"], {k: v for k, v in spec.items() if k != "name"}
    else:
        raise UsageError(f"bad scorer spec {spec!r}")
    if name.startswith("script:"):
        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", name[7:]):
            raise UsageError(f"bad script scorer name {name!r}")
    elif name not in SCORER_NAMES:
        raise UsageError(
            f"unknown scorer {name!r}; known: {', '.join(SCORER_NAMES)}, script:<name>"
        )
    return name, cfg


def validate_case(case: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    dataset = manifest["dataset"]
    _check_id(case.get("case_id", ""), "case")
    out: dict[str, Any] = {"case_id": case["case_id"]}
    inputs = case.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise UsageError("inputs must be a mapping")
    files = [validate_ref(r) for r in inputs.get("files", [])]
    out["inputs"] = {**{k: v for k, v in inputs.items() if k != "files"}, "files": files}
    out["setup"] = case.get("setup") or {}
    exp = case.get("expected")
    if exp is None:
        raise RefusedError(f"case {case['case_id']}: expected is required (path + sha256 refs)")
    exp_list = exp if isinstance(exp, list) else [exp]
    out["expected"] = [validate_ref(r, dataset=dataset, expected=True) for r in exp_list]
    out["teardown"] = case.get("teardown") or {}
    scorers = case.get("scorers") or manifest.get("default_scorers") or []
    for s in scorers:
        normalize_scorer(s)
    out["scorers"] = list(scorers)
    source = case.get("source", "known")
    if source not in SOURCES:
        raise UsageError(f"source must be one of {SOURCES}")
    out["source"] = source
    tags = case.get("tags") or []
    out["tags"] = sorted({str(t) for t in tags} | {source})
    return out


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


# --------------------------------------------------------------------------- loaded dataset


@dataclass
class Dataset:
    ws: Path
    manifest: dict[str, Any]
    cases: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return str(self.manifest["dataset"])

    @property
    def kind(self) -> str:
        return str(self.manifest["kind"])

    @property
    def version(self) -> int:
        return int(self.manifest["version"])

    def workstream(self, case_id: str) -> str:
        return case_workstream(self.name, self.manifest["layout"], case_id)

    def active_case_ids(self, version: int | None = None) -> list[str]:
        v = version or self.version
        out = []
        for cid, meta in sorted(self.manifest["cases"].items()):
            retired = meta.get("retired_in")
            if meta["added_in"] <= v and (retired is None or retired > v):
                out.append(cid)
        return out

    def active_cases(
        self, version: int | None = None, *, source: str | None = None, tag: str | None = None
    ) -> list[dict[str, Any]]:
        res = [self.cases[c] for c in self.active_case_ids(version) if c in self.cases]
        if source:
            res = [c for c in res if c["source"] == source]
        if tag:
            res = [c for c in res if tag in c["tags"]]
        return res


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n")


def _write_case(ws: Path, dataset: str, case: dict[str, Any]) -> str:
    p = dataset_dir(ws, dataset) / "cases" / f"{case['case_id']}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(case, sort_keys=False)
    p.write_text(text)
    return sha256_hex(text.encode())


def load_dataset(ws: Path, dataset: str) -> Dataset:
    d = dataset_dir(ws, dataset)
    mp = d / "manifest.json"
    if not mp.exists():
        raise UsageError(f"dataset {dataset!r} not found at {mp}")
    manifest = json.loads(mp.read_text())
    cases: dict[str, dict[str, Any]] = {}
    for cid in manifest["cases"]:
        cp = d / "cases" / f"{cid}.yaml"
        if cp.exists():
            cases[cid] = yaml.safe_load(cp.read_text())
    return Dataset(ws, manifest, cases)


def recorded_workstream_ids(ws: Path) -> dict[str, str]:
    """Workstream name -> platform id, from builder.json and every dataset manifest.

    The runner names workstreams (``ds:<dataset>:<case>``); the BFF wants ids. Manifest records win
    over builder.json for the dataset's own workstreams.
    """
    from cli.builder_common import workspace as bws

    out: dict[str, str] = {}
    try:
        out.update({str(k): str(v) for k, v in bws.load_builder_config(ws)["workstreams"].items()})
    except UsageError:
        pass
    for name in list_datasets(ws):
        recorded = load_dataset(ws, name).manifest.get("workstream_ids") or {}
        out.update({str(k): str(v) for k, v in recorded.items()})
    return out


def list_datasets(ws: Path) -> list[str]:
    root = datasets_dir(ws)
    return (
        sorted(p.name for p in root.iterdir() if (p / "manifest.json").exists())
        if root.exists()
        else []
    )


def _bump(ds: Dataset, note: str) -> None:
    m = ds.manifest
    m["version"] = int(m["version"]) + 1
    _finalize_version(ds, note)


def _finalize_version(ds: Dataset, note: str) -> None:
    m = ds.manifest
    v = int(m["version"])
    active = ds.active_case_ids(v)
    m.setdefault("history", []).append(
        {"version": v, "change_note": note, "case_count": len(active), "at": _now()}
    )
    snap = {
        "dataset": ds.name,
        "version": v,
        "kind": m["kind"],
        "case_hashes": {c: m["cases"][c]["sha256"] for c in active},
        "change_note": note,
    }
    d = dataset_dir(ds.ws, ds.name)
    _write_json(d / "versions" / f"v{v}.json", snap)
    _write_json(d / "manifest.json", m)


# --------------------------------------------------------------------------- CRUD


def create_dataset(
    ws: Path,
    name: str,
    kind: str,
    *,
    client: str = "",
    layout: str | None = None,
    run_mode: str | None = None,
    target_env: str | dict[str, str] | None = None,
    probe_agent: str | None = None,
    default_scorers: list[Any] | None = None,
) -> Dataset:
    _check_id(name, "dataset")
    if kind not in KINDS:
        raise UsageError(f"kind must be one of {KINDS}")
    if (dataset_dir(ws, name) / "manifest.json").exists():
        raise UsageError(f"dataset {name!r} already exists")
    if kind == "read_your_writes" and not (target_env and probe_agent):
        raise UsageError("read_your_writes datasets need --target-env and --probe")
    layout = layout or {"work_product": "per_case"}.get(kind, "shared")
    run_mode = (
        run_mode
        or {
            "work_product": "per_case",
            "labeled": "batch",
            "read_your_writes": "sequential",
            "custom": "per_case",
        }[kind]
    )
    if layout not in LAYOUTS or run_mode not in RUN_MODES:
        raise UsageError(f"layout in {LAYOUTS}, run_mode in {RUN_MODES}")
    scorers = default_scorers or []
    for s in scorers:
        normalize_scorer(s)
    manifest = {
        "dataset": name,
        "kind": kind,
        "client": client,
        "layout": layout,
        "run_mode": run_mode,
        "target_env": target_env,
        "probe_agent": probe_agent,
        "default_scorers": scorers,
        "version": 1,
        "archived": False,
        "cases": {},
        "history": [],
    }
    ds = Dataset(ws, manifest)
    _finalize_version(ds, "created")
    return ds


def _add(ds: Dataset, case: dict[str, Any]) -> None:
    valid = validate_case(case, ds.manifest)
    cid = valid["case_id"]
    if cid in ds.manifest["cases"] and ds.manifest["cases"][cid].get("retired_in") is None:
        raise UsageError(f"case {cid!r} already exists; use update_case")
    sha = _write_case(ds.ws, ds.name, valid)
    ds.cases[cid] = valid
    ds.manifest["cases"][cid] = {
        "added_in": int(ds.manifest["version"]) + 1,
        "retired_in": None,
        "sha256": sha,
    }


def add_cases(ws: Path, dataset: str, cases: list[dict[str, Any]], note: str = "") -> Dataset:
    """Add one or more cases as ONE new version."""
    ds = load_dataset(ws, dataset)
    _frozen_check(ds)
    for c in cases:
        _add(ds, c)
    _bump(ds, note or f"added {len(cases)} case(s)")
    return ds


def add_case(ws: Path, dataset: str, case: dict[str, Any], note: str = "") -> Dataset:
    return add_cases(ws, dataset, [case], note or f"added {case.get('case_id')}")


def _frozen_check(ds: Dataset) -> None:
    if ds.manifest.get("archived"):
        raise RefusedError(f"dataset {ds.name!r} is archived (frozen)")


def update_case(
    ws: Path,
    dataset: str,
    case: dict[str, Any],
    note: str = "",
    *,
    loosens_gate: bool = False,
    approved_by: str | None = None,
) -> Dataset:
    """Replace a case (new expected path/hash etc.). Loosening a gate needs a lead's approval."""
    ds = load_dataset(ws, dataset)
    _frozen_check(ds)
    cid = case.get("case_id", "")
    if cid not in ds.cases or ds.manifest["cases"][cid].get("retired_in") is not None:
        raise UsageError(f"no active case {cid!r}")
    if loosens_gate and not (approved_by and approved_by.strip()):
        raise RefusedError("this update loosens a gate; a lead's recorded approval is required")
    valid = validate_case(case, ds.manifest)
    sha = _write_case(ws, dataset, valid)
    ds.cases[cid] = valid
    ds.manifest["cases"][cid]["sha256"] = sha
    ds.manifest["cases"][cid]["updated_in"] = int(ds.manifest["version"]) + 1
    n = note or f"updated {cid}"
    if approved_by:
        n += f" (approved by {approved_by})"
    _bump(ds, n)
    return ds


def retire_case(ws: Path, dataset: str, case_id: str, note: str = "") -> Dataset:
    ds = load_dataset(ws, dataset)
    _frozen_check(ds)
    meta = ds.manifest["cases"].get(case_id)
    if not meta or meta.get("retired_in") is not None:
        raise UsageError(f"no active case {case_id!r}")
    meta["retired_in"] = int(ds.manifest["version"]) + 1  # files are kept
    _bump(ds, note or f"retired {case_id}")
    return ds


def archive_dataset(ws: Path, dataset: str, referenced_by: list[str] | None = None) -> Dataset:
    """Freeze. Refused while any release criteria reference the dataset."""
    ds = load_dataset(ws, dataset)
    if referenced_by:
        raise RefusedError(f"dataset {dataset!r} is referenced by criteria: {referenced_by}")
    ds.manifest["archived"] = True
    _write_json(dataset_dir(ws, dataset) / "manifest.json", ds.manifest)
    return ds


def put_expected(
    store: ArtifactStore, dataset: str, case_id: str, name: str, data: bytes
) -> dict[str, str]:
    """Upload an expected file/labels/state to the ds- store and return its {path, sha256} ref."""
    path = expected_path(dataset, case_id, name)
    return {"path": path, "sha256": store.put(path, data)}


def put_input(
    store: ArtifactStore, dataset: str, case_id: str, name: str, data: bytes
) -> dict[str, str]:
    path = input_path(dataset, case_id, name)
    return {"path": path, "sha256": store.put(path, data)}


# --------------------------------------------------------------------------- synthetic cases


@dataclass
class SynthCase:
    case_id: str
    inputs: dict[str, bytes]
    expected: dict[str, bytes]
    tags: list[str]


def synth(
    templates: list[dict[str, Any]],
    count: int,
    known_clients: dict[str, dict[str, Any]] | list[str],
    seed: int = 0,
    *,
    id_prefix: str = "syn",
) -> list[SynthCase]:
    """Deterministic synthetic labelled cases.

    A template is ``{"id": str, "subject": str, "body": str, "doc_types": [..],
    "attachments": [str], "pages": int}``; ``{client}``, ``{n}`` and ``{amount}`` are
    substituted. The client is picked from ``known_clients`` so the expected label is
    known up front. Same (templates, count, clients, seed) gives the same cases.
    """
    if not templates or count < 0:
        raise UsageError("need at least one template and a non-negative count")
    clients = sorted(known_clients)
    if not clients:
        raise UsageError("known_clients is empty: mappings must be known up front")
    rng = random.Random(seed)
    out: list[SynthCase] = []
    for i in range(count):
        t = templates[rng.randrange(len(templates))]
        client = clients[rng.randrange(len(clients))]
        fill = {
            "client": client,
            "n": rng.randrange(1000, 9999),
            "amount": rng.randrange(100, 9000),
        }
        item = {
            "subject": str(t.get("subject", "")).format(**fill),
            "body": str(t.get("body", "")).format(**fill),
            "attachments": [str(a).format(**fill) for a in t.get("attachments", [])],
        }
        pages = int(t.get("pages", 1))
        split = sorted(
            {1, *(rng.randrange(1, pages + 1) for _ in range(int(t.get("docs", 1)) - 1))}
        )
        meta = known_clients[client] if isinstance(known_clients, dict) else {}
        label = {
            "label": meta.get("workstream", client),
            "client": client,
            "doc_types": list(t.get("doc_types", [])),
            "boundaries": split,
        }
        cid = f"{id_prefix}-{seed}-{i:04d}-" + hashlib.sha1(_canon(item)).hexdigest()[:6]
        out.append(
            SynthCase(
                cid,
                {"item.json": json.dumps(item, indent=2).encode()},
                {"labels.json": json.dumps(label, indent=2).encode()},
                ["synthetic", str(t.get("id", "template"))],
            )
        )
    return out


def add_synth_cases(
    ws: Path,
    dataset: str,
    synthetic: list[SynthCase],
    store: ArtifactStore,
    note: str = "",
) -> Dataset:
    """Upload synthetic inputs/expected to the stores and add the cases as one new version."""
    ds = load_dataset(ws, dataset)
    cases = []
    for s in synthetic:
        cases.append(
            {
                "case_id": s.case_id,
                "inputs": {
                    "files": [
                        put_input(store, dataset, s.case_id, n, b) for n, b in s.inputs.items()
                    ]
                },
                "expected": [
                    put_expected(store, dataset, s.case_id, n, b) for n, b in s.expected.items()
                ],
                "source": "synthetic",
                "tags": s.tags,
            }
        )
    return add_cases(ws, ds.name, cases, note or f"synth {len(cases)} case(s)")


# --------------------------------------------------------------------------- rows


def assert_row_clean(row: dict[str, Any]) -> None:
    """Structural guard: expected_refs hold only {path, sha256}; no unknown fields."""
    extra = set(row) - set(CASE_ROW_FIELDS)
    if extra:
        raise RefusedError(f"case row has fields outside the allow-list: {sorted(extra)}")
    for ref in row.get("expected_refs", []):
        validate_ref(ref)
        if set(ref) & _FORBIDDEN_EXPECTED_KEYS:
            raise RefusedError("expected values must never be in a row")


_LAYOUT_COLUMN = {"per_case": "workstream_per_case", "shared": "shared_workstream"}


def _scorer_names(scorers: list[Any]) -> list[str]:
    return [s if isinstance(s, str) else str(s.get("name")) for s in scorers]


def _expected_columns(refs: list[dict[str, str]]) -> dict[str, str]:
    """``expected_path`` and ``expected_sha256`` for a case's expected refs (paths and hashes)."""
    if not refs:
        return {}
    if len(refs) == 1:
        return {"expected_path": refs[0]["path"], "expected_sha256": refs[0]["sha256"]}
    common = os.path.commonprefix([r["path"].split("/") for r in refs])
    digest = hashlib.sha256(
        json.dumps(sorted((r["path"], r["sha256"]) for r in refs)).encode()
    ).hexdigest()
    return {"expected_path": "/".join(common) + "/", "expected_sha256": digest}


def to_rows(ws: Path, dataset: str, version: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """Rows for bld_datasets, bld_dataset_versions and bld_dataset_cases. Paths + hashes only."""
    ds = load_dataset(ws, dataset)
    m = ds.manifest
    v = version or ds.version
    te = m.get("target_env")
    ds_row = {
        "dataset_key": ds.name,
        "name": ds.name,
        "current_version": ds.version,
        "expected_store_id": f"ds-{ds.name}",
        "kind": m["kind"],
        "layout": _LAYOUT_COLUMN[m["layout"]],
        "run_mode": m["run_mode"],
        "target_env": te if te is None or isinstance(te, str) else json.dumps(te),
        "probe_process_id": m.get("probe_agent"),
        "default_scorers": _scorer_names(m.get("default_scorers", [])),
    }
    ver_rows = [
        {
            "dataset_version_key": f"{ds.name}@{h['version']}",
            "dataset_key": ds.name,
            "version": h["version"],
            "case_count": h["case_count"],
            "change_note": h["change_note"],
            "manifest_path": manifest_doc_path(ds.name, h["version"]),
        }
        for h in m["history"]
    ]
    case_rows = []
    for cid, meta in sorted(m["cases"].items()):
        c = ds.cases.get(cid)
        if c is None or meta["added_in"] > v:
            continue
        row = {
            "case_key": f"{ds.name}:{cid}",
            "dataset": ds.name,
            "case_id": cid,
            "kind": m["kind"],
            "workstream": ds.workstream(cid),
            "inputs": c["inputs"],
            "setup": c["setup"],
            "expected_refs": c["expected"],
            "teardown": c["teardown"],
            "scorers": c["scorers"],
            "source": c["source"],
            "tags": c["tags"],
            "added_in_version": meta["added_in"],
            "retired_in_version": meta.get("retired_in"),
        }
        assert_row_clean(row)  # the case as the library holds it; the row below has no values
        case_rows.append(
            {
                "case_key": row["case_key"],
                "dataset_key": ds.name,
                "case_id": cid,
                "case_workstream_id": row["workstream"],
                "inputs": row["inputs"],
                "setup": row["setup"],
                "teardown": row["teardown"],
                "scorers": _scorer_names(row["scorers"]),
                "source": row["source"],
                "tags": row["tags"],
                "first_version": meta["added_in"],
                "retired_in_version": meta.get("retired_in"),
                **_expected_columns(row["expected_refs"]),
            }
        )
    return {
        "bld_datasets": [ds_row],
        "bld_dataset_versions": ver_rows,
        "bld_dataset_cases": case_rows,
    }
