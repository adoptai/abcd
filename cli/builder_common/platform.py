"""Routes the Builder needs on top of ``HarnessAPIClient``.

Every route here is an adoptwebui route (never adoptai-workflows). Sources
(adoptwebui ``backend/app``, adoptai-workflows ``api/app``), as read for this module:

- ``GET /v1/end-user/agent-harness/skills`` -- ``routes/end_user_agent_harness.py``
  ``list_skills`` (``@router.get("/skills")``, merged default + org catalog; each item
  has ``source`` ``"default"|"org"``, workflows ``SkillSummary``). Any org member.
- ``GET /v1/end-user/agent-harness/skills/{name}`` -- same file ``get_skill``;
  returns ``{name, source, description, body, aux_files: [{path, size}]}``
  (workflows ``api/app/routers/agent_harness.py`` ``SkillDetailResponse``).
- ``GET /v1/end-user/agent-harness/skills/{name}/files?path=`` -- ``get_skill_file``;
  returns ``{path, content}``, text only, truncated; 415 for a binary file, 404 for a
  missing one (workflows ``get_skill_file_endpoint``).
- ``POST /v1/end-user/agent-harness/skills`` -- ``upload_skill``, Admin only. The
  existing ``HarnessAPIClient.upload_skill`` with ``replace=False`` is the first
  upload (the platform's default-tier collision guard only fires then).
- ``GET|PUT /v1/org/processes/{id}/write-workstreams`` -- ``routes/process.py``
  ``get_write_grants`` / ``set_write_grants``, Admin only. PUT body is
  ``WriteWorkstreamSet {workstream_ids: [str], folder_prefix: str}``; the prefix is
  required unless ``workstream_ids`` is empty (``ProcessService._normalise_folder_prefix``).
  GET returns ``{grants: [{workstream_id, folder_prefix}], workstream_ids, folder_prefix}``.
- ``GET /v1/end-user/agent-harness/conversations/{id}/transcript`` -- ``routes/
  end_user_agent_harness.py`` ``get_conversation_transcript``; owner-only; the body has
  ``events`` (``kind == "user_message"`` events carry the member's ``text``).
- ``GET /v1/end-user/agent-harness/default-skills/{name}`` -- Adopt operators only.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from cli.harness_common.api_client import HarnessAPIClient, HarnessAPIError

_BASE = "/v1/end-user/agent-harness"


@dataclass(frozen=True)
class SkillFileRead:
    """One aux file as the platform returns it.

    ``state`` is ``text`` (``content`` is set; ``truncated`` says the platform cut it
    short), ``binary`` (HTTP 415, no preview) or ``missing`` (404).
    """

    path: str
    size: int
    state: str
    content: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class SkillSnapshot:
    name: str
    source: str
    description: str
    skill_md: str
    aux: dict[str, SkillFileRead] = field(default_factory=dict)

    @property
    def partial_paths(self) -> list[str]:
        """Aux files that could not be read back whole (binary, missing, truncated)."""
        return sorted(p for p, f in self.aux.items() if f.state != "text" or f.truncated)


class Transcript(list[dict[str, Any]]):
    """Events of a conversation plus the transcript's ``is_shared`` flag."""

    is_shared: bool = False


@dataclass(frozen=True)
class PatIdentity:
    """Who the PAT authenticates as: every id and email the platform could know it by.

    The platform's ``Caller.user_id`` is the JWT ``sub`` (``harness_caller.py:30``); PAT tokens
    have been seen with ``userId`` != ``sub`` (PLAN.md section 6), so all of them count, plus the
    ``user_id`` of ``GET /me`` when that answers. More ids only make the check stricter.
    """

    ids: frozenset[str]
    emails: frozenset[str]

    def __bool__(self) -> bool:
        return bool(self.ids or self.emails)


def jwt_claims(token: str) -> dict[str, Any]:
    """Payload of a JWT, decoded WITHOUT verifying it (the platform verifies; this only reads who
    the token says it is). ``{}`` for anything that is not a JWT."""
    try:
        part = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _items(catalog: Any) -> list[dict[str, Any]]:
    if isinstance(catalog, list):
        return [c for c in catalog if isinstance(c, dict)]
    if isinstance(catalog, dict):
        for key in ("items", "skills", "data"):
            if isinstance(catalog.get(key), list):
                return [c for c in catalog[key] if isinstance(c, dict)]
    return []


def rebuild_skill_md(detail: Mapping[str, Any], fallback_name: str) -> str:
    """A SKILL.md from ``GET /skills/{name}``.

    The platform returns the frontmatter's ``description`` and ``output_*`` fields and the body
    *without* the frontmatter (live, 2026-10-29), so a pulled skill would not upload again as it
    came back. The ``name``, ``description`` and non-default output fields are restored; a
    ``process:`` block and any other key are not served and are lost (``ws pull`` marks that).
    """
    body = str(detail.get("body") or "")
    if body.lstrip().startswith("---"):
        return body  # a platform build that serves the raw file
    import yaml

    meta: dict[str, Any] = {
        "name": str(detail.get("name") or fallback_name),
        "description": str(detail.get("description") or ""),
    }
    if detail.get("output_type") and detail["output_type"] != "text":
        meta["output_type"] = detail["output_type"]
    if detail.get("output_schema"):
        meta["output_schema"] = detail["output_schema"]
    dumped = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=10**9)
    return f"---\n{dumped}---\n\n{body.lstrip(chr(10))}"


class BuilderPlatformClient(HarnessAPIClient):
    """``HarnessAPIClient`` plus the routes the Builder uses."""

    @classmethod
    def wrap(cls, client: HarnessAPIClient) -> BuilderPlatformClient:
        return cls(client._bearer_token, client.webui_endpoint)

    # -- Turns ----------------------------------------------------------

    def stop_turn(self, turn_id: str, conversation_id: str) -> dict[str, Any]:  # type: ignore[override]
        """``POST /turns/{id}/stop`` requires ``{conversation_id}``; the base class sends none (422)."""
        return self._request(
            "POST",
            f"{_BASE}/turns/{quote(turn_id, safe='')}/stop",
            json={"conversation_id": conversation_id},
        )

    # -- Skills ---------------------------------------------------------

    def list_catalog(self) -> list[dict[str, Any]]:
        """The merged default + org skill catalog, as a list of items."""
        return _items(self.list_skills())

    def list_default_skills(self) -> list[dict[str, Any]]:
        """Catalog items from the default ("Built-in") tier."""
        return [s for s in self.list_catalog() if s.get("source") == "default"]

    def default_skill_names(self) -> set[str]:
        return {str(s["name"]) for s in self.list_default_skills() if s.get("name")}

    def default_skill_version(self, skill_name: str) -> dict[str, Any] | None:
        """Version pointer (``bundle_version``) of a default skill, or None when the
        caller may not read it (the route is Adopt-operator only: 401/403) or it is absent."""
        try:
            return self.get_default_skill(skill_name)
        except HarnessAPIError as e:
            if e.status_code in (401, 403, 404):
                return None
            raise

    def get_skill_file_raw(self, skill_name: str, path: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{_BASE}/skills/{quote(skill_name, safe='')}/files",
            params={"path": path},
        )

    def read_skill_file(self, skill_name: str, path: str, size: int = 0) -> SkillFileRead:
        """Read one aux file, mapping 404 and 415 to ``missing`` and ``binary``."""
        try:
            body = self.get_skill_file_raw(skill_name, path)
        except HarnessAPIError as e:
            if e.status_code == 415:
                return SkillFileRead(path, size, "binary")
            if e.status_code == 404:
                return SkillFileRead(path, size, "missing")
            raise
        content = str(body.get("content") or "")
        truncated = size > 0 and len(content.encode("utf-8")) < size
        return SkillFileRead(path, size, "text", content, truncated)

    def get_skill_files(self, skill_name: str) -> SkillSnapshot:
        """SKILL.md plus every aux file the platform will give back as text."""
        detail = self.get_skill(skill_name)
        aux: dict[str, SkillFileRead] = {}
        for entry in detail.get("aux_files") or []:
            path = str(entry.get("path"))
            aux[path] = self.read_skill_file(skill_name, path, int(entry.get("size") or 0))
        return SkillSnapshot(
            name=str(detail.get("name") or skill_name),
            source=str(detail.get("source") or ""),
            description=str(detail.get("description") or ""),
            skill_md=rebuild_skill_md(detail, skill_name),
            aux=aux,
        )

    def upload_skill_first(
        self,
        skill_name: str,
        skill_md_b64: str,
        aux_files: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """First upload of a name: ``replace`` is false, so the platform refuses a name
        that collides with a default skill (PLAN.md section 4)."""
        return self.upload_skill(skill_name, skill_md_b64, aux_files, replace=False)

    # -- Processes ------------------------------------------------------

    def find_process_by_name(self, name: str) -> dict[str, Any] | None:
        result = self.list_processes()
        rows = result.get("processes", result) if isinstance(result, dict) else result
        for row in rows or []:
            if isinstance(row, dict) and row.get("name") == name:
                return row
        return None

    def get_write_workstreams(self, process_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/org/processes/{process_id}/write-workstreams")

    def set_write_workstreams(
        self, process_id: str, workstream_ids: list[str], folder_prefix: str = ""
    ) -> dict[str, Any]:
        """Full replace. ``["*"]`` grants every workstream; a prefix is required
        unless ``workstream_ids`` is empty."""
        return self._request(
            "PUT",
            f"/v1/org/processes/{process_id}/write-workstreams",
            json={"workstream_ids": workstream_ids, "folder_prefix": folder_prefix},
        )

    # -- Identity ------------------------------

    def pat_identity(self) -> PatIdentity:
        """Ids and emails of the identity this client's token authenticates as."""
        claims = jwt_claims(self._bearer_token)
        ids = {str(claims[k]) for k in ("userId", "sub") if claims.get(k)}
        emails = {str(claims["email"]).lower()} if claims.get("email") else set()
        try:
            me = self._request("GET", f"{_BASE}/me")
        except HarnessAPIError:
            me = None
        if isinstance(me, dict):
            if me.get("user_id"):
                ids.add(str(me["user_id"]))
            if me.get("email"):
                emails.add(str(me["email"]).lower())
        return PatIdentity(frozenset(ids), frozenset(emails))

    # -- Conversations --------------------------------------------------

    def transcript_events(self, conversation_id: str) -> Transcript:
        """Events of a conversation the caller can read (``get_conversation_transcript``); the
        list's ``is_shared`` says whether it is a workstream-shared run (the only kind in which
        the platform attributes human turns)."""
        body = self.get_conversation_transcript(conversation_id)
        events = body.get("events") if isinstance(body, dict) else body
        emails: dict[str, str] = {}
        authors = body.get("authors") if isinstance(body, dict) else None
        for uid, info in (authors or {}).items() if isinstance(authors, dict) else []:
            if isinstance(info, dict) and info.get("email"):
                emails[str(uid)] = str(info["email"]).lower()
        out = Transcript(e for e in events or [] if isinstance(e, dict))
        out.is_shared = bool(body.get("is_shared")) if isinstance(body, dict) else False
        for e in out:  # attribution the platform stamped on shared conversations
            if e.get("author_user_id") and "author_email" not in e:
                e["author_email"] = emails.get(str(e["author_user_id"]), "")
        return out
