# Action CLI reference (verified against `--help` on this branch)

Run every command from the repo root as `python cli/<script>.py` (or prefix it with
`poetry run`). All of them use the active environment (`workspaces/.active_env`)
unless the row says `--env` is accepted.

## Discovery and creation

| Script | Flags |
|---|---|
| `discover.py` | `--list-tools` `--list-all` `--list-workflows` `--list-apis` `--list-uber-agents` · `--actions Q` `--apis Q` `--requirements/-r FILE` · `--mode/-m semantic\|fuzzy\|hybrid` (default hybrid) `--tools-only` `--top/-k N` `--threshold/-t F` `--json` `--details` `--refresh` `--verbose` |
| `manage_wdl_action.py` | `--create` / `--update` · `--workflow-id/-w ID` · `--template simple\|workflow` · `--requirements/-r FILE` · `--title/-t` · `--agent/-a NAME` · `--standalone/-s` · `--create-remote` · `--save-draft` · `--use-tool ID` (repeatable) · `--use-api ID` (repeatable) · `--generate-only` · `--dry-run` `--verbose` |
| `workspace.py env` | `create --id --name [--description] [--target development\|staging\|production] [--client] [--domain] [--use]` · `list` · `show` · `use` · `delete` |
| `workspace.py agent` | `create` `list` `show` · `add-subaction --agent --action --remote-id [--title] [--description]` · `remove-subaction` · `move-action` · `checkout --remote-id --env [--id] [--include-subactions]` · `sync` |
| `workspace.py action` | `create` `list` · `checkout-all [--env] [--limit] [--force] [--tools-only\|--workflows-only\|--uber-agents-only] [--include-subactions]` |
| `workspace.py profile` | `show --action <id>` (resolved config inheritance) |
| `checkout_wdl_version.py` | `[action_id] --version N [--workflow-id/-w] [--standalone] [--dry-run]`. See the defects section below. |

## Test, validate, status

| Script | Flags |
|---|---|
| `test_runner.py` | positional `actions...` · `--compile/-c` · `--validate` (compiler check, then execute) · `--remote` · `--test/-t FILE` (filename only) · `--all` · `--multi-turn F1 F2 ...` · `--inline [a,b]` · `--workspace/-w ENV` · `--agent/-a` `--all-subactions` · `--via-agent --subaction NAME` · `--parallel/-p N` (default 5) · `--env/-e` · `--verbose/-v` · `--dry-run` |
| `validate.py` | `<workflow_id> [--auto-fix] [--orchestrator/-o] [--verbose]`. Checks JSON, ids, op fields, required_inputs-as-list, orchestrator titles, duplicate ids, references, and the server compiler. |
| `status.py` | `<workflow_id> [--no-sync]` |
| `reconnect.py` | `<workflow_id> [action_id] [--search/-s] [--dry-run]` |

## Save, publish, deploy

| Script | Flags |
|---|---|
| `save_wdl_draft.py` | positional `actions...` · `--agent/-a` · `--force/-f` (all sub-actions) · `--parallel/-p` · `--description/-d` · `--env/-e` · `--dry-run` `--verbose` · legacy: `--workflow-id/-w` `--action-id` `--standalone/-s` |
| `publish_wdl_action.py` | positional `actions...` · `--agent/-a` · `--parallel/-p` · `--yes/-y` · `--version N` · `--description/-d` · `--env/-e` · `--dry-run` `--verbose` · legacy `--workflow-id/-w` `--standalone/-s` |
| `deployment_rules.py` | `<action>` and exactly one of `--show` `--enable-tool-mode` `--disable-tool-mode` `--visible` `--hidden` · `--dry-run` |
| `patch_action_metadata.py` | `[action] [--agent/-a AGENT] [--title/-t] [--description/-d] [--statement/-s] [--dry-run]`. With no field flags it syncs every field from `metadata.json`. |
| `list_wdl_versions.py` | `[action_id] [--workflow-id/-w] [--standalone/-s]` |
| `move_action.py` | `[item_id] [--agent] --from ENV --to ENV [--source-agent] [--dest-agent] [--copy] [--force] [--keep-id] [--list-envs\|--list-actions\|--list-agents] [--env] [--dry-run]`. Needs explicit user confirmation. |

What `save_wdl_draft.py` does, in order: (1) resolve the action ID, recovering it by
title or creating the remote action; (2) get the draft; (3) publish the WDL into the
draft and wait; (5) populate instructions; (6b) push the `statement` from
`metadata.json` or the WDL, and warn if it is missing (an action without a statement
may never be matched); (6c) wait for regeneration; (7) save the draft with your
`--description`, write `versions/v<N>_widdle.json` and update metadata.

## Chrome extension (CE)

| Command | Purpose |
|---|---|
| `ce_test.py setup` | Check prerequisites, first-time guidance |
| `ce_test.py status` | Chrome and extension readiness |
| `ce_test.py configure <agent> [--profile-id ID] [--target-url URL]` | One-time playground profile and target URL |
| `ce_test.py generate <agent>` | Generate CE test cases from agent metadata |
| `ce_test.py start <agent> [--target-url URL]` | Start Chrome. Long-running, so run it in a separate terminal or in the background. |
| `ce_test.py run <agent> [--test 1,3,5]` | Run CE test cases |
| `ce_test.py send <agent> "<query>"` | Send one ad-hoc query |

`cli/ce_browser.py` is a library (Chrome detection and launch, CDP wait, extension
boot, query runner). It has no CLI of its own.

## Security headers and token configs

Remote playground profiles must **never** hold hardcoded secrets. The CLI rejects
them. Create a token config that says how to extract the secret in the browser, then
reference it by name:

```bash
python cli/workspace.py token-config create --name "acme_api_token" --domain-suffix "example.com" \
  --storage-type customScript --custom-script "return window.__TOKEN__;"
python cli/workspace.py playground-profile update <profile-id> --security-headers '{"x-api-key": "acme_api_token"}'
```

The storage types are `customScript`, `cookie` (`--cookie-key`), `localStorage` and
`sessionStorage` (`--storage-key`), and `domElement` (`--dom-selector`). Local CLI
tests may keep literal values in `adopt_profile.json` → `security_params`, because
those never leave the machine.

## Known CLI defects

Found on this branch while verifying flags. Don't work around them by calling the
API directly. Tell the user.

- `checkout_wdl_version.py` registers `-v` for both `--version` and `--verbose`, so
  argparse raises `ArgumentError` on **every** invocation, including `--help`.
  Alternatives: `workspace.py agent checkout --remote-id <id> --env <env> --include-subactions`
  for agents, and `workspace.py action checkout-all --env <env>` for bulk checkout.
  Local `versions/v<N>_widdle.json` snapshots are also already on disk after every
  save or publish.
- `generate_test_cases.py` fails at import (`No module named 'wdl_common.auth'`).
  Write `test_cases/test_N.json` by hand (see `testing.md`).
- The inline sub-action prefix in `test_runner.py` is `inline::`, but the tool-name
  regex needs `inline__`. See `uber-agents.md`.
- `prompts/system/REMOTE_ACTION_WORKFLOW_PROMPT.md` shows `checkout_wdl_version.py ... --env`,
  but that flag does not exist. `.cursor/rules/agents.mdc` mentions `test_wdl_action.py`,
  which does not exist. Use `test_runner.py`.
