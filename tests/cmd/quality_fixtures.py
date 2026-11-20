"""Fixtures for the quality command tests: a git workspace, fake services, a CLI runner.

No network: the Doc Store, Data Store, platform operations and the harness are in-memory fakes
(``MemStore``/``FakeDriver`` from ``tests/builder/evals_fakes.py``, ``MemoryStore`` from
``tests/builder/fakes.py``). Rows go through the real ``SpecRows`` mapper and the table specs.
"""

from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Callable, Iterator
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from cli import abcd
from cli import builder_eval as be
from cli.builder_common import repo
from cli.builder_common import workspace as bws
from cli.builder_common.rowstore import OrgRowStore
from cli.core.context import WORKSPACES_ENV_VAR, apply_workspaces_override, set_workspaces_dir
from cli.wdl_common import workspace_manager as wm
from tests.builder.evals_fakes import (
    AGENT,
    PROCESS,
    Call,
    FakeDriver,
    MemStore,
    Reply,
    jbytes,
    write_prompts,
)
from tests.builder.fakes import MemoryStore

SENTINEL_ENTITY = "Zenith Orchard Partners"
SENTINEL_TAX = 48213.77
CLIENT = "acme"
PROBE = "acme-crm-probe"


class FakeOps:
    """In-memory ``PlatformOps``: records workstreams, store links and assignments."""

    def __init__(self) -> None:
        self.workstreams: dict[str, dict[str, Any]] = {}
        self.stores: dict[str, str | None] = {}
        self.assignments: dict[str, set[str]] = {}
        self.archived: list[str] = []

    def ensure_workstream(
        self, name: str, description: str = "", properties: dict[str, Any] | None = None
    ) -> str:
        if name not in self.workstreams:
            self.workstreams[name] = {"id": f"w-{len(self.workstreams) + 1}", "props": properties}
        return str(self.workstreams[name]["id"])

    def workstream_id(self, name: str) -> str | None:
        ws = self.workstreams.get(name)
        return str(ws["id"]) if ws else None

    def store_id(self, store_name: str) -> str:
        # ds-<dataset> stores are linked to builder-ops only; workstream stores to their workstream
        self.stores.setdefault(
            store_name, "builder-ops" if store_name.startswith("ds-") else store_name
        )
        return f"s-{store_name}"

    def assign_process(self, process_id: str, workstream_ids: list[str]) -> list[str]:
        self.assignments.setdefault(process_id, set()).update(workstream_ids)
        return sorted(self.assignments[process_id])

    def archive_workstream(self, name: str) -> None:
        self.archived.append(name)


@dataclass
class QEnv:
    ws: Path
    root: Path
    store: MemStore
    backend: MemoryStore
    ops: FakeOps
    services: be.Services
    answers: dict[str, Any] = field(default_factory=dict)
    polls: int = 0
    run_fn: Callable[..., tuple[int, dict[str, Any]]] | None = None

    def run(self, *argv: str) -> tuple[int, dict[str, Any]]:
        """Run ``abcd <argv> --env acme --json`` and return (exit code, the JSON document)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = abcd.main([*argv, "--env", CLIENT, "--json"])
        out = buf.getvalue().strip()
        doc = json.loads(out.splitlines()[-1]) if out else {}
        assert doc["exit_code"] == code
        return code, doc

    def ok(self, *argv: str) -> dict[str, Any]:
        code, doc = self.run(*argv)
        assert code == 0, doc
        return doc["data"]

    def file(self, name: str, content: bytes | str | dict[str, Any]) -> str:
        p = self.root / "files" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            content = json.dumps(content)
        p.write_bytes(content.encode() if isinstance(content, str) else content)
        return str(p)

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.backend.list_all(table)

    def git_history(self) -> str:
        return subprocess.run(
            ["git", "log", "-p", "--all", "--format=%H %s"],
            cwd=self.ws, capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip

    def commits(self) -> list[str]:
        out = subprocess.run(
            ["git", "log", "--format=%s"], cwd=self.ws, capture_output=True, text=True, check=True
        ).stdout
        return out.splitlines()

    def case_files(self, case: str, tax: float, entity: str = SENTINEL_ENTITY) -> tuple[str, str]:
        """An input file and an expected file for one return case."""
        return (
            self.file(f"{case}-in.txt", f"documents for {case}"),
            self.file(f"{case}-return.json", {"entity": entity, "total_tax": tax}),
        )

    def make_dataset(self, n: int = 3, name: str = "returns") -> None:
        self.ok("dataset", "create", name, "--kind", "work_product", "--client", CLIENT,
                "--scorer", "field_match", "--agent", AGENT)  # fmt: skip
        for i in range(1, n + 1):
            inp, exp = self.case_files(f"c{i}", SENTINEL_TAX + i)
            self.ok("dataset", "add-case", name, f"c{i}", "--input", inp, "--expected", exp)

    def handler(self, call: Call) -> Reply:
        case = call.workstream.rsplit(":", 1)[-1]
        if case in self.answers:
            obj = self.answers[case]
            return Reply(
                outputs={"return.json": jbytes(obj)} if obj is not None else {}, polls=self.polls
            )
        i = int(case[1:])
        return Reply(
            outputs={
                "return.json": jbytes({"entity": SENTINEL_ENTITY, "total_tax": SENTINEL_TAX + i})
            },
            polls=self.polls,
        )


@pytest.fixture
def qenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[QEnv]:
    original = wm.WORKSPACES_DIR
    root = tmp_path
    workspaces = root / "workspaces"
    ws = workspaces / CLIENT
    ws.mkdir(parents=True)
    (ws / "env.json").write_text(json.dumps({"env_id": CLIENT, "name": CLIENT}))
    monkeypatch.setenv(WORKSPACES_ENV_VAR, str(workspaces))
    apply_workspaces_override()
    bws.init_layout(ws, tenant=CLIENT)
    repo.init_repo(ws)
    for k, v in (("user.name", "Test"), ("user.email", "t@example.invalid")):
        subprocess.run(["git", "config", k, v], cwd=ws, check=True)
    bws.new_agent_skeleton(ws, AGENT)
    bws.new_agent_skeleton(ws, PROBE)
    bws.set_agent_process_ids(ws, AGENT, staged_process_id=PROCESS, live_process_id="live-1")
    bws.set_agent_process_ids(ws, PROBE, staged_process_id="probe-1")
    write_prompts(ws)
    repo.commit(ws, "init workspace")

    store, backend, ops = MemStore(), MemoryStore(), FakeOps()
    env = QEnv(ws, root, store, backend, ops, None)  # type: ignore[arg-type]
    driver = FakeDriver(env.handler)
    env.services = be.Services(store, OrgRowStore(backend), ops, driver)
    monkeypatch.setattr(be, "get_services", lambda ctx: env.services)
    monkeypatch.setattr(be, "sleep", lambda s: None)
    yield env
    monkeypatch.delenv(WORKSPACES_ENV_VAR, raising=False)
    set_workspaces_dir(original)
