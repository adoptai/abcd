"""``abcd cloud setup --home``: prepare a client's Builder home org for cloud runs (PLAN.md section 6).

Run with ``--env`` set to a workspace whose ``.env`` PAT belongs to the client's Builder HOME org.
It makes, idempotently:

1. the ``abcd-<env>`` workstream and its Doc Store;
2. ``abcd-workspace.json`` (``{env, repo, ref}``) in that store, replaced on purpose: it is a pointer;
3. the ``abcd`` harness skill (with its ``abcd-bundle.zip``) and the ``abcd-release-gate`` skill the
   runner uses. abcd's CI publishes both system-wide on releases (the default "Built-in" tier):
   when a skill is published there, no org copy is pushed and the process components use
   ``source: default``. Only when it is absent is it pushed at the ORG tier, as before.
   ``--org-copy`` (needs ``--yes``) forces the org push to test a branch; an org copy HIDES the
   default for this org, so this command only warns about one and prints the delete command;
4. the ``abcd-runner``, ``abcd-nightly`` and ``abcd-weekly`` processes, from the meta templates
   (new ones come up enabled; an existing process keeps its enabled state, so a re-run never
   re-enables what you disabled).

It never creates the vault secrets or the cron pipelines: it prints them as manual steps (a pipeline
created with a PAT records the token id as its owner, so pipelines are created from a browser).
It refuses a workspace whose builder.json says ``role: client``.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from cli import builder_agent as ba
from cli import builder_cloud
from cli.builder_common import agent_bundle as ab
from cli.builder_common import cloud, repo
from cli.builder_common import meta_templates as mt
from cli.builder_common import workspace as bws
from cli.builder_common.docstore import DocStore, NotFoundError
from cli.builder_common.errors import PlatformError, RefusedError, UsageError
from cli.builder_common.platform import BuilderPlatformClient
from cli.builder_common.versions import detail_digest, skill_md_digest
from cli.builder_setup import ensure_store, ensure_workstream
from cli.core.output import Result
from cli.harness_common.api_client import HarnessAPIError
from cli.harness_skill import _lint_frontmatter

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.registry import Registry

ABCD_SKILL = "abcd"
BUNDLE_NAME = "abcd-bundle.zip"
SECRETS = (
    ("SANDBOX_ABCD_PAT_CLIENT_ID", "client id of the Admin PAT for the client org"),
    ("SANDBOX_ABCD_PAT_SECRET", "secret of that PAT"),
    ("SANDBOX_GITHUB_TOKEN", "fine-grained token limited to the client's workspace repo"),
    (
        "SANDBOX_GITHUB_DEPLOY_KEY",
        "alternative to the token: write deploy key (private key) for the workspace repo; "
        "git only, PRs and releases are flagged",
    ),
    ("SANDBOX_ABCD_WEBUI_ENDPOINT", "optional: overrides ADOPT_APP_BASE_URL"),
)
DEFAULT_TIER_NOTE = "decided at run time: default tier when published there, else the org tier"
PIPELINES = (
    ("abcd-nightly", "0 2 * * *", "every night"),
    ("abcd-weekly", "0 16 * * 5", "Friday"),
)

#: Tests replace this: ``build(out_path)`` writes the bundle zip there.
BUNDLE_BUILDER: Callable[[Path], None] | None = None


def build_bundle(out: Path) -> None:
    if BUNDLE_BUILDER is not None:
        BUNDLE_BUILDER(out)
        return
    builder = builder_cloud._load_bundle_builder()
    builder.build(builder_cloud.REPO_ROOT, out)


def home_workstream(env: str) -> str:
    return f"abcd-{env}"


def refuse_client_workspace(cfg: dict[str, Any], label: str) -> None:
    """A workspace set up as a client org (``role: client``, or ``builder-context`` recorded by
    ``builder setup`` before the role existed) must not receive home org objects."""
    if cfg.get("role") == "client" or (
        cfg.get("role") is None and "builder-context" in cfg.get("workstreams", {})
    ):
        raise RefusedError(
            f"{label} is a client org workspace (builder.json role: client). The Builder home org "
            "holds the PAT and the abcd-* processes, not the client org: sandbox secrets are "
            "org-wide, so an Admin PAT in a client org would reach the client's own agents. "
            "Run this with the home org's workspace"
        )


def pointer(env: str, repo_slug: str, ref: str) -> dict[str, str]:
    return {"env": env, "repo": repo_slug, "ref": ref}


def pointer_text(doc: dict[str, str]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def resolve_repo_ref(ctx: Context, workspace_env: str, own: dict[str, Any]) -> tuple[str, str]:
    a = ctx.args
    source = own
    path = ctx.workspaces_dir / workspace_env / bws.BUILDER_JSON
    if workspace_env != ctx.env and path.is_file():
        source = bws.load_builder_config(path.parent)
    slug = a.repo
    if not slug and source.get("repo_remote"):
        _host, owner, name = repo.parse_remote(str(source["repo_remote"]))
        slug = f"{owner}/{name}"
    if not slug:
        raise UsageError(
            f"no repo for workspace {workspace_env!r}: pass --repo owner/name, or set repo_remote in "
            "its builder.json"
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", slug):
        raise UsageError(f"--repo {slug!r} must look like owner/name")
    return slug, a.ref or str(source.get("default_branch") or "main")


def render_process_bundles(env: str, client: str, out: Path) -> dict[str, ab.AgentBundle]:
    """The three home processes, rendered from the templates and parsed like any bundle."""
    values = {"client": client, "env": env}
    bundles: dict[str, ab.AgentBundle] = {}
    for name in mt.HOME_AGENTS:
        d = out / name
        d.mkdir(parents=True)
        for rel, text in mt.agent_files(name, values).items():
            (d / rel).write_text(text, encoding="utf-8")
        bundles[name] = ab.load_bundle(d)
    return bundles


def build_plan(env: str, slug: str, ref: str, org_copy: bool = False) -> dict[str, Any]:
    """Everything the run would create or reuse. Pure: no call, no write."""
    tier = "org" if org_copy else "auto"
    note = {} if org_copy else {"tier_note": DEFAULT_TIER_NOTE}
    ws_name = home_workstream(env)
    processes = []
    for name in mt.HOME_AGENTS:
        doc = yaml.safe_load(mt.agent_files(name, {"client": "", "env": env})["process.yaml"])
        processes.append(
            {
                "name": name,
                "workstreams": [ws_name],
                "components": [str(c["ref"]) for c in doc.get("components") or []],
            }
        )
    return {
        "role": "home",
        "workstream": ws_name,
        "doc_store": {"workstream": ws_name, "reuses_default_store": True},
        "pointer": {
            "path": f"{ws_name}/{cloud.WORKSPACE_FILE_NAME}",
            "content": pointer(env, slug, ref),
            "replace": True,
        },
        "skills": [
            {
                "name": ABCD_SKILL,
                "tier": tier,
                "aux": [BUNDLE_NAME],
                "builds_bundle": True,
                **note,
            },
            {"name": "abcd-release-gate", "tier": tier, "aux": [], **note},
        ],
        "processes": processes,
        "manual_steps": manual_steps(env, slug),
    }


def manual_steps(env: str, slug: str) -> dict[str, Any]:
    ws_name = home_workstream(env)
    return {
        "vault_secrets": [
            {
                "name": n,
                "group": ws_name,
                "holds": what,
                "value": "write-only; enter in the browser",
            }
            for n, what in SECRETS
        ],
        "pipelines": [
            {
                "process": p,
                "workstream": ws_name,
                "schedule_type": "cron",
                "cron_expr": cron,
                "when": when,
                "state": "running",
            }
            for p, cron, when in PIPELINES
        ],
        "notes": [
            "Create the two cron pipelines from a browser session: a pipeline created with a PAT "
            "records the token id as its owner. `abcd pipeline create` exists for tests only.",
            f"The GitHub token (or the deploy key, which replaces it) must reach only {slug}.",
        ],
    }


def render_manual_steps(steps: dict[str, Any]) -> str:
    lines = [
        "Remaining manual steps (this command does none of them):",
        "  1. Vault secrets in this home org (values are write-only; the sandbox sees each name "
        "without the SANDBOX_ prefix):",
    ]
    lines += [
        f"       {s['name']}  ({s['holds']}; group {s['group']})" for s in steps["vault_secrets"]
    ]
    lines.append("  2. Two cron pipelines, created from a browser session:")
    lines += [
        f"       {p['process']} on workstream {p['workstream']}: cron {p['cron_expr']} "
        f"({p['when']}), state running"
        for p in steps["pipelines"]
    ]
    lines += [f"     Note: {n}" for n in steps["notes"]]
    return "\n".join(lines)


def render_plan(plan: dict[str, Any]) -> str:
    lines = [
        "Would create or reuse (home org):",
        f"  Workstream {plan['workstream']} and its Doc Store",
        f"  {plan['pointer']['path']} (replace allowed): {json.dumps(plan['pointer']['content'])}",
        "  Skills (a skill published at the default tier is used as is, with no org copy):",
    ]
    lines += [
        f"    {s['name']}"
        + (f" with {', '.join(s['aux'])}" if s["aux"] else "")
        + (f"  [{s['tier_note']}]" if s.get("tier_note") else "  [org tier, --org-copy]")
        for s in plan["skills"]
    ]
    lines.append("  Processes:")
    lines += [
        f"    {p['name']}  components {', '.join(p['components'])}  workstream {p['workstreams'][0]}"
        for p in plan["processes"]
    ]
    lines.append("  builder.json: role home, workstream and store ids")
    return "\n".join(lines) + "\n" + render_manual_steps(plan["manual_steps"])


# -- platform steps ------------------------------------------------------------------------


DIGEST_FILE = "bundle.sha256"


def digest_aux(bundle: Path) -> dict[str, str]:
    """The small text aux file holding the bundle's sha256: the platform hands text back, so
    it tells whether a same-size bundle changed (the zip itself is binary and is not returned)."""
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    return {"path": DIGEST_FILE, "content_b64": base64.b64encode(f"{digest}\n".encode()).decode()}


def _digest_matches(client: Any, name: str, aux: list[dict[str, str]]) -> bool:
    """When ``aux`` carries ``bundle.sha256``, the live skill's copy of that text must equal it.
    A missing file counts as changed. Without the file there is nothing more to compare."""
    mine = next((a for a in aux if a["path"] == DIGEST_FILE), None)
    if mine is None:
        return True
    try:
        got = client.read_skill_file(name, DIGEST_FILE)
    except HarnessAPIError:
        return False
    want = base64.b64decode(mine["content_b64"]).decode().strip()
    return got.state == "text" and (got.content or "").strip() == want


def push_org_skill(
    client: Any, name: str, md: str, aux: list[dict[str, str]], shadow_default: bool = False
) -> dict[str, Any]:
    """Upload ``name`` at the org tier. Unchanged content is not re-sent. A name that is only a
    default skill is refused by the platform on the first upload (no ``replace``), unless
    ``shadow_default`` (``--org-copy``) says to create the org copy over it on purpose."""
    try:
        _lint_frontmatter(md, Path(f"{name}/SKILL.md"))
    except ValueError as exc:
        raise UsageError(str(exc)) from exc
    findings = ba.secret_findings(md, aux, f"{name}/")
    if findings:
        raise RefusedError(
            f"{name}: secret scan found {len(findings)} credential(s); not uploading"
        )
    exists = name in ba.org_skill_names(client)
    state = "created"
    if exists:
        live = client.get_skill(name)
        live_aux = sorted(
            (str(a["path"]), int(a.get("size") or 0)) for a in live.get("aux_files") or []
        )
        mine = sorted((a["path"], len(base64.b64decode(a["content_b64"]))) for a in aux)
        if (
            detail_digest(live) == skill_md_digest(md)
            and live_aux == mine
            and _digest_matches(client, name, aux)
        ):
            return {"name": name, "state": "unchanged", "tier": "org"}
        state = "updated"
    hashed = ba.hash_payload(md, aux)
    b64 = base64.b64encode(md.encode()).decode("ascii")
    try:
        if exists or shadow_default:
            client.upload_skill(name, b64, aux, replace=True)
        else:
            client.upload_skill_first(name, b64, aux)
    except HarnessAPIError as exc:
        if exc.status_code == 409:
            raise RefusedError(
                f"{name}: the platform refused the upload (name taken): {exc.body}"
            ) from exc
        raise PlatformError(f"upload of skill {name} failed: {exc}") from exc
    return {"name": name, "state": state, "tier": "org", "bundle_sha256": hashed["bundle_sha256"]}


def default_tier_status(client: Any, name: str) -> dict[str, Any] | None:
    """The published default-tier copy of ``name``: ``{"bundle_version": ...}``, or None.

    The merged catalog lists a name once and an org copy wins, so a default hidden behind an org
    copy is only seen through the version pointer, which the platform serves to Adopt operators
    (any other PAT gets a 403, read as "cannot tell"). A readable pointer that says
    ``published: false`` is not usable."""
    in_catalog = name in client.default_skill_names()
    pointer = client.default_skill_version(name)
    if pointer is not None and not pointer.get("published"):
        return None
    if pointer is None and not in_catalog:
        return None
    version = (pointer or {}).get("bundle_version")
    return {"bundle_version": str(version) if version else "unknown"}


def delete_hint(name: str, env: str) -> str:
    return f"abcd skill delete {name} --yes --env {env}"


def org_copy_warning(name: str, env: str) -> str:
    return (
        f"{name} exists at the org tier and also at the default tier: the org copy hides the "
        f"default for this org (the default's updates never reach it). Nothing was deleted; to "
        f"remove the org copy run `{delete_hint(name, env)}`"
    )


def _use_default_components(bundle: ab.AgentBundle, names: set[str]) -> ab.AgentBundle:
    comps = tuple(
        (t, "default" if t == "skill" and r in names else s, r) for t, s, r in bundle.components
    )
    return dataclasses.replace(bundle, components=comps)


def _setup_home(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    bws.assert_not_default(ctx.env)
    if not ws.is_dir():
        raise UsageError(f"Workspace not found: {ws}")
    cfg = bws.load_builder_config(ws)  # needs `abcd ws init` first
    refuse_client_workspace(cfg, ws.name)
    env = mt.check_env(a.workspace_env or ctx.env)
    slug, ref = resolve_repo_ref(ctx, env, cfg)
    org_copy = bool(getattr(a, "org_copy", False))
    plan = build_plan(env, slug, ref, org_copy)
    if ctx.dry_run:
        return Result.done(
            {"dry_run": True, "plan": plan},
            "DRY RUN: nothing was called or written.\n" + render_plan(plan),
        )

    if org_copy and not ctx.yes:
        raise RefusedError(
            "--org-copy pushes an org-tier copy of the abcd skills, which HIDES the default-tier "
            "(system-wide) copy for this org. It is for testing a branch only. Pass --yes to confirm"
        )

    client = BuilderPlatformClient.wrap(ctx.harness_client())
    ws_name = home_workstream(env)
    wid, ws_created = ensure_workstream(client, ws_name, "abcd cloud runs for one client workspace")
    store = ensure_store(client, ws_name, wid)

    docs = DocStore(client, {ws_name: store["id"]})
    path = f"{ws_name}/{cloud.WORKSPACE_FILE_NAME}"
    text = pointer_text(pointer(env, slug, ref))
    try:
        same = docs.get(path).decode("utf-8") == text
    except NotFoundError:
        same = False
    if not same:
        docs.put_text(path, text, replace=True)  # a pointer file, rewritten on purpose
    pointer_state = "unchanged" if same else "written"

    skills: list[dict[str, Any]] = []
    warnings: list[str] = []
    skill_md_path = builder_cloud.REPO_ROOT / "harness-skills" / "abcd" / "SKILL.md"
    template_skills = sorted({s for n in mt.HOME_AGENTS for s in mt.AGENTS[n].skills})
    wanted = [ABCD_SKILL, *template_skills]
    org_names = ba.org_skill_names(client)
    default_names: set[str] = set()
    for name in wanted:
        status = default_tier_status(client, name)
        if org_copy:
            note = f"--org-copy: {name} is pushed at the org tier, which hides the default for this org"
            if status is not None:
                note += f" (default bundle_version {status['bundle_version']})"
            warnings.append(note)
        elif status is not None:
            default_names.add(name)
            skills.append(
                {
                    "name": name,
                    "state": "using default tier",
                    "tier": "default",
                    "bundle_version": status["bundle_version"],
                }
            )
            if name in org_names:
                warnings.append(org_copy_warning(name, ctx.env))
    to_push = [n for n in wanted if n not in default_names]
    if to_push:
        with tempfile.TemporaryDirectory(prefix="abcd-home-") as tmp:
            tmpdir = Path(tmp)
            for name in to_push:
                if name == ABCD_SKILL:
                    if not skill_md_path.is_file():
                        raise UsageError(
                            f"{skill_md_path} not found: `cloud setup --home` needs a source checkout"
                        )
                    zip_path = tmpdir / BUNDLE_NAME
                    build_bundle(zip_path)
                    aux = [
                        {
                            "path": BUNDLE_NAME,
                            "content_b64": base64.b64encode(zip_path.read_bytes()).decode("ascii"),
                        },
                        digest_aux(zip_path),
                    ]
                    skills.append(
                        push_org_skill(client, name, skill_md_path.read_text(), aux, org_copy)
                    )
                else:
                    files = mt.skill_files(name, {})
                    extra = [
                        {"path": r, "content_b64": base64.b64encode(t.encode()).decode("ascii")}
                        for r, t in files.items()
                        if r != "SKILL.md"
                    ]
                    skills.append(push_org_skill(client, name, files["SKILL.md"], extra, org_copy))
    with tempfile.TemporaryDirectory(prefix="abcd-home-") as tmp:
        rendered = render_process_bundles(env, "", Path(tmp) / "agents")
    bundles = {n: _use_default_components(b, default_names) for n, b in rendered.items()}

    processes = []
    for name, bundle in bundles.items():
        # A process that already exists keeps its enabled state: an operator disables the nightly
        # and weekly runners until their cron pipelines exist, and a re-run must not wake them.
        live = client.find_process_by_name(name)
        if live is not None:
            bundle = dataclasses.replace(bundle, is_enabled=bool(live.get("is_enabled")))
        sync = ba.sync_process(client, bundle, {ws_name: wid})
        bad = ba.verify_process(client, bundle, {ws_name: wid}, sync.process_id)
        if bad:
            raise PlatformError(f"{name}: platform differs from the template in {bad}")
        processes.append({"name": name, **sync.to_dict()})

    bws.update_builder_config(
        ws, workstreams={ws_name: wid}, stores={ws_name: store["id"]}, role="home"
    )
    steps = plan["manual_steps"]
    data = {
        "dry_run": False,
        "role": "home",
        "workstream": {"name": ws_name, "id": wid, "created": ws_created},
        "doc_store": {"id": store["id"], "name": store["name"], "created": store["created"]},
        "pointer": {"path": path, "state": pointer_state, **pointer(env, slug, ref)},
        "skills": skills,
        "warnings": warnings,
        "processes": processes,
        "manual_steps": steps,
    }
    created = [p["name"] for p in processes if p["created"]]
    skill_text = ", ".join(
        f"{s['name']} using default tier (bundle_version {s['bundle_version']})"
        if s.get("tier") == "default"
        else f"{s['name']} {s['state']}"
        for s in skills
    )
    head = (
        f"Home org set up for workspace {env}: workstream {ws_name} "
        f"({'created' if ws_created else 'reused'}), pointer {pointer_state}, "
        f"skills {skill_text}, "
        f"processes created: {', '.join(created) or 'none'} (the rest already matched or were patched)."
        "\nbuilder.json updated (role home); commit it."
    )
    warn = "".join(f"\nWARNING: {w}" for w in warnings)
    return Result.done(data, head + warn + "\n" + render_manual_steps(steps))


def register(registry: Registry) -> None:
    g = registry.group("cloud", "Cloud runner: bootstrap, bundle and the nightly run")
    p = g.command(
        "setup",
        _setup_home,
        help="Set up the Builder home org for cloud runs (workstream, pointer, abcd skill, processes)",
        writes=True,
    )
    p.add_argument(
        "--home",
        action="store_true",
        required=True,
        help="Set up the Builder HOME org (--env's PAT must belong to it); refused on a client org",
    )
    p.add_argument(
        "--workspace-env",
        help="The client workspace the cloud runs work on (default: --env). Names the abcd-<workspace-env> "
        "workstream and the pointer; its builder.json is read only for the repo and ref you leave out",
    )
    p.add_argument(
        "--org-copy",
        action="store_true",
        help="TESTING ONLY: push the abcd skills at the org tier even when the default tier has "
        "them. An org copy hides the system-wide default for this org. Needs --yes",
    )
    p.add_argument("--repo", help="owner/name of the workspace repo (default: its repo_remote)")
    p.add_argument("--ref", help="Pinned ref (default: the workspace's default branch)")
