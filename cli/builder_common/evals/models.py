"""Result types shared by the runner, metrics, gate and report."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

MISS_CAUSES = ("missing_context", "skill_bug", "rounding", "playbook_difference", "real_error")

STATUS_SCORED = "scored"
STATUS_PENDING = "pending_judgement"
STATUS_ERROR = "error"
STATUS_ASKED = "asked"  # the agent asked a question (a blind run records it as a gap candidate)


@dataclass
class CaseResult:
    """One case in one run. Everything here is safe to persist: paths and hashes, no values.

    ``details`` (mismatches with expected values) is in memory only; it is written to the
    ``ds-<dataset>`` store as the diff file, never to rows or the workspace.
    """

    case_id: str
    passed: bool | None = None
    score: float | None = None
    status: str = STATUS_SCORED
    scorers: list[dict[str, Any]] = field(default_factory=list)
    failed_check_ids: list[str] = field(default_factory=list)
    pending_check_ids: list[str] = field(default_factory=list)
    miss_cause: str | None = None
    steps: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    hitl_count: int = 0
    observed_path: str | None = None
    observed_sha256: str | None = None
    diff_path: str | None = None
    diff_sha256: str | None = None
    source: str = "known"
    tags: list[str] = field(default_factory=list)
    question: str | None = None
    error: str | None = None
    regression_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("details", None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any] | CaseResult) -> CaseResult:
        if isinstance(d, CaseResult):
            return d
        names = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in names})
