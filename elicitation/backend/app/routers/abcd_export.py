"""ABCD workspace export router.

Assembles a complete ABCD workspace bundle from captured data and
returns it as a downloadable zip file.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_detector import detect_auth_patterns
from app.database import get_db
from app.har_analyzer import annotate_har_entries, detect_api_groups, filter_har_entries
from app.models import (
    CaptureSession,
    ClickEvent,
    Message,
    Narration,
    Process,
    Question,
    TimelineEvent,
)
from app.profile_generator import generate_adopt_profile
from app.requirements_generator import generate_requirements_md
from app.test_case_generator import generate_test_cases
from app.wdl_generator import generate_wdl

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/abcd", tags=["abcd-export"])


# ── Export endpoint ──────────────────────────────────────────────────────────

@router.post("/processes/{process_id}/export")
async def export_abcd_workspace(
    process_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Generate and download an ABCD workspace bundle as a zip file.

    Assembles: adopt_profile.json, widdle.json, requirements.md,
    description.txt, metadata.json, test_cases/, apis/.
    """
    # ── Fetch process ────────────────────────────────────────────────────
    process = await db.get(Process, process_id)
    if not process:
        raise HTTPException(404, "Process not found")

    # ── Load all captured data ───────────────────────────────────────────
    data = await _load_process_data(db, process)

    # ── Run generators ───────────────────────────────────────────────────
    base_url = process.base_url or ""

    # Filter and annotate HAR
    filtered_har = []
    for har_log in data["har_logs"]:
        entries = filter_har_entries(har_log)
        if entries:
            annotated = annotate_har_entries(
                entries,
                timeline_events=data["timeline_dicts"],
                click_events=data["click_dicts"],
            )
            filtered_har.extend(annotated)

    # Auth detection
    auth_result = detect_auth_patterns(filtered_har)

    # API groups
    api_groups = detect_api_groups(filtered_har)

    # Adopt profile
    adopt_profile = generate_adopt_profile(
        base_url=base_url,
        har_entries=filtered_har,
        click_events=data["click_dicts"],
    )

    # WDL generation
    wdl_result = generate_wdl(
        har_entries=filtered_har,
        click_events=data["click_dicts"],
        narrations=data["narration_dicts"],
        timeline_events=data["timeline_dicts"],
        base_url=base_url,
    )

    # Requirements
    requirements_md = generate_requirements_md(
        process_name=process.name,
        process_description=process.description,
        narrations=data["narration_dicts"],
        messages=data["message_dicts"],
        questions=data["question_dicts"],
        click_events=data["click_dicts"],
        timeline_events=data["timeline_dicts"],
    )

    # Test cases
    test_cases = generate_test_cases(
        process_name=process.name,
        click_events=data["click_dicts"],
        narrations=data["narration_dicts"],
        har_entries=filtered_har,
        wdl_params=wdl_result.get("detected_params", {}),
    )

    # Description
    description = process.description or process.name

    # Metadata
    metadata = {
        "action_id": f"{_slugify(process.name)}-{process.id[:8]}",
        "name": process.name,
        "description": description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "elicitation-agent",
        "source_process_id": process.id,
        "source_project_id": process.project_id,
        "version": "draft",
        "tool_mode": False,
        "generation_stats": {
            "har_entries_total": sum(
                len(h.get("log", {}).get("entries", []))
                for h in data["har_logs"]
            ),
            "har_entries_filtered": len(filtered_har),
            "click_events": len(data["click_dicts"]),
            "narrations": len(data["narration_dicts"]),
            "messages": len(data["message_dicts"]),
            "wdl_steps": len(wdl_result.get("steps", [])),
            "test_cases": len(test_cases),
            "auth_type": auth_result["primary_auth"],
            "api_groups": len(api_groups),
        },
    }

    # API manifest
    api_manifest = {
        "apis": [
            {
                "id": _slugify(g["name"]),
                "name": g["name"],
                "base_url": g["base_url"],
                "endpoint_count": len(g.get("endpoints", [])),
                "endpoints": g.get("endpoints", []),
            }
            for g in api_groups
        ],
    }

    # ── Assemble zip ─────────────────────────────────────────────────────
    action_dir = _slugify(process.name)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{action_dir}/adopt_profile.json", _pretty_json(adopt_profile))
        zf.writestr(f"{action_dir}/widdle.json", _pretty_json({"steps": wdl_result["steps"]}))
        zf.writestr(f"{action_dir}/requirements.md", requirements_md)
        zf.writestr(f"{action_dir}/description.txt", description)
        zf.writestr(f"{action_dir}/metadata.json", _pretty_json(metadata))
        zf.writestr(f"{action_dir}/apis/manifest.json", _pretty_json(api_manifest))
        zf.writestr(f"{action_dir}/test_cases/test_cases.json", _pretty_json(test_cases))
        # Empty directories (zip needs at least a file to create dirs)
        zf.writestr(f"{action_dir}/versions/.gitkeep", "")
        zf.writestr(f"{action_dir}/traces/.gitkeep", "")

    buf.seek(0)

    filename = f"abcd-{action_dir}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Analysis-only endpoint (no zip, just JSON results) ───────────────────────

@router.get("/processes/{process_id}/analysis")
async def analyze_process(
    process_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return ABCD analysis results without generating a zip.

    Useful for MCP tools and preview in the extension.
    """
    process = await db.get(Process, process_id)
    if not process:
        raise HTTPException(404, "Process not found")

    data = await _load_process_data(db, process)
    base_url = process.base_url or ""

    filtered_har = []
    for har_log in data["har_logs"]:
        entries = filter_har_entries(har_log)
        filtered_har.extend(entries)

    auth_result = detect_auth_patterns(filtered_har)
    api_groups = detect_api_groups(filtered_har)

    wdl_result = generate_wdl(
        har_entries=filtered_har,
        click_events=data["click_dicts"],
        narrations=data["narration_dicts"],
        timeline_events=data["timeline_dicts"],
        base_url=base_url,
    )

    test_cases = generate_test_cases(
        process_name=process.name,
        click_events=data["click_dicts"],
        narrations=data["narration_dicts"],
        har_entries=filtered_har,
        wdl_params=wdl_result.get("detected_params", {}),
    )

    return {
        "process_id": process_id,
        "process_name": process.name,
        "auth": auth_result,
        "api_groups": api_groups,
        "wdl": wdl_result,
        "test_cases": test_cases,
        "stats": {
            "har_entries_filtered": len(filtered_har),
            "click_events": len(data["click_dicts"]),
            "narrations": len(data["narration_dicts"]),
        },
    }


# ── Data loading ─────────────────────────────────────────────────────────────

async def _load_process_data(db: AsyncSession, process: Process) -> dict:
    """Load all captured data for a process from the database."""

    # Capture sessions + HAR files
    cs_result = await db.execute(
        select(CaptureSession)
        .where(CaptureSession.process_id == process.id)
        .order_by(CaptureSession.started_at.asc())
    )
    capture_sessions = cs_result.scalars().all()

    _HAR_SIZE_LIMIT = 50 * 1024 * 1024  # 50 MB

    har_logs = []
    for cs in capture_sessions:
        if cs.har_file_path:
            try:
                raw_text = await asyncio.to_thread(Path(cs.har_file_path).read_text)
                if len(raw_text) > _HAR_SIZE_LIMIT:
                    logger.warning(
                        "HAR file %s exceeds size limit (%d bytes) — skipping",
                        cs.har_file_path,
                        len(raw_text),
                    )
                    continue
                har_data = json.loads(raw_text)
                har_logs.append(har_data)
            except Exception as e:
                logger.warning("Failed to read HAR file %s: %s", cs.har_file_path, e)

    # Click events
    click_result = await db.execute(
        select(ClickEvent)
        .where(ClickEvent.process_id == process.id)
        .order_by(ClickEvent.timestamp.asc())
    )
    click_events = click_result.scalars().all()
    click_dicts = [
        {
            "id": c.id,
            "event_type": c.event_type,
            "url": c.url,
            "tag_name": c.tag_name,
            "element_id": c.element_id,
            "text_content": c.text_content,
            "selector": c.selector,
            "field_name": c.field_name,
            "input_type": c.input_type,
            "value": c.value,
            "timestamp": c.timestamp.isoformat() if c.timestamp else "",
        }
        for c in click_events
    ]

    # Narrations
    nar_result = await db.execute(
        select(Narration)
        .where(Narration.process_id == process.id)
        .order_by(Narration.timestamp.asc())
    )
    narrations = nar_result.scalars().all()
    narration_dicts = [
        {
            "id": n.id,
            "content": n.content,
            "url": n.url,
            "timestamp": n.timestamp.isoformat() if n.timestamp else "",
        }
        for n in narrations
    ]

    # Timeline events
    te_result = await db.execute(
        select(TimelineEvent)
        .where(TimelineEvent.process_id == process.id)
        .order_by(TimelineEvent.timestamp.asc())
    )
    timeline_events = te_result.scalars().all()
    timeline_dicts = [
        {
            "id": t.id,
            "event_type": t.event_type,
            "summary": t.summary,
            "metadata_json": t.metadata_json,
            "timestamp": t.timestamp.isoformat() if t.timestamp else "",
        }
        for t in timeline_events
    ]

    # Messages
    msg_result = await db.execute(
        select(Message)
        .where(Message.process_id == process.id)
        .order_by(Message.timestamp.asc())
    )
    messages = msg_result.scalars().all()
    message_dicts = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "content_type": m.content_type,
            "timestamp": m.timestamp.isoformat() if m.timestamp else "",
        }
        for m in messages
    ]

    # Questions
    q_result = await db.execute(
        select(Question)
        .where(Question.process_id == process.id)
    )
    questions = q_result.scalars().all()
    question_dicts = [
        {
            "id": q.id,
            "content": q.content,
            "answer": q.answer,
            "status": q.status,
            "context_url": q.context_url,
        }
        for q in questions
    ]

    return {
        "har_logs": har_logs,
        "click_dicts": click_dicts,
        "narration_dicts": narration_dicts,
        "timeline_dicts": timeline_dicts,
        "message_dicts": message_dicts,
        "question_dicts": question_dicts,
    }


# ── Utilities ────────────────────────────────────────────────────────────────

def _pretty_json(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower())
    return slug.strip("-") or "unnamed"
