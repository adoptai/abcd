"""An in-process fake of the adoptwebui BFF, for offline end-to-end tests of the ``abcd`` CLI.

A threaded ``http.server`` on 127.0.0.1 (random port) that implements the routes of
``BFF_ROUTES.md`` (see ``tests/integration/README.md`` for the coverage table) with their request
and response shapes, status codes and auth rules. All state is in memory (``FakeBFFState``).

It is a model of the documented contract, NOT the real platform: live staging was not available.
Every request is logged in ``state.requests`` (BFF routes only: anything outside ``/v1/`` is a
404 and still logged, so a test can prove the CLI stays on the BFF path and never sends
``X-Workflows-Secret``). Presigned storage URLs live under ``/_presigned/`` and are logged
separately in ``state.storage_requests``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import json
import posixpath
import re
import secrets
import sqlite3
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from email import message_from_bytes
from email.policy import default as email_default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from tests.integration.fake_agent import (
    APPROVAL_RE,
    BUILDER_DATA_RE,
    DEFAULT_STEPS,
    Event,
    Script,
    Steps,
    TurnCtx,
    absorb_events,
    apply_builder_data,
)

BASE = "/v1/end-user/agent-harness"
ORG_ID = "adopt-test-org"
SIGNING_KEY = b"fake-bff-signing-key-not-a-secret"
DEFAULT_CLIENT_ID = "fake-pat-client"
DEFAULT_SECRET = "fake-pat-secret-value"
LOCKED_CLIENT_ID = "locked-pat-client"
LOCKED_MESSAGE = "User is locked out after too many failed attempts. Ask an admin to unlock."
TRUNC_MARKER = "\n[... truncated: only the first 262144 bytes shown]"
MAX_FILE_BYTES = 256 * 1024
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
COLUMN_TYPES = {
    "text", "varchar", "citext", "int", "bigint", "numeric", "float8", "timestamptz",
    "timestamp", "date", "time", "uuid", "boolean", "jsonb", "json", "text[]", "enum", "vector",
}  # fmt: skip
IMPLICIT_COLUMNS = ("id", "created_at", "updated_at", "is_deleted", "source_id", "created_by")
CREDENTIAL_RE = re.compile(
    r"AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"
    r"|sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|Authorization:\s*Bearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_jwt(claims: dict[str, Any]) -> str:
    """An HS256-shaped JWT (three base64url parts, signed with the fake's own key)."""
    head = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(claims).encode())
    sig = hmac.new(SIGNING_KEY, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64url(sig)}"


def decode_jwt(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    want = hmac.new(SIGNING_KEY, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url(want), parts[2]):
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, binascii.Error):
        return None
    if not isinstance(claims, dict) or claims.get("exp", 0) < time.time():
        return None
    return claims


class HTTPError(Exception):
    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


def missing(*fields: str) -> HTTPError:
    return HTTPError(
        422,
        [{"loc": ["body", f], "msg": "Field required", "type": "missing"} for f in fields],
    )


class Raw:
    """A non-JSON response."""

    def __init__(
        self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None
    ):
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = headers or {}


class Stream:
    """A streamed NDJSON response (a generator of lines, closed by the server)."""

    def __init__(self, lines: Iterator[str]) -> None:
        self.lines = lines


class Req:
    def __init__(self, st: FakeBFFState, method: str, path: str, query: dict[str, list[str]],
                 body: bytes, headers: dict[str, str], params: dict[str, str]) -> None:  # fmt: skip
        self.st = st
        self.method = method
        self.path = path
        self.query = query
        self.raw = body
        self.headers = headers
        self.params = params
        self.claims: dict[str, Any] = {}

    def q(self, name: str, default: str | None = None) -> str | None:
        vals = self.query.get(name)
        return vals[0] if vals else default

    def need_q(self, name: str) -> str:
        val = self.q(name)
        if not val:
            raise HTTPError(
                422, [{"loc": ["query", name], "msg": "Field required", "type": "missing"}]
            )
        return val

    def qint(self, name: str, default: int, lo: int = 1, hi: int = 10_000) -> int:
        val = self.q(name)
        try:
            n = int(val) if val is not None else default
        except ValueError:
            raise HTTPError(422, f"{name} must be an integer") from None
        if not lo <= n <= hi:
            raise HTTPError(422, f"{name} must be between {lo} and {hi}")
        return n

    def json(self, required: bool = True) -> dict[str, Any]:
        if not self.raw:
            if required:
                raise HTTPError(
                    422, [{"loc": ["body"], "msg": "Field required", "type": "missing"}]
                )
            return {}
        try:
            data = json.loads(self.raw)
        except json.JSONDecodeError:
            raise HTTPError(422, "Invalid JSON body") from None
        if not isinstance(data, dict):
            raise HTTPError(422, "JSON body must be an object")
        return data

    @property
    def tenant(self) -> str:
        return str(self.claims["tenantId"])

    @property
    def sub(self) -> str:
        return str(self.claims.get("sub") or "")

    @property
    def user_id(self) -> str:
        """Harness identity: userId, falling back to sub (BFF_ROUTES section 0)."""
        return str(self.claims.get("userId") or self.claims.get("sub") or "")

    @property
    def is_admin(self) -> bool:
        return "Admin" in (self.claims.get("roles") or [])

    @property
    def email(self) -> str:
        return str(self.claims.get("email") or "")


Handler = Callable[[Req], Any]
ROUTES: list[tuple[str, re.Pattern[str], str, Handler]] = []


def route(method: str, pattern: str, auth: str = "jwt") -> Callable[[Handler], Handler]:
    """Register ``method pattern``. ``auth``: none | jwt | admin | adopt (@adopt.ai email)."""
    regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn: Handler) -> Handler:
        ROUTES.append((method, regex, auth, fn))
        return fn

    return deco


def _page(items: list[Any], rq: Req, key: str = "items", max_size: int = 100) -> dict[str, Any]:
    page = rq.qint("page", 1)
    size = rq.qint("page_size", 25, 1, max_size)
    start = (page - 1) * size
    return {
        key: items[start : start + size],
        "total": len(items),
        "page": page,
        "page_size": size,
        "total_pages": max(1, -(-len(items) // size)),
    }


# =============================================================================================
# State
# =============================================================================================
class FakeBFFState:
    """Everything the fake BFF remembers. Tests may read and seed it directly."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.base_url = ""
        self.org_id = ORG_ID
        # PAT exchange
        self.pats: dict[str, dict[str, Any]] = {}
        self.exchanges: list[str] = []  # client ids, one entry per successful exchange
        self.failed_exchanges = 0
        self.add_pat(DEFAULT_CLIENT_ID, DEFAULT_SECRET)
        self.add_pat(LOCKED_CLIENT_ID, "locked-secret", locked=True)
        # logs
        self.requests: list[dict[str, Any]] = []
        self.storage_requests: list[dict[str, Any]] = []
        # skills and plugins
        self.org_skills: dict[str, dict[str, Any]] = {}
        self.default_skills: dict[str, dict[str, Any]] = {}
        self.plugins: dict[str, dict[str, Any]] = {}
        # processes and workstreams
        self.processes: dict[str, dict[str, Any]] = {}
        self.process_ws: dict[str, list[str]] = {}
        self.write_ws: dict[str, dict[str, Any]] = {}
        self.workstreams: dict[str, dict[str, Any]] = {}
        self.grants: dict[str, set[str]] = {}  # user id -> workstream ids (non-admin access)
        # doc store
        self.stores: dict[str, dict[str, Any]] = {}
        self.documents: dict[str, dict[str, dict[str, Any]]] = {}
        self.batches: dict[str, dict[str, Any]] = {}
        self.objects: dict[str, bytes] = {}
        # org-db
        self.tables: dict[str, dict[str, Any]] = {}
        self.table_rows: dict[str, list[dict[str, Any]]] = {}
        self.row_seq = 0
        # turns
        self.conversations: dict[str, dict[str, Any]] = {}
        self.turns: dict[str, dict[str, Any]] = {}
        self.scripts: list[Script] = []
        self.approvals: list[dict[str, Any]] = []
        self.turn_requests: list[dict[str, Any]] = []  # POST /turns bodies (credentials kept out)
        self.escalations: dict[str, dict[str, Any]] = {}
        self.pipeline_escalations: dict[str, dict[str, Any]] = {}
        # pipelines, vault
        self.pipelines: dict[str, dict[str, Any]] = {}
        self.pipeline_runs: dict[str, list[dict[str, Any]]] = {}
        self.vault: dict[str, dict[str, Any]] = {}

    # -- seeding -------------------------------------------------------------------------
    def add_pat(self, client_id: str, secret: str, *, user_id: str | None = None,
                email: str = "fde@example.test", roles: list[str] | None = None,
                locked: bool = False) -> None:  # fmt: skip
        self.pats[client_id] = {
            "secret": secret,
            "user_id": user_id or f"user-{client_id}",
            "email": email,
            "roles": ["Admin"] if roles is None else roles,
            "locked": locked,
        }

    def script(self, steps: Steps | None = None, **kw: Any) -> Script:
        """Register a turn script. See ``fake_agent.Script`` for the keyword arguments."""
        s = Script(steps=DEFAULT_STEPS if steps is None else steps, **kw)
        with self.lock:
            self.scripts.insert(0, s)  # latest registration wins
        return s

    def add_default_skill(self, name: str, description: str = "A built-in skill",
                          bundle_version: str = "1") -> None:  # fmt: skip
        md = f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n"
        self.default_skills[name] = _skill_record(md.encode(), {}, bundle_version=bundle_version)

    def add_workstream(self, name: str) -> dict[str, Any]:
        ws = {
            "id": uuid.uuid4().hex, "name": name, "description": None, "custom_properties": None,
            "member_count": 0, "members": [], "created_by": "seed", "created_at": now(),
            "is_deleted": False,
        }  # fmt: skip
        self.workstreams[ws["id"]] = ws
        self._default_store_for(ws)
        return ws

    def _default_store_for(self, ws: dict[str, Any]) -> None:
        # Real BFF (observed live 2026-10-29): POST /v1/org/workstreams also creates a store
        # named "Workstream Files" already linked to the new workstream.
        sid = uuid.uuid4().hex
        self.stores[sid] = _store_record(sid, "Workstream Files", self.org_id, [ws["id"]])
        self.documents[sid] = {}

    def add_pipeline_run(self, pipeline_id: str, status: str = "succeeded",
                         error: str | None = None) -> dict[str, Any]:  # fmt: skip
        rid = uuid.uuid4().hex[:16]
        run = {
            "id": rid, "pipeline_id": pipeline_id, "started_at": now(), "finished_at": now(),
            "duration_ms": 1200, "status": status, "total_records_synced": 0,
            "error_message": error, "error_step_id": None, "workstream_id": None,
            "workstream_name": None, "metadata": {}, "has_detail": True, "connectors": [],
        }  # fmt: skip
        self.pipeline_runs.setdefault(pipeline_id, []).append(run)
        return run

    def add_pipeline_escalation(self, pipeline_id: str, run_id: str, question: str) -> str:
        eid = "esc-" + uuid.uuid4().hex[:10]
        self.pipeline_escalations[eid] = {
            "id": eid, "pipeline_run_id": run_id, "pipeline_id": pipeline_id,
            "conversation_id": None, "builder_id": None, "assigned_to_kind": "role",
            "assigned_to_value": "reviewer", "question": question, "status": "awaiting_human",
            "created_at": now(),
        }  # fmt: skip
        for run in self.pipeline_runs.get(pipeline_id, []):
            if run["id"] == run_id:
                run["status"] = "waiting_on_hitl"
        return eid

    # -- HITL ----------------------------------------------------------------------------
    def open_escalation(self, builder_id: str, conv: dict[str, Any], blk: dict[str, Any]) -> None:
        if builder_id in self.escalations:
            return  # a card re-sent by the agent never re-opens a decided escalation
        self.escalations[builder_id] = {
            "builder_id": builder_id, "conversation_id": conv["id"], "title": blk.get("title"),
            "subtitle": blk.get("description") or "", "metadata": {}, "status": "open",
            "assigned_to_kind": (blk.get("assignee") or {}).get("kind", "role"),
            "assigned_to_value": (blk.get("assignee") or {}).get("value", "admin"),
            "decision": None, "note": "", "decided_by": None, "created_at": now(),
        }  # fmt: skip

    def resolve_escalation_from_submission(self, builder_id: str, data: dict[str, Any],
                                           conv: dict[str, Any]) -> None:  # fmt: skip
        esc = self.escalations.get(builder_id)
        if not esc or esc["status"] != "open":
            return
        values: dict[str, Any] = dict(data.get("values") or {})
        for blk in (data.get("submission") or {}).get("blocks") or []:
            values.update(blk.get("values") or {})
        decision = values.get("decision")
        if decision in ("approve", "reject"):
            esc.update(status="resolved", decision=decision, note=str(values.get("note") or ""))

    def request_paths(self) -> list[str]:
        return [r["path"] for r in self.requests]

    def calls(self, method: str | None, prefix: str) -> list[dict[str, Any]]:
        """Logged BFF requests whose path starts with ``prefix`` (any method when ``None``)."""
        return [
            r
            for r in self.requests
            if (method is None or r["method"] == method) and r["path"].startswith(prefix)
        ]


def _skill_record(md: bytes, aux: dict[str, bytes], **extra: Any) -> dict[str, Any]:
    meta = _frontmatter(md)
    return {
        "md": md, "aux": dict(aux), "meta": meta, "updated_at": now(), **extra,
    }  # fmt: skip


def _frontmatter(md: bytes) -> dict[str, Any]:
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", md.decode("utf-8", "replace"), re.DOTALL)
    if not m:
        raise HTTPError(422, "SKILL.md must start with a YAML frontmatter block")
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise HTTPError(422, f"SKILL.md frontmatter is not valid YAML: {exc}") from None
    if not isinstance(meta, dict) or not meta.get("name"):
        raise HTTPError(422, "SKILL.md frontmatter needs a name")
    return meta


def _store_record(sid: str, name: str, org: str, ws_ids: list[str]) -> dict[str, Any]:
    return {
        "id": sid, "org_id": org, "name": name, "status": "active", "s3_bucket": "fake-bucket",
        "s3_prefix": f"orgs/{org}/stores/{sid}", "document_count": 0, "config": {},
        "connector_id": None, "pipeline_id": None, "last_sync_timestamp": None,
        "created_at": now(), "updated_at": now(), "workstream_ids": list(ws_ids),
    }  # fmt: skip


# =============================================================================================
# Auth routes
# =============================================================================================
@route("POST", "/v1/users/api-token", auth="none")
def api_token(rq: Req) -> Any:
    body = rq.json()
    for f in ("client_id", "secret"):
        if not body.get(f):
            raise missing(f)
    pat = rq.st.pats.get(str(body["client_id"]))
    if pat is None or not hmac.compare_digest(str(pat["secret"]), str(body["secret"])):
        rq.st.failed_exchanges += 1
        return Raw(401, b'{"errors":["Invalid client credentials"]}', "application/json")
    if pat["locked"]:
        rq.st.failed_exchanges += 1
        return Raw(400, LOCKED_MESSAGE.encode(), "text/plain")
    iat = int(time.time())
    claims = {
        "tenantId": rq.st.org_id, "roles": pat["roles"], "sub": f"pat-{body['client_id']}",
        "userId": pat["user_id"], "email": pat["email"], "iat": iat, "exp": iat + 3600,
    }  # fmt: skip
    rq.st.exchanges.append(str(body["client_id"]))
    return {
        "access_token": make_jwt(claims),
        "refresh_token": secrets.token_hex(16),
        "expires_in": 3600,
    }


@route("POST", "/v1/users/validate-token", auth="none")
def validate_token(rq: Req) -> Any:
    auth = rq.headers.get("authorization", "")
    claims = decode_jwt(auth[7:]) if auth.lower().startswith("bearer ") else None
    if not claims:
        return {"valid": False, "org_id": None, "user_id": None, "user_email": None}
    return {"valid": True, "org_id": claims["tenantId"], "user_id": claims.get("sub"),
            "user_email": claims.get("email")}  # fmt: skip


@route("GET", BASE + "/me")
def me(rq: Req) -> Any:
    return {
        "user_id": rq.user_id, "org_id": rq.tenant, "email": rq.email,
        "role": "admin" if rq.is_admin else "reviewer", "workstream_staging_enabled": False,
    }  # fmt: skip


@route("GET", "/v1/users/organization")
def organization_users(rq: Req) -> Any:
    users = [
        {"frontegg_id": p["user_id"], "name": cid, "email": p["email"], "roles": p["roles"]}
        for cid, p in rq.st.pats.items()
    ]
    return {"users": users, "total": len(users)}


@route("GET", BASE + "/limits")
def limits(rq: Req) -> Any:
    return {"max_plugin_upload_bytes": 50 * 1024 * 1024, "max_skill_upload_mb": 50}


@route("GET", BASE + "/models")
def models(rq: Req) -> Any:
    return [
        # Observed live (staging 2026-10-29): 9 rows, provider is capitalised ("Anthropic"),
        # every row has route "gateway", cheapest is claude-haiku-4-5, one row is_default.
        {"model_id": "claude-sonnet-4-6", "label": "Sonnet", "provider": "Anthropic",
         "thinking": False, "context_window": 200000, "route": "gateway", "is_default": True},
        {"model_id": "claude-haiku-4-5", "label": "Claude Haiku 4.5", "provider": "Anthropic",
         "thinking": False, "context_window": 200000, "route": "gateway", "is_default": False},
    ]  # fmt: skip


# =============================================================================================
# Skills, default skills, plugins
# =============================================================================================
def _norm_aux_path(path: str) -> str:
    norm = posixpath.normpath(path.replace("\\", "/"))
    if (
        not path
        or path.startswith("/")
        or norm.startswith("..")
        or norm == "SKILL.md"
        or norm == "."
    ):
        raise HTTPError(422, f"invalid aux path: {path!r}")
    return norm


def _decode_skill_payload(body: dict[str, Any]) -> tuple[str, bytes, dict[str, bytes]]:
    name = body.get("skill_name")
    if not name:
        raise missing("skill_name")
    if not body.get("skill_md_b64"):
        raise missing("skill_md_b64")
    if not SLUG_RE.match(str(name)):
        raise HTTPError(422, "skill_name must match ^[a-z0-9][a-z0-9-]*$")
    try:
        md = base64.b64decode(str(body["skill_md_b64"]), validate=True)
        aux = {
            _norm_aux_path(str(a["path"])): base64.b64decode(str(a["content_b64"]), validate=True)
            for a in body.get("aux_files") or []
        }
    except (binascii.Error, KeyError, TypeError):
        raise HTTPError(422, "invalid base64 or aux_files entry") from None
    _frontmatter(md)
    return str(name), md, aux


@route("POST", BASE + "/skills", auth="admin")
def upload_skill(rq: Req) -> Any:
    body = rq.json()
    name, md, aux = _decode_skill_payload(body)
    st = rq.st
    replace = bool(body.get("replace", False))
    if (name in st.org_skills or name in st.default_skills) and not replace:
        where = "org" if name in st.org_skills else "default"
        raise HTTPError(
            409,
            f"{'An' if where == 'org' else 'A'} {where} skill named '{name}' already exists; pass replace=true",
        )
    st.org_skills[name] = _skill_record(md, aux)
    return {"keys": [f"orgs/{st.org_id}/skills/{name}/SKILL.md"]
            + [f"orgs/{st.org_id}/skills/{name}/{p}" for p in sorted(aux)]}  # fmt: skip


def _catalog_item(
    name: str, rec: dict[str, Any], source: str, plugin: str | None = None
) -> dict[str, Any]:
    meta = rec["meta"]
    return {
        "name": name, "description": str(meta.get("description") or ""), "source": source,
        "plugin": plugin, "output_type": meta.get("output_type") or "text",
        "output_schema": meta.get("output_schema"), "featured": bool(meta.get("featured")),
        "has_process": "process" in meta, "updated_at": rec["updated_at"],
    }  # fmt: skip


@route("GET", BASE + "/skills")
def list_skills(rq: Req) -> Any:
    st = rq.st
    items = {n: _catalog_item(n, r, "default") for n, r in st.default_skills.items()}
    for slug, plug in st.plugins.items():
        for sname, rec in plug["skills"].items():
            items[sname] = _catalog_item(sname, rec, "org", plugin=slug)
    for n, r in st.org_skills.items():  # org wins on a name collision
        items[n] = _catalog_item(n, r, "org")
    return {"items": sorted(items.values(), key=lambda i: i["name"])}


def _find_skill(st: FakeBFFState, name: str) -> tuple[dict[str, Any], str]:
    if name in st.org_skills:
        return st.org_skills[name], "org"
    if name in st.default_skills:
        return st.default_skills[name], "default"
    for plug in st.plugins.values():
        if name in plug["skills"]:
            return plug["skills"][name], "org"
    raise HTTPError(404, f"Skill '{name}' not found")


@route("GET", BASE + "/skills/{name}")
def get_skill(rq: Req) -> Any:
    rec, source = _find_skill(rq.st, rq.params["name"])
    meta = rec["meta"]
    return {
        "name": rq.params["name"], "source": source, "description": str(meta.get("description") or ""),
        # the real platform strips the frontmatter (and the blank lines after it) from `body`
        "body": re.sub(r"\A---\s*\n.*?\n---\s*\n", "", rec["md"].decode("utf-8", "replace"),
                       count=1, flags=re.DOTALL).lstrip("\n"),
        "aux_files": [{"path": p, "size": len(c)} for p, c in sorted(rec["aux"].items())],
        "output_type": meta.get("output_type") or "text", "output_schema": meta.get("output_schema"),
    }  # fmt: skip


def _file_content(rec: dict[str, Any], path: str) -> dict[str, Any]:
    data = rec["md"] if path == "SKILL.md" else rec["aux"].get(path)
    if data is None:
        raise HTTPError(404, f"File '{path}' not found")
    try:
        if b"\x00" in data:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "binary")
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPError(415, "Binary files cannot be read as text") from None
    if len(data) > MAX_FILE_BYTES:
        content = data[:MAX_FILE_BYTES].decode("utf-8", "ignore") + TRUNC_MARKER
    return {"path": path, "content": content}


@route("GET", BASE + "/skills/{name}/files")
def skill_files(rq: Req) -> Any:
    rec, _ = _find_skill(rq.st, rq.params["name"])
    return _file_content(rec, rq.need_q("path"))


@route("DELETE", BASE + "/skills/{name}", auth="admin")
def delete_skill(rq: Req) -> Any:
    rec = rq.st.org_skills.pop(rq.params["name"], None)  # org tier only; defaults are untouched
    return {"deleted": 0 if rec is None else 1 + len(rec["aux"])}


@route("POST", BASE + "/default-skills", auth="adopt")
def publish_default_skill(rq: Req) -> Any:
    body = rq.json()
    name, md, aux = _decode_skill_payload(body)
    contract = int(body.get("min_platform_contract") or 1)
    if contract > 1:
        raise HTTPError(409, "min_platform_contract exceeds the live platform contract (1)")
    version = str(body.get("bundle_version") or "dev")
    rq.st.default_skills[name] = _skill_record(md, aux, bundle_version=version)
    return {"skill_name": name, "bundle_version": version, "content_dir": f"default/{name}/{version}",
            "keys": [f"default/{name}/{version}/SKILL.md"]}  # fmt: skip


@route("GET", BASE + "/default-skills/{name}", auth="adopt")
def default_skill_pointer(rq: Req) -> Any:
    rec = rq.st.default_skills.get(rq.params["name"])
    if rec is None:  # the real route answers 200 with published false, not 404
        return {"skill_name": rq.params["name"], "published": False, "bundle_version": None,
                "content_dir": None}  # fmt: skip
    return {"skill_name": rq.params["name"], "published": True,
            "bundle_version": rec.get("bundle_version"),
            "content_dir": f"default/{rq.params['name']}/{rec.get('bundle_version')}"}  # fmt: skip


def _parse_multipart(rq: Req) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    ctype = rq.headers.get("content-type", "")
    if "multipart/form-data" not in ctype:
        raise HTTPError(422, "expected multipart/form-data")
    msg = message_from_bytes(
        f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n".encode() + rq.raw,
        policy=email_default,
    )
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in msg.iter_parts():  # type: ignore[attr-defined]
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename is not None:
            files[str(name)] = (filename, payload)
        else:
            fields[str(name)] = payload.decode("utf-8", "replace")
    return fields, files


@route("POST", BASE + "/plugins", auth="admin")
def upload_plugin(rq: Req) -> Any:
    fields, files = _parse_multipart(rq)
    if "plugin" not in files or not files["plugin"][1]:
        raise HTTPError(422, "plugin file is empty or missing")
    try:
        zf = zipfile.ZipFile(io.BytesIO(files["plugin"][1]))
    except zipfile.BadZipFile:
        raise HTTPError(422, "plugin is not a valid zip") from None
    names = zf.namelist()
    if ".claude-plugin/plugin.json" not in names:
        raise HTTPError(422, "plugin has no .claude-plugin/plugin.json")
    try:
        manifest = json.loads(zf.read(".claude-plugin/plugin.json"))
    except json.JSONDecodeError:
        raise HTTPError(422, "plugin.json is not valid JSON") from None
    slug = str(manifest.get("name") or "")
    if not SLUG_RE.match(slug):
        raise HTTPError(422, "plugin name must match ^[a-z0-9][a-z0-9-]*$")
    skills: dict[str, dict[str, Any]] = {}
    for n in names:
        m = re.match(r"^skills/([^/]+)/SKILL\.md$", n)
        if m:
            sname = m.group(1)
            prefix = f"skills/{sname}/"
            aux = {x[len(prefix):]: zf.read(x) for x in names
                   if x.startswith(prefix) and x != n and not x.endswith("/")}  # fmt: skip
            skills[sname] = _skill_record(zf.read(n), aux)
    if not skills:
        raise HTTPError(422, "plugin has no skills/<slug>/SKILL.md")
    replace = fields.get("replace", "false").lower() == "true"
    if slug in rq.st.plugins and not replace:
        raise HTTPError(409, f"Plugin '{slug}' already exists; pass replace=true")
    readme = zf.read("README.md").decode("utf-8", "replace") if "README.md" in names else ""
    rq.st.plugins[slug] = {
        "name": slug, "slug": slug, "version": str(manifest.get("version") or "0.0.0"),
        "description": str(manifest.get("description") or ""), "author": manifest.get("author"),
        "keywords": manifest.get("keywords") or [], "readme": readme, "skills": skills,
        "updated_at": now(),
    }  # fmt: skip
    return {
        "slug": slug, "version": rq.st.plugins[slug]["version"],
        "description": rq.st.plugins[slug]["description"],
        "skills": [{"name": s, "slug": s, "description": str(r["meta"].get("description") or ""),
                    "aux_count": len(r["aux"])} for s, r in skills.items()],
    }  # fmt: skip


def _plugin_item(p: dict[str, Any]) -> dict[str, Any]:
    keys = ("name", "slug", "version", "description", "author", "keywords", "updated_at")
    return {**{k: p[k] for k in keys}, "skill_count": len(p["skills"]), "source": "org"}


@route("GET", BASE + "/plugins")
def list_plugins(rq: Req) -> Any:
    return {"items": [_plugin_item(p) for p in rq.st.plugins.values()]}


@route("GET", BASE + "/plugins/{name}")
def get_plugin(rq: Req) -> Any:
    p = rq.st.plugins.get(rq.params["name"])
    if p is None:
        raise HTTPError(404, "Plugin not found")
    skills = [
        {"name": s, "slug": s, "description": str(r["meta"].get("description") or ""),
         "body": r["md"].decode("utf-8", "replace"),
         "aux_files": [{"path": a, "size": len(c)} for a, c in sorted(r["aux"].items())]}
        for s, r in p["skills"].items()
    ]  # fmt: skip
    return {**_plugin_item(p), "readme": p["readme"], "skills": skills}


@route("GET", BASE + "/plugins/{name}/skills/{slug}/files")
def plugin_skill_files(rq: Req) -> Any:
    p = rq.st.plugins.get(rq.params["name"])
    if p is None or rq.params["slug"] not in p["skills"]:
        raise HTTPError(404, "Plugin skill not found")
    return _file_content(p["skills"][rq.params["slug"]], rq.need_q("path"))


@route("DELETE", BASE + "/plugins/{name}", auth="admin")
def delete_plugin(rq: Req) -> Any:
    return {"deleted": 1 if rq.st.plugins.pop(rq.params["name"], None) else 0}


# =============================================================================================
# Workstreams
# =============================================================================================
def _live_workstreams(st: FakeBFFState) -> list[dict[str, Any]]:
    return [w for w in st.workstreams.values() if not w["is_deleted"]]


def _get_ws(st: FakeBFFState, wid: str) -> dict[str, Any]:
    ws = st.workstreams.get(wid)
    if ws is None or ws["is_deleted"]:
        raise HTTPError(404, f"Workstream not found: {wid}")
    return ws


@route("GET", "/v1/org/workstreams")
def list_workstreams(rq: Req) -> Any:
    search = (rq.q("search") or "").lower()
    items = [w for w in _live_workstreams(rq.st) if search in w["name"].lower()]
    return _page(items, rq)


@route("POST", "/v1/org/workstreams", auth="admin")
def create_workstream(rq: Req) -> Any:
    body = rq.json()
    name = str(body.get("name") or "").strip()
    if not name:
        raise missing("name")
    if any(w["name"].lower() == name.lower() for w in _live_workstreams(rq.st)):
        raise HTTPError(409, f"A workstream named '{name}' already exists")
    ws = rq.st.add_workstream(name)
    ws.update(description=body.get("description"), custom_properties=body.get("custom_properties"),
              created_by=rq.sub)  # fmt: skip
    # add_workstream already created the linked default store (as the real BFF does)
    return 201, ws


@route("GET", "/v1/org/workstreams/{wid}")
def get_workstream(rq: Req) -> Any:
    return _get_ws(rq.st, rq.params["wid"])


@route("POST", "/v1/org/workstreams/{wid}/update", auth="admin")
def update_workstream(rq: Req) -> Any:
    ws = _get_ws(rq.st, rq.params["wid"])
    ws.update({k: v for k, v in rq.json().items() if k in ("name", "description", "custom_properties")
               and v is not None})  # fmt: skip
    return ws


@route("POST", "/v1/org/workstreams/{wid}/delete", auth="admin")
def delete_workstream(rq: Req) -> Any:
    ws = _get_ws(rq.st, rq.params["wid"])
    if any(p["state"] != "deleted" and ws["id"] in p.get("workstream_ids", [])
           for p in rq.st.pipelines.values()):  # fmt: skip
        raise HTTPError(409, "active_pipelines")
    ws["is_deleted"] = True
    return {"message": "Workstream deleted"}


@route("GET", BASE + "/workstreams")
def harness_workstreams(rq: Req) -> Any:
    st = rq.st
    items = _live_workstreams(st)
    if not rq.is_admin:
        allowed = st.grants.get(rq.user_id, set())
        items = [w for w in items if w["id"] in allowed]
    ids = [i for i in (rq.q("ids") or "").split(",") if i]
    if ids:
        items = [w for w in items if w["id"] in ids]
    search = (rq.q("search") or "").lower()
    items = [w for w in items if search in w["name"].lower()]
    pid = rq.q("process_id")
    if pid:
        items = [w for w in items if w["id"] in st.process_ws.get(pid, [])]
    limit = rq.qint("limit", 100, 1, 100)
    return {"items": [{"id": w["id"], "name": w["name"]} for w in items[:limit]]}


# =============================================================================================
# Processes
# =============================================================================================
def _sanitise_components(raw: Any) -> list[dict[str, str]]:
    out = []
    for c in raw or []:
        if isinstance(c, dict) and c.get("type") and c.get("ref"):
            out.append({"type": str(c["type"]), "source": str(c.get("source") or ""),
                        "ref": str(c["ref"])})  # fmt: skip
    return out


def _live_process(st: FakeBFFState, pid: str) -> dict[str, Any]:
    p = st.processes.get(pid)
    if p is None or p.get("deleted"):
        raise HTTPError(404, "Process not found")
    return p


@route("GET", "/v1/org/processes")
def list_processes(rq: Req) -> Any:
    out = []
    for p in rq.st.processes.values():
        if p.get("deleted"):
            continue
        flow = p.get("compiled_flow") or {}
        row = {k: v for k, v in p.items() if k not in ("compiled_flow", "deleted", "created_by")}
        row["starters"] = flow.get("starters", [])
        out.append(row)
    return {"processes": out}


@route("POST", "/v1/org/processes", auth="admin")
def create_process(rq: Req) -> Any:
    body = rq.json()
    for f in ("name", "display_name"):
        if not body.get(f):
            raise missing(f)
    st = rq.st
    if any(p["name"] == body["name"] and not p.get("deleted") for p in st.processes.values()):
        raise HTTPError(409, f"A process named '{body['name']}' already exists")
    flow = body.get("compiled_flow")
    if flow is not None and len(json.dumps(flow)) > 512_000:
        raise HTTPError(422, "compiled_flow exceeds 512000 bytes")
    pid = str(uuid.uuid4())
    st.processes[pid] = {
        "id": pid, "name": body["name"], "display_name": body["display_name"],
        "description": body.get("description"), "components": _sanitise_components(body.get("components")),
        "agent_instructions": body.get("agent_instructions"),
        "is_enabled": bool(body.get("is_enabled", False)), "experience_kind": "agent",
        "dashboard_view": None, "compiled_flow": flow, "created_by": rq.sub, "created_at": now(),
    }  # fmt: skip
    st.process_ws[pid] = []
    return 201, st.processes[pid]


@route("GET", "/v1/org/processes/{pid}", auth="admin")
def get_process(rq: Req) -> Any:
    return _live_process(rq.st, rq.params["pid"])


@route("PATCH", "/v1/org/processes/{pid}", auth="admin")
def patch_process(rq: Req) -> Any:
    p = _live_process(rq.st, rq.params["pid"])
    body = rq.json()
    for k in ("display_name", "description", "components", "agent_instructions", "is_enabled",
              "compiled_flow"):  # fmt: skip
        if k in body and (body[k] is not None or k in ("description", "agent_instructions")):
            p[k] = _sanitise_components(body[k]) if k == "components" else body[k]
    return p


@route("DELETE", "/v1/org/processes/{pid}", auth="admin")
def delete_process(rq: Req) -> Any:
    p = _live_process(rq.st, rq.params["pid"])
    p["deleted"] = True
    rq.st.process_ws[p["id"]] = []
    return 204, None


@route("GET", "/v1/org/processes/{pid}/workstreams")
def get_process_ws(rq: Req) -> Any:
    _live_process(rq.st, rq.params["pid"])
    return {"workstream_ids": sorted(rq.st.process_ws.get(rq.params["pid"], []))}


@route("PUT", "/v1/org/processes/{pid}/workstreams", auth="admin")
def set_process_ws(rq: Req) -> Any:
    _live_process(rq.st, rq.params["pid"])
    ids = rq.json().get("workstream_ids")
    if not isinstance(ids, list):
        raise missing("workstream_ids")
    unknown = [i for i in ids if i not in rq.st.workstreams or rq.st.workstreams[i]["is_deleted"]]
    if unknown:
        raise HTTPError(400, f"Unknown workstream ids: {unknown}")
    rq.st.process_ws[rq.params["pid"]] = sorted(set(map(str, ids)))
    return {"workstream_ids": rq.st.process_ws[rq.params["pid"]]}


@route("GET", "/v1/org/processes/{pid}/write-workstreams", auth="admin")
def get_write_ws(rq: Req) -> Any:
    _live_process(rq.st, rq.params["pid"])
    return rq.st.write_ws.get(rq.params["pid"], {"workstream_ids": [], "folder_prefix": None})


@route("PUT", "/v1/org/processes/{pid}/write-workstreams", auth="admin")
def set_write_ws(rq: Req) -> Any:
    _live_process(rq.st, rq.params["pid"])
    body = rq.json()
    ids = body.get("workstream_ids")
    if not isinstance(ids, list):
        raise missing("workstream_ids")
    if "*" in ids and len(ids) > 1:
        raise HTTPError(400, "'*' cannot be mixed with workstream ids")
    unknown = [i for i in ids if i != "*" and i not in rq.st.workstreams]
    if unknown:
        raise HTTPError(400, f"Unknown workstream ids: {unknown}")
    prefix = str(body.get("folder_prefix") or "").strip("/")
    if ".." in prefix.split("/"):
        raise HTTPError(400, "folder_prefix must not contain '..'")
    if ids and not prefix:
        raise HTTPError(400, "folder_prefix is required when granting write access")
    rq.st.write_ws[rq.params["pid"]] = {"workstream_ids": ids, "folder_prefix": prefix}
    return rq.st.write_ws[rq.params["pid"]]


def _fan_out(rq: Req, p: dict[str, Any], workstream_ids: list[str]) -> dict[str, Any]:
    st = rq.st
    started = []
    for wid in workstream_ids:
        conv = _new_conversation(st, uuid.uuid4().hex, rq.sub, wid, p["id"], shared=True)
        msg = p.get("agent_instructions") or f"Run the '{p['name']}' process for this customer."
        _run_turn(st, conv, msg, p, rq)
        started.append({"workstream_id": wid, "conversation_id": conv["id"]})
    return {"started": started, "failed": [], "count": len(started)}


@route("POST", "/v1/org/processes/{pid}/run", auth="admin")
def run_process(rq: Req) -> Any:
    """Builder test run: one kickoff turn per assigned workstream, whatever the caller's grants."""
    p = _live_process(rq.st, rq.params["pid"])
    if not p["is_enabled"]:
        raise HTTPError(409, "Process is disabled")
    wsids = rq.st.process_ws.get(p["id"], [])
    if not wsids:
        raise HTTPError(400, "Process has no workstreams assigned.")
    return _fan_out(rq, p, list(wsids))


@route("POST", "/v1/processes/{pid}/run")
def end_user_run_process(rq: Req) -> Any:
    """End-user run: only workstreams that are assigned to the process AND accessible."""
    p = _live_process(rq.st, rq.params["pid"])
    if not p["is_enabled"]:
        raise HTTPError(409, "Process is disabled")
    assigned = set(rq.st.process_ws.get(p["id"], []))
    accessible = (
        {w["id"] for w in _live_workstreams(rq.st)}
        if rq.is_admin
        else rq.st.grants.get(rq.user_id, set())
    )
    usable = assigned & accessible
    wanted = rq.json(False).get("workstream_ids")
    if wanted is not None:
        if not set(wanted) <= usable:
            raise HTTPError(
                400, "workstream_ids must be within the assigned and accessible workstreams"
            )
        usable = set(wanted)
    if not usable:
        raise HTTPError(403, "No accessible workstream for this process")
    return _fan_out(rq, p, sorted(usable))


@route("GET", "/v1/processes")
def end_user_processes(rq: Req) -> Any:
    return {"processes": [p for p in list_processes(rq)["processes"] if p["is_enabled"]]}


# =============================================================================================
# Turns, conversations, transcript, outputs, trace
# =============================================================================================
def _new_conversation(st: FakeBFFState, cid: str, owner: str, workstream_id: str | None,
                      process_id: str | None, shared: bool = False) -> dict[str, Any]:  # fmt: skip
    ws = st.workstreams.get(workstream_id or "")
    conv = {
        "id": cid, "created_by_user_id": owner, "workstream_id": workstream_id,
        "workstream_name": ws["name"] if ws else None, "process_id": process_id, "shared": shared,
        "created_at": now(), "updated_at": now(), "title": "", "turns": [], "transcript": [],
        "steps": {}, "builders": {}, "submissions": [], "rejected_submissions": [],
        "questions": [], "files": {}, "approvals": [],
    }  # fmt: skip
    st.conversations[cid] = conv
    return conv


def _check_ws_access(rq: Req, workstream_id: str | None) -> None:
    if workstream_id in (None, "", "general", "unscoped") or rq.is_admin:
        if workstream_id not in (None, "", "general", "unscoped"):
            _get_ws(rq.st, str(workstream_id))
        return
    _get_ws(rq.st, str(workstream_id))
    if workstream_id not in rq.st.grants.get(rq.user_id, set()):
        raise HTTPError(403, "No access to this workstream")


def _owns(rq: Req, conv: dict[str, Any]) -> None:
    if conv["created_by_user_id"] == rq.user_id:
        return
    if conv["shared"] and (
        rq.is_admin or conv["workstream_id"] in rq.st.grants.get(rq.user_id, set())
    ):
        return
    raise HTTPError(403, "You do not have access to this conversation")


def _conv(rq: Req, cid: str) -> dict[str, Any]:
    conv = rq.st.conversations.get(cid)
    if conv is None:
        raise HTTPError(404, "Conversation not found")
    _owns(rq, conv)
    return conv


def _turn(rq: Req, turn_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    cid = rq.need_q("conversation_id")
    conv = _conv(rq, cid)
    turn = rq.st.turns.get(turn_id)
    if turn is None or turn["conversation_id"] != cid:
        raise HTTPError(404, "Turn not found")
    return conv, turn


def _run_turn(st: FakeBFFState, conv: dict[str, Any], message: str, process: dict[str, Any] | None,
              rq: Req, model: str | None = None) -> dict[str, Any]:  # fmt: skip
    turn_id = uuid.uuid4().hex
    approvals = APPROVAL_RE.findall(message)
    for fp in approvals:
        conv["approvals"].append(fp)
        st.approvals.append({"fingerprint": fp, "conversation_id": conv["id"], "turn_id": turn_id,
                             "user_id": rq.user_id})  # fmt: skip
    conv["transcript"].append({"kind": "user_message", "text": message, "turn_id": turn_id,
                               "created_at": now()})  # fmt: skip
    ctx = TurnCtx(
        user_message=message,
        conversation_id=conv["id"],
        turn_id=turn_id,
        process_id=conv["process_id"],
        process_name=process["name"] if process else None,
        workstream_id=conv["workstream_id"],
        conversation=conv,
        approvals=list(conv["approvals"]),
    )
    events: list[Event] = apply_builder_data(conv, message, st)
    script: Script | None = None
    for s in st.scripts:
        if s.matches(ctx):
            script = s
            break
    if script is not None:
        events += script.events(ctx)
    elif not BUILDER_DATA_RE.search(message):
        events += list(DEFAULT_STEPS)
    events = absorb_events(conv, events, st)
    for ev in events:
        blk = (ev.get("data") or {}).get("block") or {}
        if blk.get("type") == "builder":
            conv["transcript"].append(
                {"kind": "builder", **{k: v for k, v in blk.items() if k != "type"}}
            )
        elif blk.get("type") == "artifact":
            # the durable ArtifactEvent (adoptai-workflows agent_harness/models.py:228)
            conv["transcript"].append(
                {"kind": "artifact", "artifact_id": f"art-{uuid.uuid4().hex[:12]}",
                 "filename": blk.get("name"), "sandbox_path": f"/workspace/{blk.get('path')}",
                 "relative_path": blk.get("path"), "turn_id": turn_id}
            )  # fmt: skip
    final = "".join(
        e["data"]["block"].get("text", "") for e in events
        if e["data"].get("block", {}).get("type") == "text_delta"
    )  # fmt: skip
    if final:
        conv["transcript"].append({"kind": "final", "text": final, "turn_id": turn_id})
    hold = bool(script and script.hold)
    st.turns[turn_id] = {
        "id": turn_id, "conversation_id": conv["id"], "events": events, "message": message,
        "status": "RUNNING" if hold else "COMPLETED", "stop_requested": False,
        "stop_reason": script.stop_reason if script else "end_turn",
        "failure": script.failure if script else None, "model": model or "claude-sonnet-4-6",
        "created_at": now(),
    }  # fmt: skip
    conv["turns"].append(turn_id)
    conv["updated_at"] = now()
    return st.turns[turn_id]


@route("POST", BASE + "/turns")
def start_turn(rq: Req) -> Any:
    body = rq.json()
    st = rq.st
    if "user_message" not in body or not isinstance(body["user_message"], str):
        raise missing("user_message")
    if not body.get("conversation_id"):
        raise missing("conversation_id")
    message = str(body["user_message"])
    ack = bool(body.get("credential_warning_acknowledged", False))
    if CREDENTIAL_RE.search(message) and not ack:
        return {"requires_confirmation": True, "confirmation_type": "credential_in_message",
                "warning": "The message looks like it contains a credential. Resend with "
                           "credential_warning_acknowledged=true to proceed."}  # fmt: skip
    cid = str(body["conversation_id"])
    conv = st.conversations.get(cid)
    workstream_id = body.get("workstream_id") or (conv["workstream_id"] if conv else None)
    process_id = body.get("process_id") or (conv["process_id"] if conv else None)
    if conv is not None:
        _owns(rq, conv)
    _check_ws_access(rq, workstream_id)
    process = None
    if process_id:
        process = st.processes.get(str(process_id))
        if process is None or process.get("deleted"):
            raise HTTPError(403, "This process no longer exists")
        if not process["is_enabled"]:
            raise HTTPError(403, "This process is disabled")
        real = workstream_id not in (None, "", "general", "unscoped")
        if real and workstream_id not in st.process_ws.get(str(process_id), []):
            raise HTTPError(403, "This process is not assigned to that workstream")
    if conv is None:
        conv = _new_conversation(st, cid, rq.user_id, workstream_id, process_id)
    elif any(st.turns[t]["status"] == "RUNNING" for t in conv["turns"]):
        raise HTTPError(409, "A turn is already running in this conversation")
    safe = {k: v for k, v in body.items() if k != "user_message"}
    st.turn_requests.append({**safe, "user_message": message})
    turn = _run_turn(st, conv, message, process, rq, body.get("model") or None)
    return {"turn_id": turn["id"], "workflow_id": f"wf-{turn['id']}", "conversation_id": cid}


@route("POST", BASE + "/turns/{turn_id}/stop")
def stop_turn(rq: Req) -> Any:
    body = rq.json()
    if not body.get("conversation_id"):
        raise missing("conversation_id")
    conv = _conv(rq, str(body["conversation_id"]))
    turn = rq.st.turns.get(rq.params["turn_id"])
    if turn is None or turn["conversation_id"] != conv["id"]:
        raise HTTPError(404, "Turn not found")
    turn["stop_requested"] = True
    return {"turn_id": turn["id"], "workflow_id": f"wf-{turn['id']}", "signalled": True}


def _turn_status(turn: dict[str, Any]) -> str:
    if turn["status"] == "RUNNING" and turn["stop_requested"]:
        turn["status"] = "CANCELED"
        turn["stop_reason"] = "user_cancelled"
    return str(turn["status"])


@route("GET", BASE + "/turns/{turn_id}/status")
def turn_status(rq: Req) -> Any:
    _, turn = _turn(rq, rq.params["turn_id"])
    status = _turn_status(turn)
    return {"turn_id": turn["id"], "status": status,
            "stage": "thinking" if status == "RUNNING" else "done",
            "current_chip_title": None, "stop_requested": turn["stop_requested"],
            "terminal": status != "RUNNING", "describe_ok": True}  # fmt: skip


@route("GET", BASE + "/turns/{turn_id}/stream-checkpoint")
def stream_checkpoint(rq: Req) -> Any:
    _, turn = _turn(rq, rq.params["turn_id"])
    status = _turn_status(turn)
    return {"turn_id": turn["id"], "running": status == "RUNNING",
            "last_id": f"{len(turn['events'])}-0", "terminal": status != "RUNNING"}  # fmt: skip


@route("GET", BASE + "/turns/{turn_id}/temporal-history")
def temporal_history(rq: Req) -> Any:
    _, turn = _turn(rq, rq.params["turn_id"])
    return {
        "turn_id": turn["id"],
        "events": [{"event_id": 1, "event_type": "WorkflowExecutionStarted"}],
    }


def _envelope(n: int, name: str, data: dict[str, Any]) -> str:
    return json.dumps({"source": "redis", "redis_id": f"{n}-0",
                       "event": {"event_name": name, "type": "response", "data": data}}) + "\n"  # fmt: skip


@route("GET", BASE + "/turns/{turn_id}/stream")
def stream_turn(rq: Req) -> Any:
    conv, turn = _turn(rq, rq.params["turn_id"])
    iterations = max(
        1, sum(1 for e in turn["events"] if e["data"].get("block", {}).get("type") == "tool_use")
    )
    # Real BFF (observed live 2026-10-29): the stream of turn N first replays the retained Redis
    # events of every earlier turn of the conversation, each ending in its own terminal event.
    earlier = [rq.st.turns[t] for t in conv["turns"][: conv["turns"].index(turn["id"])]]

    def lines() -> Iterator[str]:
        yield json.dumps({"source": "keepalive", "cursor": 0}) + "\n"
        n = 0
        for old in earlier:
            for ev in old["events"]:
                n += 1
                yield _envelope(n, ev["event_name"], ev["data"])
            n += 1
            yield _envelope(n, "harness_turn_complete", {
                "turn_id": old["id"], "stop_reason": old["stop_reason"], "completed": True,
                "iterations_used": 1, "max_iterations": 40, "failure": None,
            })  # fmt: skip
        n += 1
        yield _envelope(n, "harness_sandbox_ready", {"sandbox_id": "sbx-fake"})
        for ev in turn["events"]:
            n += 1
            yield _envelope(n, ev["event_name"], ev["data"])
        deadline = time.time() + 10
        while turn["status"] == "RUNNING" and not turn["stop_requested"] and time.time() < deadline:
            time.sleep(0.02)
        status = _turn_status(turn)
        n += 1
        if turn["failure"]:
            yield _envelope(n, "harness_error", {"turn_id": turn["id"], "error": turn["failure"]})
            turn["status"] = "FAILED"
            return
        yield _envelope(n, "harness_turn_complete", {
            "turn_id": turn["id"], "stop_reason": turn["stop_reason"],
            "completed": status == "COMPLETED", "iterations_used": iterations,
            "max_iterations": 40, "failure": None,
        })  # fmt: skip

    return Stream(lines())


@route("GET", BASE + "/conversations")
def list_conversations(rq: Req) -> Any:
    st = rq.st
    limit = rq.qint("limit", 20, 1, 100)
    out = []
    for c in sorted(st.conversations.values(), key=lambda c: c["created_at"], reverse=True):
        try:
            _owns(rq, c)
        except HTTPError:
            continue
        if rq.q("workstream_id") and c["workstream_id"] != rq.q("workstream_id"):
            continue
        if rq.q("process_id") and c["process_id"] != rq.q("process_id"):
            continue
        # Real BFF row (observed live 2026-10-29, values redacted): conversation_id,
        # workstream_id, workstream_name, process_id, created_by_user_id, title, created_at,
        # last_message_at, process_run, meta_data{sandbox_id}, result_seen, turn_running,
        # running_turn_id, participants[{user_id,name,email,is_system}]. No "id" key.
        out.append({"id": c["id"], "conversation_id": c["id"], "title": c["title"],
                    "process_id": c["process_id"], "workstream_id": c["workstream_id"],
                    "workstream_name": c["workstream_name"], "created_at": c["created_at"],
                    "updated_at": c["updated_at"], "created_by_user_id": c["created_by_user_id"],
                    "is_shared": c["shared"]})  # fmt: skip
    return {"items": out[:limit], "developer_trace_default": True}


@route("GET", BASE + "/conversations/{cid}/transcript")
def transcript(rq: Req) -> Any:
    conv = _conv(rq, rq.params["cid"])
    return {"events": conv["transcript"], "is_shared": conv["shared"]}


@route("GET", BASE + "/conversations/{cid}/outputs/download")
def download_output(rq: Req) -> Any:
    conv = _conv(rq, rq.params["cid"])
    rq.need_q("workstream_id")
    path = rq.need_q("path")
    if path not in conv["files"]:
        raise HTTPError(404, "Output not found")
    return Raw(200, conv["files"][path], "application/octet-stream",
               {"Content-Disposition": f'attachment; filename="{path.rsplit("/", 1)[-1]}"'})  # fmt: skip


def _trace_for(turn: dict[str, Any], conv: dict[str, Any]) -> dict[str, Any]:
    tools = [
        e["data"]["block"]
        for e in turn["events"]
        if e["data"].get("block", {}).get("type") == "tool_use"
    ]
    return {
        "schemaVersion": 7, "turn_id": turn["id"], "conversation_id": conv["id"],
        "status": turn["status"], "stop_reason": turn["stop_reason"], "model": turn["model"],
        "spans": [{"name": f"tool:{t['name']}", "kind": "tool", "duration_ms": 120} for t in tools],
        "iterations": len(tools) or 1,
    }  # fmt: skip


@route("GET", BASE + "/trace/{turn_id}")
def trace(rq: Req) -> Any:
    rq.need_q("workstream_id")
    conv, turn = _turn(rq, rq.params["turn_id"])
    return _trace_for(turn, conv)


@route("GET", BASE + "/conversations/{cid}/trace")
def conversation_trace(rq: Req) -> Any:
    conv = _conv(rq, rq.params["cid"])
    return {"traces": [_trace_for(rq.st.turns[t], conv) for t in conv["turns"]]}


# -- escalations ---------------------------------------------------------------------------
@route("GET", BASE + "/escalations/{bid}/card")
def escalation_card(rq: Req) -> Any:
    esc = rq.st.escalations.get(rq.params["bid"])
    if esc is None:
        raise HTTPError(404, "Escalation not found")
    keys = (
        "builder_id",
        "title",
        "subtitle",
        "metadata",
        "status",
        "assigned_to_kind",
        "assigned_to_value",
    )
    return {k: esc[k] for k in keys}


@route("POST", BASE + "/escalations/{bid}/decide")
def decide_escalation(rq: Req) -> Any:
    bid = rq.params["bid"]
    body = rq.json()
    decision = body.get("decision")
    esc = rq.st.escalations.get(bid)
    # Real BFF (live 2026-10-29): a form/card builder id that is not a policy escalation answers
    # 404 {"detail": "Escalation not found"}, not the documented 422.
    if esc is None:
        raise HTTPError(404, "Escalation not found")
    if not bid.startswith("policy-") or decision not in ("approve", "reject"):
        raise HTTPError(
            422, "decision must be approve|reject and builder_id must start with policy-"
        )
    conv = rq.st.conversations[esc["conversation_id"]]
    assignee_ok = esc["assigned_to_value"] in (rq.user_id, rq.email)
    if not (rq.is_admin or assignee_ok or conv["created_by_user_id"] == rq.user_id):
        raise HTTPError(403, "Only the assignee, the conversation creator or an admin may decide")
    if esc["status"] != "open":
        raise HTTPError(409, "Escalation already resolved")
    esc.update(status="resolved", decision=decision, note=str(body.get("note") or ""),
               decided_by=rq.user_id)  # fmt: skip
    conv["builders"].setdefault(bid, {})["status"] = "resolved"
    return {"recorded": True}


@route("GET", BASE + "/builders/{bid}/capabilities")
def builder_capabilities(rq: Req) -> Any:
    esc = rq.st.escalations.get(rq.params["bid"])
    return {"builder_id": rq.params["bid"], "can_act": bool(rq.is_admin or esc is None)}


@route("GET", "/v1/end-user/agent-escalations")
def list_agent_escalations(rq: Req) -> Any:
    rq.need_q("org_id")
    st = rq.st
    view = rq.q("view", "tasks")
    open_states = {"open", "in_progress", "awaiting_human"}
    rows: list[dict[str, Any]] = []
    for conv in st.conversations.values():
        for bid, b in conv["builders"].items():
            esc = st.escalations.get(bid)
            status = esc["status"] if esc else ("open" if b["status"] == "pending" else "resolved")
            rows.append({"id": bid, "pipeline_run_id": None, "conversation_id": conv["id"],
                         "builder_id": bid, "assigned_to_kind": (esc or {}).get("assigned_to_kind", "role"),
                         # Real row (live 2026-10-29): question "" for a builder card; the card
                         # title is display_label, the gated step is step_name, source_type
                         # "agent", escalation_type "approve", pipeline_run_id = process run id.
                         "question": "", "display_label": (b.get("card") or {}).get("title"),
                         "step_name": (b.get("card") or {}).get("step_id"),
                         "escalation_type": "approve", "source_type": "agent",
                         "status": status,
                         "workstream_id": conv["workstream_id"], "process_id": conv["process_id"],
                         "created_at": conv["updated_at"]})  # fmt: skip
    rows += [dict(e) for e in st.pipeline_escalations.values()]
    rows = [r for r in rows if (r["status"] in open_states) == (view != "activity")]
    for key in ("workstream_id", "process_id", "pipeline_run_id", "status"):
        if rq.q(key):
            rows = [r for r in rows if r.get(key) == rq.q(key)]
    page = rq.qint("page", 1)
    per = rq.qint("per_page", 25, 1, 100)
    return {"data": rows[(page - 1) * per : page * per],
            "pagination": {"page": page, "per_page": per, "total": len(rows)}}  # fmt: skip


# =============================================================================================
# Runs
# =============================================================================================
def _conv_run_status(st: FakeBFFState, conv: dict[str, Any]) -> str:
    turns = [st.turns[t] for t in conv["turns"]]
    if any(_turn_status(t) == "RUNNING" for t in turns):
        return "active"
    if any(b["status"] == "pending" for b in conv["builders"].values()):
        return "waiting_on_hitl"
    last = turns[-1] if turns else None
    if last and last["stop_reason"] == "user_cancelled":
        return "cancelled"
    if last and (last["failure"] or last["status"] == "FAILED"):
        return "failed"
    return "succeeded"


def _conv_run_row(st: FakeBFFState, conv: dict[str, Any], me_id: str) -> dict[str, Any]:
    proc = st.processes.get(conv["process_id"] or "")
    status = _conv_run_status(st, conv)
    return {
        "id": f"cv:{conv['id']}", "source": "conversation",
        "agent_name": proc["display_name"] if proc else "Chat", "agent": proc["name"] if proc else None,
        "agent_type": "process" if proc else "chat", "workstream_id": conv["workstream_id"],
        "workstream_name": conv["workstream_name"], "initiated_by": conv["created_by_user_id"],
        "initiator_id": conv["created_by_user_id"], "initiator_type": "user", "status": status,
        "started_at": conv["created_at"], "last_activity_at": conv["updated_at"], "duration_ms": 1000,
        "current_step": None, "has_open_hitl": status == "waiting_on_hitl", "hitl_stuck": False,
        "turns": len(conv["turns"]), "tokens": 0, "attention": "held" if status == "waiting_on_hitl" else None,
        "attention_reason": None, "waiting_on": None, "needs_you": status == "waiting_on_hitl",
        "feedback_up": 0, "feedback_down": 0,
    }  # fmt: skip


def _pipeline_run_row(
    st: FakeBFFState, pipe: dict[str, Any], run: dict[str, Any]
) -> dict[str, Any]:
    return {
        "id": f"pr:{run['id']}", "source": "pipeline", "agent_name": pipe["name"], "agent": pipe["name"],
        "agent_type": "pipeline", "workstream_id": run.get("workstream_id"),
        "workstream_name": run.get("workstream_name"), "initiated_by": pipe["created_by"],
        "initiator_id": pipe["created_by"], "initiator_type": "schedule", "status": run["status"],
        "started_at": run["started_at"], "last_activity_at": run["finished_at"],
        "duration_ms": run["duration_ms"], "current_step": None,
        "has_open_hitl": run["status"] == "waiting_on_hitl", "hitl_stuck": False, "turns": 0,
        "tokens": 0, "attention": None, "attention_reason": None, "waiting_on": None,
        "needs_you": run["status"] == "waiting_on_hitl", "feedback_up": 0, "feedback_down": 0,
    }  # fmt: skip


def _all_runs(st: FakeBFFState, rq: Req) -> list[dict[str, Any]]:
    rows = [_conv_run_row(st, c, rq.user_id) for c in st.conversations.values()]
    for pid, runs in st.pipeline_runs.items():
        pipe = st.pipelines.get(pid)
        if pipe:
            rows += [_pipeline_run_row(st, pipe, r) for r in runs]
    return sorted(rows, key=lambda r: r["started_at"], reverse=True)


@route("GET", "/v1/runs")
def list_runs(rq: Req) -> Any:
    st = rq.st
    rows = _all_runs(st, rq)
    for key, col in (("status", "status"), ("source", "source")):
        if rq.q(key):
            rows = [r for r in rows if r[col] == rq.q(key)]
    for key, col in (("agent", "agent"), ("workstream_id", "workstream_id")):
        wanted = rq.query.get(key) or rq.query.get(key + "[]") or []
        if wanted:
            rows = [r for r in rows if r[col] in wanted or r["agent_name"] in wanted]
    if rq.q("hitl") == "any":
        rows = [r for r in rows if r["has_open_hitl"]]
    if rq.q("search"):
        s = str(rq.q("search")).lower()
        rows = [r for r in rows if s in json.dumps(r).lower()]
    page = rq.qint("page", 1)
    size = rq.qint("page_size", 25, 1, 200)
    return {"runs": rows[(page - 1) * size : page * size], "total": len(rows), "page": page,
            "page_size": size, "needs_you_total": sum(1 for r in rows if r["needs_you"]),
            "all_total": len(rows), "facets": {}}  # fmt: skip


@route("GET", "/v1/runs/detail")
def run_detail(rq: Req) -> Any:
    st = rq.st
    run_id = rq.need_q("run_id")
    kind, _, rid = run_id.partition(":")
    if kind == "cv" and rid in st.conversations:
        conv = st.conversations[rid]
        row = _conv_run_row(st, conv, rq.user_id)
        row["conversation_turns"] = [
            {"seq": i + 1, "turn_id": t, "status": _turn_status(st.turns[t]),
             "stop_reason": st.turns[t]["stop_reason"]} for i, t in enumerate(conv["turns"])
        ]  # fmt: skip
        row["error_message"] = None
        row["conversation_id"] = conv["id"]
        return row
    if kind == "pr":
        for pid, runs in st.pipeline_runs.items():
            for run in runs:
                if run["id"] == rid and pid in st.pipelines:
                    return {**_pipeline_run_row(st, st.pipelines[pid], run),
                            "error_message": run["error_message"], "conversation_turns": []}  # fmt: skip
    raise HTTPError(404, "Run not found")


def _org_conv(rq: Req) -> dict[str, Any]:
    conv = rq.st.conversations.get(rq.need_q("conversation_id"))
    if conv is None:
        raise HTTPError(404, "Conversation not found in this org")
    return conv


@route("GET", "/v1/runs/conversation-trace")
def runs_conversation_trace(rq: Req) -> Any:
    conv = _org_conv(rq)
    return {"conversation_id": conv["id"],
            "traces": [_trace_for(rq.st.turns[t], conv) for t in conv["turns"]]}  # fmt: skip


@route("GET", "/v1/runs/turn-trace")
def runs_turn_trace(rq: Req) -> Any:
    conv = _org_conv(rq)
    turn = rq.st.turns.get(rq.need_q("turn_id"))
    if turn is None or turn["conversation_id"] != conv["id"]:
        raise HTTPError(404, "Turn not found")
    return _trace_for(turn, conv)


# =============================================================================================
# Doc Store
# =============================================================================================
def _store(rq: Req, sid: str) -> dict[str, Any]:
    s = rq.st.stores.get(sid)
    if s is None or s["status"] == "deleted":
        raise HTTPError(404, "Store not found")
    if (
        not rq.is_admin
        and s["workstream_ids"]
        and not (set(s["workstream_ids"]) & rq.st.grants.get(rq.user_id, set()))
    ):
        raise HTTPError(403, "No access to this store")
    return s


@route("POST", "/v1/docstore/stores")
def create_store(rq: Req) -> Any:
    body = rq.json()
    if not body.get("name"):
        raise missing("name")
    wid = body.get("workstream_id")
    linked = [] if wid in (None, "", "general") else [str(wid)]
    for w in linked:
        _get_ws(rq.st, w)
    sid = uuid.uuid4().hex
    rec = _store_record(sid, str(body["name"]), rq.st.org_id, linked)
    rec["config"] = body.get("config") or {}
    rq.st.stores[sid] = rec
    rq.st.documents[sid] = {}
    return 201, rec


@route("POST", "/v1/docstore/stores/resolve")
def resolve_store(rq: Req) -> Any:
    body = rq.json()
    for s in rq.st.stores.values():
        if s["name"] == body.get("name") and s["status"] != "deleted":
            return s
    return create_store(rq)


@route("GET", "/v1/docstore/stores")
def list_stores(rq: Req) -> Any:
    # newest first, like the real list (live 2026-10-29)
    rows = [s for s in reversed(list(rq.st.stores.values())) if s["status"] != "deleted"]
    if rq.q("workstream_id"):
        rows = [s for s in rows if rq.q("workstream_id") in s["workstream_ids"]]
    if not rq.is_admin:
        allowed = rq.st.grants.get(rq.user_id, set())
        rows = [s for s in rows if not s["workstream_ids"] or set(s["workstream_ids"]) & allowed]
    page = rq.qint("page", 1)
    size = rq.qint("page_size", 25, 1, 100)
    for s in rows:
        s["document_count"] = _live_docs(rq.st, s["id"])
    return {"stores": rows[(page - 1) * size : page * size], "total": len(rows), "page": page,
            "page_size": size}  # fmt: skip


def _live_docs(st: FakeBFFState, sid: str) -> int:
    return sum(1 for d in st.documents.get(sid, {}).values() if d["status"] != "deleted")


@route("GET", "/v1/docstore/stores/{sid}")
def get_store(rq: Req) -> Any:
    s = _store(rq, rq.params["sid"])
    s["document_count"] = _live_docs(rq.st, s["id"])
    return s


@route("PUT", "/v1/docstore/stores/{sid}")
def update_store(rq: Req) -> Any:
    s = _store(rq, rq.params["sid"])
    s.update({k: v for k, v in rq.json().items() if k in ("name", "config", "connector_id", "status")
              and v is not None})  # fmt: skip
    return s


@route("DELETE", "/v1/docstore/stores/{sid}")
def delete_store(rq: Req) -> Any:
    _store(rq, rq.params["sid"])["status"] = "deleted"
    return 204, None


@route("POST", "/v1/docstore/stores/{sid}/workstreams")
def link_store(rq: Req) -> Any:
    s = _store(rq, rq.params["sid"])
    ids = rq.json().get("workstream_ids")
    if not isinstance(ids, list) or not ids:
        raise missing("workstream_ids")
    if "general" in ids:
        raise HTTPError(400, "Cannot link a store to the general workstream")
    for w in ids:
        _check_ws_access(rq, str(w))
    s["workstream_ids"] = sorted(set(s["workstream_ids"]) | set(map(str, ids)))
    return {"linked": [str(i) for i in ids], "store_id": s["id"]}


@route("DELETE", "/v1/docstore/stores/{sid}/workstreams/{wid}")
def unlink_store(rq: Req) -> Any:
    s = _store(rq, rq.params["sid"])
    s["workstream_ids"] = [w for w in s["workstream_ids"] if w != rq.params["wid"]]
    return 204, None


@route("POST", "/v1/docstore/stores/{sid}/upload-urls")
def upload_urls(rq: Req) -> Any:
    st = rq.st
    s = _store(rq, rq.params["sid"])
    body = rq.json()
    files = body.get("files")
    if not isinstance(files, list) or not files:
        raise missing("files")
    override = bool(body.get("override", False))
    paths: list[str] = []
    for f in files:
        if not f.get("filename") or "file_type" not in f or "size_bytes" not in f:
            raise missing("files.filename", "files.file_type", "files.size_bytes")
        paths.append(str(f.get("relative_path") or f["filename"]))
    if len({p.lower() for p in paths}) != len(paths):
        raise HTTPError(409, "DuplicateFilename: duplicate paths within one batch")
    existing = {d["relative_path"].lower(): d for d in st.documents[s["id"]].values()
                if d["status"] != "deleted"}  # fmt: skip
    for p in paths:
        if p.lower() in existing and not override:
            raise HTTPError(409, f"DuplicateFilename: '{p}' already exists in this store")
    for p in paths:
        if p.lower() in existing:
            existing[p.lower()]["status"] = "deleted"
    batch = uuid.uuid4().hex
    out, expected = [], []
    for f, p in zip(files, paths, strict=True):
        key = f"{s['s3_prefix']}/{batch}/{p}"
        expected.append({"filename": f["filename"], "relative_path": p, "s3_key": key,
                         "file_type": f["file_type"], "size_bytes": f["size_bytes"]})  # fmt: skip
        out.append({"filename": f["filename"], "s3_key": key, "expires_in": 900,
                    "presigned_url": f"{st.base_url}/_presigned/put/{key}?sig={secrets.token_hex(8)}"})  # fmt: skip
    st.batches[batch] = {"store_id": s["id"], "expected": expected, "confirmed": False}
    return 201, {"batch_id": batch, "store_id": s["id"], "upload_urls": out}


@route("POST", "/v1/docstore/stores/{sid}/upload-confirm")
def upload_confirm(rq: Req) -> Any:
    st = rq.st
    s = _store(rq, rq.params["sid"])
    bid = rq.json().get("batch_id")
    batch = st.batches.get(str(bid))
    if not bid:
        raise missing("batch_id")
    if batch is None or batch["store_id"] != s["id"]:
        raise HTTPError(404, "Batch not found")
    docs = []
    for item in batch["expected"]:
        data = st.objects.get(item["s3_key"])
        if data is None:
            continue
        did = uuid.uuid4().hex
        doc = {
            "id": did, "store_id": s["id"], "pipeline_id": None, "batch_id": bid, "org_id": st.org_id,
            "filename": item["filename"], "relative_path": item["relative_path"],
            "file_type": item["file_type"], "s3_url": f"s3://fake-bucket/{item['s3_key']}",
            "s3_key": item["s3_key"], "size_bytes": len(data), "chunk_count": 0, "status": "ready",
            "error_message": None, "indexed_at": None, "created_at": now(),
        }  # fmt: skip
        st.documents[s["id"]][did] = doc
        docs.append(doc)
    batch["confirmed"] = True
    return {"batch_id": bid, "store_id": s["id"], "confirmed": len(docs), "documents": docs}


@route("GET", "/v1/docstore/stores/{sid}/documents")
def list_documents(rq: Req) -> Any:
    s = _store(rq, rq.params["sid"])
    status = rq.q("status")
    docs = [d for d in rq.st.documents[s["id"]].values()
            if (d["status"] == status if status else d["status"] != "deleted")]  # fmt: skip
    out = _page(docs, rq, key="documents", max_size=500)
    out.pop("total_pages")
    return out


@route("GET", "/v1/docstore/stores/{sid}/documents/{did}/preview-url")
def preview_url(rq: Req) -> Any:
    s = _store(rq, rq.params["sid"])
    d = rq.st.documents[s["id"]].get(rq.params["did"])
    if d is None or d["status"] == "deleted":
        raise HTTPError(404, "Document not found")
    return {"url": f"{rq.st.base_url}/_presigned/get/{d['s3_key']}?sig={secrets.token_hex(8)}",
            "expires_in": 900}  # fmt: skip


@route("GET", BASE + "/docstore/stores/{sid}/document-download-url")
def console_download_url(rq: Req) -> Any:
    s = _store(rq, rq.params["sid"])
    rel = rq.need_q("relative_path")
    for d in rq.st.documents[s["id"]].values():
        if d["relative_path"] == rel and d["status"] != "deleted":
            return {"url": f"{rq.st.base_url}/_presigned/get/{d['s3_key']}?sig={secrets.token_hex(8)}",
                    "expires_in": 900}  # fmt: skip
    raise HTTPError(404, "Document not found")


@route("GET", BASE + "/docstore/stores")
def console_stores(rq: Req) -> Any:
    return list_stores(rq)


@route("GET", BASE + "/docstore/stores/{sid}/documents")
def console_documents(rq: Req) -> Any:
    return list_documents(rq)


@route("DELETE", "/v1/docstore/stores/{sid}/documents/{did}")
def delete_document(rq: Req) -> Any:
    s = _store(rq, rq.params["sid"])
    d = rq.st.documents[s["id"]].get(rq.params["did"])
    if d is None:
        raise HTTPError(404, "Document not found")
    d["status"] = "deleted"
    return 204, None


@route("POST", "/v1/docstore/stores/{sid}/folders")
def create_folder(rq: Req) -> Any:
    s = _store(rq, rq.params["sid"])
    path = str(rq.json().get("folder_path") or "")
    if not path:
        raise missing("folder_path")
    return {"id": uuid.uuid4().hex, "store_id": s["id"], "folder_path": path, "filename": ".keep"}


# =============================================================================================
# Data Store (org-db)
# =============================================================================================
def _table(st: FakeBFFState, tid: str) -> dict[str, Any]:
    t = st.tables.get(tid)
    if t is None:
        raise HTTPError(404, "Table not found")
    return t


def _table_detail(t: dict[str, Any]) -> dict[str, Any]:
    return {**t, "columns": t["column_schema"]}


def _implicit_cols() -> list[dict[str, Any]]:
    return [{"name": n, "type": "bigint" if n == "id" else "text", "nullable": n != "id",
             "primary": n == "id", "unique": False, "implicit": True} for n in IMPLICIT_COLUMNS]  # fmt: skip


def _check_column(c: Any) -> dict[str, Any]:
    if not isinstance(c, dict) or not c.get("name") or c.get("type") not in COLUMN_TYPES:
        raise HTTPError(422, f"invalid column definition: {c!r}")
    keys = ("name", "type", "nullable", "primary", "unique", "default", "enumVals", "vectorDims",
            "precision", "scale", "index")  # fmt: skip
    col = {k: c[k] for k in keys if k in c}
    col.setdefault("nullable", True)
    col.setdefault("primary", False)
    col.setdefault("unique", False)
    return col


@route("POST", "/v1/org-db/provision")
def orgdb_provision(rq: Req) -> Any:
    return {"db_id": f"db-{rq.tenant}", "status": "ready",
            "deployment": rq.json(False).get("deployment", "container")}  # fmt: skip


@route("GET", "/v1/org-db/tables")
def orgdb_list_tables(rq: Req) -> Any:
    search = (rq.q("search") or "").lower()
    rows = [t for t in rq.st.tables.values() if search in t["table_name"].lower()]
    if rq.q("status"):
        rows = [t for t in rows if t["status"] == rq.q("status")]
    out = _page([{k: v for k, v in t.items() if k != "column_schema"} for t in rows], rq)
    out.pop("total_pages")
    return out


@route("GET", "/v1/org-db/tables/{tid}")
def orgdb_get_table(rq: Req) -> Any:
    t = _table(rq.st, rq.params["tid"])
    return {**_table_detail(t), "row_count": len(rq.st.table_rows[t["id"]])}


@route("POST", "/v1/org-db/tables")
def orgdb_create_table(rq: Req) -> Any:
    body = rq.json()
    name = str(body.get("table_name") or "")
    if not name:
        raise missing("table_name")
    cols = body.get("columns")
    if not isinstance(cols, list) or not cols:
        raise missing("columns")
    if not TABLE_RE.match(name):
        raise HTTPError(422, f"table_name {name!r} must match {TABLE_RE.pattern}")
    if any(t["table_name"] == name for t in rq.st.tables.values()):
        raise HTTPError(409, f"Table '{name}' already exists")
    if body.get("created_source", "ui") not in {"ui", "ai", "wdl", "migration"}:
        # real BFF enum (live 2026-10-29): anything else is a 422
        raise HTTPError(422, "Input should be 'ui', 'ai', 'wdl' or 'migration'")
    checked = [_check_column(c) for c in cols]
    if len({c["name"] for c in checked}) != len(checked):
        raise HTTPError(422, "duplicate column names")
    if any(c["name"] == "workstream_id" for c in checked):
        # the real workflows service adds workstream_id to scoped tables (the default)
        raise HTTPError(
            500,
            'Failed to create table in Postgres: column "workstream_id" specified more than once',
        )
    tid = uuid.uuid4().hex
    rq.st.tables[tid] = {
        "id": tid, "table_name": name, "description": body.get("description"), "status": "active",
        "column_schema": [c for c in _implicit_cols() if c["name"] not in {x["name"] for x in checked}]
        + checked, "workstreams": [], "created_by": rq.sub,
        "created_source": body.get("created_source", "ui"), "created_at": now(),
    }  # fmt: skip
    rq.st.table_rows[tid] = []
    return 201, _table_detail(rq.st.tables[tid])


@route("PATCH", "/v1/org-db/tables/{tid}")
def orgdb_patch_table(rq: Req) -> Any:
    t = _table(rq.st, rq.params["tid"])
    if "description" in rq.json():
        t["description"] = rq.json()["description"]
    return _table_detail(t)


@route("PATCH", "/v1/org-db/tables/{tid}/schema")
def orgdb_schema(rq: Req) -> Any:
    t = _table(rq.st, rq.params["tid"])
    ops = rq.json().get("operations")
    if not isinstance(ops, list) or not 1 <= len(ops) <= 20:
        raise HTTPError(422, "operations must hold 1..20 items")
    cols: list[dict[str, Any]] = t["column_schema"]
    by = {c["name"]: c for c in cols}
    for op in ops:
        action = op.get("action")
        if action == "add_column":
            c = _check_column(op.get("column"))
            if c["name"] in by:
                raise HTTPError(400, f"column '{c['name']}' already exists")
            cols.append(c)
            by[c["name"]] = c
        elif action == "drop_column":
            name = op.get("column_name")
            if name not in by or op.get("confirmation") != name:
                raise HTTPError(
                    400, "drop_column needs an existing column and confirmation == name"
                )
            cols.remove(by.pop(name))
        elif action == "rename_column":
            if op.get("old_name") not in by or not op.get("new_name"):
                raise HTTPError(400, "rename_column: unknown column")
            by[op["old_name"]]["name"] = op["new_name"]
            by[op["new_name"]] = by.pop(op["old_name"])
        elif action == "change_type":
            if op.get("column_name") not in by or op.get("new_type") not in COLUMN_TYPES:
                raise HTTPError(400, "change_type: unknown column or type")
            by[op["column_name"]]["type"] = op["new_type"]
        else:
            raise HTTPError(422, f"unknown action {action!r}")
    return _table_detail(t)


@route("DELETE", "/v1/org-db/tables/{tid}")
def orgdb_delete_table(rq: Req) -> Any:
    t = _table(rq.st, rq.params["tid"])
    if rq.json().get("confirmation") != t["table_name"]:
        raise HTTPError(400, "confirmation must equal the table name")
    del rq.st.tables[t["id"]]
    del rq.st.table_rows[t["id"]]
    return {"deleted": True, "table_name": t["table_name"]}


@route("POST", "/v1/org-db/tables/{tid}/workstreams")
def orgdb_link_ws(rq: Req) -> Any:
    t = _table(rq.st, rq.params["tid"])
    body = rq.json()
    if not body.get("workstream_id"):
        raise missing("workstream_id")
    t["workstreams"] = [w for w in t["workstreams"] if w["workstream_id"] != body["workstream_id"]]
    link = {"id": uuid.uuid4().hex, "workstream_id": body["workstream_id"],
            "permission_mode": body.get("permission_mode", "read")}  # fmt: skip
    t["workstreams"].append(link)
    return link


@route("DELETE", "/v1/org-db/tables/{tid}/workstreams/{wid}")
def orgdb_unlink_ws(rq: Req) -> Any:
    t = _table(rq.st, rq.params["tid"])
    t["workstreams"] = [w for w in t["workstreams"] if w["workstream_id"] != rq.params["wid"]]
    return 204, None


@route("POST", "/v1/org-db/tables/{tid}/assign-workstream", auth="admin")
def orgdb_assign_ws(rq: Req) -> Any:
    _table(rq.st, rq.params["tid"])
    body = rq.json()
    ids = body.get("row_ids")
    if not body.get("workstream_id") or not isinstance(ids, list) or not 1 <= len(ids) <= 1000:
        raise HTTPError(422, "workstream_id and 1..1000 row_ids are required")
    return {"assigned": len(ids)}


def _insert_row(st: FakeBFFState, t: dict[str, Any], row: Any, who: str) -> None:
    if not isinstance(row, dict):
        raise HTTPError(422, "each row must be an object")
    cols = {c["name"]: c for c in t["column_schema"] if not c.get("implicit")}
    unknown = [k for k in row if k not in cols]
    if unknown:
        raise HTTPError(400, f"unknown column(s): {unknown}")
    for name, c in cols.items():
        if row.get(name) is None and not c["nullable"] and c.get("default") is None:
            raise HTTPError(400, f"null value in column '{name}' violates not-null constraint")
        if c["unique"] or c["primary"]:
            val = row.get(name)
            if val is not None and any(r.get(name) == val for r in st.table_rows[t["id"]]):
                raise HTTPError(409, f"duplicate key value violates unique constraint on '{name}'")
    st.row_seq += 1
    st.table_rows[t["id"]].append({"id": st.row_seq, "created_at": now(), "updated_at": now(),
                                   "is_deleted": False, "created_by": who, **row})  # fmt: skip


@route("POST", "/v1/org-db/tables/{tid}/rows")
def orgdb_insert(rq: Req) -> Any:
    t = _table(rq.st, rq.params["tid"])
    body = rq.json()
    rows = body.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
        raise HTTPError(422, "rows must hold 1..500 items")
    if body.get("admin_workstream_id") and not rq.is_admin:
        raise HTTPError(403, "admin_workstream_id requires an Admin")
    inserted = 0
    for row in rows:  # one by one, no transaction: earlier rows stay when a later one fails
        _insert_row(rq.st, t, row, rq.sub)
        inserted += 1
    return 201, {"inserted": inserted}


@route("GET", "/v1/org-db/tables/{tid}/rows")
def orgdb_rows(rq: Req) -> Any:
    t = _table(rq.st, rq.params["tid"])
    rows = [r for r in rq.st.table_rows[t["id"]] if not r["is_deleted"]]
    col, val = rq.q("filter_col"), rq.q("filter_val")
    if col:
        rows = [r for r in rows if str(r.get(col)) == val]
    sort = rq.q("sort_col")
    if sort:
        rows = sorted(rows, key=lambda r: (r.get(sort) is None, str(r.get(sort))),
                      reverse=rq.q("sort_dir", "asc") == "desc")  # fmt: skip
    page = rq.qint("page", 1)
    size = rq.qint("page_size", 100, 1, 500)
    return {"items": rows[(page - 1) * size : page * size], "total": len(rows), "page": page,
            "page_size": size}  # fmt: skip


def _row(st: FakeBFFState, tid: str, rid: str) -> dict[str, Any]:
    t = _table(st, tid)
    for r in st.table_rows[t["id"]]:
        if str(r["id"]) == rid:
            return r
    raise HTTPError(404, "Row not found")


@route("PATCH", "/v1/org-db/tables/{tid}/rows/{rid}")
def orgdb_patch_row(rq: Req) -> Any:
    r = _row(rq.st, rq.params["tid"], rq.params["rid"])
    data = rq.json().get("data")
    if not isinstance(data, dict):
        raise missing("data")
    r.update(data, updated_at=now())
    return r


@route("DELETE", "/v1/org-db/tables/{tid}/rows/{rid}")
def orgdb_delete_row(rq: Req) -> Any:
    r = _row(rq.st, rq.params["tid"], rq.params["rid"])
    rq.st.table_rows[rq.params["tid"]].remove(r)
    return {"deleted": True, "id": r["id"]}


_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|pragma|attach|copy|vacuum)\b|"
    r"pg_catalog|information_schema|sqlite_",
    re.I,
)


@route("POST", "/v1/org-db/sql")
def orgdb_sql(rq: Req) -> Any:
    body = rq.json()
    sql = str(body.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        raise missing("sql")
    limit = int(body.get("limit") or 100)
    if not 1 <= limit <= 10_000:
        raise HTTPError(422, "limit must be 1..10000")
    if ";" in sql or not re.match(r"(?is)^(select|with)\b", sql) or _SQL_FORBIDDEN.search(sql):
        raise HTTPError(400, "Only a single read-only SELECT / WITH...SELECT statement is allowed")
    started = time.time()
    db = sqlite3.connect(":memory:")
    try:
        for t in rq.st.tables.values():
            names = [c["name"] for c in t["column_schema"]]
            db.execute(f'CREATE TABLE "{t["table_name"]}" ({", ".join(f"{n!r}" for n in names)})')
            for r in rq.st.table_rows[t["id"]]:
                if not r["is_deleted"]:
                    vals = [json.dumps(v) if isinstance(v, dict | list) else v
                            for v in (r.get(n) for n in names)]  # fmt: skip
                    db.execute(
                        f'INSERT INTO "{t["table_name"]}" VALUES ({",".join("?" * len(names))})',
                        vals,
                    )
        cur = db.execute(sql)
        cols = [d[0] for d in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(limit)]
    except sqlite3.Error as exc:
        raise HTTPError(400, f"SQL error: {exc}") from None
    finally:
        db.close()
    return {"columns": cols, "rows": rows, "row_count": len(rows),
            "duration_ms": int((time.time() - started) * 1000)}  # fmt: skip


# =============================================================================================
# Pipelines
# =============================================================================================
def _live_pipeline(st: FakeBFFState, pid: str) -> dict[str, Any]:
    p = st.pipelines.get(pid)
    if p is None or p["state"] == "deleted":
        raise HTTPError(404, "Pipeline not found")
    return p


@route("POST", "/v1/pipelines")
def create_pipeline(rq: Req) -> Any:
    st = rq.st
    body = rq.json()
    if not body.get("name"):
        raise missing("name")
    stype = body.get("schedule_type", "manual")
    state = body.get("state", "draft")
    if stype not in ("manual", "cron") or state not in ("draft", "running", "paused"):
        raise HTTPError(422, "schedule_type must be manual|cron and state draft|running|paused")
    pid_proc = body.get("process_id")
    wsids: list[str] = []
    wdl: list[Any] = body.get("wdl") or []
    pname = None
    if pid_proc:
        proc = st.processes.get(str(pid_proc))
        if proc is None or proc.get("deleted"):
            raise HTTPError(404, "Process not found")
        if not proc["is_enabled"]:
            raise HTTPError(400, "The process must be enabled before it can be scheduled")
        if any(body.get(k) for k in ("workstream_ids", "skills", "source", "action_ids")):
            raise HTTPError(400, "A process pipeline takes its workstreams from the process")
        wsids = list(st.process_ws.get(str(pid_proc), []))
        wdl = [{"id": "run_process", "operation": "RUN_PROCESS", "process_id": str(pid_proc)}]
        pname = proc["display_name"]
    pid = uuid.uuid4().hex[:16]
    st.pipelines[pid] = {
        "id": pid, "org_id": st.org_id, "name": body["name"], "description": body.get("description"),
        "process_id": pid_proc, "process_display_name": pname, "wdl": wdl, "destinations": [],
        "schedule_type": stype, "cron_expr": body.get("cron_expr"), "state": state,
        "next_run_at": None, "last_run_at": None, "last_success_at": None, "last_failure_at": None,
        "consecutive_failures": 0, "max_consecutive_failures": 3, "created_by": rq.sub,
        "workstream_ids": wsids or list(body.get("workstream_ids") or []), "created_at": now(),
    }  # fmt: skip
    return 201, st.pipelines[pid]


@route("GET", "/v1/pipelines")
def list_pipelines(rq: Req) -> Any:
    rows = [p for p in rq.st.pipelines.values() if p["state"] != "deleted"]
    for key in ("state", "schedule_type"):
        if rq.q(key):
            rows = [p for p in rows if p[key] == rq.q(key)]
    if rq.q("search"):
        rows = [p for p in rows if str(rq.q("search")).lower() in p["name"].lower()]
    if rq.q("workstream_id"):
        rows = [p for p in rows if rq.q("workstream_id") in p["workstream_ids"]]
    return _page(rows, rq)


@route("GET", "/v1/pipelines/{pid}")
def get_pipeline(rq: Req) -> Any:
    return _live_pipeline(rq.st, rq.params["pid"])


@route("POST", "/v1/pipelines/{pid}/update")
def update_pipeline(rq: Req) -> Any:
    p = _live_pipeline(rq.st, rq.params["pid"])
    body = {k: v for k, v in rq.json().items() if v is not None}
    if p["process_id"] and any(
        body.get(k) for k in ("skills", "source", "action_ids", "wdl", "workstream_ids")
    ):
        raise HTTPError(400, "A process pipeline cannot change its type or take workstreams")
    if "state" in body and body["state"] not in ("draft", "running", "paused"):
        raise HTTPError(422, "state must be draft|running|paused")
    if "schedule_type" in body and body["schedule_type"] not in ("manual", "cron"):
        raise HTTPError(422, "schedule_type must be manual|cron")
    for k in ("name", "description", "prompt", "schedule_type", "cron_expr", "state", "remark",
              "max_consecutive_failures"):  # fmt: skip
        if k in body:
            p[k] = body[k]
    return p


@route("DELETE", "/v1/pipelines/{pid}")
def delete_pipeline(rq: Req) -> Any:
    _live_pipeline(rq.st, rq.params["pid"])["state"] = "deleted"
    return 204, None


@route("GET", "/v1/pipelines/{pid}/runs")
def pipeline_runs(rq: Req) -> Any:
    _live_pipeline(rq.st, rq.params["pid"])
    rows = list(reversed(rq.st.pipeline_runs.get(rq.params["pid"], [])))
    if rq.q("status"):
        rows = [r for r in rows if r["status"] == rq.q("status")]
    return _page(rows, rq)


def _pipeline_run(rq: Req) -> dict[str, Any]:
    _live_pipeline(rq.st, rq.params["pid"])
    for r in rq.st.pipeline_runs.get(rq.params["pid"], []):
        if r["id"] == rq.params["rid"]:
            return r
    raise HTTPError(404, "Run not found")


@route("GET", "/v1/pipelines/{pid}/runs/{rid}/detail")
def pipeline_run_detail(rq: Req) -> Any:
    return {**_pipeline_run(rq), "steps": []}


@route("POST", "/v1/pipelines/{pid}/runs/{rid}/stop")
def pipeline_run_stop(rq: Req) -> Any:
    run = _pipeline_run(rq)
    run["status"] = "cancelled"
    return {"stopped": True}


@route("POST", "/v1/pipelines/{pid}/runs/{rid}/resolve")
def pipeline_run_resolve(rq: Req) -> Any:
    run = _pipeline_run(rq)
    body = rq.json()
    esc = rq.st.pipeline_escalations.get(str(body.get("escalation_id")))
    if esc is None or esc["pipeline_run_id"] != run["id"]:
        raise HTTPError(404, "Escalation not found for this run")
    esc.update(
        status="resolved",
        resolution={k: body.get(k) for k in ("message", "decision", "corrections")},
    )
    run["status"] = "running"
    return {"resolved": True, "escalation_id": esc["id"]}


@route("POST", "/v1/pipelines/workflows/test-run")
def pipeline_test_run(rq: Req) -> Any:
    st = rq.st
    body = rq.json()
    pipe = _live_pipeline(st, str(body.get("pipeline_id") or ""))
    runs = st.pipeline_runs.setdefault(pipe["id"], [])
    if not body.get("test_mode", True) and any(r["status"] == "running" for r in runs):
        raise HTTPError(409, "A run is already running for this pipeline")
    run = st.add_pipeline_run(pipe["id"], status="succeeded")
    pipe.update(last_run_at=run["started_at"], last_success_at=run["finished_at"])
    return {"run_id": run["id"], "status": run["status"]}


# =============================================================================================
# Vault
# =============================================================================================
def _secret_meta(s: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in s.items() if k != "value"}


@route("POST", "/v1/settings/secrets", auth="admin")
def vault_create(rq: Req) -> Any:
    body = rq.json()
    for f in ("key", "value", "group_name"):
        if not body.get(f):
            raise missing(f)
    if len(str(body["key"])) > 255 or len(str(body.get("description") or "")) > 500:
        raise HTTPError(422, "key or description too long")
    if body["key"] in rq.st.vault:
        raise HTTPError(409, f"Secret '{body['key']}' already exists")
    rec = {
        "key": body["key"], "value": body["value"], "description": body.get("description"),
        "group_name": body["group_name"], "created_by_email": rq.email, "created_at": now(),
        "updated_at": now(), "created_by": rq.sub, "last_updated_by": rq.sub,
    }  # fmt: skip
    rq.st.vault[str(body["key"])] = rec
    return 201, _secret_meta(rec)


@route("GET", "/v1/settings/secrets", auth="admin")
def vault_list(rq: Req) -> Any:
    search = (rq.q("search") or "").lower()
    rows = [_secret_meta(s) for s in rq.st.vault.values() if search in s["key"].lower()]
    return {"items": rows[: rq.qint("limit", 200, 1, 200)], "total": len(rows)}


@route("PUT", "/v1/settings/secrets/{key}", auth="admin")
def vault_update(rq: Req) -> Any:
    s = rq.st.vault.get(rq.params["key"])
    if s is None:
        raise HTTPError(404, "Secret not found")
    body = rq.json()
    if not body.get("value"):
        raise missing("value")
    s.update(value=body["value"], updated_at=now(), last_updated_by=rq.sub)
    if "description" in body:
        s["description"] = body["description"]
    return _secret_meta(s)


@route("DELETE", "/v1/settings/secrets/{key}", auth="admin")
def vault_delete(rq: Req) -> Any:
    if rq.st.vault.pop(rq.params["key"], None) is None:
        raise HTTPError(404, "Secret not found")
    return {"deleted": True, "key": rq.params["key"]}


# =============================================================================================
# Server
# =============================================================================================
class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # silence the default stderr log
        return

    def _send(
        self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None
    ) -> None:
        record = getattr(self, "_record", None)
        if record is not None:
            record(status)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle(self) -> None:
        st = self.server.state
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        if url.path.startswith("/_presigned/"):
            self._presigned(url, body)
            return
        logged = False

        def record(status: int) -> None:
            nonlocal logged
            if logged:
                return
            logged = True
            entry = {
                "method": self.command, "path": url.path,
                "query": {k: v[0] for k, v in parse_qs(url.query).items()}, "status": status,
                "has_auth": "authorization" in headers,
                "x_workflows_secret": "x-workflows-secret" in headers,
                "body_keys": _body_keys(body, headers.get("content-type", "")),
            }  # fmt: skip
            with st.lock:
                st.requests.append(entry)

        # the entry is logged before the response goes out, so a client that reads the log right
        # after its call returns always sees its own request
        self._record = record
        status = 500
        try:
            status = self._dispatch(st, url, body, headers)
        finally:
            record(status)

    def _dispatch(self, st: FakeBFFState, url: Any, body: bytes, headers: dict[str, str]) -> int:
        query = parse_qs(url.query, keep_blank_values=True)
        match: tuple[Handler, dict[str, str], str] | None = None
        path_known = False
        for method, regex, auth, fn in ROUTES:
            m = regex.match(url.path)
            if m:
                path_known = True
                if method == self.command:
                    match = (fn, m.groupdict(), auth)
                    break
        if match is None:
            status = 405 if path_known else 404
            self._send(status, json.dumps({"detail": "Not Found" if status == 404 else "Method Not Allowed"}).encode(),
                       "application/json")  # fmt: skip
            return status
        fn, params, auth = match
        rq = Req(
            st,
            self.command,
            url.path,
            query,
            body,
            headers,
            {k: unquote(v) for k, v in params.items()},
        )
        try:
            if auth != "none":
                token = headers.get("authorization", "")
                claims = decode_jwt(token[7:]) if token.lower().startswith("bearer ") else None
                if claims is None or not claims.get("tenantId"):
                    raise HTTPError(401, "Not authenticated")
                rq.claims = claims
                if auth == "admin" and not rq.is_admin:
                    raise HTTPError(403, "Admin role required")
                if auth == "adopt" and not rq.email.endswith("@adopt.ai"):
                    raise HTTPError(403, "Adopt staff only")
            with st.lock:
                result = fn(rq)
            return self._reply(result)
        except HTTPError as exc:
            self._send(exc.status, json.dumps({"detail": exc.detail}).encode(), "application/json")
            return exc.status
        except Exception as exc:  # a bug in the fake must be loud, not a silent 200
            self._send(
                500, json.dumps({"detail": f"fake BFF error: {exc!r}"}).encode(), "application/json"
            )
            return 500

    def _reply(self, result: Any) -> int:
        if isinstance(result, Raw):
            self._send(result.status, result.body, result.content_type, result.headers)
            return result.status
        if isinstance(result, Stream):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            try:
                for line in result.lines:
                    self.wfile.write(line.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return 200
        status = 200
        if isinstance(result, tuple):
            status, result = result
        if result is None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return status
        self._send(status, json.dumps(result, default=str).encode(), "application/json")
        return status

    def _presigned(self, url: Any, body: bytes) -> None:
        st = self.server.state
        m = re.match(r"^/_presigned/(put|get)/(.+)$", url.path)
        with st.lock:
            st.storage_requests.append({"method": self.command, "path": url.path})
            if not m or not parse_qs(url.query).get("sig"):
                self._send(403, b"forbidden", "text/plain")
            elif m.group(1) == "put" and self.command == "PUT":
                st.objects[unquote(m.group(2))] = body
                self._send(200, b"", "text/plain", {"ETag": hashlib.md5(body).hexdigest()})  # noqa: S324
            elif (
                m.group(1) == "get" and self.command == "GET" and unquote(m.group(2)) in st.objects
            ):
                self._send(200, st.objects[unquote(m.group(2))], "application/octet-stream")
            else:
                self._send(404, b"not found", "text/plain")

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _handle  # noqa: N815


def _body_keys(body: bytes, ctype: str) -> list[str]:
    """Top-level JSON keys of a request body (never values: the log must not hold secrets)."""
    if "json" not in ctype or not body:
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    return sorted(data) if isinstance(data, dict) else []


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    state: FakeBFFState


def route_table() -> list[str]:
    """Every implemented route as ``METHOD /path/{param}`` (the README coverage table)."""
    out = []
    for method, regex, auth, _ in ROUTES:
        pattern = re.sub(r"\(\?P<(\w+)>\[\^/\]\+\)", r"{\1}", regex.pattern[1:-1])
        out.append(f"{method} {pattern} [{auth}]")
    return out


class FakeBFFServer:
    """``with FakeBFFServer() as bff:`` then point ``ADOPT_WEBUI_ENDPOINT`` at ``bff.url``."""

    def __init__(self) -> None:
        self.state = FakeBFFState()
        self._httpd = _Server(("127.0.0.1", 0), _Handler)
        self._httpd.state = self.state
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self.state.base_url = self.url
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> FakeBFFServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> FakeBFFServer:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # convenience for tests
    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.state.requests

    def forbidden_calls(self) -> list[dict[str, Any]]:
        """Requests that left the BFF path: not under /v1/, or carrying X-Workflows-Secret."""
        return [r for r in self.state.requests
                if not r["path"].startswith("/v1/") or r["x_workflows_secret"]]  # fmt: skip

    def route_keys(self) -> set[str]:
        """``METHOD /route/pattern`` of every route served at least once (for coverage)."""
        seen: set[str] = set()
        for r in self.state.requests:
            for method, regex, _, _ in ROUTES:
                if method == r["method"] and regex.match(r["path"]):
                    pattern = re.sub(r"\(\?P<(\w+)>\[\^/\]\+\)", r"{\1}", regex.pattern[1:-1])
                    seen.add(f"{method} {pattern}")
                    break
        return seen
