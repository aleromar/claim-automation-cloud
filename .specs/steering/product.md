# Product — Claim Automation (Cloud)

## What this is

A cloud-hosted re-implementation of `claim_automation` (the laptop app in the sibling
repo `../claim_automation`). The original is a single Python process that polls a Gmail
inbox, parses insurance-claim emails, and creates/updates Trello cards with generated
PDFs, driven by a local NiceGUI control panel.

This project keeps the **same claim-processing logic** but moves it off the operator's
laptop into managed cloud infrastructure, adding a **hosted web dashboard** for
authentication, monitoring, and control.

## Why move to the cloud

- No dependency on a laptop being powered on / awake.
- Centralized, always-available monitoring instead of a local-only GUI.
- Authentication handled through a web app the operator logs into, rather than a
  desktop OAuth browser flow tied to one machine.
- Cost-optimized: pay for compute only when work is actually happening.

## Actors

- **Operator** — the single human user. **The operator is also the owner of the
  monitored Gmail mailbox.** This unifies dashboard login and Gmail data access into one
  Google sign-in (see `tech.md`, Auth flows). Access is gated by a **single-email
  allowlist** (D17).
- **Admin** — for v1 the admin **is the operator**; there is no separate admin identity or
  page. Application health is monitored out-of-band via the cloud portal (App Insights
  Smart Detection email + Storage Explorer) under the Azure login, never the operator's
  Google login (D21). A distinct admin/operator role split is deferred until a separate
  end-operator takes over the mailbox.

## Core capabilities

1. **Dashboard (web frontend)**
   - Operator logs in via a **single Google consent** that also grants Gmail access; the
     backend brokers Google and issues a signed session JWT (Bearer header, not a cookie —
     D22), then authorizes the operator
     against a single-email allowlist (D17/D18). No separate "Connect Google" step.
   - Configures Trello credentials (API key + token + board/list IDs).
   - Views metrics and status.
   - **Switches the worker on/off.**
   - Shows a **"Reconnect Gmail"** state when the refresh token goes stale (re-runs the same
     consent).

2. **Worker (backend pipeline)**
   - Runs on a schedule (**every 30 minutes**) and on-demand ("process now" from the
     dashboard).
   - Ports the original pipeline unchanged: poll `UNREAD` Gmail → parse `ClaimData` →
     classify `ClaimType` → generate letterhead PDF → create/update Trello card →
     record result → relabel email.

## The on/off switch (design intent)

The worker is controlled by an **`enabled` flag** in the state store, not by starting or
stopping infrastructure:

- The scheduled trigger **still fires every 30 minutes regardless.**
- The **first thing the worker does on wake is read the `enabled` flag.**
- If **off** → record a heartbeat / last-run timestamp and exit immediately
  (milliseconds, near-zero cost).
- If **on** → run the full pipeline.
- The dashboard toggles the worker purely by flipping this flag via the API backend.

This keeps the control plane trivial and cheap — no process lifecycle, locks, or
container start/stop to manage.

## Metrics to track (v1)

Deliberately minimal to start; expand later.

| Metric | Source in original app |
|--------|------------------------|
| Emails processed | count of successfully handled `UNREAD` messages |
| Cards created | count of new Trello cards |
| Last-run timestamp | worker heartbeat at start/end of each run |
| Token health | validity of the stored Gmail refresh token (drives "Reconnect Gmail") |

## Scope / non-goals (for now)

- **In scope:** dashboard auth, Gmail OAuth broker, scheduled + on-demand worker, Trello
  integration, the four v1 metrics, worker on/off.
- **Out of scope / deferred:** the original app's self-update mechanism, contact form,
  attachment-download tab, and cross-platform (Windows/macOS) daemon lifecycle — all of
  which are laptop concerns that the cloud model removes or replaces.
- **Not yet decided:** full metric set (v1 is 4), and the actual monitored mailbox type
  (Workspace vs consumer) — the code path is account-agnostic (D16), only the OAuth
  publishing status depends on it. Cloud provider is **settled: Azure (D10).** See
  `tech.md` open questions.
