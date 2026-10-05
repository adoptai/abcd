# Capture rules — the mistakes that cost a whole recording

Condensed from the NoUI toolkit reference (`cli/noui/README.md`). Each rule exists because
it was broken on a real build.

## Describe the goal, not the clicks

You have not seen the app. Any control you name is a guess, and the human will follow it.

- Good: "Download the annual statement for the last financial year."
- Bad: "Click the PDF toggle, then DOWNLOAD. Don't click View Statement."

A real build failed this way: the instruction prescribed a path that only reached the current
month and forbade the one that worked. The capture missed the download entirely and nobody
noticed until run time.

- **Never tell the human to avoid part of the app.** You cannot know which link is the route.
- **Say to keep going until the thing actually happens** — the file lands, the confirmation
  renders. A recording that stops one step short compiles into a skill that stops one step short.
- **Warn about what breaks a capture**: stay in the same window, do not reload or close it,
  let slow pages (often a hand-off to a second host) finish, and click "Finish & export" only
  once the goal is complete.
- **Ask rather than assume.** If the route is genuinely ambiguous, ask how they normally do it
  and repeat it back as *their* description.

## Two sessions, two sign-ins — never confuse them

| | Recording session | Runtime session |
|---|---|---|
| What | a human drives a browser so NoUI can capture it | the profile's own session the installed skill drives |
| Created by | `capture_record` | Tabby, on demand |
| Viewer | VNC link with **"Finish & export"** (`?mode=recording`) | a sign-in card the platform renders |

A `login_required` (replay, verify, an installed skill) needs a **runtime** sign-in. Never answer
it with a new recording link. Get the sign-in by calling the skill's own operation; the platform
renders the card. Then **wait** — do not approve, install or re-run on a timer while waiting.

## Kind is a decision — confirm it

`capture_import` prints `KIND: browser-driven` or `KIND: replay (call_web_api)`. Auto-detection is
usually right, but nobody chose it. Say which and why, and ask. A capture requested as a
"browser based skill" once compiled into two replayed POSTs, and nothing flagged it.

## Never hand-write what the compiler emits

- A missing operation means the recording missed the evidence: **re-record**. A hand-written
  operation has no verified selector, no expectation and no parameter — it is exactly what
  fails in production while looking fine in review.
- Never substitute your own selector for a recorded one.
- A URL that contains a session token is not a bug. Browser skills never navigate to it; they
  replay the recorded click chain. "Fixing" it once destroyed a working skill.
- Browser skills carry `recording_bundle.json` + a provenance digest; the installer checks every
  locator was observed in the recording. Adding provenance fields by hand does not help.

## Banks and other financial portals

Always record with `--residential-proxy` (datacenter IPs get blocked or fraud-flagged). Set
`browser_policy.block_navigate` on the App Template for portals that lose the session on reload.
