"""``GET /skills/{name}`` serves the description and a frontmatter-less body (live 2026-10-29)."""

from __future__ import annotations

from cli.builder_common.platform import rebuild_skill_md
from cli.builder_common.staging import split_frontmatter
from cli.builder_common.versions import detail_digest, skill_md_digest

MD = '---\nname: tb\ndescription: "Does a thing."\n---\n\n# tb\n\nSteps.\n'
DETAIL = {"name": "tb", "description": "Does a thing.", "body": "# tb\n\nSteps.\n", "aux_files": []}


def test_pulled_skill_md_has_frontmatter_and_hashes_like_the_upload() -> None:
    rebuilt = rebuild_skill_md(DETAIL, "tb")
    meta, body = split_frontmatter(rebuilt)  # a skill pulled from the platform uploads again
    assert meta == {"name": "tb", "description": "Does a thing."} and "# tb" in body
    assert skill_md_digest(rebuilt) == skill_md_digest(MD) == detail_digest(DETAIL)


def test_a_raw_body_is_kept_and_output_fields_are_restored() -> None:
    assert rebuild_skill_md({"body": MD}, "tb") == MD
    out = rebuild_skill_md({**DETAIL, "output_type": "json", "output_schema": {"a": 1}}, "tb")
    assert split_frontmatter(out)[0]["output_type"] == "json"
