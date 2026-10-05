"""Generalize a compiled skill: prune noise, rename, reword -- by plan, not by hand.

WHY A PLAN FILE. A compile is a verbatim mirror of one recording: it carries the
calls the page happened to fire (keepalives, telemetry, duplicates) and names
lifted straight from URL paths (``get_api_notes_2``). Cleaning that up needs
judgment, so it is an agent step -- but agents editing ``operations.json``
directly is exactly how browser skills lost their provenance. So generalize is
split in three:

1. ``draft_plan``   deterministic: classifies every operation (noise or not, and
                    why), suggests a natural name and description, and writes a
                    plan with a pre-filled decision per operation.
2. the agent edits the decisions, shows the plan to the member, and the member
   confirms (``confirm_plan`` stamps who/when). Nothing is applied before that.
3. ``apply_plan``   deterministic: applies only what the skill KIND allows and
                    re-renders SKILL.md/API.md from the result.

WHAT EACH KIND ALLOWS.

- replay skills (``call_web_api`` recipes, harness execution mode): drop
  operations, rename them, reword descriptions, rename PATH parameters (they are
  only template placeholders) and describe any parameter. Query/body parameter
  names are wire keys -- renaming one would change the request -- so they can be
  described but not renamed.
- browser skills (recorded steps + provenance): rename and reword only. The
  steps, parameters and which operations exist come from the recording; the
  installer's steps digest refuses anything else (see provenance.steps_digest).
  Renaming does move the replay-approval fingerprint, so an approved skill has
  to be replayed and approved again -- apply says so.

The compiler stays deterministic and makes no LLM calls; neither does this module.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PLAN_FILE = "generalize_plan.json"
PLAN_VERSION = 1

ACTIONS = ("keep", "drop")

# Path tokens that mark a request as incidental to the task.
_KEEPALIVE = {
    "health",
    "healthz",
    "livez",
    "readyz",
    "ping",
    "heartbeat",
    "keepalive",
    "keep-alive",
    "alive",
}
_TELEMETRY = {
    "analytics",
    "telemetry",
    "collect",
    "beacon",
    "track",
    "tracking",
    "pixel",
    "gen_204",
    "metrics",
    "rum",
    "log",
    "logs",
    "logging",
    "tr",
    "consent",
    "get-data-layer-variables",
    "feature-flags",
    "featureflags",
    "flags",
}
_TELEMETRY_HOSTS = (
    "google-analytics.",
    "googletagmanager.",
    "doubleclick.",
    "sentry.",
    "datadoghq.",
    "segment.",
    "hotjar.",
    "mixpanel.",
    "amplitude.",
    "newrelic.",
    "nr-data.",
    "launchdarkly.",
    "optimizely.",
    "fullstory.",
    "clarity.ms",
)
_STATIC_EXT = (
    ".js",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
)
_SKIP_SEGMENTS = {"api", "rest", "v1", "v2", "v3", "v4", "public", "internal"}
_VERB = {"POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}


class GeneralizeError(Exception):
    """A plan cannot be drafted or applied as asked."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise GeneralizeError(f"{path} not found") from e
    except ValueError as e:
        raise GeneralizeError(f"{path} is not valid JSON: {e}") from e


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_operations(skill_dir: Path) -> list[dict[str, Any]]:
    if not (skill_dir / "operations.json").exists() and (skill_dir / "operations").is_dir():
        raise GeneralizeError(
            f"{skill_dir} is a tabby/http execution-mode skill (Python operation modules); "
            "generalize works on harness skills -- recompile it from the saved bundle instead."
        )
    doc = _read_json(skill_dir / "operations.json")
    ops = doc.get("operations") if isinstance(doc, dict) else doc
    if not isinstance(ops, list):
        raise GeneralizeError(f"{skill_dir / 'operations.json'} has no operations list")
    return ops


def skill_kind(skill_dir: Path, operations: list[dict[str, Any]]) -> str:
    """``browser`` for recorded-step skills, ``replay`` for call_web_api recipes."""
    manifest: dict[str, Any] = {}
    if (skill_dir / "manifest.json").exists():
        loaded = _read_json(skill_dir / "manifest.json")
        manifest = loaded if isinstance(loaded, dict) else {}
    if manifest.get("provenance") or (skill_dir / "recording_bundle.json").exists():
        return "browser"
    if any("steps" in op or op.get("tool") == "call_web_browser" for op in operations):
        return "browser"
    if any("url_template" in op for op in operations):
        return "replay"
    raise GeneralizeError(
        f"{skill_dir} is not a harness skill (no call_web_api recipes or browser steps). "
        "tabby/http execution-mode skills ship Python operation modules; recompile "
        "them from the saved bundle instead."
    )


# ---------------------------------------------------------------------------
# Classification + suggestions
# ---------------------------------------------------------------------------


def _segments(path: str) -> list[str]:
    return [s for s in path.split("?")[0].split("/") if s]


def _registrable(host: str) -> str:
    parts = host.lower().split(":")[0].split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def _primary_domain(operations: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for op in operations:
        host = urlsplit(str(op.get("url_template") or "")).netloc
        if host:
            dom = _registrable(host)
            counts[dom] = counts.get(dom, 0) + 1
    return max(counts, key=lambda d: counts[d]) if counts else ""


def classify(op: dict[str, Any], *, primary_domain: str, seen: set[tuple[str, str]]) -> list[str]:
    """Reasons an operation looks incidental to the task (empty = looks real)."""
    url = str(op.get("url_template") or "")
    parts = urlsplit(url)
    host = parts.netloc.lower()
    segs = [s.lower() for s in _segments(parts.path)]
    reasons: list[str] = []

    key = (str(op.get("method") or "").upper(), url)
    if url and key in seen:
        reasons.append("duplicate of an earlier operation (same method + URL)")
    seen.add(key)

    if segs and segs[-1] in _KEEPALIVE:
        reasons.append(f"keepalive/health endpoint (/{segs[-1]})")
    hits = sorted({s for s in segs if s in _TELEMETRY})
    if hits:
        reasons.append(f"telemetry/consent/flags path ({', '.join('/' + h for h in hits)})")
    if any(marker in host for marker in _TELEMETRY_HOSTS):
        reasons.append(f"known telemetry host ({host})")
    if parts.path.lower().endswith(_STATIC_EXT):
        reasons.append("static asset")
    if host and primary_domain and _registrable(host) != primary_domain and not reasons:
        # Not dropped by default: a workflow can legitimately span an auth host
        # and an API host. Flagged so a human looks.
        reasons.append(f"REVIEW: third-party host {host} (app is on {primary_domain})")
    return reasons


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("ses") or word.endswith("xes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 1:
        return word[:-1]
    return word


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def suggest_name(op: dict[str, Any]) -> str:
    """``GET /api/notes`` -> ``list_notes``; ``GET /api/notes/{id}`` -> ``get_note``."""
    method = str(op.get("method") or "GET").upper()
    segs = _segments(urlsplit(str(op.get("url_template") or "")).path)
    words = [s for s in segs if s.lower() not in _SKIP_SEGMENTS]
    ends_with_param = bool(words) and words[-1].startswith("{")
    nouns = [_slug(w) for w in words if not w.startswith("{")]
    nouns = [n for n in nouns if n]
    if not nouns:
        return _slug(str(op.get("name") or "operation")) or "operation"
    resource = nouns[-1]
    qualifier = "_".join(nouns[:-1][-1:])  # at most one parent segment for context
    if method == "GET":
        verb, noun = ("get", _singular(resource)) if ends_with_param else ("list", resource)
    else:
        verb = _VERB.get(method, method.lower())
        noun = _singular(resource)
    name = f"{verb}_{noun}"
    return (
        f"{name}_for_{_singular(qualifier)}"
        if qualifier and ends_with_param and len(nouns) > 1
        else name
    )


def suggest_description(op: dict[str, Any], name: str) -> str:
    verb, _, rest = name.partition("_")
    noun = rest.replace("_", " ") or "resource"
    params = [p.get("name") for p in op.get("path_params") or [] if p.get("name")]
    by = f" by {', '.join(str(p) for p in params)}" if params else ""
    text = {
        "list": f"List {noun}",
        "get": f"Get a {noun}{by}",
        "create": f"Create a {noun}",
        "update": f"Update a {noun}{by}",
        "delete": f"Delete a {noun}{by}",
    }.get(verb, f"{verb.capitalize()} {noun}{by}")
    return text + "."


def _unique(name: str, taken: set[str]) -> str:
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name}_{n}", n + 1
    taken.add(candidate)
    return candidate


# ---------------------------------------------------------------------------
# Draft / confirm
# ---------------------------------------------------------------------------


def draft_plan(skill_dir: Path) -> dict[str, Any]:
    """Build a draft plan with a suggested decision for every operation."""
    operations = load_operations(skill_dir)
    kind = skill_kind(skill_dir, operations)
    primary = _primary_domain(operations)
    seen: set[tuple[str, str]] = set()
    taken: set[str] = set()
    entries: list[dict[str, Any]] = []
    for op in operations:
        name = str(op.get("name") or "")
        entry: dict[str, Any] = {"name": name, "description": op.get("description", "")}
        if kind == "replay":
            reasons = classify(op, primary_domain=primary, seen=seen)
            drop = any(not r.startswith("REVIEW:") for r in reasons)
            new_name = name if drop else _unique(suggest_name(op), taken)
            entry.update(
                {
                    "method": op.get("method"),
                    "url_template": op.get("url_template"),
                    "noise_reasons": reasons,
                    "decision": {
                        "action": "drop" if drop else "keep",
                        "rename": new_name if new_name != name else None,
                        "description": None if drop else suggest_description(op, new_name),
                        "params": {},
                    },
                }
            )
        else:
            entry.update(
                {
                    "kind": op.get("kind") or "read",
                    "noise_reasons": [],
                    "decision": {"action": "keep", "rename": None, "description": None},
                }
            )
        entries.append(entry)
    return {
        "version": PLAN_VERSION,
        "skill": skill_dir.name,
        "kind": kind,
        "status": "draft",
        "drafted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "allowed": (
            ["drop", "rename", "description", "path-param rename", "param description"]
            if kind == "replay"
            else ["rename", "description"]
        ),
        "how_to_use": (
            "Review every decision (action keep|drop, rename, description"
            + (", params{name:{rename?,description?}}" if kind == "replay" else "")
            + "). Show the plan to the member; only after they agree run "
            "`generalize.py confirm`, then `generalize.py apply`."
        ),
        "operations": entries,
    }


def write_draft(skill_dir: Path, *, force: bool = False) -> Path:
    path = skill_dir / PLAN_FILE
    if path.exists() and not force:
        existing = _read_json(path)
        if isinstance(existing, dict) and existing.get("status") in ("confirmed", "applied"):
            raise GeneralizeError(
                f"{path} is already {existing.get('status')}; pass --force to draft a new plan."
            )
    _write_json(path, draft_plan(skill_dir))
    return path


def validate_plan(plan: dict[str, Any], operations: list[dict[str, Any]], kind: str) -> list[str]:
    """Every problem with a plan, so they can all be fixed in one pass."""
    problems: list[str] = []
    if plan.get("version") != PLAN_VERSION:
        problems.append(f"unsupported plan version {plan.get('version')!r}")
    if plan.get("kind") != kind:
        problems.append(f"plan was drafted for a {plan.get('kind')} skill; this is a {kind} skill")
    by_name = {str(op.get("name")): op for op in operations}
    entries = plan.get("operations") or []
    names = [str(e.get("name")) for e in entries]
    if sorted(names) != sorted(by_name):
        problems.append("plan operations do not match operations.json (recompiled since drafting?)")
    final: list[str] = []
    for e in entries:
        d = e.get("decision") or {}
        name = str(e.get("name"))
        action = d.get("action")
        if action not in ACTIONS:
            problems.append(f"{name}: action must be one of {ACTIONS}")
            continue
        if kind == "browser" and action == "drop":
            problems.append(
                f"{name}: browser skills cannot drop operations (the set comes from the recording)"
            )
        if action == "drop":
            continue
        new = d.get("rename") or name
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", str(new)):
            problems.append(f"{name}: rename {new!r} must match [a-zA-Z0-9_-]{{1,64}}")
        final.append(str(new))
        params = d.get("params") or {}
        if params and kind == "browser":
            problems.append(f"{name}: browser skills cannot change parameters")
            continue
        op = by_name.get(name, {})
        path_names = {p.get("name") for p in op.get("path_params") or []}
        all_names = path_names | {
            p.get("name") for key in ("query_params", "body_params") for p in op.get(key) or []
        }
        for pname, change in params.items():
            if pname not in all_names:
                problems.append(f"{name}: unknown parameter {pname!r}")
                continue
            if not isinstance(change, dict):
                problems.append(f"{name}.{pname}: expected {{rename?, description?}}")
                continue
            if change.get("rename") and pname not in path_names:
                problems.append(
                    f"{name}.{pname}: only path parameters can be renamed (query/body names are "
                    "sent on the wire); describe it instead"
                )
            if change.get("rename") and not re.fullmatch(
                r"[a-zA-Z_][a-zA-Z0-9_]*", str(change["rename"])
            ):
                problems.append(f"{name}.{pname}: rename {change['rename']!r} is not an identifier")
        renamed = {
            p: str(c["rename"])
            for p, c in params.items()
            if isinstance(c, dict) and c.get("rename") and p in path_names
        }
        after = [renamed.get(str(n), str(n)) for n in all_names]
        clashes = sorted({n for n in after if after.count(n) > 1})
        if clashes:
            problems.append(f"{name}: parameter names would collide: {', '.join(clashes)}")
    dupes = sorted({n for n in final if final.count(n) > 1})
    if dupes:
        problems.append(f"operation names would collide: {', '.join(dupes)}")
    if not final:
        problems.append("the plan drops every operation")
    return problems


def confirm_plan(skill_dir: Path, *, confirmed_by: str) -> Path:
    path = skill_dir / PLAN_FILE
    plan = _read_json(path)
    operations = load_operations(skill_dir)
    problems = validate_plan(plan, operations, skill_kind(skill_dir, operations))
    if problems:
        raise GeneralizeError("plan is not valid:\n  - " + "\n  - ".join(problems))
    if plan.get("status") == "applied":
        raise GeneralizeError("plan was already applied; draft a new one to change the skill again")
    plan["status"] = "confirmed"
    plan["confirmed_by"] = confirmed_by
    plan["confirmed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    _write_json(path, plan)
    return path


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _apply_to_recipe(op: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    op = json.loads(json.dumps(op))  # deep copy
    if decision.get("rename"):
        op["name"] = decision["rename"]
    if decision.get("description"):
        op["description"] = decision["description"]
    changes = decision.get("params") or {}
    renames = {
        p: str(c["rename"])
        for p, c in changes.items()
        if c.get("rename") and p in {x.get("name") for x in op.get("path_params") or []}
    }
    for key in ("path_params", "query_params", "body_params"):
        for p in op.get(key) or []:
            change = changes.get(p.get("name")) or {}
            if change.get("description"):
                p["description"] = change["description"]
            if key == "path_params" and p.get("name") in renames:
                p["name"] = renames[p["name"]]
    # One pass over the template, so swaps (a->b, b->a) and chains stay correct.
    op["url_template"] = _rename_placeholders(str(op.get("url_template", "")), renames)
    return op


def _rename_placeholders(template: str, renames: dict[str, str]) -> str:
    if not renames:
        return template
    return re.sub(
        r"\{([^{}]+)\}", lambda m: "{" + renames.get(m.group(1), m.group(1)) + "}", template
    )


def _apply_to_browser_op(op: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    op = dict(op)
    if decision.get("rename"):
        op["name"] = decision["rename"]
    if decision.get("description"):
        op["description"] = decision["description"]
    return op


def _tool_defs_from_recipes(recipes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inverse of harness_md_generator.build_operation_recipe, for re-rendering docs."""
    defs: list[dict[str, Any]] = []
    for r in recipes:
        url = str(r.get("url_template") or "")
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}" if parts.netloc else ""
        path = url[len(base) :] if base else url
        params: list[dict[str, Any]] = []
        for key, source in (
            ("path_params", "path"),
            ("query_params", "query"),
            ("body_params", "body"),
        ):
            for p in r.get(key) or []:
                params.append({**p, "source": source})
        headers = [
            {"name": k, "value": v}
            for k, v in (r.get("headers") or {}).items()
            if not str(v).startswith("${SECRET:")
        ]
        td: dict[str, Any] = {
            "name": r.get("name"),
            "description": r.get("description", r.get("name")),
            "method": str(r.get("method") or "GET").upper(),
            "base_url": base,
            "path": path,
            "params": params,
            "request_headers": headers,
        }
        if r.get("content_type"):
            td["request_content_type"] = r["content_type"]
        defs.append(td)
    return defs


def _frontmatter_description(text: str | None) -> str:
    """The SKILL.md description, without needing PyYAML (not a bundle dependency).

    Reads the single-line form the compiler writes (see _escape_yaml_scalar): a plain
    scalar, or a double-quoted one with \\ and \" escaped. A hand-written block scalar
    falls back to PyYAML when it happens to be installed.
    """
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text or "", re.DOTALL)
    if not m:
        return ""
    line = re.search(r"^description:[ \t]*(.*)$", m.group(1), re.MULTILINE)
    if not line:
        return ""
    raw = line.group(1).strip()
    if raw in ("|", ">", "|-", ">-", "") or raw.startswith("'"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            return raw
        try:
            meta = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            return raw
        return str(meta.get("description") or "") if isinstance(meta, dict) else raw
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return re.sub(r'\\(["\\])', r"\1", raw[1:-1])
    return raw


def _rerender_replay_docs(
    skill_dir: Path,
    recipes: list[dict[str, Any]],
    manifest: dict[str, Any],
    old_recipes: list[dict[str, Any]] | None = None,
) -> None:
    from noui_core.compile.api_doc_generator import generate_api_markdown
    from noui_core.compile.harness_md_generator import render_harness_skill_md

    auth_plan: dict[str, Any] = {}
    if (skill_dir / "auth_plan.json").exists():
        loaded = _read_json(skill_dir / "auth_plan.json")
        auth_plan = loaded if isinstance(loaded, dict) else {}
    tool_defs = _tool_defs_from_recipes(recipes)
    app = manifest.get("app") or {}
    workflow = manifest.get("workflow") or {}
    profile = (manifest.get("auth") or {}).get("profile_slug") or ""
    skill_md = skill_dir / "SKILL.md"
    existing = skill_md.read_text(encoding="utf-8") if skill_md.exists() else None
    # The SKILL.md name is the catalog slug and may have been changed after the
    # compile (e.g. prefixed for an org); never let a re-render revert it.
    current = re.match(r"\A---\s*\nname:\s*(\S+)\s*\n", existing or "")
    skill_id = current.group(1) if current else str(manifest.get("skill_id") or skill_dir.name)
    render_args: dict[str, Any] = {
        "skill_id": skill_id,
        "app_name": str(app.get("name") or skill_id),
        "workflow_name": str(workflow.get("name") or skill_id),
        "auth_plan": auth_plan,
        "profile_slug": str(profile),
    }
    # The frontmatter description is regenerated from the operations -- unless a human
    # wrote it. Tell the two apart by re-rendering the OLD operations: if that is what
    # the file says, it was never edited and can follow the new operations.
    description_override = ""
    current_desc = _frontmatter_description(existing)
    if current_desc and old_recipes is not None:
        generated = _frontmatter_description(
            render_harness_skill_md(tool_defs=_tool_defs_from_recipes(old_recipes), **render_args)
        )
        if current_desc != generated:
            description_override = current_desc
    skill_md.write_text(
        render_harness_skill_md(
            tool_defs=tool_defs,
            existing=existing,
            description_override=description_override,
            **render_args,
        ),
        encoding="utf-8",
    )
    api_md = skill_dir / "API.md"
    if api_md.exists():
        api_md.write_text(
            generate_api_markdown(
                server_id=skill_id,
                app_name=str(app.get("name") or skill_id),
                app_slug=str(app.get("slug") or skill_id),
                workflow_name=str(workflow.get("name") or skill_id),
                tool_defs=tool_defs,
                tabby_profile_id=str(profile),
                existing=api_md.read_text(encoding="utf-8"),
            ),
            encoding="utf-8",
        )


def _rename_in_browser_skill_md(skill_dir: Path, renames: dict[str, str]) -> None:
    skill_md = skill_dir / "SKILL.md"
    if not renames or not skill_md.exists():
        return
    text = skill_md.read_text(encoding="utf-8")
    # One alternation pass, so chains (a->b, b->c) do not cascade.
    pattern = "|".join(re.escape(old) for old in sorted(renames, key=len, reverse=True))
    text = re.sub(
        rf"(?<![A-Za-z0-9_-])(?:{pattern})(?![A-Za-z0-9_-])", lambda m: renames[m.group(0)], text
    )
    skill_md.write_text(text, encoding="utf-8")


def apply_plan(skill_dir: Path) -> dict[str, Any]:
    """Apply a confirmed plan. Returns a summary of what changed."""
    path = skill_dir / PLAN_FILE
    plan = _read_json(path)
    if plan.get("status") != "confirmed":
        raise GeneralizeError(
            f"plan status is {plan.get('status')!r}: show it to the member and run "
            "`generalize.py confirm` once they agree"
        )
    doc = _read_json(skill_dir / "operations.json")
    operations = load_operations(skill_dir)
    kind = skill_kind(skill_dir, operations)
    problems = validate_plan(plan, operations, kind)
    if problems:
        raise GeneralizeError("plan is not valid:\n  - " + "\n  - ".join(problems))

    decisions = {str(e["name"]): e["decision"] for e in plan["operations"]}
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    renames: dict[str, str] = {}
    for op in operations:
        name = str(op.get("name"))
        d = decisions[name]
        if d["action"] == "drop":
            dropped.append(name)
            continue
        new = _apply_to_recipe(op, d) if kind == "replay" else _apply_to_browser_op(op, d)
        if new.get("name") != name:
            renames[name] = str(new["name"])
        kept.append(new)

    approval_invalidated = False
    if kind == "browser":
        from noui_core.compile.provenance import steps_digest

        if steps_digest(kept) != steps_digest(operations):  # defensive: must never move
            raise GeneralizeError("refusing: the change would alter what the browser skill runs")
        approval_invalidated = bool(renames) and (skill_dir / "replay_approval.json").exists()

    if isinstance(doc, dict):
        doc["operations"] = kept
    else:
        doc = kept
    _write_json(skill_dir / "operations.json", doc)

    manifest: dict[str, Any] = {}
    if (skill_dir / "manifest.json").exists():
        loaded = _read_json(skill_dir / "manifest.json")
        manifest = loaded if isinstance(loaded, dict) else {}
        by_name = {str(op.get("name")): op for op in operations}
        new_entries = []
        for entry in manifest.get("operations") or []:
            old = str(entry.get("name"))
            if old in dropped:
                continue
            d = decisions.get(old)
            if d:
                entry = dict(entry)
                if d.get("rename"):
                    entry["name"] = d["rename"]
                if d.get("description"):
                    entry["description"] = d["description"]
                path_names = {p.get("name") for p in by_name[old].get("path_params") or []}
                param_renames = {
                    p: str(c["rename"])
                    for p, c in (d.get("params") or {}).items()
                    if c.get("rename") and p in path_names
                }
                if param_renames:
                    entry["path"] = _rename_placeholders(str(entry.get("path", "")), param_renames)
                    for arg in entry.get("args") or []:
                        if arg.get("name") in param_renames:
                            arg["name"] = param_renames[arg["name"]]
            new_entries.append(entry)
        if manifest.get("operations") is not None:
            manifest["operations"] = new_entries
        manifest.setdefault("generation", {})["generalized_at"] = datetime.now(UTC).isoformat(
            timespec="seconds"
        )
        _write_json(skill_dir / "manifest.json", manifest)

    if kind == "replay":
        _rerender_replay_docs(skill_dir, kept, manifest, old_recipes=operations)
    else:
        _rename_in_browser_skill_md(skill_dir, renames)

    plan["status"] = "applied"
    plan["applied_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    _write_json(path, plan)
    return {
        "skill": skill_dir.name,
        "kind": kind,
        "kept": [op.get("name") for op in kept],
        "dropped": dropped,
        "renamed": renames,
        "approval_invalidated": approval_invalidated,
    }
