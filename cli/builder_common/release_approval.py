"""The human approval a cloud run needs for anything live (PLAN.md sections 6, 7 and 21).

In a cloud run the agent must not be able to approve its own release, so ``--yes`` is not enough:
the human's own turn in the conversation the sandbox was started for must carry

    <!--adopt:release-approval v="1" action="release" release="<agent>@v<n>" sha="<sha, 12 hex>"-->

and a rollback needs its own token, ``action="rollback"``, naming the version rolled back TO and
its commit. The action is matched, so a release token never authorises a rollback (a token with
no ``action`` is a release token). The browser-skill token of ``browser_gate`` is checked the
same way. The human types the token in the chat UI; ``abcd hitl approve-release`` (``--action
release|rollback``) and ``approve-skill`` are refused in a cloud run and are sent from a local
machine.

What the platform lets abcd rely on (adoptwebui ``origin/dev``; ``E`` is
``backend/app/routes/end_user_agent_harness.py``, ``H`` is ``backend/app/dependencies/harness_caller.py``):

- The PAT is a user. ``Caller.user_id`` is the JWT ``sub`` and ``org_id`` its ``tenantId`` (H:28-42);
  ``GET /me`` returns ``user_id``, ``email`` and ``role`` (E:414-448).
- ``POST /turns`` with an existing ``conversation_id`` owned by someone else is REFUSED with 403
  (E:733-739), EXCEPT for a workstream-shared run (one the system agent created or a person
  started with an agent's Run button): any member of that workstream may take a turn there
  (``agent_conversation_service.py:223-227``, H:120-126). The transcript is owner-only unless the
  conversation is shared (E:1746-1768).
- Only for a shared conversation does the transcript attribute turns: ``is_shared`` is true,
  user-side events carry ``author_user_id`` (E:1821-1834, ``message_attribution.py:43-101``) and
  an ``authors`` map ``{user_id: {name, email}}`` is added. In a private chat human turns are
  not attributed, so they cannot be told from the PAT's own.

An approval token is accepted only when ALL of these hold, all read from the ONE transcript the
caller fetched (no owner lookup, no second fetch):

1. the transcript is a workstream-shared run (``is_shared`` true);
2. the token is in a ``user_message`` event (text in assistant or tool output is ignored);
3. that event's ``author_user_id`` (or ``author_email``) is present and is NOT the identity the
   PAT authenticates as (JWT claims ``userId``/``sub``/``email`` decoded without verifying, plus
   ``GET /me``);
4. the conversation is the one the sandbox was started for: the id ``cloud bootstrap`` wrote to
   ``.builder/cloud.json`` (and, when ``ADOPT_CONVERSATION_ID`` is also set, it must agree).

A private chat is refused with a clear message.

Limits, written down honestly. These checks are defence in depth against an agent's mistakes, not
a security boundary. An agent with a shell can unset the sandbox variables and delete the marker:
that run is not in cloud mode, so the approval check is skipped (it is then just a local run with
the agent's own PAT). An agent with a PAT can also call the BFF directly, and if its PAT is the
same user as the human who runs the chat no turn can be told from the human's. The real boundary
is the service-user PAT: the cloud PAT belongs to a dedicated service user, a different person
from every FDE, whose permissions the platform enforces.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from cli.builder_common.cloudmode import cloud_mode, read_marker
from cli.builder_common.errors import RefusedError

RELEASE_APPROVAL_RE = re.compile(
    r'<!--\s*adopt:release-approval\s+v="1"(?:\s+action="(release|rollback)")?'
    r'\s+release="([a-z0-9][a-z0-9-]*@v\d+)"\s+sha="([0-9a-f]{12})"\s*-->'
)
ACTIONS = ("release", "rollback")

CLOUD_REFUSAL = (
    "forced releases, accepted regressions and local approvals are made by a human locally, "
    "never in a cloud run"
)
HUMAN_SENDS = "a human sends approvals from the chat UI, not the agent"


def release_approval_token(release: str, sha: str, action: str = "release") -> str:
    if action not in ACTIONS:
        raise ValueError(f"unknown approval action {action!r}")
    return f'<!--adopt:release-approval v="1" action="{action}" release="{release}" sha="{sha[:12]}"-->'


def approved_releases(
    events: Iterable[dict[str, Any]], action: str = "release"
) -> set[tuple[str, str]]:
    """``(release, sha12)`` approved for ``action`` in the member's own turns (``user_message``
    only). A token without ``action`` is a release token; a rollback needs its own."""
    out: set[tuple[str, str]] = set()
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("kind") != "user_message":
            continue
        for act, rel, sha in RELEASE_APPROVAL_RE.findall(str(ev.get("text") or "")):
            if (act or "release") == action:
                out.add((rel, sha))
    return out


def refuse_self_asserted_in_cloud(ws: Path | None = None, **flags: object) -> None:
    """Refuse ``--force-reason``, ``--lead``, ``--accept-regression``, ``--local-approval``,
    ``--approved-by``, ``--by`` ... in a cloud run: they are a person asserting a name or an
    approval, made locally. The actor in the cloud is always ``identity.current_user()``."""
    if not cloud_mode(ws):
        return
    used = [f"--{k.replace('_', '-')}" for k, v in flags.items() if v]
    if used:
        raise RefusedError(f"{CLOUD_REFUSAL} (got {', '.join(used)})")


def refuse_in_cloud(what: str, ws: Path | None = None) -> None:
    """Refuse a command that sends or records a human's approval."""
    if cloud_mode(ws):
        raise RefusedError(f"{what} is refused in a cloud run: {HUMAN_SENDS}")


def sandbox_conversation(ws: Path | None = None) -> str:
    """The conversation this sandbox was started for: the bootstrap marker's, cross-checked
    against ``ADOPT_CONVERSATION_ID``. Refuses when there is no marker id or the two differ."""
    marker = read_marker(ws) or {}
    from_marker = str(marker.get("conversation_id") or "").strip()
    from_env = os.environ.get("ADOPT_CONVERSATION_ID", "").strip()
    if not from_marker:
        raise RefusedError(
            "no bootstrap marker (.builder/cloud.json) with a conversation id: the conversation "
            "this sandbox was started for is unknown, so no approval can be checked. Re-run "
            "`abcd cloud bootstrap --replace`"
        )
    if from_env and from_env != from_marker:
        raise RefusedError(
            f"ADOPT_CONVERSATION_ID ({from_env}) differs from the conversation recorded at "
            f"bootstrap ({from_marker}); refusing to read approvals from either"
        )
    return from_marker


def human_user_events(
    client: Any,
    conversation: str,
    *,
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The ``user_message`` events of ``conversation`` that a human other than the PAT wrote.

    ``events`` is the transcript the caller already fetched (``client.transcript_events``, whose
    result says ``is_shared``); without it the transcript is fetched once here. Raises
    ``RefusedError`` when the PAT's identity or the transcript cannot be read, or when the
    conversation is not a workstream-shared run (see the module docstring).
    """
    try:
        who = client.pat_identity()
        raw = events if events is not None else client.transcript_events(conversation)
    except RefusedError:
        raise
    except Exception as e:
        raise RefusedError(
            f"cannot verify who wrote the approval in conversation {conversation}: "
            f"{type(e).__name__}: {e}"
        ) from e
    if not who:
        raise RefusedError(
            "cannot tell which user the PAT authenticates as (no userId/sub/email claim and "
            "GET /me failed), so an approval cannot be told from the agent's own message"
        )
    if not getattr(raw, "is_shared", False):
        raise RefusedError(
            f"conversation {conversation} is not a workstream-shared run: cloud approvals need a "
            "workstream-shared run; the platform attributes human turns only there"
        )
    kept: list[dict[str, Any]] = []
    for ev in raw:
        if not isinstance(ev, dict) or ev.get("kind") != "user_message":
            continue
        author = str(ev.get("author_user_id") or "")
        email = str(ev.get("author_email") or "").lower()
        if not author and not email:
            continue  # unattributed: cannot be told from the PAT's own turn
        if author in who.ids or (email and email in who.emails):
            continue
        kept.append(ev)
    return kept


def require_release_approval(
    client: Any, release: str, sha: str, ws: Path | None = None, action: str = "release"
) -> None:
    """In a cloud run, refuse unless the sandbox's conversation holds the human's token for
    ``action`` (``release`` or ``rollback``)."""
    if not cloud_mode(ws):
        return
    conversation = sandbox_conversation(ws)
    want = (release, sha[:12])
    if want in approved_releases(human_user_events(client, conversation), action):
        return
    raise RefusedError(
        f"{release}: no {action} approval from a human in conversation {conversation}. Ask the "
        f"FDE to type the approval in this chat ({HUMAN_SENDS}): "
        f"{release_approval_token(release, sha, action)}; --yes alone is not an approval in a "
        "cloud run"
    )
