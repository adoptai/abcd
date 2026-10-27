# The replay gate (browser skills)

A browser skill installs only after a member has watched it run. The installer — locally
(`activate_install`) and on the harness (`install_skill`) — refuses one without an approved
replay whose fingerprint matches the operations on disk.

## The loop

1. `verify_replay ws:skills/<id> --profile-slug <slug>` replays the compiled draft against the
   profile's own live session and writes `replay_report.json`. It never approves itself.
   - Exit 2: no signed-in session. Show the runtime sign-in card (call the skill's operation;
     the platform renders it), say you are waiting, and **stop**. Do not approve, install or
     retry on a timer.
2. Show the member the report. Where a `skill-replay` card is available the host renders it;
   otherwise present it yourself. For each operation:
   - did it **reach its goal**? A download whose steps all passed but that produced no file did NOT.
   - which steps were **blocked**, and the error.
   - which steps were **improvised** (amendments) — nobody has verified those.
   - anything **held for approval** (moves money, irreversible, or never touched by the human).
3. Ask whether it looks right, or which step to change.
4. Approve only on their yes: `verify_approve ws:skills/<id>`. It refuses when a goal was not
   reached (`--accept-failing OPERATION` exists for a member who explicitly accepts that).

## Fingerprints

- The approval is tied to a fingerprint of the replayed plan (operation names, kinds, steps,
  parameter names/defaults). Amend, recompile or **rename** an operation and the approval no
  longer matches. Replay and approve again.
- Descriptions are excluded, so rewording never invalidates an approval.
- The provenance steps digest in `manifest.json` excludes names *and* descriptions. That is
  why `generalize` may rename/reword a browser skill but nothing else.

## Amendments

When a recorded locator no longer resolves at replay, `verify_replay --amend JSON` records the
working control in `amendments.json` — kept apart from the recorded steps. Members approve
amendments explicitly (`verify_approve --approve-amendment ID`). An amendment only counts if
the replay actually exercised it.

## One replay

A replay that reached its goal has proved what a second would. A repeat re-drives the member's
live account, can trip rate limits or one-per-day export caps, and reads as distrust. Replay
again only when the member asked, or something changed after a failed run.
