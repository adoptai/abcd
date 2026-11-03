"""``abcd meta list|install|maker-checker``: the meta agents as files in the workspace repo.

* ``meta list``: the shipped meta agent templates and where each one runs.
* ``meta install <name>... [--client C]``: copy the templates into ``harness/agents/<name>/`` (and
  ``harness/skills/<skill>/``), fill in the client and workstream names, and commit on a branch,
  never the default branch. Nothing is pushed to the platform; ``abcd agent push`` and
  ``abcd release`` do that.
* ``meta maker-checker <agent>``: generate ``harness/skills/<agent>-maker-checker/`` from the
  agent's ``harness/checks/*.yaml`` and ``release.yaml``, and add it to the agent's ``process.yaml``.

PLAN.md sections 6, 12 and 13.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cli import builder_eval as be
from cli.builder_common import agent_bundle as ab
from cli.builder_common import meta_templates as mt
from cli.builder_common import repo
from cli.builder_common import workspace as bws
from cli.builder_common.errors import RefusedError, UsageError
from cli.builder_common.evals.scorers import load_checks
from cli.builder_setup import client_name, slugify
from cli.core.output import Result
from cli.harness_skill import _lint_frontmatter

if TYPE_CHECKING:
    from cli.core.context import Context
    from cli.core.registry import Registry

_SEVERITY_LABEL = {"error": "blocking", "warning": "warning"}


# -- values and files ----------------------------------------------------------------------


def detect_client(ws: Path, cfg: dict[str, Any], override: str | None = None) -> str:
    """``--client``, else the client in a recorded ``builder-status-*``/``builder-sme-*``
    workstream name, else the workspace's ``env.json`` client or folder name."""
    if override:
        return slugify(override)
    for name in cfg.get("workstreams", {}):
        for prefix in ("builder-status-", "builder-sme-"):
            if name.startswith(prefix) and len(name) > len(prefix):
                return name[len(prefix) :]
    return client_name(ws)


def _role_guard(role: str | None, where: str, label: str) -> None:
    if where == mt.CLIENT and role == "home":
        raise RefusedError(
            f"{label} runs in the client org, but this workspace is a Builder home org workspace "
            "(builder.json role: home); install it from the client's workspace"
        )
    if where == mt.HOME and role == "client":
        raise RefusedError(
            f"{label} runs in the Builder home org, but this workspace is a client org workspace "
            "(builder.json role: client); use `abcd cloud setup --home` with the home org's workspace"
        )


def install_plan(
    names: list[str], values: dict[str, str]
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """``({workspace path: text}, [per-agent summary])`` for the agents and their skills."""
    files: dict[str, str] = {}
    summary: list[dict[str, Any]] = []
    for name in names:
        agent = mt.AGENTS[name]
        for rel, text in mt.agent_files(name, values).items():
            files[f"harness/agents/{name}/{rel}"] = text
        for skill in agent.skills:
            for rel, text in mt.skill_files(skill, values).items():
                files[f"harness/skills/{skill}/{rel}"] = text
        summary.append({"agent": name, "runs_in": agent.where, "skills": list(agent.skills)})
    return files, summary


def lint_rendered(files: dict[str, str], root: Path) -> None:
    """Parse every rendered skill and bundle the way the platform will; raises ``UsageError``."""
    for rel, text in files.items():
        if rel.endswith("/SKILL.md"):
            try:
                _lint_frontmatter(text, root / rel)
            except ValueError as exc:
                raise UsageError(str(exc)) from exc


def _write_files(ws: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _file_states(ws: Path, files: dict[str, str]) -> dict[str, str]:
    out = {}
    for rel, text in files.items():
        path = ws / rel
        if not path.exists():
            out[rel] = "new"
        else:
            out[rel] = "unchanged" if path.read_text(encoding="utf-8") == text else "differs"
    return out


def next_commands(names: list[str], env: str, branch: str | None) -> list[str]:
    cmds = []
    if branch:
        cmds.append(
            f"git push -u origin {branch}   # then open a pull request; do not merge it yourself"
        )
    client_side = [n for n in names if mt.AGENTS[n].where == mt.CLIENT]
    home_side = [n for n in names if mt.AGENTS[n].where == mt.HOME]
    for n in client_side:
        cmds.append(f"abcd agent push {n} --env {env} --yes   # in the client org's workspace")
    if home_side:
        cmds.append(
            f"abcd cloud setup --home --env {env} --dry-run   # then without --dry-run; "
            "pushes the abcd skill and creates these processes"
        )
    if "context-curator" in names:
        cmds.append(
            "Create the nightly cron pipeline for context-curator from a browser session "
            "(a pipeline created with a PAT records the token as its owner)"
        )
    if "context-agent" in names:
        cmds.append(
            "Link bld_context_gaps and bld_case_notes to the builder-sme workstream "
            "(`abcd builder setup` does it) and grant the SME write access"
        )
    return cmds


# -- commands ------------------------------------------------------------------------------


def _cmd_list(ctx: Context) -> Result:
    ws = ctx.env_dir
    have_ws = ws.is_dir()
    rows = []
    for name, agent in mt.AGENTS.items():
        installed = (bws.agent_dir(ws, name) / "process.yaml").is_file() if have_ws else None
        rows.append(
            {
                "name": name,
                "runs_in": agent.where,
                "skills": list(agent.skills),
                "summary": agent.summary,
                "installed": installed,
            }
        )
    lines = [
        f"{r['name']:<16} {r['runs_in']:<7} {r['summary']}"
        + (" [installed]" if r["installed"] else "")
        for r in rows
    ]
    lines.append(
        "maker-checker    client  generated per live agent: abcd meta maker-checker <agent>"
    )
    return Result.done({"agents": rows, "maker_checker": "per agent"}, "\n".join(lines))


def _cmd_install(ctx: Context) -> Result:
    a = ctx.args
    names = list(dict.fromkeys(a.names))
    unknown = [n for n in names if n not in mt.AGENTS]
    if unknown:
        raise UsageError(f"unknown meta agent(s) {unknown}; known: {', '.join(mt.AGENTS)}")
    ws = ctx.env_dir
    bws.assert_not_default(ctx.env)
    if not ws.is_dir():
        raise UsageError(f"Workspace not found: {ws}")
    cfg = bws.load_builder_config(ws)
    for n in names:
        _role_guard(cfg.get("role"), mt.AGENTS[n].where, n)
    values = {
        "client": detect_client(ws, cfg, a.client),
        "env": mt.check_env(a.workspace_env or ctx.env),
    }
    files, summary = install_plan(names, values)
    lint_rendered(files, ws)
    states = _file_states(ws, files)
    changed = sorted(r for r, s in states.items() if s == "differs")
    if changed and not a.replace:
        raise RefusedError(
            "already in the workspace and different from the template: "
            + ", ".join(changed)
            + "; pass --replace to overwrite them"
        )
    todo = sorted(r for r, s in states.items() if s != "unchanged")
    data: dict[str, Any] = {
        "client": values["client"],
        "env": values["env"],
        "agents": summary,
        "files": states,
        "branch": None,
        "commit": None,
        "pushed": False,
    }
    if ctx.dry_run:
        data["dry_run"] = True
        data["next"] = next_commands(names, ctx.env, None)
        return Result.done(
            data,
            "DRY RUN: nothing was written.\n"
            + "\n".join(f"  {s:<9} {r}" for r, s in states.items()),
        )
    if not todo:
        data["next"] = next_commands(names, ctx.env, None)
        return Result.done(data, "Already installed; nothing changed.")
    if not (ws / ".git").exists():
        raise RefusedError(f"{ws} is not a git repository; run `abcd ws init` first")
    _write_files(ws, {r: files[r] for r in todo})
    for n in names:
        ab.load_bundle(bws.agent_dir(ws, n))  # the written bundle must parse
    done = be.commit_work(
        ws, f"meta: install {', '.join(names)}", todo, topic="meta-install-" + "-".join(names)
    )
    branch = done.branch or repo.current_branch(ws)
    data.update(branch=branch, commit=done.sha, next=next_commands(names, ctx.env, branch))
    text = (
        f"Installed {', '.join(names)} for client {values['client']} on branch {branch}"
        + (f" ({done.sha[:10]})" if done.sha else "")
        + done.note
        + "\nNothing was pushed to the platform. Next:\n"
        + "\n".join(f"  {c}" for c in data["next"])
    )
    return Result.done(data, text)


def _cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).replace("|", "\\|").strip()


def _rule_text(check: dict[str, Any]) -> str:
    if check.get("kind") == "judged" or "rubric" in check:
        return f"Rubric: {check.get('rubric', '')}"
    rule = check.get("rule") or {}
    parts = [f"{rule.get('type', '?')} {rule.get('field', '')}".strip()]
    for k in ("value", "min", "max", "pattern"):
        if k in rule:
            parts.append(f"{k}={json.dumps(rule[k])}")
    return " ".join(parts)


def checks_table(checks: list[dict[str, Any]]) -> str:
    lines = ["| Check | Kind | Severity | Rule or rubric |", "|---|---|---|---|"]
    for c in checks:
        kind = "judged" if c.get("kind") == "judged" or "rubric" in c else "deterministic"
        sev = _SEVERITY_LABEL.get(str(c.get("severity", "error")), str(c.get("severity")))
        title = c.get("title") or ""
        label = f"`{c['check_id']}`" + (f" {title}" if title and title != c["check_id"] else "")
        lines.append(f"| {_cell(label)} | {kind} | {sev} | {_cell(_rule_text(c))} |")
    return "\n".join(lines)


def criteria_note(release: dict[str, Any]) -> str:
    crit = [c for c in release.get("criteria") or [] if isinstance(c, dict)]
    if not crit:
        return ""
    lines = [
        "The release gate also tracks these targets. They are context only; you do not apply them:",
        "",
    ]
    for c in crit:
        metric = c.get("metric") or c.get("metric_key") or c.get("criteria_key")
        target = c.get("target")
        lines.append(
            f"- {metric} {c.get('comparator', '')}" + (f" {target}" if target is not None else "")
        )
    return "\n".join(lines)


def maker_checker_files(
    agent: str, checks: list[dict[str, Any]], release: dict[str, Any]
) -> dict[str, str]:
    values = {
        "agent": agent,
        "checks_table": checks_table(checks),
        "criteria_note": criteria_note(release),
        "client": "",
        "env": "",
    }
    files = mt.skill_files(mt.MAKER_CHECKER, values)
    files["references/checks.json"] = (
        json.dumps(
            [
                {k: v for k, v in c.items() if k not in ("source_decision",)}
                for c in sorted(checks, key=lambda c: str(c["check_id"]))
            ],
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return files


def _cmd_maker_checker(ctx: Context) -> Result:
    a = ctx.args
    ws = ctx.env_dir
    bws.assert_not_default(ctx.env)
    d = be.agent_dir(ws, a.agent)
    checks = [c for c in load_checks(ws) if c.get("agent") == a.agent]
    if not checks:
        raise RefusedError(
            f"{a.agent} has no checks in harness/checks/; add some with `abcd check add` first"
        )
    release = be.read_yaml(d / "release.yaml", {})
    skill = f"{a.agent}-maker-checker"
    files = {
        f"harness/skills/{skill}/{rel}": text
        for rel, text in maker_checker_files(a.agent, checks, release).items()
    }
    lint_rendered(files, ws)
    bundle = ab.load_bundle(d)
    component = ("skill", "org", skill)
    add = component not in bundle.components
    states = _file_states(ws, files)
    data: dict[str, Any] = {
        "agent": a.agent,
        "skill": skill,
        "checks": [str(c["check_id"]) for c in checks],
        "files": states,
        "component_added": add,
    }
    next_cmds = [
        f"abcd skill audit harness/skills/{skill} --env {ctx.env} --json",
        f"abcd agent diff {a.agent} --env {ctx.env} --json",
        f"abcd stage {a.agent} --env {ctx.env}   # the maker-checker ships with the next release",
        "Link bld_check_results to the live agent's workstream: "
        "`abcd builder setup --agent-workstream <name>`",
    ]
    data["next"] = next_cmds
    if ctx.dry_run:
        data["dry_run"] = True
        return Result.done(data, f"DRY RUN: would write {len(files)} file(s) and add {skill}.")
    _write_files(ws, files)
    if add:
        ab.write_bundle(dataclasses.replace(bundle, components=(*bundle.components, component)), d)
    text = (
        f"Wrote harness/skills/{skill}/ from {len(checks)} check(s)"
        + (f" and added it to {a.agent}'s process.yaml" if add else " (already a component)")
        + ". Not committed. Next:\n"
        + "\n".join(f"  {c}" for c in next_cmds)
    )
    return Result.done(data, text)


def register(registry: Registry) -> None:
    g = registry.group("meta", "Meta agents: Status, Context, curator, maker-checker, abcd runners")
    g.command("list", _cmd_list, help="List the meta agent templates", writes=False)
    p = g.command(
        "install",
        _cmd_install,
        help="Copy meta agent templates into harness/agents/ and commit on a branch",
        writes=True,
    )
    p.add_argument("names", nargs="+", metavar="name", help="Meta agent name(s); see `meta list`")
    p.add_argument("--client", help="Client slug (default: from builder.json, else env.json)")
    p.add_argument(
        "--workspace-env",
        help="Home agents: the client workspace the cloud run works on (default: --env)",
    )
    p.add_argument("--replace", action="store_true", help="Overwrite files that differ")
    p = g.command(
        "maker-checker",
        _cmd_maker_checker,
        help="Generate the agent's maker-checker skill from its checks and add it to process.yaml",
        writes=True,
    )
    p.add_argument("agent")
