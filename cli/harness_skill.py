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

Usage:
    python cli/harness_skill.py audit <skill_dir>
    python cli/harness_skill.py push <skill_dir> [--name NAME] [--replace] [--force] [--skip-audit] [--env ENV]
    python cli/harness_skill.py verify <skill_name> [--env ENV]
"""

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env

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


def _collect_aux_files(skill_dir: Path) -> list[dict[str, str]]:
    aux_files = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path.name == "SKILL.md":
            continue
        rel = path.relative_to(skill_dir).as_posix()
        aux_files.append(
            {"path": rel, "content_b64": base64.b64encode(path.read_bytes()).decode("ascii")}
        )
    return aux_files


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

    aux_files = _collect_aux_files(skill_dir)
    print(f"   {len(aux_files)} aux file(s)")

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
        live_has_process = bool(live.get("has_process") or live.get("process"))
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit_p = sub.add_parser(
        "audit", help="Run the local adopt-skill-review check (free, no harness spend)"
    )
    audit_p.add_argument(
        "skill_dir", type=Path, help="Local directory containing SKILL.md (+ aux files)"
    )

    push_p = sub.add_parser("push", help="Upload a local skill directory to the org tier")
    push_p.add_argument(
        "skill_dir", type=Path, help="Local directory containing SKILL.md (+ aux files)"
    )
    push_p.add_argument("--name", help="Override skill name (defaults to frontmatter 'name')")
    push_p.add_argument(
        "--replace", action="store_true", help="Overwrite an existing skill of the same name"
    )
    push_p.add_argument(
        "--force", action="store_true", help="Push even if adopt-skill-review finds critical issues"
    )
    push_p.add_argument(
        "--skip-audit", action="store_true", help="Skip the adopt-skill-review check entirely"
    )
    push_p.add_argument("--env", help="Environment to use (defaults to active env)")

    verify_p = sub.add_parser("verify", help="Re-fetch a live skill and print it")
    verify_p.add_argument("skill_name")
    verify_p.add_argument("--env", help="Environment to use (defaults to active env)")

    args = parser.parse_args()

    if args.command == "audit":
        sys.exit(audit(args.skill_dir))
    elif args.command == "push":
        sys.exit(
            push(args.skill_dir, args.name, args.replace, args.force, args.skip_audit, args.env)
        )
    elif args.command == "verify":
        sys.exit(verify(args.skill_name, None, args.env))


if __name__ == "__main__":
    main()
