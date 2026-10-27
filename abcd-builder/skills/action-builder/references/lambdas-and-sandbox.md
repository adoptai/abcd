# Lambdas and sandbox steps

The full guide is `prompts/guidelines/SANDBOX_LAMBDA_EXECUTION_GUIDE.md`. It covers the
decision flow, secrets, warm pools, image-pull latency and startup debugging. The
`pipeline-builder` skill uses this file too, since pipelines can call lambdas.

## Choose

- **Declarative WDL (default):** API orchestration, transforms, LLM prompts. No custom code.
- **`EXECUTE_LAMBDA`:** registered, reusable Python (or JavaScript) that runs in a
  first-party sandbox with platform resource access. Networking is restricted to
  platform FQDNs. It uses the audited `adopt-lambda-runtime` image and works in cloud
  and on-prem. Prefer this whenever custom code is needed.
- **`SANDBOX`:** ad-hoc container with any image and configurable network. It keeps a
  session across steps (`init` → `exec` … → `teardown`). Use it for shell scripts,
  package installs and internet access. **Cloud only, disabled on-prem.**

A package missing from the lambda runtime means a runtime-image rebuild (a platform
change). A `pip install` at run time is not the answer.

## Lambda workspace

Lambdas live in `workspaces/<env>/lambdas/<name>/`, shared across agents rather than
placed under an agent. A single `metadata.json` holds the config and the remote link
(`lambda_id`, `entry_point`, `runtime_image`, `timeout_seconds`, `cpu_limit`,
`memory_limit`, `resource_permissions`). `agent checkout` downloads the lambdas that
the WDL `EXECUTE_LAMBDA` steps reference.

```bash
python cli/manage_lambda.py --create my-lambda [--language python|javascript] [--agent acme-agent]
# edit workspaces/<env>/lambdas/my-lambda/script.py
python cli/save_lambda.py my-lambda [--dry-run]                 # upload
python cli/test_lambda.py my-lambda --compile                   # validate only
python cli/test_lambda.py my-lambda --input '{"key": "value"}'  # or --input-file f.json, or --all (test_cases/)
python cli/lambda_logs.py my-lambda [--limit 5]                 # recent executions
python cli/lambda_logs.py my-lambda --execution-id <id>         # full log
python cli/manage_lambda.py --update my-lambda --timeout 120 --memory 1024 --cpu 512
python cli/manage_lambda.py --list | --show my-lambda [--local-only] | --delete my-lambda
```

`manage_lambda.py`, `save_lambda.py`, `test_lambda.py` and `lambda_logs.py` all accept
`--env/-e`.

## WDL

```json
{"id": "runAnalysis", "operation": "EXECUTE_LAMBDA", "lambda_name": "data-analyzer",
 "input": "previousStep", "env": {"DEBUG": "true"}}
```

```json
[
  {"id": "setupEnv", "operation": "SANDBOX", "action": "init", "image": "python:3.11-slim",
   "upload_files": [{"path": "/workspace/script.py", "content": "..."}]},
  {"id": "runScript", "operation": "SANDBOX", "action": "exec", "command": "python /workspace/script.py"},
  {"id": "cleanup", "operation": "SANDBOX", "action": "teardown"}
]
```

## Secrets

Never put a literal secret, token or sensitive base URL in `widdle.json`. WDL is
versioned, synced and shown in traces.

- `{security_params.NAME}`: credentials, resolved at runtime from the encrypted
  store and scrubbed from logs.
- `{workflow_arguments.NAME}`: non-secret caller inputs. Declare them in `required_inputs`.
- `{stepId}` / `{stepId.field}`: outputs of earlier steps.

## Slow or failing starts (triage order)

Check these in order: image pull or registry 429, then cluster capacity, then the
warm-pool policy dropping the request, then a missing FQDN in lambda networking,
then server-proxy health. See §3–4 of the guide before you change pool or image
settings.
