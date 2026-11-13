#!/usr/bin/env python3
"""NoUI defaults for the shared default-tier deploy script (``scripts/deploy_default_skill.py``).

Kept so ``python harness-skills/noui/deploy_default_skill.py`` behaves exactly as before:
``SKILL_NAME=noui``, this folder's ``SKILL.md`` and ``noui-bundle.zip`` unless overridden.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parent.parent / "scripts" / "deploy_default_skill.py"

if __name__ == "__main__":
    os.environ.setdefault("SKILL_NAME", "noui")
    os.environ.setdefault("SKILL_MD", str(_HERE / "SKILL.md"))
    if not os.environ.get("AUX"):  # the old script treated an empty AUX as unset
        os.environ["AUX"] = f"noui-bundle.zip={_HERE / 'noui-bundle.zip'}"
    sys.argv[0] = str(_SHARED)
    runpy.run_path(str(_SHARED), run_name="__main__")
