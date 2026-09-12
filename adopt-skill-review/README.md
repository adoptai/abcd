# adopt-skill-review

A Claude Code skill that reviews a **skill or plugin built for the Adopt agent harness** and
reports what is costing it latency, LLM steps and reliability.

Point it at a skill you wrote, or one you inherited, and it tells you where the steps are
going, what the platform already enforces for you, whether your files are in the folders the
harness actually reads them from, and which of your rules are prose that a run can silently
ignore.

## Install

Copy this directory anywhere and add it as a plugin, or symlink the skill into your skills
directory:

```bash
ln -s "$PWD/skills/adopt-skill-review" ~/.claude/skills/adopt-skill-review
```

Needs Python 3 and PyYAML (`pip install pyyaml`). Without PyYAML the frontmatter checks
degrade but everything else still runs.

## Use

In Claude Code:

```
review my harness skill at workspaces/<env>/plugins/<plugin>
```

Or run the audit directly:

```bash
# a skill, or a whole plugin (point at the plugin when there is one -- skills that
# hand off inside one turn share a single 100-step budget)
python3 skills/adopt-skill-review/scripts/audit_skill.py <path> --format text

# measure a real run instead of estimating
temporal workflow show --workflow-id <agent-harness-...> --output json > turn.json
python3 skills/adopt-skill-review/scripts/analyze_run_trace.py turn.json

# check the pinned platform facts before restructuring a skill around one
python3 skills/adopt-skill-review/scripts/verify_platform_facts.py \
    --repo ../adoptai-workflows
```

`audit_skill.py` exits 0 (clean), 1 (medium/high findings), 2 (critical) — so it works in CI.

## What it looks for

A harness turn gets 100 LLM steps, and one assistant response is one step no matter how many
tool calls it carries. Almost every "this skill is slow" complaint is the skill's own text
forcing more steps than the work needs.

- **Batching** — staging scattered across sections instead of one batch; no explicit
  instruction to batch; a ceiling stated without forbidding the one-at-a-time default; the
  five-slot transfer limit; and the opposite mistake, cramming a whole batch into one call's
  arguments, where a truncated call writes **nothing**.
- **Discovery burn** — scripts with no invocation, no stdout shape, no exit codes, no
  documented refusals. Usually the largest single finding: measured at 146 of 170 bash calls
  spent probing the skill's own source.
- **File placement** — `references/` text files are inline-only and **never** written to the
  sandbox, so a script that opens one finds nothing and may silently fall back to a built-in
  default. Also catches shared modules missing from the staging batch, which is the most
  commonly forgotten line in a harness skill.
- **Data through the model** — missing `db_query save_to_file`, missing `render_ui file_ref`,
  stdout piping, scripts dumping full payloads past the 100KB cap.
- **Duplication** — rules the harness already injects every turn, which cost context on
  every step and can only be weakened by paraphrase.
- **Missing stop conditions** — one real turn had a valid deliverable at step 38 and spent
  the remaining 62 re-verifying it into the ceiling.
- **Prose guards nothing enforces**, plus the frontmatter traps that fail silently: a quoted
  `"true"` on `datastore_write` no-ops the write gate, and any YAML that fails strict parsing
  drops the entire `process:` block while upload still returns 200.
- **Process/HITL and GenUI** — gate shape, artifact slots, the multi-block rule, component
  names.
- **Plugin level** — skills that hand off inside one turn and share one budget.

Platform facts are pinned to a commit and dated. `verify_platform_facts.py` re-checks them,
because hard-won workarounds outlive the bugs they were written for.

## Layout

```
skills/adopt-skill-review/
  SKILL.md                        the review procedure
  scripts/audit_skill.py          deterministic audit -> JSON/text, CI exit codes
  scripts/analyze_run_trace.py    Temporal history -> where the 100 steps went
  scripts/verify_platform_facts.py  drift check against adoptai-workflows
  references/
    platform-contract.md          pinned limits, tool gates, dispatch lanes, frontmatter
    already-enforced.md           what the harness injects every turn -- delete it
    step-budget.md                step economics and every batching rule
    script-contracts.md           the contract block, envelopes, exit codes, refusals
    file-layout.md                scripts/ vs assets/ vs references/ staging matrix
    state-and-resume.md           durability tiers, sandbox lifecycle, memory, identity
    process-and-hitl.md           process blocks, gates, builders, escalations, GenUI
    determinism.md                turning prose guards into enforced ones
```

Nothing here is specific to a use case. The incidents behind the rules are real, and are
carried as anonymised failure shapes.
