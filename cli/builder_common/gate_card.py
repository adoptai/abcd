"""The ``abcd-release-gate`` HITL card: one place for the field names the runner agent puts on
the card and the checker reads back (PLAN.md section 21).

The runner opens the gate (``render_builder``, gated step ``approve``) with a card whose fields
are the ones ``abcd release gate-card <agent> --action release|rollback`` prints. The FDE submits
it; the platform then sends ``<builder_data>{..., "step_id":..., "builder_id":...}</builder_data>``
as the FDE's own user_message and advances the gated step to done. ``release_approval`` accepts
that submission as an approval only when the card and the submission name the exact release,
action and staged sha12 and the decision is ``approve``.
"""

from __future__ import annotations

import json
import re
from typing import Any

STEP_ID = "approve"  # the gated step of the abcd-release-gate skill
BLOCK_ID = "release-approval"
FIELD_RELEASE = "release"  # <agent>@v<n>; for a rollback, the version rolled back TO
FIELD_ACTION = "action"  # release | rollback
FIELD_SHA = "sha"  # first 12 hex characters of the staged (or rollback target) commit
FIELD_DECISION = "decision"  # the one field the FDE fills in: approve | reject
FIELD_NOTE = "note"  # optional free text
IDENTITY_FIELDS = (FIELD_RELEASE, FIELD_ACTION, FIELD_SHA)
ACTIONS = ("release", "rollback")
APPROVE = "approve"
REJECT = "reject"
APPROVING = frozenset({"approve", "approved", "true"})
REJECTING = frozenset({"reject", "rejected", "false", "deny", "denied"})
SUBMIT_LABEL = "Submit decision"

_BUILDER_DATA_RE = re.compile(r"<builder_data>(.*?)</builder_data>", re.S)
_CONTEXT_RE = re.compile(r"\b(release|action|sha)=([^\s;,]+)")


def card_spec(release: str, sha: str, action: str = "release") -> dict[str, Any]:
    """The card the runner agent puts on the gate: what ``gate-card`` prints."""
    if action not in ACTIONS:
        raise ValueError(f"unknown approval action {action!r}")
    sha12 = sha[:12]
    values = {FIELD_RELEASE: release, FIELD_ACTION: action, FIELD_SHA: sha12}
    return {
        "step_id": STEP_ID,
        "builder_kind": "form",
        "title": f"Approve {action} of {release}",
        "description": f"Approve or reject this {action}. It changes the client's live agent.",
        "submit_label": SUBMIT_LABEL,
        "block": {
            "id": BLOCK_ID,
            "title": f"{action.capitalize()} {release} at {sha12}",
            "context": f"release={release} action={action} sha={sha12}",
            "fields": [
                {
                    "name": FIELD_RELEASE,
                    "field_type": "text",
                    "required": True,
                    "read_only": True,
                    "value": release,
                },
                {
                    "name": FIELD_ACTION,
                    "field_type": "text",
                    "required": True,
                    "read_only": True,
                    "value": action,
                },
                {
                    "name": FIELD_SHA,
                    "field_type": "text",
                    "required": True,
                    "read_only": True,
                    "value": sha12,
                },
                {
                    "name": FIELD_DECISION,
                    "field_type": "select",
                    "required": True,
                    "options": [APPROVE, REJECT],
                },
                {"name": FIELD_NOTE, "field_type": "textarea", "required": False},
            ],
        },
        "values": values,
    }


def _payload(ev: dict[str, Any]) -> dict[str, Any]:
    inner = ev.get("builder")
    return inner if isinstance(inner, dict) else ev


def card_values(ev: dict[str, Any]) -> dict[str, str]:
    """``release``/``action``/``sha`` a builder event names: field values or defaults, or the
    ``release=... action=... sha=...`` line of a block's context."""
    out: dict[str, str] = {}
    p = _payload(ev)
    for block in p.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for f in block.get("fields") or []:
            if not isinstance(f, dict) or f.get("name") not in IDENTITY_FIELDS:
                continue
            for key in ("value", "default", "default_value"):
                if f.get(key) not in (None, ""):
                    out.setdefault(str(f["name"]), str(f[key]).strip())
                    break
        for k, v in _CONTEXT_RE.findall(str(block.get("context") or "")):
            out.setdefault(k, v)
    return out


def is_gate_card(ev: dict[str, Any]) -> bool:
    p = _payload(ev)
    return ev.get("kind") == "builder" and bool(p.get("builder_id")) and p.get("step_id") == STEP_ID


def card_ids(ev: dict[str, Any]) -> tuple[str, str]:
    p = _payload(ev)
    return str(p.get("builder_id") or ""), str(p.get("step_id") or "")


def submission(text: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """``(envelope, values)`` of a ``<builder_data>`` message: values of every block (or the flat
    ``values``) merged. ``None`` when there is no parseable submission."""
    m = _BUILDER_DATA_RE.search(text or "")
    if not m:
        return None
    try:
        env = json.loads(m.group(1))
    except ValueError:
        return None
    if not isinstance(env, dict):
        return None
    values: dict[str, Any] = {}
    if isinstance(env.get("values"), dict):
        values.update(env["values"])
    for b in env.get("blocks") or []:
        if isinstance(b, dict) and isinstance(b.get("values"), dict):
            values.update(b["values"])
    return env, values


def decision_is_approve(values: dict[str, Any]) -> bool:
    """An approving ``decision``/``approved`` and no rejecting one."""
    signals = [str(values[k]).strip().lower() for k in (FIELD_DECISION, "approved") if k in values]
    if not signals or any(s in REJECTING for s in signals):
        return False
    return any(s in APPROVING for s in signals)


def process_step_status(ev: dict[str, Any], step_id: str = STEP_ID) -> str | None:
    """The status a ``process_state`` event gives ``step_id`` (``None`` if it does not say)."""
    if ev.get("kind") != "process_state":
        return None
    data = ev.get("data")
    state = ev.get("state") or (data.get("state") if isinstance(data, dict) else None)
    steps = state.get("steps") if isinstance(state, dict) else None
    for s in steps or []:
        if isinstance(s, dict) and s.get("id") == step_id:
            return str(s.get("status") or "")
    return None


def gate_card_approved(
    events: list[dict[str, Any]],
    allowed: set[int],
    release: str,
    sha: str,
    action: str,
) -> bool:
    """True when ``events`` hold a gate card naming ``release``/``action``/``sha`` and, after it,
    an approving ``<builder_data>`` submission (matching ``builder_id`` and ``step_id`` and the
    same three values) in a ``user_message`` whose ``id()`` is in ``allowed``. If a later
    ``process_state`` says the step, it must say ``done``."""
    want = {FIELD_RELEASE: release, FIELD_ACTION: action, FIELD_SHA: sha[:12]}
    cards: dict[str, int] = {}  # builder_id -> index of the matching card
    for i, ev in enumerate(events):
        if isinstance(ev, dict) and is_gate_card(ev):
            if card_values(ev) == want:
                cards.setdefault(card_ids(ev)[0], i)
    for j, ev in enumerate(events):
        if not isinstance(ev, dict) or ev.get("kind") != "user_message" or id(ev) not in allowed:
            continue
        sub = submission(str(ev.get("text") or ""))
        if sub is None:
            continue
        env, values = sub
        bid, step = str(env.get("builder_id") or ""), str(env.get("step_id") or "")
        if step != STEP_ID or bid not in cards or cards[bid] > j:
            continue
        if {k: str(values.get(k, "")).strip() for k in IDENTITY_FIELDS} != want:
            continue
        if not decision_is_approve(values):
            continue
        later = [
            s
            for e in events[j + 1 :]
            if isinstance(e, dict) and (s := process_step_status(e)) is not None
        ]
        if later and later[-1] != "done":
            continue
        return True
    return False
