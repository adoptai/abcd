"""Requirements.md generation from captured data.

Combines narrations, messages, questions, click events, and timeline events
into a structured markdown document compatible with ABCD's requirements.md.
"""

from __future__ import annotations

from datetime import datetime


def generate_requirements_md(
    process_name: str,
    process_description: str,
    narrations: list[dict] | None = None,
    messages: list[dict] | None = None,
    questions: list[dict] | None = None,
    click_events: list[dict] | None = None,
    timeline_events: list[dict] | None = None,
) -> str:
    """Generate a requirements.md document from captured data.

    Args:
        process_name: Name of the process.
        process_description: Description of the process.
        narrations: Narration dicts with ``content``, ``timestamp``, ``url``.
        messages: Message dicts with ``role``, ``content``, ``timestamp``.
        questions: Question dicts with ``content``, ``answer``, ``status``.
        click_events: Click event dicts with ``event_type``, ``field_name``, ``value``.
        timeline_events: Timeline event dicts with ``event_type``, ``summary``, ``timestamp``.

    Returns:
        Markdown string.
    """
    narrations = narrations or []
    messages = messages or []
    questions = questions or []
    click_events = click_events or []
    timeline_events = timeline_events or []

    sections: list[str] = []

    # Header
    sections.append(f"# Requirements: {process_name}\n")

    # Overview
    sections.append("## Overview\n")
    if process_description:
        sections.append(process_description + "\n")
    elif narrations:
        sections.append(narrations[0].get("content", "") + "\n")
    else:
        sections.append("*No description available.*\n")

    # Workflow Steps (from timeline)
    workflow_steps = _extract_workflow_steps(timeline_events)
    if workflow_steps:
        sections.append("## Workflow Steps\n")
        for i, step in enumerate(workflow_steps, 1):
            sections.append(f"{i}. {step}")
        sections.append("")

    # Narrations
    if narrations:
        sections.append("## User Narrations\n")
        sections.append("Voice narrations captured during the demonstration:\n")
        for nar in narrations:
            ts = _format_timestamp(nar.get("timestamp"))
            content = nar.get("content", "")
            url = nar.get("url", "")
            line = f"- **[{ts}]** {content}"
            if url:
                line += f" *(on {url})*"
            sections.append(line)
        sections.append("")

    # Captured Parameters
    form_inputs = [
        c for c in click_events
        if c.get("event_type") in ("input", "change") and c.get("field_name")
    ]
    if form_inputs:
        sections.append("## Captured Parameters\n")
        sections.append("| Field | Example Value | Input Type |")
        sections.append("|-------|--------------|------------|")
        seen: set[str] = set()
        for click in form_inputs:
            field = click.get("field_name", "")
            if field in seen:
                continue
            seen.add(field)
            value = click.get("value", "")
            input_type = click.get("input_type", "text")
            sections.append(f"| {field} | {value} | {input_type} |")
        sections.append("")

    # Open Questions
    open_qs = [q for q in questions if q.get("status") != "resolved"]
    answered_qs = [q for q in questions if q.get("status") in ("answered", "resolved") and q.get("answer")]
    if open_qs or answered_qs:
        sections.append("## Questions\n")
        if answered_qs:
            sections.append("### Answered\n")
            for q in answered_qs:
                sections.append(f"**Q:** {q.get('content', '')}")
                sections.append(f"**A:** {q.get('answer', '')}\n")
        if open_qs:
            sections.append("### Open\n")
            for q in open_qs:
                sections.append(f"- {q.get('content', '')}")
            sections.append("")

    # Notes from Conversation
    human_messages = [
        m for m in messages
        if m.get("role") == "human" and _looks_like_intent(m.get("content", ""))
    ]
    if human_messages:
        sections.append("## Notes from Conversation\n")
        sections.append("Relevant user messages expressing intent or constraints:\n")
        for msg in human_messages[:10]:  # Cap at 10 to avoid bloat
            ts = _format_timestamp(msg.get("timestamp"))
            content = msg.get("content", "")
            # Truncate long messages
            if len(content) > 200:
                content = content[:200] + "..."
            sections.append(f"- **[{ts}]** {content}")
        sections.append("")

    return "\n".join(sections)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_workflow_steps(timeline_events: list[dict]) -> list[str]:
    """Extract a human-readable workflow step list from timeline events."""
    steps: list[str] = []
    for event in timeline_events:
        etype = event.get("event_type", "")
        summary = event.get("summary", "")

        if etype == "url_change" and summary:
            steps.append(f"Navigate: {summary}")
        elif etype == "click" and summary:
            steps.append(f"Click: {summary}")
        elif etype in ("input", "change") and summary:
            steps.append(f"Input: {summary}")
        elif etype == "narration" and summary:
            steps.append(f"Narration: {summary}")
        elif etype == "network_request" and summary:
            steps.append(f"API call: {summary}")

    return steps


def _format_timestamp(ts) -> str:
    """Format a timestamp for display."""
    if not ts:
        return "?"
    if isinstance(ts, datetime):
        return ts.strftime("%H:%M:%S")
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.strftime("%H:%M:%S")
        except (ValueError, AttributeError):
            pass
    return str(ts)[:8]


def _looks_like_intent(content: str) -> bool:
    """Heuristic: does this message express intent rather than being trivial?"""
    if not content or len(content) < 10:
        return False
    # Skip very short or boilerplate messages
    lower = content.lower().strip()
    if lower in ("hi", "hello", "thanks", "ok", "yes", "no", "sure"):
        return False
    # Intent signals
    intent_words = ("want", "need", "should", "create", "add", "update",
                    "delete", "automate", "workflow", "process", "api")
    return any(w in lower for w in intent_words)
