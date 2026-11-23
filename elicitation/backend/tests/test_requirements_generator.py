"""Tests for app.requirements_generator — requirements.md generation."""

import pytest
from app.requirements_generator import (
    generate_requirements_md,
    _extract_workflow_steps,
    _format_timestamp,
    _looks_like_intent,
)


# ── generate_requirements_md ─────────────────────────────────────────────────

class TestGenerateRequirementsMd:
    def test_basic_output(self):
        md = generate_requirements_md("Create Customer", "Creates a new customer record")
        assert "# Requirements: Create Customer" in md
        assert "Creates a new customer record" in md

    def test_empty_description_uses_first_narration(self):
        narrations = [{"content": "This is the first narration about the process"}]
        md = generate_requirements_md("My Process", "", narrations=narrations)
        assert "first narration about the process" in md

    def test_empty_everything(self):
        md = generate_requirements_md("Empty Process", "")
        assert "# Requirements: Empty Process" in md
        assert "*No description available.*" in md

    def test_narrations_section(self):
        narrations = [
            {"content": "Step one: fill the form", "timestamp": "2026-02-23T10:00:00Z", "url": "https://app.com/form"},
            {"content": "Step two: submit it", "timestamp": "2026-02-23T10:01:00Z", "url": ""},
        ]
        md = generate_requirements_md("Test", "Desc", narrations=narrations)
        assert "## User Narrations" in md
        assert "Step one: fill the form" in md
        assert "*(on https://app.com/form)*" in md
        assert "Step two: submit it" in md

    def test_click_events_table(self):
        clicks = [
            {"event_type": "input", "field_name": "email", "value": "test@example.com", "input_type": "email"},
            {"event_type": "input", "field_name": "name", "value": "Alice", "input_type": "text"},
            {"event_type": "click", "field_name": "submit"},  # should be excluded
        ]
        md = generate_requirements_md("Test", "Desc", click_events=clicks)
        assert "## Captured Parameters" in md
        assert "| email |" in md
        assert "| name |" in md
        # click event should not appear in params table
        assert "submit" not in md.split("## Captured Parameters")[1].split("##")[0] if "## Captured Parameters" in md else True

    def test_questions_section(self):
        questions = [
            {"content": "What auth method?", "answer": "Bearer token", "status": "answered"},
            {"content": "What about rate limiting?", "answer": None, "status": "pending"},
        ]
        md = generate_requirements_md("Test", "Desc", questions=questions)
        assert "## Questions" in md
        assert "### Answered" in md
        assert "What auth method?" in md
        assert "Bearer token" in md
        assert "### Open" in md
        assert "What about rate limiting?" in md

    def test_conversation_notes(self):
        messages = [
            {"role": "human", "content": "I want to create an automated workflow for customer creation", "timestamp": "2026-02-23T10:00:00Z"},
            {"role": "human", "content": "ok", "timestamp": "2026-02-23T10:01:00Z"},  # too short / trivial
            {"role": "agent", "content": "I'll help you with that", "timestamp": "2026-02-23T10:02:00Z"},  # agent msg excluded
        ]
        md = generate_requirements_md("Test", "Desc", messages=messages)
        assert "## Notes from Conversation" in md
        assert "automated workflow" in md
        # Trivial "ok" message should be filtered
        assert "\nok\n" not in md

    def test_timeline_workflow_steps(self):
        timeline = [
            {"event_type": "url_change", "summary": "Navigated to /customers", "timestamp": "2026-02-23T10:00:00Z"},
            {"event_type": "click", "summary": "Clicked 'Add Customer'", "timestamp": "2026-02-23T10:00:01Z"},
            {"event_type": "input", "summary": "Entered customer name", "timestamp": "2026-02-23T10:00:02Z"},
        ]
        md = generate_requirements_md("Test", "Desc", timeline_events=timeline)
        assert "## Workflow Steps" in md
        assert "Navigate: Navigated to /customers" in md
        assert "Click: Clicked 'Add Customer'" in md

    def test_deduplicated_params(self):
        """Same field_name should only appear once in the params table."""
        clicks = [
            {"event_type": "input", "field_name": "username", "value": "alice", "input_type": "text"},
            {"event_type": "input", "field_name": "username", "value": "bob", "input_type": "text"},
        ]
        md = generate_requirements_md("Test", "Desc", click_events=clicks)
        # Count occurrences of "| username |" in the table (excludes header row)
        count = md.count("| username |")
        assert count == 1


# ── _extract_workflow_steps ──────────────────────────────────────────────────

class TestExtractWorkflowSteps:
    def test_url_change(self):
        events = [{"event_type": "url_change", "summary": "Went to /home"}]
        steps = _extract_workflow_steps(events)
        assert steps == ["Navigate: Went to /home"]

    def test_click(self):
        events = [{"event_type": "click", "summary": "Clicked button"}]
        steps = _extract_workflow_steps(events)
        assert steps == ["Click: Clicked button"]

    def test_network_request(self):
        events = [{"event_type": "network_request", "summary": "POST /api/customers"}]
        steps = _extract_workflow_steps(events)
        assert steps == ["API call: POST /api/customers"]

    def test_unknown_type_skipped(self):
        events = [{"event_type": "unknown", "summary": "Something"}]
        steps = _extract_workflow_steps(events)
        assert steps == []

    def test_empty_summary_skipped(self):
        events = [{"event_type": "click", "summary": ""}]
        steps = _extract_workflow_steps(events)
        assert steps == []


# ── _format_timestamp ────────────────────────────────────────────────────────

class TestFormatTimestamp:
    def test_iso_string(self):
        assert _format_timestamp("2026-02-23T14:30:45Z") == "14:30:45"

    def test_none(self):
        assert _format_timestamp(None) == "?"

    def test_empty(self):
        assert _format_timestamp("") == "?"


# ── _looks_like_intent ───────────────────────────────────────────────────────

class TestLooksLikeIntent:
    def test_intent_message(self):
        assert _looks_like_intent("I want to create a customer workflow") is True

    def test_trivial_message(self):
        assert _looks_like_intent("ok") is False
        assert _looks_like_intent("hi") is False
        assert _looks_like_intent("thanks") is False

    def test_short_message(self):
        assert _looks_like_intent("yes sure") is False

    def test_empty(self):
        assert _looks_like_intent("") is False

    def test_api_intent(self):
        assert _looks_like_intent("We need to automate the api call sequence") is True
