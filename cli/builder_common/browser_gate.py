"""The browser-skill gate abcd applies on every push of a browser skill (PLAN.md section 7).

The upload route runs no replay or approval check, so abcd applies the platform's full
rule itself:

1. ``noui_core.verify.gate.check_installable`` passes for the operations and amendments
   being pushed (``replay_approval.json`` matches their fingerprint);
2. a member approval token ``<!--adopt:skill-approval v="1" fp="<32-hex>"-->`` with that
   fingerprint sits in a ``user_message`` event of the conversation recorded in the
   approval. Same rule as adoptai-workflows ``agent_harness/activities.py``
   (``_SKILL_APPROVAL_TOKEN_RE``, ``_member_approved_fingerprints``): only events whose
   ``kind`` is ``"user_message"`` count, and their ``text`` is searched.

A replay approved outside any harness conversation has no token; it is accepted only
with ``local_approval=True`` and a named approver (recorded by the caller).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cli.builder_common.cloudmode import cloud_mode
from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.release_approval import (
    CLOUD_REFUSAL,
    human_user_events,
    sandbox_conversation,
)
from cli.harness_common.api_client import HarnessAPIError

#: Identical to the platform's ``_SKILL_APPROVAL_TOKEN_RE``.
APPROVAL_TOKEN_RE = re.compile(r'<!--\s*adopt:skill-approval\s+v="1"\s+fp="([0-9a-f]{32})"\s*-->')

#: The platform's ``_BROWSER_COMMANDS`` (activities.py): a step issuing one is browser-driven.
BROWSER_COMMANDS = frozenset(
    {
        "get_page_summary", "get_page_info", "navigate", "click_by_text", "click_element",
        "wait_for_selector", "scroll_page", "screenshot", "list_downloads", "get_download",
        "type_text", "type_into_label", "press_key", "set_checked", "select_option", "hover",
    }
)  # fmt: skip

APPROVAL_FILE = "replay_approval.json"
MANIFEST_FILE = "manifest.json"
RECORDING_BUNDLE_FILE = "recording_bundle.json"

#: Commands whose ``text`` is the value typed, not the control driven (platform's
#: ``_TYPED_VALUE_COMMANDS``).
#: Platform ``_PRECONDITION_COMMANDS`` / ``_OPEN_STEP_STATUSES``.
PRECONDITION_COMMANDS = frozenset({"await_member_at_start"})
OPEN_STEP_STATUSES = frozenset({"blocked", "error", "failed", "needs_approval"})
TYPED_VALUE_COMMANDS = frozenset({"type_into_label", "type_text", "fill", "select_option"})

_NOUI_ROOT = Path(__file__).resolve().parent.parent / "noui"


class TranscriptClient(Protocol):
    def transcript_events(self, conversation_id: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class GateResult:
    """``mode`` is ``not_browser``, ``conversation`` (token found) or ``local``."""

    browser: bool
    mode: str
    fingerprint: str | None = None
    conversation_id: str | None = None
    approver: str | None = None


class GateRefusedError(RefusedError):
    """A refusal with a stable machine-readable ``reason``.

    Reasons: ``no_approval_file``, ``not_approved``, ``fingerprint_mismatch``,
    ``amendments_unapproved``, ``no_conversation_recorded``, ``no_token``,
    ``token_fingerprint_mismatch``, ``token_not_in_user_message``, ``contradictory_manifest``,
    ``not_compiled``, ``approval_unreadable``, ``approval_no_operations``,
    ``failing_not_waived``, plus the provenance
    reasons ``steps_changed``, ``no_provenance``, ``no_recording_bundle``,
    ``bundle_unreadable``, ``bundle_mismatch``, ``no_observed_locators``,
    ``invented_locators``, ``abandoned_goals``.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


#: Back-compat name (ruff N818 wants the ``Error`` suffix).
GateRefusal = GateRefusedError


def _noui() -> tuple[Any, Any]:
    if str(_NOUI_ROOT) not in sys.path:
        sys.path.insert(0, str(_NOUI_ROOT))
    from noui_core.verify import gate, replay

    return gate, replay


def _operations(skill_dir: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((skill_dir / "operations.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ops = data.get("operations") if isinstance(data, dict) else None
    return [o for o in ops if isinstance(o, dict)] if isinstance(ops, list) else []


def is_browser_skill(skill_dir: Path) -> bool:
    """NoUI's rule (manifest style, ``call_web_browser`` tool), widened the way the
    platform widens it: browser commands in steps, or SKILL.md naming ``call_web_browser``.
    Fails closed: a hand-written skill that declares nothing is still caught."""
    skill_dir = Path(skill_dir)
    gate, _ = _noui()
    if gate.is_browser_skill(skill_dir):
        return True
    for op in _operations(skill_dir):
        for step in op.get("steps") or []:
            if isinstance(step, dict) and (step.get("command") or "") in BROWSER_COMMANDS:
                return True
    try:
        return "call_web_browser" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return False


def current_fingerprint(skill_dir: Path) -> str:
    """Fingerprint of the operations on disk (NoUI's ``operations_fingerprint``)."""
    _, replay = _noui()
    return str(replay.operations_fingerprint(_operations(Path(skill_dir))))


def approved_fingerprints(events: Iterable[dict[str, Any]]) -> set[str]:
    """Fingerprints approved in the member's own turns (``kind == "user_message"`` only)."""
    out: set[str] = set()
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("kind") != "user_message":
            continue
        out.update(m.group(1) for m in APPROVAL_TOKEN_RE.finditer(str(ev.get("text") or "")))
    return out


def recorded_conversation_id(approval: dict[str, Any]) -> str | None:
    """The conversation the approval was made in: ``conversation_id`` at the top level or
    nested under ``approval`` / ``approved_in`` / ``source`` (also ``conversationId``)."""
    for holder in (approval, *(approval.get(k) for k in ("approval", "approved_in", "source"))):
        if isinstance(holder, dict):
            for key in ("conversation_id", "conversationId"):
                value = holder.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _read_approval(skill_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((skill_dir / APPROVAL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _contradictory_manifest(
    operations: list[dict[str, Any]], manifest: dict[str, Any] | None
) -> None:
    """Platform ``_contradictory_manifest``: ``operation_style: browser`` over operations
    that do not call ``call_web_browser`` is a replay skill wearing the wrong label."""
    runtime = (manifest or {}).get("runtime")
    style = runtime.get("operation_style") if isinstance(runtime, dict) else None
    if style != "browser":
        return
    tools = {str(op.get("tool") or "") for op in operations}
    tools.discard("")
    if not tools or "call_web_browser" in tools:
        return
    raise GateRefusedError(
        "contradictory_manifest",
        f"this manifest says operation_style 'browser' but its operations call "
        f"{', '.join(sorted(tools))}. The kind is decided by which compiler ran, not by the "
        "label: re-import the capture with --browser-driven so the browser compiler runs",
    )


def _check_amendments(skill_dir: Path, approval: dict[str, Any] | None) -> None:
    """Platform ``_unapproved_amendments``: each amendment is approved by id."""
    from noui_core.compile import amendments as amendments_mod

    _noui()
    try:
        raw = (skill_dir / "amendments.json").read_text(encoding="utf-8")
    except OSError:
        raw = ""
    amendments = amendments_mod.load(raw)
    approved = set((approval or {}).get("approved_amendments") or [])
    unapproved = [a for a in amendments if amendments_mod.amendment_id(a) not in approved]
    if unapproved:
        raise GateRefusedError(
            "amendments_unapproved",
            f"{len(unapproved)} step(s) were discovered at replay rather than in the "
            "recording, and the member has not approved them. Show the member each one and "
            "record their answer per amendment (approved_amendments)",
        )


def _only_asked_to_be_at_the_start(op: dict[str, Any]) -> bool:
    """Platform ``_only_asked_to_be_at_the_start``: an operation that stopped at
    ``await_member_at_start`` never ran, so it did not fail."""
    raw = op.get("steps")
    steps = [st for st in (raw if isinstance(raw, list) else []) if isinstance(st, dict)]
    if not any(str(st.get("command") or "") in PRECONDITION_COMMANDS for st in steps):
        return False
    return not any(
        str(st.get("status") or "") in OPEN_STEP_STATUSES
        and str(st.get("command") or "") not in PRECONDITION_COMMANDS
        for st in steps
    )


def _check_file(skill_dir: Path, manifest: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    """The platform's approval-file checks in its order; returns ``(approval, fingerprint)``."""
    path = skill_dir / APPROVAL_FILE
    approval = _read_approval(skill_dir)
    if approval is None and not path.exists() and manifest is None:
        raise GateRefusedError(
            "not_compiled",
            "this browser skill was not compiled from a recording: there is no manifest.json, "
            "which the compiler always writes. A browser skill cannot be authored by hand; "
            "record the workflow, compile it, replay it and have the member approve it",
        )
    if approval is None:
        if path.exists():
            raise GateRefusedError(
                "approval_unreadable",
                f"{APPROVAL_FILE} is not readable JSON; treat it as absent and replay again",
            )
        raise GateRefusedError(
            "no_approval_file",
            "browser skill has no replay_approval.json: replay it and have a member approve it",
        )
    if not approval.get("installable"):
        raise GateRefusedError("not_approved", "the recorded replay was not approved by a member")
    fp = current_fingerprint(skill_dir)
    if approval.get("fingerprint") != fp:
        raise GateRefusedError(
            "fingerprint_mismatch",
            "the operations changed since the replay was approved: replay again and get a "
            "fresh approval",
        )
    ops_result = approval.get("operations")
    if not isinstance(ops_result, list) or not ops_result:
        raise GateRefusedError(
            "approval_no_operations",
            "the approval carries no operation results, so nothing in it shows the replay "
            "reached its goals; replay and approve again",
        )
    failing = {
        str(op.get("name"))
        for op in ops_result
        if isinstance(op, dict)
        and not op.get("goal_reached")
        and not _only_asked_to_be_at_the_start(op)
    }
    if failing:
        named = {
            str(a.get("operation"))
            for a in approval.get("accepted_failing") or []
            if isinstance(a, dict)
        }
        missing = sorted(failing - named)
        if missing:
            raise GateRefusedError(
                "failing_not_waived",
                "the approval does not cover every operation that failed: "
                f"{', '.join(missing)}. Fix what blocked them and replay, or re-approve "
                "naming each (verify_approve --accept-failing NAME)",
            )
    return approval, fp


# -- provenance (mirror of adoptai-workflows ``_replay_gate_error``) ---------


def bundle_digest(bundle: dict[str, Any]) -> str:
    """Platform ``_bundle_digest`` / noui ``bundle_digest``."""
    blob = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def steps_digest(operations: list[dict[str, Any]]) -> str:
    """Platform ``_steps_digest`` / noui ``steps_digest``: names and descriptions excluded."""
    material = [
        {
            "kind": op.get("kind") or "read",
            "steps": op.get("steps") or [],
            "parameters": [
                {"name": p.get("name"), "default": p.get("default")}
                for p in op.get("parameters") or []
            ],
        }
        for op in operations or []
    ]
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def observed_locators(bundle: dict[str, Any]) -> set[str]:
    """Platform ``_observed_locators``: every locator value the recorder saw."""
    seen: set[str] = set()

    def add(v: object) -> None:
        t = str(v or "").strip()
        if not t:
            return
        seen.add(t)
        if "|" in t:
            seen.add(t.split("|", 1)[1])

    for click in bundle.get("click_events") or []:
        if not isinstance(click, dict):
            continue
        for cand in click.get("candidates") or []:
            if isinstance(cand, dict):
                add(cand.get("value"))
        loc = click.get("locator")
        if isinstance(loc, dict):
            add(loc.get("value"))
        for key in ("selector", "text", "css_path"):
            add(click.get(key))
    return seen


def step_locators(operations: list[dict[str, Any]]) -> list[str]:
    """Platform ``_step_locators``: the locator each step targets."""
    out: list[str] = []
    for op in operations or []:
        for step in op.get("steps") or []:
            if not isinstance(step, dict):
                continue
            inner = step.get("params")
            params: dict[str, Any] = inner if isinstance(inner, dict) else step
            command = str(step.get("command") or "")
            keys = (
                ("label", "selector")
                if command in TYPED_VALUE_COMMANDS
                else ("selector", "text", "label", "value")
            )
            for key in keys:
                v = params.get(key)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())
                    break
    return out


def _scoped_inside_observed(locator: str, observed: set[str]) -> bool:
    """Platform ``_is_observed_scoped_inside_observed``: "<observed> <observed>"."""
    if " " not in locator:
        return False
    for part in observed:
        prefix = f"{part} "
        if locator.startswith(prefix) and locator[len(prefix) :] in observed:
            return True
    return False


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _stamped(manifest: dict[str, Any] | None, key: str) -> str:
    prov = (manifest or {}).get("provenance") or {}
    return str(prov.get(key) or "") if isinstance(prov, dict) else ""


def _check_steps_digest(skill_dir: Path) -> None:
    """Rewritten steps are reported first (platform: before anything else is trusted)."""
    stamped = _stamped(_read_json_dict(skill_dir / MANIFEST_FILE), "steps_sha256")
    if stamped and stamped != steps_digest(_operations(skill_dir)):
        raise GateRefusedError(
            "steps_changed",
            "these operations are not the ones compiled from the recording: their steps "
            "changed after compiling (steps_sha256 mismatch). Renaming an operation, "
            "rewording a description or adding a parameter is fine; changing what a step "
            "targets, reordering, adding or removing an operation is not. Restore the "
            "compiled operations.json, or re-record and compile again",
        )


def _check_provenance(skill_dir: Path) -> None:
    """Platform ``_provenance_error`` plus ``_abandoned_goals``: the recording bundle is the
    independent evidence, so verify against it rather than against the manifest's claim."""
    operations = _operations(skill_dir)
    stamped = _stamped(_read_json_dict(skill_dir / MANIFEST_FILE), "bundle_sha256")
    if not stamped:
        raise GateRefusedError(
            "no_provenance",
            "this browser skill carries no recording provenance: its manifest has no "
            "provenance.bundle_sha256, which the compiler always stamps. Compile it from a "
            "recording with the NoUI capture pipeline; do not add the field by hand",
        )
    try:
        raw = (skill_dir / RECORDING_BUNDLE_FILE).read_bytes()
    except OSError:
        raw = b""
    if not raw:
        raise GateRefusedError(
            "no_recording_bundle",
            f"this browser skill claims a recording but {RECORDING_BUNDLE_FILE} is not "
            "there, so nothing backs the claim. Re-compile from the capture bundle",
        )
    try:
        bundle = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        bundle = None
    if not isinstance(bundle, dict):
        raise GateRefusedError(
            "bundle_unreadable", f"{RECORDING_BUNDLE_FILE} is not a readable recording bundle"
        )
    actual = bundle_digest(bundle)
    if actual != stamped:
        raise GateRefusedError(
            "bundle_mismatch",
            "the recording does not match what this skill was compiled from "
            f"(stamped {stamped[:12]}, found {actual[:12]}); re-compile so the two agree",
        )
    observed = observed_locators(bundle)
    if not observed:
        raise GateRefusedError(
            "no_observed_locators",
            "the recording contains no observed locators, so it cannot support a browser "
            "skill; re-record actually performing the task",
        )
    invented = [
        loc
        for loc in step_locators(operations)
        if loc not in observed and not _scoped_inside_observed(loc, observed)
    ]
    if invented:
        shown = ", ".join(repr(i) for i in invented[:5])
        more = f" (and {len(invented) - 5} more)" if len(invented) > 5 else ""
        raise GateRefusedError(
            "invented_locators",
            f"these locators were never seen in the recording: {shown}{more}. A step can "
            "only drive a control the recorder watched resolve; re-record that part",
        )
    downloads = sum(
        1
        for c in bundle.get("click_events") or []
        if isinstance(c, dict) and (c.get("outcome") or {}).get("download")
    )
    if downloads and not any((op.get("kind") or "") == "download" for op in operations):
        raise GateRefusedError(
            "abandoned_goals",
            f"the recording downloaded {downloads} file(s) and this skill has no download "
            "operation, so it no longer does the thing it was recorded for",
        )


def check_browser_skill(
    skill_dir: Path,
    client: TranscriptClient | None,
    *,
    local_approval: bool = False,
    approver: str | None = None,
) -> GateResult:
    """Apply the gate. Non-browser skills pass through. Raises :class:`GateRefusedError`.

    ``local_approval`` accepts a replay with no member token (a local NoUI run); the caller
    must pass ``approver`` (the FDE) so it can be recorded in ``bld_versions``.
    """
    skill_dir = Path(skill_dir)
    manifest = _read_json_dict(skill_dir / MANIFEST_FILE)
    _contradictory_manifest(_operations(skill_dir), manifest)
    if not is_browser_skill(skill_dir):
        return GateResult(browser=False, mode="not_browser")

    _check_steps_digest(skill_dir)
    _check_amendments(skill_dir, _read_approval(skill_dir))
    if manifest is not None and not _stamped(manifest, "bundle_sha256"):
        _check_provenance(skill_dir)  # raises no_provenance: replaying cannot fix it
    approval, fp = _check_file(skill_dir, manifest)
    _check_provenance(skill_dir)
    if local_approval and not (approver and approver.strip()):
        raise UsageError("--local-approval needs the approver's identity")
    local = GateResult(True, "local", fp, recorded_conversation_id(approval), approver)

    conversation_id = recorded_conversation_id(approval)
    cloud = cloud_mode(skill_dir)
    if cloud and local_approval:
        raise RefusedError(f"{CLOUD_REFUSAL} (got --local-approval)")
    if cloud:
        sandbox = sandbox_conversation(skill_dir)
        if conversation_id != sandbox:
            raise GateRefusedError(
                "approval_conversation_mismatch",
                f"the replay was approved in conversation {conversation_id or '(none recorded)'}, "
                f"not the one this sandbox was started for ({sandbox}); a cloud run accepts only "
                "its own conversation's approval",
            )
    if conversation_id is None:
        if local_approval:
            return local
        raise GateRefusedError(
            "no_conversation_recorded",
            "replay_approval.json records no conversation, so no member approval token can be "
            "checked. If the replay was approved outside a harness chat, push with "
            "--local-approval",
        )
    if client is None:
        raise UsageError("a platform client is needed to read the approval conversation")

    try:
        events = client.transcript_events(conversation_id)
    except HarnessAPIError as e:
        if local_approval:
            return local
        raise PlatformError(
            f"could not read conversation {conversation_id} to check the approval token: {e}"
        ) from e

    trusted = human_user_events(client, conversation_id, events=events) if cloud else events
    if fp in approved_fingerprints(trusted):
        return GateResult(True, "conversation", fp, conversation_id, approver)
    if local_approval:
        return local

    anywhere = {
        m.group(1)
        for ev in events
        if isinstance(ev, dict)
        for m in APPROVAL_TOKEN_RE.finditer(json.dumps(ev).replace('\\"', '"'))
    }
    if fp in anywhere:
        raise GateRefusedError(
            "token_not_in_user_message",
            "the approval token appears only in assistant or tool output; the platform counts "
            "only the member's own user_message turns",
        )
    if approved_fingerprints(events):
        raise GateRefusedError(
            "token_fingerprint_mismatch",
            f"the conversation holds approval tokens, none for fingerprint {fp}",
        )
    raise GateRefusedError(
        "no_token",
        f"no member approval token for fingerprint {fp} in conversation {conversation_id}",
    )
