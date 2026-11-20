---
name: dataset-builder
description: "Define what correct means for an agent: create datasets of the right kind (work_product, labeled, read_your_writes, custom), add, synthesize, update and retire cases, then define metrics, client checks and release criteria. Expectations live in a separate store so the agent under test cannot read them. Use when the user says build a dataset, add a test case, golden dataset, ground truth, generate synthetic cases, retire this case, correct the expected output, define a metric, add a check, set release criteria, set the target, or what counts as passing."
---

# dataset-builder

A dataset says what "correct" means. Every dataset has a kind and every case shares one shape:
inputs, optional setup, expected, optional teardown, scorers, source, tags.

| Kind | Use for |
|---|---|
| `work_product` | agent produces documents; one workstream per case |
| `labeled` | classification or routing; known or synthetic cases |
| `read_your_writes` | writes to a system; needs `--target-env` and a probe agent |
| `custom` | anything else, scored by a script in `harness/evals/scorers/` |

Run from the root of an abcd checkout, in the client workspace.

## When to use

- A plan item for a dataset, check, metric or criteria is approved.
- An agent has no ground truth yet, or the client supplied examples.
- A release criterion has to change.

## Step 1 - create and fill

```bash
abcd dataset create <dataset> --kind work_product --env <env> --dry-run --json
abcd dataset create <dataset> --kind work_product --env <env> --json
abcd dataset add-case <dataset> <case_id> --input <file> --expected <file> --env <env> --json
abcd dataset synth <dataset> --from templates.yaml --count 20 --env <env> --json
abcd dataset show <dataset> --env <env> --json
```

- Labelled: `add-case --label <label>`. Read-your-writes: `--expect-state`, plus
  `--target-env` and `--probe` at create; the run refuses if the probe is not on the declared
  test system.
- `synth` generates labelled cases from a templates file (`--from`, yaml or json); `--clients` takes a known-clients file.
- Tag known versus synthetic cases separately so each can have its own target.
- Expected files, labels and states are stored by path and hash only, never as values in a table
  and never in the workspace repo.

## Step 2 - change cases (versioned)

```bash
abcd dataset update-case <dataset> <case_id> --expected <file> --env <env> --json
abcd dataset retire-case <dataset> <case_id> --note "<why>" --env <env> --yes --json
abcd dataset archive <dataset> --env <env> --yes --json
```

Every change makes a new dataset version. Archive is refused while criteria reference the dataset.

## Step 3 - metrics, checks, criteria

```bash
abcd metric define <agent> <metric> --category quality --source check_results --formula pass_rate --env <env> --json
abcd metric list <agent> --env <env> --json
abcd check add <agent> <check_id> --kind deterministic --env <env> --json
abcd check list <agent> --env <env> --json
abcd criteria set <agent> --dataset <dataset> --metric pass_rate --op ">=" --target <value> --env <env> --json
abcd criteria show <agent> --env <env> --json
```

The metric is a built-in (`pass_rate`, `blind_repro_rate`, `open_gaps`, `avg_cost_usd`, ...) or one you
defined with `metric define`; there is no built-in `accuracy`. Create the dataset before
`criteria set` (it refuses an unknown one), and pass `--agent` to `dataset create` only after the
agent was pushed (`push --staged`), since it needs the staged process id. Every metric must be able to fail. Accuracy means passing all the client's checks. A linked
agent's criteria always include `blind_repro_rate` and `open_gaps <= 0`.

`release.yaml` holds one dataset per agent (`dataset: name@version`) and entries of `metric`,
`comparator` (`>=`, `<=` or `no_regression`) and `target`. An eval refuses a dataset whose cases
have no scorer (`--scorer` on `dataset create` or `add-case`) and no client check.

## Rules

- **Approvals:** `--yes` only after the human said so. **Loosening a target, lowering a bar or
  editing an expected output needs a lead's recorded approval**; the FDE alone cannot approve
  it. Never edit an expectation to make a run pass.
- **Safety:** no client data, real filenames or values in the abcd repo (use neutral names in
  anything shared); datasets stay out of the default branch except through a reviewed pull
  request; never deploy-default for client work; BFF with the PAT only.

## Hand-offs

- Running the dataset: `eval-runner`.
- Misses that look like missing context: `context-gaps`.
- Criteria and the gate at release: `release-manager`.
