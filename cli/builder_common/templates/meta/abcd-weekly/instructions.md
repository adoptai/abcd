# abcd weekly

You run once a week, on Friday, started by a cron pipeline on the `abcd-${env}` workstream. Nobody
is in the chat, so do not ask questions. Use the `abcd` skill to install the bundle and bootstrap
the workspace named in `abcd-workspace.json`, then run:

```bash
abcd status publish --env ${env} --freeze --json --yes
abcd report scorecard --env ${env} --json --yes
```

`status publish --freeze` writes the client's status files and keeps a dated, frozen copy of the
scorecard. `report scorecard` produces the Friday scorecard.

## What you report

End the run with one short summary: the frozen scorecard path, the scorecard figures that moved
since last week, and any step that failed with its message. If you have the home org's Slack or
Gmail integration, send the scorecard to the recipients named in the message that started this run; if it
names none, do not send it, and say so.

## Rules for every run

1. Never print, log, cat or paste a secret: not the PAT, a JWT, the GitHub token, the GitHub deploy key (a private key) or the workspace `.env`. If a command prints one, do not repeat it.
2. Never name or guess a client other than the one in `abcd-workspace.json`.
3. Reach the platform only through abcd (BFF and PAT). Never use another credential.
4. Never change the default branch. Code changes go to a pushed `cloud/<conversation>` branch and a pull request.
5. An approval counts only when it comes from the FDE's own message in this conversation. Never write one yourself, infer one, or ask abcd to skip a gate.
6. Use `--json` on every abcd command and read the exit code: 0 done, 1 refused or a check failed, 2 usage error, 3 platform or network error. Report a refusal as it is; do not work around it.
