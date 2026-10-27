#!/usr/bin/env python3
"""Generalize a compiled skill — prune noise, rename, reword — through a confirmed plan.

    python scripts/generalize.py draft   workbench/skills/<skill>          # write generalize_plan.json
    python scripts/generalize.py show    workbench/skills/<skill>          # print the plan as a table
    python scripts/generalize.py confirm workbench/skills/<skill> --by "<member>"
    python scripts/generalize.py apply   workbench/skills/<skill>

This is the generalization step that compiled SKILL.md files ask for. The
draft is deterministic (noise classification + suggested names); YOU then edit
the decisions in generalize_plan.json, show the plan to the member, and only
after they agree run `confirm` and `apply`. `apply` refuses an unconfirmed plan
and anything the skill's kind does not allow (browser skills: rename/reword
only — never steps, parameters or which operations exist).

Exit codes: 0 ok, 1 refused (fix the plan and retry), 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from noui_core.compile import generalize as g


def _show(skill_dir: Path) -> int:
    plan = json.loads((skill_dir / g.PLAN_FILE).read_text(encoding="utf-8"))
    print(f"{plan['skill']}  kind={plan['kind']}  status={plan['status']}")
    print(f"allowed: {', '.join(plan.get('allowed') or [])}")
    for e in plan["operations"]:
        d = e.get("decision") or {}
        target = d.get("rename") or e["name"]
        arrow = f" -> {target}" if target != e["name"] else ""
        print(f"  [{d.get('action', '?'):4}] {e['name']}{arrow}")
        if e.get("method"):
            print(f"         {e['method']} {e.get('url_template', '')}")
        for r in e.get("noise_reasons") or []:
            print(f"         - {r}")
        if d.get("description"):
            print(f"         desc: {d['description']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft", help="write a draft generalize_plan.json")
    d.add_argument("skill_dir")
    d.add_argument("--force", action="store_true", help="overwrite a confirmed/applied plan")
    s = sub.add_parser("show", help="print the plan")
    s.add_argument("skill_dir")
    c = sub.add_parser("confirm", help="record that the member agreed to the plan")
    c.add_argument("skill_dir")
    c.add_argument("--by", required=True, help="who confirmed (the member, not the agent)")
    a = sub.add_parser("apply", help="apply a confirmed plan")
    a.add_argument("skill_dir")
    args = p.parse_args()

    skill_dir = Path(args.skill_dir).resolve()
    if not skill_dir.is_dir():
        print(f"Error: {skill_dir} is not a directory", file=sys.stderr)
        return 2
    try:
        if args.cmd == "draft":
            path = g.write_draft(skill_dir, force=args.force)
            print(f"Draft written: {path}\n")
            return _show(skill_dir)
        if args.cmd == "show":
            return _show(skill_dir)
        if args.cmd == "confirm":
            print(f"Confirmed: {g.confirm_plan(skill_dir, confirmed_by=args.by)}")
            return 0
        summary = g.apply_plan(skill_dir)
        print(json.dumps(summary, indent=2))
        if summary.get("approval_invalidated"):
            print(
                "\nRenaming moved the replay-approval fingerprint: replay "
                "(verify_replay.py) and get the member's approval again before installing.",
                file=sys.stderr,
            )
        return 0
    except g.GeneralizeError as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
