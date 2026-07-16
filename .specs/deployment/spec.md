# Spec: Deployment Infrastructure (Azure, free tier)

> **Created:** 2026-07-15 · **Status:** Approved (Gate 1 passed 2026-07-15) · **Mode:** Lite (constitution P9)
> **Authority:** [azure-implementation.md](../azure-implementation.md) owns the Azure service
> mapping; this spec adds the deploy-time decisions and the buildable slice.

## Overview

Provision the full Azure runtime for claim-automation-cloud and wire CI/CD so that merging
to `main` deploys the app. Two repos are touched (repo split per the Repositories section
of azure-implementation.md; privacy per D15):

- **This repo** — `KeyVaultSecretStore` (the production `SecretStore` backend promised in
  `backend/app/secret_store.py`), prod API base URL wiring in the frontend, and the two app
  deploy workflows (`deploy-functions.yml`, `deploy-swa.yml`).
- **New private repo `claim-automation-infra`** — Bicep IaC + provision workflow + OIDC
  bootstrap script.

Deploy-time decisions resolved this session:

| Item | Decision |
|------|----------|
| Subscription | **Personal free-tier account** (personal tenant, not the work tenant) |
| Region | **swedencentral** + **eastus2** for the SWA metadata record. **[REVISED 2026-07-15 during Task 12: westeurope rejects new subscriptions (`RequestDisallowedByAzure`), and northeurope/germanywestcentral/uksouth have zero Y1 quota on this subscription; SWA's supported-region list excludes all other EU regions — its content is edge-served globally, so only management metadata lives in eastus2. User-approved.]** Original: westeurope (closest to CH). |
| Resource group | `rg-claim-automation` |
| Infra repo | `aleromar/claim-automation-infra`, **private** |
| App repo | `aleromar/claim-automation-cloud` (public, already exists) |

## Requirements

### REQ-1: Idempotent provisioning via Bicep

**User Story:** As the admin, I want all Azure resources declared in Bicep so the
environment is reproducible and reviewable.

1. WHEN the infra pipeline (or `az deployment sub create`) runs against an empty
   subscription THE SYSTEM SHALL create: resource group `rg-claim-automation`, a Linux
   Consumption (Y1) Function App (Python 3.12), a Static Web App (Free), **one** Storage
   account (Standard_LRS, TLS ≥ 1.2) that serves both as the Functions host storage and
   the Table Storage home for the five D23 tables, a Key Vault (RBAC mode), a
   Log-Analytics-backed Application Insights, and a $5/month budget with email alert to
   `aleromar@gmail.com`.
2. WHEN the same deployment runs a second time with unchanged templates
   THE SYSTEM SHALL complete successfully with no resource changes (idempotent) — role
   assignments use deterministic `guid(scope, principalId, roleId)` names.
3. THE SYSTEM SHALL grant the Function App's system-assigned managed identity
   **Key Vault Secrets Officer** on the vault (read + runtime-write of
   `gmail-refresh-token`) and **Storage Table Data Contributor** on the storage account —
   no connection strings for **application-level** data access (D14).

   **[REVISED 2026-07-15 — D14 carve-out (Gate 1, risk critic):** the Functions *runtime*
   on Linux Consumption requires key-based `AzureWebJobsStorage` +
   `WEBSITE_CONTENTAZUREFILECONNECTIONSTRING`/`WEBSITE_CONTENTSHARE`; identity-based host
   storage is not supported on Y1. These host-plumbing settings are the sole, documented
   exception to D14; azure-implementation.md to record it (Task 13).**]**

**Test coverage:** pytest assertions over compiled ARM JSON (`bicep build` output) in
`claim-automation-infra/tests/`, **scoped to cost/security invariants** (SKUs Y1/Free/LRS,
budget amount, TLS floor, RBAC role IDs, Log Analytics daily cap); live `what-if` in CI.

### REQ-2: KeyVaultSecretStore (production secret backend)

**User Story:** As the backend, I need to read and write secrets in Key Vault via managed
identity, behind the existing `SecretStore` protocol.

1. WHEN `SECRET_STORE_BACKEND=keyvault` THE SYSTEM SHALL construct a `KeyVaultSecretStore`
   from `KEY_VAULT_URI` using `DefaultAzureCredential` (managed identity in Azure, `az`
   CLI locally).
2. WHEN `get(name)` is called for a secret that exists THE SYSTEM SHALL return its value;
   IF the secret does not exist THEN THE SYSTEM SHALL return `None` (matching
   `FileSecretStore` semantics, so `require_secret` raises uniformly).
3. WHEN `set(name, value)` is called THE SYSTEM SHALL create/update the secret in the
   vault (runtime-written `gmail-refresh-token` path).

**Test coverage:** `backend/tests/unit/test_secret_store_keyvault.py` (Azure SDK client
mocked; no network).

### REQ-3: CI→Azure auth via OIDC, zero stored cloud credentials (D15)

**[REVISED 2026-07-15 — split identities (Gate 1, contradiction critic): one shared
subscription-wide identity would let the public app repo assume near-Owner rights,
violating azure-implementation.md's own public-repo mitigations.]**

1. WHEN the **infra** workflow runs THE SYSTEM SHALL authenticate as app registration
   `gha-claim-infra` — federated to the **private** infra repo (`main` branch subject
   **and** a `pull_request` subject so PR `what-if` runs can log in) — holding
   **Contributor + Role Based Access Control Administrator** at subscription scope and
   **Key Vault Secrets Officer** on the vault (data-plane seeding).
2. WHEN an **app** deploy workflow runs THE SYSTEM SHALL authenticate as app registration
   `gha-claim-app` — federated to the public app repo's protected **`production`
   environment** — holding only RG-scoped deploy rights (**Website Contributor** on the
   Function App + **Contributor on the Static Web App** for token retrieval).
3. THE SYSTEM SHALL store no client secret, publish profile, or long-lived SWA token in
   GitHub secrets; the SWA deployment token SHALL be fetched at run time
   (`az staticwebapp secrets list`) after OIDC login.
4. Public-repo hardening: app-repo workflows SHALL pin third-party actions by commit SHA,
   declare least-privilege `permissions:` (`id-token: write`, `contents: read`), and
   deploy only from the `production` environment on `main`.

**Test coverage:** `actionlint` on all workflows (Task 8); grep gate: no
`AZURE_CREDENTIALS`/publish-profile references; first live run.

### REQ-4: App deploys from this repo

1. WHEN a push lands on `main` touching `backend/**` THE SYSTEM SHALL build and deploy the
   Function App package (remote/Oryx build for Python deps).
2. WHEN a push lands on `main` touching `frontend/**` THE SYSTEM SHALL build the SPA with
   the production `VITE_API_BASE_URL` and deploy it to the Static Web App.
3. WHEN neither path changed THE SYSTEM SHALL skip the corresponding deploy (path filter).

**Deploy ordering & rollback:** the infra pipeline (provision + seed) MUST complete before
the first app deploy — `app.main`'s lifespan fail-fasts if `session-signing-key` or
`google-client-secret` is missing. Y1 has no deployment slots: rollback = re-run the
deploy workflow on the previous commit/tag.

**Test coverage:** `actionlint` (Task 8); first live run of each workflow; health endpoint
+ SWA URL verified (Task 12).

### REQ-5: Secret seeding without repo exposure (D14)

1. WHEN the infra pipeline runs THE SYSTEM SHALL seed `session-signing-key` with a
   generated random value **only if absent** (never rotate silently — that would
   invalidate live sessions).
2. WHEN the infra pipeline runs THE SYSTEM SHALL seed/update `google-client-secret` from
   the **required** GitHub secret `GOOGLE_CLIENT_SECRET`, failing fast if unset.
   **[REVISED 2026-07-15 (Gate 1): the earlier "Google client pending" branch was
   illusory — `app.main` fail-fasts without the secret, and the client already exists
   (CLARIFY-1). Required is simpler and honest.]**
3. `gmail-refresh-token` SHALL NOT be seeded by CI — it is runtime-written after the
   operator's first consent (REQ-2.3).
4. Seeding SHALL retry with backoff to absorb RBAC-propagation lag after first provision.

### NFR: Cost envelope

Monthly cost SHALL stay in the $0–1 envelope of azure-implementation.md: only free/
pay-per-use SKUs (Y1, SWA Free, LRS storage, App Insights ≤ 5 GB). Enforced, not hoped:
Log Analytics workspace gets `dailyQuotaGb: 1`; Functions `host.json` enables adaptive
sampling; the $5 budget alert is the tripwire. Verified by: ARM assertions on SKUs +
quota + budget amount (REQ-1 tests).

## Design

### Resource naming (single environment, v1)

`uniq = uniqueString(subscription().id, 'claim-automation')` — deterministic per
subscription; storage/KV names must be globally unique. Caveat: Key Vault soft-delete
holds names for 90 days — a teardown/recreate needs `az keyvault purge` (or recover)
first; acceptable for a single long-lived environment.

| Resource | Name |
|----------|------|
| Resource group | `rg-claim-automation` |
| Function App | `func-claim-automation-${uniq}` |
| App Service plan | `plan-claim-automation` (Y1, linux) |
| Static Web App | `swa-claim-automation` |
| Storage account | `stclaim${uniq}` (≤24 lowercase; host storage + D23 tables) |
| Key Vault | `kv-claim-${uniq}` (≤24) |
| App Insights / Log Analytics | `appi-claim-automation` / `log-claim-automation` (1 GB/day cap) |
| Budget | `budget-claim-automation` ($5, actual + forecast alerts → aleromar@gmail.com) |

### Bicep layout (infra repo)

```
claim-automation-infra/
  main.bicep                # subscription-scope: RG + module
  modules/resources.bicep   # RG-scope: everything else + RBAC + outputs
  tests/test_compiled_arm.py# pytest over `az bicep build` JSON — cost/security invariants only
  scripts/bootstrap-oidc.sh # one-time: 2 app registrations, federated creds, RBAC, gh secrets
  .github/workflows/deploy.yml  # PR: build+tests+what-if · main: deploy + seed secrets
  pyproject.toml            # uv; pytest only
```

### Function App settings (from `backend/app/config.py` contract)

| App setting | Value |
|-------------|-------|
| `GOOGLE_CLIENT_ID` | deploy parameter (client exists — CLARIFY-1) |
| `OPERATOR_EMAIL` | deploy parameter (fail-fast at startup if empty — existing behavior) |
| `OAUTH_REDIRECT_URI` | `https://<funcapp>.azurewebsites.net/api/auth/callback` |
| `FRONTEND_BASE_URL` / `CORS_ALLOWED_ORIGIN` | `https://<swa defaultHostname>` |
| `SECRET_STORE_BACKEND` | `keyvault` |
| `KEY_VAULT_URI` | KV output (new setting, consumed by REQ-2) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights output |
| `AzureWebJobsStorage`, `WEBSITE_CONTENT*` | key-based host plumbing (documented D14 carve-out) |

SWA `defaultHostname` and the Function App URL are both known within one Bicep deployment
(SWA doesn't reference the Function App, so no cycle); the MI `principalId` feeds role
assignments in the same pass.

### Frontend prod API base (structure.md amendment)

**Conflict found:** structure.md says "`/api/*` relative … SWA route in prod", but SWA
route-proxying to an external Function App (linked backend) requires the **Standard plan
($9/mo)** — and D22 already treats SWA↔Functions as cross-origin (Bearer + CORS).
**Resolution (ASSUMPTION-2, approved):** frontend reads `VITE_API_BASE_URL` (empty in dev
→ relative + Vite proxy; set to the Function App URL in the SWA deploy workflow).
structure.md has **three** stale spots to amend (Task 13): the Vite-proxy rationale
("every environment"), the API-paths convention line, and the conventions-log entry.
Accepted gap: e2e still runs single-origin via the Vite proxy; the prod cross-origin
topology (CORS + absolute URL) is covered by the post-deploy manual smoke (step 2 below).

### OIDC bootstrap (one-time script, run by admin while `az login`'d to personal account)

`scripts/bootstrap-oidc.sh` (shellcheck-linted, Task 6; idempotent re-runs):

1. App registration **`gha-claim-infra`** + federated credentials
   `repo:aleromar/claim-automation-infra:ref:refs/heads/main` **and**
   `repo:aleromar/claim-automation-infra:pull_request`. Roles: Contributor + RBAC
   Administrator (subscription). Key Vault Secrets Officer on the vault is assigned in
   Bicep (CI principalId passed as parameter).
2. App registration **`gha-claim-app`** + federated credential
   `repo:aleromar/claim-automation-cloud:environment:production`. Roles (RG scope):
   Website Contributor + SWA Contributor — assigned in Bicep.
3. Creates the `production` environment in the public app repo and sets
   `AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID` (per-repo values) via
   `gh secret set`; prompts for `GOOGLE_CLIENT_SECRET` on the infra repo.

### Post-deploy manual checklist (outside IaC)

1. In the existing Google OAuth client, add redirect URI
   `https://<funcapp>.azurewebsites.net/api/auth/callback`.
2. First login in the dashboard → verify JWT gate + `gmail-refresh-token` lands in KV
   (this also smoke-tests the prod cross-origin CORS path).
3. Confirm budget alert email arrives on test threshold (optional).

## Tasks (test-first)

| # | Task | Type | Repo | REQ |
|---|------|------|------|-----|
| 1 | Failing unit tests for `KeyVaultSecretStore` | TEST | app | REQ-2 |
| 2 | Implement `KeyVaultSecretStore` + settings wiring (`KEY_VAULT_URI`) | IMPL | app | REQ-2 |
| 3 | Failing frontend test: API base URL from `VITE_API_BASE_URL` | TEST | app | REQ-4.2 |
| 4 | Implement frontend API base wiring | IMPL | app | REQ-4.2 |
| 5 | Scaffold infra repo (private) + failing ARM-assertion pytest harness (invariants) | TEST | infra | REQ-1, NFR |
| 6 | shellcheck harness wired for `scripts/` (RED: script absent/failing) | TEST | infra | REQ-3 |
| 7 | Write `main.bicep` + `modules/resources.bicep` until Task-5 tests green; `what-if` clean | IMPL | infra | REQ-1, NFR |
| 8 | actionlint wired for all workflows in both repos (RED before workflows exist) | TEST | both | REQ-3,4 |
| 9 | `bootstrap-oidc.sh` (passes Task 6) + run it (requires personal `az login`) | IMPL | infra | REQ-3 |
| 10 | Infra `deploy.yml` (PR: build+tests+what-if; main: deploy + seed w/ retry) | IMPL | infra | REQ-1,3,5 |
| 11 | `deploy-functions.yml` + `deploy-swa.yml` (SHA-pinned, env `production`) | IMPL | app | REQ-3,4 |
| 12 | Live verification: provision, deploy, hit `/api/health`, load SWA, login smoke | VERIFY | both | all |
| 13 | Docs: amend structure.md (3 spots, VITE_API_BASE_URL), azure-implementation.md (Log Analytics row, D14 carve-out, region=westeurope), tech.md block-13 stale row | DOC | app | — |

## Bugfix log

Defects found during first live execution (Tasks 9–12): built as designed, failed live.
Decision changes forced by the environment (region, OIDC identity split) live as
`[REVISED]` markers above; this table is defects only.

| # | Symptom | Root cause | Fix | Guard |
|---|---------|-----------|-----|-------|
| 1 | Infra CI OIDC login rejected: `AADSTS700213: No matching federated identity record` | GitHub enforces immutable ID-embedded OIDC subjects (`repo:owner@id/repo@id:…`) for repos created on/after **2026-07-15** — the infra repo's creation day. Name-only federated subjects stopped matching. | infra `280ad8a`: bootstrap script now derives each repo's `sub_claim_prefix` from the GitHub API and updates existing federated credentials on drift | Idempotent bootstrap re-run heals drift; every `deploy.yml` run exercises the login |
| 2 | Local probes failed `SubscriptionNotFound`; first deploy attempt would have too | Brand-new subscription had **no resource providers registered** (Microsoft.Storage/Web/KeyVault/OperationalInsights/Insights) | one-time admin `az provider register` for the five providers (not CI's job — its identity is least-privilege) | PR-path `what-if`/validate now passes preflight |
| 3 | Every route 404; `az functionapp function list` empty | `backend/host.json` was never committed — the Functions host indexes nothing without it. Invisible locally: dev and e2e run uvicorn directly, so Azure hosting was first exercised live | PR #3 (`d257f1b`): add `host.json` incl. the NFR's adaptive sampling | `test_function_host.py` (exists, v2 schema, sampling on) |
| 4 | Still 404; App Insights: `ModuleNotFoundError: No module named 'fastapi'` | On **Linux Consumption + RBAC**, `functions-action` forces `WEBSITE_RUN_FROM_PACKAGE` and silently ignores `scm-do-build-during-deployment`/`enable-oryx-build` — no remote build ever ran, so the package shipped without dependencies | PR #4 (`207d4c4`): vendor deps in CI into `.python_packages/lib/site-packages`; drop the dead Oryx inputs | deploy workflow's health smoke; actionlint |
| 5 | 503 `Function host is not running`; exceptions: `RoutePatternException` on template `api//{*route}` | [Worker bug #1310](https://github.com/Azure/azure-functions-python-worker/issues/1310): `AsgiFunctionApp` registers its catch-all as `/{*route}` (leading slash), so any non-empty `routePrefix` composes an invalid double slash | PR #4 (`84ca909`): `routePrefix: ""` in host.json — FastAPI already declares `/api/*` itself, public URLs unchanged | `test_host_json_empties_route_prefix` |

## Out of Scope

- Timer trigger / pipeline worker (own feature; infra hosts it when it arrives)
- Trello secrets in Key Vault (`SecretName` doesn't include them yet — arrives with the
  Trello feature)
- Custom domain, staging environments, Docker/Azurite local emulation
- Idempotent card creation, alert rules beyond Smart Detection + budget

## Clarifications & Open Questions

### Unresolved

(none)

### Resolved

- [x] `[RESOLVED CLARIFY-1]` Google OAuth client **exists** — user provides
  `GOOGLE_CLIENT_ID` as a deploy parameter and `GOOGLE_CLIENT_SECRET` as a **required**
  GH Actions secret before the first infra run (user, 2026-07-15; hardened at Gate 1).
- [x] `[RESOLVED CLARIFY-2]` Budget-alert email = `aleromar@gmail.com` (user, 2026-07-15).
- [x] `[RESOLVED ASSUMPTION-1]` Infra repo = `aleromar/claim-automation-infra`, private
  (user, 2026-07-15).
- [x] `[RESOLVED ASSUMPTION-2]` structure.md amended to `VITE_API_BASE_URL` for prod
  (supersedes "SWA route in prod") (user, 2026-07-15).
- [x] `[RESOLVED ASSUMPTION-3]` Deploy triggers confirmed: app on push to `main`
  (path-filtered, `production` environment); infra on push to infra `main` +
  `workflow_dispatch` (user, 2026-07-15).
- [x] `[RESOLVED]` Subscription = personal free account; region = westeurope (user,
  2026-07-15).
- [x] `[RESOLVED]` Gate 1 (P10) ran 2026-07-15: 4 critics → 5 critical fixes applied
  (split OIDC identities; D14 host-storage carve-out; CI KV data-plane role; PR federated
  credential; lint TEST tasks). No project files may contain the user's employer name
  (user, 2026-07-15).
- [x] `[RESOLVED]` OAuth publishing status / mailbox type stays open per
  azure-implementation.md — does not block provisioning ("Reconnect Gmail" flow tolerates
  Testing mode).

## Dependencies

- Auth feature (merged, PR #1): `SecretStore` protocol, `Settings` contract.
- Admin must `az login` to the personal subscription for Task 9 and first provision.
- `azure-identity` + `azure-keyvault-secrets` become backend prod dependencies (Task 2).
- Existing Google OAuth client (redirect URI added post-deploy).
