"""``abcd session`` and ``abcd hitl``: drive full harness sessions through the BFF.

Everything goes through adoptwebui routes with the PAT-derived bearer (never adoptai-workflows).
Route shapes: ``BFF_ROUTES.md`` sections 4 (turns, stream, transcript, HITL) and 5 (runs).

There is no pause/resume protocol: a held gate, a question or an escalation card is an event in a
turn's stream and the turn then ends normally. The human answer is the next ``POST /turns`` on the
same ``conversation_id``. This module classifies each finished turn as ``completed``,
``needs_input`` (with every pending item) or ``failed``, and sends the answers.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import requests

from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.release_approval import refuse_in_cloud, release_approval_token
from cli.core.output import EXIT_PLATFORM, EXIT_REFUSED, Result
from cli.harness_common.api_client import (
    UNSCOPED_WORKSTREAM_ID,
    HarnessAPIClient,
    HarnessAPIError,
)
from cli.harness_common.stream import _format_event, _inline_text
from cli.harness_common.workspace import load_run, load_workstreams_cache, run_dir, save_run

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.registry import Registry

_BASE = "/v1/end-user/agent-harness"
_TERMINAL = ("harness_turn_complete", "harness_error")
_SKILL_APPROVAL_FP = re.compile(r"[0-9a-f]{32}")
# The platform's ArtifactEvent carries relative_path (and filename, sandbox_path); no `path`
# (adoptai-workflows agent_harness/models.py ArtifactEvent).
_ARTIFACT_PATH_KEYS = ("relative_path", "path", "file_path", "filepath", "filename", "name")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class SessionClient(HarnessAPIClient):
    """``HarnessAPIClient`` plus a raw byte download (the output route returns a file)."""

    def download_output(self, conversation_id: str, workstream_id: str, path: str) -> bytes:
        url = self._url(f"{_BASE}/conversations/{quote(conversation_id, safe='')}/outputs/download")
        response = requests.request(
            "GET",
            url,
            headers=self.headers,
            params={"workstream_id": workstream_id, "path": path},
            timeout=120,
        )
        if response.status_code >= 400:
            raise HarnessAPIError("GET", url, response.status_code, response.text)
        return bytes(response.content)


def get_client(ctx: Context) -> SessionClient:
    """The env's client; a plain ``HarnessAPIClient`` is rewrapped, a ``SessionClient`` kept."""
    client = ctx.harness_client()
    if isinstance(client, SessionClient):
        return client
    return SessionClient(client._bearer_token, client.webui_endpoint)


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------


def resolve_workstream(client: HarnessAPIClient, ref: str, env: str | None) -> str:
    """A workstream name (cache, then the BFF list) or an id -> the id."""
    cache = load_workstreams_cache(env)
    if ref in cache and isinstance(cache[ref], dict) and cache[ref].get("workstream_id"):
        return str(cache[ref]["workstream_id"])
    if ref in ("general", UNSCOPED_WORKSTREAM_ID):
        return ref
    try:
        found = client.list_workstreams(search=ref)
    except HarnessAPIError:
        return ref
    items = found.get("items") if isinstance(found, dict) else found
    for item in items or []:
        if isinstance(item, dict) and ref in (item.get("id"), item.get("name")):
            return str(item["id"])
    return ref


def resolve_process(client: HarnessAPIClient, ref: str) -> dict[str, Any]:
    """A process name, display name or id -> its list row (``GET /v1/org/processes``)."""
    result = client.list_processes()
    rows = result.get("processes", []) if isinstance(result, dict) else result
    for key in ("id", "name", "display_name"):
        for row in rows or []:
            if isinstance(row, dict) and row.get(key) == ref:
                return row
    raise UsageError(f"process not found: {ref!r} (try `abcd agent list`)")


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def _event(envelope: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    event = envelope.get("event") or {}
    return event.get("event_name"), event.get("data") or {}


def _block(envelope: dict[str, Any]) -> dict[str, Any]:
    name, data = _event(envelope)
    if name != "block_delta":
        return {}
    block = data.get("block")
    return block if isinstance(block, dict) else {}


def _text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_text_of(v) for v in value)
    if isinstance(value, dict):
        return _text_of(value.get("text") or value.get("content") or "")
    return ""


def _card(block: dict[str, Any]) -> dict[str, Any]:
    """Normalise a ``builder`` block or transcript event into the card summary."""
    raw = block.get("blocks")
    blocks: list[Any] = raw if isinstance(raw, list) else []
    return {
        "builder_id": block.get("builder_id"),
        "step_id": block.get("step_id"),
        "title": block.get("title"),
        "description": block.get("description"),
        "builder_kind": block.get("builder_kind"),
        "assignee": block.get("assignee"),
        "policy_id": block.get("policy_id"),
        "blocks": blocks,
        "fields": [f for b in blocks if isinstance(b, dict) for f in (b.get("fields") or [])],
    }


def _question(block: dict[str, Any]) -> dict[str, Any]:
    return {"text": _text_of(block.get("text")), "options": block.get("options")}


def _steps_in_review(state: dict[str, Any]) -> list[dict[str, Any]]:
    inner = state.get("state") if isinstance(state.get("state"), dict) else state
    steps = inner.get("steps") if isinstance(inner, dict) else None
    return [
        {"id": s.get("id"), "title": s.get("title"), "status": s.get("status")}
        for s in steps or []
        if isinstance(s, dict) and s.get("status") == "review"
    ]


def _step_status(state: dict[str, Any], step_id: str) -> str | None:
    inner = state.get("state") if isinstance(state.get("state"), dict) else state
    for s in (inner.get("steps") if isinstance(inner, dict) else None) or []:
        if isinstance(s, dict) and s.get("id") == step_id:
            return str(s.get("status"))
    return None


_TERMINAL_NAMES = ("harness_turn_complete", "harness_error")


def turn_segment(events: list[dict[str, Any]], turn_id: str | None) -> list[dict[str, Any]]:
    """The envelopes of one turn out of a stream that may replay earlier turns.

    The real BFF streams a conversation's retained Redis events from the start (every earlier
    turn, each ending in its own ``harness_turn_complete``) and then the new turn's live events,
    plus a ``transcript`` replay. Content blocks carry no turn id, so the only boundary is a
    terminal event of another turn: everything before it is dropped. With ``turn_id=None`` the
    stream is returned unchanged.
    """
    if not turn_id:
        return events
    segment: list[dict[str, Any]] = []
    for env in events:
        segment.append(env)
        name, data = _event(env)
        if name in _TERMINAL_NAMES:
            owner = data.get("turn_id")
            if owner in (None, turn_id):
                return segment
            segment = []  # another turn's terminal: what came before was that turn's
    return segment


def classify_turn(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify a turn's stream envelopes.

    Returns ``{status, stop_reason, pending, failure, text, tool_calls, errors, ...}`` where
    ``status`` is ``completed``, ``needs_input``, ``cancelled``, ``failed`` or ``unknown`` (no terminal event).
    """
    builders: dict[str, dict[str, Any]] = {}
    questions: list[dict[str, Any]] = []
    process_state: dict[str, Any] = {}
    text: list[str] = []
    tool_calls: list[str] = []
    errors: list[dict[str, Any]] = []
    terminal: dict[str, Any] = {}
    harness_error: dict[str, Any] | None = None
    seen_errors: set[Any] = set()
    for env in events:
        name, data = _event(env)
        if name == "harness_turn_complete":
            terminal = data
        elif name == "harness_error":
            harness_error = data
        block = _block(env)
        btype = block.get("type")
        if btype == "text_delta":
            text.append(str(block.get("text") or ""))
        elif btype == "tool_use":
            tool_calls.append(str(block.get("name")))
        elif btype == "tool_result" and block.get("is_error"):
            # The platform can emit one tool_result twice (same tool_use_id): count it once.
            err = {"tool_result": _text_of(block.get("content"))[:300]}
            if block.get("tool_use_id") is None or block.get("tool_use_id") not in seen_errors:
                errors.append(err)
            seen_errors.add(block.get("tool_use_id"))
        elif btype == "builder":
            card = _card(block)
            builders[str(card["builder_id"])] = card
        elif btype == "question":
            questions.append(_question(block))
        elif btype == "process_state":
            process_state = block
    review = _steps_in_review(process_state)
    pending = {"builders": list(builders.values()), "questions": questions, "review_steps": review}
    failure = terminal.get("failure") or (harness_error if harness_error is not None else None)
    if harness_error is None and terminal.get("stop_reason") == "user_cancelled":
        status = "cancelled"  # `session stop` (or the UI): not a platform failure
    elif harness_error is not None or failure or terminal.get("completed") is False:
        status = "failed"
    elif not terminal:
        status = "unknown"
    elif pending["builders"] or questions or review:
        status = "needs_input"
    else:
        status = "completed"
    return {
        "status": status,
        "stop_reason": terminal.get("stop_reason"),
        "iterations_used": terminal.get("iterations_used"),
        "max_iterations": terminal.get("max_iterations"),
        "failure": failure,
        "harness_error": harness_error,
        "pending": pending,
        "process_state": process_state,
        "text": "".join(text),
        "tool_calls": tool_calls,
        "errors": errors,
    }


def _fields_of(event: dict[str, Any], key: str) -> dict[str, Any]:
    for candidate in (event, event.get("block"), event.get("data"), event.get("payload")):
        if isinstance(candidate, dict) and key in candidate:
            return candidate
    return event


def pending_from_transcript(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Pending HITL items of a conversation from its durable transcript events.

    A builder card is pending until a later ``user_message`` carries ``<builder_data>`` with its
    ``builder_id``; a question is pending until any later user message.
    """
    builders: dict[str, dict[str, Any]] = {}
    questions: list[dict[str, Any]] = []
    process_state: dict[str, Any] = {}
    for e in events:
        kind = e.get("kind")
        if kind == "builder":
            card = _card(_fields_of(e, "builder_id"))
            builders[str(card["builder_id"])] = card
        elif kind == "question":
            questions.append(_question(_fields_of(e, "text")))
        elif kind == "process_state":
            process_state = _fields_of(e, "steps") if "steps" in e else e
        elif kind == "user_message":
            msg = _text_of(e.get("text"))
            questions = []
            for m in re.finditer(r"<builder_data>(.*?)</builder_data>", msg, re.S):
                try:
                    answered = json.loads(m.group(1)).get("builder_id")
                except (ValueError, AttributeError):
                    continue
                builders.pop(str(answered), None)
    review = _steps_in_review(process_state)
    return {"builders": list(builders.values()), "questions": questions, "review_steps": review}


def _has_pending(pending: dict[str, Any]) -> bool:
    return bool(pending["builders"] or pending["questions"] or pending["review_steps"])


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Debugging summary of one or more turns' envelopes."""
    c = classify_turn(events)
    counts: dict[str, int] = {}
    for name in c["tool_calls"]:
        counts[name] = counts.get(name, 0) + 1
    return {
        "status": c["status"],
        "stop_reason": c["stop_reason"],
        "iterations_used": c["iterations_used"],
        "max_iterations": c["max_iterations"],
        "tool_calls": counts,
        "tool_call_total": len(c["tool_calls"]),
        "tool_errors": c["errors"],
        "harness_error": c["harness_error"],
        "failure": c["failure"],
    }


def summarize_turns(segments: list[list[dict[str, Any]]]) -> dict[str, Any]:
    """Debugging summary of a whole conversation: every turn's tool calls and errors, but the
    status, stop reason and failure of the LAST turn.

    Classifying the concatenated turns reported ``needs_input`` for a finished conversation: the
    builder card of turn 1 stays "pending" in the concatenation although turn 2 answered it.
    """
    if not segments:
        return summarize_events([])
    summary = summarize_events([e for seg in segments for e in seg])
    last = classify_turn(segments[-1])
    for key in ("status", "stop_reason", "iterations_used", "max_iterations", "failure"):
        summary[key] = last[key]
    return summary


def _render_pending(pending: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for b in pending["builders"]:
        names = ", ".join(
            f"{f.get('name')}{'*' if f.get('required') else ''}" for f in b["fields"] if f
        )
        lines.append(
            f"  builder {b['builder_id']} step={b['step_id']} {b['title'] or ''} fields: {names}"
        )
    for q in pending["questions"]:
        lines.append(f"  question: {q['text']}")
    for s in pending["review_steps"]:
        lines.append(f"  step in review: {s['id']} {s['title'] or ''}")
    return lines


# ---------------------------------------------------------------------------
# Turn plumbing
# ---------------------------------------------------------------------------


def _iter_stream(
    client: HarnessAPIClient, turn_id: str, conversation_id: str, workstream_id: str | None
) -> Iterator[dict[str, Any]]:
    return client.stream_turn(turn_id, conversation_id, workstream_id or UNSCOPED_WORKSTREAM_ID)


def consume_turn(
    client: HarnessAPIClient,
    turn_id: str,
    conversation_id: str,
    workstream_id: str | None,
    ndjson_path: Path | None,
    *,
    live: bool = True,
) -> tuple[list[dict[str, Any]], str | None]:
    """Read a turn's NDJSON to its end, echoing a live view to stderr and saving the raw lines.

    Returns ``(events, stream_error)``; ``stream_error`` is set when the stream failed.
    """
    events: list[dict[str, Any]] = []
    error: str | None = None
    out = None
    if ndjson_path is not None:
        ndjson_path.parent.mkdir(parents=True, exist_ok=True)
        out = ndjson_path.open("w")
    pending_text = False
    try:
        for env in _iter_stream(client, turn_id, conversation_id, workstream_id):
            events.append(env)
            if out:
                out.write(json.dumps(env) + "\n")
            if not live:
                continue
            inline = _inline_text(env)
            if inline is not None:
                print(inline, end="", file=sys.stderr, flush=True)
                pending_text = True
                continue
            line = _format_event(env)
            if line:
                if pending_text:
                    print(file=sys.stderr)
                    pending_text = False
                print(line, file=sys.stderr)
    except HarnessAPIError as e:
        error = str(e)
    finally:
        if out:
            out.close()
    if pending_text:
        print(file=sys.stderr)
    return events, error


def _load_session(ctx: Context, conversation_id: str) -> dict[str, Any]:
    return load_run(conversation_id, ctx.env) or {}


def _session_ctx(ctx: Context, client: HarnessAPIClient, conversation_id: str) -> dict[str, Any]:
    """Workstream/process/model of a conversation: the saved run, overridden by flags."""
    run = _load_session(ctx, conversation_id)
    a = ctx.args
    ws = getattr(a, "workstream", None)
    proc = getattr(a, "process", None)
    return {
        "workstream_id": resolve_workstream(client, ws, ctx.env)
        if ws
        else run.get("workstream_id"),
        "process_id": resolve_process(client, proc)["id"] if proc else run.get("process_id"),
        "model": getattr(a, "model", None) or run.get("model"),
        "run": run,
    }


def post_turn(
    ctx: Context,
    client: HarnessAPIClient,
    conversation_id: str,
    message: str,
    sess: dict[str, Any],
    *,
    wait: bool = True,
) -> dict[str, Any]:
    """``POST /turns`` and (with ``wait``) stream it to the end and classify it.

    Raises ``RefusedError`` for a ``requires_confirmation`` answer unless
    ``--ack-credential-warning`` was passed, and ``PlatformError`` on a missing turn id.
    """
    payload: dict[str, Any] = {"user_message": message, "conversation_id": conversation_id}
    for key in ("workstream_id", "process_id", "model"):
        if sess.get(key):
            payload[key] = sess[key]
    ack = bool(getattr(ctx.args, "ack_credential_warning", False))
    resp = client.start_turn(payload)
    if isinstance(resp, dict) and resp.get("requires_confirmation"):
        if not ack:
            raise RefusedError(
                "the BFF held the turn: the message looks like it contains a credential "
                f"({resp.get('warning') or resp.get('confirmation_type')}). Nothing was started. "
                "Re-run with --ack-credential-warning if that is intended."
            )
        resp = client.start_turn({**payload, "credential_warning_acknowledged": True})
    turn_id = resp.get("turn_id") if isinstance(resp, dict) else None
    if not turn_id:
        raise PlatformError(f"no turn_id in the /turns response: {resp}")
    out: dict[str, Any] = {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "workflow_id": resp.get("workflow_id"),
        "workstream_id": sess.get("workstream_id"),
        "process_id": sess.get("process_id"),
    }
    run = sess.get("run") or {}
    run.setdefault("run_id", conversation_id)
    run.setdefault("conversation_id", conversation_id)
    run.update({k: sess.get(k) for k in ("workstream_id", "process_id", "model")})
    run.setdefault("created_at", datetime.now(UTC).isoformat())
    turns = run.setdefault("turns", [])
    seq = len(turns) + 1
    ndjson = run_dir(conversation_id, ctx.env) / f"turn-{seq:03d}.ndjson"
    entry = {"seq": seq, "turn_id": turn_id, "user_message": message[:500], "ndjson": ndjson.name}
    out["ndjson"] = str(ndjson)
    if not wait:
        out["status"] = "started"
        turns.append(entry)
        save_run(conversation_id, run, ctx.env)
        return out
    ctx.log(f"turn {turn_id} (conversation {conversation_id})")
    events, err = consume_turn(client, turn_id, conversation_id, sess.get("workstream_id"), ndjson)
    c = classify_turn(turn_segment(events, turn_id))
    if err:
        c["stream_error"] = err
        if c["status"] == "unknown":
            c["status"] = "failed"
            c["failure"] = c["failure"] or {"stream_error": err}
    entry.update({"stop_reason": c["stop_reason"], "status": c["status"]})
    turns.append(entry)
    save_run(conversation_id, run, ctx.env)
    out.update(c)
    return out


def turn_result(out: dict[str, Any], extra_text: str = "") -> Result:
    """Render a classified turn as a ``Result`` (``failed``/``unknown`` exit 3)."""
    status = out.get("status")
    lines = [
        f"conversation: {out['conversation_id']}",
        f"turn: {out['turn_id']}",
        f"status: {status}  stop_reason={out.get('stop_reason')}",
    ]
    pending = out.get("pending") or {"builders": [], "questions": [], "review_steps": []}
    if status == "needs_input":
        lines.append("pending input:")
        lines += _render_pending(pending)
        lines.append(
            "next: abcd hitl resolve|answer ... (or abcd hitl list --conversation "
            f"{out['conversation_id']})"
        )
    if out.get("failure"):
        lines.append(f"failure: {json.dumps(out['failure'], default=str)}")
    if out.get("text"):
        lines.append("")
        lines.append(str(out["text"]).strip()[-2000:])
    if extra_text:
        lines.append(extra_text)
    text = "\n".join(lines)
    if status == "cancelled":
        return Result(EXIT_REFUSED, out, text, "turn cancelled (stop_reason user_cancelled)")
    if status in ("failed", "unknown"):
        msg = f"turn {status}"
        if out.get("stream_error"):
            msg += f": {out['stream_error']}"
        return Result(EXIT_PLATFORM, out, text, msg)
    return Result.done(out, text)


# ---------------------------------------------------------------------------
# session commands
# ---------------------------------------------------------------------------


def _cmd_start(ctx: Context) -> Result:
    a = ctx.args
    client = get_client(ctx)
    ws = resolve_workstream(client, a.workstream, ctx.env)
    proc = resolve_process(client, a.process)
    conversation_id = uuid.uuid4().hex
    if ctx.dry_run:
        return Result.done(
            {"conversation_id": conversation_id, "workstream_id": ws, "process_id": proc["id"]},
            f"DRY RUN: would start a turn on process {proc.get('name')} in workstream {ws}",
        )
    sess = {"workstream_id": ws, "process_id": proc["id"], "model": a.model, "run": {}}
    out = post_turn(ctx, client, conversation_id, a.message, sess, wait=not a.no_wait)
    return turn_result(out)


def _cmd_send(ctx: Context) -> Result:
    a = ctx.args
    client = get_client(ctx)
    if ctx.dry_run:
        return Result.done({"conversation_id": a.conversation}, "DRY RUN: would send a turn")
    sess = _session_ctx(ctx, client, a.conversation)
    return turn_result(post_turn(ctx, client, a.conversation, a.message, sess, wait=not a.no_wait))


def _transcript(client: HarnessAPIClient, conversation_id: str) -> list[dict[str, Any]]:
    body = client.get_conversation_transcript(conversation_id)
    events = body.get("events") if isinstance(body, dict) else body
    return [e for e in events or [] if isinstance(e, dict)]


def _turn_ids(run: dict[str, Any], transcript: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for t in run.get("turns") or []:
        if t.get("turn_id") and t["turn_id"] not in ids:
            ids.append(t["turn_id"])
    for e in transcript:
        tid = e.get("turn_id")
        if tid and tid not in ids:
            ids.append(str(tid))
    return ids


def _transcript_workstream(transcript: list[dict[str, Any]]) -> str | None:
    for e in transcript:
        if e.get("workstream_id"):
            return str(e["workstream_id"])
    return None


def _cmd_status(ctx: Context) -> Result:
    a = ctx.args
    client = get_client(ctx)
    run = _load_session(ctx, a.conversation)
    transcript: list[dict[str, Any]] = []
    transcript_error = None
    try:
        transcript = _transcript(client, a.conversation)
    except HarnessAPIError as e:
        transcript_error = str(e)
    known = _turn_ids(run, transcript)
    turn_id: str | None = a.turn or (known[-1] if known else None)
    data: dict[str, Any] = {"conversation_id": a.conversation, "turn_id": turn_id}
    if turn_id:
        data["turn"] = client.turn_status(turn_id, a.conversation)
    if transcript_error:
        data["transcript_error"] = transcript_error
    else:
        data["pending"] = pending_from_transcript(transcript)
    lines = [f"conversation: {a.conversation}", f"turn: {turn_id}"]
    if turn_id:
        t = data["turn"]
        lines.append(
            f"status: {t.get('status')} stage={t.get('stage')} terminal={t.get('terminal')}"
        )
        if t.get("status") == "RUNNING" and t.get("terminal"):
            # Observed live: for minutes after the final answer the workflow reports
            # RUNNING/saving_outputs with terminal=True; the stream has already ended.
            lines.append(
                "note: terminal=True means the answer is final; the workflow is only saving outputs"
            )
    if data.get("pending") and _has_pending(data["pending"]):
        lines.append("pending input:")
        lines += _render_pending(data["pending"])
    elif transcript_error:
        lines.append(f"transcript unavailable: {transcript_error}")
    return Result.done(data, "\n".join(lines))


def _artifact_entries(source: list[dict[str, Any]], from_stream: bool) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for e in source:
        block = _block(e) if from_stream else _fields_of(e, "path")
        is_artifact = (
            block.get("type") == "artifact" if from_stream else e.get("kind") == "artifact"
        )
        if not is_artifact:
            continue
        path = next((block.get(k) for k in _ARTIFACT_PATH_KEYS if block.get(k)), None)
        if path:
            found.append({"path": str(path), "artifact_id": block.get("artifact_id")})
    return found


def _summary_text(s: dict[str, Any]) -> str:
    lines = [
        f"status: {s['status']}  stop_reason={s['stop_reason']}  "
        f"iterations={s['iterations_used']}/{s['max_iterations']}",
        f"tool calls: {s['tool_call_total']} "
        + ", ".join(f"{k} x{v}" for k, v in sorted(s["tool_calls"].items())),
    ]
    for err in s["tool_errors"]:
        lines.append(f"tool error: {err['tool_result']}")
    if s["harness_error"] is not None:
        lines.append(f"harness_error: {json.dumps(s['harness_error'], default=str)}")
    if s["failure"]:
        lines.append(f"failure: {json.dumps(s['failure'], default=str)}")
    return "\n".join(lines)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str))


def _cmd_logs(ctx: Context) -> Result:
    a = ctx.args
    client = get_client(ctx)
    conv = a.conversation
    out_dir = Path(a.out) if a.out else run_dir(conv, ctx.env)
    out_dir.mkdir(parents=True, exist_ok=True)
    run = _load_session(ctx, conv)
    files: list[str] = []
    errors: dict[str, str] = {}

    transcript: list[dict[str, Any]] = []
    try:
        body = client.get_conversation_transcript(conv)
        _write_json(out_dir / "transcript.json", body)
        files.append("transcript.json")
        events_raw = body.get("events") if isinstance(body, dict) else body
        transcript = [e for e in events_raw or [] if isinstance(e, dict)]
    except HarnessAPIError as e:
        errors["transcript"] = str(e)

    for label, fetch in (
        ("run-detail", lambda: client.get_run_detail(f"cv:{conv}")),
        ("conversation-trace", lambda: client.get_conversation_trace(conv)),
    ):
        if label == "conversation-trace" and not a.trace:
            continue
        try:
            _write_json(out_dir / f"{label}.json", fetch())
            files.append(f"{label}.json")
        except HarnessAPIError as e:
            errors[label] = str(e)

    turn_ids = _turn_ids(run, transcript)
    if a.turn:
        turn_ids = [t for t in turn_ids if t == a.turn] or [a.turn]
    ws = run.get("workstream_id") or _transcript_workstream(transcript)
    segments: list[list[dict[str, Any]]] = []
    for seq, turn_id in enumerate(turn_ids, 1):
        label = f"turn-{seq:03d}"
        events, err = consume_turn(
            client, turn_id, conv, ws, out_dir / f"{label}.ndjson", live=False
        )
        files.append(f"{label}.ndjson")
        if err:
            errors[f"{label}.ndjson"] = err
        segments.append(turn_segment(events, turn_id))
        if a.trace:
            try:
                _write_json(
                    out_dir / f"{label}-trace.json",
                    client.turn_trace(turn_id, conv, ws or UNSCOPED_WORKSTREAM_ID),
                )
                files.append(f"{label}-trace.json")
            except HarnessAPIError as e:
                errors[f"{label}-trace.json"] = str(e)

    summary = summarize_turns(segments)
    _write_json(out_dir / "summary.json", summary)
    files.append("summary.json")
    data = {
        "conversation_id": conv,
        "dir": str(out_dir),
        "files": files,
        "turns": turn_ids,
        "summary": summary,
        "errors": errors,
    }
    text = "\n".join(
        [f"logs written to {out_dir} ({len(files)} files)", _summary_text(summary)]
        + [f"could not fetch {k}: {v}" for k, v in errors.items()]
    )
    return Result.done(data, text)


def _cmd_outputs(ctx: Context) -> Result:
    a = ctx.args
    client = get_client(ctx)
    conv = a.conversation
    run = _load_session(ctx, conv)
    transcript = _transcript(client, conv)
    ws = run.get("workstream_id") or _transcript_workstream(transcript) or UNSCOPED_WORKSTREAM_ID
    found = _artifact_entries(transcript, from_stream=False)
    for t in _turn_ids(run, []):
        events, _ = consume_turn(client, t, conv, ws, None, live=False)
        found += _artifact_entries(events, from_stream=True)
    unique: dict[str, dict[str, Any]] = {}
    for item in found:
        unique.setdefault(item["path"], item)
    items = list(unique.values())
    if a.path:
        items = [i for i in items if i["path"] == a.path or i["path"].endswith(a.path)]
        if not items:
            items = [{"path": a.path, "artifact_id": None}]
    data: dict[str, Any] = {"conversation_id": conv, "workstream_id": ws, "outputs": items}
    if a.download or a.path or a.out:
        out_dir = Path(a.out) if a.out else run_dir(conv, ctx.env) / "outputs"
        saved: list[str] = []
        for item in items:
            rel = Path(item["path"].lstrip("/"))
            if ".." in rel.parts:
                raise UsageError(f"refusing an artifact path with '..': {item['path']}")
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(client.download_output(conv, ws, item["path"]))
            saved.append(str(target))
        data["downloaded"] = saved
    lines = [f"{len(items)} output(s)"] + [f"  {i['path']}" for i in items]
    lines += [f"  saved {p}" for p in data.get("downloaded", [])]
    return Result.done(data, "\n".join(lines))


def _cmd_stop(ctx: Context) -> Result:
    a = ctx.args
    if ctx.dry_run:
        return Result.done({}, f"DRY RUN: would stop turn {a.turn}")
    resp = get_client(ctx).stop_turn(a.turn, a.conversation)
    return Result.done(
        {"conversation_id": a.conversation, "turn_id": a.turn, "response": resp},
        f"stop signalled for turn {a.turn}; the stream ends with stop_reason user_cancelled",
    )


# ---------------------------------------------------------------------------
# hitl commands
# ---------------------------------------------------------------------------


def _cmd_hitl_list(ctx: Context) -> Result:
    a = ctx.args
    client = get_client(ctx)
    data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    if a.conversation:
        try:
            data["conversation"] = pending_from_transcript(_transcript(client, a.conversation))
        except HarnessAPIError as e:
            errors["transcript"] = str(e)
    try:
        esc = client._request(
            "GET",
            "/v1/end-user/agent-escalations",
            params={"org_id": "me", "view": "tasks", "per_page": 100},
        )
        data["escalations"] = esc.get("data", esc) if isinstance(esc, dict) else esc
        if isinstance(esc, dict) and isinstance(esc.get("pagination"), dict):
            data["escalations_total"] = esc["pagination"].get("total")
    except HarnessAPIError as e:
        errors["escalations"] = str(e)
    try:
        runs = client.list_runs(hitl="any")
        data["runs"] = runs.get("runs", []) if isinstance(runs, dict) else runs
    except HarnessAPIError as e:
        errors["runs"] = str(e)
    if a.conversation:  # the org-wide lists are noise when you asked about one conversation
        data["escalations"] = [
            i
            for i in data.get("escalations") or []
            if isinstance(i, dict) and i.get("conversation_id") == a.conversation
        ]
        data["runs"] = [
            r
            for r in data.get("runs") or []
            if isinstance(r, dict) and r.get("id") == f"cv:{a.conversation}"
        ]
    data["errors"] = errors
    lines: list[str] = []
    if "conversation" in data:
        lines.append(f"conversation {a.conversation}:")
        lines += _render_pending(data["conversation"]) or ["  nothing pending"]
    total = None if a.conversation else data.get("escalations_total")
    shown = len(data.get("escalations") or [])
    lines.append(
        f"escalation tasks: {shown}" + (f" of {total}" if total not in (None, shown) else "")
    )
    for item in data.get("escalations") or []:
        if isinstance(item, dict):
            label = item.get("question") or item.get("display_label") or ""
            extra = f" builder={item['builder_id']}" if item.get("builder_id") else ""
            if item.get("step_name"):
                extra += f" step={item['step_name']}"
            lines.append(
                f"  {item.get('id')} conv={item.get('conversation_id')}{extra} "
                f"[{item.get('status')}] {label}"
            )
    lines.append(f"runs with HITL: {len(data.get('runs') or [])}")
    for r in data.get("runs") or []:
        if isinstance(r, dict):
            lines.append(f"  {r.get('id')} {r.get('agent_name')} {r.get('status')}")
    lines += [f"could not read {k}: {v}" for k, v in errors.items()]
    return Result.done(data, "\n".join(lines))


def _parse_values(args: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if args.values:
        try:
            parsed = json.loads(args.values)
        except ValueError as e:
            raise UsageError(f"--values is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise UsageError("--values must be a JSON object")
        values.update(parsed)
    for item in args.value or []:
        key, sep, val = item.partition("=")
        if not sep or not key:
            raise UsageError(f"--value needs k=v, got {item!r}")
        values[key] = val
    if not values:
        raise UsageError("give --values JSON or at least one --value k=v")
    return values


_BOOL_TYPES = {"checkbox", "boolean", "toggle", "switch"}
_NUMBER_TYPES = {"number", "integer", "float", "currency"}


def _coerce(field: dict[str, Any], value: Any) -> Any:
    """``--value k=v`` gives strings; send a checkbox as a boolean and a number as a number."""
    if not isinstance(value, str):
        return value
    kind = str(field.get("field_type") or "").lower()
    if kind in _BOOL_TYPES and value.strip().lower() in ("true", "false", "yes", "no", "1", "0"):
        return value.strip().lower() in ("true", "yes", "1")
    if kind in _NUMBER_TYPES:
        try:
            number = float(value)
        except ValueError:
            return value
        return int(number) if number.is_integer() and "." not in value else number
    return value


def build_submission(card: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
    """Submission blocks from a card's fields; refuses an unknown key or a missing required field.

    ``values`` is flat (``{field: value}``) or per block (``{block_id: {field: value}}``).
    """
    blocks = [b for b in card["blocks"] if isinstance(b, dict)]
    block_ids = {str(b.get("id")) for b in blocks}
    per_block = {k: v for k, v in values.items() if k in block_ids and isinstance(v, dict)}
    flat = {k: v for k, v in values.items() if k not in per_block}
    all_fields = {
        str(f.get("name")) for b in blocks for f in b.get("fields") or [] if isinstance(f, dict)
    }
    unknown = sorted(
        {k for k in flat if k not in all_fields}
        | {k for v in per_block.values() for k in v if k not in all_fields}
    )
    if unknown:
        raise UsageError(f"unknown field(s) {unknown}; the card has: {sorted(all_fields)}")
    out: list[dict[str, Any]] = []
    missing: list[str] = []
    for b in blocks:
        given = {**flat, **per_block.get(str(b.get("id")), {})}
        vals: dict[str, Any] = {}
        for f in b.get("fields") or []:
            if not isinstance(f, dict):
                continue
            name = str(f.get("name"))
            if name in given:
                vals[name] = _coerce(f, given[name])
            elif f.get("required"):
                missing.append(name)
        out.append({"id": b.get("id"), "title": b.get("title"), "values": vals})
    if missing:
        raise RefusedError(f"required field(s) missing: {sorted(set(missing))}")
    return out


def builder_message(builder_id: str, step_id: str | None, blocks: list[dict[str, Any]]) -> str:
    body = {"submission": {"blocks": blocks}, "step_id": step_id, "builder_id": builder_id}
    return "<builder_data>" + json.dumps(body, separators=(",", ":")) + "</builder_data>"


def _cmd_hitl_resolve(ctx: Context) -> Result:
    a = ctx.args
    client = get_client(ctx)
    values = _parse_values(a)
    pending = pending_from_transcript(_transcript(client, a.conversation))
    card = next((b for b in pending["builders"] if b["builder_id"] == a.builder_id), None)
    if card is None:
        raise RefusedError(
            f"no pending builder card {a.builder_id!r} in conversation {a.conversation} "
            "(see `abcd hitl list --conversation`)"
        )
    step_id = a.step_id or card.get("step_id")
    blocks = build_submission(card, values)
    message = builder_message(a.builder_id, step_id, blocks)
    if ctx.dry_run:
        return Result.done({"user_message": message}, f"DRY RUN: would send {message}")
    sess = _session_ctx(ctx, client, a.conversation)
    out = post_turn(ctx, client, a.conversation, message, sess)
    state = out.get("process_state") or {}
    status = _step_status(state, step_id) if step_id and state else None
    if step_id and status is None and out.get("status") != "failed":
        try:
            fresh = _transcript(client, a.conversation)
            now = pending_from_transcript(fresh)
            status = "review" if any(s["id"] == step_id for s in now["review_steps"]) else "left"
        except HarnessAPIError:
            status = None
    out["step_id"] = step_id
    out["step_status"] = status
    out["left_review"] = None if status is None else status != "review"
    note = (
        f"step {step_id}: {status} (left review: {out['left_review']})"
        if step_id
        else "no step_id on the card"
    )
    return turn_result(out, note)


def _cmd_hitl_answer(ctx: Context) -> Result:
    a = ctx.args
    client = get_client(ctx)
    if ctx.dry_run:
        return Result.done({"user_message": a.text}, "DRY RUN: would answer the question")
    sess = _session_ctx(ctx, client, a.conversation)
    return turn_result(post_turn(ctx, client, a.conversation, a.text, sess))


def _cmd_hitl_decide(ctx: Context) -> Result:
    a = ctx.args
    if a.decision == "approve" and not ctx.yes:
        raise RefusedError(
            "approving an escalation is a decision on the member's behalf: pass --yes"
        )
    if ctx.dry_run:
        return Result.done({}, f"DRY RUN: would record {a.decision} for {a.builder_id}")
    try:
        resp = get_client(ctx)._request(
            "POST",
            f"{_BASE}/escalations/{quote(a.builder_id, safe='')}/decide",
            json={"decision": a.decision, "note": a.note or ""},
        )
    except HarnessAPIError as e:
        if e.status_code in (404, 422):
            raise PlatformError(
                f"{e}\nOnly policy escalations (builder_id starting with 'policy-') can be "
                "decided here. A form or approval card is resolved with `abcd hitl resolve`, "
                "a text question with `abcd hitl answer`."
            ) from e
        raise
    if isinstance(resp, dict) and resp.get("recorded") is False:
        # the BFF re-opens the card when nothing was stored (adoptwebui decide route)
        raise PlatformError(
            f"the platform did not record the decision for {a.builder_id} (recorded: false); "
            "the card is still open, so retry"
        )
    note = (
        "decision recorded; the platform then starts a follow-up turn on the held "
        "conversation by itself (turn id policy-decision-<key>), where the agent sees the "
        "approval or rejection. Read it with `abcd session status <conversation>` or "
        "`abcd session logs <conversation>`; no turn from you is needed"
    )
    return Result.done(
        {"builder_id": a.builder_id, "decision": a.decision, "response": resp, "note": note},
        f"decision {a.decision} recorded for {a.builder_id}: {resp}\nNote: {note}",
    )


def approval_token(fingerprint: str) -> str:
    return f'<!--adopt:skill-approval v="1" fp="{fingerprint}"-->'


def _cmd_hitl_approve_skill(ctx: Context) -> Result:
    a = ctx.args
    refuse_in_cloud("approve-skill", ctx.env_dir)
    if not _SKILL_APPROVAL_FP.fullmatch(a.fingerprint):
        raise UsageError("--fingerprint must be 32 lowercase hex characters")
    if not ctx.yes:
        raise RefusedError(
            "approve-skill sends the member's own approval of a skill install "
            f"(fingerprint {a.fingerprint}): re-run with --yes once you reviewed it"
        )
    message = approval_token(a.fingerprint)
    if ctx.dry_run:
        return Result.done({"user_message": message}, f"DRY RUN: would send {message}")
    client = get_client(ctx)
    sess = _session_ctx(ctx, client, a.conversation)
    return turn_result(post_turn(ctx, client, a.conversation, message, sess))


_RELEASE_REF = re.compile(r"[a-z0-9][a-z0-9-]*@v\d+")
_SHA12 = re.compile(r"[0-9a-f]{12}")


def _cmd_hitl_approve_release(ctx: Context) -> Result:
    """The human's release approval, sent as the FDE's own turn in the cloud conversation."""
    a = ctx.args
    refuse_in_cloud("approve-release", ctx.env_dir)
    if not _RELEASE_REF.fullmatch(a.release):
        raise UsageError("--release must look like <agent>@v<n>")
    if not _SHA12.fullmatch(a.sha):
        raise UsageError("--sha must be the first 12 hex characters of the staged commit")
    if not ctx.yes:
        raise RefusedError(
            f"approve-release sends the member's own {getattr(a, 'action', 'release')} approval of "
            f"{a.release} at "
            f"{a.sha}: re-run with --yes once you reviewed the eval report"
        )
    message = release_approval_token(a.release, a.sha, getattr(a, "action", "release"))
    if ctx.dry_run:
        return Result.done({"user_message": message}, f"DRY RUN: would send {message}")
    client = get_client(ctx)
    sess = _session_ctx(ctx, client, a.conversation)
    return turn_result(post_turn(ctx, client, a.conversation, message, sess))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _turn_flags(p: Any) -> None:
    p.add_argument("--no-wait", action="store_true", help="Post the turn and return its ids")
    p.add_argument(
        "--ack-credential-warning",
        action="store_true",
        help="Send even if the BFF flags a credential-looking string in the message",
    )


def register(registry: Registry) -> None:
    g = registry.group("session", "Drive and debug full agent-harness sessions")
    p = g.command(
        "start", _cmd_start, help="Start a session: stream a turn, classify the result", writes=True
    )
    p.add_argument("--workstream", required=True, help="Workstream name or id")
    p.add_argument("--process", required=True, help="Process (agent) name or id")
    p.add_argument("--message", required=True, help="The user message (keep it short; no secrets)")
    p.add_argument(
        "--model",
        help="Model id (default: the org's default; `abcd session models` lists them)",
    )
    p.add_argument("--wait", action="store_true", help="Wait for the turn to end (the default)")
    _turn_flags(p)

    p = g.command("send", _cmd_send, help="Send the next turn of a conversation", writes=True)
    p.add_argument("conversation")
    p.add_argument("--message", required=True, help="The next user message")
    p.add_argument("--workstream", help="Override the saved workstream")
    p.add_argument("--process", help="Override the saved process")
    p.add_argument("--model", help="Override the model saved with the session")
    _turn_flags(p)

    p = g.command("status", _cmd_status, help="Turn status and pending input of a conversation")
    p.add_argument("conversation")
    p.add_argument("--turn", help="Turn id (default: the latest)")

    p = g.command("logs", _cmd_logs, help="Download transcript, streams, traces; print a summary")
    p.add_argument("conversation")
    p.add_argument("--turn", help="Only this turn")
    p.add_argument(
        "--out", help="Output directory (default: workspaces/<env>/harness/traces/<conv>)"
    )
    p.add_argument(
        "--trace", action="store_true", help="Also download the per-turn and conversation traces"
    )

    p = g.command("outputs", _cmd_outputs, help="List (and download) a conversation's artifacts")
    p.add_argument("conversation")
    p.add_argument("--path", help="One artifact path (downloaded)")
    p.add_argument("--out", help="Download everything listed into this directory")
    p.add_argument("--download", action="store_true", help="Download every listed artifact")

    p = g.command("stop", _cmd_stop, help="Stop a running turn", writes=True)
    p.add_argument("conversation")
    p.add_argument("turn", help="Turn id (`session start` prints it; `session status` shows it)")

    h = registry.group("hitl", "Resolve human-in-the-loop items of harness sessions")
    p = h.command("list", _cmd_hitl_list, help="Pending cards, questions, escalation tasks, runs")
    p.add_argument(
        "--conversation",
        help="Show this conversation's pending cards and keep only its tasks/runs below",
    )

    p = h.command(
        "resolve", _cmd_hitl_resolve, help="Submit a builder card as a new turn", writes=True
    )
    p.add_argument("conversation")
    p.add_argument("--builder-id", required=True, help="From `abcd hitl list` (builder <id> ...)")
    p.add_argument("--step-id", help="Gated step to close (default: the card's own step_id)")
    p.add_argument("--values", help="JSON object: {field: value} or {block_id: {field: value}}")
    p.add_argument(
        "--value",
        action="append",
        metavar="K=V",
        help="One field value (repeatable); typed from the card: checkbox -> true/false, "
        "number -> number, select -> the option value",
    )
    p.add_argument("--ack-credential-warning", action="store_true")

    p = h.command(
        "answer",
        _cmd_hitl_answer,
        help="Answer a question (a question event or a plain-text question) as the next turn",
        writes=True,
    )
    p.add_argument("conversation")
    p.add_argument("--text", required=True, help="The member's answer, sent as the next message")
    p.add_argument("--ack-credential-warning", action="store_true")

    p = h.command(
        "decide", _cmd_hitl_decide, help="Record a policy escalation decision", writes=True
    )
    p.add_argument("builder_id", help="A policy escalation id (policy-...); cards use `resolve`")
    p.add_argument("--decision", required=True, choices=["approve", "reject"])
    p.add_argument("--note", default="", help="Why (recorded with the decision)")

    p = h.command(
        "approve-skill",
        _cmd_hitl_approve_skill,
        help="Send the member's skill-install approval token",
        writes=True,
    )
    p.add_argument("conversation")
    p.add_argument("--fingerprint", required=True)

    p = h.command(
        "approve-release",
        _cmd_hitl_approve_release,
        help="Send the member's release approval token to a cloud run's conversation",
        writes=True,
    )
    p.add_argument("conversation")
    p.add_argument("--release", required=True, metavar="AGENT@vN")
    p.add_argument("--sha", required=True, help="First 12 hex characters of the staged commit")
    p.add_argument(
        "--action",
        choices=("release", "rollback"),
        default="release",
        help="release (default), or rollback: --release then names the version rolled back TO",
    )
