# Uber agents (PROMPT_AND_TOOLS_AGENT)

An uber agent is an action whose WDL wraps a `PROMPT_AND_TOOLS_AGENT` step. The model
chooses among published sub-actions, which are listed by remote ID in `action_ids`.
The full guide is `prompts/system/UBER_AGENT_PROMPT.md`, and the starter WDL is
`prompts/templates/uber_agent_template.json`.

## Shape

```json
[
  {"id": "uberAgent", "operation": "PROMPT_AND_TOOLS_AGENT",
   "model_string": "<model id from the operation docs>",
   "action_ids": ["<sub-action-1 remote id>", "<sub-action-2 remote id>"],
   "system_prompt": "You are the Acme billing assistant.\n\nTools:\n1. get-invoice: ...\n2. list-invoices: ...\n\nPick the tool, ask for missing required inputs, then answer."},
  {"id": "extractAgentMessage", "operation": "EXTRACT", "input": "uberAgent", "field": "message"},
  {"id": "outputAgentResponse", "operation": "OUTPUT_TEXT", "format_string": "{}",
   "values": ["extractAgentMessage"], "raw": true},
  {"required_inputs": []}
]
```

Before you choose `model_string` and the other fields, fetch
`PROMPT_AND_TOOLS_AGENT_OPERATION_DESCRIPTION.md` from the widdle docs. Keep the
system prompt short and specific. Long prompts cost latency inside the CloudFront
budget (see `testing.md`).

## Build order

```bash
python cli/workspace.py agent create --id acme-agent --name "Acme Agent"
python cli/manage_wdl_action.py --create -r req.md -t "get-invoice" --agent acme-agent
# ...edit each actions/<sub>/widdle.json, write its test_cases/...
```

1. **Tier 1, sub-actions alone:** `python cli/test_runner.py <sub> --all` for each one.
   Fix every failure before you move on.
2. **Tier 2, agent with inline sub-actions:** `python cli/test_runner.py acme-agent --test test_1.json --inline`.
   You can limit it to some sub-actions with `--inline get-invoice,list-invoices`.
   Nothing needs to be saved for this tier. Iterate on the system prompt here.
3. **Save and publish everything:**
   ```bash
   python cli/save_wdl_draft.py --agent acme-agent --force     # agent + all sub-actions
   python cli/save_wdl_draft.py acme-agent                     # 2nd save of the AGENT (double-save rule)
   python cli/publish_wdl_action.py --agent acme-agent --yes   # sub-actions first, then agent
   python cli/deployment_rules.py <sub> --enable-tool-mode     # every sub-action
   ```
   To register a sub-action that was published separately:
   `python cli/workspace.py agent add-subaction --agent acme-agent --action get-invoice --remote-id <id> --title get-invoice --description "..."`.
4. **Tier 3, real platform `action_ids`:** `python cli/test_runner.py acme-agent --test test_1.json`.
5. Optional production check through the Chrome extension: `ce_test.py` (see `SKILL.md` §8).

Other test modes: `--via-agent --subaction <name>` runs a sub-action through its
parent agent. `--multi-turn test_1.json test_2.json --inline` turns separate test
files into one conversation.

## Double-save rule

For an agent (`PROMPT_AND_TOOLS_AGENT`), one `save_wdl_draft.py` leaves the new
version in `draft`. You need a **second** save to move it to `pending_approval`.
`publish_wdl_action.py` only approves `pending_approval` versions, and otherwise
fails with "No pending_approval version to publish". So the order is save, save,
publish. Regular actions need one save.

## Sub-action requirements

1. **Published**, for Tier 3 and production. A draft can't be a platform-side sub-tool.
   During development, `--inline` avoids that.
2. **Tool mode enabled**: `deployment_rules.py <sub> --enable-tool-mode`.
3. **Valid, unique tool names.** The model API needs `^[a-zA-Z0-9_-]{1,128}$`. The
   platform derives a tool name from **both** the `title` and the `statement` of each
   sub-action, so both must match and both must be **unique across all of the agent's
   sub-actions**. Good: `get-invoice`, `list_invoices`. Bad: `Get Invoice`,
   `list (open)`.
   - Local check: `python cli/validate.py <sub> --orchestrator [--auto-fix]`.
   - Remote fix: `python cli/patch_action_metadata.py <sub> --title get-invoice --statement "..."`,
     or `--agent acme-agent` to sync every sub-action from its metadata.
4. **`required_inputs` must be a list of JSON strings**:
   `"required_inputs": ["{\"invoice_id\": {\"type\": \"string\", \"description\": \"Invoice ID\"}}"]`.
   A dict does not work here. Fix it with `python cli/validate.py <sub> --auto-fix`.

## Inline prefix

`--inline` rewrites the agent's `action_ids` to local sub-action keys and sends the
sub-action WDLs inline (`inline_actions` in the `/run-wdl` payload). The inline key
ends up as a tool name, so it must pass the same regex. That means the prefix has to
be **`inline__<name>`**, because a colon is invalid. On this branch,
`cli/test_runner.py` (`build_inline_actions`) still builds `inline::<name>`. If an
`--inline` run fails with a tool-name or invalid-characters error, this mismatch is
the cause. Report it, and don't patch payloads by hand.

## Chrome-extension visibility

The extension lists agents from the platform's entity store. That store is synced
when a version is approved, and the sync rejects actions that have empty metadata
fields. CLI-created actions often have them. Symptom: publish succeeds but the agent
or tool is not visible in the extension. Push the metadata with
`patch_action_metadata.py` (title, description, statement), make sure a statement
exists (`save_wdl_draft.py` warns when it doesn't), then save and publish again. This
branch has no CLI for the other fields (example prompts, tags, WDL summary,
confirmation flag). Escalate to the user.

## Common errors

| Error | Fix |
|---|---|
| `tool names have invalid characters` | `validate.py <id> --orchestrator --auto-fix`, then `patch_action_metadata.py` |
| `Tool not found` | The sub-action isn't published or isn't in `action_ids`. Publish it, then `agent add-subaction` |
| `Tool mode not enabled` | `deployment_rules.py <sub> --enable-tool-mode` |
| `required_inputs should be a list` | `validate.py <id> --auto-fix` |
| `Action not found` | `reconnect.py <id> --search` |
| 504 after about 120 s | Too many tool calls in one turn. See `testing.md` |
