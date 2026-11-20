"""Generic instructions (PLAN.md section 11): pages, the leak check, and the instruction skill.

An instruction is a generic rule distilled from answered gaps. It never names a case, never
quotes an expected value and never only makes sense for one case; `check_leaks` enforces that
and the compile step refuses anything it flags. Pages live in ``builder-context`` at
``kg/instructions/<agent>/<name>.md`` (with provenance); the workspace keeps only an index of
metadata and hashes at ``.builder/instructions/<agent>.json``.

Org and agent scope compile into ``harness/skills/<agent>-instructions/``. Client scope is
synced into ``_builder/instructions/`` of the client and dataset workstreams.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.evals.protocols import ArtifactStore, RowStore, sha256_hex
from cli.builder_common.evals.scorers import flatten

SCOPES = ("org", "client", "agent")
CONTEXT_STORE = "builder-context"
SYNC_DIR = "_builder/instructions"
ROW_FIELDS = (
    "instruction_key",
    "scope",
    "agent",
    "client",
    "page_path",
    "sha256",
    "gaps",
    "provenance",
    "leak_check",
    "status",
    "skill_version",
)
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_YEAR = range(1900, 2101)
_SINGLE_CASE = [
    re.compile(p, re.I)
    for p in (
        r"\b(only|just)\s+(for|in|applies to|true for)\s+(this|that|the)\s+"
        r"(case|file|email|document|return|record|instance|example)\b",
        r"\bin this (particular |specific )?(case|instance|example)\b",
        r"\bthis (particular|specific|one-off)\s+\w+",
        r"\bone-?off\b",
        r"\bthe (example|case) (above|below|here)\b",
    )
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def index_path(ws: Path, agent: str) -> Path:
    return ws / ".builder" / "instructions" / f"{agent}.json"


def load_index(ws: Path, agent: str) -> dict[str, dict[str, Any]]:
    p = index_path(ws, agent)
    data: dict[str, dict[str, Any]] = json.loads(p.read_text()) if p.exists() else {}
    return data


def _save_index(ws: Path, agent: str, idx: dict[str, dict[str, Any]]) -> None:
    p = index_path(ws, agent)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(idx, indent=2) + "\n")


def page_path(agent: str, name: str, revision: int = 1) -> str:
    leaf = f"{name}.md" if revision == 1 else f"{name}.r{revision}.md"
    return f"{CONTEXT_STORE}/kg/instructions/{agent}/{leaf}"


def render_page(record: dict[str, Any], body: str) -> str:
    front = {
        "instruction_key": record["instruction_key"],
        "scope": record["scope"],
        "agent": record["agent"],
        "client": record.get("client") or None,
        "gaps": record["gaps"],
        "provenance": record["provenance"],
    }
    return f"---\n{yaml.safe_dump(front, sort_keys=False)}---\n\n{body.strip()}\n"


def parse_page(text: str) -> tuple[dict[str, Any], str]:
    m = re.match(r"^---\n(.*?)\n---\n\n?(.*)$", text, re.S)
    if not m:
        raise UsageError("instruction page has no frontmatter")
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, m.group(2).strip()


#: Instruction record status to the ``bld_instructions.status`` enum.
_ROW_STATUS = {"draft": "proposed", "vetted": "vetted", "compiled": "vetted"}
_ROW_LEAK = {"clean": "pass", "flagged": "flagged"}


def instruction_row(record: dict[str, Any]) -> dict[str, Any]:
    """The ``bld_instructions`` columns of an instruction record."""
    return {
        "instruction_key": record.get("instruction_key"),
        "agent_key": record.get("agent"),
        "scope": record.get("scope"),
        "client_workstream_id": record.get("client") or None,
        "page_path": record.get("page_path"),
        "source_gaps": list(record.get("gaps") or []),
        "provenance": json.dumps(record.get("provenance")),
        "leak_check": _ROW_LEAK.get(str((record.get("leak_check") or {}).get("status"))),
        "status": _ROW_STATUS.get(str(record.get("status")), "proposed"),
        "compiled_into_version": record.get("skill_version"),
    }


def _mirror(rows: RowStore | None, record: dict[str, Any]) -> None:
    if rows is not None:
        rows.insert("bld_instructions", instruction_row(record))
        rows.update("bld_instructions", record["instruction_key"], instruction_row(record))


# --------------------------------------------------------------------------- pages


def save_instruction(
    ws: Path,
    store: ArtifactStore,
    *,
    agent: str,
    name: str,
    scope: str,
    body: str,
    gaps: list[str],
    provenance: list[dict[str, str]],
    client: str = "",
    rows: RowStore | None = None,
) -> dict[str, Any]:
    """Write a page (provenance required) and index it as a draft. Editing makes a new revision."""
    if scope not in SCOPES:
        raise UsageError(f"scope must be one of {SCOPES}")
    if not _NAME_RE.match(name):
        raise UsageError(f"bad instruction name {name!r}")
    if scope == "client" and not client:
        raise UsageError("a client-scope instruction needs the client")
    if not provenance or not all(p.get("source") for p in provenance):
        raise UsageError("an instruction needs provenance: where each rule came from")
    if not body.strip():
        raise UsageError("empty instruction")
    idx = load_index(ws, agent)
    key = f"{agent}:{name}"
    rev = int(idx[key]["revision"]) + 1 if key in idx else 1
    rec: dict[str, Any] = {
        "instruction_key": key,
        "agent": agent,
        "name": name,
        "scope": scope,
        "client": client,
        "revision": rev,
        "gaps": sorted(set(gaps)),
        "provenance": provenance,
        "leak_check": {"status": "unchecked", "findings": []},
        "status": "draft",
        "vetted_by": None,
        "skill_version": None,
    }
    rec["page_path"] = page_path(agent, name, rev)
    rec["sha256"] = store.put(rec["page_path"], render_page(rec, body).encode())
    idx[key] = rec
    _save_index(ws, agent, idx)
    _mirror(rows, rec)
    return rec


def load_body(store: ArtifactStore, rec: dict[str, Any]) -> str:
    blob = store.get(rec["page_path"])
    if sha256_hex(blob) != rec["sha256"]:
        raise RefusedError(f"instruction page {rec['page_path']} does not match its recorded hash")
    return parse_page(blob.decode())[1]


# --------------------------------------------------------------------------- leak check


def _is_year(v: Any) -> bool:
    return isinstance(v, int) and v in _YEAR


def expected_candidates(blob: bytes) -> list[tuple[str, str, Any]]:
    """(field, kind, value) triples worth looking for in an instruction. In memory only."""
    out: list[tuple[str, str, Any]] = []
    try:
        parsed = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        text = blob.decode("utf-8", errors="replace")
        return [
            (f"line {i + 1}", "str", ln.strip())
            for i, ln in enumerate(text.splitlines())
            if len(ln.strip()) >= 8
        ]
    for field, v in flatten(parsed).items():
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, str) and len(v.strip()) >= 4:
            out.append((field, "str", v.strip()))
        elif isinstance(v, int | float) and not _is_year(v):
            digits = re.sub(r"\D", "", repr(v))
            if len(digits) >= 4:
                out.append((field, "num", v))
    return out


def _num_in(body_nocomma: str, v: Any) -> bool:
    for form in {repr(v), f"{v:g}" if isinstance(v, float) else str(v)}:
        if re.search(rf"(?<![\d.]){re.escape(form)}(?!\d|\.\d)", body_nocomma):
            return True
    return False


def check_leaks(
    body: str,
    *,
    case_ids: Iterable[str],
    expected_refs: Iterable[dict[str, str]],
    store: ArtifactStore,
    input_names: Iterable[str] = (),
) -> dict[str, Any]:
    """Refuse-worthy leaks: a case id, a value from any expected output, single-case wording.

    Expected files are fetched from ``store`` at check time and never persisted. Findings give
    the location of a match (file and field), never the value itself.
    """
    findings: list[dict[str, str]] = []
    for cid in sorted(set(case_ids)):
        if re.search(rf"(?<![\w-]){re.escape(cid)}(?![\w-])", body, re.I):
            findings.append({"kind": "case_id", "detail": "names a case id"})
    nocomma = re.sub(r"(?<=\d),(?=\d{3})", "", body)
    low = body.casefold()
    for ref in expected_refs:
        blob = store.get(ref["path"])
        if sha256_hex(blob) != ref["sha256"]:
            raise RefusedError(f"expected file {ref['path']} does not match its recorded hash")
        for field, kind, v in expected_candidates(blob):
            hit = (
                _num_in(nocomma, v)
                if kind == "num"
                else re.search(rf"(?<!\w){re.escape(str(v).casefold())}(?!\w)", low) is not None
            )
            if hit:
                findings.append(
                    {
                        "kind": "expected_value",
                        "detail": f"quotes a value from {ref['path']} ({field})",
                    }
                )
    for pat in _SINGLE_CASE:
        if pat.search(body):
            findings.append({"kind": "single_case", "detail": f"wording like {pat.pattern[:40]!r}"})
    for n in sorted(set(input_names)):
        base = n.rsplit("/", 1)[-1]
        if len(base) >= 4 and base.casefold() in low:
            findings.append({"kind": "single_case", "detail": "names a case input file"})
    seen: set[tuple[str, str]] = set()
    uniq = []
    for f in findings:
        k = (f["kind"], f["detail"])
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return {"status": "flagged" if uniq else "clean", "findings": uniq, "checked_at": _now()}


def run_leak_check(
    ws: Path,
    store: ArtifactStore,
    agent: str,
    key: str,
    *,
    case_ids: Iterable[str],
    expected_refs: Iterable[dict[str, str]],
    input_names: Iterable[str] = (),
    rows: RowStore | None = None,
) -> dict[str, Any]:
    idx = load_index(ws, agent)
    if key not in idx:
        raise UsageError(f"no instruction {key!r}")
    rec = idx[key]
    rec["leak_check"] = check_leaks(
        load_body(store, rec),
        case_ids=case_ids,
        expected_refs=expected_refs,
        store=store,
        input_names=input_names,
    )
    if rec["leak_check"]["status"] == "flagged" and rec["status"] != "draft":
        rec["status"] = "draft"  # a flagged page is pulled back from vetted/compiled
    _save_index(ws, agent, idx)
    _mirror(rows, rec)
    return rec


def vet(ws: Path, agent: str, key: str, *, by: str, rows: RowStore | None = None) -> dict[str, Any]:
    """The FDE or SME vets a page; abcd refuses unless its leak check is clean."""
    idx = load_index(ws, agent)
    if key not in idx:
        raise UsageError(f"no instruction {key!r}")
    rec = idx[key]
    if rec["leak_check"]["status"] != "clean":
        raise RefusedError(f"{key}: leak check is {rec['leak_check']['status']}; refusing to vet")
    if not by.strip():
        raise UsageError("vetting needs who vetted it")
    rec["status"], rec["vetted_by"] = "vetted", by
    _save_index(ws, agent, idx)
    _mirror(rows, rec)
    return rec


# --------------------------------------------------------------------------- compile


def skill_dir(ws: Path, agent: str) -> Path:
    return ws / "harness" / "skills" / f"{agent}-instructions"


def _summary(body: str) -> str:
    for line in body.splitlines():
        t = line.strip().lstrip("#").strip()
        if t:
            return t[:160]
    return ""


def compile_instruction_skill(
    ws: Path,
    store: ArtifactStore,
    agent: str,
    *,
    case_ids: Iterable[str],
    expected_refs: Iterable[dict[str, str]],
    input_names: Iterable[str] = (),
    rows: RowStore | None = None,
) -> dict[str, Any]:
    """Build ``harness/skills/<agent>-instructions/`` from vetted org/agent-scope pages.

    Every included page is leak-checked again here; any flagged page refuses the compile.
    """
    # Loaded lazily: the harness CLI pulls in requests/dotenv, and this keeps mypy to our code.
    _lint_frontmatter = importlib.import_module("cli.harness_skill")._lint_frontmatter

    case_ids, expected_refs, input_names = list(case_ids), list(expected_refs), list(input_names)
    idx = load_index(ws, agent)
    chosen = [
        r
        for _, r in sorted(idx.items())
        if r["scope"] in ("org", "agent") and r["status"] in ("vetted", "compiled")
    ]
    skipped_client = sorted(k for k, r in idx.items() if r["scope"] == "client")
    if not chosen:
        raise UsageError(f"no vetted org/agent instructions for {agent!r} to compile")
    bodies: dict[str, str] = {}
    for r in chosen:
        body = load_body(store, r)
        leak = check_leaks(
            body,
            case_ids=case_ids,
            expected_refs=expected_refs,
            store=store,
            input_names=input_names,
        )
        r["leak_check"] = leak
        if leak["status"] != "clean":
            _save_index(ws, agent, idx)
            raise RefusedError(
                f"{r['instruction_key']}: leak check flagged "
                f"{[f['kind'] for f in leak['findings']]}; refusing to compile"
            )
        bodies[r["name"]] = body
    name = f"{agent}-instructions"
    front = yaml.safe_dump(
        {
            "name": name,
            "description": (
                f"Generic business rules and context for the {agent} agent. "
                "Read the index, then open the reference that matches the task."
            ),
        },
        sort_keys=False,
        width=1000,
    )
    index_lines = [f"- [{n}](references/{n}.md): {_summary(b)}" for n, b in sorted(bodies.items())]
    skill_md = (
        f"---\n{front}---\n\n# {agent} instructions\n\n"
        "Generic rules gathered during the build. Each reference lists where its rule came from.\n\n"
        + "\n".join(index_lines)
        + "\n"
    )
    refs: dict[str, str] = {}
    for r in chosen:
        prov = "\n".join(f"- {p.get('source', '')}: {p.get('ref', '')}" for p in r["provenance"])
        refs[f"references/{r['name']}.md"] = (
            f"{bodies[r['name']].strip()}\n\n## Provenance\n\n{prov}\n\n"
            f"Closes gaps: {', '.join(r['gaps']) or 'none'}\n"
        )
    sd = skill_dir(ws, agent)
    try:
        _lint_frontmatter(skill_md, sd / "SKILL.md")
    except ValueError as e:
        raise UsageError(str(e)) from e
    if sd.exists():
        shutil.rmtree(sd)
    (sd / "references").mkdir(parents=True)
    (sd / "SKILL.md").write_text(skill_md)
    for rel, text in refs.items():
        (sd / rel).write_text(text)
    h = hashlib.sha256()
    for rel in sorted(["SKILL.md", *refs]):
        h.update(rel.encode())
        h.update((sd / rel).read_bytes())
    version = h.hexdigest()[:12]
    for r in chosen:
        r["status"], r["skill_version"] = "compiled", version
        _mirror(rows, r)
    _save_index(ws, agent, idx)
    return {
        "skill_dir": str(sd),
        "skill_name": name,
        "skill_version": version,
        "included": [r["instruction_key"] for r in chosen],
        "client_scope_not_compiled": skipped_client,
    }


# --------------------------------------------------------------------------- client scope


def client_sync_plan(
    ws: Path, agent: str, client: str, workstreams: list[str]
) -> list[dict[str, str]]:
    """Where each vetted, clean client-scope page is copied: every listed workstream."""
    plan = []
    for _, r in sorted(load_index(ws, agent).items()):
        if r["scope"] != "client" or r.get("client") != client:
            continue
        if r["status"] not in ("vetted", "compiled") or r["leak_check"]["status"] != "clean":
            continue
        for wsid in sorted(set(workstreams)):
            plan.append(
                {
                    "workstream": wsid,
                    "target": f"{wsid}/{SYNC_DIR}/{r['name']}.md",
                    "source": r["page_path"],
                    "sha256": r["sha256"],
                    "instruction_key": r["instruction_key"],
                }
            )
    return plan


def apply_sync_plan(plan: list[dict[str, str]], store: ArtifactStore) -> list[str]:
    """Copy each planned page, verifying its hash, and return the target paths written."""
    written = []
    for item in plan:
        blob = store.get(item["source"])
        if sha256_hex(blob) != item["sha256"]:
            raise RefusedError(f"{item['source']} changed since the plan was made")
        store.put(item["target"], blob)
        written.append(item["target"])
    return written
