---
name: session-debug
description: "Drive and debug Adopt Agent Harness sessions from abcd: start a session on an agent, send follow-up messages, check status, read logs and outputs, stop a runaway turn, list and resolve held human-in-the-loop gates and questions, approve a browser skill from the member's own message, and manage test pipelines. Use when the user says start a session, run the agent, send a message to the conversation, why is it stuck, show the logs, download the outputs, stop the turn, there is a pending approval, answer the question, resolve the HITL, approve the skill, create a pipeline, list pipeline runs, or pause the pipeline."
---

# session-debug

Sessions are harness conversations: `POST /turns` on a workstream and a process. There is no
pause or resume. A held gate or question appears in the stream and the turn completes; you
resolve it with a new message on the same conversation.

Run from the root of an abcd checkout, in the client workspace.

## When to use

- Trying an agent by hand, or reproducing a failure from a run or a client report.
- A turn is waiting on a human decision.
- Setting up, inspecting or pausing a pipeline for testing.

## Step 0 - inventory and the cheapest model

```bash
abcd session models --env <env> --json       # allowed model ids; omit --model for the org default
abcd workstream list --env <env> --json
abcd agent list --env <env> --json
abcd conversations list --hours 24 --env <env> --json
abcd runs list --agent <agent name> --workstream-id <workstream name> --env <env> --json
```

For a throwaway test pick the cheapest model (on staging a Haiku) with `--model`; the BFF uses
the org default when you pass none.

## Step 1 - sessions

```bash
abcd session start --workstream <workstream> --process <agent> --message "<prompt>" --env <env> --json
abcd session send <conversation_id> --message "<text>" --env <env> --json
abcd session status <conversation_id> --env <env> --json
abcd session logs <conversation_id> --env <env> --json
abcd session outputs <conversation_id> --env <env> --json
abcd session stop <conversation_id> <turn_id> --env <env> --json
```

- Use the staged process only on staging and dataset workstreams.
- Never put a token or JWT in a message; the platform returns `requires_confirmation`, and you
  do not acknowledge the warning to push it through.
- `stop` ends a runaway turn; tell the member what was lost.
- `start` and `send` print the turn's own text, tool calls and errors. The BFF streams every
  earlier turn of the conversation first (retained Redis events plus a `transcript` replay);
  abcd cuts the stream at the other turns' terminal events, so a second turn's result is that
  turn only. In a saved `turn-00N.ndjson` expect those replayed lines before the turn's own.
- `status`: for a few minutes after the final answer the workflow can still say
  `RUNNING stage=saving_outputs` with `terminal=True`. `terminal=True` means the answer is
  final; a new `send` is accepted meanwhile. A stopped turn ends `cancelled` (exit 1), not `failed`.
- `outputs --download --out <dir>` fetches what the agent saved with `save_output`.
- `logs` also writes `run-detail.json` (the org-scoped `/v1/runs/detail`); that is the way to read
  a run you do not own, for example the conversation a pipeline's run-now started
  (`abcd runs detail --run-id <conversation_id>`).
- For a deeper look use `abcd trace fetch <turn_id> --conversation-id <conversation_id>` and `abcd trace review` (`eval-runner`).

## Step 2 - held gates and questions

```bash
abcd hitl list --conversation <conversation_id> --env <env> --json
abcd hitl resolve <conversation_id> --builder-id <builder_id> --step-id <step_id> --values '{"<field>": "<value>"}' --env <env> --json
abcd hitl answer <conversation_id> --text "<the member's answer>" --env <env> --json
abcd hitl decide <builder_id> --decision approve --note "<why>" --env <env> --yes --json
abcd hitl approve-skill <conversation_id> --fingerprint <32-hex fingerprint> --env <env> --yes --json
abcd hitl approve-release <conversation_id> --release <agent>@v<n> --sha <sha12> [--action release|rollback] --env <env> --yes --json
```

- `--value k=v` is typed from the card: a `checkbox` field is sent as a boolean
  (`--value approved=true`), a `number` as a number, a `select` takes the option `value`.
- This harness has no dedicated `question` tool: an agent asked for one answers with a
  `render_builder` card (resolve it with `hitl resolve`) or just asks in plain text (reply with
  `hitl answer`). A card that is not bound to a process step has no `step_id`; that is fine.
- `hitl list --conversation <id>` keeps only that conversation's cards, escalation tasks and
  runs. `decide` only works on a `policy-...` escalation (any other id answers 404). After the
  decision is recorded the platform starts a follow-up turn on the held conversation by itself
  (turn id `policy-decision-<key>`; the agent is told it was approved or rejected), so do not
  send a turn to resume it: read the outcome with `session status` or `session logs`. A
  `recorded: false` answer means nothing was stored and the card is still open.
- Show the member the gate or question text; the **answer and decision are theirs**. `resolve`
  takes the `builder_id`, `step_id` and field names from `hitl list`; it refuses a missing
  required field. Pass `--yes` for `decide` only after they said so, and record their reason
  as the note.
- Policy escalations (`policy-...`) only record the decision; check the run afterwards.
- `approve-skill` **sends** the browser-skill approval token as a user turn under the FDE's
  PAT, which is what the platform's install gate accepts as the member's own approval. Run it
  only when the member, in this conversation, has looked at the replay result and explicitly
  approved that exact fingerprint. **Never approve a browser skill on the member's behalf**; if
  they have not approved, send them back to `skill-builder`.
- `approve-release` **sends** the release approval token
  (`<!--adopt:release-approval v="1" action="release|rollback" release="<agent>@v<n>"
  sha="<sha12>"-->`; `--action` defaults to `release`, and a rollback needs `--action rollback`
  naming the version rolled back to) as a user turn, the member's own approval in a cloud
  conversation, where `abcd release --yes` and `abcd rollback --yes` are refused without it. Run it only after the member has seen the eval report and approved
  that release and commit in this conversation.
- Both approve commands are **refused in a cloud run** ("a human sends approvals from the chat
  UI, not the agent"): in the cloud the human types the token in the chat, and abcd accepts it
  only from a user other than the one the PAT authenticates as, in a workstream-shared run.
- Boundary-detection HITLs stay blocking during evals; do not tune them away.

## Step 3 - pipelines (test and admin use)

```bash
abcd pipeline list --env <env> --json
abcd pipeline create --process <process_id> --schedule manual --env <env> --dry-run --json
abcd pipeline create --process <process_id> --schedule cron --cron "0 6 * * 1-5" --state draft --env <env> --json
abcd pipeline get <pipeline_id> --env <env> --json
abcd pipeline activate <pipeline_id> --env <env> --yes --json
abcd pipeline pause <pipeline_id> --env <env> --json
abcd pipeline runs <pipeline_id> --env <env> --json
abcd pipeline delete <pipeline_id> --env <env> --yes --json
```

`pipeline run-now <id> --yes` fires a process pipeline once (it works for a draft or paused
manual pipeline); `pipeline runs <id>` then lists the conversation ids it started, readable with
`abcd runs detail --run-id <conversation_id>` and `abcd session logs <conversation_id>`.
`pause` is the way back to a quiet state; `--state` on `create` is `draft` or `running` only.

A pipeline created with a PAT records the **token id as its owner**, not a user. Use this for
tests and admin only. Production cron pipelines are created once from a browser session. Do not
activate a cron pipeline or delete one without the member's explicit yes.

## Rules

- **Approvals:** `--yes` only after the human said so. Leads approve loosening, forced releases
  and default-tier collisions; you cannot.
- **Safety:** no client data in the abcd repo (logs and outputs stay in the workspace, ignored
  by git); never commit to the default branch; never deploy-default for client work; BFF with
  the PAT only, never workflows services directly; never print a secret.

## Hand-offs

- Fixing the skill or agent the log points at: `skill-builder`, `agent-builder`.
- Scoring many cases: `eval-runner`. Releasing the fix: `release-manager`.
- Cloud runs and the daily event flush: `workspace-setup` and the `abcd` harness skill
  (`abcd cloud bootstrap`, `abcd events flush`).
