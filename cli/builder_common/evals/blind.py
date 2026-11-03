"""Blind mode (PLAN.md section 11): the agent gets a generic prompt and the case inputs only.

Withheld from a blind run: expected outputs/labels/states, case notes, gap files, any FDE
message, hint or follow-up turn, and answers to questions the agent asks. The runner builds
the message from `blind_message` and never passes anything else to the driver.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from cli.builder_common.errors import UsageError


def prompts_path(ws: Path, agent: str) -> Path:
    return ws / "harness" / "agents" / agent / "prompts.yaml"


def load_prompts(ws: Path, agent: str) -> dict[str, str]:
    """`prompts.yaml`: either ``{blind: "..."}`` or ``{prompts: {key: "..."}}``."""
    p = prompts_path(ws, agent)
    if not p.exists():
        raise UsageError(f"{p} not found: blind evals need a generic prompt in prompts.yaml")
    data = yaml.safe_load(p.read_text()) or {}
    prompts: dict[str, Any] = data["prompts"] if isinstance(data.get("prompts"), dict) else data
    return {str(k): str(v) for k, v in prompts.items() if isinstance(v, str)}


def blind_message(ws: Path, agent: str, key: str = "blind") -> str:
    prompts = load_prompts(ws, agent)
    if not prompts.get(key, "").strip():
        raise UsageError(f"prompts.yaml for {agent!r} has no generic prompt {key!r}")
    return prompts[key]


def generic_prompt_rows(ws: Path, agent: str) -> list[dict[str, Any]]:
    """Mirror of prompts.yaml as ``bld_generic_prompts`` rows."""
    return [
        {
            "prompt_key": f"{agent}:{k}",
            "agent_key": agent,
            "prompt": v,
            "applies_to": "per_case",
            "status": "active",
        }
        for k, v in sorted(load_prompts(ws, agent).items())
    ]
