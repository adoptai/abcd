# Where files go, and where the harness actually puts them

Pinned to `adoptai-workflows` origin/dev @ `63980995` (2026-09-10).

This is the highest-value section in the whole review, because getting it wrong produces
**silent wrong output** rather than an error, and it has already happened to more than one
team independently.

## The staging matrix

A skill directory holds `SKILL.md` plus any of `scripts/`, `assets/`, `references/`. What
happens when the agent calls `read_skill_file(skill, path)` depends entirely on what kind of
file it is:

| What it is | Where it ends up | How |
|---|---|---|
| `scripts/*.py` or `scripts/*.sh` | **On disk** at `/workspace/skill_assets/<skill>/<rel>`, plus a ready `python …` / `sh …` command in the tool result | `is_skill_script_rel` — the path must start `scripts/` **and** end `.py`/`.sh` |
| **Any binary file, in any directory** | **On disk**, same tree | the reader raises `SkillFileBinaryError` and the harness materialises it |
| Anything under `assets/` | **On disk**, same tree | auto co-staged whenever **any script from that skill** is staged |
| **Any other text file — including all of `references/`** | **Inline only.** Returned as text into the model's context. **Never written to the filesystem.** | the plain text path |

"Binary" is a sniff, not an extension check: a null byte anywhere in the first 256 KiB, or
invalid UTF-8 away from the truncation boundary. So a `.pdf` or `.xlsx` under `references/`
lands on disk; a `.json`, `.md`, `.csv` or `.txt` under `references/` does not.

### The rule that follows

> If a **script** must open it, it goes in `assets/`.
> If the **agent** must read it, it goes in `references/`.
> If both need it, put it in `assets/` and describe it in `SKILL.md`.

## The failure this causes

A script that defaults to its own skill directory plus `references/`:

```python
default_map = Path(__file__).resolve().parent.parent / "references" / "lookup.json"
```

…finds nothing in the sandbox. `references/` was returned into the model's context, not
written to disk.

Two ways that plays out, and the second is worse:

- The script **raises**, and you lose steps diagnosing a missing file that looks like it
  should be there.
- The script **falls back to a built-in default** and produces a complete, plausible,
  silently wrong result. This is a live pattern: one production skill has six such
  resolutions across two skills sharing a `common.py`, one of them documented as
  "`references/…json` **if present**" — i.e. it silently substitutes its own map.

This is the same shape as an incident where a script wrote one spelling of a key while its
consumer read another: nothing errored, the missing key read as `None`, became `0`, and produced a
complete, plausible output with zeros in every column that depended on it. **A missing input
that defaults instead of refusing is the most expensive bug class on this platform.**

One team hit it, moved three JSON files from `references/` to `assets/`, and wrote the
reason into their skill. Another team never got the memo and still has it. When you review,
check this first.

## Sibling modules are NOT co-staged

Co-staging covers `assets/`. It does **not** cover other scripts.

If `verify.py` does `from common import x`, staging `verify.py` does not bring `common.py`.
Every importing script then dies on `ImportError`. A real run watched its verification step
"find 0", spent dozens of calls diagnosing it, re-staged the module and re-ran everything —
which is how that turn ran out of tool iterations.

So a shared module must be **named explicitly in the staging batch**, in the same step as
the scripts that import it. This is the single most commonly forgotten line in a harness
skill.

## How co-staging behaves (so you can reason about cost)

When a script is staged, the harness lists the skill's `assets/` prefix, runs one `find`
presence probe over the destination, and batch-transfers only what is missing through a
single `getmany` call. Then it `chmod 444`s the files and `555`s their parent directories.

- Already-present assets are **skipped** — re-staging the same bytes was previously the
  dominant cost of the call (22–26s per call, one turn repeating the same four assets ten
  times).
- The transfer is **one** round trip regardless of asset count. This used to be ~4 round
  trips *per asset*: 166s for a 38-asset skill, linear in count. Now flat.
- Staged files are read-only. A script must not try to write next to them; write to
  `/workspace/...` instead.
- Concurrent `read_skill_file` calls for the same skill are single-flighted, so a parallel
  batch does not re-stage or race.

Practical consequence: **staging cost is now flat in asset count**, so shipping assets is
cheap. Shipping *readable* files the agent doesn't need is not — see below.

## Size caps — there are two, and they differ by 200×

| Cap | Value | What happens |
|---|---|---|
| Per-file **read** | 256 KiB | Text is truncated with a marker appended. Silent in effect — whatever rule lived past the cap is simply absent. |
| Per-file read, for a **script** | 256 KiB | **Refused, not truncated.** `read_skill_file` returns an error rather than stage truncated source, so the script is unrunnable. |
| **Upload**, combined | 50 MB (`SKILL.md` + all aux) | 422 at upload |
| Aux files per skill | 100 | 422 |
| Skills per plugin | 50 | 422 |

So a 5 MB reference doc uploads fine and then gets silently cut when the agent reads it.
Keep *readable* files well under 256 KiB. Large data belongs in `assets/` read by a script
(no cap on a script's own file reads) or in object storage the script fetches.

Ask `GET /api/v1/agent-harness/limits` for canonical values rather than hardcoding.

## Ship only runtime files

`fetch_skill` appends an **aux manifest** to the body, so the agent sees a menu of every
file in the bundle. Tests, caches, stale archives and old manifests are things it may decide
to read. One bundle shipped six test files (~59 KB) and eleven stale manifests. Keep tests
outside the uploaded directory.

## Paths, and the portable form

- `read_skill_file` accepts a bare relative path (`scripts/foo.py`) or the portable
  `skill:/<name>/<path>` form, which it rewrites.
- `..` segments are blocked.
- `SKILL.md` is reserved — fetch it with `fetch_skill`, not `read_skill_file`.
- A skill can stage files from **another** skill by name; nothing scopes it to the bound
  skill except a pipeline `skills_allowlist`. That is how a multi-skill plugin shares a
  shared module — and also why those skills end up sharing one turn budget.
- Inside a plugin, the frontmatter `name` need **not** equal the directory slug: storage
  keys come from the directory, but the handle the agent uses is the frontmatter name.
  Mismatches are legal and a routine source of confusion.

## Org copies shadow defaults, totally

Resolution order for a skill name:

1. `skills/source=org/org_id=<org>/skill_name=<name>/`
2. `plugins/source=org/org_id=<org>/plugin_name=<p>/skills/<name>/`
3. `skills/source=default/skill_name=<name>/`
4. `plugins/source=default/plugin_name=<p>/skills/<name>/`

An org copy replaces the default **entirely** — `SKILL.md` *and* every aux file. You never
get a half-stitched skill. The trap: updating the default has **no effect** for an org that
has its own copy. Before telling someone their fix didn't deploy, check which tier the
target org actually resolves.
