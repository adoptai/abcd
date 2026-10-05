# Diagnose, fix, roll back

Deep references: `prompts/system/DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md` and
`prompts/guidelines/WDL_ISSUE_PATTERNS.md`.

All diagnostic scripts use the active environment's credentials and a per-env cache
in `workspaces/<env>/.cache/`. Run `python cli/workspace.py env list` first so you
diagnose the right client.

## Loop

```bash
# 1. scan everything (or one tool)
python cli/diagnose_and_fix.py --scan --format llm --output diagnostics/report.json
python cli/diagnose_and_fix.py --tool-id <tool-id> --deep-scan
# 2. read the report, then write fixes.json (format below)
# 3. apply with tests (preview first)
python cli/diagnose_and_fix.py --apply-fixes fixes.json --dry-run
python cli/diagnose_and_fix.py --apply-fixes fixes.json --test-after-fix
# 4. roll back if needed
python cli/rollback_changes.py --list
python cli/rollback_changes.py --file diagnostics/rollback_<ts>.json --show
python cli/rollback_changes.py --file diagnostics/rollback_<ts>.json [--only-apis | --only-tools] -y
```

**Fix order (mandatory):** fix the API spec first (path, method, params), then every
tool WDL that uses it, then test each tool. If the API and the WDL drift apart, the
tool breaks again.

## Scripts

| Script | Use |
|---|---|
| `diagnose_and_fix.py` | `--scan`, `--format llm\|markdown`, `--output`, `--tool-id`, `--deep-scan`, `--apply-fixes FILE`, `--test-after-fix`, `--dry-run`, `--auto-approve`, `--force-refresh/-f`, `--interactive/-i` |
| `fix_and_test.py <tool_id>` | `--fix-file FILE`, `--test` / `--quick-test --prompt "..."`, `--publish`, `--auto-approve`, `--generate-instruction`, `--output`, `--dry-run` |
| `fetch_http_logs.py` | Platform HTTP logs, used to compare the URL, method and query that the WDL sends with real traffic: `--search`, `--fetch`, `--list`, `--inspect --path/--method/--host`, `--start-date/--end-date`, `--max-logs`, `--force` |
| `diagnose_apis.py` | `--scan`, `--api-id`, `--filter trailing_slash\|workflow_arguments\|query_params` |
| `inspect_tool_wdl.py` | `[tool_id] --search --api-id --all --output` |
| `fix_api_path.py` | `[api_id] --trailing-slash add\|remove`, `--path` (no cache needed), `--from-diagnostics`, `--dry-run` |
| `patch_tool_wdl.py <tool_id>` | `--apply-fix FILE`, `--api-change --old-path --new-path`, `--dry-run` |
| `rollback_changes.py` | `--file/-f`, `--list/-l`, `--show/-s`, `--only-apis`, `--only-tools`, `--auto-approve/-y` |

## Issue types

| Type | Meaning | Severity |
|---|---|---|
| `TRAILING_SLASH_MISMATCH` | API path and logged URL differ by a trailing slash | HIGH |
| `MISSING_WORKFLOW_ARGUMENTS` | URL uses `{param}` instead of `{workflow_arguments.param}` | HIGH |
| `MISSING_REQUIRED_INPUTS` | Param is referenced but not declared | HIGH |
| `MISSING_QUERY_PARAMETERS` | Logs show query params that the WDL lacks | MEDIUM |
| `INVALID_WDL_STRUCTURE` | Missing `id` or `operation`, and so on | HIGH |
| `METHOD_MISMATCH` | HTTP method differs from the logs | CRITICAL |

## `fixes.json`

```json
{
  "fixes": [
    {
      "issue_id": "issue-abc123",
      "tool_id": "tool-xyz",
      "tool_title": "get-organization",
      "action": "update_wdl",
      "changes_summary": "Trailing slash + workflow_arguments prefix",
      "new_wdl": [
        {"id": "metadata", "required_inputs": {"orgId": {"type": "string", "definition": "Organization ID"}}},
        {"id": "get_org", "operation": "REST", "method": "GET",
         "url": "/api/v1/organizations/{workflow_arguments.orgId}/",
         "canonical_api_endpoint": "/api/v1/organizations/{orgId}/"}
      ],
      "test_prompts": ["Get details for organization acme-corp"]
    }
  ]
}
```

## Debugging a single failing test

1. Re-run it with `--verbose` and open the newest file in `traces/`.
2. Find the first failing operation and trace its inputs backwards. See the data-flow
   tracing protocol in `WDL_ISSUE_PATTERNS.md`.
3. Common causes: JQ_FILTER `extract_all` default, a `[[...]]` double-nested array
   (needs another FIRST_ELEMENT), wrong `profiles_map` or security-param name, an
   expired session cookie in `adopt_profile.json` → `security_params`.
4. Edit, `--compile`, re-run. Save only when green.
