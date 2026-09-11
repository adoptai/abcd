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

Usage:
    python cli/harness_skill.py push <skill_dir> [--name NAME] [--replace] [--env ENV]
    python cli/harness_skill.py verify <skill_name> [--env ENV]
"""

import argparse
import base64
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from cli.harness_common.api_client import HarnessAPIError, get_harness_client_for_env

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


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


def push(skill_dir: Path, name: str | None, replace: bool, env: str | None) -> int:
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
            print("   A skill with this name already exists -- retry with --replace to overwrite it.")
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

    push_p = sub.add_parser("push", help="Upload a local skill directory to the org tier")
    push_p.add_argument(
        "skill_dir", type=Path, help="Local directory containing SKILL.md (+ aux files)"
    )
    push_p.add_argument("--name", help="Override skill name (defaults to frontmatter 'name')")
    push_p.add_argument(
        "--replace", action="store_true", help="Overwrite an existing skill of the same name"
    )
    push_p.add_argument("--env", help="Environment to use (defaults to active env)")

    verify_p = sub.add_parser("verify", help="Re-fetch a live skill and print it")
    verify_p.add_argument("skill_name")
    verify_p.add_argument("--env", help="Environment to use (defaults to active env)")

    args = parser.parse_args()

    if args.command == "push":
        sys.exit(push(args.skill_dir, args.name, args.replace, args.env))
    elif args.command == "verify":
        sys.exit(verify(args.skill_name, None, args.env))


if __name__ == "__main__":
    main()
