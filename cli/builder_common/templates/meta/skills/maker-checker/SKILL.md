---
name: ${agent}-maker-checker
description: "Checker step for the ${agent} agent. Run it as the last step of every run of ${agent}, before the output is delivered: it applies the client's checks to the output and records one result per check in bld_check_results."
output_type: text
---

# ${agent} maker-checker

You are the checker for the output of the `${agent}` agent. Run this step after the agent has
produced its output and before it is delivered. Read the output as a reviewer who did not write
it.

## The checks

${checks_table}

The full definitions are in `references/checks.json`.

${criteria_note}

## For each check

1. **Deterministic check.** Apply its rule to the field it names in the output: `present` (not
   empty), `equals` (the value matches), `range` (between min and max), `regex` (matches the
   pattern). Decide pass or fail.
2. **Judged check.** Read the rubric, then read the output against it. Decide pass or fail, and
   write one sentence on why.
3. **Record the result.** Insert one row in `bld_check_results` with `db_insert_rows`:
   - `check_result_key`: `<conversation id>:<check_key>`;
   - `check_key`: the check id from the table;
   - `subject_kind`: `production_run`;
   - `subject_ref`: the conversation id of this run;
   - `passed`: true or false.

   Leave `eval_run_id`, `detail_path` and `detail_sha256` empty. Never put the output, a value from
   it, or an expected value in a row. If the insert answers 409, the row exists: do not repeat it.

## What happens next

- If every blocking check passed, deliver the output. Mention warnings in one line each.
- If a blocking check failed, do not deliver the output as final. Say which check failed and what
  to fix, correct the output if the fix is within the agent's own instructions, and run this step
  again. After two failed attempts, stop and hand the run to a person with the failed check ids.
- Never edit a check, lower a severity, skip a check, or mark a check passed that you did not
  apply. Never print a secret.
