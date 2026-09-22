#!/usr/bin/env python3
"""
verify_platform_facts.py -- re-check this plugin's pinned platform facts against a
live adoptai-workflows checkout, and report drift.

Why this exists: the facts in references/platform-contract.md are pinned to a commit.
Hard-won workarounds outlive the bugs they were written for. A real example -- one
production skill still spends a whole dedicated turn on a data-store write because
"the write gate is live only WITHIN one response", which was true before
`_fetched_skills_from_history` landed and seeds the capability from earlier turns.
Copying that pattern today burns a turn for nothing. Run this before trusting a
number, and especially before telling an FDE to restructure a skill around one.

Usage
    python3 verify_platform_facts.py --repo /path/to/adoptai-workflows [--ref origin/dev]

It reads the files out of git (so your working tree can be anything) and falls back
to the working tree if the ref is unavailable.

Exit codes
    0  every pinned fact matches
    1  drift detected
    3  repo/ref unusable
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PINNED_AT = "adoptai-workflows origin/dev @ 63980995 (2026-09-10)"

# name -> (file, regex capturing the value, expected)
CONSTANTS = [
    ("MAX_TOOL_ITERATIONS", "src/workflows/agent_harness/constants.py",
     r"^MAX_TOOL_ITERATIONS\s*=\s*(\d+)", "100"),
    ("CLEAR_TRIGGER_PCT", "src/workflows/agent_harness/constants.py",
     r"^CLEAR_TRIGGER_PCT\s*=\s*(\d+)", "60"),
    ("COMPACT_TRIGGER_PCT", "src/workflows/agent_harness/constants.py",
     r"^COMPACT_TRIGGER_PCT\s*=\s*(\d+)", "80"),
    ("CROSS_TURN_COMPACT_PCT", "src/workflows/agent_harness/constants.py",
     r"^CROSS_TURN_COMPACT_PCT\s*=\s*(\d+)", "70"),
    ("KEEP_RECENT_TOOL_USES", "src/workflows/agent_harness/constants.py",
     r"^KEEP_RECENT_TOOL_USES\s*=\s*(\d+)", "4"),
    ("KEEP_RECENT_ROUNDS", "src/workflows/agent_harness/constants.py",
     r"^KEEP_RECENT_ROUNDS\s*=\s*(\d+)", "3"),
    ("BASH_OUTPUT_MAX_CHARS", "src/workflows/agent_harness/constants.py",
     r"^BASH_OUTPUT_MAX_CHARS\s*=\s*([\d_]+)", "100_000"),
    ("GENUI_FULL_UNION", "src/workflows/agent_harness/constants.py",
     r"^GENUI_FULL_UNION\s*=\s*(\w+)", "True"),
    ("DEFAULT_CONTEXT_TOKEN_LIMIT", "src/workflows/agent_harness/constants.py",
     r"^DEFAULT_CONTEXT_TOKEN_LIMIT\s*=\s*([\d_]+)", "200_000"),
    ("db_insert_rows maxItems", "src/workflows/agent_harness/constants.py",
     r'"maxItems":\s*(\d+),\s*\n\s*"items":\s*\{"type":\s*"object"\}', "500"),
    ("_MAX_SKILL_FILE_BYTES", "src/workflows/agent_harness/skills.py",
     r"^_MAX_SKILL_FILE_BYTES\s*=\s*(.+)$", "256 * 1024"),
    ("process steps range", "src/workflows/agent_harness/process.py",
     r"steps:\s*list\[ProcessTemplateStep\]\s*=\s*Field\(min_length=(\d+),\s*max_length=(\d+)\)",
     "1|50"),
    ("_GETMANY_CONCURRENCY", "src/workflows/agent_harness/sandbox_transfer.py",
     r"^_GETMANY_CONCURRENCY\s*=\s*(\d+)", "5"),
    ("memory DEFAULT_MAX_FILES", "src/workflows/agent_harness/memory_store.py",
     r"^DEFAULT_MAX_FILES\s*=\s*(\d+)", "32"),
]

# Behaviours whose PRESENCE is the fact. Absence means the pinned advice is stale.
BEHAVIOURS = [
    ("write gate survives earlier turns", "src/workflows/agent_harness/workflow.py",
     r"_fetched_skills_from_history",
     "A skill fetched in an EARLIER turn stays capability-active, so a data-store write "
     "does NOT need its own dedicated turn."),
    ("assets/ auto co-stage", "src/workflows/agent_harness/activities.py",
     r"_co_stage_skill_assets",
     "Staging any script co-stages every file under that skill's assets/ to disk."),
    # NB: match text that sits on ONE source line -- these tool descriptions are
    # built by string concatenation, so a phrase spanning a break never matches.
    ("lifecycle tools must ride along", "src/workflows/agent_harness/constants.py",
     r"wastes a full model round-trip",
     "The platform itself now forbids a lone lifecycle call, so a skill need not."),
    ("eager staging is all-or-nothing", "src/workflows/agent_harness/workstream_staging.py",
     r"_is_eager_candidate",
     "An in-scope store larger than the session budget pre-stages NOTHING, leaving the "
     "whole budget to ws_read."),
    ("zip is a discoverable deliverable", "src/workflows/agent_harness/artifacts.py",
     r"\.zip",
     "End-of-turn discovery collects .zip, so 'zip is never auto-collected' is stale."),
    ("truncated script refuses to stage", "src/workflows/agent_harness/activities.py",
     r"cannot be staged as a runnable script",
     "A script over the 256 KiB read cap is refused rather than staged truncated."),
    ("parallel/sequential dispatch lanes", "src/workflows/agent_harness/workflow.py",
     r"_is_sequential_tool",
     "Narrative + process tools and a step-bound render_builder serialize; other work "
     "tools fan out in parallel within one step."),
]


def git_show(repo: Path, ref: str, rel: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "show", f"{ref}:{rel}"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def load(repo: Path, ref: str | None, rel: str) -> tuple[str | None, str]:
    if ref:
        txt = git_show(repo, ref, rel)
        if txt is not None:
            return txt, ref
    p = repo / rel
    if p.is_file():
        return p.read_text("utf-8", errors="replace"), "working tree"
    return None, "missing"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="path to an adoptai-workflows checkout")
    ap.add_argument("--ref", default="origin/dev",
                    help="git ref to read (default origin/dev; pass '' for the working tree)")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / "src" / "workflows").is_dir():
        print(f"not an adoptai-workflows checkout: {repo}", file=sys.stderr)
        return 3
    ref = args.ref or None

    if ref:
        subprocess.run(["git", "-C", str(repo), "fetch", "origin", "--quiet"],
                       capture_output=True, timeout=180)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", ref],
                              capture_output=True, text=True, timeout=60)
        if head.returncode != 0:
            print(f"ref {ref} not found in {repo}", file=sys.stderr)
            return 3
        print(f"checking against {ref} @ {head.stdout.strip()}")
    print(f"pinned at: {PINNED_AT}\n")

    cache: dict[str, tuple[str | None, str]] = {}
    drift = 0

    print("constants")
    for label, rel, pat, expected in CONSTANTS:
        if rel not in cache:
            cache[rel] = load(repo, ref, rel)
        txt, src = cache[rel]
        if txt is None:
            print(f"  ?  {label:34} file missing: {rel}")
            drift += 1
            continue
        m = re.search(pat, txt, re.M)
        if not m:
            print(f"  ?  {label:34} pattern no longer matches -- the constant may have "
                  f"been renamed or moved")
            drift += 1
            continue
        found = "|".join(g for g in m.groups() if g is not None)
        if found.replace(" ", "") == expected.replace(" ", ""):
            print(f"  ok {label:34} {found}")
        else:
            print(f"  DRIFT {label:31} pinned={expected}  now={found}   ({src})")
            drift += 1

    print("\nbehaviours (presence is the fact)")
    for label, rel, pat, why in BEHAVIOURS:
        if rel not in cache:
            cache[rel] = load(repo, ref, rel)
        txt, src = cache[rel]
        if txt is None:
            print(f"  ?  {label:34} file missing: {rel}")
            drift += 1
            continue
        if re.search(pat, txt):
            print(f"  ok {label:34} present")
        else:
            print(f"  DRIFT {label:31} GONE -- {why}")
            drift += 1

    print()
    if drift:
        print(f"{drift} fact(s) drifted. Update references/platform-contract.md and the pinned "
              f"values in scripts/audit_skill.py before relying on them.")
        return 1
    print("all pinned facts match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
