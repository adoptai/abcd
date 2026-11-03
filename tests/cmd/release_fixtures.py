"""Fakes and a workspace builder for the release-flow command tests (no network).

``Env`` wires one workspace (a git repo with a local bare remote), a fake platform, in-memory
rows and Doc Store, a fake eval driver and a fake GitHub into ``builder_agent.SERVICES_FACTORY``.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from cli import builder_agent, builder_release
from cli.abcd import main
from cli.builder_common import datasets as D  # noqa: N812
from cli.builder_common import repo as rp
from cli.builder_common import workspace as bws
from cli.builder_common.platform import PatIdentity, Transcript
from cli.harness_common.api_client import HarnessAPIError
from tests.builder.evals_fakes import (
    FakeDriver,
    MemStore,
    add_case,
    correct_handler,
    write_prompts,
)
from tests.builder.fakes import MemoryStore

AGENT = "acme-tax"
ENV = "acme"
SKILL_MD = (
    "---\nname: tax-calc\ndescription: Compute tax\nprocess:\n  steps: []\n---\nCompute it.\n"
)
WRITES = {
    "upload_skill",
    "upload_skill_first",
    "upload_plugin",
    "create_process",
    "update_process",
    "set_process_workstreams",
    "set_write_workstreams",
}


class FakeClient:
    """The platform: skills, plugins, processes, workstream assignments, conversations."""

    def __init__(self) -> None:
        self.org_skills: dict[str, dict[str, Any]] = {}
        self.default_skills: set[str] = set()
        self.plugins: dict[str, list[str]] = {}
        self.processes: dict[str, dict[str, Any]] = {}
        self.assigned: dict[str, list[str]] = {}
        self.grants: dict[str, dict[str, Any]] = {}
        self.conversations: dict[str, list[dict[str, Any]]] = {}
        self.pat_ids: set[str] = {"svc-pat"}  # who the PAT authenticates as
        self.shared: dict[str, bool] = {}  # conversation id -> is_shared (default True)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.uploads: list[tuple[str, bool]] = []  # (skill name, replace)
        self._n = 0

    def writes(self) -> list[str]:
        return [c for c, _ in self.calls if c in WRITES]

    def _log(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    @staticmethod
    def _err(method: str, status: int, body: str = "") -> HarnessAPIError:
        return HarnessAPIError(method, "fake", status, body)

    # skills
    def list_skills(self) -> dict[str, Any]:
        self._log("list_skills")
        items = [
            {"name": n, "source": "default", "description": ""}
            for n in sorted(self.default_skills - set(self.org_skills))
        ]
        items += [
            {"name": n, "source": "org", "description": "", "plugin": s.get("plugin")}
            for n, s in sorted(self.org_skills.items())
        ]
        return {"items": items}

    def list_catalog(self) -> list[dict[str, Any]]:
        return list(self.list_skills()["items"])

    def default_skill_names(self) -> set[str]:
        return {s["name"] for s in self.list_catalog() if s["source"] == "default"}

    def get_skill(self, name: str) -> dict[str, Any]:
        self._log("get_skill", name)
        sk = self.org_skills.get(name)
        if sk is None:
            raise self._err("GET", 404)
        aux = [{"path": p, "size": len(c)} for p, c in sorted(sk["aux"].items())]
        return {
            "name": name,
            "source": "org",
            "description": "",
            "body": sk["body"],
            "aux_files": aux,
        }

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        if "/default-skills/" in path:
            raise self._err(method, 403)
        if path.endswith("/files"):
            name = path.split("/skills/")[1].rsplit("/files", 1)[0]
            content = self.org_skills[name]["aux"].get(kw["params"]["path"])
            if content is None:
                raise self._err(method, 404)
            return {"path": kw["params"]["path"], "content": content.decode()}
        raise AssertionError(f"unrouted {method} {path}")

    def upload_skill(
        self, skill_name: str, skill_md_b64: str, aux_files: Any = None, replace: bool = False
    ) -> dict[str, Any]:
        self._log("upload_skill", skill_name, replace)
        if (skill_name in self.org_skills or skill_name in self.default_skills) and not replace:
            raise self._err("POST", 409, "exists")
        self.uploads.append((skill_name, replace))
        self.org_skills[skill_name] = {
            "body": base64.b64decode(skill_md_b64).decode(),
            "aux": {a["path"]: base64.b64decode(a["content_b64"]) for a in aux_files or []},
            "plugin": None,
        }
        return {}

    def upload_skill_first(self, name: str, md: str, aux: Any = None) -> dict[str, Any]:
        self._log("upload_skill_first", name)
        return self.upload_skill(name, md, aux, replace=False)

    # plugins
    def upload_plugin(self, data: bytes, filename: str, replace: bool = False) -> dict[str, Any]:
        self._log("upload_plugin", filename, replace)
        zf = zipfile.ZipFile(io.BytesIO(data))
        name = json.loads(zf.read(".claude-plugin/plugin.json"))["name"]
        if name in self.plugins and not replace:
            raise self._err("POST", 409)
        inner = []
        for member in zf.namelist():
            parts = member.split("/")
            if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
                slug = parts[1]
                aux = {
                    "/".join(m.split("/")[2:]): zf.read(m)
                    for m in zf.namelist()
                    if m.startswith(f"skills/{slug}/") and m != member
                }
                self.org_skills[slug] = {
                    "body": zf.read(member).decode(),
                    "aux": aux,
                    "plugin": name,
                }
                inner.append(slug)
        self.plugins[name] = inner
        return {}

    def get_plugin(self, name: str) -> dict[str, Any]:
        if name not in self.plugins:
            raise self._err("GET", 404)
        return {"name": name}

    def list_plugins(self) -> dict[str, Any]:
        return {"plugins": [{"name": n} for n in self.plugins]}

    # processes
    def find_process_by_name(self, name: str) -> dict[str, Any] | None:
        return next((p for p in self.processes.values() if p["name"] == name), None)

    def list_processes(self) -> dict[str, Any]:
        return {
            "processes": [
                {k: v for k, v in p.items() if k != "compiled_flow"}
                for p in self.processes.values()
            ]
        }

    def get_process(self, pid: str) -> dict[str, Any]:
        if pid not in self.processes:
            raise self._err("GET", 404)
        return dict(self.processes[pid])

    def create_process(self, **fields: Any) -> dict[str, Any]:
        self._log("create_process", fields["name"])
        self._n += 1
        pid = f"proc-{self._n}"
        self.processes[pid] = {"id": pid, **fields}
        self.assigned[pid] = []
        self.grants[pid] = {"workstream_ids": [], "folder_prefix": ""}
        return {"id": pid}

    def update_process(self, pid: str, **fields: Any) -> dict[str, Any]:
        self._log("update_process", pid, sorted(fields))
        self.processes[pid].update(fields)
        return {}

    def get_process_workstreams(self, pid: str) -> dict[str, Any]:
        return {"workstream_ids": list(self.assigned[pid])}

    def set_process_workstreams(self, pid: str, ids: list[str]) -> dict[str, Any]:
        self._log("set_process_workstreams", pid, list(ids))
        self.assigned[pid] = list(ids)
        return {}

    def get_write_workstreams(self, pid: str) -> dict[str, Any]:
        return dict(self.grants[pid])

    def set_write_workstreams(self, pid: str, ids: list[str], prefix: str = "") -> dict[str, Any]:
        self._log("set_write_workstreams", pid, list(ids), prefix)
        self.grants[pid] = {"workstream_ids": list(ids), "folder_prefix": prefix}
        return {}

    def list_workstreams(self) -> dict[str, Any]:
        return {"workstreams": []}

    # conversations and identity
    def pat_identity(self) -> PatIdentity:
        return PatIdentity(frozenset(self.pat_ids), frozenset())

    def transcript_events(self, conversation_id: str) -> Transcript:
        out = Transcript(self.conversations.get(conversation_id, []))
        out.is_shared = self.shared.get(conversation_id, True)
        return out


class FakeGitHub:
    def __init__(self) -> None:
        self.prs: list[dict[str, Any]] = []
        self.releases: list[dict[str, Any]] = []

    def create_pr(self, **kw: Any) -> dict[str, Any]:
        self.prs.append(kw)
        return {"url": f"https://example.test/pr/{len(self.prs)}", "number": len(self.prs)}

    def create_release(self, tag: str, **kw: Any) -> dict[str, Any]:
        self.releases.append(
            {"tag": tag, **kw, "asset_text": [Path(a).read_text() for a in kw.get("assets", [])]}
        )
        return {"url": "https://example.test/rel", "id": 1, "assets": []}

    def find_pr(self, head: str, *, state: str = "open") -> None:
        return None


class RecordingRows(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def insert_rows(self, table: str, rows: Any, *, validate: bool = True) -> Any:
        self.writes.append(f"insert {table}")
        return super().insert_rows(table, rows, validate=validate)

    def update_row(self, table: str, key_column: str, key: str, data: Any) -> Any:
        self.writes.append(f"update {table}")
        return super().update_row(table, key_column, key, data)


class RecordingDocs(MemStore):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def put(self, path: str, data: bytes) -> str:
        self.writes.append(path)
        return super().put(path, data)


def git(cwd: Path, *args: str) -> str:
    return rp.run_git(cwd, *args).stdout.strip()


@dataclass
class Env:
    root: Path
    ws: Path
    bare: Path
    client: FakeClient
    rows: RecordingRows
    docs: RecordingDocs
    gh: FakeGitHub
    handler: Callable[[Any], Any] = field(default=correct_handler)
    driver: FakeDriver | None = None

    # -- running commands -----------------------------------------------------------------
    def run(
        self, *argv: str, capsys: pytest.CaptureFixture[str] | None = None
    ) -> tuple[int, dict[str, Any]]:
        code = main([*argv, "--env", ENV, "--json"])
        doc: dict[str, Any] = {}
        if capsys is not None:
            out = capsys.readouterr().out.strip().splitlines()
            doc = json.loads(out[-1]) if out else {}
        return code, doc

    # -- git ----------------------------------------------------------------------------------
    def commit(self, msg: str = "change", *, push: bool = True) -> str:
        sha = rp.commit(self.ws, msg)
        if push:
            rp.push(self.ws)
        return sha

    def merge_to_main(self) -> None:
        """What the reviewed pull request does: the branch lands on the default branch."""
        rp.run_git(self.ws, "push", "-q", "origin", "HEAD:refs/heads/main")

    def bare_ref(self, ref: str) -> str:
        return git(self.bare, "rev-parse", ref)

    # -- editing ------------------------------------------------------------------------------
    def write(self, rel: str, text: str) -> None:
        p = self.ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def set_driver(self, handler: Callable[[Any], Any]) -> None:
        self.handler = handler
        self.driver = FakeDriver(handler)


def build_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    browser: bool = False,
    plugin: bool = False,
) -> Env:
    for k, v in {
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.test",
        "ABCD_FDE": "fde-a",
    }.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    workspaces = tmp_path / "workspaces"
    ws = workspaces / ENV
    ws.mkdir(parents=True)
    monkeypatch.setenv("ABCD_WORKSPACES_DIR", str(workspaces))
    (ws / "env.json").write_text(json.dumps({"env_id": ENV, "name": "Acme"}))
    bare = tmp_path / "remote.git"
    rp.run_git(None, "init", "-q", "--bare", "-b", "main", str(bare))
    bws.init_layout(
        ws,
        repo_remote=str(bare),
        workstreams={
            "acme-client": "ws-client",
            "acme-staging": "ws-staging",
            "ds:returns:c1": "ws-ds1",
            "ds:returns:c2": "ws-ds2",
            "ds:returns:c3": "ws-ds3",
        },
        stores={"builder-ops": "st-ops", "builder-status-acme": "st-status"},
    )
    rp.init_repo(ws, "main")
    rp.add_remote(ws, str(bare))
    rp.commit(ws, "init workspace")
    rp.push(ws, "main", allow_default=True)
    rp.create_branch(ws, "feat/agent")

    # the agent: a skill, an agent bundle, a dataset and its criteria
    (ws / "harness/skills/tax-calc").mkdir(parents=True)
    (ws / "harness/skills/tax-calc/SKILL.md").write_text(SKILL_MD)
    (ws / "harness/skills/tax-calc/scripts").mkdir()
    (ws / "harness/skills/tax-calc/scripts/calc.py").write_text("def calc(x):\n    return x * 2\n")
    comps = "  - {type: skill, source: org, ref: tax-calc}\n"
    if browser:
        from tests.builder.test_browser_gate import make_skill

        make_skill(ws / "harness/skills/bank-statement")
        comps += "  - {type: skill, source: org, ref: bank-statement}\n"
    if plugin:
        comps += "  - {type: plugin, source: org, ref: tax-plugin}\n"
        root = ws / "harness/plugins/tax-plugin"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text(
            json.dumps({"name": "tax-plugin", "version": "1.0.0", "description": "Tax helpers"})
        )
        (root / "skills/helper").mkdir(parents=True)
        (root / "skills/helper/SKILL.md").write_text(
            "---\nname: helper\ndescription: h\n---\nHelp.\n"
        )
    (ws / f"harness/agents/{AGENT}").mkdir(parents=True)
    (ws / f"harness/agents/{AGENT}/process.yaml").write_text(
        f"name: {AGENT}\ndisplay_name: Return prep\ndescription: Prepares returns\n"
        f"components:\n{comps}workstreams: [acme-client]\nwrite_workstreams: [acme-client]\n"
        "write_folder_prefix: out/\nis_enabled: true\n"
    )
    (ws / f"harness/agents/{AGENT}/instructions.md").write_text(
        "Use tax-calc to prepare the return.\n"
    )
    (ws / f"harness/agents/{AGENT}/release.yaml").write_text(
        f"agent: {AGENT}\ndataset: returns\ncriteria:\n"
        "  - {metric: pass_rate, comparator: '>=', target: 0.9}\n"
        "  - {metric: blind_repro_rate, comparator: '>=', target: 0.9}\n"
        "  - {metric: open_gaps, comparator: '<=', target: 0}\n"
    )
    write_prompts(ws, AGENT)
    store = RecordingDocs()
    D.create_dataset(ws, "returns", "work_product", client="acme", default_scorers=["field_match"])
    for i in (1, 2, 3):
        add_case(
            ws, store, "returns", f"c{i}",
            expected={"return.json": json.dumps({"entity": "Zenith Orchard Partners", "total_tax": 48213.77 + i}).encode()},
        )  # fmt: skip
    store.writes.clear()
    rp.commit(ws, "add the agent")
    rp.push(ws)
    env = Env(tmp_path, ws, bare, FakeClient(), RecordingRows(), store, FakeGitHub())
    env.set_driver(correct_handler)
    services = builder_agent.Services(
        client=env.client,
        rows=env.rows,
        docs=env.docs,
        driver=None,
        github=lambda _remote: env.gh,
    )

    def factory(ctx: Any, ws_: Path, cfg: dict[str, Any]) -> builder_agent.Services:
        services.driver = env.driver
        return services

    monkeypatch.setattr(builder_agent, "SERVICES_FACTORY", factory)
    monkeypatch.setattr(builder_agent, "audit_skill_dir", lambda d: (0, ""))
    monkeypatch.setattr(builder_release, "audit_skill_dir", lambda d: (0, ""), raising=False)
    return env


def cycle(
    env: Env, capsys: pytest.CaptureFixture[str], *, skill_text: str | None = None
) -> dict[str, Any]:
    """One build cycle up to a mergeable release: edit, push staged, stage, test-staged."""
    if skill_text is not None:
        env.write("harness/skills/tax-calc/SKILL.md", skill_text)
        env.commit("change skill")
    code, doc = env.run("push", AGENT, "--staged", capsys=capsys)
    assert code == 0, doc.get("error")
    code, doc = env.run("stage", AGENT, capsys=capsys)
    assert code == 0, doc.get("error")
    code, run = env.run("test-staged", AGENT, "--poll-interval", "0", capsys=capsys)
    assert code == 0, run.get("error")
    assert run["data"]["gate"]["passed"] is True
    return run["data"]
