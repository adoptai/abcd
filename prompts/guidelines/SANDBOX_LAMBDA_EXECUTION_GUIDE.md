# Sandbox & Lambda Execution Guide

**Read this in full when** a task involves: choosing `EXECUTE_LAMBDA` vs `SANDBOX`,
authoring/registering a lambda, passing secrets or `.env` values into a WDL,
on-prem constraints, or debugging slow/failed sandbox/lambda startup
(`ContainerCreating`, `ImagePullBackOff`, `Pending`, network-policy denials,
connectivity errors).

This is the single source of truth for execution-mode decisions. Other prompts
(`agents.md`, `CURSOR_WDL_WORKFLOW_SYSTEM_PROMPT.md`, `DIAGNOSE_AND_FIX_SYSTEM_PROMPT.md`,
`WDL_ISSUE_PATTERNS.md` Category 7) link here instead of duplicating it.

---

## 1. Decision: `EXECUTE_LAMBDA` vs `SANDBOX`

**Default to `EXECUTE_LAMBDA`.** Only choose `SANDBOX` when you genuinely need a
freedom lambda doesn't grant.

```
Does the workflow need to run on-prem?
  └─ YES → EXECUTE_LAMBDA. (SANDBOX is disabled on-prem. No exceptions.)
  └─ NO ↓
Does it need a custom image, arbitrary internet egress, runtime-installed
deps (pip/apt/npm), ad-hoc shell, or dynamically-generated LLM code?
  └─ NO  → EXECUTE_LAMBDA (reusable, audited, first-party SDK access).
  └─ YES → SANDBOX (cloud-only, free-flowing, persistent session).
```

| | `EXECUTE_LAMBDA` | `SANDBOX` |
|---|---|---|
| Image | `adopt-lambda-runtime` (audited) | Any image **except** `adopt-lambda-runtime` |
| New deps at runtime | ❌ must already be in `ProjectA3/pyproject.toml` | ✅ `pip`/`apt`/`npm` over the internet |
| Network | deny-all + platform FQDN allowlist (platform-managed) | full internet |
| First-party DB/S3/Redis/API | ✅ via `adopt_sdk` | possible, but you wire it |
| Ad-hoc shell / dynamic code | entry-point only | ✅ primary use case |
| Persistent multi-step session | no | ✅ init → exec → exec → teardown |
| On-prem | ✅ only option | ❌ disabled |

### `EXECUTE_LAMBDA` — the locked-down path
- Registered, reusable Python. Runs in a first-party sandbox built from the
  `adopt-lambda-runtime` image, which is built from `ProjectA3/pyproject.toml` —
  **the dependency set is fixed and audited**. You **cannot** install a new
  package at runtime. If a lambda needs a library, it must be added to
  `pyproject.toml` and the runtime image rebuilt — that is a platform change, not
  something you do in the handler.
- Reaches platform resources (SingleStore DB, S3, Redis, `adoptwebui` API)
  through the `adopt_sdk` overlay with a scoped JWT.
- Network is platform-managed: `defaultAction=deny` + an FQDN allowlist from
  `PLATFORM_FQDNS`. The lambda code cannot widen it. Reaching a non-allowlisted
  host fails with `Network policy denied`.
- **This is the path for current and future on-demand / on-prem clients** — it is
  the only execution capability available on-prem and adds zero audit surface.

### `SANDBOX` — the free-flowing path
- Ad-hoc container execution with **any image** (except the reserved
  `adopt-lambda-runtime`), **full internet egress**, **direct command
  execution**, and a **persistent session** across steps (init → exec → exec →
  teardown).
- Best fit for dynamically-generated / LLM-authored code, one-off scripts, and
  anything that needs to install dependencies or hit the open internet.
- Can also run the same code a lambda would (it's a superset) — use it when you
  need a lambda-style snippet **plus** one of the sandbox freedoms.
- **Cloud-only.** Never put `SANDBOX` in a workflow that must ship on-prem — it is
  rejected with `SANDBOX operations are not available in on-premises deployments`.

> Cross-ref: the feature matrix and lambda workspace layout also live in
> `agents.md` §"Lambda & Sandbox Operations".

---

## 2. Secrets & `.env` values — use param replacement, never hardcode

**Rule: no secret, API key, token, password, or sensitive base URL is ever
written literally into `widdle.json`.** WDL is saved, versioned, synced to entity
stores, and shown in traces — a hardcoded secret leaks into all of them and is
**not** scrubbed from logs.

Use the platform's param-replacement (token-manager) seam. Three placeholder
namespaces:

- `{security_params.NAME}` — **secrets / credentials.** Resolved at runtime from
  the platform's encrypted credential store (connector rows are fetched and
  decrypted). This namespace is **stripped from logs** automatically. A value that
  resolves to `None` leaves the literal placeholder in place (not blank).
- `{workflow_arguments.NAME}` — dynamic, non-secret caller inputs (declare each in
  `required_inputs`).
- `{stepId}` / `{stepId.field}` — outputs of an earlier step.

### Do / Don't

```jsonc
// ❌ secret baked into the WDL — leaks into version history, traces, entity store
{ "headers": { "Authorization": "Bearer sk_live_8f3c...REDACTED" } }

// ✅ token-manager placeholder — resolved at runtime, scrubbed from logs
{ "headers": { "Authorization": "Bearer {security_params.vendor_api_key}" } }
```

```jsonc
// ❌ environment value hardcoded into the WDL
{ "base_url": "https://acme-prod.internal.example.com" }

// ✅ supplied by the caller / profile
{ "base_url": "{workflow_arguments.base_url}" }
```

**How `.env` relates to this:** the environment's `.env` / `adopt_profile.json`
feeds the platform credential store and profile. The WDL only references the
**name** of the value (`{security_params.x}`), never the value itself. If you find
a `.env` value copied into a `widdle.json`, that's a bug — replace it with a
placeholder. (See `WDL_ISSUE_PATTERNS.md` Category 6 for `profiles_map` /
security-parameter naming issues.)

---

## 3. Warm pools, image pull latency & registry limits

Both lambdas and sandboxes run as Kubernetes pods. **Cold-starting a pod can be
slow**, and warm pools hide that latency. You need this section mostly for
**debugging** — but also when deciding whether a high-frequency workload warrants
a pool.

### Why startup is slow
- **Image cold-pull.** A fresh node has the image in no cache. `adopt-lambda-runtime`
  bundles the full `pyproject.toml` dependency set (pandas/numpy/scikit-learn,
  ~160 packages) — it is **large**, and pulling it cold dominates first-run
  latency.
- **Public registry rate limits.** Images on **Docker Hub** or **GitHub Container
  Registry (`ghcr.io`)** have anonymous/free pull rate limits. Under load or many
  simultaneous pulls you hit `429 toomanyrequests`, and pods stall in
  `ImagePullBackOff`. Fix: private registry (ECR) or a pull-through cache, and/or
  pre-pull onto warm-pool nodes.
- **Capacity.** If the cluster is full, the pod sits `Pending` until a node frees
  up; spot evictions can extend this to minutes.

### What a warm pool is
A set of **pre-started pods from a pre-pulled image** kept ready to claim instead
of cold-creating. The image→pool mapping lives in `LAMBDA_RUNTIME_POOL_REGISTRY`
(JSON: full `repo:tag` → pool name). A request whose final image matches gets a
`poolRef` and claims a warm pod; a miss falls through to cold-create.

### ⚠️ Footgun you must know when reasoning about pools
On the warm-pool path **only entrypoint and env are honored** — the caller's
**resources and network policy are silently dropped**:
- A request for `4 CPU / 8Gi` that matches a `1 CPU / 2Gi` pool runs in the
  smaller pod (may OOM/throttle, no warning).
- The deny-all + FQDN allowlist a lambda normally gets is **not applied** on the
  pool path; the warm pod uses the pool template's network config. Pool network
  policy must be set **pool-wide** in chart values. Workloads needing a custom
  per-call allowlist must **opt out of the pool** (image left out of the registry
  → cold-create).
- The SDK logs `warm-pool routing to '<pool>' ignored per-call network_policy`
  when it drops a policy — search for it when egress behaves unexpectedly.

### When to set up a warm pool
High-frequency, predictable workload with a **stable, pool-wide egress shape**
(e.g. all platform lambdas share the same `PLATFORM_FQDNS` allowlist). Size each
tier's `bufferMin` to **concurrent peak**, not total volume — every warm pod costs
money whether used or not. Don't pool the long tail of custom-allowlist workloads.

---

## 4. Debugging startup & connectivity

Triage **in this order**:

1. **Image pull** — pod `ContainerCreating`/`ImagePullBackOff`? Look for `429
   toomanyrequests` from Docker Hub/`ghcr.io`; verify image exists and node can
   reach the registry. → private registry / pre-pull.
2. **Capacity** — pod `Pending` with `Insufficient cpu/memory`? Cluster
   capacity/scheduling, not the image. Spot evictions can leave nodes `Pending`
   for minutes.
3. **Warm-pool policy drop** — `Network policy denied` for a host that *should* be
   reachable, or wrong pod size? You may be on a pool route where the per-call
   policy/resources were dropped (§3). Check for the SDK WARN.
4. **Genuine FQDN miss** — host simply isn't on `PLATFORM_FQDNS`. Lambdas can only
   reach platform services; if internet is required, the workflow needs `SANDBOX`
   (cloud only).
5. **Server-proxy health** — bash exec and keepalive route worker →
   `opensandbox-server` (reverse proxy) → sandbox pod (`OPENSANDBOX_USE_SERVER_PROXY`
   defaults to True). So the **server pod's** health is in the request path. Its
   outages surface as:
   - `Network connectivity error: All connection attempts failed` (no ready server
     endpoint)
   - `Server disconnected without sending a response` (connection cut mid-response
     — the eviction moment)
   - `the sandbox was briefly unreachable … still alive` (server→pod health-check
     timeout; this one backs off/escalates, the two above are passed raw)

   A spot eviction of `opensandbox-server` with no `priorityClassName` can leave
   it `Pending` for minutes → that's the worker-visible outage window.

**ABCD-specific checks** (see also `WDL_ISSUE_PATTERNS.md` Category 7):
- `Sandbox creation failed` → check `OPENSANDBOX_DOMAIN` in the active env's `.env`.
- `Lambda not found` / `Entry point not found` → `python cli/manage_lambda.py --list` /
  `--show <name>`; re-upload with `save_lambda.py <name>`.
- `Lambda timeout` → `manage_lambda.py --update <name> --timeout 300`.
- `Image X is reserved for first-party use` → don't use `adopt-lambda-runtime` as a
  `SANDBOX` image; it's reserved for `EXECUTE_LAMBDA`.

---

## 5. Authoring checklist

- [ ] Default to `EXECUTE_LAMBDA`; use `SANDBOX` only for custom
      image/network/commands or dynamic LLM code — never on-prem.
- [ ] Lambda needs a package not in `pyproject.toml`? That's a runtime-image
      rebuild (platform change), not a runtime `pip install`.
- [ ] Zero hardcoded secrets/keys/tokens/sensitive URLs in `widdle.json` — route
      secrets through `{security_params.X}`, dynamic inputs through
      `{workflow_arguments.X}`.
- [ ] Slow startup? Triage: image pull / registry 429 → capacity → warm-pool
      policy drop → FQDN miss → server-proxy health.
