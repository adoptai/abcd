"""Shared fakes and builders for the eval library tests (no network, no platform)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from cli.builder_common import datasets as D  # noqa: N812
from cli.builder_common.evals.protocols import (
    TURN_COMPLETED,
    TURN_RUNNING,
    TurnHandle,
    TurnStatus,
    sha256_hex,
)
from cli.builder_common.tables import validate_row

AGENT = "acme-tax"
PROBE = "probe-1"
PROCESS = "proc-1"

# Sentinels: values that must never reach a driver, a row, state.json or an instruction.
SENTINEL_ENTITY = "Zenith Orchard Partners"
SENTINEL_TAX = 48213.77
SENTINEL_NOTE = "NOTE-SENTINEL-the-sme-used-the-blue-binder"


class MemStore:
    """ArtifactStore that enforces the plan's never-overwrite rule."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.gets: list[str] = []

    def put(self, path: str, data: bytes) -> str:
        if path in self.files and self.files[path] != data and "/_builder/" not in path:
            raise AssertionError(f"overwrote {path}")
        self.files[path] = data
        return sha256_hex(data)

    def get(self, path: str) -> bytes:
        self.gets.append(path)
        return self.files[path]


class MemRows:
    KEYS = {
        "bld_eval_runs": "eval_run_id",
        "bld_eval_results": "result_key",
        "bld_metric_values": "value_key",
        "bld_context_gaps": "gap_key",
        "bld_instructions": "instruction_key",
    }

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict[str, Any]]] = {}

    def insert(self, table: str, row: dict[str, Any]) -> None:
        validate_row(table, row)  # every row the library writes must fit its column spec
        t = self.tables.setdefault(table, {})
        t.setdefault(str(row[self.KEYS[table]]), json.loads(json.dumps(row, default=str)))

    def update(self, table: str, key: str, fields: dict[str, Any]) -> None:
        validate_row(table, fields, partial=True)
        self.tables[table][key].update(json.loads(json.dumps(fields, default=str)))

    def query(self, table: str, where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rows = list(self.tables.get(table, {}).values())
        return [r for r in rows if all(r.get(k) == v for k, v in (where or {}).items())]

    def dump(self) -> str:
        return json.dumps(self.tables, sort_keys=True)


@dataclass
class Call:
    workstream: str
    process_id: str
    message: str
    conversation_id: str | None


@dataclass
class Reply:
    text: str = "done"
    outputs: dict[str, bytes] = field(default_factory=dict)
    state: str = TURN_COMPLETED
    question: str | None = None
    error: str | None = None
    polls: int = 0  # running polls before the terminal one
    cost_usd: float = 0.02
    duration_s: float = 3.0


def jbytes(obj: Any) -> bytes:
    return json.dumps(obj).encode()


class Platform:
    """The 'server': every turn ever started, shared by driver instances (a restart keeps it)."""

    def __init__(self, handler: Callable[[Call], Reply]) -> None:
        self.handler = handler
        self.calls: list[Call] = []
        self.turns: dict[str, tuple[Call, Reply]] = {}
        self.polls: dict[str, int] = {}
        self.stopped: list[str] = []


class FakeDriver:
    def __init__(self, platform: Platform | Callable[[Call], Reply]) -> None:
        self.platform = platform if isinstance(platform, Platform) else Platform(platform)

    @property
    def calls(self) -> list[Call]:
        return self.platform.calls

    def restarted(self) -> FakeDriver:
        """A fresh driver object (new process) against the same platform."""
        return FakeDriver(self.platform)

    def start_turn(
        self,
        *,
        workstream: str,
        process_id: str,
        message: str,
        conversation_id: str | None = None,
    ) -> TurnHandle:
        p = self.platform
        call = Call(workstream, process_id, message, conversation_id)
        p.calls.append(call)
        tid = f"t{len(p.calls)}"
        p.turns[tid] = (call, p.handler(call))
        return TurnHandle(tid, conversation_id or f"conv-{tid}", workstream)

    def turn_status(self, handle: TurnHandle) -> TurnStatus:
        p = self.platform
        _, rep = p.turns[handle.turn_id]
        n = p.polls.get(handle.turn_id, 0)
        p.polls[handle.turn_id] = n + 1
        if n < rep.polls:
            return TurnStatus(state=TURN_RUNNING)
        return TurnStatus(
            state=rep.state,
            final_text=rep.text,
            question=rep.question,
            error=rep.error,
            steps=4,
            tokens=1200,
            cost_usd=rep.cost_usd,
            duration_s=rep.duration_s,
        )

    def fetch_outputs(self, handle: TurnHandle) -> dict[str, bytes]:
        return dict(self.platform.turns[handle.turn_id][1].outputs)

    def fetch_transcript(self, handle: TurnHandle) -> str:
        return json.dumps({"turn": handle.turn_id}) + "\n"

    def stop_turn(self, handle: TurnHandle) -> None:
        self.platform.stopped.append(handle.turn_id)


# --------------------------------------------------------------------------- workspace builders


def write_prompts(ws: Path, agent: str = AGENT, text: str | None = None) -> None:
    p = ws / "harness" / "agents" / agent / "prompts.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        yaml.safe_dump(
            {
                "blind": text
                or "Prepare the return for this client from the files in this workstream"
            }
        )
    )


def write_check(ws: Path, check_id: str, **spec: Any) -> None:
    p = ws / "harness" / "checks" / f"{check_id}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"check_id": check_id, **spec}))


def add_case(
    ws: Path,
    store: MemStore,
    dataset: str,
    case_id: str,
    *,
    inputs: dict[str, bytes] | None = None,
    expected: dict[str, bytes] | None = None,
    scorers: list[Any] | None = None,
    source: str = "known",
    tags: list[str] | None = None,
    setup: dict[str, Any] | None = None,
    teardown: dict[str, Any] | None = None,
    message: str | None = None,
) -> D.Dataset:
    ins = inputs if inputs is not None else {"in.txt": b"input " + case_id.encode()}
    exp = expected if expected is not None else {"return.json": jbytes({"ok": case_id})}
    case: dict[str, Any] = {
        "case_id": case_id,
        "inputs": {"files": [D.put_input(store, dataset, case_id, n, b) for n, b in ins.items()]},
        "expected": [D.put_expected(store, dataset, case_id, n, b) for n, b in exp.items()],
        "source": source,
        "tags": tags or [],
    }
    if message:
        case["inputs"]["message"] = message
    if scorers:
        case["scorers"] = scorers
    if setup:
        case["setup"] = setup
    if teardown:
        case["teardown"] = teardown
    return D.add_case(ws, dataset, case)


def work_product_ws(tmp_path: Path, n: int = 3) -> tuple[Path, MemStore]:
    """Dataset 'returns': n cases, each expecting {'entity', 'total_tax'}."""
    ws, store = tmp_path / "ws", MemStore()
    write_prompts(ws)
    D.create_dataset(ws, "returns", "work_product", client="acme", default_scorers=["field_match"])
    for i in range(1, n + 1):
        add_case(
            ws,
            store,
            "returns",
            f"c{i}",
            expected={
                "return.json": jbytes({"entity": SENTINEL_ENTITY, "total_tax": SENTINEL_TAX + i})
            },
        )
    return ws, store


def correct_handler(call: Call) -> Reply:
    """Agent that reproduces every expected return (looked up by workstream)."""
    case = call.workstream.rsplit(":", 1)[-1]
    i = int(case[1:])
    return Reply(
        outputs={"return.json": jbytes({"entity": SENTINEL_ENTITY, "total_tax": SENTINEL_TAX + i})}
    )
