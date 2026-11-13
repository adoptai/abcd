"""Staged shadows of skills and plugins (PLAN.md section 7).

The staged copy is built in a temporary directory from the workspace source, so the
workspace never holds ``--staged`` names. What changes in the copy:

- frontmatter ``name:`` becomes ``<name>--staged``;
- the frontmatter ``process:`` block is dropped (no second process template);
- the description starts ``Staging copy, do not use. ``;
- references to other renamed skills/processes in the SKILL.md body (and, through
  :func:`rewrite_text` / :func:`rewrite_flow`, in ``agent_instructions`` and
  ``compiled_flow``) are rewritten.

The NoUI files (``operations.json``, ``manifest.json``, ``amendments.json``,
``replay_approval.json``, ``recording_bundle.json``) are copied byte for byte so every
digest still matches. Release does not invert a staged copy: it uploads from the
workspace source dir (:func:`live_upload_dir` only validates that dir).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from cli.builder_common.errors import BuilderError, RefusedError, UsageError
from cli.harness_skill import _FRONTMATTER_RE, _SLUG_RE, _is_excluded

STAGED_SKILL_SUFFIX = "--staged"
STAGED_AGENT_SUFFIX = "-staged"
STAGED_DESCRIPTION_PREFIX = "Staging copy, do not use. "

#: Copied byte for byte (PLAN.md section 7).
NOUI_BYTE_FILES = (
    "operations.json",
    "manifest.json",
    "amendments.json",
    "replay_approval.json",
    "recording_bundle.json",
)

_NAME_CHARS = "A-Za-z0-9_-"


def staged_name(name: str) -> str:
    """``tb-reconcile`` -> ``tb-reconcile--staged``."""
    if name.endswith(STAGED_SKILL_SUFFIX):
        raise RefusedError(f"{name!r} is already a staged name")
    return f"{name}{STAGED_SKILL_SUFFIX}"


def staged_agent_name(name: str) -> str:
    """``acme-prep`` -> ``acme-prep-staged``."""
    if name.endswith(STAGED_AGENT_SUFFIX):
        raise RefusedError(f"{name!r} is already a staged name")
    return f"{name}{STAGED_AGENT_SUFFIX}"


# -- Frontmatter ----------------------------------------------------------


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Strict-YAML frontmatter and the body that follows it (body kept exactly)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise UsageError("no YAML frontmatter block found (expected '---' ... '---' at the top)")
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        raise UsageError(f"frontmatter is not strict YAML: {e}") from e
    if not isinstance(meta, dict):
        raise UsageError("frontmatter did not parse to a mapping")
    return meta, m.group(2)


def join_frontmatter(meta: Mapping[str, Any], body: str) -> str:
    """Inverse of :func:`split_frontmatter`; key order and every value are preserved."""
    dumped = yaml.safe_dump(
        dict(meta), sort_keys=False, allow_unicode=True, default_flow_style=False, width=10**9
    )
    text = f"---\n{dumped}---\n{body}"
    reparsed, rebody = split_frontmatter(text)
    if reparsed != dict(meta) or rebody != body:
        raise BuilderError("frontmatter did not survive the YAML round trip")
    return text


# -- Reference rewriting --------------------------------------------------


def _compile(rename_map: Mapping[str, str]) -> re.Pattern[str] | None:
    names = sorted((n for n, new in rename_map.items() if n and n != new), key=len, reverse=True)
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(rf"(?<![{_NAME_CHARS}])({alt})(?![{_NAME_CHARS}])")


def rewrite_text(text: str, rename_map: Mapping[str, str]) -> str:
    """Replace whole-name occurrences of renamed objects (single pass, longest first).

    A name inside a longer identifier (``tb-reconcile-v2``, ``tb-reconcile--staged``)
    is not a reference and is left alone.
    """
    pattern = _compile(rename_map)
    if pattern is None:
        return text
    return pattern.sub(lambda m: rename_map[m.group(1)], text)


def rewrite_flow(value: Any, rename_map: Mapping[str, str]) -> Any:
    """:func:`rewrite_text` over every string value of a JSON structure (not keys)."""
    if isinstance(value, str):
        return rewrite_text(value, rename_map)
    if isinstance(value, list):
        return [rewrite_flow(v, rename_map) for v in value]
    if isinstance(value, dict):
        return {k: rewrite_flow(v, rename_map) for k, v in value.items()}
    return value


def _staged_description(description: Any) -> str:
    text = str(description or "").strip()
    return f"{STAGED_DESCRIPTION_PREFIX}{text}" if text else STAGED_DESCRIPTION_PREFIX.strip()


# -- Skills ---------------------------------------------------------------


@dataclass(frozen=True)
class StagedSkill:
    out_dir: Path
    live_name: str
    staged_name: str
    dropped_process: bool
    files: dict[str, str] = field(default_factory=dict)  # relative path -> sha256 hex


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stage_skill_md(text: str, rename_map: Mapping[str, str]) -> tuple[str, str, str, bool]:
    """Rewrite one SKILL.md. Returns ``(text, live_name, staged_name, dropped_process)``."""
    meta, body = split_frontmatter(text)
    live = str(meta.get("name") or "")
    if not _SLUG_RE.match(live):
        raise UsageError(f"frontmatter name {live!r} is not a valid slug")
    new_name = rename_map.get(live) or staged_name(live)
    if not _SLUG_RE.match(new_name):
        raise UsageError(f"staged name {new_name!r} is not a valid slug")
    full_map = {**rename_map, live: new_name}
    meta["name"] = new_name
    dropped = meta.pop("process", None) is not None
    meta["description"] = _staged_description(meta.get("description"))
    return join_frontmatter(meta, rewrite_text(body, full_map)), live, new_name, dropped


def _ensure_empty_out(out_dir: Path) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise UsageError(f"{out_dir} is not empty")
    out_dir.mkdir(parents=True, exist_ok=True)


def _copy_tree(
    src: Path, out: Path, *, skip: set[str], plugin_root: bool = False
) -> dict[str, str]:
    """Copy source files (same exclusions as a push), byte for byte. Returns hashes."""
    hashes: dict[str, str] = {}
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if rel.as_posix() in skip:
            continue
        parts = rel.parts[1:] if plugin_root and rel.parts[0] == ".claude-plugin" else rel.parts
        # NoUI gate files stay in the local staged copy (the browser gate reads them there)
        # even when the upload leaves them out, as it does recording_bundle.json.
        if parts and _is_excluded(tuple(parts)) and rel.as_posix() not in NOUI_BYTE_FILES:
            continue
        data = path.read_bytes()
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if dest.read_bytes() != data:
            raise BuilderError(f"copy of {rel.as_posix()} is not byte-identical")
        hashes[rel.as_posix()] = _sha256(data)
    return hashes


def build_staged_skill(
    src_dir: Path, out_dir: Path, rename_map: Mapping[str, str] | None = None
) -> StagedSkill:
    """Build the staged shadow of ``src_dir`` into the empty/new ``out_dir``."""
    src_dir, out_dir = Path(src_dir), Path(out_dir)
    skill_md = src_dir / "SKILL.md"
    if not skill_md.is_file():
        raise UsageError(f"no SKILL.md in {src_dir}")
    staged_text, live, new_name, dropped = stage_skill_md(skill_md.read_text(), rename_map or {})
    _ensure_empty_out(out_dir)
    hashes = _copy_tree(src_dir, out_dir, skip={"SKILL.md"})
    (out_dir / "SKILL.md").write_text(staged_text)
    hashes["SKILL.md"] = _sha256(staged_text.encode())
    for name in NOUI_BYTE_FILES:
        s, d = src_dir / name, out_dir / name
        if s.is_file() and (not d.is_file() or s.read_bytes() != d.read_bytes()):
            raise BuilderError(f"{name} was not copied byte for byte")
    return StagedSkill(out_dir, live, new_name, dropped, dict(sorted(hashes.items())))


# -- Plugins --------------------------------------------------------------


@dataclass(frozen=True)
class StagedPlugin:
    out_dir: Path
    live_name: str
    staged_name: str
    skills: dict[str, str]  # live skill name -> staged skill name
    dropped_process: list[str] = field(default_factory=list)


def build_staged_plugin(
    src_dir: Path, out_dir: Path, rename_map: Mapping[str, str] | None = None
) -> StagedPlugin:
    """Staged shadow of a plugin dir: manifest renamed, every skill renamed the same way
    (directory and frontmatter name, so ``push-plugin``'s name == directory rule holds)."""
    src_dir, out_dir = Path(src_dir), Path(out_dir)
    manifest_path = src_dir / ".claude-plugin" / "plugin.json"
    if not manifest_path.is_file():
        raise UsageError(f"{src_dir}: no .claude-plugin/plugin.json")
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        raise UsageError(f"{manifest_path}: not valid JSON ({e})") from e
    live = str(manifest.get("name") or "")
    if not _SLUG_RE.match(live):
        raise UsageError(f"{manifest_path}: name {live!r} is not a valid slug")

    skill_dirs = sorted(p.parent for p in (src_dir / "skills").glob("*/SKILL.md"))
    if not skill_dirs:
        raise UsageError(f"{src_dir}: no skills/<slug>/SKILL.md found")
    full_map = dict(rename_map or {})
    new_plugin = full_map.get(live) or staged_name(live)
    full_map[live] = new_plugin
    for sd in skill_dirs:
        full_map.setdefault(sd.name, staged_name(sd.name))

    _ensure_empty_out(out_dir)
    _copy_tree(
        src_dir,
        out_dir,
        skip={".claude-plugin/plugin.json"}
        | {p.relative_to(src_dir).as_posix() for p in src_dir.glob("skills/*/SKILL.md")},
        plugin_root=True,
    )
    # Move each copied skill directory to its staged name.
    skills: dict[str, str] = {}
    dropped: list[str] = []
    for sd in skill_dirs:
        new_slug = full_map[sd.name]
        target = out_dir / "skills" / new_slug
        source_copy = out_dir / "skills" / sd.name
        if source_copy.exists() and source_copy != target:
            shutil.move(str(source_copy), str(target))
        target.mkdir(parents=True, exist_ok=True)
        text, skill_live, skill_new, had_process = stage_skill_md(
            (sd / "SKILL.md").read_text(), full_map
        )
        if skill_live != sd.name:
            raise UsageError(f"{sd / 'SKILL.md'}: name {skill_live!r} must equal its directory")
        if skill_new != new_slug:
            raise UsageError(f"{sd / 'SKILL.md'}: rename map gives {skill_new!r}, not {new_slug!r}")
        (target / "SKILL.md").write_text(text)
        skills[skill_live] = skill_new
        if had_process:
            dropped.append(skill_live)

    staged_manifest = dict(manifest)
    staged_manifest["name"] = new_plugin
    staged_manifest["description"] = _staged_description(manifest.get("description"))
    dest = out_dir / ".claude-plugin" / "plugin.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(staged_manifest, indent=2, ensure_ascii=False) + "\n")
    return StagedPlugin(out_dir, live, new_plugin, skills, dropped)


# -- Live (release) -------------------------------------------------------


def live_upload_dir(src_dir: Path) -> Path:
    """Validate that ``src_dir`` is a workspace source dir fit to upload under live names.

    Release uploads from the workspace source, so names, ``process:`` block and
    description are already the live ones; this refuses a dir that looks staged.
    """
    src_dir = Path(src_dir)
    skill_md = src_dir / "SKILL.md"
    if not skill_md.is_file():
        raise UsageError(f"no SKILL.md in {src_dir}")
    meta, _ = split_frontmatter(skill_md.read_text())
    name = str(meta.get("name") or "")
    if not _SLUG_RE.match(name):
        raise UsageError(f"frontmatter name {name!r} is not a valid slug")
    if name.endswith(STAGED_SKILL_SUFFIX):
        raise RefusedError(f"{src_dir} holds a staged name ({name}); release uploads the source")
    if str(meta.get("description") or "").startswith(STAGED_DESCRIPTION_PREFIX.strip()):
        raise RefusedError(f"{src_dir} has a staging description; release uploads the source")
    return src_dir


def restore_live(src_dir: Path) -> Path:
    """The live upload dir for a skill: the workspace source itself, validated."""
    return live_upload_dir(src_dir)


def staged_matches_source(
    staged_dir: Path, src_dir: Path, rename_map: Mapping[str, str] | None = None
) -> bool:
    """Is ``staged_dir`` exactly what staging ``src_dir`` would produce now?"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        fresh = build_staged_skill(src_dir, Path(tmp) / "s", rename_map)
        have = {
            p.relative_to(staged_dir).as_posix(): _sha256(p.read_bytes())
            for p in sorted(Path(staged_dir).rglob("*"))
            if p.is_file()
        }
        return have == fresh.files
