"""The scriptable agent behind the fake BFF's ``POST /turns``.

A test registers a script (``FakeBFFState.script(...)``) made of events built with the helpers
below. The fake runs it when a matching turn starts, stores the events for the NDJSON stream and
the transcript, and applies the HITL rules of BFF_ROUTES.md section 4:

* a ``builder`` card that names a ``step_id`` puts that process step in ``review`` and remembers
  the ``builder_id`` (a ``process_state`` event is added right after the card);
* a ``<builder_data>`` user message advances a step to ``done`` only when the step is in
  ``review`` and its recorded ``builder_id`` matches (the forgery guard); otherwise nothing moves;
* a ``builder_id`` starting with ``policy-`` also opens a policy escalation;
* skill-approval tokens are recorded from ``user_message`` text only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Event = dict[str, Any]

APPROVAL_RE = re.compile(r'<!--\s*adopt:skill-approval\s+v="1"\s+fp="([0-9a-f]{32})"\s*-->')
BUILDER_DATA_RE = re.compile(r"<builder_data>(.*?)</builder_data>", re.DOTALL)


# -- event helpers ---------------------------------------------------------------------------
def block(btype: str, **fields: Any) -> Event:
    """A ``block_delta`` event whose block has ``type == btype``."""
    return {"event_name": "block_delta", "data": {"block": {"type": btype, **fields}}}


def text(content: str) -> Event:
    return block("text_delta", text=content)


def tool_use(name: str, tool_input: dict[str, Any] | None = None, tool_id: str = "") -> Event:
    return block("tool_use", name=name, id=tool_id or f"toolu_{name}", input=tool_input or {})


def tool_result(content: str = "ok", is_error: bool = False, tool_id: str = "") -> Event:
    return block("tool_result", content=content, is_error=is_error, tool_use_id=tool_id)


def artifact(path: str, content: str | bytes = "", name: str = "") -> Event:
    """An output file. ``content`` is served by ``/outputs/download`` for this conversation."""
    return block(
        "artifact",
        path=path,
        name=name or path.rsplit("/", 1)[-1],
        content=content.decode("utf-8", "replace") if isinstance(content, bytes) else content,
    )


def builder(
    builder_id: str,
    step_id: str | None = None,
    title: str = "Review",
    fields: list[dict[str, Any]] | None = None,
    builder_kind: str = "form",
    assignee: dict[str, str] | None = None,
    description: str = "",
) -> Event:
    """A form / stepper / escalation card. With ``step_id`` the step goes to ``review``."""
    payload: dict[str, Any] = {
        "builder_id": builder_id,
        "title": title,
        "description": description,
        "builder_kind": builder_kind,
        "blocks": [
            {
                "id": "b1",
                "title": title,
                "description": "",
                "fields": fields
                or [{"name": "approved", "field_type": "boolean", "required": True, "options": []}],
            }
        ],
        "submit_label": "Submit",
        "cancel_label": "Cancel",
    }
    if step_id:
        payload["step_id"] = step_id
    if assignee:
        payload["assignee"] = assignee
    if builder_id.startswith("policy-"):
        payload["policy_id"] = builder_id
        payload["policy_name"] = title
    return block("builder", **payload)


def question(prompt: str, options: list[str] | None = None) -> Event:
    return block("question", text=prompt, options=options or [])


def process_state(steps: dict[str, str]) -> Event:
    """``{step_id: status}`` (pending|active|running|review|done|skipped|blocked|failed)."""
    return block(
        "process_state",
        state={"steps": [{"id": sid, "status": status} for sid, status in steps.items()]},
    )


# -- scripts ---------------------------------------------------------------------------------
@dataclass
class TurnCtx:
    """What a callable script sees."""

    user_message: str
    conversation_id: str
    turn_id: str
    process_id: str | None
    process_name: str | None
    workstream_id: str | None
    conversation: dict[str, Any]
    approvals: list[str]  # skill-approval fingerprints found in user messages of this conversation


Steps = list[Event] | Callable[[TurnCtx], list[Event]]


@dataclass
class Script:
    steps: Steps
    process: str | None = None  # process id or name
    message: str | re.Pattern[str] | None = None  # substring or regex on user_message
    workstream: str | None = None
    once: bool = False
    hold: bool = False  # keep the turn RUNNING until /stop (stream ends user_cancelled)
    stop_reason: str = "end_turn"
    failure: str | None = None  # emit harness_error instead of harness_turn_complete
    used: int = field(default=0)

    def matches(self, ctx: TurnCtx) -> bool:
        if self.once and self.used:
            return False
        if self.process and self.process not in (ctx.process_id, ctx.process_name):
            return False
        if self.workstream and self.workstream != ctx.workstream_id:
            return False
        if self.message is not None:
            if isinstance(self.message, re.Pattern):
                return bool(self.message.search(ctx.user_message))
            return self.message in ctx.user_message
        return True

    def events(self, ctx: TurnCtx) -> list[Event]:
        self.used += 1
        return list(self.steps(ctx) if callable(self.steps) else self.steps)


DEFAULT_STEPS: list[Event] = [text("Understood. Nothing more to do.")]


def apply_builder_data(conv: dict[str, Any], message: str, state: Any) -> list[Event]:
    """Handle ``<builder_data>`` in a user message; return the events the harness would emit."""
    out: list[Event] = []
    for raw in BUILDER_DATA_RE.findall(message):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            out.append(text("builder_data ignored: not valid JSON"))
            continue
        step_id = str(data.get("step_id") or "")
        builder_id = str(data.get("builder_id") or "")
        step = conv["steps"].get(step_id)
        if step and step["status"] == "review" and step.get("builder_id") == builder_id:
            step["status"] = "done"
            conv["builders"].setdefault(builder_id, {})["status"] = "resolved"
            conv["submissions"].append(data)
            state.resolve_escalation_from_submission(builder_id, data, conv)
            out.append(process_state({step_id: "done"}))
            out.append(text(f"Step {step_id} accepted."))
        elif builder_id.startswith("policy-") and builder_id in state.escalations:
            conv["submissions"].append(data)
            state.resolve_escalation_from_submission(builder_id, data, conv)
            conv["builders"].setdefault(builder_id, {})["status"] = "resolved"
            out.append(text(f"Escalation {builder_id} recorded."))
        else:
            conv["rejected_submissions"].append(data)
            out.append(
                text("builder_data not applied: step is not in review or builder_id differs")
            )
    return out


def absorb_events(conv: dict[str, Any], events: list[Event], state: Any) -> list[Event]:
    """Update conversation HITL state from emitted events; may append ``process_state`` events."""
    result: list[Event] = []
    for ev in events:
        result.append(ev)
        blk = (ev.get("data") or {}).get("block") or {}
        btype = blk.get("type")
        if btype == "builder":
            bid = str(blk.get("builder_id"))
            conv["builders"][bid] = {
                "status": "pending",
                "step_id": blk.get("step_id"),
                "card": blk,
            }
            if bid.startswith("policy-"):
                state.open_escalation(bid, conv, blk)
            sid = blk.get("step_id")
            if sid:
                conv["steps"][str(sid)] = {"status": "review", "builder_id": bid}
                result.append(process_state({str(sid): "review"}))
        elif btype == "process_state":
            for st in (blk.get("state") or {}).get("steps") or []:
                cur = conv["steps"].setdefault(str(st["id"]), {"status": "pending"})
                if cur.get("builder_id") and st["status"] == "done":
                    continue  # the agent can never close a gated step; a human submission does
                cur["status"] = st["status"]
        elif btype == "artifact":
            conv["files"][str(blk.get("path"))] = str(blk.get("content") or "").encode()
        elif btype == "question":
            conv["questions"].append(blk)
    return result
