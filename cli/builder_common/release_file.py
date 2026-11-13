"""The one ``release.yaml`` schema (PLAN.md sections 5 and 10).

::

    agent: triage
    dataset: invoices@3                 # name[@version]; one dataset per agent
    criteria:
      - criteria_key: triage:invoices:pass_rate
        metric: pass_rate
        comparator: ">="                # ">=", "<=" or "no_regression"
        target: 0.9                     # absent for no_regression
        dataset_version: 3
        set_by: ...
        decision_ref: ...

``abcd criteria set`` writes it, ``abcd release``/``test-staged``/``eval gate`` read it, and the
rows in ``bld_release_criteria`` mirror each entry. ``upgrade_legacy`` reads the first form
``criteria set`` wrote (per-entry ``op`` and ``dataset``) and rewrites it into this schema.
"""

from __future__ import annotations

from typing import Any

COMPARATORS = (">=", "<=", "no_regression")
NO_REGRESSION = "no_regression"


def split_dataset(ref: Any) -> tuple[str, int | None]:
    """``name[@version]`` -> (name, version or None)."""
    name, _, ver = str(ref or "").strip().partition("@")
    return name.strip(), int(ver) if ver.strip().isdigit() else None


def join_dataset(name: str, version: int | None) -> str:
    return f"{name}@{version}" if version is not None else name


def is_legacy(doc: dict[str, Any]) -> bool:
    return any(
        isinstance(c, dict) and ("op" in c or "dataset" in c) for c in doc.get("criteria") or []
    )


def upgrade_legacy(doc: dict[str, Any]) -> dict[str, Any]:
    """Rewrite per-entry ``op``/``dataset`` into ``comparator`` and a top-level ``dataset``.

    Raises ``ValueError`` when the old file mixes datasets (the schema allows one).
    """
    if not is_legacy(doc):
        return doc
    names = {c["dataset"] for c in doc["criteria"] if c.get("dataset")}
    if len(names) > 1:
        raise ValueError(f"release.yaml mixes datasets ({', '.join(sorted(names))}); keep one")
    out = dict(doc)
    entries = []
    for c in doc["criteria"]:
        e = {k: v for k, v in c.items() if k not in ("op", "dataset")}
        e["comparator"] = c.get("comparator") or c.get("op")
        for h in e.get("history") or []:
            if "op" in h:
                h["comparator"] = h.pop("op")
        entries.append(e)
    out["criteria"] = entries
    if names and not out.get("dataset"):
        versions = [c["dataset_version"] for c in entries if c.get("dataset_version") is not None]
        out["dataset"] = join_dataset(next(iter(names)), max(versions) if versions else None)
    return out
