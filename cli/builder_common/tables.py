"""Declarative specs for every ``bld_*`` Data Store table (PLAN.md section 9).

Rules enforced here, structurally:

* **No answer ever sits in a table.** Expected values, labels, states, case
  notes and expected-versus-observed diffs live in Doc Stores. A table holds
  only ``*_path`` and ``*_sha256`` for them. ``FORBIDDEN_VALUE_COLUMNS`` lists
  column names that would carry such values; ``assert_no_answer_columns``
  fails for any spec that has one, and ``validate_row`` rejects any column that
  is not in the spec, so a value cannot be smuggled into a row either.
* **Enums are checked by abcd**, because the Data Store does not enforce them
  (enum columns are created as plain ``text``).
* Every table has one unique key (a single UNIQUE NOT NULL column; the Data
  Store's per-column ``unique`` flag), so retries are idempotent: a 409 means
  the row was already written.
* ``workstream_scoped = false`` for every table (rows written through the API
  get a null workstream_id).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from cli.builder_common.errors import RefusedError, UsageError

# Column names that would hold an answer. None may appear in any table.
FORBIDDEN_VALUE_COLUMNS = frozenset(
    {
        "expected",
        "expected_value",
        "expected_values",
        "expected_output",
        "observed",
        "observed_value",
        "observed_values",
        "observed_output",
        "label",
        "labels",
        "golden",
        "golden_output",
        "case_note",
        "case_notes",
        "note",
        "notes",
        "diff",
        "expected_vs_observed",
        "state_value",
    }
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Workstream roles a table can be linked to (resolved by the caller).
WS_OPS = "ops"
WS_CONTEXT = "context"
WS_SME = "sme"
WS_LIVE_AGENTS = "live_agents"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str
    note: str = ""
    enum_values: tuple[str, ...] = ()
    unique: bool = False
    nullable: bool = True

    def api_dict(self) -> dict[str, Any]:
        """Column as POST /v1/org-db/tables expects it (schemas/org_db.py:32-45).

        Enum columns are sent as ``text``: the Data Store would add a CHECK
        constraint for ``enum`` and abcd validates enums itself.
        """
        api_type = "text" if self.type == "enum" else self.type
        return {
            "name": self.name,
            "type": api_type,
            "nullable": self.nullable,
            "primary": False,
            "unique": self.unique,
        }


@dataclass(frozen=True)
class TableSpec:
    name: str
    group: str
    purpose: str
    unique_key: str
    columns: tuple[ColumnSpec, ...]
    workstream: str = WS_OPS
    extra_workstreams: tuple[str, ...] = ()

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> ColumnSpec:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)

    @property
    def workstream_roles(self) -> tuple[str, ...]:
        return (self.workstream, *self.extra_workstreams)

    def api_columns(self) -> list[dict[str, Any]]:
        return [c.api_dict() for c in self.columns]


def _col(name: str, type_: str, note: str = "") -> ColumnSpec:
    return ColumnSpec(name=name, type=type_, note=note)


def _enum(name: str, values: tuple[str, ...]) -> ColumnSpec:
    return ColumnSpec(name=name, type="enum", enum_values=values)


def _with_key(spec: TableSpec) -> TableSpec:
    cols = tuple(
        ColumnSpec(c.name, c.type, c.note, c.enum_values, unique=True, nullable=False)
        if c.name == spec.unique_key
        else c
        for c in spec.columns
    )
    return TableSpec(
        spec.name,
        spec.group,
        spec.purpose,
        spec.unique_key,
        cols,
        spec.workstream,
        spec.extra_workstreams,
    )


_RAW_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        name="bld_agents",
        group="Registry",
        purpose="One row per agent built through the Builder.",
        unique_key="agent_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("agent_key", "varchar", "unique, e.g. acme-1120pc-prep"),
            _col("process_id", "varchar", "the live process id"),
            _col("staged_process_id", "varchar", "the <agent>-staged process id"),
            _col("name", "text", ""),
            _col("client_workstream_id", "varchar", ""),
            _col("owner_user_id", "varchar", "the FDE"),
            _col("bundle_path", "text", "harness/agents/<agent> in the workspace repo"),
            _col("live_version", "varchar", ""),
            _col("staged_version", "varchar", ""),
            _col(
                "staged_lock_holder",
                "varchar",
                "the FDE holding the staged slot; push --staged --take moves it",
            ),
            _col("staged_lock_sha", "varchar", "the commit pushed under the lock"),
            _col("staged_lock_since", "timestamptz", ""),
            _enum("phase", ("designing", "building", "staged", "live", "paused")),
        ),
    ),
    TableSpec(
        name="bld_build_plan",
        group="Registry",
        purpose="Skills, plugins and agents to build or change, and their approval.",
        unique_key="item_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("item_key", "varchar", "unique"),
            _col("agent_key", "varchar", ""),
            _enum("kind", ("skill", "plugin", "agent", "check")),
            _col("name", "text", ""),
            _enum("action", ("new", "change", "retire")),
            _col("reason_ref", "varchar", "decision, run or ticket id"),
            _enum("status", ("proposed", "approved", "building", "done", "dropped")),
            _col("approved_by", "varchar", ""),
            _col("approved_at", "timestamptz", ""),
        ),
    ),
    TableSpec(
        name="bld_versions",
        group="Versions",
        purpose="Every immutable version of a skill, plugin or agent.",
        unique_key="version_id",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("version_id", "varchar", "unique, e.g. acme-1120pc-prep@v12"),
            _enum("object_kind", ("skill", "plugin", "agent")),
            _col("object_name", "text", ""),
            _col("content_hash", "varchar", "v=<hash> bundle id"),
            _col("skill_md_sha256", "varchar", "skills: SHA-256 of the SKILL.md as uploaded"),
            _col(
                "manifest",
                "jsonb",
                "skills: aux listing [{path,size,sha256?}]; agents: per-field hashes of the normalized process",
            ),
            _col("git_sha", "varchar", "commit in the workspace repo"),
            _col("parent_version_id", "varchar", ""),
            _enum("state", ("draft", "staged", "live", "superseded")),
            _col(
                "pinned_components",
                "jsonb",
                "agents only: exact skill and plugin versions, and the bundle_version of every default-tier skill used",
            ),
            _col(
                "replay_fingerprint",
                "varchar",
                "browser skills only: the fingerprint in replay_approval.json",
            ),
            _col("author_user_id", "varchar", ""),
            _col("reason_ref", "varchar", ""),
            _col("notes_path", "text", ""),
        ),
    ),
    TableSpec(
        name="bld_releases",
        group="Versions",
        purpose="Every promotion to live, with its evidence.",
        unique_key="release_id",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("release_id", "varchar", "unique"),
            _col("agent_key", "varchar", ""),
            _col("version_id", "varchar", ""),
            _col("previous_version_id", "varchar", "rollback target"),
            _col("eval_run_id", "varchar", "the staged run used as evidence"),
            _enum("gate_result", ("pass", "fail", "forced")),
            _col("report_path", "text", "releases/<agent>/<version>/eval_report.html"),
            _col("approved_by", "varchar", ""),
            _col("forced_reason", "text", "required when forced"),
            _col("released_at", "timestamptz", ""),
        ),
    ),
    TableSpec(
        name="bld_release_criteria",
        group="Versions",
        purpose="What the next release must meet, per agent.",
        unique_key="criteria_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("criteria_key", "varchar", "unique"),
            _col("agent_key", "varchar", ""),
            _col("applies_from_version", "varchar", ""),
            _col("dataset_key", "varchar", ""),
            _col("dataset_version", "int", ""),
            _col("metric_key", "varchar", ""),
            _enum("comparator", (">=", "<=", "no_regression")),
            _col("target", "numeric", ""),
            _col("set_by", "varchar", ""),
            _col("decision_ref", "varchar", "client sign-off"),
        ),
    ),
    TableSpec(
        name="bld_datasets",
        group="Datasets",
        purpose="The dataset index.",
        unique_key="dataset_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("dataset_key", "varchar", "unique, e.g. acme-1120pc"),
            _col("name", "text", ""),
            _col("client_workstream_id", "varchar", ""),
            _col("current_version", "int", ""),
            _col("expected_store_id", "varchar", "ds-<dataset> store"),
            _enum("kind", ("work_product", "labeled", "read_your_writes", "custom")),
            _enum("layout", ("workstream_per_case", "shared_workstream")),
            _enum("run_mode", ("per_case", "batch", "sequential")),
            _col("target_env", "varchar", "read_your_writes only, e.g. CRM_TEST_DB"),
            _col("probe_process_id", "varchar", "read_your_writes only: the read-only probe agent"),
            _col(
                "default_scorers",
                "text[]",
                "field_match, label_match, rubric, state_match, outcome_match, collateral, script:<name>",
            ),
            _col("owner_user_id", "varchar", ""),
        ),
    ),
    TableSpec(
        name="bld_dataset_versions",
        group="Datasets",
        purpose="One row per dataset version; comparisons only happen within one version.",
        unique_key="dataset_version_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("dataset_version_key", "varchar", "unique, e.g. acme-1120pc@4"),
            _col("dataset_key", "varchar", ""),
            _col("version", "int", ""),
            _col("case_count", "int", ""),
            _col("change_note", "text", "what changed and why"),
            _col("manifest_path", "text", "manifests/v4.json"),
            _col("created_by", "varchar", ""),
        ),
    ),
    TableSpec(
        name="bld_dataset_cases",
        group="Datasets",
        purpose="Every case of every dataset kind, and where its expectation lives.",
        unique_key="case_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("case_key", "varchar", "unique, dataset:case"),
            _col("dataset_key", "varchar", ""),
            _col("case_id", "varchar", ""),
            _col("case_workstream_id", "varchar", "its own, or the shared dataset workstream"),
            _col("inputs", "jsonb", "file paths or an inline payload (an instruction, an email)"),
            _col("setup", "jsonb", "read_your_writes: snapshot query, fixture to ensure"),
            _col(
                "expected_path",
                "text",
                "Doc Store path of the expected files, labels or state in ds-<dataset>; never the values",
            ),
            _col("expected_sha256", "varchar", ""),
            _col("teardown", "jsonb", "read_your_writes: restore through the same write path"),
            _col("scorers", "text[]", "overrides the dataset default"),
            _enum("source", ("real", "known", "synthetic", "production")),
            _col("tags", "text[]", "e.g. edge-case, overwrite, unknown-client"),
            _col("first_version", "int", ""),
            _col("retired_in_version", "int", "null while active"),
        ),
    ),
    TableSpec(
        name="bld_checks",
        group="Quality",
        purpose="Client checks an output must pass to count as accurate.",
        unique_key="check_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("check_key", "varchar", "unique"),
            _col("client_workstream_id", "varchar", ""),
            _col("agent_key", "varchar", ""),
            _col("title", "text", "e.g. Headline values within $2"),
            _enum("kind", ("deterministic", "judged", "goal_reached")),
            _col("rule", "jsonb", "the rule, rubric or goal"),
            _enum("severity", ("blocking", "warning")),
            _col("source_decision", "varchar", ""),
            _col("active_from", "timestamptz", ""),
        ),
    ),
    TableSpec(
        name="bld_metrics",
        group="Quality",
        purpose="Metric definitions, versioned.",
        unique_key="metric_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("metric_key", "varchar", "unique, e.g. acme-1120pc:accuracy@2"),
            _col("agent_key", "varchar", ""),
            _col("name", "text", ""),
            _enum("category", ("volume", "time", "quality", "cost")),
            _col("unit", "varchar", "%, hrs, $, #"),
            _col("formula", "text", ""),
            _enum(
                "source", ("check_results", "hitl_events", "user_actions", "run_timings", "cost")
            ),
            _col("baseline", "numeric", ""),
            _col("is_gate", "boolean", ""),
            _col("decision_ref", "varchar", "client sign-off"),
        ),
    ),
    TableSpec(
        name="bld_eval_runs",
        group="Quality",
        purpose="One row per eval run.",
        unique_key="eval_run_id",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("eval_run_id", "varchar", "unique"),
            _col("agent_key", "varchar", ""),
            _col("version_id", "varchar", ""),
            _col("dataset_version_key", "varchar", ""),
            _enum("tier", ("local", "platform", "staged", "production")),
            _col("blind", "boolean", "true for every gated run"),
            _enum("ran_in", ("local", "cloud")),
            _col("conversation_id", "varchar", "cloud runs: ADOPT_CONVERSATION_ID"),
            _col(
                "submitted_turns",
                "jsonb",
                "turn ids submitted per case, so a run can resume and poll",
            ),
            _enum("status", ("running", "done", "failed")),
            _col("cost_usd", "numeric", ""),
            _col("started_at", "timestamptz", ""),
            _col("finished_at", "timestamptz", ""),
            _col("report_path", "text", ""),
        ),
    ),
    TableSpec(
        name="bld_eval_results",
        group="Quality",
        purpose="One row per case per eval run.",
        unique_key="result_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("result_key", "varchar", "unique, run:case"),
            _col("eval_run_id", "varchar", ""),
            _col("case_key", "varchar", ""),
            _col("observed_path", "text", "path of the observed output in builder-ops"),
            _col("observed_sha256", "varchar", "SHA-256 of the file at observed_path"),
            _col("diff_path", "text", "path of the expected-versus-observed diff in builder-ops"),
            _col("diff_sha256", "varchar", "SHA-256 of the file at diff_path"),
            _col("harness_run_id", "varchar", "link to the trace"),
            _col("passed", "boolean", "all blocking checks passed"),
            _col("score", "numeric", ""),
            _col("failed_checks", "text[]", "check ids only"),
            _enum(
                "miss_cause", ("rounding", "missing_context", "playbook_difference", "real_error")
            ),
            _col("steps", "int", ""),
            _col("tokens", "bigint", ""),
            _col("duration_s", "int", ""),
            _col("hitl_count", "int", ""),
        ),
    ),
    TableSpec(
        name="bld_check_results",
        group="Quality",
        purpose="One row per check per subject, in evals and in production.",
        unique_key="check_result_key",
        workstream="ops",
        extra_workstreams=("live_agents",),
        columns=(
            _col("check_result_key", "varchar", "unique"),
            _col("check_key", "varchar", ""),
            _enum("subject_kind", ("eval_case", "production_run")),
            _col("subject_ref", "varchar", ""),
            _col("eval_run_id", "varchar", "null in production"),
            _col("passed", "boolean", ""),
            _col(
                "detail_path",
                "text",
                "path of the expected-versus-found detail in builder-ops; never the values",
            ),
            _col("detail_sha256", "varchar", "SHA-256 of the file at detail_path"),
        ),
    ),
    TableSpec(
        name="bld_metric_values",
        group="Quality",
        purpose="Computed metric values for runs, phases and weeks.",
        unique_key="value_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("value_key", "varchar", "unique"),
            _col("metric_key", "varchar", ""),
            _col("agent_key", "varchar", ""),
            _col("version_id", "varchar", ""),
            _enum("scope", ("eval_run", "phase", "week")),
            _col("scope_ref", "varchar", "e.g. UAT 1, 2026-W43"),
            _col("value", "numeric", ""),
            _col("computed_at", "timestamptz", ""),
        ),
    ),
    TableSpec(
        name="bld_kg_nodes",
        group="Context",
        purpose="Nodes of the knowledge graph.",
        unique_key="node_key",
        workstream="context",
        extra_workstreams=(),
        columns=(
            _col("node_key", "varchar", "unique, type:slug"),
            _enum(
                "type",
                ("client", "system", "api", "skill", "agent", "dataset", "incident", "person"),
            ),
            _col("name", "text", ""),
            _col("page_path", "text", "kg/... in Builder: context"),
            _enum("status", ("proposed", "vetted", "retired")),
            _col("owner_user_id", "varchar", ""),
        ),
    ),
    TableSpec(
        name="bld_kg_edges",
        group="Context",
        purpose="Typed links between nodes.",
        unique_key="edge_key",
        workstream="context",
        extra_workstreams=(),
        columns=(
            _col("edge_key", "varchar", "unique"),
            _col("from_node", "varchar", ""),
            _col("to_node", "varchar", ""),
            _enum("relation", ("uses", "calls", "owns", "replaced_by", "failed_on", "tested_by")),
            _col("source_ref", "varchar", "call, doc, run or release that showed it"),
            _enum("status", ("proposed", "vetted")),
        ),
    ),
    TableSpec(
        name="bld_decisions",
        group="Context",
        purpose="Every answered question, so it is never asked twice.",
        unique_key="decision_key",
        workstream="context",
        extra_workstreams=(),
        columns=(
            _col("decision_key", "varchar", "unique"),
            _col("client_workstream_id", "varchar", ""),
            _col("agent_key", "varchar", ""),
            _col("question", "text", ""),
            _col("answer", "text", ""),
            _col("decided_by", "varchar", ""),
            _enum("source_kind", ("call", "email", "fde", "client")),
            _col("source_path", "text", ""),
            _enum("status", ("proposed", "confirmed")),
        ),
    ),
    TableSpec(
        name="bld_open_items",
        group="Context",
        purpose="What is pending on an agent's design, and who it waits on.",
        unique_key="item_key",
        workstream="ops",
        extra_workstreams=("context",),
        columns=(
            _col("item_key", "varchar", "unique"),
            _col("agent_key", "varchar", ""),
            _enum("kind", ("design_gap", "question", "waiting_on_client", "blocked")),
            _col("title", "text", ""),
            _enum("waiting_on", ("fde", "client", "platform")),
            _col("due", "date", ""),
            _enum("status", ("open", "resolved")),
            _col("resolved_by_decision", "varchar", ""),
        ),
    ),
    TableSpec(
        name="bld_context_gaps",
        group="Context",
        purpose="Every case a blind eval could not reproduce because context was missing, until a blind eval reproduces it.",
        unique_key="gap_key",
        workstream="ops",
        extra_workstreams=("sme",),
        columns=(
            _col("gap_key", "varchar", "unique"),
            _col("agent_key", "varchar", ""),
            _col("case_key", "varchar", "the case that showed it"),
            _col("eval_run_id", "varchar", "the blind run that opened it"),
            _col("failed_checks", "text[]", ""),
            _col("hypothesis", "text", "what the Builder thinks is missing"),
            _col("question", "text", "what it asks, with evidence attached"),
            _col(
                "evidence_path",
                "text",
                "gaps/<gap_id>.md: input excerpt, output vs expected, the agent's reasoning",
            ),
            _enum("assigned_to", ("fde", "sme")),
            _col(
                "answer_path", "text", "path of the answer in builder-sme; never the answer itself"
            ),
            _col("answer_sha256", "varchar", "SHA-256 of the answer file"),
            _col("instruction_key", "varchar", "the instruction that encodes it"),
            _enum("status", ("open", "asked", "answered", "encoded", "verified", "waived")),
            _col("verified_by_run", "varchar", "the blind run that reproduced the case"),
            _col("waive_reason", "text", ""),
        ),
    ),
    TableSpec(
        name="bld_case_notes",
        group="Context",
        purpose="Path and hash of a note on how the SME or FDE produced the golden output for one case. Never shown to a blind run.",
        unique_key="note_key",
        workstream="ops",
        extra_workstreams=("sme",),
        columns=(
            _col("note_key", "varchar", "unique"),
            _col("case_key", "varchar", ""),
            _col("author", "varchar", "SME or FDE"),
            _col(
                "note_path",
                "text",
                "the note in builder-sme, e.g. why one line is higher than the input suggests; never the note itself",
            ),
            _col("note_sha256", "varchar", ""),
            _col("source_path", "text", "call, email or upload it came from"),
            _col("generalized_into", "varchar", "instruction_key, once generalised"),
        ),
    ),
    TableSpec(
        name="bld_instructions",
        group="Context",
        purpose="Generic instructions: the rules, exceptions and judgment calls a person uses to produce the golden output.",
        unique_key="instruction_key",
        workstream="context",
        extra_workstreams=(),
        columns=(
            _col("instruction_key", "varchar", "unique, agent:topic@version"),
            _col("agent_key", "varchar", ""),
            _enum("scope", ("org", "client", "agent")),
            _col("client_workstream_id", "varchar", "client scope only"),
            _col("page_path", "text", "kg/instructions/<agent>/<topic>.md"),
            _col("source_gaps", "text[]", "gaps it closes"),
            _col("provenance", "text", "who said it, where"),
            _enum("leak_check", ("pass", "flagged")),
            _enum("status", ("proposed", "vetted", "retired")),
            _col("compiled_into_version", "varchar", "instruction skill version"),
        ),
    ),
    TableSpec(
        name="bld_generic_prompts",
        group="Context",
        purpose="The generic prompts a blind eval may use for an agent.",
        unique_key="prompt_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("prompt_key", "varchar", "unique"),
            _col("agent_key", "varchar", ""),
            _col(
                "prompt",
                "text",
                "e.g. Prepare the 1120-PC for this client from the files in this workstream.",
            ),
            _enum("applies_to", ("per_case", "batch")),
            _enum("status", ("active", "retired")),
        ),
    ),
    TableSpec(
        name="bld_training_assets",
        group="Context",
        purpose="The explainer and training kit generated for each live version.",
        unique_key="asset_key",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("asset_key", "varchar", "unique"),
            _col("agent_key", "varchar", ""),
            _col("version_id", "varchar", ""),
            _enum("kind", ("explainer", "quickstart", "prompts", "scenarios", "faq", "whats_new")),
            _col("path", "text", ""),
            _col("generated_at", "timestamptz", ""),
        ),
    ),
    TableSpec(
        name="bld_dev_sessions",
        group="Activity",
        purpose="Coding-agent sessions per FDE, and cloud conversations.",
        unique_key="session_id",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("session_id", "varchar", "unique"),
            _col("user_id", "varchar", ""),
            _col("workspace", "varchar", "the workspace env, e.g. acme"),
            _col("agent_key", "varchar", ""),
            _enum("coding_agent", ("claude_code", "codex", "cursor", "harness", "other")),
            _col("git_sha_start", "varchar", ""),
            _col("git_sha_end", "varchar", ""),
            _col("started_at", "timestamptz", ""),
            _col("ended_at", "timestamptz", ""),
            _col("platform_cost_usd", "numeric", ""),
            _col("transcript_path", "text", ""),
        ),
    ),
    TableSpec(
        name="bld_dev_events",
        group="Activity",
        purpose="Every abcd command and loop step, with its workspace, result, and whether it ran locally or in the cloud.",
        unique_key="event_id",
        workstream="ops",
        extra_workstreams=(),
        columns=(
            _col("event_id", "varchar", "unique; a 409 on retry means already written"),
            _col("session_id", "varchar", ""),
            _col("workspace", "varchar", "the workspace env, e.g. acme"),
            _enum("ran_in", ("local", "cloud")),
            _col("actor", "varchar", "the FDE, or cloud:<process> for a cloud run"),
            _col("conversation_id", "varchar", "cloud runs only"),
            _col("ts", "timestamptz", ""),
            _col("step", "int", "loop step 1 to 10"),
            _col("kind", "varchar", "review, push, run, eval, stage, release, ..."),
            _enum("result", ("ok", "warn", "fail")),
            _col("cost_usd", "numeric", ""),
            _col("payload", "jsonb", ""),
            _col("artifact_path", "text", ""),
        ),
    ),
)

TABLES: tuple[TableSpec, ...] = tuple(_with_key(s) for s in _RAW_SPECS)
TABLES_BY_NAME: dict[str, TableSpec] = {t.name: t for t in TABLES}

# Platform-side writers have their own links (PLAN section 9): these roles take
# a list of workstream ids, the others exactly one.
MULTI_WORKSTREAM_ROLES = frozenset({WS_SME, WS_LIVE_AGENTS})


def get_spec(name: str) -> TableSpec:
    try:
        return TABLES_BY_NAME[name]
    except KeyError:
        raise UsageError(f"unknown Builder table {name!r}") from None


def assert_no_answer_columns(specs: Iterable[TableSpec] = TABLES) -> None:
    """Fail when any spec has a column that could hold an answer value."""
    for spec in specs:
        for col in spec.columns:
            if col.name in FORBIDDEN_VALUE_COLUMNS:
                raise RefusedError(
                    f"{spec.name}.{col.name}: answers never sit in a table; use *_path + *_sha256"
                )
            if col.name.endswith("_sha256") and col.type != "varchar":
                raise RefusedError(f"{spec.name}.{col.name}: a hash column must be varchar")


_BOOL_TYPES = {"boolean"}
_INT_TYPES = {"int", "bigint"}
_NUM_TYPES = {"numeric", "float8"}
_PATH_MAX = 1024


def validate_row(table: str | TableSpec, row: Mapping[str, Any], *, partial: bool = False) -> None:
    """Check one row against its spec. ``partial`` is for updates (key optional).

    Raises ``UsageError`` listing every problem: unknown columns, a missing
    unique key, enum values outside the spec, wrong scalar types, a ``*_path``
    that is not a single short line, and a ``*_sha256`` that is not 64 hex.
    """
    spec = get_spec(table) if isinstance(table, str) else table
    problems: list[str] = []
    known = {c.name: c for c in spec.columns}
    for key, value in row.items():
        col = known.get(key)
        if col is None:
            problems.append(f"unknown column {key!r}")
            continue
        if value is None:
            if not col.nullable:
                problems.append(f"{key!r} may not be null")
            continue
        if col.type == "enum":
            if value not in col.enum_values:
                problems.append(f"{key!r}={value!r} not in {list(col.enum_values)}")
        elif col.type in _BOOL_TYPES:
            if not isinstance(value, bool):
                problems.append(f"{key!r} must be a boolean")
        elif col.type in _INT_TYPES:
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append(f"{key!r} must be an integer")
        elif col.type in _NUM_TYPES:
            if isinstance(value, bool) or not isinstance(value, int | float):
                problems.append(f"{key!r} must be a number")
        elif col.type == "text[]":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                problems.append(f"{key!r} must be a list of strings")
        elif col.type in ("jsonb", "json"):
            pass
        elif not isinstance(value, str) and col.type in ("text", "varchar", "date", "timestamptz"):
            problems.append(f"{key!r} must be a string")
        if isinstance(value, str):
            if key.endswith("_sha256") and not _SHA256_RE.match(value):
                problems.append(f"{key!r} must be 64 lowercase hex characters")
            if key.endswith("_path") and ("\n" in value or len(value) > _PATH_MAX):
                problems.append(
                    f"{key!r} must be a single-line path (answers never sit in a table)"
                )
    if not partial:
        if row.get(spec.unique_key) in (None, ""):
            problems.append(f"missing unique key {spec.unique_key!r}")
    if problems:
        raise UsageError(f"{spec.name}: " + "; ".join(problems))


class _TableClient(Protocol):
    def ensure_table(self, spec: TableSpec) -> Any: ...
    def link_table_workstream(
        self, table_id: str, workstream_id: str, permission_mode: str = "write"
    ) -> Any: ...


def workstreams_for(
    spec: TableSpec, workstream_ids: Mapping[str, str | Sequence[str]]
) -> list[str]:
    """Resolve a spec's workstream roles to ids; a role with no id is skipped.

    ``ops`` and ``context`` are required for the tables whose primary role they
    are; extra roles (``sme``, ``live_agents``) may be absent early on.
    """
    out: list[str] = []
    for role in spec.workstream_roles:
        value = workstream_ids.get(role)
        if value is None:
            if role == spec.workstream:
                raise UsageError(f"{spec.name}: no workstream id given for role {role!r}")
            continue
        for ws in [value] if isinstance(value, str) else list(value):
            if ws and ws not in out:
                out.append(ws)
    return out


def ensure_all_tables(
    client: Any,
    workstream_ids: Mapping[str, str | Sequence[str]],
    specs: Iterable[TableSpec] = TABLES,
) -> dict[str, dict[str, Any]]:
    """Create or verify every table, then link each to its Builder workstreams.

    ``workstream_ids`` maps roles (``ops``, ``context``, ``sme``,
    ``live_agents``) to a workstream id or a list of ids. No dataset
    workstream is ever linked. Returns ``{table: {"table_id", "created",
    "added_columns", "linked": [ws ids]}}``.
    """
    results: dict[str, dict[str, Any]] = {}
    for spec in specs:
        result = client.ensure_table(spec)
        linked = client.link_table_workstreams(
            result.table_id, workstreams_for(spec, workstream_ids)
        )
        results[spec.name] = {
            "table_id": result.table_id,
            "created": result.created,
            "added_columns": list(result.added_columns),
            "linked": linked,
        }
    return results
