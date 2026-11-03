# abcd, Agent Harness edition

The publishable artifact that lets an Agent Harness turn run abcd inside the sandbox as
the Builder's cloud runner (`docs/adopt-builder/PLAN.md` section 6). The abcd **source**
is this repo; this folder only packages it.

Contents:

- `SKILL.md`: the harness-facing skill (frontmatter `name: abcd`). Teaches the agent to
  stage and install the bundle, run `abcd cloud bootstrap`, drive abcd with `--json`, never
  print a secret, and leave approvals and the default branch alone.
- `build_bundle.py`: builds `abcd-bundle.zip` from git-tracked files only: `cli/` (NoUI
  included), `abcd-builder/`, `adopt-skill-review/`, `pyproject.toml`, `README.md`. It
  leaves out `workspaces/`, `tabby/`, tests and every dotfile or `.env`, and is
  deterministic.

## Build

```bash
python harness-skills/abcd/build_bundle.py
```

## Publish

Org tier of the **Builder home org only**, with that org's Admin PAT in the workspace
`.env`. **Never deploy to the default tier**: default skills are visible to every org and
this one is an FDE tool.

```bash
abcd skill push harness-skills/abcd --env <home-org-env> --replace \
    --aux abcd-bundle.zip=harness-skills/abcd/abcd-bundle.zip
```

The push lints the frontmatter and runs the content secret scan over `SKILL.md` and every
member of `abcd-bundle.zip` before anything is sent.

## Runtime prerequisites (home org)

Vault secrets (the platform strips `SANDBOX_` and injects them as env vars in every sandbox
of the org, so keep one home org per client):

| Vault secret | Env var in the sandbox | Notes |
|---|---|---|
| `SANDBOX_ABCD_PAT_CLIENT_ID` | `ABCD_PAT_CLIENT_ID` | Admin PAT for the client org |
| `SANDBOX_ABCD_PAT_SECRET` | `ABCD_PAT_SECRET` | |
| `SANDBOX_GITHUB_TOKEN` | `GITHUB_TOKEN` | Fine-grained, this client's workspace repo only; clone/fetch/push over HTTPS and the GitHub API (PRs, releases) |
| `SANDBOX_GITHUB_DEPLOY_KEY` | `GITHUB_DEPLOY_KEY` | Alternative to the token: a write deploy key (private key) for the one repo; git over SSH only, so pull requests and GitHub releases are flagged, not created |
| `SANDBOX_ABCD_WEBUI_ENDPOINT` | `ABCD_WEBUI_ENDPOINT` | Optional; else `ADOPT_APP_BASE_URL` |

Set one of the two GitHub secrets; the token wins when both exist. The harness also injects `ADOPT_APP_BASE_URL` and `ADOPT_CONVERSATION_ID`.

The home org needs a workstream `abcd-<env>` whose Doc Store holds one file,
`abcd-workspace.json`:

```json
{"env": "acme", "repo": "<owner>/<workspace-repo>", "ref": "<branch, tag or sha>"}
```

The harness copies it into the sandbox, so a run knows its workspace without being told.
