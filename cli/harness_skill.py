#!/usr/bin/env python3
"""
Push a local agent-harness skill (SKILL.md + aux files) to the platform's org
tier, and verify it actually parsed the way the local file intends.

Frontmatter mistakes (an unquoted colon, a malformed `process:` block, a bad
output_schema) degrade SILENTLY to a plain-text skill on the platform instead
of erroring on upload -- see adoptai-workflows/docs/agent-harness/
AUTHORING_SKILLS_AND_PLUGINS.md sections 1, 7, and 11 ("Gotchas"). This
script lints the frontmatter locally before spending an upload, and always
re-fetches the skill afterward to confirm what actually landed -- never trust
a 200.

`push` also runs the vendored adopt-skill-review audit (adopt-skill-review/) first --
a free, local, deterministic check for the step-budget/batching/file-placement
issues that actually cost turns on the harness. This is the cheap half of the
loop: debug and fix a skill locally (Claude Code seat, no harness LLM spend)
until `audit` is clean and the local behavior is what you want, THEN push and
run it on the harness to confirm it matches -- expect some real-harness
degradation even after a clean local pass (different system prompt, sandbox,
tool gating), but the harness run should now be confirming parity, not doing
your first-draft debugging for you.

Every upload path also runs a content secret scan (cli/harness_common/secret_scan.py)
over SKILL.md and every aux file -- zip members included -- BEFORE the PAT is
exchanged; a finding blocks the upload unless --allow-secret-findings is passed.

Usage:
    abcd skill audit <skill_dir>
    abcd skill push <skill_dir> [--name NAME] [--replace] [--force] [--skip-audit]
                                     [--aux ARCNAME=PATH ...] [--allow-secret-findings] [--env ENV]
    abcd skill verify <skill_name> [--env ENV]
    abcd skill delete <skill_name> --yes [--env ENV]
    abcd plugin push <plugin_dir> [--replace] [--force] [--skip-audit]
                                     [--allow-secret-findings] [--env ENV]
    abcd skill deploy-default <skill_dir> [--name NAME] [--bundle-version V]
                                     [--min-platform-contract N] [--aux ARCNAME=PATH ...]
                                     [--dry-run] [--env ENV]
    (the old `python cli/harness_skill.py audit|push|verify|delete|push-plugin|deploy-default`
    form still works; `push-plugin` is `abcd plugin push`)

`deploy-default` publishes to the platform-wide default ("Built-in") tier -- every
org sees it, and the endpoint is gated to @adopt.ai principals. It is how the NoUI
harness skill ships (see harness-skills/noui/ and .github/workflows/deploy-skill.yml).
"""

from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from cli.builder_common.errors import UsageError
from cli.core.registry import Registry, legacy_main
from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env
from cli.harness_common.secret_scan import Finding, scan_bytes

if TYPE_CHECKING:
    from cli.core.context import Context

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_AUDIT_SCRIPT = (
    Path(__file__).parent.parent
    / "adopt-skill-review"
    / "skills"
    / "adopt-skill-review"
    / "scripts"
    / "audit_skill.py"
)


def run_skill_audit(skill_dir: Path) -> tuple[int, str]:
    """
    Run the vendored adopt-skill-review audit against a local skill dir.

    Returns (exit_code, combined_output): 0 = clean, 1 = medium/high findings
    (advisory), 2 = critical findings. Returns (0, "") if the plugin isn't
    vendored in this checkout, so a missing adopt-skill-review/ never blocks
    push -- it's an enforcement aid, not a hard dependency of the CLI.
    """
    if not _AUDIT_SCRIPT.exists():
        return 0, ""
    result = subprocess.run(
        [sys.executable, str(_AUDIT_SCRIPT), str(skill_dir), "--format", "text"],
        capture_output=True,
        text=True,
    )
    return result.returncode, (result.stdout + result.stderr)


def _lint_frontmatter(skill_md_text: str, skill_md_path: Path) -> dict:
    """
    Parse SKILL.md frontmatter with the exact strictness the platform uses
    (yaml.safe_load). A parse failure means the platform will silently keep
    only name/description and drop everything else -- process block,
    keywords, featured, output_schema, capability flags.

    Raises:
        ValueError: With an actionable message, if the frontmatter won't
            parse or is missing a required field.
    """
    match = _FRONTMATTER_RE.match(skill_md_text)
    if not match:
        raise ValueError(
            f"{skill_md_path}: no YAML frontmatter block found "
            "(expected '---\\n...\\n---\\n' at the top of the file)"
        )

    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        raise ValueError(
            f"{skill_md_path}: frontmatter is not strict YAML -- the platform will silently "
            "drop everything except name/description (process block, keywords, output_schema, "
            "capability flags all vanish). Common cause: an unquoted ':' in a value, including "
            f"a nested step title inside a process block. Parse error: {e}"
        ) from e

    if not isinstance(meta, dict):
        raise ValueError(f"{skill_md_path}: frontmatter did not parse to a mapping")

    name = meta.get("name")
    if not name:
        raise ValueError(f"{skill_md_path}: frontmatter is missing required 'name'")
    if not _SLUG_RE.match(name):
        raise ValueError(
            f"{skill_md_path}: name '{name}' is not a valid slug (must match ^[a-z0-9][a-z0-9-]*$)"
        )
    if not meta.get("description"):
        print(
            f"⚠️  {skill_md_path}: no 'description' -- the catalog/fetch_skill tool description "
            "won't be able to tell the model when to use this skill."
        )

    if "process" in meta and not isinstance(meta["process"], dict):
        raise ValueError(
            f"{skill_md_path}: 'process' must be a mapping, got {type(meta['process']).__name__}"
        )

    return meta


# Directory names never staged as aux files, regardless of depth -- these are dev-only /
# generated / test scaffolding, never something the platform needs to run the skill. A real
# incident: dev_only/ held a live M365 Graph private key (dev_only/.env.graph) that got
# uploaded whole to an org's skill catalog because this collector walked the raw filesystem
# with no exclusions at all -- .gitignore only protects git, not this.
_EXCLUDED_DIR_NAMES = {
    "dev_only",
    "tests",
    "test",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
# File suffixes/substrings that are almost always secrets or build junk, even outside an
# excluded directory above.
_EXCLUDED_FILE_SUFFIXES = (".pem", ".key", ".pyc", ".pyo")
_EXCLUDED_FILENAME_SUBSTRINGS = (".env",)
# NoUI capture data: recording_bundle.json keeps the full login HAR and the app's cookies
# (PLAN section 3). It is read locally by the browser-skill gate and never uploaded.
_EXCLUDED_FILE_NAMES = {"recording_bundle.json"}


def _is_excluded(rel_parts: tuple[str, ...]) -> bool:
    if any(part in _EXCLUDED_DIR_NAMES or part.startswith(".") for part in rel_parts[:-1]):
        return True
    name = rel_parts[-1]
    if name == ".env.example":
        # A plain .env.example template is allowed through at skill root (the content scan
        # still checks it); every other dotfile and .env variant is not.
        return False
    if name.startswith("."):
        return True
    if name in _EXCLUDED_FILE_NAMES:
        return True
    if name.endswith(_EXCLUDED_FILE_SUFFIXES):
        return True
    return any(sub in name for sub in _EXCLUDED_FILENAME_SUBSTRINGS)


def _collect_aux_files(skill_dir: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Returns (aux_files, skipped_paths) -- skipped_paths is always reported, never silent."""
    aux_files = []
    skipped = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path.name == "SKILL.md":
            continue
        rel = path.relative_to(skill_dir)
        rel_posix = rel.as_posix()
        if _is_excluded(rel.parts):
            skipped.append(rel_posix)
            continue
        aux_files.append(
            {"path": rel_posix, "content_b64": base64.b64encode(path.read_bytes()).decode("ascii")}
        )
    return aux_files, skipped


def scan_upload(
    skill_md_text: str, aux_files: list[dict[str, str]], prefix: str = ""
) -> list[Finding]:
    """Content secret scan over exactly what would be uploaded."""
    findings = scan_bytes(f"{prefix}SKILL.md", skill_md_text.encode())
    for aux in aux_files:
        findings.extend(scan_bytes(f"{prefix}{aux['path']}", base64.b64decode(aux["content_b64"])))
    return findings


def _report_secret_findings(findings: list[Finding], allow: bool) -> bool:
    """Print findings; return True if the upload must stop."""
    if not findings:
        return False
    print(f"\n🔐 Secret scan found {len(findings)} likely credential(s) in the upload:")
    for f in findings:
        print(f"      - {f}")
    if allow:
        print("   ⚠️  --allow-secret-findings given -- uploading anyway.")
        return False
    print(
        "❌ Refusing to upload. Move the value into the harness secret store "
        "(${SECRET:name} placeholders) and remove it from the files, or pass "
        "--allow-secret-findings if every finding above is a false positive."
    )
    return True


def audit(skill_dir: Path) -> int:
    """Run the local, free adopt-skill-review audit and print its findings."""
    code, output = run_skill_audit(skill_dir)
    if not output:
        print(
            "⚠️  adopt-skill-review is not vendored in this checkout "
            "(expected at adopt-skill-review/skills/adopt-skill-review/scripts/audit_skill.py)"
        )
        return 0
    print(output)
    return code


def push(
    skill_dir: Path,
    name: str | None,
    replace: bool,
    force: bool,
    skip_audit: bool,
    env: str | None,
    allow_secret_findings: bool = False,
    aux_specs: list[str] | None = None,
) -> int:
    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.exists():
        print(f"❌ No SKILL.md in {skill_dir}")
        return 1

    skill_md_text = skill_md_path.read_text()
    try:
        meta = _lint_frontmatter(skill_md_text, skill_md_path)
    except ValueError as e:
        print(f"❌ Frontmatter lint failed: {e}")
        return 1

    skill_name = name or meta["name"]
    has_process = "process" in meta
    print(f"📦 Skill: {skill_name}" + (" (process)" if has_process else ""))

    skipped: list[str] = []
    if aux_specs:
        try:
            aux_files = _parse_aux_specs(aux_specs)
        except OSError as e:
            print(f"❌ {e}")
            return 1
    else:
        aux_files, skipped = _collect_aux_files(skill_dir)
    print(f"   {len(aux_files)} aux file(s)")
    if skipped:
        print(
            f"   🚫 excluded {len(skipped)} file(s) never staged for upload (dev-only/tests/secrets):"
        )
        for rel in skipped:
            print(f"      - {rel}")

    if _report_secret_findings(scan_upload(skill_md_text, aux_files), allow_secret_findings):
        return 1

    if not skip_audit:
        print("\n🔎 Running adopt-skill-review audit (local, free -- no harness spend)...")
        audit_code, audit_output = run_skill_audit(skill_dir)
        if audit_output:
            print(audit_output)
        if audit_code == 2 and not force:
            print(
                "❌ Critical adopt-skill-review findings -- fix locally first (this is the "
                "cheap half of the loop), or re-run with --force to push anyway."
            )
            return 1
        if audit_code == 1:
            print(
                "⚠️  adopt-skill-review found medium/high issues above -- worth fixing locally "
                "before spending a harness run on this push, but not blocking."
            )

    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    print(f"\n⬆️  Uploading to org tier{' (replace)' if replace else ''}...")
    try:
        client.upload_skill(
            skill_name=skill_name,
            skill_md_b64=base64.b64encode(skill_md_text.encode()).decode("ascii"),
            aux_files=aux_files,
            replace=replace,
        )
    except HarnessAPIError as e:
        print(f"❌ Upload failed: {e}")
        if e.status_code == 409:
            print(
                "   A skill with this name already exists -- retry with --replace to overwrite it."
            )
        return 1
    print("   ✅ Upload accepted")

    return verify(skill_name, meta, env)


def verify(skill_name: str, expected_meta: dict | None, env: str | None) -> int:
    """
    Re-fetch the skill from the platform and confirm it parsed the way we
    intended -- never trust a 200 on upload.
    """
    try:
        client = get_harness_client_for_env(env)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    print(f"\n🔍 Verifying live skill: {skill_name}")
    try:
        live = client.get_skill(skill_name)
    except HarnessAPIError as e:
        print(f"❌ Could not fetch skill after upload: {e}")
        return 1

    print(json.dumps(live, indent=2))

    if expected_meta is not None:
        expected_has_process = "process" in expected_meta
        # GET /skills/{name} never carries process info; only the catalog listing exposes
        # has_process, computed from the same server-side frontmatter parse.
        live_has_process = bool(live.get("has_process") or live.get("process"))
        if expected_has_process and not live_has_process:
            catalog = client.list_skills()
            entries: list[Any] = (
                catalog
                if isinstance(catalog, list)
                else next(
                    (
                        catalog[k]
                        for k in ("skills", "items", "data")
                        if isinstance(catalog, dict) and k in catalog
                    ),
                    [],
                )
            )
            live_has_process = any(
                isinstance(s, dict) and s.get("name") == skill_name and s.get("has_process")
                for s in entries
            )
        if expected_has_process and not live_has_process:
            print(
                "\n❌ MISMATCH: local SKILL.md has a 'process:' block, but the live skill does "
                "not show it. The frontmatter likely failed strict-YAML parsing on upload -- "
                "check for an unquoted ':' anywhere in the process block, including nested step "
                "titles."
            )
            return 1

        expected_output_type = expected_meta.get("output_type")
        if expected_output_type == "structured" and live.get("output_type") != "structured":
            print(
                "\n❌ MISMATCH: local SKILL.md declares output_type: structured, but the live "
                f"skill's output_type is {live.get('output_type')!r}. output_schema likely "
                "failed validation and silently degraded to text -- check the response above."
            )
            return 1

    print("\n✅ Live skill matches local intent")
    return 0


def delete(skill_name: str, env: str | None, yes: bool) -> int:
    if not yes:
        print(f"❌ Refusing to delete org skill '{skill_name}' without --yes")
        return 1
    try:
        client = get_harness_client_for_env(env)
        client.delete_skill(skill_name)
    except (ValueError, HarnessAPIError) as e:
        print(f"❌ Delete failed: {e}")
        return 1
    print(f"🗑️  Deleted org skill '{skill_name}'")
    return 0


def _audit_gate(target: Path, force: bool) -> bool:
    """Run the audit; return True if the push must stop."""
    print("\n🔎 Running adopt-skill-review audit (local, free -- no harness spend)...")
    code, output = run_skill_audit(target)
    if output:
        print(output)
    if code == 2 and not force:
        print(
            "❌ Critical adopt-skill-review findings -- fix locally first, or re-run with --force."
        )
        return True
    if code == 1:
        print("⚠️  adopt-skill-review found medium/high issues above (not blocking).")
    return False


def build_plugin_zip(plugin_dir: Path) -> tuple[bytes, dict, list[str], list[Finding]]:
    """Validate a plugin dir and zip it. Returns (zip_bytes, plugin_meta, skills, findings)."""
    manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        raise ValueError(f"{plugin_dir}: no .claude-plugin/plugin.json")
    try:
        meta = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"{manifest_path}: not valid JSON ({e})") from e
    plugin_name = str(meta.get("name") or "")
    if not _SLUG_RE.match(plugin_name):
        raise ValueError(f"{manifest_path}: name {plugin_name!r} must match ^[a-z0-9][a-z0-9-]*$")

    skills_root = plugin_dir / "skills"
    skill_dirs = (
        sorted(p.parent for p in skills_root.glob("*/SKILL.md")) if skills_root.exists() else []
    )
    if not skill_dirs:
        raise ValueError(f"{plugin_dir}: no skills/<slug>/SKILL.md found")
    findings: list[Finding] = []
    names = []
    for sd in skill_dirs:
        skill_md = sd / "SKILL.md"
        sm = _lint_frontmatter(skill_md.read_text(), skill_md)
        if sm["name"] != sd.name:
            raise ValueError(
                f"{skill_md}: name {sm['name']!r} must equal its directory {sd.name!r}"
            )
        names.append(sd.name)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(plugin_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(plugin_dir)
            # .claude-plugin/ is the one dot-directory a plugin needs.
            check_parts = rel.parts[1:] if rel.parts[0] == ".claude-plugin" else rel.parts
            if check_parts and _is_excluded(tuple(check_parts)):
                continue
            data = path.read_bytes()
            findings.extend(scan_bytes(rel.as_posix(), data))
            zf.writestr(rel.as_posix(), data)
    return buf.getvalue(), meta, names, findings


def push_plugin(
    plugin_dir: Path,
    replace: bool,
    force: bool,
    skip_audit: bool,
    env: str | None,
    allow_secret_findings: bool = False,
) -> int:
    try:
        zip_bytes, meta, skills, findings = build_plugin_zip(plugin_dir)
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    print(f"📦 Plugin: {meta['name']} {meta.get('version', '')} -- skills: {', '.join(skills)}")
    print(f"   zip: {len(zip_bytes)} bytes")
    if _report_secret_findings(findings, allow_secret_findings):
        return 1
    if not skip_audit and _audit_gate(plugin_dir, force):
        return 1
    try:
        client = get_harness_client_for_env(env)
        print(f"\n⬆️  Uploading plugin to org tier{' (replace)' if replace else ''}...")
        client.upload_plugin(zip_bytes, f"{meta['name']}.zip", replace=replace)
        live = client.get_plugin(str(meta["name"]))
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    except HarnessAPIError as e:
        print(f"❌ Upload failed: {e}")
        if e.status_code == 409:
            print("   A plugin with this name already exists -- retry with --replace.")
        return 1
    print(json.dumps(live, indent=2))
    print("\n✅ Plugin is live")
    return 0


def _parse_aux_specs(specs: list[str]) -> list[dict[str, str]]:
    out = []
    for spec in specs:
        arcname, _, path = spec.partition("=")
        src = Path(path or arcname)
        out.append(
            {
                "path": arcname if path else src.name,
                "content_b64": base64.b64encode(src.read_bytes()).decode(),
            }
        )
    return out


def deploy_default(
    skill_dir: Path,
    name: str | None,
    bundle_version: str,
    min_platform_contract: int,
    aux_specs: list[str] | None,
    dry_run: bool,
    env: str | None,
    allow_secret_findings: bool = False,
) -> int:
    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.exists():
        print(f"❌ No SKILL.md in {skill_dir}")
        return 1
    skill_md_text = skill_md_path.read_text()
    try:
        meta = _lint_frontmatter(skill_md_text, skill_md_path)
        aux_files = _parse_aux_specs(aux_specs) if aux_specs else _collect_aux_files(skill_dir)[0]
    except (ValueError, OSError) as e:
        print(f"❌ {e}")
        return 1
    skill_name = name or meta["name"]
    print(
        f"📦 Default-tier skill: {skill_name} bundle_version={bundle_version} "
        f"min_platform_contract={min_platform_contract} aux={[a['path'] for a in aux_files]}"
    )
    if any(not a["content_b64"] for a in aux_files):
        print("❌ an aux file is empty")
        return 1
    if _report_secret_findings(scan_upload(skill_md_text, aux_files), allow_secret_findings):
        return 1
    if dry_run:
        print("DRY RUN OK: payload assembles; nothing published.")
        return 0
    try:
        client = get_harness_client_for_env(env)
        print("\n⬆️  Publishing to the default (Built-in) tier...")
        client.upload_default_skill(
            skill_name=skill_name,
            skill_md_b64=base64.b64encode(skill_md_text.encode()).decode("ascii"),
            aux_files=aux_files,
            bundle_version=bundle_version,
            min_platform_contract=min_platform_contract,
        )
        pointer = client.get_default_skill(skill_name)
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    except HarnessAPIError as e:
        print(f"❌ Deploy failed: {e}")
        return 1
    if not pointer.get("published"):
        print(f"❌ verify FAILED: skill not live after publish: {pointer}")
        return 1
    print(
        f"✅ live: bundle_version={pointer.get('bundle_version')} content_dir={pointer.get('content_dir')}"
    )
    return 0


def _cmd_audit(ctx: Context) -> int:
    if not ctx.args.skill_dir.is_dir():
        # The audit script answers a missing path with exit 2 ("critical findings") and a bare
        # JSON error; a wrong path is a usage error, not a skill finding.
        raise UsageError(f"not a directory: {ctx.args.skill_dir}")
    return audit(ctx.args.skill_dir)


def _cmd_push(ctx: Context) -> int:
    a = ctx.args
    if ctx.dry_run_notice(f"would audit and upload skill dir {a.skill_dir}"):
        return 0
    return push(
        a.skill_dir,
        a.name,
        a.replace,
        a.force,
        a.skip_audit,
        a.env,
        a.allow_secret_findings,
        a.aux,
    )


def _cmd_verify(ctx: Context) -> int:
    return verify(ctx.args.skill_name, None, ctx.env_arg)


def _cmd_delete(ctx: Context) -> int:
    if ctx.dry_run_notice(f"would delete org skill '{ctx.args.skill_name}'"):
        return 0
    return delete(ctx.args.skill_name, ctx.env_arg, ctx.yes)


def _cmd_push_plugin(ctx: Context) -> int:
    a = ctx.args
    if ctx.dry_run_notice(f"would audit, zip and upload plugin dir {a.plugin_dir}"):
        return 0
    return push_plugin(
        a.plugin_dir, a.replace, a.force, a.skip_audit, a.env, a.allow_secret_findings
    )


def _cmd_deploy_default(ctx: Context) -> int:
    a = ctx.args
    return deploy_default(
        a.skill_dir,
        a.name,
        a.bundle_version,
        a.min_platform_contract,
        a.aux,
        a.dry_run,
        a.env,
        a.allow_secret_findings,
    )


def register(registry: Registry) -> None:
    """Register ``abcd skill ...`` and ``abcd plugin push`` (and the flat ``harness_skill.py``)."""
    skill = registry.group("skill", "Agent-harness skills")
    plugin = registry.group("plugin", "Agent-harness plugins")

    p = skill.command(
        "audit",
        _cmd_audit,
        help="Run the local adopt-skill-review check (free, no harness spend)",
        legacy_name="audit",
    )
    p.add_argument("skill_dir", type=Path, help="Local directory containing SKILL.md (+ aux files)")

    p = skill.command(
        "push",
        _cmd_push,
        help="Upload a local skill directory to the org tier",
        writes=True,
        legacy_name="push",
    )
    p.add_argument("skill_dir", type=Path, help="Local directory containing SKILL.md (+ aux files)")
    p.add_argument("--name", help="Override skill name (defaults to frontmatter 'name')")
    p.add_argument(
        "--replace", action="store_true", help="Overwrite an existing skill of the same name"
    )
    p.add_argument(
        "--force", action="store_true", help="Push even if adopt-skill-review finds critical issues"
    )
    p.add_argument(
        "--skip-audit", action="store_true", help="Skip the adopt-skill-review check entirely"
    )
    p.add_argument(
        "--aux",
        action="append",
        help="Upload exactly these aux files (ARCNAME=PATH, repeatable) instead of the dir's",
    )
    p.add_argument(
        "--allow-secret-findings",
        action="store_true",
        help="Upload even if the content secret scan reports findings (false positives only)",
    )

    p = skill.command(
        "verify",
        _cmd_verify,
        help="Re-fetch a live skill and print it",
        legacy_name="verify",
    )
    p.add_argument("skill_name")

    p = skill.command(
        "delete",
        _cmd_delete,
        help="Delete an org-tier skill (needs --yes)",
        writes=True,
        legacy_name="delete",
    )
    p.add_argument("skill_name")

    p = plugin.command(
        "push",
        _cmd_push_plugin,
        help="Zip + upload a plugin dir to the org tier",
        writes=True,
        legacy_name="push-plugin",
    )
    p.add_argument("plugin_dir", type=Path, help="Dir with .claude-plugin/plugin.json + skills/")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--skip-audit", action="store_true")
    p.add_argument("--allow-secret-findings", action="store_true")

    p = skill.command(
        "deploy-default",
        _cmd_deploy_default,
        help="Publish to the platform default (Built-in) tier",
        writes=True,
        legacy_name="deploy-default",
    )
    p.add_argument("skill_dir", type=Path)
    p.add_argument("--name", help="Catalog slug (defaults to frontmatter 'name')")
    p.add_argument("--bundle-version", default="dev")
    p.add_argument("--min-platform-contract", type=int, default=1)
    p.add_argument(
        "--aux",
        action="append",
        help="Aux file as ARCNAME=PATH (repeatable); default: the skill dir's aux files",
    )
    p.add_argument("--allow-secret-findings", action="store_true")


def main(argv: list[str] | None = None) -> int:
    return legacy_main("harness_skill.py", register, __doc__, argv)


if __name__ == "__main__":
    sys.exit(main())
