"""Injectable seams for the eval library: harness driver, artifact store, row store.

Everything in ``cli/builder_common/evals`` talks to the platform only through these
protocols, so tests use in-memory fakes and the CLI wires the real BFF later.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

TURN_RUNNING = "running"
TURN_COMPLETED = "completed"
TURN_FAILED = "failed"
TURN_NEEDS_INPUT = "needs_input"  # the agent asked a question and is waiting for an answer
TERMINAL_STATES = (TURN_COMPLETED, TURN_FAILED, TURN_NEEDS_INPUT)

# Unique key of every table this package writes (PLAN.md section 9).
TABLE_KEYS: dict[str, str] = {
    "bld_datasets": "dataset_key",
    "bld_dataset_versions": "dataset_version_key",
    "bld_dataset_cases": "case_key",
    "bld_eval_runs": "eval_run_id",
    "bld_eval_results": "result_key",
    "bld_check_results": "check_result_key",
    "bld_metrics": "metric_key",
    "bld_metric_values": "value_key",
    "bld_release_criteria": "criteria_key",
    "bld_context_gaps": "gap_key",
    "bld_case_notes": "note_key",
    "bld_instructions": "instruction_key",
    "bld_generic_prompts": "prompt_key",
}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class TurnHandle:
    """What `start_turn` returns: enough to poll and fetch later, and to persist for resume."""

    turn_id: str
    conversation_id: str
    workstream: str

    def to_dict(self) -> dict[str, str]:
        return {
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "workstream": self.workstream,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TurnHandle:
        return cls(str(d["turn_id"]), str(d["conversation_id"]), str(d["workstream"]))


@dataclass
class TurnStatus:
    state: str = TURN_RUNNING
    final_text: str = ""
    question: str | None = None
    error: str | None = None
    steps: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    hitl_count: int = 0


@runtime_checkable
class HarnessDriver(Protocol):
    """Drives agent-harness turns (POST /turns, status, outputs, transcript)."""

    def start_turn(
        self,
        *,
        workstream: str,
        process_id: str,
        message: str,
        conversation_id: str | None = None,
    ) -> TurnHandle:
        """Submit a turn and return at once. Submit-only runs persist the handle."""
        ...

    def turn_status(self, handle: TurnHandle) -> TurnStatus: ...

    def fetch_outputs(self, handle: TurnHandle) -> dict[str, bytes]:
        """Files the turn produced, by relative path."""
        ...

    def fetch_transcript(self, handle: TurnHandle) -> str: ...

    def stop_turn(self, handle: TurnHandle) -> None: ...


@runtime_checkable
class ArtifactStore(Protocol):
    """Doc Store files by path. Never overwrite: callers always use a fresh path."""

    def put(self, path: str, data: bytes) -> str:
        """Store the file and return its sha256 hex."""
        ...

    def get(self, path: str) -> bytes: ...


@runtime_checkable
class RowStore(Protocol):
    """Data Store rows. `insert` is idempotent on the table's unique key (a 409 means done)."""

    def insert(self, table: str, row: dict[str, Any]) -> None: ...

    def update(self, table: str, key: str, fields: dict[str, Any]) -> None:
        """Merge ``fields`` into the row whose unique key equals ``key``."""
        ...

    def query(self, table: str, where: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...
