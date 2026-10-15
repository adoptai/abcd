# Testing actions

Deep reference: `prompts/system/TESTING_PROMPT.md`.

## Modes of `cli/test_runner.py`

| Mode | Command | Needs a save? | What runs |
|---|---|---|---|
| Compile | `test_runner.py <id> --compile` | no | JSON syntax check, then the remote compiler. No execution. **Mandatory before any run.** |
| Direct (default) | `test_runner.py <id> [--test test_1.json \| --all]` | no | Local `widdle.json` via `/run-wdl` |
| Compile then run | `test_runner.py <id> --validate` | no | Compiler validation, then execution |
| Remote | `test_runner.py <id> --remote` | **yes** | The saved remote action via run_action |
| Inline agent | `test_runner.py <agent> --inline [a,b]` | no | Agent WDL with sub-action WDLs sent inline |
| Via agent | `test_runner.py <agent> --via-agent --subaction <name>` | depends | A sub-action through its parent |
| Multi-turn | `test_runner.py <agent> --multi-turn test_1.json test_2.json [--inline]` | no | Files composed into one conversation |
| Batch | `--workspace <env>` · `--agent <a> --all-subactions` · `a b c --parallel 3` | no | Many actions |
| Dry run | `--dry-run` | no | Lists the tests that would run. No API calls. |

`--test` takes a **filename only**: `--test test_2.json`, not `--test test_cases/test_2.json`.
`--verbose` prints the WDL operations and full traces. Traces are saved to `traces/`.

## Test case files (`test_cases/test_N.json`)

Write three before the first run: a common case, a different input combination, and
an edge case.

Single turn:

```json
{
  "prompt": "Show invoice INV-1001",
  "workflow_params": {"invoiceId": "INV-1001"},
  "expected_output": {
    "description": "Invoice details with amount, status and due date",
    "validation": "similarity",
    "key_fields": ["amount", "status", "due_date"],
    "sample_output": "Invoice INV-1001: $1,200.00, status open, due 2026-01-31"
  }
}
```

Multi-turn (the server keeps conversation state via `trace_id`, and each turn sends
only its own prompt):

```json
{
  "turns": [
    {"prompt": "I need an invoice", "expected_output": {"description": "Asks which invoice", "validation": "similarity"}},
    {"prompt": "INV-1001", "expected_output": {"description": "Returns its details", "validation": "similarity", "key_fields": ["amount"]}}
  ],
  "workflow_params": {}
}
```

Validation types:
- `similarity` (the default choice): **you** judge whether the actual output has the
  same structure and real, non-hallucinated data.
- `contains`: code checks that `key_fields` are present.
- `exact`: code checks for an exact match. Rarely right.

## CloudFront 120 s gateway limit

Every platform call (`/run-wdl`, `/run`, compile) goes through CloudFront, which has a
**hard ~120 s timeout**. A longer turn returns **504**. This is infrastructure and the
client can't fix it.

Observed budget for an uber agent turn:

| Tool calls in one turn | `--remote` (`/run`) | default (`/run-wdl`) |
|---|---|---|
| 1–2 | about 60–68 s, pass | pass |
| 3 (with script generation) | about 62 s, pass | about 121 s, **504** |
| 4+ | **504** | **504** |

So plan for **at most about 3 tool calls per turn with `--remote`** and **about 2 with
`/run-wdl`**. `--remote` is a little faster per request. To stay under the limit:

- Test each sub-action alone (Tier 1) and keep agent test prompts narrow, one or two
  tools per turn.
- Split a long scenario into a `turns` test or use `--multi-turn`.
- Make the system prompt shorter. Size is latency.
- A 504 is not a WDL bug, so don't "fix" the WDL for it. Change the test's scope.

## Production check through the Chrome extension

`ce_test.py` runs the published agent through the real Adopt copilot in Chrome
(command list in `cli-reference.md`). It is the only test that covers the entity-store
sync and visibility. Run it after publish when the user wants production validation.
Run `ce_test.py start` in the background, then `ce_test.py run`.
