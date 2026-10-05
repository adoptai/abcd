# NoUI — Agent Harness edition

This folder is the **publishable artifact** that lets an Agent Harness turn run the
NoUI toolkit *inside the sandbox* to author new skills (record → compile → generalize →
install). The toolkit **source** is `cli/noui/` (the same code the local
`discover-and-plan` / `skill-builder` skills drive through `cli/noui_workspace.py`).

Contents:

- `SKILL.md` — the harness-facing skill (frontmatter `name: noui`, the only skill with
  that name). Teaches the agent to stage + install the toolkit and drive capture /
  compile / generalize. Auth is handled by the harness control-plane broker (no Tabby
  credentials in the sandbox).
- `build_bundle.py` — builds `noui-bundle.zip` (gitignored) from `cli/noui/`: the zip root
  is the bundle root, so the agent can unzip it and `pip install -e .`. Excludes `.venv/`,
  `workbench/`, caches and any `.env`.
- `deploy_default_skill.py` — standalone (stdlib-only) CI deployer to the platform
  **default** ("Built-in") tier; used by `.github/workflows/deploy-skill.yml`.

## Build

```bash
python harness-skills/noui/build_bundle.py
```

## Publish

Default ("Built-in") tier — what CI does, `@adopt.ai` service-account PAT:

```bash
python cli/harness_skill.py deploy-default harness-skills/noui --env <env> \
    --aux noui-bundle.zip=harness-skills/noui/noui-bundle.zip --bundle-version "$(git rev-parse --short HEAD)"
# or, exactly as CI runs it:
DRY_RUN=1 SKILL_NAME=noui python harness-skills/noui/deploy_default_skill.py
```

Org tier (an org-admin PAT in the workspace `.env`):

```bash
python cli/harness_skill.py push harness-skills/noui --env <env> --replace \
    --aux noui-bundle.zip=harness-skills/noui/noui-bundle.zip
```

Both paths lint the frontmatter and run the content secret scan over `SKILL.md` and
every member of `noui-bundle.zip` before anything is sent.

## Runtime prerequisites (harness side)

The sandbox must be seeded with the broker env: `TABBY_API_URL=<broker>`,
`NOUI_TABBY_AUTH_MODE=broker`, `NOUI_BROKER_TOKEN=<capability>` — which requires the
worker config `AGENT_HARNESS_NOUI_BROKER_SANDBOX_URL` and the broker process
(`python -m src.workflows.agent_harness.noui_broker`, adoptai-workflows) running.

The browser-skill install gate re-computes provenance/approval digests in
adoptai-workflows; the shared test vectors live in `tests/noui/test_provenance.py` and
`tests/noui/test_install_gate.py` and must stay identical to
`adoptai-workflows tests/agent_harness/test_web_browser_dispatch.py`.
