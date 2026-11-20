---
name: context-and-kg
description: "Work with the Builder's knowledge graph and call context for a client: pull the context bundle for an agent, add call transcripts, emails and process docs, write and link knowledge graph pages, record decisions, and check what a change would affect before a push. Agents propose and FDEs vet. Use when the user says pull the context, add this call transcript, add the SOP to context, write a KG page, link these in the knowledge graph, what does this change affect, record this decision, what do we know about this system, or update the knowledge graph."
---

# context-and-kg

The knowledge graph (KG) is markdown pages in the `builder-context` Doc Store plus typed nodes
and edges in tables. It answers "what do we know?" and "what does this change affect?".
Agents propose, FDEs vet: new pages start in `proposed/`.

Run from the root of an abcd checkout, in the client workspace.

## When to use

- Before building or changing an agent (get the context bundle).
- A call, email or SOP arrived and must become context.
- A fact, decision or API behaviour should be remembered for the next FDE.
- Before a push, to see what a change touches.

## Step 1 - pull the bundle

```bash
abcd context pull <agent> --env <env> --json
```

Downloads into `workspaces/<env>/context/` (ignored by git): the agent's page and neighbours one
or two edges out, its decisions and open items, its checks, metrics and criteria. Read it before
asking the member anything; ask only what it does not settle.

## Step 2 - add inputs

```bash
abcd context add calls <file> --client <client> --env <env> --json
abcd context add emails <file> --client <client> --env <env> --json
abcd context add process-docs <file> --client <client> --process <process> --env <env> --json
```

Files go to `context/calls`, `context/emails` or `context/process-docs` in `builder-context`.
Client material goes there and only there, never into the abcd repo. Use `--dry-run` first for
anything large.

## Step 3 - pages and links

```bash
abcd kg page <type> <name> --file page.md --env <env> --json
abcd kg link <type>:<from> uses <type>:<to> --env <env> --json
abcd kg impact <name> --env <env> --json
```

- Edge types: `uses`, `calls`, `owns`, `replaced_by`, `failed_on`, `tested_by`.
- New pages are proposed, not vetted. Tell the member which ones await their review.
- Run `kg impact` before pushing a shared skill: every dependent agent's gate will run.

## Decisions

Every answered question is saved as a decision (answer, who, source, status), never left only
in chat. Record the member's own words and say who answered.

## Rules

- **Approvals:** `--yes` only after the human said so. Vetting a page is the FDE's or SME's act,
  never the agent's.
- **Safety:** no client data in the abcd repo; no secrets in pages; case-specific expected
  values never go in a page that a blind run can read (use `context-gaps` for answers).
  Never commit to the default branch; never deploy-default for client work; BFF with the PAT
  only.

## Hand-offs

- Planning from the context: `build-plan`.
- A missing-context miss from an eval: `context-gaps`.
- NoUI output (APIs, skills) joins the KG here after `discover-and-plan`.
