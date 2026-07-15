# Azure Implementation

> Provider layer for the cloud claim-automation. **Azure is the chosen provider** (D10),
> targeting the free tier. The steering files (`steering/*.md`) describe roles/blocks to keep
> the pure logic portable, but Azure is the committed provider (D10); this document owns all
> Azure-specific detail. Living document — refined as decisions land.

## Goal

Run the cloud claim-automation on Azure at **~$0–$1/month**, using serverless building
blocks inside Azure's always-free / near-free envelope, satisfying the architecture in
`steering/tech.md`.

## Decisions reflected here

Master log lives in `steering/tech.md`; the Azure-relevant ones:

| ID | Decision |
|----|----------|
| D10 | Cloud provider = **Azure** (free-tier target) |
| D11 | State store = **Table Storage** for v1 |
| D13 | Observability = **Application Insights**, no mounted volume |
| D14 | Secrets = **Key Vault** (not object storage) |
| D15 | CI→Azure auth = **GitHub Actions + OIDC**; infra repo **private** for now |
| D22 | Dashboard session = **backend-minted JWT** presented as a **Bearer header** (not a cookie — SWA/Function are cross-origin) |
| D23 | Table Storage schema = **one table per data intent** (table count is free) |
| D24 | IaC tool = **Bicep** |
| D25 | Onboarding = **allowlist email as deploy-time app setting**; Trello creds entered via dashboard after first login |
| D26 | Membrete assets = **bundled in the Function deployment artifact** |

## Service mapping (building block → Azure service)

| Block | Azure service | Free allowance | Fit at our scale |
|-------|---------------|----------------|------------------|
| Worker (timer) | **Functions, Consumption (Y1)** — Timer trigger | 1M exec + 400k GB-s / mo / subscription, always free | ✅ ~1,440 runs/mo is negligible |
| API / auth backend | **Functions, Consumption (Y1)** — HTTP trigger (same app) | shares the grant above | ✅ low dashboard traffic |
| Frontend (dashboard) | **Static Web Apps, Free** | 100 GB bandwidth/mo, 1 custom domain | ✅ hosting free (see auth note) |
| State store | **Table Storage** (in the Functions storage account) | pay-per-use, fractions of a cent | ✅ simplest + cheapest |
| Secrets | **Key Vault** | no free tier (~$0.03 / 10k ops) | ✅ pennies |
| Observability | **Application Insights** (workspace-based → requires a **Log Analytics workspace**) | first 5 GB/mo free, ~90-day retention; workspace capped at 1 GB/day by IaC | ✅ tiny volume — effectively free |

**State store alternatives considered (the tradeoff):** the choice is between *dumb / free /
zero-latency / weak queries* (Table), *rich queries at the cost of an always-on quota you
must manage* (Cosmos), and *full relational power at the cost of cold-start + ops weight*
(SQL). Our data is tiny (1 flag, 1 config row, a few counters, tens–low-hundreds of claims/mo)
and access is point-based, so richer query power buys nothing today while its costs are real.

| Store | Query power | Free/cost | Cold start | Fit |
|-------|-------------|-----------|-----------|-----|
| **Table Storage** | point + partition scan; no server-side aggregation | pay-per-use, fractions of a cent, rides the Functions storage acct | none | ✅ ideal |
| Cosmos DB (free) | rich filter/sort/aggregate, auto indexes | 1000 RU/s + 25 GB lifetime free, **always-on, 1/subscription** | none | ⚠️ overkill |
| Azure SQL (free serverless) | full SQL, joins, transactions | 100k vCore-s/mo, **auto-pauses after 1h idle** | ~30–60s wake | ❌ wrong shape + cold-start UX |

**Revisit trigger:** move to Cosmos only if the dashboard needs cross-row filter/sort/aggregate
over history (e.g. "claims in date range X grouped by type"); at our row counts we just pull
all rows and aggregate in-Function. SQL only if we ever get genuinely relational reporting.

### State store schema — one table per data intent (D23)

Table Storage bills by *data + transactions*, **not table count**, so multiple tables cost the
same as one. We therefore use **one table per data intent** rather than partitioning a single
table:

| Table | PartitionKey / RowKey | Holds |
|-------|-----------------------|-------|
| `WorkerState` | `"worker"` / `"enabled"` | the on/off flag |
| `TrelloConfig` | `"trello"` / `"config"` | board_id, list_id (**runtime-entered via dashboard**) |
| `Heartbeat` | `"run"` / `"last"` | last-run timestamp + status |
| `Metrics` | `"metric"` / `<YYYY-MM>` | emails_processed, cards_created counters |
| `ClaimHistory` | `"claim"` / `<claim_number>` | result, card_id, processed_at |

- **`ClaimHistory` keyed by claim number** makes "did I already create a card for this claim?"
  a single **point read** — the cheap **idempotent-create** hardening (D12's preferred future
  concurrency fix) falls out almost for free.
- **Resolves the "app settings vs Table Storage" ambiguity:** anything the operator types at
  runtime (Trello board/list IDs) **must** be a Table row — app settings are deploy-time and
  would need a redeploy to change. App settings are only for deploy-time config (Google
  `client_id`, App Insights connection string, allowlist email, Gmail scopes).

## Component topology

```
 Operator browser
      │  HTTPS (login + dashboard)
      ▼
┌─────────────────────────── Resource Group: rg-claim-automation ──────────────────────────┐
│                                                                                            │
│  ┌──────────────────────┐        HTTPS (API)     ┌──────────────────────────────────────┐ │
│  │ Static Web App (Free) │ ─────────────────────▶ │ Function App (Consumption Y1)         │ │
│  │  dashboard SPA        │                        │  • HTTP trigger:                      │ │
│  └──────────────────────┘                        │      OAuth callback, toggle enabled,  │ │
│                                                    │      process-now, serve dashboard data│ │
│                                                    │  • Timer trigger (30 min):            │ │
│                                                    │      read enabled → run pipeline      │ │
│                                                    │  [System-assigned Managed Identity]   │ │
│                                                    └──┬──────────────┬──────────────┬──────┘ │
│                              managed identity (RBAC)  │              │              │        │
│                    ┌─────────────────────────────────┘              │              │        │
│                    ▼                                                 ▼              ▼        │
│         ┌────────────────────┐                    ┌────────────────────┐  ┌────────────────┐│
│         │ Key Vault          │                    │ Storage Account    │  │ App Insights   ││
│         │ • Google client sec│                    │ • Table: enabled   │  │ logs / traces /││
│         │ • Gmail refresh tok│                    │   flag, claim      │  │ exceptions /   ││
│         │ • Trello key/token │                    │   history, metrics │  │ dependencies   ││
│         │ • session key      │                    │ • (Functions rt)   │  │                ││
│         └────────────────────┘                    └────────────────────┘  └────────────────┘│
└────────────────────────────────────────────────────────────────────────────────────────────┘
      │ outbound HTTPS
      ▼
 External:  Google OAuth + Gmail API   ·   Trello API
```

- **One Function App, two triggers** (Timer + HTTP) sharing one pipeline library — the
  ported pure-logic modules from `../claim_automation` (parsing, classification, PDF,
  Trello).
- The Function App reaches Key Vault and Storage via its **managed identity** (RBAC) — no
  connection strings stored (D14).
- We reuse the storage account Functions auto-provisions rather than adding a separate one.

## Authentication

### Dashboard login + Gmail access — one unified Google sign-in (D3)

Because the operator **is** the mailbox owner, a single **Google OAuth** flow provides both
dashboard identity and Gmail scopes:

- **We** own the Google OAuth client (`client_id` + `client_secret`); the operator never
  handles it — they click **"Sign in with Google"** and consent once (D18: login and Gmail
  grant are one consent; there is no separate "Connect Google" step) (a big UX win over the original,
  where the user had to create a GCP project and download `client_secret.json`).
- The flow is handled by **our own Function HTTP endpoint** (OAuth callback), which stores
  the resulting **refresh token** in Key Vault.
- **SWA Free-tier gotcha:** Static Web Apps' *built-in* auth only supports Entra ID +
  GitHub; Google requires the Standard plan ($9/mo). **Sidestep:** don't use SWA built-in
  auth — run the Google flow in our own Function (which we're building anyway). Dashboard
  stays free.

### Dashboard session = backend-minted JWT, Bearer header — not a cookie (D22)

The HTTP-trigger Functions are **stateless per request** — each invocation has no memory of
the last — so the backend must re-establish *who is calling* on every request. It does this
**without server-side session state**: after the one-time Google consent, the backend mints
**its own signed JWT** (signed with the session signing key from Key Vault), and the dashboard
presents it on each subsequent call. Each Function authenticates a request by **verifying the
JWT signature + expiry + allowlisted email** — a stateless check, no session store.

Login → API request flow:

1. Operator clicks **Login** → browser hits backend `/api/auth/login` → redirect to Google → consent.
2. Google redirects to backend `/api/auth/callback?code=…`. Backend exchanges the code:
   **Gmail refresh token → Key Vault**; **id_token → verify email against the single-email
   allowlist (D17)**.
3. Backend mints a short-lived **session JWT** (signed with the session key) and returns it
   to the browser.
4. Dashboard stores the JWT and sends `Authorization: Bearer <jwt>` on **every** API call.
5. Each HTTP Function validates signature + expiry + allowlisted email. Stateless.

**Why Bearer header, not a cookie:** the SWA (`…azurestaticapps.net`) and Function App
(`…azurewebsites.net`) are **different origins**. A cookie set by the API domain is
*third-party* to the SPA → subject to `SameSite` rules and browser third-party-cookie
blocking (fragile). A Bearer token the SPA holds and attaches explicitly is **origin-agnostic**
— we only allow the SWA origin via CORS. This supersedes the earlier "session cookie" wording.

### Gmail account type is not a code branch

One OAuth-broker path handles Workspace and consumer `@gmail.com` identically. Account type
only affects **refresh-token longevity**, governed by our OAuth **app publishing status**
(a deployment setting, not per-user logic):

- Workspace + "Internal" app → long-lived tokens, no verification (smoothest).
- External "Testing" → tokens expire ~7 days → periodic 2-click "Reconnect Gmail".
- External "In production" + restricted `gmail.modify` scope → Google CASA verification
  (heavy for solo use).

"Reconnect Gmail" is a first-class dashboard state, so even the 7-day case is tolerable.

### Trello

Static API key + token + board/list IDs entered once in a dashboard config form. The
**key + token** go to Key Vault (secrets); **board_id / list_id** are config, not secrets
(see inventory). Later option: a "Connect Trello" OAuth button.

## Secrets & configuration

Split **true secrets** (Key Vault) from **config/identifiers** (app settings / Table
Storage). Mapped from the original `config.yaml`.

**Secrets → Key Vault**

| Secret | Origin | Notes |
|--------|--------|-------|
| Google OAuth **client secret** | `client_secret_*.json` | for the OAuth app *we* own |
| Gmail **refresh token** | `token.json` | per-operator; runtime-written after consent |
| Trello **API key** | `trello.api_key` | |
| Trello **token** | `trello.token` | |
| Dashboard **session signing key** | *new* | HMAC/asymmetric key that signs the backend-minted session **JWT** (D22) |

**Config → app settings / Table Storage (not secret)**

| Value | Origin |
|-------|--------|
| Google OAuth **client ID** | not sensitive |
| Trello **board_id**, **list_id** | `trello.*` identifiers |
| Gmail **scopes** (`gmail.modify` + `openid`/`email`; **no `gmail.send`** — D19), **max_results** | `gmail.*` |
| **bug-report email** | `bug_report.email` |
| App Insights **connection string** | *new* (app setting) |

**Never stored:** Gmail *access tokens* (short-lived, minted per run from the refresh
token).

**Managed identity** lets the Function reach Key Vault + Table Storage with **no stored
connection strings** — the recommended least-privilege pattern (D14).

**D14 carve-out (2026-07-15, deployment spec Gate 1):** the Functions *runtime* on Linux
Consumption (Y1) requires key-based `AzureWebJobsStorage` + content-share connection strings —
identity-based host storage is not supported on Y1. This host plumbing is the sole exception;
all **application-level** access (Key Vault, Table data) stays managed-identity.

## Observability & debugging — no volume (D13)

- Consumption instances are **ephemeral** (scale to zero, local disk wiped on recycle), so
  a file-based `app.log` on a volume would lose exactly the history you need to debug — and
  Consumption doesn't cleanly mount persistent volumes anyway.
- **Application Insights** is built into Functions: invocations, exceptions with stack
  traces, and dependency calls (each Gmail/Trello HTTP call with timing/status) flow
  automatically; queried with KQL. The original `logging`-based `app_logger` pipes in with
  near-zero code change (drop the rotating-file handler, keep the logger).
- **Separation of concerns:** App Insights = diagnostics (~90-day retention); Table Storage
  = durable business state (claim history, metrics, `enabled` flag). Transient PDFs live in
  `/tmp` and are discarded after attaching to Trello.
- **Alerting = App Insights Smart Detection (D20).** Built-in ML failure-anomaly detection
  emails the admin when exception/failure rates spike — **free**, no rule to author, no
  Gmail send. This replaces the original app's `gmail.send` bug-report email. Custom
  log-search/metric alert rules (pennies/mo) remain an option later if finer triggers are
  needed; email notifications via action groups are free.

## Local development (Docker)

Run and debug the whole pipeline locally without touching Azure.

| Cloud service | Local equivalent | How |
|---------------|------------------|-----|
| Table Storage | **Azurite** (`mcr.microsoft.com/azure-storage/azurite`) | Docker; connect via `UseDevelopmentStorage=true` — same SDK, no code branch |
| Functions | **Azure Functions Core Tools** (`func start`) | on host, or the Functions Docker base image |
| Key Vault | **gitignored local secret file** (SecretStore `file` backend) + `.env` for read-only config | one secret-access interface (`SecretStore`): local → file, cloud → Key Vault; a file (not env) because some secrets are runtime-*written* (Gmail refresh token) — see `.specs/auth/spec.md` |
| App Insights | **console/stdout** | Python `logging` prints locally |

Dev loop: `docker compose up` (Azurite) + `func start`, config from a gitignored `.env`,
runtime-written secrets in the gitignored local secret file.
Mirrors the existing forecasting-api setup (postgres + azurite). Postgres only enters if we
later switch to a relational store.

## Repositories, CI/CD & secrets flow

**Two repositories** (frontend rides with the backend for now):

### Repo 1 — `claim-automation-cloud` (application; can be public)

```
claim-automation-cloud/
  backend/            # Azure Functions (Python) — HTTP + Timer triggers
    pipeline/         #   ported pure-logic package (parsing, classification, PDF, Trello)
    functions/        #   trigger entry points (thin adapters over pipeline/)
  frontend/           # dashboard SPA → deploys to Static Web App
  .github/workflows/  #   deploy-functions.yml, deploy-swa.yml
```

Frontend + backend together because the UI is thin and tightly coupled to the API
contract. No secrets in the repo; local dev uses a gitignored `.env`.

### Repo 2 — `claim-automation-infra` (deployment; **private**)

```
claim-automation-infra/
  main.bicep                   # RG, Function App, Static Web App, Storage,
                               # Key Vault, App Insights, Managed Identity + RBAC
  .github/workflows/deploy.yml # provision + seed Key Vault
```

### Secrets & CI auth flow (D14 + D15)

- **No repo holds secrets.** Secret *values* live only in Key Vault; the pipeline seeds
  them from the GitHub Actions secret store.
- **CI → Azure uses OIDC** (workload identity federation): GitHub Actions gets a
  short-lived token from Azure at run time; **no long-lived cloud credential is stored**.
  The federated credential is scoped to this repo + branch/environment.
- The infra repo is **private for now** — defense-in-depth against topology/RBAC
  disclosure and CI supply-chain surface, not because it holds secrets. (If ever made
  public: scope OIDC to a protected environment, pin actions by SHA, set least-privilege
  workflow `permissions`.)

### Repo → service mapping

| Repo / path | Produces | Azure service |
|-------------|----------|---------------|
| `claim-automation-cloud/backend` | Function package | **Function App** (HTTP + Timer) |
| `claim-automation-cloud/frontend` | static build | **Static Web App** |
| `claim-automation-infra` | **Bicep** deployment (D24) | **all resources** + Managed Identity + RBAC |

*(Splitting the frontend later makes it Repo 3 → Static Web App only; nothing else changes.)*

## Cost summary

- Realistic monthly cost: **$0–$1** (a few cents of Key Vault ops + Table Storage).
- The Consumption plan auto-creates a **storage account not covered by the free grant** —
  still pennies at our volume.
- **Azure has no hard spend cap** — overages bill immediately. **Budget alert set at $5/mo**
  (well above the expected $0–1; catches a misconfigured always-on SKU within days).

## Resolved this round

- [x] **IaC tool = Bicep (D24)** — Azure-native, no state file, first-class in GH Actions.
- [x] **Onboarding = allowlist email as deploy-time app setting; Trello creds entered via the
  dashboard after first Google login (D25)** — matches D17/D21 (admin = operator for v1).
- [x] **Membrete assets bundled in the Function deployment artifact (D26)** — tiny, rarely
  change, versioned with code.
- [x] **Budget alert = $5/month** — above the expected $0–1, catches runaway within days.
- [x] **Idempotent card creation stays deferred (D12)** — revisit only if duplicate cards appear.

## Open items to refine

- [ ] **Monitored-mailbox type: consumer `@gmail.com` vs Google Workspace domain.** This is
  the only input that sets OAuth publishing status → token longevity: consumer →
  External/Testing → ~7-day "Reconnect Gmail" cycle; Workspace-you-administer → Internal →
  long-lived tokens. Code is account-agnostic (D16); pure deploy-time console setting.
- [x] Region selection — **resolved 2026-07-15 (revised same day): northeurope** for
  compute/data, **eastus2** for the SWA metadata record. westeurope was the original
  choice but Azure rejects it for new subscriptions (`RequestDisallowedByAzure`, "region
  not accepting new customers"); SWA's only remaining supported regions are US/East Asia
  (content is edge-served globally, so user latency is unaffected). See
  `.specs/deployment/spec.md`.
- [ ] Whether to split the frontend into its own repo (monorepo for now).
- [ ] Full metric set beyond the v1 four (expand once dashboard needs are clear).
