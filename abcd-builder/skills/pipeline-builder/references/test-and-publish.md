# Testing, streaming and publishing pipelines

## How `test_pipeline.py` streams

Sources: `cli/test_pipeline.py` and `cli/wdl_common/pipeline_stream.py`.

1. Preconditions: the workspace exists, and `pipeline.json` has a `remote_pipeline_id`.
   If it doesn't, run `save_pipeline_draft.py` first. `--status`, `--mark-passed` and
   `--mark-failed` are the only modes that skip the remote check.
2. The WDL is the saved remote draft, unless you pass `--local-wdl`, which reads and
   validates the local `widdle.json` (it must be non-empty valid JSON).
3. The run is triggered with `test_mode=true`. `--production` makes it
   `test_mode=false`, and then the call returns 409 if another run is active.
   `workflow_params` always includes `auth_token` (a fresh bearer).
4. **Stream (default):** `GET <BFF>/stream/<remote_pipeline_id>` with
   `Authorization: Bearer` and `Accept: application/x-ndjson`. The trigger runs on a
   background thread so the subscription doesn't miss the first events.
   - `:` keepalive lines are ignored.
   - A "Stream not found" error is transient, because the workflow session starts a
     moment after the trigger. The client reconnects every 3 s until the deadline.
   - `scheduling-test-run-step-progress` / `wdl_step_progress` are printed as
     `Step i/N: label [started|running|completed|failed]`, with the error when a
     step fails.
   - The final event is `scheduling-test-run-output`. Chunks with status
     `started`/`streaming` are put back together into `result`. The run also ends on
     `wdl_execution_test_mode` / `wdl_execution_completed` / `wdl_execution_failed`.
     Output is printed and cut off at 2000 chars.
   - `__end` means the server closed the stream.
   - Payloads may arrive raw or wrapped under `data`. Both are handled. The `source`
     field (`pusher` or `redis`) is informational.
5. Outcome:
   - status `passed`/`success`/`completed` → **auto `mark_test_passed`**, exit 0
   - status `failed`/`error` → **auto `mark_test_failed`**, exit 1
   - any other status → exit 0, and test status is left untouched
   - no final event before `--timeout` (default 300 s) → exit 1 with "no final result
     received". The run may still be going. Check `--status` or `harness_runs.py`.
   - trigger error → exit 1. A 409 means another run is active.
   - `httpx` missing → a warning and no result. It is in the poetry env.
6. **`--no-stream`** only triggers the run, prints the run ID, and exits 0. Results can
   still be read from `/stream/<id>`. Mark the outcome by hand afterwards with
   `--mark-passed` / `--mark-failed`.

When a run fails: read the failing step's label and error from the stream, fix
`widdle.json`, re-run with `--local-wdl`, and save when it's green. For long
pipelines, raise `--timeout` instead of switching to `--no-stream`, so that you keep
the auto-marking.

## Publish

`publish_pipeline.py` marks the test as passed, then publishes the draft version.
With `--activate` it also sets `state=running`. `--skip-test-mark` skips the mark
step. Publishing is live, so you need the user's confirmation, and then `--yes`.

## CLI flags

| Script | Flags |
|---|---|
| `manage_pipeline.py` | `--create` \| `--show ID` · `--id` · `--title/-t` · `--description/-d` · `--prompt` · `--source-type internal\|salesforce\|connector` · `--source-connector-id` `--source-connector-type` `--source-connector-name` · `--destination-connector-id` `--destination-connector-type` `--destination-label` · `--schedule-type manual\|cron` · `--json` (with `--show`) |
| `save_pipeline_draft.py` | `PIPELINE_ID...` · `--description/-d` · `--parallel N` · `--dry-run` · `--legacy-prompt-flow` · `--verbose/-v` |
| `test_pipeline.py` | `pipeline_id` · `--production` · `--local-wdl` · `--mark-passed` · `--mark-failed` · `--status` · `--no-stream` · `--timeout SECONDS` (default 300) · `--verbose/-v` |
| `publish_pipeline.py` | `PIPELINE_ID...` · `--activate` · `--skip-test-mark` · `--parallel N` · `--yes/-y` · `--dry-run` · `--verbose/-v` |
| `list_pipelines.py` | `--remote` · `--state draft\|running\|paused\|error\|local` · `--search TEXT` · `--json` |
| `workspace.py pipeline` | `create --title [--id] [--description] [--prompt] [--source-type ...] [--source-connector-*]` · `list` · `show` · `checkout-all [--env] [--limit N] [--state draft\|running\|paused\|error] [--search] [--force] [--dry-run]` |
| `harness_runs.py` | `list [--range 24h\|7d\|30d\|all] [--status succeeded\|active\|waiting_on_hitl\|failed\|cancelled] [--source pipeline\|conversation] [--workstream-id] [--agent] [--search] [--page] [--page-size] [--env] [--json]` · `detail --run-id pr:<id>\|cv:<id>` · `trace --conversation-id ID` |

The pipeline scripts use the active env only. Switch with `workspace.py env use`.

## Scripted pipelines

Legacy `create_*_pipeline.py` scripts can replace their inline client with
`from cli.wdl_common.pipeline_client import get_pipeline_client`. New work should use
the workspace flow above.
