#!/usr/bin/env python3
"""
audit_skill.py -- deterministic audit of an Adopt agent-harness skill or plugin.

Finds what actually costs LLM steps and wall-clock time inside the harness, plus the
silent-failure traps that let a skill ship plausible-but-wrong output.

Every check here is mechanical. Judgement calls (is this instruction clear? is this
guard real?) are left to the reviewing agent -- this script's job is to hand it a
complete, evidence-backed list so it never has to go spelunking through the bundle.

Usage
    python3 audit_skill.py <path> [--format json|text] [--out FILE]

<path> is either
    a skill directory   -- contains SKILL.md
    a plugin directory  -- contains .claude-plugin/plugin.json and skills/<slug>/SKILL.md

Exit codes
    0  nothing above `low`
    1  at least one `medium` or `high` finding
    2  at least one `critical` finding
    3  usage error / unreadable target

Platform facts pinned here are verified against adoptai-workflows origin/dev.
See references/platform-contract.md for the stamp and scripts/verify_platform_facts.py
to re-check them against a live checkout.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    import yaml  # type: ignore

    HAVE_YAML = True
except ImportError:  # pragma: no cover - degraded mode
    HAVE_YAML = False


# ---------------------------------------------------------------------------
# Pinned platform limits (adoptai-workflows origin/dev @ 63980995, 2026-09-10)
# ---------------------------------------------------------------------------

MAX_TOOL_ITERATIONS = 100
MAX_SKILL_FILE_BYTES = 256 * 1024          # read cap; truncation, silent for text
MAX_SKILL_UPLOAD_BYTES = 50 * 1024 * 1024  # SKILL.md + all aux combined
MAX_AUX_FILES_PER_SKILL = 100
MAX_SKILLS_PER_PLUGIN = 50
BASH_OUTPUT_MAX_CHARS = 100_000
GETMANY_CONCURRENCY = 5                    # sandbox parallel file-transfer slots
PROCESS_STEPS_MIN, PROCESS_STEPS_MAX = 1, 50
DB_INSERT_MAX_ROWS = 500
MEMORY_MAX_FILES = 32

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# The 65 render_ui component schemas the harness ships. Two are harness-emitted only
# and must never be named by a skill.
GENUI_SCHEMAS = {
    "action-card", "action-list", "aging-report", "alert", "allocation-donut",
    "amortization-table", "area-chart", "audit-trail", "balance-sheet", "bullet-chart",
    "cash-flow-statement", "chart-of-accounts", "cited-answer", "combo-chart",
    "comparative-bar-chart", "compliance-checklist", "connect-integration", "data-table",
    "decision-card", "decision-queue", "depreciation-schedule",
    "document-field-extraction", "document-preview", "download-card",
    "engagement-pipeline", "entity-card-grid", "escalation-card", "expandable-table",
    "file-browser", "flow-canvas", "funnel-chart", "gauge-chart", "general-ledger",
    "grouped-table", "heatmap-table", "income-statement", "integrations-list",
    "invoice-detail", "job-tracker", "journal-entry", "key-value-list",
    "legal-test-result", "line-chart", "news-feed", "ops-dashboard", "pipeline-preview",
    "provenance-chain", "radar-chart", "reconciliation-view", "scatter-chart",
    "screener-table", "search-results", "skill-replay", "sparkline-table",
    "stacked-breakdown", "stat-grid", "status-card", "tabby-auth", "tabs-panel",
    "transaction-feed", "treemap-chart", "trial-balance", "variance-analysis",
    "waterfall-chart", "workflow-stepper",
}
GENUI_HARNESS_EMITTED_ONLY = {"tabby-auth", "skill-replay"}

# Frontmatter keys the harness reads (skills._meta_to_summary + live reads).
KNOWN_FRONTMATTER = {
    "name", "description", "keywords", "featured", "process", "genui_schemas",
    "output_type", "output_schema", "datastore_write", "write_tables",
    "datastore_delete", "delete_tables", "auth", "api_hosts", "api_host",
}
# Keys that look plausible and do nothing. Value = what actually happens.
SILENT_NOOP_KEYS = {
    "tagline": "never read -- the tagline is always the first 8 words of `description`",
    "version": "Claude-Code-style key, not read by this harness",
    "license": "not read",
    "allowed-tools": "not read -- per-turn tool gating is platform-side, a skill cannot request tools",
    "model": "not read",
    "author": "not read on a skill (only plugin.json uses it)",
    "tools": "not read",
    "argument-hint": "Claude-Code-style key, not read by this harness",
}

# Guidance the harness injects into the system prompt on EVERY turn. A skill that
# restates these pays context tokens for them on every step and can only weaken them.
# (pattern, what the platform already says, where it comes from)
ALREADY_ENFORCED = [
    (re.compile(r"\bnever (?:read|grep|open)\b[^.\n]{0,60}\.py\b|\bdo not read\b[^.\n]{0,40}source", re.I),
     "never read a skill's .py source to understand it",
     "build_skills_system_guidance"),
    (re.compile(r"\bbatch(?:ing|ed)?\b[^.\n]{0,60}\bsame (?:response|message|step)\b|\bsame (?:response|assistant message)\b[^.\n]{0,60}\bbatch", re.I),
     "batching narrative + work tools into one response (with BAD/GOOD examples)",
     "build_narrative_system_guidance -> 'CRITICAL: Batching to preserve iteration budget'"),
    (re.compile(r"\bindependent\b[^.\n]{0,50}\b(?:parallel|one response|same message)\b|\bin parallel\b[^.\n]{0,40}\bone (?:response|message)\b", re.I),
     "run independent operations in parallel in one response",
     "build_narrative_system_guidance -> 'CRITICAL: Run independent operations in parallel'"),
    (re.compile(r"\bone script beats\b|\bdo not (?:call bash )?repeatedly\b|\bre-?open the same (?:file|workbook)\b", re.I),
     "one script beats many exploratory calls; don't reopen the same file",
     "build_narrative_system_guidance -> 'CRITICAL: One script beats many exploratory calls'"),
    (re.compile(r"\bdo not paste\b[^.\n]{0,50}\b(?:script|source)\b|\bmulti-?KB\b[^.\n]{0,30}bash", re.I),
     "keep bash commands small; never paste script source into bash",
     "build_narrative_system_guidance -> 'CRITICAL: Keep bash commands small'"),
    (re.compile(r"\bend (?:your|the) turn\b[^.\n]{0,40}render_builder|render_builder\b[^.\n]{0,40}\bend (?:your|the) turn\b", re.I),
     "after render_builder, END THE TURN and call no more tools",
     "render_builder tool description + build_narrative_system_guidance"),
    (re.compile(r"ASCII|box-?art|box-?draw|\u250c\u2500|\u2502\s*\u2514", re.I),
     "never print an ASCII / box-art stage tracker; use flow-canvas or the pinned stepper",
     "build_genui_system_guidance + build_process_system_guidance"),
    (re.compile(r"\bnever (?:fabricate|synthesi[sz]e|invent|mock)\b|\bsynthetic (?:data|demo)\b|\bdemo data\b", re.I),
     "never fabricate/synthesize data; missing input is a file_upload escalation",
     "build_narrative_system_guidance -> 'Data integrity -- never fabricate'"),
    (re.compile(r"\bmore than one block\b|\bmulti-?block\b[^.\n]{0,40}stepper|builder_kind\b[^.\n]{0,30}\bstepper\b[^.\n]{0,40}\bblocks\b", re.I),
     "more than one block ⇒ builder_kind MUST be 'stepper' (form/escalation drop later blocks)",
     "build_narrative_system_guidance -> 'HARD RULE'"),
    (re.compile(r"\bfile_ref\b", re.I),
     "use render_ui {type, file_ref} for large payloads; never retype or cat them",
     "build_genui_system_guidance -> 'Large payloads: pass file_ref, never retype'"),
    (re.compile(r"\bgated step\b[^.\n]{0,60}\bnever\b[^.\n]{0,20}\bdone\b|\bcannot\b[^.\n]{0,30}\bmark\b[^.\n]{0,20}\bdone\b", re.I),
     "a gated step can never be set to 'done' by the agent (server-enforced)",
     "build_process_system_guidance + update_process_step tool description"),
    (re.compile(r"\bnever spend a step\b|\b(?:start_chip|reflect)\b[^.\n]{0,40}\balone\b", re.I),
     "never spend a step on a lifecycle/progress call alone",
     "start_chip / reflect / start_phase / end_phase / update_process_step tool descriptions (landed on dev)"),
    (re.compile(r"\bprose\b[^.\n]{0,40}\b(?:menu|numbered|bracket)|\breply 1\b|\[1\]\s*\w+\s+\[2\]", re.I),
     "never render a hand-typed choice menu in prose; use builder fields",
     "build_narrative_system_guidance"),
    (re.compile(r"\bmatplotlib\b|\bplotly\b|\bseaborn\b|\bASCII chart\b", re.I),
     "never generate charts with matplotlib/plotly/seaborn; use a render_ui chart component",
     "build_genui_system_guidance -> 'Visualizations belong in render_ui'"),
    (re.compile(r"\bthroat-?clearing\b|\bnever (?:open|start) with\b[^.\n]{0,30}(?:I'll|Let me)", re.I),
     "voice rules: no throat-clearing, first action is a tool call",
     "build_narrative_system_guidance -> 'Voice rules (enforced)'"),
    (re.compile(r"\bdo not render the (?:process )?stepper\b|\bstepper IS the tracker\b", re.I),
     "do not render the process stepper via render_ui -- the pinned stepper is the tracker",
     "build_process_system_guidance"),
]

READER_CMDS = ("grep", "sed", "cat", "head", "tail", "wc", "find", "ls", "awk")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass
class Evidence:
    file: str
    line: int | None = None
    text: str = ""


@dataclass
class Finding:
    check: str
    category: str
    severity: str
    title: str
    why: str
    fix: str
    skill: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    est_steps_saved: int | None = None
    effort: str = "low"
    reference: str = ""


@dataclass
class SkillBundle:
    name: str
    dir: Path
    skill_md: Path
    raw: bytes
    frontmatter_text: str
    meta: dict
    meta_error: str
    body: str
    body_offset: int  # line number in SKILL.md where the body starts
    aux: list[Path] = field(default_factory=list)

    @property
    def body_lines(self) -> list[str]:
        return self.body.splitlines()

    def rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.dir))
        except ValueError:
            return str(p)

    def body_line_no(self, idx0: int) -> int:
        """SKILL.md line number for a 0-based index into the body."""
        return self.body_offset + idx0 + 1


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(rb"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)

IGNORED_AUX = {".DS_Store"}
IGNORED_AUX_DIRS = {"__pycache__", ".git", ".pytest_cache", ".ruff_cache", "node_modules"}


def load_skill(skill_dir: Path) -> SkillBundle:
    md = skill_dir / "SKILL.md"
    raw = md.read_bytes()
    m = FRONTMATTER_RE.match(raw)
    fm_text, body, offset = "", raw.decode("utf-8", "replace"), 0
    meta: dict = {}
    meta_error = ""
    if not m:
        meta_error = "no YAML frontmatter delimited by --- ... --- at the very start of the file"
    else:
        fm_text = m.group(1).decode("utf-8", "replace")
        body = m.group(2).decode("utf-8", "replace")
        offset = fm_text.count("\n") + 3  # two --- lines + the newline after
        if HAVE_YAML:
            try:
                loaded = yaml.safe_load(fm_text)
                if isinstance(loaded, dict):
                    meta = loaded
                else:
                    meta_error = f"frontmatter parsed as {type(loaded).__name__}, not a mapping"
            except Exception as e:  # noqa: BLE001 - we want the parser's own message
                meta_error = f"yaml.safe_load failed: {e}"
        else:
            meta_error = "PyYAML not installed -- frontmatter checks degraded"

    aux: list[Path] = []
    for p in sorted(skill_dir.rglob("*")):
        if not p.is_file() or p.name == "SKILL.md":
            continue
        if p.name in IGNORED_AUX or any(part in IGNORED_AUX_DIRS for part in p.parts):
            continue
        aux.append(p)

    name = str(meta.get("name") or skill_dir.name)
    return SkillBundle(
        name=name, dir=skill_dir, skill_md=md, raw=raw, frontmatter_text=fm_text,
        meta=meta, meta_error=meta_error, body=body, body_offset=offset, aux=aux,
    )


def discover(target: Path) -> tuple[list[SkillBundle], dict | None, str]:
    """Return (skills, plugin_json, kind)."""
    if (target / "SKILL.md").is_file():
        return [load_skill(target)], None, "skill"

    manifest = target / ".claude-plugin" / "plugin.json"
    skills_root = target / "skills"
    if manifest.is_file() or skills_root.is_dir():
        pj = None
        if manifest.is_file():
            try:
                pj = json.loads(manifest.read_text("utf-8"))
            except Exception as e:  # noqa: BLE001
                pj = {"__parse_error__": str(e)}
        bundles = []
        if skills_root.is_dir():
            for d in sorted(p for p in skills_root.iterdir() if p.is_dir()):
                if (d / "SKILL.md").is_file():
                    bundles.append(load_skill(d))
        return bundles, pj, "plugin"

    raise FileNotFoundError(
        f"{target} is neither a skill directory (no SKILL.md) nor a plugin directory "
        f"(no .claude-plugin/plugin.json and no skills/)"
    )


# ---------------------------------------------------------------------------
# Staging rules -- where the harness actually puts a file
# ---------------------------------------------------------------------------

def is_script_rel(rel: str) -> bool:
    """Mirrors skills.is_skill_script_rel: only scripts/*.py|.sh stage as runnable."""
    rel = rel.replace("\\", "/")
    if not rel.startswith("scripts/"):
        return False
    name = rel.rsplit("/", 1)[-1]
    return bool(name) and name.endswith((".py", ".sh"))


def looks_binary(data: bytes) -> bool:
    """Mirrors skills._looks_binary."""
    sample = data[:MAX_SKILL_FILE_BYTES]
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as e:
        return e.start < len(sample) - 4
    return False


def staging_fate(rel: str, data: bytes) -> str:
    """Where this aux file ends up: 'disk-script' | 'disk-asset' | 'disk-binary' | 'inline'."""
    rel = rel.replace("\\", "/")
    if is_script_rel(rel):
        return "disk-script"
    if looks_binary(data):
        return "disk-binary"
    if rel.startswith("assets/"):
        return "disk-asset"
    return "inline"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_lines(text: str, pattern: re.Pattern, offset: int = 0, limit: int = 8):
    out = []
    for i, line in enumerate(text.splitlines()):
        if pattern.search(line):
            out.append((offset + i + 1, line.strip()[:200]))
            if len(out) >= limit:
                break
    return out


def read_text(p: Path) -> str:
    try:
        return p.read_text("utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# CHECK GROUP 1 -- file placement and staging (silent-wrong-output class)
# ---------------------------------------------------------------------------

# Scripts resolving a `references/` path off disk. references/* TEXT is inline-only:
# it is returned into the model's context and NEVER written to the sandbox filesystem.
ASSET_ON_DISK_RE = re.compile(r"""['"]assets['"]|/assets/|\.\./assets/""")

# Fallback only, for a file Python cannot tokenize (syntax error, or a .sh script).
REF_ON_DISK_RES = [
    re.compile(r"""\w\s*/\s*['"]references['"]"""),
    re.compile(r"""os\.path\.join\([^)]*['"]references['"]"""),
    re.compile(r"""open\(\s*['"][^'"]*references/"""),
]


def _enclosing_callee(toks: list, idx: int) -> str:
    """Name of the function whose argument list contains token ``idx``.

    Walks back to the unmatched opening bracket, so it works for a string in ANY
    argument position -- `os.path.join(base, "references/x")` must resolve to
    `join`, not to `base`, or a real path build reads as innocent.
    """
    depth = 0
    j = idx - 1
    while j >= 0:
        s = toks[j].string
        if s in (")", "]", "}"):
            depth += 1
        elif s in ("(", "[", "{"):
            if depth == 0:
                if s == "(" and j > 0:
                    return toks[j - 1].string
                return ""
            depth -= 1
        j -= 1
    return ""


def find_reference_path_uses(src: str) -> list[tuple[int, str]]:
    """Lines where the code genuinely resolves a `references/` PATH.

    Tokenizing rather than regexing, because a prose mention of the idiom -- in a
    docstring, a comment, or an error message explaining this very rule -- is not a
    path resolution, and flagging it makes the check untrustworthy. A tokenizer
    separates the two for free: prose is one long STRING token whose value is a
    sentence, while a real path segment is a STRING token whose value is exactly
    `references` (adjacent to a `/` join) or begins `references/`.
    """
    import io
    import tokenize

    hits: list[tuple[int, str]] = []
    try:
        toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline)
                if t.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
                                  tokenize.INDENT, tokenize.DEDENT)]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                continue
            if any(r.search(line) for r in REF_ON_DISK_RES):
                hits.append((i + 1, line.strip()[:200]))
        return hits

    lines = src.splitlines()
    for idx, t in enumerate(toks):
        if t.type != tokenize.STRING:
            continue
        try:
            val = ast.literal_eval(t.string)
        except (ValueError, SyntaxError):
            continue
        if not isinstance(val, str):
            continue
        norm = val.replace("\\", "/").strip()
        is_segment = norm == "references"
        is_relpath = norm.startswith("references/") or norm.startswith("../references/")
        if not (is_segment or is_relpath):
            continue
        prev = toks[idx - 1].string if idx else ""
        nxt = toks[idx + 1].string if idx + 1 < len(toks) else ""
        callee = _enclosing_callee(toks, idx)

        # Only a string BEING BUILT INTO A PATH counts. The same literal appears in
        # three innocent shapes that are not path resolution at all, and flagging
        # them would make this check untrustworthy:
        #   a doc pointer      reference="references/file-layout.md"   (prev is '=')
        #   an inspection      p.startswith("references/")            (callee is a predicate)
        #   a comparison       norm == "references"
        # A genuine resolution is either adjacent to the pathlib `/` operator or an
        # argument to something that opens or constructs a path.
        PATH_CALLS = {
            "open", "Path", "PurePath", "PurePosixPath", "PosixPath", "join",
            "read_text", "read_bytes", "loads", "load", "read_json", "glob",
            "iterdir", "joinpath", "with_name", "resolve", "exists", "is_file",
        }
        joined = prev == "/" or nxt == "/"
        if is_segment and not joined:
            continue
        if is_relpath and not (joined or (prev in ("(", ",") and callee in PATH_CALLS)):
            continue
        if is_segment and prev == "(" and callee and callee not in PATH_CALLS:
            continue
        ln = t.start[0]
        text = lines[ln - 1].strip()[:200] if 0 < ln <= len(lines) else t.string
        hits.append((ln, text))
    return hits


def check_file_placement(b: SkillBundle) -> list[Finding]:
    out: list[Finding] = []
    scripts = [p for p in b.aux if is_script_rel(b.rel(p))]
    has_scripts = bool(scripts)

    # 1a. Scripts resolving references/ from disk -> the file is never there.
    ev: list[Evidence] = []
    for p in scripts:
        for ln, text in find_reference_path_uses(read_text(p)):
            ev.append(Evidence(b.rel(p), ln, text))
    if ev:
        out.append(Finding(
            check="references_resolved_from_disk",
            category="file-placement",
            severity="critical",
            title=f"{len(ev)} script location(s) read a `references/` path off disk, "
                  f"but references/ text files are never written to the sandbox",
            why="`read_skill_file` stages to disk ONLY: scripts/*.py|.sh, any binary file, and "
                "everything under assets/ (auto co-staged when any script of that skill stages). "
                "A TEXT file under references/ is returned inline into the model's context and "
                "never lands on the filesystem. A script defaulting to "
                "`Path(__file__).parent.parent/'references'/x.json` therefore finds nothing. "
                "If the script raises you lose steps diagnosing it; if it falls back to a built-in "
                "default it produces plausible, silently wrong output.",
            fix="Move every file a script must open into `assets/`, and point the script at "
                "`assets/`. Files the AGENT reads (prose, patterns, schemas) stay in `references/`. "
                "A file both need should live in `assets/` and be described in SKILL.md.",
            skill=b.name, evidence=ev, effort="low",
            est_steps_saved=None,
            reference="references/file-layout.md#the-staging-matrix",
        ))

    # 1b. assets/ present but no script -> co-staging never fires.
    assets = [p for p in b.aux if b.rel(p).replace("\\", "/").startswith("assets/")]
    if assets and not has_scripts:
        out.append(Finding(
            check="assets_without_scripts",
            category="file-placement", severity="high",
            title=f"{len(assets)} file(s) under assets/ but this skill ships no scripts/*.py|.sh",
            why="assets/ is co-staged to disk as a side effect of staging a SCRIPT from the same "
                "skill. With no script, nothing triggers the co-stage, so a binary asset only "
                "lands if the agent read_skill_file's it directly, and a TEXT asset never lands "
                "at all.",
            fix="Either ship the script that consumes these assets in this same skill, or have "
                "SKILL.md name the exact read_skill_file call per asset. Assets belonging to "
                "another skill's scripts must live in THAT skill.",
            skill=b.name,
            evidence=[Evidence(b.rel(p)) for p in assets[:8]],
            effort="low", reference="references/file-layout.md#co-staging",
        ))

    # 1c. Text files under assets/ that nothing seems to read (they cost co-stage time).
    # and binary files under references/ (they DO land, but the layout misleads readers).
    bin_in_refs = [p for p in b.aux
                   if b.rel(p).replace("\\", "/").startswith("references/")
                   and looks_binary(p.read_bytes())]
    if bin_in_refs:
        out.append(Finding(
            check="binary_under_references",
            category="file-placement", severity="low",
            title=f"{len(bin_in_refs)} binary file(s) under references/",
            why="Binary files land on disk from any directory (the reader raises "
                "SkillFileBinaryError and the harness materialises them), so these work -- but "
                "they sit in the directory whose contract is 'inline only', which is how the "
                "references-on-disk mistake spreads.",
            fix="Move binaries to assets/ so the directory names the behaviour.",
            skill=b.name, evidence=[Evidence(b.rel(p)) for p in bin_in_refs[:8]],
            effort="low", reference="references/file-layout.md#the-staging-matrix",
        ))

    # 1d. Read cap and staging cap.
    for p in b.aux:
        rel = b.rel(p)
        size = p.stat().st_size
        if size <= MAX_SKILL_FILE_BYTES:
            continue
        if is_script_rel(rel):
            out.append(Finding(
                check="script_over_read_cap", category="file-placement", severity="critical",
                title=f"{rel} is {size // 1024} KiB -- over the {MAX_SKILL_FILE_BYTES // 1024} KiB "
                      f"read cap, so it CANNOT be staged",
                why="read_skill_file refuses to stage a script whose source was truncated "
                    "(truncated source would fail at runtime). The tool call returns an error and "
                    "the script is unrunnable.",
                fix="Split the script, or move the bulk of its data into assets/ and read it at runtime.",
                skill=b.name, evidence=[Evidence(rel, None, f"{size} bytes")],
                effort="medium", reference="references/file-layout.md#size-caps",
            ))
        else:
            out.append(Finding(
                check="aux_over_read_cap", category="file-placement", severity="high",
                title=f"{rel} is {size // 1024} KiB -- silently truncated at "
                      f"{MAX_SKILL_FILE_BYTES // 1024} KiB when read",
                why="Upload accepts up to 50 MB combined, but every READ is capped at 256 KiB with "
                    "a truncation marker appended. A reference doc past the cap loses its tail, and "
                    "whatever rule lived down there is simply absent from the agent's context.",
                fix="Split the file, or move the data into assets/ and have a script read it "
                    "(scripts read from disk with no cap).",
                skill=b.name, evidence=[Evidence(rel, None, f"{size} bytes")],
                effort="low", reference="references/file-layout.md#size-caps",
            ))

    # 1e. Shipped junk.
    junk = [p for p in sorted(b.dir.rglob("*")) if p.is_file() and (
        "__pycache__" in p.parts or p.name == ".DS_Store" or p.suffix in {".pyc", ".zip", ".log"}
        or p.name.startswith("test_") or "/tests/" in str(p).replace("\\", "/")
    )]
    if junk:
        total = sum(p.stat().st_size for p in junk)
        out.append(Finding(
            check="non_runtime_files_shipped", category="hygiene", severity="low",
            title=f"{len(junk)} non-runtime file(s) ({total // 1024} KiB) in the bundle",
            why="Tests, caches and stale archives count against the 50 MB upload and the 100-aux "
                "limit, and show up in fetch_skill's aux manifest -- which is a menu the agent may "
                "decide to read from.",
            fix="Keep tests outside the uploaded skill directory.",
            skill=b.name, evidence=[Evidence(b.rel(p)) for p in junk[:10]],
            effort="low", reference="references/file-layout.md#ship-only-runtime-files",
        ))

    if len(b.aux) > MAX_AUX_FILES_PER_SKILL:
        out.append(Finding(
            check="aux_count_over_cap", category="file-placement", severity="high",
            title=f"{len(b.aux)} aux files -- over the {MAX_AUX_FILES_PER_SKILL} per-skill limit",
            why="Upload rejects the skill with 422.",
            fix="Consolidate, or split into two skills.",
            skill=b.name, effort="medium",
            reference="references/platform-contract.md#upload-caps",
        ))

    return out


# ---------------------------------------------------------------------------
# CHECK GROUP 2 -- discovery burn (the largest measured step sink)
# ---------------------------------------------------------------------------

CONTRACT_HINTS = ("run:", "reads:", "writes:", "shape:", "prints", "stdout", "exit ",
                  "output", "returns", "--help", "usage")
ERR_STR_RE = re.compile(r"""["'](?:error|why)["']\s*:\s*(?:f?["'])([^"'{}]{12,120})""")
EXIT_MSG_RE = re.compile(r"""sys\.exit\(\s*f?["']([^"']{12,120})["']""")


def check_discovery(b: SkillBundle) -> list[Finding]:
    out: list[Finding] = []
    scripts = [p for p in b.aux if is_script_rel(b.rel(p))]
    if not scripts:
        return out

    body_l = b.body.lower()
    lines = b.body.splitlines()

    # Only ENTRY POINTS need an invocation + output contract. A shared module is
    # imported, never invoked -- its requirement is being in the staging batch,
    # which is a separate check below.
    entry_points, modules = [], []
    for p in scripts:
        src = read_text(p)
        is_entry = bool(
            re.search(r'if\s+__name__\s*==\s*["\']__main__["\']', src)
            or "argparse" in src
            or "sys.argv" in src
        )
        (entry_points if is_entry else modules).append(p)

    lines_b = b.body.splitlines()
    unnamed: list[str] = []
    # Per script, WHICH contract parts are missing. Grading them together matters:
    # a script with no documented invocation almost never has a documented shape
    # either, and the old code `continue`d after recording the missing invocation --
    # so it never shape-checked exactly the scripts a real run reverse-engineered
    # most (one was re-read 33 times and showed up in no shape finding at all).
    incomplete: list[tuple[str, list[str]]] = []
    for p_ in entry_points:
        rel = b.rel(p_)
        stem = Path(rel).name
        if stem.lower() not in body_l:
            unnamed.append(rel)
            continue
        missing: list[str] = []
        if not re.search(rf"(?:python3?|sh)\s+[^\n`]*{re.escape(stem)}", b.body):
            missing.append("invocation")
        hit_idx = [i for i, l in enumerate(lines_b) if stem.lower() in l.lower()]
        shape_ok = exits_ok = False
        for i in hit_idx:
            window = "\n".join(lines_b[i:i + 45]).lower()
            if re.search(r"```json|\bprints?\b|\bstdout\b|\breturns?\b|"
                         r"output looks like|what the script prints", window):
                shape_ok = True
            if re.search(r"exit\s*(?:code)?\s*[0-9]|\bexits?\s+(?:non-?zero|0|1|2|3)\b|"
                         r"\brefus(?:e|es|al)\b", window):
                exits_ok = True
        if not shape_ok:
            missing.append("stdout shape")
        if not exits_ok:
            missing.append("exit codes")
        if missing:
            incomplete.append((rel, missing))

    if unnamed:
        out.append(Finding(
            check="script_not_named_in_skill_md", category="discovery", severity="high",
            title=f"{len(unnamed)} runnable script(s) never named in SKILL.md",
            why="A script the body doesn't name is a script the agent has to discover. Measured on "
                "real runs: 146 of 170 bash calls spent probing source, and one export turn spent "
                "27 bash calls grepping its own scripts then 47 inline `python3 -c` calls "
                "reimplementing them -- bypassing the validation that stops bad output.",
            fix="Name every runtime script in SKILL.md with its full contract, or delete it from "
                "the bundle.",
            skill=b.name, evidence=[Evidence(r) for r in unnamed[:10]],
            est_steps_saved=4 * len(unnamed), effort="low",
            reference="references/script-contracts.md#the-contract-block",
        ))

    if incomplete:
        worst = sorted(incomplete, key=lambda kv: -len(kv[1]))
        parts = Counter(m for _, ms in incomplete for m in ms)
        out.append(Finding(
            check="script_contract_incomplete", category="discovery", severity="high",
            title=f"{len(incomplete)} script(s) named but with an incomplete contract (missing: "
                  + ", ".join(f"{k} x{v}" for k, v in parts.most_common()) + ")",
            why="A script's SOURCE never returns to the model's context -- read_skill_file writes it "
                "to disk and returns one confirmation line. Meanwhile the platform tells the agent "
                "the contract IS SKILL.md and never to read .py source. If the contract is not "
                "there, the agent has been pointed at a document that cannot answer it, and reads "
                "the source anyway. On a real 4-turn run of a skill in this state, 125 of 185 bash "
                "calls went on reading or reimplementing its own scripts -- one re-read 33 times, "
                "another 16 -- and two turns hit the 100-step cap.",
            fix="Give each script the full block: Run (the literal command with every flag), Reads, "
                "Writes, Shape (the real stdout keys, success AND refusal), Exits, and 'safe to run "
                "alongside X' wherever true -- that last line is what tells the agent what it may "
                "batch. Start with the scripts missing the most.",
            skill=b.name,
            evidence=[Evidence(r, None, "missing: " + ", ".join(ms)) for r, ms in worst[:12]],
            est_steps_saved=sum(4 * len(ms) for _, ms in incomplete), effort="medium",
            reference="references/script-contracts.md#the-contract-block",
        ))

    # 2c. Refusal / error strings the body never mentions.
    missing_errs: list[Evidence] = []
    for p in scripts:
        src = read_text(p)
        strings = set(ERR_STR_RE.findall(src)) | set(EXIT_MSG_RE.findall(src))
        for s in strings:
            probe = s.strip().rstrip(".")[:40]
            if len(probe) >= 12 and probe.lower() not in body_l:
                missing_errs.append(Evidence(b.rel(p), None, probe))
    if missing_errs:
        out.append(Finding(
            check="refusals_not_documented", category="discovery", severity="high",
            title=f"{len(missing_errs)} script refusal/error message(s) not documented in SKILL.md",
            why="When a script refuses, the agent needs to know that the refusal is the answer and "
                "what to change. Undocumented, a refusal reads as a puzzle: one real turn spent "
                "steps 22-99 reverse-engineering scripts after a single refusal it could have "
                "escalated at step 50.",
            fix="Document every refusal shape as a table -- error string, what it means, the ONE "
                "next action -- and state that a refusal is fixed by changing the INPUT and "
                "re-running, never by rebuilding the script's work inline.",
            skill=b.name, evidence=missing_errs[:10],
            est_steps_saved=20, effort="low",
            reference="references/script-contracts.md#refusals-are-escalations-not-puzzles",
        ))

    # 2d. Shared module imported by scripts but absent from the staging instructions.
    script_names = {Path(b.rel(p)).name for p in scripts}
    stems = {n[:-3] for n in script_names if n.endswith(".py")}
    shared: dict[str, set[str]] = {}
    for p in scripts:
        src = read_text(p)
        for mod in re.findall(r"^\s*(?:from\s+([a-z_][a-z0-9_]*)\s+import|import\s+([a-z_][a-z0-9_]*))",
                              src, re.M):
            name = mod[0] or mod[1]
            if name in stems and f"{name}.py" != Path(b.rel(p)).name:
                # A file that does both `import common` and `from common import x`
                # is still ONE importer -- a set, not a list.
                shared.setdefault(f"{name}.py", set()).add(b.rel(p))
    for mod, importer_set in shared.items():
        importers = sorted(importer_set)
        if mod.lower() not in body_l:
            out.append(Finding(
                check="shared_module_not_in_staging_list", category="discovery", severity="critical",
                title=f"scripts/{mod} is imported by {len(importers)} script(s) but never named in SKILL.md",
                why="A sibling module is NOT co-staged by staging the script that imports it -- only "
                    "assets/ is. Without an explicit read_skill_file for the shared module, every "
                    "importing script dies on ImportError. A real run watched HS verification "
                    "'find 0', spent dozens of calls diagnosing it, re-staged the module and re-ran "
                    "everything -- which is how that turn ran out of tool iterations.",
                fix=f"Add scripts/{mod} to the explicit staging batch in SKILL.md, in the same step "
                    f"as the scripts that import it.",
                skill=b.name,
                evidence=[Evidence(r, None, f"imports {mod}") for r in importers[:8]],
                est_steps_saved=12, effort="low",
                reference="references/file-layout.md#sibling-modules-are-not-co-staged",
            ))

    return out


# ---------------------------------------------------------------------------
# CHECK GROUP 3 -- batching and the step floor
# ---------------------------------------------------------------------------

RSF_RE = re.compile(r"read_skill_file")
BATCH_LANG_RE = re.compile(
    r"\bone step\b|\bsingle step\b|\bsame step\b|\bsame (?:response|message)\b|"
    r"\btogether\b|\bin ONE\b|\bone call\b|\bat once\b|\bparallel\b|\bsimultaneous",
    re.I)
ONE_AT_A_TIME_BAN_RE = re.compile(
    r"\bnever\b[^.\n]{0,60}\bone (?:at a time|per step|file per step)\b|"
    r"\bdo not\b[^.\n]{0,60}\bone (?:at a time|per step)\b|"
    r"\bone at a time\b[^.\n]{0,40}\b(?:never|forbidden|banned)\b", re.I)
SCRIPT_RUN_RE = re.compile(r"python3?\s+[^\s`'\"]*?([A-Za-z0-9_]+\.py)")
# Batching PAYLOAD into a single call: "one insert for all seven rows", "ONE call
# for the batch", "a single db_insert_rows carrying every row".
WIDE_PAYLOAD_RE = re.compile(
    r"\b(?:one|single|1)\s+(?:\w+\s+){0,3}(?:call|insert|db_insert_rows|db_insert_rows_from_file|"
    r"ws_add|request)\b[^.\n]{0,60}\b(?:all|every|whole|entire|batch)\b|"
    r"\bONE\s+call\s+for\s+the\s+(?:batch|whole)\b|"
    r"\ball\s+(?:the\s+)?(?:rows|items|records|documents|attachments)\b[^.\n]{0,40}"
    r"\b(?:one|single)\s+(?:call|insert|request)\b",
    re.I)
# A stated width bound near payload batching: per-call ceilings, chunking, or the
# explicit "a truncated call means send less" rule.
WIDTH_BOUND_RE = re.compile(
    r"\bper\s+(?:call|step|insert|request)\b|\bat a time\b|\bchunk(?:s|ed|ing)?\b|"
    r"\brows per\b|\bno more than\s+\d+\b|<=\s*\d+|≤\s*\d+|\bbounded by payload\b|"
    r"\btruncated\b[^.\n]{0,40}\bsend less\b|\bsend less\b",
    re.I)
FETCH_SKILL_RE = re.compile(r"fetch_skill")
OUT_FLAG_RE = re.compile(r"--(?:out|output|output-dir|out-dir|o)[= ]+([^\s`'\"\\]+)")
ARG_TOKEN_RE = re.compile(r"--[a-z0-9-]+[= ]+([^\s`'\"\\]+)")


def _clusters(line_nos: list[int], gap: int = 6) -> list[list[int]]:
    if not line_nos:
        return []
    line_nos = sorted(line_nos)
    groups = [[line_nos[0]]]
    for n in line_nos[1:]:
        if n - groups[-1][-1] <= gap:
            groups[-1].append(n)
        else:
            groups.append([n])
    return groups


def check_batching(b: SkillBundle) -> tuple[list[Finding], dict]:
    out: list[Finding] = []
    lines = b.body.splitlines()

    rsf_lines = [i + 1 for i, l in enumerate(lines) if RSF_RE.search(l)]
    groups = _clusters(rsf_lines)
    staging_steps = len(groups)
    total_rsf = len(rsf_lines)

    has_batch_lang = bool(BATCH_LANG_RE.search(b.body))
    has_ban = bool(ONE_AT_A_TIME_BAN_RE.search(b.body))

    if total_rsf >= 2 and staging_steps >= 2:
        out.append(Finding(
            check="staging_scattered_across_sections", category="batching", severity="high",
            title=f"{total_rsf} read_skill_file mentions spread over {staging_steps} places in the body",
            why="Every assistant response is one step against the 100 regardless of how many tool "
                "calls it carries, and work-tool calls in one response fan out in parallel. Staging "
                "instructions written in the sections where each file happens to be needed get "
                "executed there -- one step per file. Consolidating them into a single named batch "
                f"turns {total_rsf} steps into {max(1, -(-total_rsf // GETMANY_CONCURRENCY))}.",
            fix="Put ONE explicit staging block near the top of the body listing every file the "
                "turn will need, and say it must be issued as a single step. Keep each batch to "
                f"{GETMANY_CONCURRENCY} files or fewer -- the sandbox transfer helper runs "
                f"{GETMANY_CONCURRENCY} concurrent slots, so a 6th file queues behind them.",
            skill=b.name,
            evidence=[Evidence("SKILL.md", g[0], lines[g[0] - 1].strip()[:160]) for g in groups[:8]],
            est_steps_saved=max(0, total_rsf - max(1, -(-total_rsf // GETMANY_CONCURRENCY))),
            effort="low", reference="references/step-budget.md#stage-once-in-one-step",
        ))

    if total_rsf >= 2 and not has_batch_lang:
        out.append(Finding(
            check="no_explicit_batching_instruction", category="batching", severity="high",
            title="Multiple staged files with no instruction to issue them in one step",
            why="The harness already tells the agent to batch independent calls, but a skill that "
                "lists files one per bullet reads as a sequence and is executed as one. A run spent "
                "98 of 100 steps issuing calls one at a time despite the platform prompt already "
                "stating the batched form.",
            fix="State the batch explicitly and show it as one block of calls.",
            skill=b.name, est_steps_saved=max(0, total_rsf - 1), effort="low",
            reference="references/step-budget.md#stage-once-in-one-step",
        ))

    if total_rsf >= 2 and not has_ban:
        out.append(Finding(
            check="batching_ceiling_without_prohibition", category="batching", severity="medium",
            title="Batching is described as a ceiling but the one-at-a-time default is not forbidden",
            why="Models pattern-match into busywork under uncertainty unless the wrong default is "
                "explicitly forbidden, not merely bounded. Every batching instruction that was "
                "stated as a number alone was violated in production; pairing it with an explicit "
                "prohibition is what made it hold.",
            fix="Pair each ceiling with the prohibition: 'never issue these one at a time across "
                "separate steps'.",
            skill=b.name, est_steps_saved=None, effort="low",
            reference="references/step-budget.md#a-ceiling-needs-a-prohibition",
        ))

    if total_rsf > GETMANY_CONCURRENCY:
        big = [g for g in groups if len(g) > GETMANY_CONCURRENCY]
        if big:
            out.append(Finding(
                check="staging_batch_over_concurrency", category="batching", severity="low",
                title=f"A staging batch names more than {GETMANY_CONCURRENCY} files",
                why=f"Batching works, but the sandbox file-transfer helper defaults to "
                    f"{GETMANY_CONCURRENCY} concurrent operations. A larger batch does not fail -- "
                    f"the excess queues behind the same slots, so the step's wall-clock is paced by "
                    f"the queue rather than the slowest file.",
                fix=f"Split into batches of {GETMANY_CONCURRENCY}: one step of 5 plus one step of "
                    f"the remainder beats one step of 8.",
                skill=b.name, effort="low",
                reference="references/step-budget.md#the-five-slot-ceiling",
            ))

    # Batching PAYLOAD into one call is a different thing from batching CALLS, and
    # it fails differently: on stop_reason=max_tokens the harness deliberately does
    # NOT dispatch the half-formed call -- it runs a continuation step. So an
    # over-wide call costs a step AND writes nothing.
    wide = [(i + 1, l) for i, l in enumerate(lines) if WIDE_PAYLOAD_RE.search(l)]
    if wide:
        bounded = bool(WIDTH_BOUND_RE.search(b.body))
        if not bounded:
            out.append(Finding(
                check="payload_batched_without_width_bound", category="batching", severity="high",
                title=f"{len(wide)} instruction(s) put a whole batch into ONE tool call with no "
                      f"width bound",
                why="Batching independent CALLS into one step is free. Batching many items into one "
                    "call's ARGUMENTS is not: the arguments are output tokens, and when the response "
                    "is truncated the harness reports stop_reason=max_tokens and runs a continuation "
                    "step INSTEAD of dispatching the half-formed call. Nothing is written and the "
                    "step is spent. Real incident: 7 rows with all their documents in one "
                    "db_insert_rows was truncated mid-string, the turn errored after 4.5 minutes and "
                    "NOTHING was written -- seven separate inserts would have cost six more steps "
                    "and finished cleanly.",
                fix="Bound the width, not just the step count: state a maximum number of items per "
                    "call (e.g. one insert per row, or <=25 ws_add per step) and say plainly that a "
                    "truncated call means SEND LESS, never send it again. Note the tool ceilings "
                    f"are separate limits, not targets: db_insert_rows accepts {DB_INSERT_MAX_ROWS} "
                    "rows and db_insert_rows_from_file reads 5000 from a file the script wrote -- "
                    "prefer the file form for script-produced bulk, since the rows never pass "
                    "through the model at all.",
                skill=b.name,
                evidence=[Evidence("SKILL.md", b.body_line_no(ln - 1), t.strip()[:160])
                          for ln, t in wide[:6]],
                est_steps_saved=None, effort="low",
                reference="references/step-budget.md#calls-batch-payloads-do-not",
            ))

    # Fixed script chains with no wrapper.
    run_hits: list[tuple[int, str, str]] = []
    for i, l in enumerate(lines):
        for mm in SCRIPT_RUN_RE.finditer(l):
            run_hits.append((i + 1, mm.group(1), l.strip()))
    wrappers = _detect_wrappers(b)
    chains = _detect_chains(run_hits)
    script_run_steps = len({s for _, s, _ in run_hits})

    for chain in chains:
        names = [c[0] for c in chain]
        if any(set(names) <= set(cov) for cov in wrappers.values()):
            continue
        out.append(Finding(
            check="fixed_chain_without_wrapper", category="batching", severity="medium",
            title=f"Fixed chain of {len(names)} scripts with no wrapper: {' -> '.join(names)}",
            why="These run in a fixed order and one consumes what the previous wrote, so they "
                "cannot be batched -- but they can be collapsed. Each separate invocation is a full "
                "model round trip. A wrapper that shells out to the same scripts with the same "
                "arguments turns N round trips into one without changing any logic.",
            fix="Add a run_<step>.py wrapper that invokes them in the fixed order, and keep the "
                "underlying scripts independently runnable for re-running just one.",
            skill=b.name,
            evidence=[Evidence("SKILL.md", ln, txt[:160]) for _, ln, txt in
                      [(n, ln, txt) for n, ln, txt in [(c[0], c[1], c[2]) for c in chain]]][:6],
            est_steps_saved=len(names) - 1, effort="medium",
            reference="references/step-budget.md#collapse-fixed-chains",
        ))

    # Re-fetching its own SKILL.md.
    fs = find_lines(b.body, FETCH_SKILL_RE, b.body_offset)
    self_fetch = [(ln, t) for ln, t in fs if b.name in t]
    if self_fetch:
        out.append(Finding(
            check="skill_tells_agent_to_refetch_itself", category="batching", severity="medium",
            title="Body instructs the agent to fetch_skill its own SKILL.md",
            why="The body is already in context by the time it is being followed. Re-fetching costs "
                "a step and re-injects the whole body, accelerating the context growth that drives "
                "compaction (clearing at 60% of the window, compaction at 80%).",
            fix="Remove the self-fetch. If the concern is surviving compaction, put the invariants "
                "in the process block's agent_instructions -- those are injected every turn at no "
                "step cost.",
            skill=b.name,
            evidence=[Evidence("SKILL.md", ln, t[:160]) for ln, t in self_fetch[:4]],
            est_steps_saved=len(self_fetch), effort="low",
            reference="references/step-budget.md#never-re-fetch-the-skill",
        ))

    measurements = {
        "read_skill_file_mentions": total_rsf,
        "staging_clusters": staging_steps,
        "distinct_scripts_invoked": script_run_steps,
        "wrappers_detected": {k: sorted(v) for k, v in wrappers.items()},
        "fixed_chains": [[c[0] for c in ch] for ch in chains],
        "has_explicit_batching_language": has_batch_lang,
        "has_one_at_a_time_prohibition": has_ban,
        "estimated_step_floor": staging_steps + script_run_steps,
    }
    return out, measurements


def _detect_wrappers(b: SkillBundle) -> dict[str, list[str]]:
    """script -> the sibling scripts it shells out to."""
    wrappers: dict[str, list[str]] = {}
    scripts = [p for p in b.aux if is_script_rel(b.rel(p))]
    names = {Path(b.rel(p)).name for p in scripts}
    for p in scripts:
        src = read_text(p)
        if "subprocess" not in src:
            continue
        covered = sorted(n for n in names if n != Path(b.rel(p)).name and n in src)
        if covered:
            wrappers[Path(b.rel(p)).name] = covered
    return wrappers


def _detect_chains(run_hits: list[tuple[int, str, str]]) -> list[list[tuple[str, int, str]]]:
    """Consecutive prescribed invocations where one's --out value is another's argument."""
    chains: list[list[tuple[str, int, str]]] = []
    cur: list[tuple[str, int, str]] = []
    prev_outs: set[str] = set()
    for ln, script, text in run_hits:
        outs = {Path(v).name for v in OUT_FLAG_RE.findall(text)}
        args = {Path(v).name for v in ARG_TOKEN_RE.findall(text)}
        linked = bool(prev_outs & args)
        if linked and cur:
            cur.append((script, ln, text))
        else:
            if len(cur) >= 2:
                chains.append(cur)
            cur = [(script, ln, text)]
        prev_outs = outs or prev_outs
    if len(cur) >= 2:
        chains.append(cur)
    return [c for c in chains if len({s for s, _, _ in c}) >= 2]


# ---------------------------------------------------------------------------
# CHECK GROUP 4 -- data routed through the model
# ---------------------------------------------------------------------------

def check_data_through_model(b: SkillBundle) -> list[Finding]:
    out: list[Finding] = []
    body = b.body
    body_l = body.lower()

    if "db_query" in body_l and "save_to_file" not in body_l:
        out.append(Finding(
            check="db_query_without_save_to_file", category="data-through-model", severity="high",
            title="db_query prescribed with no `save_to_file: true`",
            why="Without the flag a small-enough result comes back INLINE and the model has to "
                "retype every row into the next tool call (~1 minute and ~8k output tokens per 50 "
                "rows), and it physically cannot re-emit a large one. With the flag the rows land "
                "at /workspace/db/query_<id>.csv at any size and the script reads the path.",
            fix="Add `save_to_file: true` wherever a script consumes the rows. Omit it only when "
                "the MODEL itself must reason over them.",
            skill=b.name,
            evidence=[Evidence("SKILL.md", ln, t) for ln, t in
                      find_lines(body, re.compile("db_query"), b.body_offset, 4)],
            est_steps_saved=None, effort="low",
            reference="references/step-budget.md#route-pointers-not-payloads",
        ))

    if "render_ui" in body_l and "file_ref" not in body_l:
        out.append(Finding(
            check="render_ui_without_file_ref", category="data-through-model", severity="medium",
            title="render_ui prescribed with no file_ref path",
            why="A component payload emitted inline is bounded by the output-token budget and gets "
                "silently shipped as a subset when it doesn't fit. One run stopped at max_tokens "
                "(49,100 output tokens) mid-handoff on a 13-item batch; real traffic on that stream "
                "reaches 40 items.",
            fix="Have the script that computes the data WRITE the payload JSON, then render with "
                "`{type, file_ref: '/workspace/<file>.json'}`. Tell the agent that on a validation "
                "error it fixes the FILE and re-renders with the same file_ref -- never falls back "
                "to a partial inline payload.",
            skill=b.name, effort="medium",
            reference="references/step-budget.md#route-pointers-not-payloads",
        ))

    if re.search(r"\bpipe\b|\|\s*python|stdout\s+(?:into|to)\b", body_l):
        out.append(Finding(
            check="stdout_piping_between_scripts", category="data-through-model", severity="medium",
            title="Body appears to chain scripts by piping stdout",
            why="Piping a script's JSON into the next puts whole documents through the model's "
                "context on the way. Chaining by FILE keeps the payload on disk and only the path "
                "in context.",
            fix="Each script writes its own --output file; the next reads that path.",
            skill=b.name,
            evidence=[Evidence("SKILL.md", ln, t) for ln, t in
                      find_lines(body, re.compile(r"\bpipe\b|\|\s*python", re.I), b.body_offset, 4)],
            effort="low", reference="references/step-budget.md#chain-by-file-not-stdout",
        ))

    # Scripts that dump their whole payload to stdout.
    dump_ev, indent_ev = [], []
    for p in (q for q in b.aux if is_script_rel(b.rel(q))):
        src = read_text(p)
        for i, line in enumerate(src.splitlines()):
            if re.search(r"print\(\s*json\.dumps\(", line) and "indent" in line:
                dump_ev.append(Evidence(b.rel(p), i + 1, line.strip()[:160]))
            if re.search(r"json\.dump\([^)]*indent\s*=\s*[1-9]", line):
                indent_ev.append(Evidence(b.rel(p), i + 1, line.strip()[:160]))
    if dump_ev:
        out.append(Finding(
            check="script_prints_full_payload", category="data-through-model", severity="high",
            title=f"{len(dump_ev)} script location(s) pretty-print a full JSON payload to stdout",
            why=f"bash stdout returned to the model is capped at {BASH_OUTPUT_MAX_CHARS // 1000}KB and "
                "truncated in the MIDDLE, so a large result arrives cut -- and the agent then probes "
                "the output file one `python3 -c` per fact to recover what it needed.",
            fix="Write the full object to --out, and print only a compact summary: ok/state, counts, "
                "a bounded failures list, next_action, and the out path.",
            skill=b.name, evidence=dump_ev[:8], est_steps_saved=8, effort="low",
            reference="references/script-contracts.md#print-a-summary-write-the-payload",
        ))
    if indent_ev:
        out.append(Finding(
            check="indented_json_written_to_file", category="data-through-model", severity="low",
            title=f"{len(indent_ev)} location(s) write indented JSON to a file",
            why="Indentation roughly doubles the bytes for a file no human reads, and those bytes "
                "count against the read cap if the agent ever reads it back.",
            fix="Drop indent= on machine-read files.",
            skill=b.name, evidence=indent_ev[:6], effort="low",
            reference="references/script-contracts.md#print-a-summary-write-the-payload",
        ))

    return out


# ---------------------------------------------------------------------------
# CHECK GROUP 5 -- duplicating what the harness already injects
# ---------------------------------------------------------------------------

def check_duplication(b: SkillBundle) -> tuple[list[Finding], dict]:
    hits: list[Evidence] = []
    topics: list[str] = []
    lines = b.body.splitlines()
    for pat, says, where in ALREADY_ENFORCED:
        found = [(i + 1, l) for i, l in enumerate(lines) if pat.search(l)]
        if found:
            topics.append(says)
            for ln, l in found[:3]:
                hits.append(Evidence("SKILL.md", b.body_line_no(ln - 1), l.strip()[:160]))
    out: list[Finding] = []
    if len(topics) >= 3:
        out.append(Finding(
            check="restates_platform_guidance", category="duplication", severity="medium",
            title=f"Body restates {len(topics)} rule(s) the harness already injects every turn",
            why="The harness injects the batching rules, the parallel-calls rule, "
                "one-script-beats-many, the bash size rule, never-read-.py-source, the whole "
                "builder/escalation ruleset, the process step lifecycle and gate rule, the "
                "no-ASCII-tracker rule and the file_ref rule into the system prompt on EVERY turn "
                "-- and the lifecycle-batching rule now sits in the tool descriptions themselves. "
                "Restating them costs context tokens on every step and can only weaken them by "
                "paraphrase. A skill body should carry DOMAIN logic only.",
            fix="Delete the restatements. Keep only where your domain genuinely narrows a platform "
                "rule (a specific batch, a specific dependency that must not be collapsed).",
            skill=b.name, evidence=hits[:12],
            est_steps_saved=None, effort="low",
            reference="references/already-enforced.md",
        ))
    return out, {"restated_topics": topics, "restated_hits": len(hits)}


# ---------------------------------------------------------------------------
# CHECK GROUP 6 -- frontmatter and contract validity (cheap pre-pass)
# ---------------------------------------------------------------------------

QUOTED_BOOL_RE = re.compile(r"^\s*(datastore_write|datastore_delete|featured)\s*:\s*(['\"].*?['\"]|[01])\s*$",
                            re.M)
NESTED_UNQUOTED_COLON_RE = re.compile(r"^\s+[A-Za-z_][\w-]*\s*:\s+(?![\"'\[{|>&*])(?:[^\n#]*?\S:\s)", re.M)


def check_frontmatter(b: SkillBundle) -> list[Finding]:
    out: list[Finding] = []

    if b.meta_error and not b.meta:
        ev = [Evidence("SKILL.md", 1, b.meta_error)]
        for m in NESTED_UNQUOTED_COLON_RE.finditer(b.frontmatter_text):
            ln = b.frontmatter_text[:m.start()].count("\n") + 2
            ev.append(Evidence("SKILL.md", ln, m.group(0).strip()[:160]))
        out.append(Finding(
            check="frontmatter_not_strict_yaml", category="validity", severity="critical",
            title="Frontmatter does not parse as strict YAML",
            why="_parse_skill_md tries yaml.safe_load first; on failure it falls back to a regex "
                "that recovers ONLY name and description. The entire process: block, keywords, "
                "featured, genui_schemas and every capability flag are silently dropped -- upload "
                "still returns 200, so nothing tells you. The usual trigger is an unquoted colon "
                "in a nested value such as a step `sub:` or `title:`.",
            fix="Quote every value containing a colon, then re-run this audit. Verify after upload "
                "with GET /skills/{name} -- a 200 on upload does not mean the frontmatter parsed.",
            skill=b.name, evidence=ev[:8], effort="low",
            reference="references/platform-contract.md#frontmatter-is-strict-yaml-or-nothing",
        ))
        return out

    meta = b.meta

    for m in QUOTED_BOOL_RE.finditer(b.frontmatter_text):
        key, val = m.group(1), m.group(2)
        ln = b.frontmatter_text[:m.start()].count("\n") + 2
        if key in ("datastore_write", "datastore_delete"):
            out.append(Finding(
                check="capability_flag_not_literal_boolean", category="validity", severity="critical",
                title=f"`{key}: {val}` is not a literal YAML boolean -- the capability silently no-ops",
                why=f"The harness reads `meta.get('{key}') is True` -- an identity check against the "
                    "Python bool. A quoted string or a number is fail-closed, so the write/delete "
                    "tool is simply never offered. Upload succeeds and the skill looks correct.",
                fix=f"Write `{key}: true` unquoted.",
                skill=b.name, evidence=[Evidence("SKILL.md", ln, m.group(0).strip())],
                effort="low", reference="references/platform-contract.md#capability-flags",
            ))
        else:
            out.append(Finding(
                check="featured_is_truthy_string", category="validity", severity="medium",
                title=f"`featured: {val}` -- any non-empty string is truthy here",
                why="featured is read as `bool(meta.get('featured', False))`, so `featured: \"false\"` "
                    "evaluates TRUE and pins the skill in the inline menu.",
                fix="Write `featured: true` or `featured: false` unquoted, or omit the key.",
                skill=b.name, evidence=[Evidence("SKILL.md", ln, m.group(0).strip())],
                effort="low", reference="references/platform-contract.md#capability-flags",
            ))

    name = str(meta.get("name") or "")
    if not name:
        out.append(Finding(
            check="missing_name", category="validity", severity="critical",
            title="Frontmatter has no `name`", why="A skill with no name is dropped from the catalog.",
            fix="Add a slug name.", skill=b.name, effort="low",
            reference="references/platform-contract.md#frontmatter",
        ))
    elif not SLUG_RE.match(name):
        out.append(Finding(
            check="name_not_a_slug", category="validity", severity="critical",
            title=f"`name: {name}` fails the slug regex ^[a-z0-9][a-z0-9-]*$",
            why="Upload is slug-validated and rejects it.",
            fix="Lowercase, digits and hyphens only.", skill=b.name, effort="low",
            reference="references/platform-contract.md#frontmatter",
        ))
    if name and name != b.dir.name:
        out.append(Finding(
            check="name_differs_from_directory", category="validity", severity="info",
            title=f"frontmatter name `{name}` differs from directory `{b.dir.name}`",
            why="Legal inside a plugin -- storage keys come from the directory while the "
                "LLM-facing handle is the frontmatter name -- but it means read_skill_file calls "
                "must use the frontmatter name, which is a routine source of confusion.",
            fix="Make them match unless you have a reason not to.", skill=b.name, effort="low",
            reference="references/platform-contract.md#frontmatter",
        ))

    desc = str(meta.get("description") or "").strip()
    if not desc:
        out.append(Finding(
            check="missing_description", category="validity", severity="high",
            title="No `description`",
            why="description is the catalog entry AND the discovery signal the model matches on; "
                "the tagline is derived from its first 8 words. Without it the skill is effectively "
                "undiscoverable.",
            fix="One sentence: what it does, and when it should trigger.", skill=b.name,
            effort="low", reference="references/platform-contract.md#frontmatter",
        ))

    kw = meta.get("keywords")
    if kw is not None and not isinstance(kw, list):
        out.append(Finding(
            check="keywords_not_a_list", category="validity", severity="medium",
            title="`keywords` is not a YAML list -- it is dropped entirely",
            why="Only a list is read; a comma-separated string is discarded. keywords also act as "
                "the fallback phrase_hints for a process skill, so losing them can stop "
                "start_process from ever matching.",
            fix="Use a YAML list: [a, b, c].", skill=b.name, effort="low",
            reference="references/platform-contract.md#frontmatter",
        ))

    for key, effect in SILENT_NOOP_KEYS.items():
        if key in meta:
            out.append(Finding(
                check="silent_noop_frontmatter_key", category="validity", severity="low",
                title=f"`{key}:` is not read by this harness",
                why=effect, fix=f"Remove `{key}` or move the intent somewhere the harness reads.",
                skill=b.name, effort="low",
                reference="references/platform-contract.md#silent-no-ops",
            ))

    if "genui_schemas" in meta:
        out.append(Finding(
            check="genui_schemas_has_no_runtime_effect", category="validity", severity="info",
            title="`genui_schemas` is parsed into the catalog but no consumer reads it",
            why="GENUI_FULL_UNION is True, so the full render_ui union ships every turn regardless. "
                "Declaring it is harmless documentation; it is not access control and it will not "
                "put a component in reach that isn't already there.",
            fix="Keep it for documentation if you like, but don't rely on it.", skill=b.name,
            effort="low", reference="references/process-and-hitl.md#genui-scoping",
        ))

    ot = meta.get("output_type")
    if ot is not None and ot not in ("text", "structured"):
        out.append(Finding(
            check="invalid_output_type", category="validity", severity="high",
            title=f"`output_type: {ot}` is not a recognised value -- it degrades silently to `text`",
            why="An unknown value is accepted at upload and quietly becomes text, so the skill is "
                "no longer pipeline-mappable and nothing reports it.",
            fix="Use `structured` with a valid object output_schema, or omit the key.",
            skill=b.name, effort="low",
            reference="references/platform-contract.md#output-contract",
        ))
    if ot == "structured":
        schema = meta.get("output_schema")
        ok = isinstance(schema, dict) and schema.get("type") == "object" and isinstance(
            schema.get("properties"), dict)
        if not ok:
            out.append(Finding(
                check="structured_without_valid_schema", category="validity", severity="high",
                title="`output_type: structured` without a valid object `output_schema`",
                why="The contract degrades silently to text, so finalize_result is never offered "
                    "and the skill cannot be mapped by a pipeline.",
                fix="Provide output_schema as {type: object, properties: {...}}; include a required "
                    "`summary` string for the human-readable line.",
                skill=b.name, effort="medium",
                reference="references/platform-contract.md#output-contract",
            ))

    if meta.get("write_tables") is not None and meta.get("datastore_write") is not True:
        out.append(Finding(
            check="write_tables_without_flag", category="validity", severity="high",
            title="`write_tables` set but `datastore_write: true` is not",
            why="write_tables is ignored entirely unless the flag is truly enabled, so no write "
                "tool is offered at all.",
            fix="Add `datastore_write: true`, or drop write_tables.", skill=b.name, effort="low",
            reference="references/platform-contract.md#capability-flags",
        ))
    if meta.get("datastore_write") is True and not meta.get("write_tables"):
        out.append(Finding(
            check="write_flag_without_table_allowlist", category="validity", severity="medium",
            title="`datastore_write: true` with no `write_tables` -- writes are UNRESTRICTED",
            why="Omitting the list does not mean 'none', it means every write-eligible table for "
                "the workstream. And the effective per-turn allowlist is the UNION across all "
                "active write skills, so one unrestricted skill makes the whole turn unrestricted.",
            fix="Name the tables this skill may append to.", skill=b.name, effort="low",
            reference="references/platform-contract.md#capability-flags",
        ))

    return out


# ---------------------------------------------------------------------------
# CHECK GROUP 7 -- process block, gates and HITL
# ---------------------------------------------------------------------------

def check_process(b: SkillBundle) -> list[Finding]:
    out: list[Finding] = []
    proc = b.meta.get("process")
    body_l = b.body.lower()

    if not isinstance(proc, dict) or not proc:
        if "update_process_step" in body_l or "render_builder" in body_l:
            gated = "gate" in body_l or "approval" in body_l
            # A skill that is ONE step of an orchestrating Agent's process legitimately
            # has no block of its own. Don't nag when the body already says so.
            deliberate = bool(re.search(
                r"no\s+`?process:?`?\s+block\s+by\s+design|"
                r"process\s+(?:lives|is owned)\s+(?:in|by)\s+the\s+(?:agent|orchestrat)|"
                r"gate\s+lives\s+in\s+the\s+orchestrating", body_l))
            if gated and not deliberate:
                out.append(Finding(
                    check="hitl_without_process_block", category="process", severity="medium",
                    title="Body relies on human approval but the skill carries no `process:` block",
                    why="Server-side gate enforcement comes from a process step's `gate`. Without a "
                        "process block there is no gate, so 'wait for the human' is advisory prose "
                        "only -- and prose guards get skipped. There may be a good reason (the "
                        "orchestrating Agent owns the process and this skill is one step of it), in "
                        "which case say so in the body so the next reader doesn't 'fix' it.",
                    fix="Either add a process: block with a gated step, or state explicitly that "
                        "the gate lives in the orchestrating process.",
                    skill=b.name, effort="medium",
                    reference="references/process-and-hitl.md#gates-are-the-only-real-hitl",
                ))
        return out

    steps = proc.get("steps")
    if not isinstance(steps, list) or not steps:
        out.append(Finding(
            check="process_without_steps", category="process", severity="critical",
            title="`process:` block has no steps list", why="An empty steps list is rejected.",
            fix=f"Provide {PROCESS_STEPS_MIN}-{PROCESS_STEPS_MAX} ordered steps, each with id and title.",
            skill=b.name, effort="medium", reference="references/process-and-hitl.md#the-process-block",
        ))
        return out

    if not (PROCESS_STEPS_MIN <= len(steps) <= PROCESS_STEPS_MAX):
        out.append(Finding(
            check="process_step_count_out_of_range", category="process", severity="critical",
            title=f"{len(steps)} steps -- outside the allowed {PROCESS_STEPS_MIN}-{PROCESS_STEPS_MAX}",
            why="Derivation rejects the template, so the skill never appears in the process catalog.",
            fix="Merge or split steps to fit.", skill=b.name, effort="medium",
            reference="references/process-and-hitl.md#the-process-block",
        ))

    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not s.get("id") or not s.get("title"):
            out.append(Finding(
                check="process_step_missing_id_or_title", category="process", severity="critical",
                title=f"step[{i}] is missing `id` or `title`", why="Both are required.",
                fix="Add them.", skill=b.name,
                evidence=[Evidence("SKILL.md", None, json.dumps(s)[:160])], effort="low",
                reference="references/process-and-hitl.md#the-process-block",
            ))
            continue
        g = s.get("gate")
        if g is not None:
            if not isinstance(g, dict) or g.get("type") != "approval":
                out.append(Finding(
                    check="malformed_gate", category="process", severity="high",
                    title=f"step `{s.get('id')}` has a gate that is not {{type: approval, ...}}",
                    why="Only an approval gate is enforced server-side; anything else is inert, so "
                        "the human step becomes skippable.",
                    fix="Use `gate: { type: approval, role: <Role> }`.", skill=b.name,
                    evidence=[Evidence("SKILL.md", None, json.dumps(g)[:160])], effort="low",
                    reference="references/process-and-hitl.md#gates-are-the-only-real-hitl",
                ))
            elif s.get("assignee_role") != "human":
                out.append(Finding(
                    check="gated_step_not_assigned_to_human", category="process", severity="medium",
                    title=f"step `{s.get('id')}` is gated but assignee_role is not `human`",
                    why="The gate still enforces, but the stepper shows the step as the agent's, "
                        "which misleads whoever is meant to act on it.",
                    fix="Set `assignee_role: human` and an `assignee_hint` role label.",
                    skill=b.name, effort="low",
                    reference="references/process-and-hitl.md#gates-are-the-only-real-hitl",
                ))
        ar = s.get("assignee_role")
        if ar is not None and ar not in ("agent", "human"):
            out.append(Finding(
                check="invalid_assignee_role", category="process", severity="medium",
                title=f"step `{s.get('id')}` has assignee_role `{ar}` (must be agent|human)",
                why="An unrecognised value is not the enum the model expects.", fix="Use agent or human.",
                skill=b.name, effort="low", reference="references/process-and-hitl.md#the-process-block",
            ))

    gated = [s for s in steps if isinstance(s, dict) and isinstance(s.get("gate"), dict)]
    if not gated and ("review" in body_l or "approve" in body_l or "reviewer" in body_l):
        out.append(Finding(
            check="process_has_no_gated_step", category="process", severity="medium",
            title="Process talks about review/approval but declares no gated step",
            why="Without a gate the agent can walk straight past the human. The gate is the only "
                "mechanism that physically prevents it -- apply_step_update refuses to move a gated "
                "step to done.",
            fix="Add `gate: { type: approval, role: ... }` to the step a human must sign off.",
            skill=b.name, effort="low",
            reference="references/process-and-hitl.md#gates-are-the-only-real-hitl",
        ))

    arts = proc.get("artifacts")
    needs_files = any(w in body_l for w in ("upload", "attach", "source document",
                                           "source file", "files"))
    if needs_files and not arts:
        out.append(Finding(
            check="no_artifact_slots", category="process", severity="medium",
            title="Process needs source files but declares no `artifacts` slots",
            why="Artifact slots are what make the harness raise a file-upload escalation with one "
                "file_upload field per slot instead of the agent asking in prose or improvising "
                "demo data.",
            fix="Declare one slot per real input/output with its accepted extensions.",
            skill=b.name, effort="low", reference="references/process-and-hitl.md#artifacts-drive-file-intake",
        ))

    for key in ("genui",):
        names = proc.get(key)
        if isinstance(names, list):
            bad = [n for n in names if isinstance(n, str) and n not in GENUI_SCHEMAS]
            emitted = [n for n in names if n in GENUI_HARNESS_EMITTED_ONLY]
            if bad:
                out.append(Finding(
                    check="unknown_genui_component", category="process", severity="low",
                    title=f"process.{key} names {len(bad)} component(s) that do not exist: {', '.join(bad[:5])}",
                    why="Not an upload error, but it tells the agent a component is available when "
                        "render_ui will reject that type outright.",
                    fix="Use a name from the shipped schema set.", skill=b.name, effort="low",
                    reference="references/process-and-hitl.md#genui-scoping",
                ))
            if emitted:
                out.append(Finding(
                    check="harness_emitted_component_declared", category="process", severity="medium",
                    title=f"process.{key} names harness-emitted-only component(s): {', '.join(emitted)}",
                    why="tabby-auth and skill-replay are withheld from the model's render_ui union "
                        "on purpose -- the harness emits them itself with authoritative data. A "
                        "model-authored one carries no valid session id or approval fingerprint.",
                    fix="Remove them.", skill=b.name, effort="low",
                    reference="references/process-and-hitl.md#genui-scoping",
                ))

    if not proc.get("phrase_hints") and not b.meta.get("keywords"):
        out.append(Finding(
            check="no_phrase_hints_or_keywords", category="process", severity="medium",
            title="Process has neither `phrase_hints` nor `keywords`",
            why="phrase_hints are what the model matches to decide to call start_process, and they "
                "fall back to keywords when absent. With neither, the process is in the catalog but "
                "effectively untriggerable by phrasing.",
            fix="Add phrase_hints in the words a user would actually type.", skill=b.name,
            effort="low", reference="references/process-and-hitl.md#the-process-block",
        ))

    # agent_instructions: the zero-step injection channel.
    ai = str(proc.get("agent_instructions") or "").strip()
    invariant_lang = re.search(
        r"\bon turn 1\b|\bevery turn\b|\balways\b[^.\n]{0,40}\bfirst\b|\bself-?orient\b|\bresume\b",
        b.body, re.I)
    if not ai and invariant_lang:
        out.append(Finding(
            check="invariants_in_body_not_agent_instructions", category="budget", severity="medium",
            title="Turn-level invariants live in the body, where they cost a fetch_skill to load",
            why="For a BOUND process run, `process.agent_instructions` is injected into the system "
                "prompt on every turn under 'Template instructions:' -- no tool call, no step. The "
                "same text in the body is only in context after a fetch_skill, and is the first "
                "thing lost to compaction. This is the cheapest step saving available to a process "
                "skill and it is almost never used.",
            fix="Move the per-turn invariants (what to do first, how to resume, what must never "
                "happen) into agent_instructions. Keep the detailed domain procedure in the body. "
                "Note the trade: agent_instructions costs context tokens on every turn, so keep it "
                "to invariants, not the whole procedure.",
            skill=b.name,
            evidence=[Evidence("SKILL.md", b.body_line_no(b.body[:invariant_lang.start()].count("\n")),
                               invariant_lang.group(0))],
            est_steps_saved=1, effort="medium",
            reference="references/step-budget.md#agent-instructions-is-free-context",
        ))

    return out


# ---------------------------------------------------------------------------
# CHECK GROUP 8 -- state, resume and stop conditions
# ---------------------------------------------------------------------------

def check_state_and_stop(b: SkillBundle) -> list[Finding]:
    out: list[Finding] = []
    body_l = b.body.lower()

    produces = "save_output" in body_l or "deliverable" in body_l
    stop_lang = re.search(
        r"\bstop there\b|\bdo not (?:re-?verify|re-?check|re-?read|re-?run)\b|"
        r"\bis done the moment\b|\bfinish(?:ed)? line\b|\bnever re-?verify\b|"
        r"\bdo not (?:diff|unzip|re-?open) the (?:output|deliverable)\b", body_l)
    if produces and not stop_lang:
        out.append(Finding(
            check="no_stop_condition", category="budget", severity="high",
            title="Skill produces a deliverable but never states when it is finished",
            why="Documenting what a script returns removes the REASON to go digging; it says nothing "
                "about when to STOP. A real turn had a valid deliverable at step 38 of 100, then "
                "spent the remaining 62 steps checking its own work -- unzipping the output, "
                "re-summing totals, re-running the export twice into /tmp to diff against the real "
                "one -- and died at max_tool_iterations. Nothing after step 38 changed the output.",
            fix="State the finish line explicitly: a green result from a script that REFUSES rather "
                "than emit bad output is trustworthy, and reproducing it cannot add information. "
                "Name the one call that ends the step and forbid re-deriving the deliverable.",
            skill=b.name, est_steps_saved=30, effort="low",
            reference="references/step-budget.md#state-the-finish-line",
        ))

    if "save_output" in body_l and "ws_add" not in body_l:
        out.append(Finding(
            check="save_output_only_no_ws_add", category="state", severity="medium",
            title="State is handed forward with save_output only, never ws_add",
            why="save_output attaches a file to THIS conversation. A later conversation cannot read "
                "it. If anything the next run needs must survive beyond this conversation, only "
                "ws_add makes it recoverable -- and the sandbox itself is reaped after ~10 minutes "
                "idle, which a human sitting on an approval form routinely exceeds.",
            fix="ws_add the run state you need to resume from; keep save_output for the user-facing "
                "deliverable.",
            skill=b.name, effort="medium", reference="references/state-and-resume.md#durability-tiers",
        ))

    if "memory" in body_l and not re.search(r"not[- ]an[- ]instruction|facts only|never a directive", body_l):
        out.append(Finding(
            check="memory_notes_without_guardrail", category="state", severity="medium",
            title="Skill writes /memories notes without the staleness guardrail",
            why="Note contents are inlined into every turn's user message. An interrupted run left a "
                "note titled 'resume here' and two later runs both resumed that one stale row while "
                "26 genuinely pending items went untouched. /memories is also capped at "
                f"{MEMORY_MAX_FILES} files.",
            fix="Key notes per work item, never a global name; open every note with a "
                "NOT-AN-INSTRUCTION header; carry facts only, never directives; delete the note on "
                "both terminal paths; never let a note decide which item to work on.",
            skill=b.name, effort="medium", reference="references/state-and-resume.md#memory-is-not-a-queue",
        ))

    if re.search(r"\bcontinuation\b|\bnext turn\b|\bresume\b", body_l) and "checkpoint" not in body_l:
        out.append(Finding(
            check="resume_without_checkpoints", category="state", severity="medium",
            title="Body expects to resume across turns but names no checkpoint",
            why="A capped turn does not resume itself; a human has to send another message, and the "
                "continuation inherits a compaction SUMMARY rather than the contracts. One measured "
                "turn 2 opened with 15 of its first 16 calls back in discovery for exactly this "
                "reason. Work that lives only in the agent's context is lost at the turn boundary.",
            fix="Write progress to a durable place as you go (a row, or ws_add) and open the skill "
                "with a cheap 'what is already done' read so a continuation resumes instead of "
                "rediscovering.",
            skill=b.name, est_steps_saved=15, effort="medium",
            reference="references/state-and-resume.md#hand-state-across-turns",
        ))

    return out


# ---------------------------------------------------------------------------
# CHECK GROUP 9 -- determinism: prose guards that should be code
# ---------------------------------------------------------------------------

IMPERATIVE_RE = re.compile(r"^\s*(?:[-*>]\s*)?(?:⛔|🚨|⚠️|\*\*)?\s*(?:NEVER|ALWAYS|MUST|DO NOT|Never|Always|Must)\b",
                           re.M)


def check_determinism(b: SkillBundle) -> tuple[list[Finding], dict]:
    out: list[Finding] = []
    imperatives = len(IMPERATIVE_RE.findall(b.body))
    refusing = 0
    for p in (q for q in b.aux if is_script_rel(b.rel(q))):
        src = read_text(p)
        if re.search(r"sys\.exit\(\s*[1-9]", src) or re.search(r'"error"\s*:', src):
            refusing += 1
    scripts = sum(1 for q in b.aux if is_script_rel(b.rel(q)))

    if imperatives >= 12 and scripts and refusing / max(1, scripts) < 0.5:
        out.append(Finding(
            check="prose_guards_not_backed_by_refusals", category="determinism", severity="high",
            title=f"{imperatives} imperative prose guards but only {refusing}/{scripts} scripts refuse",
            why="Every guard that failed in production was advisory prose: violating it produced no "
                "error and the turn still reported success. The same guard was skipped twice in a "
                "row across two incidents. A rule the agent can silently ignore is not a rule -- it "
                "is a hope. The replacements that held either require the agent to emit something "
                "only compliance can produce, or block the output on a count.",
            fix="For each load-bearing guard, move enforcement into a script: refuse to write the "
                "output when the invariant is violated (non-zero exit + an `error`/`why` payload), "
                "or assert a count before the write. Keep the prose as explanation, not as the "
                "mechanism.",
            skill=b.name, est_steps_saved=None, effort="high",
            reference="references/determinism.md",
        ))

    # Exit-code convention: ok:false but always exit 0.
    ev = []
    for p in (q for q in b.aux if is_script_rel(b.rel(q))):
        src = read_text(p)
        soft_fail = re.search(r'"ok"\s*:\s*False|ok\s*=\s*False', src)
        hard_exit = re.search(r"sys\.exit\(\s*[1-9]", src)
        if soft_fail and not hard_exit:
            ev.append(Evidence(b.rel(p), None, "reports ok:false but never exits non-zero"))
    if ev:
        out.append(Finding(
            check="inconsistent_exit_codes", category="determinism", severity="medium",
            title=f"{len(ev)} script(s) report failure in JSON but always exit 0",
            why="If some scripts exit non-zero and others return 0 with ok:false, the agent cannot "
                "trust the exit code and reads the JSON to find out -- and when it misreads, it "
                "proceeds on a failure. A uniform convention lets one bash call run the chain and "
                "stop in the right place on its own.",
            fix="Adopt one convention across every script: 0 clean, 2 a check failed, 3 the agent is "
                "needed. Document it once in SKILL.md.",
            skill=b.name, evidence=ev[:8], effort="medium",
            reference="references/script-contracts.md#one-exit-code-convention",
        ))

    return out, {"imperative_guards": imperatives, "scripts_that_refuse": refusing,
                 "script_count": scripts}


# ---------------------------------------------------------------------------
# CHECK GROUP 10 -- plugin level: skills sharing one turn budget
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CHECK GROUP 11 -- data contracts BETWEEN skills in a chain
# ---------------------------------------------------------------------------
#
# One skill's scripts write a JSON shape; the next skill's scripts read it. Nothing
# validates the handoff, so a disagreement is silent: Python hands back None and the
# run fails two or three scripts downstream, having produced a plausible bundle.
#
# The real case this exists for: a loader read `t.get("date")` while the producing
# extractor wrote `transaction_date`. The month lookup returned None, all 88 rows were
# marked low-confidence, the readiness gate reported 88 exceptions, and finding it cost
# ~60 steps of grepping plus the capped turn.
#
# This is a CANDIDATE GENERATOR, not a prover: a script legitimately reads keys from
# data nothing in the chain writes (an API response, a customer workbook, a DB row).
# So a bare "read but never written" is reported only as a short list to eyeball, and
# the loud finding needs a NEAR MISS -- a written key that looks like what was meant.

_GENERIC_READ_KEYS = frozenset({
    "type", "name", "id", "value", "status", "code", "message", "text", "url",
    "key", "keys", "items", "rows", "columns", "content", "result", "results",
    "ok", "count", "index", "args", "kwargs", "self",
})


def _key_sites(path: Path, rel: str):
    """(written, read, container_keys, consumes_json) for one script, via AST.

    ``container_keys`` are read with a dict/list default -- the code is asserting a
    nesting level exists. When nothing in the chain writes that key, the wrapper is
    imagined and every value under it reads as empty.
    """
    written: dict[str, list[tuple[int, str]]] = defaultdict(list)
    read: dict[str, list[tuple[int, str]]] = defaultdict(list)
    container: set[str] = set()
    src = read_text(path)
    consumes_json = bool(re.search(r"json\.load\(|json\.loads\(|read_text\(", src))
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return {}, {}, container, consumes_json
    lines = src.splitlines()

    def txt(node) -> str:
        ln = getattr(node, "lineno", 0)
        return lines[ln - 1].strip()[:160] if 0 < ln <= len(lines) else ""

    for node in ast.walk(tree):
        # {"k": v} literal
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    written[k.value].append((getattr(k, "lineno", 0), txt(k)))
        # d["k"] = v
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.slice, ast.Constant)
                        and isinstance(tgt.slice.value, str)):
                    written[tgt.slice.value].append((getattr(tgt, "lineno", 0), txt(tgt)))
        # x.get("k") -- and note a dict/list default, which asserts a nesting level
        elif isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == "get" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                key = node.args[0].value
                read[key].append((getattr(node, "lineno", 0), txt(node)))
                if len(node.args) > 1 and isinstance(node.args[1], (ast.Dict, ast.List)):
                    container.add(key)
        # x["k"] in a read position
        elif (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
              and isinstance(node.slice, ast.Constant)
              and isinstance(node.slice.value, str)):
            read[node.slice.value].append((getattr(node, "lineno", 0), txt(node)))
    return dict(written), dict(read), container, consumes_json


def _near_miss(read_key: str, written: set[str]) -> str | None:
    """A written key that plausibly IS what `read_key` meant."""
    r = read_key.lower()
    best = None
    for w in written:
        wl = w.lower()
        if wl == r:
            return None
        # Underscore-boundary containment: date <-> transaction_date. Requires the
        # shared part to be a real stem (>=4 chars) and the extra qualifier to be
        # substantial (>=4), or every `date`/`start_date` and `accounts`/
        # `accounts_request_id` pair in an external API response fires.
        short, lng = (r, wl) if len(r) <= len(wl) else (wl, r)
        if (len(short) >= 4 and len(lng) - len(short) >= 5
                and (lng.endswith("_" + short) or lng.startswith(short + "_"))):
            return w
        # same letters, different separators: gross_weight <-> grossweight
        if len(r) >= 6 and wl.replace("_", "") == r.replace("_", ""):
            return w
        # NB: plain edit distance is deliberately NOT used. At k=2 on 6-char keys it
        # matched `amount` to `count` and `ms_code` to `ms` on the real corpus -- the
        # kind of false critical that gets a whole reviewer ignored.
        # Agrees at BOTH ends, disagrees in the middle -- how `ending_cost_by_asset`
        # and `ending_cost_per_asset` differ. Edit distance misses these (3 edits)
        # while 18 shared characters of 21 make the intent obvious.
        if len(r) >= 8 and len(wl) >= 8:
            pre = _common_prefix(wl, r)
            suf = _common_suffix(wl[pre:], r[pre:])
            if pre >= 3 and suf >= 3 and (pre + suf) >= 0.7 * max(len(wl), len(r)):
                best = best or w
    return best


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _common_suffix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x != y:
            break
        n += 1
    return n


def _within_edits(a: str, b: str, k: int) -> bool:
    if abs(len(a) - len(b)) > k:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > k:
            return False
        prev = cur
    return prev[-1] <= k


def check_data_contracts(bundles: list[SkillBundle],
                         chain: list[SkillBundle]) -> list[Finding]:
    """Compare keys READ by each skill against keys WRITTEN anywhere in the chain."""
    out: list[Finding] = []
    # ONLY runs when the reviewer explicitly names a producing/consuming skill with
    # --chain. Without that assertion the prior is wrong: most JSON a skill reads
    # comes from OUTSIDE the chain (Graph, Plaid, VIES, TARIC, a spreadsheet, or a
    # file the agent hand-writes), so "nothing in the chain writes this key" is the
    # normal case and the check produces almost pure noise. Measured on six real
    # skills before this gate: 5 flagged CRITICAL, nearly all wrong.
    if not chain:
        return out
    all_bundles = bundles + [c for c in chain if c.dir not in {b.dir for b in bundles}]
    if not all_bundles:
        return out

    written_union: set[str] = set()
    per_skill: list[tuple[SkillBundle, dict, dict, set[str]]] = []
    for b in all_bundles:
        w_all: dict[str, list] = defaultdict(list)
        r_all: dict[str, list] = defaultdict(list)
        consumers: set[str] = set()
        containers: dict[str, tuple[str, int, str]] = {}
        for p in (q for q in b.aux if is_script_rel(b.rel(q))):
            w, r, cont, cj = _key_sites(p, b.rel(p))
            for k in cont:
                if k not in containers and r.get(k):
                    ln, t = r[k][0]
                    containers[k] = (b.rel(p), ln, t)
            for k, v in w.items():
                w_all[k] += [(b.rel(p), ln, t) for ln, t in v]
            for k, v in r.items():
                r_all[k] += [(b.rel(p), ln, t) for ln, t in v]
            if cj:
                consumers.add(b.rel(p))
        written_union |= set(w_all)
        per_skill.append((b, dict(w_all), dict(r_all), consumers, containers))

    if len(written_union) < 5:
        return out  # not enough signal to say anything

    for b, w_all, r_all, consumers, containers in per_skill:
        if b not in bundles:
            continue  # chain-only skills contribute writes, aren't themselves reviewed
        mismatches: list[Evidence] = []
        for key, sites in sorted(r_all.items()):
            if key in written_union or key in _GENERIC_READ_KEYS or len(key) < 4:
                continue
            hit = _near_miss(key, written_union)
            if hit:
                f, ln, t = sites[0]
                mismatches.append(Evidence(
                    f, ln, f'reads "{key}" — nothing in this chain writes it; '
                           f'"{hit}" is a near match  |  {t}'))

        if mismatches:
            out.append(Finding(
                check="cross_skill_key_mismatch", category="data-contract", severity="medium",
                title=f"{len(mismatches)} CANDIDATE shape mismatch(es) against the named "
                      f"chain — verify each; external-data reads look identical to this",
                why="A shape disagreement between two skills is SILENT: the read returns None, the "
                    "run builds a plausible object, and it fails two or three scripts later. One "
                    "real loader read `date` while the producing extractor wrote "
                    "`transaction_date` — the lookup returned None, all 88 rows were flagged "
                    "low-confidence, the readiness gate reported 88 exceptions, and locating it "
                    "cost ~60 steps of grepping plus the turn's whole remaining budget.",
                fix="Verify each pair against the producing skill. Then make the consumer accept "
                    "both spellings AND refuse loudly with a named error listing the fields it "
                    "could not recognise. A one-step refusal beats sixty-step discovery. If the "
                    "producer is the one that is wrong, fix it there instead — but do not leave "
                    "the reader silently defaulting either way.",
                skill=b.name, evidence=mismatches[:10],
                est_steps_saved=40, effort="medium",
                reference="references/script-contracts.md#the-handoff-between-two-skills",
            ))

        ghosts = [(k, v) for k, v in containers.items()
                  if k not in written_union and k not in _GENERIC_READ_KEYS and len(k) >= 5]
        if ghosts:
            out.append(Finding(
                check="assumed_wrapper_never_written", category="data-contract",
                severity="medium",
                title=f"{len(ghosts)} key(s) read with a dict/list default that nothing in the "
                      f"chain ever writes",
                why="A `.get(\"k\", {})` asserts that a nesting level exists. When the producer "
                    "never writes that key the default swallows it: every value underneath reads "
                    "as absent and the run continues on empty data. One real loader read balances "
                    "from an `extracted_fields` wrapper that never existed -- the producer nested "
                    "them one level deeper under a different parent, and the values came through "
                    "null with no error anywhere.",
                fix="Confirm the real nesting against the producing skill, then read the actual "
                    "path AND refuse when it is missing. A dict default is the right idiom for an "
                    "optional field and the wrong one for a required container.",
                skill=b.name,
                evidence=[Evidence(f, ln, f'reads "{k}" as a container; nothing writes it  |  {t}')
                          for k, (f, ln, t) in ghosts[:8]],
                est_steps_saved=20, effort="medium",
                reference="references/script-contracts.md#the-handoff-between-two-skills",
            ))

    return out


# ---------------------------------------------------------------------------
# CHECK GROUP 12 -- loops that skip input silently
# ---------------------------------------------------------------------------

_DIR_ITER_RE = re.compile(r"\b(?:glob|rglob|iterdir|listdir|scandir|walk)\b")


def check_silent_skip(b: SkillBundle) -> list[Finding]:
    """A file-matching loop that `continue`s on a non-match and never refuses.

    If nothing matches the filename contract, the output is simply empty -- with no
    error anywhere. One real loader did exactly this: no file matched its
    `<prefix>_<id>_<period>.json` name contract, the loop skipped every candidate, and
    the collection came out empty.
    """
    out: list[Finding] = []
    ev: list[Evidence] = []
    for p in (q for q in b.aux if is_script_rel(b.rel(q))):
        src = read_text(p)
        if not _DIR_ITER_RE.search(src):
            continue
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            continue
        lines = src.splitlines()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_src = "\n".join(
                lines[fn.lineno - 1: getattr(fn, "end_lineno", fn.lineno)])
            if not _DIR_ITER_RE.search(body_src):
                continue
            loops = [n for n in ast.walk(fn) if isinstance(n, ast.For)]
            has_continue = any(isinstance(n, ast.Continue) for lo in loops
                               for n in ast.walk(lo))
            if not has_continue:
                continue
            refuses = any(
                (isinstance(n, ast.Raise))
                or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "exit")
                for n in ast.walk(fn)
            )
            if refuses:
                continue
            ev.append(Evidence(b.rel(p), fn.lineno,
                               f"{fn.name}() iterates a directory, skips non-matches with "
                               f"`continue`, and never raises or exits non-zero"))
    if ev:
        out.append(Finding(
            check="silent_skip_on_no_match", category="determinism", severity="medium",
            title=f"{len(ev)} function(s) skip unmatched input files without ever refusing",
            why="If nothing matches the filename contract the result is an EMPTY output and no "
                "error — indistinguishable from 'there was no data'. The agent then spends its "
                "budget diagnosing a downstream failure whose cause was an unmatched filename.",
            fix="Count what matched. If the count is zero, exit non-zero with a message naming the "
                "expected filename pattern and listing what was actually present. A usage message "
                "at the point of failure is worth dozens of discovery steps.",
            skill=b.name, evidence=ev[:8], est_steps_saved=15, effort="low",
            reference="references/determinism.md",
        ))
    return out


def check_plugin(bundles: list[SkillBundle], pj: dict | None) -> list[Finding]:
    out: list[Finding] = []
    if pj is not None:
        if "__parse_error__" in pj:
            out.append(Finding(
                check="plugin_json_unparseable", category="validity", severity="critical",
                title="plugin.json does not parse", why=pj["__parse_error__"],
                fix="Fix the JSON.", effort="low",
                reference="references/platform-contract.md#plugins",
            ))
        else:
            slug = str(pj.get("name") or "")
            if not slug or not SLUG_RE.match(slug):
                out.append(Finding(
                    check="plugin_slug_invalid", category="validity", severity="critical",
                    title=f"plugin.json name `{slug}` is not a valid slug",
                    why="The plugin slug comes from plugin.json::name (not the zip's top directory) "
                        "and is slug-validated at upload.",
                    fix="Use ^[a-z0-9][a-z0-9-]*$.", effort="low",
                    reference="references/platform-contract.md#plugins",
                ))
            desc = str(pj.get("description") or "")
            if len(desc) > 600:
                out.append(Finding(
                    check="plugin_description_is_a_changelog", category="hygiene", severity="medium",
                    title=f"plugin.json description is {len(desc)} characters",
                    why="This field is a catalog one-liner and may be surfaced in the skills menu. A "
                        "changelog here is context every reader pays for and no reader wants.",
                    fix="One sentence. Move the history into references/ or a CHANGELOG.md.",
                    effort="low", reference="references/platform-contract.md#plugins",
                ))
    if len(bundles) > MAX_SKILLS_PER_PLUGIN:
        out.append(Finding(
            check="too_many_skills_in_plugin", category="validity", severity="critical",
            title=f"{len(bundles)} skills -- over the {MAX_SKILLS_PER_PLUGIN} per-plugin limit",
            why="Upload rejects the bundle.", fix="Split the plugin.", effort="medium",
            reference="references/platform-contract.md#plugins",
        ))

    # Cross-skill staging => these skills run in the same turn and share one 100.
    names = {b.name for b in bundles}
    cross: dict[str, set[str]] = {}
    for b in bundles:
        for other in names - {b.name}:
            if re.search(rf"read_skill_file[^\n]{{0,80}}{re.escape(other)}", b.body) or \
               re.search(rf"{re.escape(other)}[^\n]{{0,40}}(?:scripts|references|assets)/", b.body):
                cross.setdefault(b.name, set()).add(other)
    if cross:
        ev = [Evidence(f"{k}/SKILL.md", None, f"stages from {', '.join(sorted(v))}")
              for k, v in cross.items()]
        out.append(Finding(
            check="skills_share_one_turn_budget", category="budget", severity="high",
            title=f"{len(cross)} skill(s) stage files from a sibling skill -- they run in one turn "
                  f"and draw from the SAME 100 steps",
            why="The 100-iteration cap is per TURN, not per skill. Skills that hand off inside one "
                "turn are spending one budget between them. Two measured runs had the first skill "
                "alone consume 82-84 of the 100 before the next skill started, and both turns ended "
                "degraded before the deliverable was produced.",
            fix="Give each skill an explicit step allowance in its own body, state where in the turn "
                "it runs and how much is likely left, and put the shared mechanism in ONE reference "
                "file rather than restating it in each. Then check whether the handoff needs to be "
                "in one turn at all.",
            evidence=ev, est_steps_saved=None, effort="medium",
            reference="references/step-budget.md#one-turn-one-budget",
        ))
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def audit(target: Path, chain_paths: list[str] | None = None) -> dict:
    bundles, pj, kind = discover(target)
    chain: list[SkillBundle] = []
    for cp in chain_paths or []:
        try:
            cb, _cpj, _ck = discover(Path(cp).expanduser().resolve())
            chain += cb
        except FileNotFoundError:
            pass
    findings: list[Finding] = []
    measurements: dict = {"kind": kind, "target": str(target), "skills": []}

    for b in bundles:
        findings += check_frontmatter(b)
        findings += check_file_placement(b)
        findings += check_discovery(b)
        bf, bm = check_batching(b)
        findings += bf
        df, dm = check_duplication(b)
        findings += df
        findings += check_data_through_model(b)
        findings += check_process(b)
        findings += check_state_and_stop(b)
        findings += check_silent_skip(b)
        detf, detm = check_determinism(b)
        findings += detf

        fates: dict[str, int] = {}
        for p in b.aux:
            fates[staging_fate(b.rel(p), p.read_bytes())] = \
                fates.get(staging_fate(b.rel(p), p.read_bytes()), 0) + 1
        measurements["skills"].append({
            "name": b.name,
            "dir": str(b.dir),
            "skill_md_lines": len(b.raw.splitlines()),
            "skill_md_bytes": len(b.raw),
            "aux_files": len(b.aux),
            "aux_bytes": sum(p.stat().st_size for p in b.aux),
            "staging_fates": fates,
            "has_process": isinstance(b.meta.get("process"), dict) and bool(b.meta.get("process")),
            "batching": bm,
            "duplication": dm,
            "determinism": detm,
        })

    findings += check_plugin(bundles, pj)
    findings += check_data_contracts(bundles, chain)

    # Dedupe evidence within each finding -- several checks walk the same file twice.
    for f in findings:
        seen: set[tuple] = set()
        uniq: list[Evidence] = []
        for e in f.evidence:
            key = (e.file, e.line, e.text)
            if key not in seen:
                seen.add(key)
                uniq.append(e)
        f.evidence = uniq

    findings.sort(key=lambda f: (
        SEV_ORDER.get(f.severity, 9), -(f.est_steps_saved or 0), f.check))

    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    # These estimates OVERLAP -- fixing the staging batch and collapsing a chain can
    # save the same step. Summing them is an upper bound, not a forecast, and it is
    # capped below the ceiling so it can never read as "more than the whole budget".
    raw_est = sum(f.est_steps_saved or 0 for f in findings)
    est = min(raw_est, MAX_TOOL_ITERATIONS - 1)

    # One entry per distinct check, highest severity first.
    do_first: list[str] = []
    for f in findings:
        if f.severity in ("critical", "high") and f.effort == "low" and f.check not in do_first:
            do_first.append(f.check)
        if len(do_first) == 6:
            break

    return {
        "summary": {
            "target": str(target),
            "kind": kind,
            "skills": [b.name for b in bundles],
            "findings": len(findings),
            "by_severity": counts,
            "estimated_steps_recoverable_upper_bound": est,
            "estimate_note": "Findings overlap; this is an upper bound on steps recoverable "
                             "per turn, not a forecast. Treat the ordering as the signal.",
            "do_first": do_first,
            "step_ceiling": MAX_TOOL_ITERATIONS,
            "platform_facts_verified": "adoptai-workflows origin/dev @ 63980995 (2026-09-10)",
            "yaml_available": HAVE_YAML,
            "chain_skills": [c.name for c in chain],
        },
        "measurements": measurements,
        "findings": [asdict(f) for f in findings],
    }


def render_text(report: dict) -> str:
    s = report["summary"]
    L = [
        f"Adopt harness skill audit -- {s['kind']}: {s['target']}",
        f"skills: {', '.join(s['skills']) or '(none)'}",
        f"findings: {s['findings']}  {s['by_severity']}",
        f"steps recoverable per turn: up to ~{s['estimated_steps_recoverable_upper_bound']} "
        f"of {s['step_ceiling']}  (upper bound -- findings overlap)",
        f"platform facts: {s['platform_facts_verified']}",
        "",
    ]
    if s["do_first"]:
        L += ["DO FIRST (high impact, low effort): " + ", ".join(s["do_first"]), ""]
    for f in report["findings"]:
        head = f"[{f['severity'].upper():8}] {f['check']}"
        if f.get("skill"):
            head += f"  ({f['skill']})"
        L.append(head)
        L.append(f"  {f['title']}")
        if f.get("est_steps_saved"):
            L.append(f"  est. steps saved: ~{f['est_steps_saved']}   effort: {f['effort']}")
        else:
            L.append(f"  effort: {f['effort']}")
        L.append(f"  why: {f['why']}")
        L.append(f"  fix: {f['fix']}")
        for e in f.get("evidence", [])[:6]:
            loc = e["file"] + (f":{e['line']}" if e.get("line") else "")
            L.append(f"    - {loc}  {e.get('text', '')}")
        if f.get("reference"):
            L.append(f"  see: {f['reference']}")
        L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="skill directory (has SKILL.md) or plugin directory")
    ap.add_argument("--format", choices=("json", "text"), default="json")
    ap.add_argument("--out", help="write the full JSON report here as well")
    ap.add_argument("--chain", action="append", metavar="PATH",
                    help="another skill/plugin dir this one hands data to or from. "
                         "Repeatable. Its scripts contribute WRITTEN keys so the "
                         "cross-skill shape check can see both sides.")
    args = ap.parse_args()

    target = Path(args.path).expanduser().resolve()
    if not target.is_dir():
        print(json.dumps({"error": f"not a directory: {target}"}), file=sys.stderr)
        return 3
    try:
        report = audit(target, args.chain)
    except FileNotFoundError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 3

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=None) + "\n", "utf-8")
    print(render_text(report) if args.format == "text"
          else json.dumps(report, indent=None))

    sev = report["summary"]["by_severity"]
    if sev.get("critical"):
        return 2
    if sev.get("high") or sev.get("medium"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
