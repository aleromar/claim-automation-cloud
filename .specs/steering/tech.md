# Tech / Architecture — Claim Automation (Cloud)

> Systems design in terms of roles and building blocks so the pure logic stays portable.
> **Azure is the chosen provider (D10)** — concrete Azure services are authoritative and
> owned by `../azure-implementation.md`. GCP/AWS are named only as reference points.

## Building blocks (from the original app → cloud)

The original app decomposes into blocks. Blocks marked **pure logic** port over
unchanged from `../claim_automation`; the rest are re-platformed.

| # | Block | Original (laptop) | Cloud target |
|---|-------|-------------------|--------------|
| 1 | Trigger / ingestion | 30s polling loop | **30-min scheduled trigger** + on-demand HTTP trigger |
| 2 | Gmail auth | interactive `run_local_server` + `token.json` | OAuth broker in dashboard → refresh token in secret store |
| 3 | Trello auth | static API key + token (query params) | same creds, stored in secret store |
| 4 | Compute runtime | detached subprocess | serverless functions (timer + HTTP) |
| 5 | Email parsing *(pure logic)* | `claim_data.py` (MIME + HTML sniff) | port unchanged |
| 6 | Classification *(pure logic)* | `ClaimType.from_subject()` | port unchanged |
| 7 | PDF generation *(pure logic)* | `pdf_gen.py` + `membretes/*.png` | port; assets bundled or in object storage |
| 8 | Trello integration *(pure logic)* | `trello.py` REST calls | port unchanged |
| 9 | State / dedup | Gmail labels + JSONL ledger | Gmail labels (keep) + managed DB |
| 10 | Secrets | YAML + JSON files | secret manager |
| 11 | Observability | heartbeat file + rotating logs | cloud logging/metrics + heartbeat row in DB |
| 12 | Operator UI | local NiceGUI panel | hosted dashboard (this project's frontend) |
| 13 | Notifications | bug-report email via Gmail send | App Insights Smart Detection (D20); Gmail send dropped |

> **Not a block: concurrency control (deliberately deferred).** The original app used a
> `filelock` single-instance guard. We are **not** carrying it over at this stage — it adds
> complexity for a problem we don't yet have. Its only purpose is to stop two overlapping
> pipeline runs from double-processing an email into duplicate Trello cards. See "Deferred:
> concurrency" below for the reasoning and the trigger to revisit.

## Component roles (all serverless)

Three roles. The **worker** and the **on-demand path in the API backend share the same
pipeline code** (blocks 5–8).

```
   Operator ─login─▶  FRONTEND (dashboard SPA)
   (browser)         │  app login · Connect Google · metrics · Trello config · on/off
                     └──── HTTP ────┐        ▲ read metrics/status
                                    ▼        │
              API / AUTH BACKEND (HTTP-trigger)
              • OAuth callback → store refresh token
              • toggle `enabled` flag  • "process now"  • serve dashboard data
                     │ read/write             │ read
                     ▼                         │
        SECRET / TOKEN STORE            STATE STORE (DB)
        • Gmail refresh token           • enabled flag
        • Trello key+token+ids          • claim history
        • OAuth client secret           • run metrics + heartbeat
                     ▲                         ▲
                     │ read tokens             │ write results/metrics
              WORKER (timer-trigger, every 30 min)
              read `enabled` → (off: heartbeat+exit) → (on: run pipeline)
```

This maps to **Azure Functions with two trigger types** (Timer + HTTP) sharing a library,
plus Key Vault (secrets), Table Storage (state), and Static Web Apps (frontend). Equivalent
stacks exist on GCP and AWS but are **out of scope** — Azure is chosen (D10). See
`../azure-implementation.md` for the authoritative service mapping.

## Authentication (the crux)

Two conceptual flows, satisfied by **one Google consent** (D18). Because **the operator is
the mailbox owner**, a single sign-in requests Gmail scopes at login — dashboard identity and
Gmail access in one consent — and the backend then issues its own **signed session JWT**
(presented as a Bearer header, not a cookie — D22; see `../azure-implementation.md`).
Authorization is a **single-email allowlist** (D17): the backend authenticates via Google,
then permits only the one configured operator email.

| Flow | Purpose | Mechanism | Runtime cost |
|------|---------|-----------|--------------|
| A. Dashboard login | who may view/control | **Google as IdP, brokered by our backend** (not SWA built-in OIDC — see azure-implementation.md); backend mints a signed session **JWT** carried in the `Authorization: Bearer` header (D22); access gated by single-email allowlist (D17) | none |
| B. Gmail data access | read/label emails | same consent as A (D18) → **refresh token** in secret store → backend refreshes non-interactively. Scopes: `gmail.modify` + `openid`/`email` (D19) | ~1 token refresh per run |
| C. Trello access | create/read cards | static API key + token entered in dashboard, stored in secret store | none |

### Why serverless is compatible with auth (resolves the "constant container?" concern)

OAuth has two phases; only the first needs a browser:

1. **Consent (interactive, one-time):** happens in the operator's browser against the
   dashboard — the environment OAuth is designed for. Produces a **refresh token**.
2. **Refresh (non-interactive, ongoing):** backend exchanges `refresh_token` +
   `client_id` + `client_secret` for a short-lived access token via a plain HTTPS call
   (~200 ms). **No browser, no long-running process.**

Therefore a **timer-triggered function can authenticate on its own** each wake. A constant
container is only required for sub-minute always-on polling, which we explicitly avoid for
cost reasons. The refresh token rests in the secret store between runs; the pipeline is
stateless.

### Token health & recovery

The dashboard is the OAuth broker, which also fixes Option A's one weakness (token
revocation/expiry): if the refresh token goes stale, the dashboard shows a **"Reconnect
Gmail"** banner and the operator re-consents in two clicks — no redeploy or shell access.
"Token health" is one of the v1 metrics.

## Trigger model & the on/off switch

- **Scheduled:** every **30 minutes**. Worst-case processing latency = ~30 min
  (acceptable, per product decision).
- **On-demand:** dashboard "process now" → HTTP backend runs the same pipeline once.
- **On/off:** an `enabled` flag in the state store. The timer always fires; the worker
  reads the flag first and exits immediately when off (heartbeat only). The frontend flips
  the flag; no infrastructure is started/stopped. See `product.md`.

## State & dedup

- **Dedup stays in Gmail:** the original marks messages `UNREAD → procesado`/`failed`.
  Gmail itself is the source of truth for "already processed", so re-runs are naturally
  idempotent — a strong property to preserve.
- **Ledger → managed DB:** the JSONL `claim_history` becomes DB rows, which also back the
  dashboard metrics and the heartbeat/last-run timestamp.

## Cost model

- Low, bursty email volume → an always-on container is mostly paid idle time.
- 30-min timer function + pay-per-request HTTP function ≈ pennies (often free tier).
- Only always-on costs are the small managed DB and the secret store — both cheap.
- Latency/cost trade-off is explicit: 30-min poll trades latency for near-zero compute.

## Key decisions (log)

| # | Decision | Status |
|---|----------|--------|
| D1 | Serverless (timer + HTTP functions), not an always-on container | **Decided** |
| D2 | Dashboard doubles as the Gmail OAuth broker | **Decided** |
| D3 | Operator = mailbox owner → single Google sign-in for login + Gmail scopes | **Decided** |
| D4 | Worker controlled by an `enabled` flag; timer always fires, worker self-exits when off | **Decided** |
| D5 | Schedule interval = 30 minutes | **Decided** |
| D6 | Dashboard login via Google OIDC | **Decided** |
| D7 | v1 metrics: emails processed, cards created, last-run timestamp, token health | **Decided** |
| D8 | Preserve Gmail-label-based dedup for idempotency | **Decided** |
| D9 | Reuse pure-logic modules (parsing, classification, PDF, Trello) from original repo | **Decided** |
| D12 | No concurrency control for now (deferred — see "Deferred: concurrency") | **Decided** |
| D14 | Secrets in a managed secret store, not general object storage (cost negligible, secure-by-default, runtime-writable) | **Decided** |
| D10 | Cloud provider = **Azure** (free-tier target); see `../azure-implementation.md` | **Decided** |
| D11 | State store = managed key-value/table (Azure: Table Storage) for v1 | **Decided** |
| D15 | CI→cloud auth = GitHub Actions + OIDC (no stored secrets); infra repo private for now | **Decided** |
| D16 | Gmail account support = **single refresh-token broker path** for both Workspace and consumer; account type is deployment config, not a code branch. **No** service-account / domain-wide-delegation path | **Decided** |
| D17 | Dashboard authorization = **single-email allowlist**; backend authenticates via Google, then authorizes the one operator email (authn ≠ authz) | **Decided** |
| D18 | **One Google consent** at login (identity + Gmail scopes together) → backend issues its own signed session token; landing→dashboard is navigation, not a second consent. "Reconnect Gmail" re-runs the same consent only on token staleness. (Clarifies D3/D6.) | **Decided** |
| D22 | Dashboard session = **backend-minted JWT** signed with the session key, presented as an `Authorization: Bearer` token — **not a cookie** (SWA and Function App are different origins; Functions validate the JWT statelessly per request). Supersedes "session cookie" wording. | **Decided** |
| D23 | State store schema = **one Table Storage table per data intent** (WorkerState, TrelloConfig, Heartbeat, Metrics, ClaimHistory); table count is free. Runtime-entered values (Trello board/list IDs) live in a table row, not deploy-time app settings. ClaimHistory keyed by claim number enables cheap idempotent-create (see D12). | **Decided** |
| D24 | IaC tool = **Bicep** (Azure-native, no state file, first-class in GitHub Actions) | **Decided** |
| D25 | Onboarding = **allowlist email is a deploy-time app setting** (only admin sets it); **Trello creds entered via the dashboard** after first Google login → Key Vault + Table row. Matches D17/D21 (admin = operator for v1). | **Decided** |
| D26 | Membrete letterhead PNGs = **bundled in the Function deployment artifact** (tiny, rarely change, versioned with code); Blob-Storage upload deferred | **Decided** |
| D19 | Gmail scopes = **`gmail.modify` + `openid`/`email`** only; **`gmail.send` dropped** | **Decided** |
| D20 | Notifications = **Azure-native App Insights Smart Detection** (free failure-anomaly email); no Gmail send (supersedes bug-report email) | **Decided** |
| D21 | **Single operator = admin for v1**; admin monitors via cloud portal (App Insights + Storage Explorer) under Azure login. Admin/operator role split **deferred** until a separate end-operator exists | **Decided** |

## Open questions

1. ~~**Cloud provider**~~ — **Resolved (D10): Azure.** Provider-agnostic framing dropped;
   `../azure-implementation.md` owns all provider-specific detail.
2. ~~**Gmail account hardening**~~ — **Resolved (D16):** one refresh-token broker path serves
   both Workspace and consumer; account type only affects OAuth *app publishing status* (a
   deploy setting) and token longevity, both handled by the "Reconnect Gmail" flow. **Still to
   confirm at deploy time:** the actual monitored mailbox type, which sets publishing status.
3. **Full metric set** — v1 is minimal (4 metrics); expand once dashboard needs are clear.
4. ~~**Membrete assets**~~ — **Resolved (D26): bundled in the Function deployment artifact.**
5. ~~**Notifications**~~ — **Resolved (D20):** Azure-native App Insights Smart Detection
   (free failure-anomaly email); `gmail.send` dropped.

## Deferred: concurrency

**Decision (D12): do not implement concurrency control for now** — it adds complexity for
a problem we don't yet have.

- **What it would guard against:** two pipeline runs executing at the same time both read
  the same `UNREAD` email and create duplicate Trello cards, because the label-based dedup
  (block #9) is not atomic — there's a read→process→relabel race window.
- **Where overlap could come from:** the scheduled timer overlapping an on-demand "process
  now" run. (A timer alone won't overlap itself — timer triggers are singleton by default
  on typical serverless runtimes.)
- **Why it's safe to defer now:** low email volume, a single operator, and 30-min spacing
  make simultaneous runs unlikely; a duplicate card is a mild, visible, manually-fixable
  nuisance, not data loss.
- **How we'd address it later, cheapest first:** (a) pin the worker to a single instance /
  host-level singleton lock; (b) a lease row in the state store; or, preferred,
  (c) **make card creation idempotent** — search Trello for an existing card by claim
  number before creating (the update path already does this; the create path does not).
  Option (c) removes the need for any lock and also hardens against retries.
- **Revisit trigger:** if we observe duplicate cards, add heavy concurrent triggers, or
  allow scale-out beyond one instance.
