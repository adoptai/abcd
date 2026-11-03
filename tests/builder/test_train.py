"""cli/builder_common/train.py."""

from __future__ import annotations

from pathlib import Path

from cli.builder_common import train as tr
from cli.builder_common.explain import AgentInfo, Scenario, Step, VersionRow
from cli.builder_common.train import FaqEntry

INFO = AgentInfo(
    agent="return-prep",
    display_name="Return prep",
    steps=(Step("Extract", "Reads the forms"),),
    versions=(VersionRow("v3", "2026-10-20", "faster", live=True),),
    scenarios=(Scenario("Scanned return", "A scanned PDF arrives", "Runs OCR"),),
)


def test_prompt_validation_filters() -> None:
    def run(p: str) -> bool:
        if p == "boom":
            raise RuntimeError("run failed")
        return p.startswith("good")

    res = tr.validate_training_prompts(
        ["good one", "bad one", "boom", "good one", "  ", "good two"], run
    )
    assert res.shipped == ["good one", "good two"]
    assert res.dropped == ["bad one", "boom"]


def test_kit_files_and_content(tmp_path: Path) -> None:
    res = tr.generate_training_kit(
        INFO,
        tmp_path / "agent",
        prompts=["Prepare the return for Acme"],
        faq=[FaqEntry("Why was it flagged?", "A check failed.")],
        whats_new=["Faster extraction"],
    )
    assert res.ok
    names = sorted(p.name for p in res.paths)
    assert names == sorted(tr.TRAINING_FILES)
    root = tmp_path / "agent" / "training"
    assert "Prepare the return for Acme" in (root / "prompts.md").read_text()
    assert "Why was it flagged?" in (root / "faq.md").read_text()
    assert "Faster extraction" in (root / "whats-new.md").read_text()
    assert "Live version: v3" in (root / "quickstart.md").read_text()
    assert "Scanned return" in (root / "scenarios.md").read_text()
    for p in res.paths:
        text = p.read_text()
        assert "None" not in text and "undefined" not in text


def test_empty_kit_has_honest_placeholders(tmp_path: Path) -> None:
    res = tr.generate_training_kit(AgentInfo(agent="bare"), tmp_path / "a")
    assert res.ok
    assert "No prompts have been verified yet." in (tmp_path / "a/training/prompts.md").read_text()
    assert "Nothing released yet." in (tmp_path / "a/training/whats-new.md").read_text()


def test_failure_reported_not_raised(tmp_path: Path) -> None:
    blocker = tmp_path / "a"
    blocker.write_text("file")
    res = tr.generate_training_kit(INFO, blocker)
    assert not res.ok and "training kit failed" in res.error
