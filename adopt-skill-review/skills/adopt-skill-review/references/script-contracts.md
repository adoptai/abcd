# Script contracts — the single highest-value thing to fix

Pinned to `adoptai-workflows` origin/dev @ `63980995` (2026-09-10).

## Why this is the top finding, almost always

**A script's source never returns to the model's context.** `read_skill_file` on a
`scripts/*.py` writes it to disk and returns a one-line confirmation — the file path, its
size in chars, and a run command. The agent gets *no* view of what the script does.

Meanwhile the platform tells the agent: *"`SKILL.md` is the skill's complete CONTRACT:
purpose, trigger, inputs, outputs, CLI usage, options, and error handling… NEVER read a
skill's `.py` source to understand it. Reading implementation is wasted budget."*

So if the contract is not in `SKILL.md`, the skill has put the agent in an impossible
position: it has been told the answer is in a document that does not contain it. It goes and
reads the source anyway, and the budget goes with it.

Measured on real runs:

- **146 of 170** bash calls spent probing source code or JSON files rather than doing work.
- One export turn: **27** bash calls grepping its own scripts (`grep -n "<a column name>"`,
  `sed -n '200,260p'`, …) then **47** inline `python3 -c` calls reimplementing them.
  **74 of 77** bash calls were spelunking and reimplementation. Nothing errored.
- **78** source reads across two turns.
- In one baseline, **97 of 170** calls were the agent discovering the skill's own tooling and
  only **9** ran a script to do work. One script was touched **30 times** to be run twice.

After contracts were written for that skill: every pipeline script ran exactly once, no flag
hunting, and the agent batched the independent checks by itself. Discovery dropped from 56
calls to 46 and the run still capped — which is the honest caveat: **contracts are necessary
and not always sufficient.** They change where the budget goes.

## The contract block

One per runnable script, in `SKILL.md`, next to the step that runs it:

```
### scripts/normalize_rows.py
Run:     python scripts/normalize_rows.py --input raw/ --out normalized.json [--strict]
Reads:   every *.csv under --input; column aliases from assets/aliases.json
Writes:  normalized.json
Shape:   {"ok": bool,
          "counts": {"read": int, "kept": int, "rejected": int},
          "rows": [{"id": str, "amount": float, "currency": str}],
          "rejects": [{"row": int, "reason": str}]}
Exits:   0 clean · 2 a check failed · 3 the agent is needed
Notes:   --strict exits non-zero if anything lands in rejects.
         Safe to run alongside enrich_rows.py — different outputs, no shared writes.
```

Every line earns its place:

- **Run** — the literal command with every flag. Without it the agent has to work them out;
  each guess is a step.
- **Reads / Writes** — what it consumes and produces, so the agent knows the data flow
  without inspecting it, and knows which file to hand to the next step.
- **Shape** — the *real* keys. Without this the agent runs the script and then prints the
  output's keys just to learn it is `counts` and not `summary`.
- **Exits** — so the agent can trust the exit code instead of reading the JSON to find out.
- **Notes → "safe to run alongside"** — this is the line that tells the agent what it may
  **batch**. Add it wherever it is true; it is the cheapest batching instruction there is.

For a script whose whole result is its stdout, a "what it prints" section works better than
a shape line:

> ### What the script prints — read THIS, never the source
>
> `build_export.py` writes nothing to stdout except **one JSON object**. That object is the
> complete result. There is nowhere else to look, and no reason to open the file.
>
> **Success (exit 0):** `{"output_path": …, "deliverables": […], "record_count": 3}`
> `output_path` is the final deliverable and its filename is already correct — do not
> rename, copy, move or post-process it.

## Refusals are escalations, not puzzles

A script that refuses rather than emit bad output is doing its job. The agent needs to be
told that the refusal **is** the answer.

Undocumented, a refusal reads as a problem to solve. One real turn hit a single refusal at
around step 22 and spent **steps 22 to 99** reverse-engineering the scripts to find where a
figure came from, grepping for the same two identifiers more than a dozen times. Documented,
it would have ended at about step 50 with a clean escalation.

Document every refusal as a table:

| `error` | What it means | Your next action |
|---|---|---|
| `no eligible records found` | the batch carries nothing this step can act on | Report it and stop. Never synthesise one. |
| `input validation failed — NO CSV written` | a required key is missing or misnamed; unfixed it would land as `0` | Fix the input JSON, re-run |
| `<amount> could not be apportioned` | a row would carry a zero where a real figure belongs | Supply the missing input, then re-run |

And state the rule plainly: **every one of these is fixed by changing the INPUT and
re-running.** Never by rebuilding the script's work inline — that bypasses the one guard
stopping a wrong result from being filed. This belongs in the skill because it is exactly
what a model under pressure will not infer.

## One exit-code convention

Pick one and apply it to every script:

```
0  clean
2  a check failed
3  the agent is needed (a judgement, a vision read, a human decision)
```

The problem it solves is mixed conventions. When some scripts exit non-zero and others
return `0` with `"ok": false`, the agent cannot trust the exit code, so it reads the JSON to
find out — and when it misreads, it proceeds on a failure. A uniform convention lets one
bash call run a chain and stop in the right place on its own.

## Print a summary, write the payload

`bash` stdout returned to the model is capped at **100,000 chars and truncated in the
middle**. A script that pretty-prints its whole result therefore delivers a *cut* result,
and the agent then probes the output file one `python3 -c` per fact to recover it.

```python
def emit(obj, out, *, summary_keys=("ok", "state", "counts", "next_action")):
    Path(out).write_text(json.dumps(obj, default=str) + "\n")   # no indent: half the bytes
    head = {k: obj[k] for k in summary_keys if k in obj}
    head["failures"] = obj.get("failures", [])[:20]
    head["out"] = str(out)
    print(json.dumps(head, default=str))
```

Drop `indent=` on machine-read files too — it doubles the bytes for a file no human reads,
and those bytes count against the 256 KiB read cap if the agent ever reads it back.

## A uniform output envelope

When several scripts feed one flow, give them the same shape so the agent learns it once:

```
{"state": "pass" | "fail" | "needs_agent" | "unevaluated",
 "counts": {...},
 "failures": [{"path":…, "row":…, "column":…, "actual":…, "expected":…, "delta":…}],
 "next_action": "render decision-queue for 3 items"}
```

`next_action` is worth the effort: it lets the script tell the agent what to do next instead
of the agent deriving it. And a **uniform failure row** matters more than it looks — when
each check emits a different set of fields, the agent assembles the comparison by hand,
which is both steps and a place to make mistakes.

## Lookup flags instead of loading big references

If a script needs a large reference file, don't make the agent read it. Give the script
`--describe-format` / `--describe-check` flags so it can answer questions about its own
config. One skill had an 86 KB and a 53 KB reference; keeping them out of context saved up
to 140 KB.

## Don't make the agent guess paths

Every input and output defaulting to a run directory (`--run-dir`, defaulting to
`/workspace/<skill>/<step>.json`) removes a class of step: with five to ten path flags per
script, each typo costs a round trip.

## The handoff between two skills

When skill A's scripts write a JSON file that skill B's scripts read, that shape is an
interface — and nothing on the platform validates it. A disagreement is **silent**: the
read returns `None`, the run builds a plausible object, and it fails two or three scripts
later, far from the cause.

A real triage found one consumer disagreeing with its producer in five distinct ways. The
shapes generalise to any domain:

| The disagreement | How it surfaced |
|---|---|
| a field read under a shorter name than the producer writes (`x` vs `prefix_x`) | the lookup returned `None`; every row was flagged low-confidence and the readiness gate reported the whole batch as exceptions |
| values read through a wrapper key the producer never emits | the values came through null |
| one code path attaching the raw record, a sibling path not attaching it | a whole output column came out empty and a cross-check went off by a large amount |
| a derived field produced by an enrichment step the consumer's path skips | that figure was simply missing from the total |
| files required to match a filename pattern, with no match | the collection came out empty |

Nothing raised, in any of the five. Locating them cost about 60 steps of grepping and took
the turn's remaining budget with it — which is how a skill whose documentation was correct
still hit the cap.

**What to require of the consumer:**

- **Accept both spellings and both nesting shapes** where the producer's history is
  genuinely ambiguous.
- **Then refuse loudly.** A named failure that lists the fields it could not recognise
  turns sixty steps of discovery into one. This is the determinism rule applied to an
  interface: a reader that silently defaults is the defect, whichever side is "wrong".
- **A dict default is for an optional field, never a required container.**
  `rec.get("wrapper", {})` on a wrapper the producer does not emit swallows the whole
  subtree.
- **Count what matched.** A file-glob loop that skips non-matches must refuse when the
  count is zero, naming the expected filename pattern and listing what was present.
- **Say where the shape is defined**, in both skills' bodies, so the next person changing
  either one knows there is a counterpart.

`audit_skill.py --chain <other-skill-dir>` compares keys read against keys written across
the named skills and reports candidates. It is gated behind `--chain` on purpose: without
that assertion, keys legitimately read from an API, a spreadsheet or an agent-written file
look identical to a mismatch.
