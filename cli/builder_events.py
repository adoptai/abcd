"""Activity logging for admins (PLAN.md R16).

The core event wrapper already appends one JSONL line per command to
``workspaces/<env>/.builder/events.jsonl``. This module turns that file into ``bld_dev_events``
rows: a sink registered with ``cli.core.events.register_sink`` uploads the unsent lines in batches
once enough have piled up, and ``abcd events flush`` uploads them on demand. A cursor file
(``.builder/events.cursor.json``) records how far the upload got; uploads are idempotent because
``event_id`` is a hash of the line. The sink never raises and never blocks the command.

``abcd events session start|end`` records a coding-agent session: ``end --transcript FILE`` uploads
the (secret-masked) transcript to ``builder-ops`` ``sessions/<fde>/<date>/<session_id>/`` and writes
the ``bld_dev_sessions`` row.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cli.builder_common import identity
from cli.builder_common.docstore import ConflictError
from cli.builder_common.errors import UsageError
from cli.builder_common.evals.protocols import sha256_hex
from cli.builder_common.orgdb import InsertResult
from cli.builder_common.tables import validate_row
from cli.builder_common.workspace import builder_state_dir
from cli.builder_context import (
    OPS_STORE,
    actor,
    check_name,
    now_iso,
    open_db,
    open_docs,
    today,
)
from cli.builder_status import step_for
from cli.core.events import mask_text, register_sink

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.output import Result
    from cli.core.registry import Registry

EVENTS_FILE = "events.jsonl"
CURSOR_FILE = "events.cursor.json"
SESSION_FILE = "session.json"
BATCH_SIZE = 200  # rows per insert call
SINK_THRESHOLD = 25  # unsent lines before the sink uploads on its own
UPLOAD_ENV_SWITCH = "ABCD_EVENTS_UPLOAD"  # set to 0 to keep events local
CODING_AGENTS = ("claude_code", "codex", "cursor", "harness", "other")

Insert = Callable[[list[dict[str, Any]]], InsertResult]


# -- buffer and cursor ------------------------------------------------------------------------


def events_path(ws: Path) -> Path:
    return builder_state_dir(ws) / EVENTS_FILE


def _cursor_path(ws: Path) -> Path:
    return builder_state_dir(ws) / CURSOR_FILE


def read_offset(ws: Path) -> int:
    try:
        return int(json.loads(_cursor_path(ws).read_text()).get("offset", 0))
    except (OSError, ValueError, AttributeError):
        return 0


def write_offset(ws: Path, offset: int) -> None:
    _cursor_path(ws).write_text(json.dumps({"offset": offset}) + "\n")


def pending_lines(ws: Path) -> list[tuple[int, bytes]]:
    """Unsent complete lines as ``(end_offset, line)``."""
    path = events_path(ws)
    try:
        with open(path, "rb") as fh:
            start = read_offset(ws)
            size = path.stat().st_size
            if start > size:  # the file was rotated or truncated
                start = 0
            fh.seek(start)
            blob = fh.read()
    except OSError:
        return []
    out: list[tuple[int, bytes]] = []
    pos = start
    for line in blob.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break  # a line still being written
        pos += len(line)
        out.append((pos, line.strip()))
    return out


# -- event -> row -----------------------------------------------------------------------------


def session_id_for(ws: Path) -> str:
    env = os.environ.get("ABCD_SESSION_ID", "").strip()
    if env:
        return env
    try:
        return str(
            json.loads((builder_state_dir(ws) / SESSION_FILE).read_text()).get("session_id", "")
        )
    except (OSError, ValueError, AttributeError):
        return os.environ.get("ADOPT_CONVERSATION_ID", "")


def event_row(event: dict[str, Any], raw: bytes, session_id: str) -> dict[str, Any]:
    """One ``bld_dev_events`` row. The id is a hash of the line, so a retry is a 409."""
    command = str(event.get("command") or "")
    code = int(event.get("exit_code", 0 if event.get("ok") else 1))
    payload: dict[str, Any] = {
        "command": command,
        "exit_code": code,
        "duration_ms": int(event.get("duration_ms") or 0),
    }
    raw_args = event.get("args")
    args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
    for key in ("agent", "name", "dataset"):
        if isinstance(args.get(key), str):
            payload.setdefault("agent" if key == "agent" else key, args[key])
    if event.get("message"):
        payload["message"] = str(event["message"])[:300]
    row: dict[str, Any] = {
        "event_id": sha256_hex(raw)[:32],
        "session_id": session_id,
        "workspace": str(event.get("env") or ""),
        "ran_in": "cloud" if event.get("mode") == "cloud" else "local",
        "actor": str(event.get("actor") or actor()),
        "ts": str(event.get("ts") or now_iso()),
        "step": step_for(command),
        "kind": command.split(" ", 1)[0] or "unknown",
        "result": "ok" if code == 0 else ("warn" if code == 1 else "fail"),
        "cost_usd": float(event.get("cost_usd") or 0.0),
        "payload": payload,
    }
    if event.get("conversation_id"):
        row["conversation_id"] = str(event["conversation_id"])
    return row


def flush(ws: Path, insert: Insert, *, limit: int | None = None) -> dict[str, Any]:
    """Upload the unsent events in batches; the cursor moves only past batches that were written."""
    lines = pending_lines(ws)
    if limit is not None:
        lines = lines[:limit]
    session = session_id_for(ws)
    sent = skipped = duplicates = 0
    error = ""
    batch: list[dict[str, Any]] = []
    batch_end = read_offset(ws)

    def write_batch() -> bool:
        nonlocal sent, duplicates, error
        if batch:
            result = insert(list(batch))
            if not result.ok:
                error = (
                    f"{len(result.failed)} row(s) not written: {result.failed[0].get('error', '')}"
                )
                return False
            sent += result.inserted
            duplicates += result.duplicates
        write_offset(ws, batch_end)
        batch.clear()
        return True

    for end, raw in lines:
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("not an object")
            row = event_row(event, raw, session)
            validate_row("bld_dev_events", row)
            batch.append(row)
        except Exception:
            skipped += 1  # a damaged or unknown line is passed over, not retried forever
        batch_end = end
        if len(batch) >= BATCH_SIZE and not write_batch():
            return {
                "sent": sent,
                "duplicates": duplicates,
                "skipped": skipped,
                "pending": len(pending_lines(ws)),
                "error": error,
            }
    write_batch()
    return {
        "sent": sent,
        "duplicates": duplicates,
        "skipped": skipped,
        "pending": len(pending_lines(ws)),
        "error": error,
    }


# -- the sink ---------------------------------------------------------------------------------


def _client_for_env(env: str) -> Any:
    from dotenv import load_dotenv

    from cli.harness_common.api_client import get_harness_client_for_env

    dotenv = Path(os.environ.get("ABCD_WORKSPACES_DIR", "")) / env / ".env"
    if dotenv.exists():
        load_dotenv(dotenv, override=False)
    return get_harness_client_for_env(env)


def _sink(event: dict[str, Any]) -> None:
    """Upload buffered events once a batch has piled up. Never raises."""
    try:
        if os.environ.get(UPLOAD_ENV_SWITCH, "1") == "0":
            return
        from cli.core.context import apply_workspaces_override

        ws = apply_workspaces_override() / str(event.get("env") or "")
        if not (ws / "builder.json").is_file() or not (ws / ".env").is_file():
            return  # not a Builder workspace with credentials: stay buffered
        if len(pending_lines(ws)) < SINK_THRESHOLD:
            return
        from cli.builder_common.orgdb import OrgDBClient

        db = OrgDBClient.from_client(_client_for_env(ws.name))
        flush(ws, lambda rows: db.insert_rows("bld_dev_events", rows))
    except Exception:
        return


def _cmd_flush(ctx: Context) -> Result:
    from cli.core.output import Result

    ws = ctx.env_dir
    waiting = len(pending_lines(ws))
    if ctx.dry_run_notice(f"would upload {waiting} buffered event(s) from {events_path(ws)}"):
        return Result.done({"dry_run": True, "pending": waiting})
    if not waiting:
        return Result.done({"sent": 0, "pending": 0}, "No buffered events.")
    db = open_db(ctx)
    res = flush(ws, lambda rows: db.insert_rows("bld_dev_events", rows))
    text = f"Uploaded {res['sent']} event(s) ({res['duplicates']} already present, {res['skipped']} skipped); {res['pending']} pending."
    if res["error"]:
        return Result(3, res, text, res["error"])
    return Result.done(res, text)


# -- sessions ---------------------------------------------------------------------------------


def _git_sha(ws: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ws, capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def redact(text: str) -> str:
    """Mask token-shaped strings and secret-named ``KEY=value`` / ``"key": "value"`` /
    ``key: value`` pairs; transcripts must not carry credentials into the Doc Store."""
    return mask_text(text)


def _session_state(ws: Path) -> dict[str, Any]:
    try:
        data = json.loads((builder_state_dir(ws) / SESSION_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _session_start(ctx: Context) -> Result:
    from cli.core.output import Result

    a = ctx.args
    ws = ctx.env_dir
    sid = check_name(a.session_id or uuid.uuid4().hex, "session id")
    state = {
        "session_id": sid,
        "started_at": now_iso(),
        "fde": actor(ws),
        "coding_agent": a.coding_agent,
        "git_sha_start": _git_sha(ws),
    }
    if ctx.dry_run_notice(f"would start session {sid}"):
        return Result.done({"dry_run": True, **state})
    builder_state_dir(ws).mkdir(parents=True, exist_ok=True)
    (builder_state_dir(ws) / SESSION_FILE).write_text(json.dumps(state, indent=2) + "\n")
    row = {
        "session_id": sid,
        "user_id": state["fde"],
        "workspace": ctx.env,
        "coding_agent": a.coding_agent,
        "git_sha_start": state["git_sha_start"],
        "started_at": state["started_at"],
    }
    data: dict[str, Any] = {**state, "recorded": False}
    try:
        res = open_db(ctx).insert_rows("bld_dev_sessions", [row])
        data["recorded"] = res.ok
    except Exception as exc:  # a hook must not fail because the platform is unreachable
        data["warning"] = f"session row not written: {exc}"
    return Result.done(data, f"Session {sid} started")


def _session_end(ctx: Context) -> Result:
    from cli.core.output import Result

    a = ctx.args
    ws = ctx.env_dir
    state = _session_state(ws)
    sid = a.session_id or state.get("session_id")
    if not sid:
        raise UsageError("no session to end: run `abcd events session start` or pass --session-id")
    sid = check_name(str(sid), "session id")
    fde = str(state.get("fde") or actor(ws))
    fde_seg = check_name(identity.slug(fde), "fde")
    started = str(state.get("started_at") or "")
    day = (started or now_iso())[:10] if started else today()
    transcript = Path(a.transcript) if a.transcript else None
    if transcript is not None and not transcript.is_file():
        raise UsageError(f"{transcript} is not a file")
    path = f"{OPS_STORE}/sessions/{fde_seg}/{day}/{sid}/transcript.jsonl"
    if ctx.dry_run_notice(
        f"would end session {sid}" + (f" and upload {transcript} to {path}" if transcript else "")
    ):
        return Result.done({"dry_run": True, "session_id": sid})
    fields: dict[str, Any] = {"ended_at": now_iso(), "git_sha_end": _git_sha(ws)}
    uploaded = False
    if transcript is not None:
        body = redact(transcript.read_text(encoding="utf-8", errors="replace"))
        try:
            open_docs(ctx).put_text(path, body)
        except ConflictError:
            pass  # already uploaded by an earlier end: the path is never rewritten
        fields["transcript_path"] = path
        uploaded = True
    db = open_db(ctx)
    base = {
        "session_id": sid,
        "user_id": fde,
        "workspace": ctx.env,
        "coding_agent": str(state.get("coding_agent") or a.coding_agent),
        "git_sha_start": str(state.get("git_sha_start") or ""),
        "started_at": started or fields["ended_at"],
    }
    res = db.insert_rows("bld_dev_sessions", [{**base, **fields}])
    res.raise_for_failures()
    if res.duplicates:
        db.update_row("bld_dev_sessions", "session_id", sid, fields)
    with_path = {
        "session_id": sid,
        "transcript_path": fields.get("transcript_path"),
        "uploaded": uploaded,
    }
    try:
        (builder_state_dir(ws) / SESSION_FILE).unlink()
    except OSError:
        pass
    return Result.done(
        with_path, f"Session {sid} ended" + (f"; transcript at {path}" if uploaded else "")
    )


def _cmd_session(ctx: Context) -> Result:
    return _session_start(ctx) if ctx.args.action == "start" else _session_end(ctx)


def register(registry: Registry) -> None:
    register_sink(_sink)
    g = registry.group("events", "Upload command events and session transcripts")
    g.command(
        "flush", _cmd_flush, help="Upload buffered command events to bld_dev_events", writes=True
    )
    p = g.command(
        "session", _cmd_session, help="Start or end a coding-agent session record", writes=True
    )
    p.add_argument("action", choices=("start", "end"))
    p.add_argument("--transcript", help="end: transcript file to upload to builder-ops")
    p.add_argument(
        "--session-id", help="Session id (default: generated at start, read from state at end)"
    )
    p.add_argument("--coding-agent", choices=CODING_AGENTS, default="claude_code")
