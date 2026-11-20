"""``abcd ws init|clone|status|pull|push-bundles|pull-bundles|hook``: a client workspace is a repo.

Builds on ``cli/builder_common`` (workspace layout, git, hook, drift, agent bundles). The older
``ws list|use|show|create|delete`` live in ``cli/workspace.py``. PLAN.md section 5.

No command here commits to the default branch except the first commit of ``ws init``
(``allow_default``). ``ws pull`` writes to a ``drift/<date>`` branch.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import requests

from cli.builder_common import agent_bundle, drift, precommit, repo
from cli.builder_common import workspace as bws
from cli.builder_common.docstore import DocStore, NotFoundError, Upload
from cli.builder_common.errors import BuilderError, RefusedError, UsageError
from cli.builder_common.orgdb import OrgDBClient
from cli.builder_common.platform import BuilderPlatformClient
from cli.builder_common.versions import VersionLedger
from cli.builder_setup import BUNDLES_STORE, rows_of
from cli.core.output import Result
from cli.core.registry import Registry
from cli.harness_common.api_client import HarnessAPIError

if TYPE_CHECKING:
    from cli.core.context import Context

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _workspace(ctx: Context, *, must_exist: bool = True) -> Path:
    bws.assert_not_default(ctx.env)
    ws = ctx.env_dir
    if must_exist and not ws.is_dir():
        raise UsageError(f"Workspace not found: {ws}")
    return ws


def _require_repo(ws: Path) -> None:
    if not (ws / ".git").exists():
        raise UsageError(f"{ws} is not a git repo; run `abcd ws init --env {ws.name}` first")


def _identity(ws: Path) -> tuple[str, str] | None:
    """A commit identity only when git has none configured (so the commit never fails)."""
    name = repo.run_git(ws, "config", "user.name", check=False).stdout.strip()
    email = repo.run_git(ws, "config", "user.email", check=False).stdout.strip()
    if name and email:
        return None
    return (name or "abcd builder", email or "builder@abcd.invalid")


def _author(ws: Path) -> str:
    return repo.run_git(ws, "config", "user.name", check=False).stdout.strip() or "abcd"


def _has_commit(ws: Path) -> bool:
    return repo.run_git(ws, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def _platform(ctx: Context) -> BuilderPlatformClient:
    return BuilderPlatformClient.wrap(ctx.harness_client())


def _safe_rel(path: str) -> str | None:
    p = PurePosixPath(path)
    if p.is_absolute() or not p.parts or ".." in p.parts:
        return None
    return p.as_posix()


# ---------------------------------------------------------------------------
# ws init
# ---------------------------------------------------------------------------


def _init(ctx: Context) -> Result:
    a = ctx.args
    ws = _workspace(ctx)
    remote: str = a.remote or ""
    if remote:
        repo.check_remote_url(remote)
    if ctx.dry_run:
        plan = [
            "create the Builder layout, builder.json, ADOPT.md and .gitignore",
            "git init and install the pre-commit hook",
            "make the first commit" if not (ws / ".git").exists() else "keep the existing history",
            f"set remote origin to {remote}; push the default branch only if the remote has none "
            "yet (never on a re-run)"
            if remote
            else "no remote given: skip push",
        ]
        return Result.done(
            {"dry_run": True, "workspace": str(ws), "plan": plan},
            "DRY RUN: would " + "; ".join(plan),
        )

    existing = bws.builder_json_path(ws).is_file()
    seed: dict[str, Any] = {"repo_remote": remote}
    if a.default_branch:
        seed["default_branch"] = a.default_branch
    created = bws.init_layout(ws, **seed)
    if existing:
        updates: dict[str, Any] = {}
        if remote:
            updates["repo_remote"] = remote
        if a.default_branch:
            updates["default_branch"] = a.default_branch
        if updates:
            bws.update_builder_config(ws, **updates)
    cfg = bws.load_builder_config(ws)
    branch_name = cfg["default_branch"]

    repo.init_repo(ws, branch_name)
    hook = precommit.install_hook(ws)
    if remote:
        repo.add_remote(ws, remote)

    committed = False
    if not _has_commit(ws):
        repo.commit(ws, "Initialize Adopt Builder workspace", identity=_identity(ws))
        committed = True
    pushed = False
    refused_push = False
    if remote:
        current = repo.current_branch(ws)
        if current == branch_name:
            # Only the run that made the first commit, or a remote with no default branch yet,
            # may push the default branch; a re-run never does (PLAN.md section 5).
            if committed or branch_name not in repo.ls_remote_heads(ws):
                repo.push(ws, current, allow_default=True)
                pushed = True
            else:
                refused_push = True
    data = {
        "workspace": str(ws),
        "created": created,
        "branch": repo.current_branch(ws),
        "sha": repo.current_sha(ws),
        "committed": committed,
        "remote": remote or None,
        "pushed": pushed,
        "push_refused": refused_push,
        "hook": str(hook),
    }
    lines = [f"Workspace {ws.name} is a repo on {data['branch']} ({str(data['sha'])[:10]})."]
    lines.append(
        "Created: " + (", ".join(created) if created else "nothing new (already initialised)")
    )
    lines.append(f"Pre-commit hook: {hook}")
    if pushed:
        lines.append(f"Pushed {data['branch']} to {remote}.")
    elif refused_push:
        lines.append(
            f"Not pushed: {branch_name} already exists on {remote} and this run made no first "
            "commit. abcd never pushes the default branch on a re-run; push a branch and open "
            "a pull request."
        )
    elif remote:
        lines.append(
            f"Not pushed: you are on {data['branch']}, not the default branch {branch_name}."
        )
    else:
        lines.append("No remote yet: rerun with --remote <url> to add it and push.")
    if repo.run_git(ws, "status", "--porcelain", "--", "builder.json", check=False).stdout.strip():
        lines.append(
            "builder.json changed (repo_remote) and is not committed: commit it on a feature "
            "branch and open a pull request; `abcd stage` refuses a dirty tree."
        )
    return Result.done(data, "\n".join(lines))


# ---------------------------------------------------------------------------
# ws clone
# ---------------------------------------------------------------------------


def _env_from_url(url: str) -> str:
    base = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return re.sub(r"\.git$", "", base)


def _clone(ctx: Context) -> Result:
    url: str = ctx.args.url
    env = ctx.env_arg or _env_from_url(url)
    if not env or not _SAFE_NAME.match(env):
        raise UsageError(f"cannot derive a workspace name from {url!r}; pass --env")
    bws.assert_not_default(env)
    dest = ctx.workspaces_dir / env
    repo.check_remote_url(url)
    if ctx.dry_run:
        return Result.done(
            {"dry_run": True, "url": url, "dest": str(dest)},
            f"DRY RUN: would clone {url} into {dest}, write a .env template, install the hook",
        )
    repo.clone(url, dest)
    hook = precommit.install_hook(dest)
    data = {"env": env, "path": str(dest), "hook": str(hook), "branch": repo.current_branch(dest)}
    return Result.done(
        data,
        f"Cloned {url} into {dest}.\nFill in {dest / '.env'} (credentials, never committed), "
        f"then `abcd ws use {env}`.",
    )


# ---------------------------------------------------------------------------
# ws status
# ---------------------------------------------------------------------------


def _platform_drift(ctx: Context) -> dict[str, Any]:
    """Drift summary through ``audit_drift``; degrades to a reason when it cannot run."""
    try:
        client = _platform(ctx)
    except Exception as exc:  # no PAT, no endpoint, bad .env: status must still work
        return {"available": False, "reason": f"no platform client: {exc}"}
    try:
        ledger = VersionLedger(OrgDBClient.from_client(client))
        report = drift.audit_drift(client, ledger)
    except (BuilderError, HarnessAPIError, requests.RequestException) as exc:
        return {"available": False, "reason": f"drift check failed: {exc}"}
    return {"available": True, "has_drift": report.has_drift, **report.to_dict()}


def _status(ctx: Context) -> Result:
    ws = _workspace(ctx)
    _require_repo(ws)
    st = repo.status(ws)
    data: dict[str, Any] = {"workspace": str(ws), **st.to_dict()}
    lines = [
        f"Workspace {ws.name}: branch {st.branch or '(detached)'}"
        + (f" tracking {st.upstream}" if st.upstream else " (no upstream)"),
        f"Ahead {st.ahead}, behind {st.behind}; {len(st.changed)} changed, {len(st.untracked)} untracked.",
    ]
    if st.changed:
        lines.append("Changed: " + ", ".join(st.changed[:20]))
    if st.untracked_builder:
        lines.append("Untracked Builder files: " + ", ".join(st.untracked_builder[:20]))
    if ctx.args.platform:
        plat = _platform_drift(ctx)
        data["platform"] = plat
        if not plat.get("available"):
            lines.append(f"Platform drift: not checked ({plat['reason']})")
        elif plat["has_drift"]:
            lines.append(f"Platform drift: {len(plat['items'])} item(s)")
            for item in plat["items"][:20]:
                lines.append(
                    f"  {item.get('severity', '')}: {item.get('kind', '')} {item.get('name', '')}"
                )
        else:
            lines.append("Platform drift: none")
    return Result.done(data, "\n".join(lines))


# ---------------------------------------------------------------------------
# ws pull
# ---------------------------------------------------------------------------


def _write_skill(ws: Path, item: drift.PullItem) -> tuple[list[str], list[dict[str, str]]]:
    """Write SKILL.md and the text aux files; returns (written rel paths, partial files)."""
    if not _SAFE_NAME.match(item.name):
        return [], [{"skill": item.name, "path": "", "reason": "unsafe skill name"}]
    root = bws.harness_dir(ws) / "skills" / item.name
    written: list[str] = []
    partial: list[dict[str, str]] = []
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(item.skill_md or "", encoding="utf-8")
    written.append(f"harness/skills/{item.name}/SKILL.md")
    for f in item.files:
        rel = _safe_rel(f.path)
        if f.status != "text" or f.content is None or rel is None:
            reason = f.reason if f.status != "text" else "no content"
            partial.append({"skill": item.name, "path": f.path, "reason": reason or "unsafe path"})
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content, encoding="utf-8")
        written.append(f"harness/skills/{item.name}/{rel}")
    return written, partial


def _write_agents(
    ctx: Context, ws: Path, client: BuilderPlatformClient, items: list[drift.PullItem]
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    """Agent bundles for the platform processes in the plan; returns (paths, names, skipped)."""
    if not items:
        return [], [], []
    ids = {str(p.get("name")): str(p.get("id")) for p in drift.list_processes(client)}
    by_id: dict[str, str] = {}
    page = 1
    while True:
        data = client._request(
            "GET", "/v1/org/workstreams", params={"page": page, "page_size": 100}
        )
        rows = rows_of(data, "workstreams")
        for r in rows:
            by_id[str(r["id"])] = str(r["name"])
        if len(rows) < 100:
            break
        page += 1
    paths: list[str] = []
    names: list[str] = []
    skipped: list[dict[str, str]] = []
    for item in items:
        pid = ids.get(item.name)
        if pid is None or not _SAFE_NAME.match(item.name):
            skipped.append({"agent": item.name, "reason": "no id or unsafe name"})
            continue
        try:
            process = client.get_process(pid)
            assigned = client.get_process_workstreams(pid).get("workstream_ids") or []
            try:
                grants: dict[str, Any] | None = client.get_write_workstreams(pid)
            except HarnessAPIError as exc:
                if exc.status_code not in (401, 403):
                    raise
                grants = None
            bundle = agent_bundle.from_platform(
                process, by_id, assigned_workstream_ids=assigned, write_grants=grants
            )
            target = bws.agent_dir(ws, item.name)
            agent_bundle.write_bundle(bundle, target)
        except agent_bundle.BundleError as exc:
            skipped.append({"agent": item.name, "reason": str(exc)})
            continue
        for f in sorted(target.iterdir()):
            paths.append(f"harness/agents/{item.name}/{f.name}")
        names.append(item.name)
    return paths, names, skipped


def _branch_name(ws: Path) -> str:
    base = f"drift/{datetime.now(UTC).strftime('%Y-%m-%d')}"
    name, n = base, 2
    while (
        repo.run_git(
            ws, "rev-parse", "--verify", "-q", f"refs/heads/{name}", check=False
        ).returncode
        == 0
    ):
        name, n = f"{base}-{n}", n + 1
    return name


def _render_plan(plan: drift.PullPlan) -> list[str]:
    lines = []
    for it in plan.items:
        tag = " (partial files)" if it.partial else ""
        lines.append(f"  import {it.kind} {it.name}{tag}")
        lines += [f"    partial: {f.path} ({f.reason})" for f in it.files if f.status == "partial"]
    if plan.already_in_repo:
        lines.append(f"  already in the repo: {', '.join(plan.already_in_repo)}")
    lines += [f"  skipped {s['name']}: {s['reason']}" for s in plan.skipped]
    return lines


def _pull(ctx: Context) -> Result:
    a = ctx.args
    ws = _workspace(ctx)
    check = bool(a.check or ctx.dry_run)
    client = _platform(ctx)
    plan = drift.pull_plan(client, bws.harness_dir(ws), with_content=not check)
    data: dict[str, Any] = {"check": check, **plan.to_dict()}
    if check:
        lines = ["Would import:" if plan.items else "Nothing to import."]
        lines += _render_plan(plan)
        if a.adopt:
            data["adopt"] = {"would_record": True}
            lines.append("--adopt would also record what is live as version 0 (bld_versions).")
        if ctx.dry_run and not a.check:
            lines.insert(0, "DRY RUN: nothing written.")
        return Result.done(data, "\n".join(lines))

    _require_repo(ws)
    skills = [i for i in plan.items if i.kind == "skill"]
    procs = [i for i in plan.items if i.kind == "process"]
    original = repo.current_branch(ws)
    branch: str | None = None
    commit_sha: str | None = None
    written: list[str] = []
    partial: list[dict[str, str]] = []
    agent_names: list[str] = []
    agents_skipped: list[dict[str, str]] = []
    if plan.items:
        branch = _branch_name(ws)
        repo.create_branch(ws, branch)
        try:
            for item in skills:
                paths, part = _write_skill(ws, item)
                written += paths
                partial += part
            ag_paths, agent_names, agents_skipped = _write_agents(ctx, ws, client, procs)
            written += ag_paths
            if written:
                lines_msg = [
                    f"Import from platform: {len(skills)} skill(s), {len(agent_names)} agent(s)",
                    "",
                ]
                lines_msg += [f"partial: {p['skill']}/{p['path']} ({p['reason']})" for p in partial]
                commit_sha = repo.commit(
                    ws, "\n".join(lines_msg).rstrip(), paths=written, identity=_identity(ws)
                )
        finally:
            if original:
                repo.checkout(ws, original)
    data.update(
        {
            "branch": branch if commit_sha else None,
            "commit": commit_sha,
            "written": written,
            "partial": partial,
            "agents_imported": agent_names,
            "agents_skipped": agents_skipped,
        }
    )
    lines = []
    if commit_sha:
        lines.append(
            f"Imported {len(skills)} skill(s) and {len(agent_names)} agent(s) on branch {branch} "
            f"({commit_sha[:10]}); review and merge it by pull request."
        )
    else:
        lines.append("Nothing imported.")
    lines += [
        f"Partial (not whole, import by hand): {p['skill']}/{p['path']} ({p['reason']})"
        for p in partial
    ]
    lines += [f"Skipped agent {s['agent']}: {s['reason']}" for s in agents_skipped]

    if a.adopt:
        ledger = VersionLedger(OrgDBClient.from_client(client))
        records = drift.adopt_baseline(
            client,
            author=_author(ws),
            git_sha=commit_sha or (repo.current_sha(ws) if _has_commit(ws) else None),
            fetch_aux=True,
        )
        recorded: list[str] = []
        existing: list[str] = []
        for rec in records:
            if ledger.get(rec.version_id) is not None:
                existing.append(rec.version_id)
            else:
                ledger.record_live(rec)
                recorded.append(rec.version_id)
        tags = [r.version_id for r in records if r.object_kind == "agent"]
        data["adopt"] = {"recorded": recorded, "already_recorded": existing, "tags": tags}
        lines.append(
            f"Recorded {len(recorded)} version-0 baseline row(s); {len(existing)} already there."
        )
        if tags:
            lines.append(
                "After the branch is merged, tag the merge commit: "
                + "; ".join(f"git tag -a {t} <merge-sha> -m {t}" for t in tags)
            )
    return Result.done(data, "\n".join(lines))


# ---------------------------------------------------------------------------
# ws push-bundles / pull-bundles
# ---------------------------------------------------------------------------


def _bundle_files(ws: Path) -> list[str]:
    out: list[str] = []
    bdir = bws.harness_dir(ws) / "bundles"
    if bdir.is_dir():
        out += [p.relative_to(ws).as_posix() for p in sorted(bdir.rglob("*")) if p.is_file()]
    skills = bws.harness_dir(ws) / "skills"
    if skills.is_dir():
        out += [
            p.relative_to(ws).as_posix() for p in sorted(skills.glob("*/recording_bundle.json"))
        ]
    return out


def _doc_path(rel: str, sha: str) -> str:
    """A content-addressed Doc Store path: a changed bundle gets a new path, none is overwritten."""
    parent, name = rel.rsplit("/", 1)
    return f"{parent}/{sha[:12]}/{name}"


def _docs(ctx: Context, ws: Path) -> DocStore:
    if not bws.load_builder_config(ws)["stores"].get(BUNDLES_STORE):
        raise UsageError(f"builder.json has no '{BUNDLES_STORE}' store; run `abcd builder setup`")
    return DocStore(_platform(ctx), bws.load_builder_config(ws)["stores"])


def _push_bundles(ctx: Context) -> Result:
    ws = _workspace(ctx)
    lock = bws.load_bundles_lock(ws)["entries"]
    todo: list[dict[str, Any]] = []
    for rel in _bundle_files(ws):
        blob = (ws / rel).read_bytes()
        sha = hashlib.sha256(blob).hexdigest()
        if lock.get(rel, {}).get("sha256") == sha:
            continue
        todo.append(
            {"path": rel, "sha256": sha, "size": len(blob), "doc_store_path": _doc_path(rel, sha)}
        )
    data: dict[str, Any] = {"dry_run": ctx.dry_run, "uploaded": todo, "unchanged": len(lock)}
    if not todo:
        return Result.done(data, "All capture bundles are already uploaded.")
    if ctx.dry_run:
        lines = [f"DRY RUN: would upload {len(todo)} bundle(s) to {BUNDLES_STORE}:"]
        lines += [f"  {t['path']} -> {t['doc_store_path']}" for t in todo]
        return Result.done(data, "\n".join(lines))
    docs = _docs(ctx, ws)
    docs.put_many(
        BUNDLES_STORE,
        [
            Upload(
                t["doc_store_path"],
                (ws / t["path"]).read_bytes(),
                "application/json" if t["path"].endswith(".json") else "application/octet-stream",
            )
            for t in todo
        ],
    )
    for t in todo:
        bws.record_bundle(ws, t["path"], t["doc_store_path"])
    return Result.done(
        data,
        f"Uploaded {len(todo)} bundle(s) to {BUNDLES_STORE}; recorded in harness/{bws.BUNDLES_LOCK} "
        "(commit it; the bundles themselves stay out of git).",
    )


def _pull_bundles(ctx: Context) -> Result:
    ws = _workspace(ctx)
    entries = bws.load_bundles_lock(ws)["entries"]
    todo: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for rel, entry in sorted(entries.items()):
        if _safe_rel(rel) is None or not rel.startswith("harness/"):
            skipped.append({"path": rel, "reason": "unsafe path in the lock"})
            continue
        local = ws / rel
        if local.is_file():
            have = hashlib.sha256(local.read_bytes()).hexdigest()
            if have == entry["sha256"]:
                continue
            if not ctx.yes:
                skipped.append(
                    {"path": rel, "reason": "local file differs (use --yes to overwrite)"}
                )
                continue
        todo.append({"path": rel, **entry})
    data: dict[str, Any] = {"dry_run": ctx.dry_run, "restore": todo, "skipped": skipped}
    if ctx.dry_run or not todo:
        text = (
            f"DRY RUN: would restore {len(todo)} bundle(s)."
            if ctx.dry_run
            else "Nothing to restore."
        )
        return Result.done(data, text)
    docs = _docs(ctx, ws)
    restored: list[str] = []
    for t in todo:
        try:
            blob = docs.get(f"{BUNDLES_STORE}/{t['doc_store_path']}")
        except NotFoundError as exc:
            raise RefusedError(f"{t['doc_store_path']} is not in {BUNDLES_STORE}") from exc
        if hashlib.sha256(blob).hexdigest() != t["sha256"]:
            raise RefusedError(f"{t['path']}: downloaded content does not match bundles.lock.json")
        target = ws / t["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        restored.append(t["path"])
    data["restored"] = restored
    lines = [f"Restored {len(restored)} bundle(s)."]
    lines += [f"Skipped {s['path']}: {s['reason']}" for s in skipped]
    return Result.done(data, "\n".join(lines))


# ---------------------------------------------------------------------------
# ws hook
# ---------------------------------------------------------------------------


def _hook(ctx: Context) -> Result:
    if ctx.args.action != "install":
        raise UsageError(f"unknown hook action {ctx.args.action!r}; use `install`")
    ws = _workspace(ctx)
    _require_repo(ws)
    if ctx.dry_run:
        return Result.done({"dry_run": True}, f"DRY RUN: would install the pre-commit hook in {ws}")
    hook = precommit.install_hook(ws, force=bool(ctx.args.force))
    return Result.done({"hook": str(hook)}, f"Installed the pre-commit hook: {hook}")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(registry: Registry) -> None:
    group = registry.group("ws", "Workspaces (environments)")

    p = group.command(
        "init",
        _init,
        help="Make the workspace a Builder repo: layout, hook, first commit, push",
        writes=True,
    )
    p.add_argument("--remote", help="The client repo URL (no credentials in it)")
    p.add_argument("--default-branch", help="Default branch name (default: main)")

    p = group.command(
        "clone", _clone, help="Clone a client's repo into workspaces/<env>", writes=True
    )
    p.add_argument("url")

    p = group.command("status", _status, help="Branch, ahead/behind, dirty files, optional drift")
    p.add_argument(
        "--platform", action="store_true", help="Also summarise drift against the platform"
    )

    p = group.command(
        "pull",
        _pull,
        help="Import skills and agents the platform has and the repo does not (drift/<date> branch)",
        writes=True,
    )
    p.add_argument(
        "--check", action="store_true", help="Report what would be imported; write nothing"
    )
    p.add_argument("--adopt", action="store_true", help="Also record what is live as version 0")

    group.command(
        "push-bundles",
        _push_bundles,
        help="Upload capture bundles to the noui-bundles Doc Store and record bundles.lock.json",
        writes=True,
    )
    group.command(
        "pull-bundles",
        _pull_bundles,
        help="Restore capture bundles listed in bundles.lock.json (--yes overwrites local files)",
        writes=True,
    )

    p = group.command("hook", _hook, help="Workspace git hook: `hook install`", writes=True)
    p.add_argument("action", choices=["install"])
    p.add_argument("--force", action="store_true", help="Replace a hook abcd did not write")
