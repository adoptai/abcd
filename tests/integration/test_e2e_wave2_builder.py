"""Wave-2 Builder commands (setup, datasets, staging, release) against the fake BFF.

The remote is a local bare repo that git sees as ``https://github.test/acme/acme.git`` and ``gh``
is a fake on PATH, so release's tag, GitHub release and pull-request steps run offline.
Each test skips until every command it uses is registered.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from tests.integration import fake_agent as fa
from tests.integration.conftest import CLIResult, FakeGitHub
from tests.integration.fake_bff import FakeBFFServer
from tests.integration.helpers import git, require_command

pytestmark = pytest.mark.integration

EXPECTED_JSON = '{"invoice": "A-1", "bucket": "approve"}\n'
INPUT_TXT = "invoice A-1 amount 100\n"
LIVE_WORKSTREAM = "acme-finance"


@dataclass
class Builder:
    cli: Callable[..., CLIResult]
    ws: Path
    bff: FakeBFFServer
    github: FakeGitHub
    skill_dir: Path

    def commit_all(self, message: str) -> None:
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-m", message)

    def merge_to_main(self) -> None:
        """What a reviewer does on GitHub: the feature branch lands on the default branch."""
        branch = git(self.ws, "rev-parse", "--abbrev-ref", "HEAD").strip()
        git(self.ws, "push", "-u", "origin", branch)
        git(self.ws, "checkout", "main")
        git(self.ws, "merge", "--ff-only", branch)
        git(self.ws, "push", "origin", "main")

    def live_workstream_id(self) -> str:
        return next(
            w["id"] for w in self.bff.state.workstreams.values() if w["name"] == LIVE_WORKSTREAM
        )


@pytest.fixture
def repo_ws(cli, acme: Path, bff: FakeBFFServer, fake_github: FakeGitHub) -> Path:
    """The ``acme`` workspace as a repo with Builder files, pushed to the fake GitHub remote."""
    require_command("ws init", "builder setup")
    init = cli("ws", "init", "--remote", fake_github.url)
    assert init.code == 0, init.text
    return acme


@pytest.fixture
def builder(
    repo_ws: Path,
    cli,
    bff: FakeBFFServer,
    fake_github: FakeGitHub,
    fixture_skill: Path,
    tmp_path: Path,
) -> Builder:
    """An agent ``triage`` on one skill, with a dataset and release criteria, on a feature branch."""
    require_command("builder setup", "agent new", "dataset create", "dataset add-case", "criteria set",
                    "push", "stage", "test-staged", "release")  # fmt: skip
    assert cli("workstream", "ensure", LIVE_WORKSTREAM).code == 0
    setup = cli("builder", "setup", "--agent-workstream", LIVE_WORKSTREAM)
    assert setup.code == 0, setup.text
    skills = repo_ws / "harness" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fixture_skill, skills / "invoice-triage")
    assert cli("agent", "new", "triage", "--display-name", "Invoice triage").code == 0
    agent_dir = repo_ws / "harness" / "agents" / "triage"
    proc = yaml.safe_load((agent_dir / "process.yaml").read_text())
    proc.update(
        components=[{"type": "skill", "source": "org", "ref": "invoice-triage"}],
        workstreams=[LIVE_WORKSTREAM], is_enabled=True,
    )  # fmt: skip
    (agent_dir / "process.yaml").write_text(yaml.safe_dump(proc, sort_keys=False))
    (agent_dir / "instructions.md").write_text("Triage the invoices in the workstream.\n")
    (agent_dir / "prompts.yaml").write_text(
        "blind: Process the attached files and write the result to outputs/.\n"
    )
    b = Builder(cli, repo_ws, bff, fake_github, fixture_skill)
    # `builder setup --agent-workstream` recorded the live workstream's id for the release step
    cfg = json.loads((repo_ws / "builder.json").read_text())
    assert cfg["workstreams"][LIVE_WORKSTREAM] == b.live_workstream_id()
    git(repo_ws, "checkout", "-b", "feat/triage")
    b.commit_all("Add the triage agent")

    inputs, expected = tmp_path / "invoice.txt", tmp_path / "expected.json"
    inputs.write_text(INPUT_TXT)
    expected.write_text(EXPECTED_JSON)
    created = cli(
        "dataset", "create", "invoices", "--kind", "work_product", "--scorer", "field_match"
    )
    assert created.code == 0, created.text
    added = cli(
        "dataset", "add-case", "invoices", "c1", "--input", str(inputs), "--expected", str(expected)
    )
    assert added.code == 0, added.text
    crit = cli(
        "criteria",
        "set",
        "triage",
        "--dataset",
        "invoices",
        "--metric",
        "pass_rate",
        "--op",
        ">=",
        "--target",
        "0.5",
    )
    assert crit.code == 0, crit.text
    git(repo_ws, "push", "-u", "origin", "feat/triage")
    return b


# -- builder setup -------------------------------------------------------------------------
def test_builder_setup_is_idempotent_and_isolates_the_bundles_store(
    repo_ws: Path, cli, bff: FakeBFFServer
) -> None:
    require_command("builder setup")
    dry = cli("builder", "setup", "--dry-run")
    assert dry.code == 0 and bff.state.tables == {}

    first = cli("builder", "setup", "--json")
    assert first.code == 0, first.text
    names = {w["name"]: w["id"] for w in bff.state.workstreams.values()}
    assert {
        "builder-context",
        "builder-ops",
        "builder-sme-acme",
        "builder-status-acme",
        "acme-staging",
    } <= set(names)
    assert len(bff.state.tables) == 25 and all(t["workstreams"] for t in bff.state.tables.values())
    bundles = next(s for s in bff.state.stores.values() if s["name"] == "noui-bundles")
    assert bundles["workstream_ids"] == [names["builder-ops"]]  # capture bundles hold cookies

    # every workstream already has a default "Workstream Files" store (live); the ops store the
    # Builder records must be that one, never the noui-bundles store linked to the same workstream
    stores = json.loads((repo_ws / "builder.json").read_text())["stores"]
    assert stores["builder-ops"] != stores["noui-bundles"]
    assert bff.state.stores[stores["builder-ops"]]["name"] != "noui-bundles"

    cfg = json.loads((repo_ws / "builder.json").read_text())
    assert cfg["workstreams"]["builder-ops"] == names["builder-ops"]
    assert set(cfg["tables"]) == {t["table_name"] for t in bff.state.tables.values()}

    stores_before, tables_before, ws_before = (
        len(bff.state.stores),
        len(bff.state.tables),
        len(bff.state.workstreams),
    )
    again = cli("builder", "setup", "--json")
    assert again.code == 0, again.text
    assert (len(bff.state.stores), len(bff.state.tables), len(bff.state.workstreams)) == (
        stores_before,
        tables_before,
        ws_before,
    )
    assert json.loads((repo_ws / "builder.json").read_text())["stores"] == stores
    assert bff.forbidden_calls() == []


# -- datasets ------------------------------------------------------------------------------
def test_dataset_cases_keep_expected_values_out_of_tables(builder: Builder) -> None:
    bff = builder.bff
    shown = builder.cli("dataset", "show", "invoices", "--json")
    assert shown.code == 0 and "c1" in shown.text
    # expected values live in the ds- Doc Store; the Data Store holds only paths and hashes
    ds_store = next(s for s in bff.state.stores.values() if s["name"] == "ds-invoices")
    contents = [
        bff.state.objects[d["s3_key"]]
        for d in bff.state.documents[ds_store["id"]].values()
        if d["status"] != "deleted"
    ]
    assert EXPECTED_JSON.encode() in contents
    for rows in bff.state.table_rows.values():
        assert "A-1,approve" not in json.dumps(rows)
    # the dataset workstream carries inputs only: the expected-values store is not linked to it
    case_ws = next(w for w in bff.state.workstreams.values() if w["name"].startswith("ds:invoices"))
    assert case_ws["id"] not in ds_store["workstream_ids"]


# -- staging -------------------------------------------------------------------------------
def test_push_staged_creates_shadows_and_touches_nothing_live(builder: Builder) -> None:
    bff = builder.bff
    res = builder.cli("push", "--staged", "triage", "--json")
    assert res.code == 0, res.text
    assert "invoice-triage--staged" in bff.state.org_skills
    assert "invoice-triage" not in bff.state.org_skills  # the live name is untouched
    staged = bff.state.org_skills["invoice-triage--staged"]
    assert staged["meta"]["name"] == "invoice-triage--staged"
    assert str(staged["meta"]["description"]).startswith("Staging copy, do not use")
    assert (
        builder.ws / "harness" / "skills" / "invoice-triage" / "SKILL.md"
    ).read_bytes() == builder.skill_dir.joinpath("SKILL.md").read_bytes()

    procs = {p["name"]: p for p in bff.state.processes.values()}
    assert set(procs) == {"triage-staged"}  # no live process yet
    shadow = procs["triage-staged"]
    assert shadow["components"] == [
        {"type": "skill", "source": "org", "ref": "invoice-triage--staged"}
    ]
    assigned = set(bff.state.process_ws[shadow["id"]])
    assert (
        builder.live_workstream_id() not in assigned
    )  # a staged agent never runs in a client workstream
    names = {bff.state.workstreams[w]["name"] for w in assigned}
    assert names and all(n.endswith("-staging") or n.startswith("ds:") for n in names)
    assert bff.forbidden_calls() == []


def test_stage_refuses_a_dirty_tree_and_an_unpushed_commit(builder: Builder) -> None:
    assert builder.cli("push", "--staged", "triage").code == 0
    (builder.ws / "harness" / "agents" / "triage" / "instructions.md").write_text(
        "Changed but not committed.\n"
    )
    dirty = builder.cli("stage", "triage")
    assert dirty.code == 1 and "uncommitted" in dirty.text.lower() or "dirty" in dirty.text.lower()
    assert "triage@v1-rc" not in git(builder.ws, "tag", "--list")
    builder.commit_all("Edit instructions")
    unpushed = builder.cli("stage", "triage")
    assert unpushed.code == 1 and "triage@v1-rc" not in git(builder.ws, "tag", "--list")


def _script_blind_run(bff: FakeBFFServer) -> None:
    bff.state.script(
        [fa.artifact("outputs/result.json", EXPECTED_JSON), fa.text("Triaged one invoice.")],
        process="triage-staged",
    )  # fmt: skip


def test_stage_release_flow_promotes_exactly_what_was_staged(builder: Builder) -> None:
    bff, cli, ws = builder.bff, builder.cli, builder.ws
    _script_blind_run(bff)
    assert cli("push", "--staged", "triage").code == 0
    blind = cli("test-staged", "triage")
    assert blind.code == 0, blind.text
    turn = bff.state.turn_requests[-1]
    assert turn["process_id"] in {
        p["id"] for p in bff.state.processes.values() if p["name"] == "triage-staged"
    }
    assert "triage" not in {
        p["name"] for p in bff.state.processes.values()
    }  # evals never run the live agent

    staged = cli("stage", "triage")
    assert staged.code == 0, staged.text
    assert "triage@v1-rc" in git(ws, "tag", "--list")

    # the release gate: no approval, no live change
    no_yes = cli("release", "triage")
    assert no_yes.code == 1 and "invoice-triage" not in bff.state.org_skills
    # a commit that is not on the default branch cannot be released
    off_branch = cli("release", "triage", "--yes")
    assert off_branch.code == 1 and "default branch" in off_branch.text
    assert "invoice-triage" not in bff.state.org_skills

    builder.merge_to_main()
    main_head = git(ws, "rev-parse", "main").strip()
    released = cli("release", "triage", "--yes", "--json")
    assert released.code == 0, released.text

    live = bff.state.org_skills["invoice-triage"]
    assert (
        live["md"] == builder.skill_dir.joinpath("SKILL.md").read_bytes()
    )  # names restored, byte for byte
    assert set(live["aux"]) == {"references/rules.md", "scripts/summarize.py"}
    procs = {p["name"]: p for p in bff.state.processes.values()}
    assert set(procs) == {"triage", "triage-staged"}
    assert procs["triage"]["components"] == [
        {"type": "skill", "source": "org", "ref": "invoice-triage"}
    ]
    assert bff.state.process_ws[procs["triage"]["id"]] == [builder.live_workstream_id()]
    assert "triage@v1" in git(ws, "tag", "--list")
    # nothing commits to the default branch: the generated files go to a release branch and a PR
    assert git(ws, "rev-parse", "main").strip() == main_head
    assert "release/triage@v1" in git(builder.github.bare, "branch", "--list")
    gh = [c[:2] for c in builder.github.calls()]
    assert ["release", "create"] in gh and ["pr", "create"] in gh
    assert bff.forbidden_calls() == []

    drift = cli("audit", "drift")
    assert drift.code == 0, drift.text


def test_out_of_band_edit_of_a_released_skill_shows_as_drift(builder: Builder) -> None:
    require_command("audit drift")
    bff, cli = builder.bff, builder.cli
    _script_blind_run(bff)
    assert cli("push", "--staged", "triage").code == 0
    assert cli("test-staged", "triage").code == 0
    assert cli("stage", "triage").code == 0
    builder.merge_to_main()
    assert cli("release", "triage", "--yes").code == 0
    assert cli("audit", "drift").code == 0  # what is live is what was released

    edited = builder.skill_dir.parent.parent / "edited" / "invoice-triage"
    edited.mkdir(parents=True)
    (edited / "SKILL.md").write_text(
        "---\nname: invoice-triage\ndescription: Edited in the UI.\n---\n\n# changed by hand\n"
    )
    assert cli("skill", "push", str(edited), "--replace").code == 0  # someone edits the live skill
    drift = cli("audit", "drift", "--json")
    assert drift.code == 1, drift.text
    assert "invoice-triage" in drift.text


# -- evals ---------------------------------------------------------------------------------
def test_local_eval_scores_outputs_without_running_the_agent(
    builder: Builder, tmp_path: Path
) -> None:
    require_command("eval run")
    bff, cli = builder.bff, builder.cli
    expected, inputs = tmp_path / "expected.json", tmp_path / "in.txt"
    expected.write_text(json.dumps({"A-1": "approve", "total": 100}))
    inputs.write_text("invoice A-1 amount 100\n")
    assert (
        cli("dataset", "create", "ledger", "--kind", "work_product", "--scorer", "field_match").code
        == 0
    )
    assert (
        cli(
            "dataset",
            "add-case",
            "ledger",
            "l1",
            "--input",
            str(inputs),
            "--expected",
            str(expected),
        ).code
        == 0
    )

    def run(label: str, observed: dict[str, object]) -> dict[str, object]:
        case = tmp_path / label / "l1"
        case.mkdir(parents=True)
        (case / "expected.json").write_text(json.dumps(observed))
        res = cli(
            "eval",
            "run",
            "triage",
            "--dataset",
            "ledger",
            "--tier",
            "local",
            "--outputs",
            str(case.parent),
            "--json",
        )
        assert res.code == 0, res.text
        data: dict[str, object] = res.json["data"]
        return data

    good = run("good", {"A-1": "approve", "total": 100})
    assert good["passed"] == 1 and good["tier"] == "local" and good["failed"] == []
    bad = run("bad", {"A-1": "reject", "total": 100})
    assert bad["passed"] == 0 and bad["failed"] == ["l1"]
    assert bff.state.turns == {}  # local tier: no harness turn, no spend
    for rows in bff.state.table_rows.values():
        assert "A-1" not in json.dumps(rows)  # results hold hashes and counts, never values
    assert bff.forbidden_calls() == []
