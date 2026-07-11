# Steering Docs Audit — 2026-07-11

Parallel multi-critic audit of the steering/spec documentation. Four independent critics judged
different dimensions (cross-reference integrity, contradictions, constitution alignment,
gaps/engineering-risk); findings below were reconciled and **every cited `file:line` was opened and
verified by hand** before inclusion.

**No documentation was modified by this audit.** These are findings for triage only.

Audited: `.specs/steering/{constitution,product,tech,structure}.md`, `.specs/azure-implementation.md`,
`.specs/walking-skeleton/spec.md` (~985 lines).

---

## Summary

| # | Severity | Location(s) | Finding | Raised by |
|---|----------|-------------|---------|-----------|
| 1 | **Should-fix (High)** | `tech.md:129-153` vs `azure-implementation.md:16,22` | **D13 missing from the master decision log** — SoT break | 3 of 4 critics |
| 2 | **Should-fix** | `constitution.md` (Art. 4) ↔ `tech.md:170-186` | "Idempotency **by default**" (absolute) vs D12 deferring the concurrent-run fix | Constitution + Risk |
| 3 | **Should-fix** | `tech.md:41`, `azure-implementation.md:128` vs `product.md:40` | Stale **"Connect Google"** label contradicts D18 "no separate step" | Contradiction |
| 4 | **Should-fix** | `tech.md:49-52` vs `azure-implementation.md:105` | tech.md secret-store diagram **omits the session signing key** (needed for D22 JWT) | Contradiction |
| 5 | **Should-fix** | all docs (absence) | No **error-handling / retry policy** for partial pipeline failure | Risk |
| 6 | **Should-fix** | all docs (absence) | No **heartbeat-staleness / "timer stopped" alert** — Smart Detection can't see silent stop | Risk |
| 7 | **Should-fix** | all docs (absence) | No **`ClaimHistory` retention / PII policy** — rows persist forever, claims are personal data | Risk |
| 8 | **Should-fix** | `tech.md:64`, `spec.md:192` | Auth flow is "the crux" yet has **no documented test strategy** (only scoped *out*) | Risk |
| 9 | Nit | `tech.md:129-153` | Decision log **not in numeric order** (obscures the D13 gap) | 2 critics |
| 10 | Nit | `tech.md:51` | Trello board/list **IDs shown as secrets**; D23 puts them in a Table row, not Key Vault | Contradiction |
| 11 | Nit | `spec.md:78,107` | REQ-3 & REQ-6 acceptance rely on **manual review, no automated verifier** | Risk (EARS) |
| 12 | Nit | various | App Insights absent from tech.md diagram; CORS origin unpinned; allowlist change needs redeploy | Risk |

**No Blockers.** No finding contradicts *what work should happen next*; all are documentation
completeness/consistency issues or deferred-risk acknowledgements.

---

## Details

### 1. D13 is missing from the master decision log — Should-fix (High)
`azure-implementation.md:16` states "Master log lives in `steering/tech.md`", and its table
(`azure-implementation.md:22`) lists **D13 = Observability = Application Insights, no mounted
volume**. But the master log in `tech.md:129-153` runs D1–D12 then jumps straight to D14 — **there
is no D13 row**. So the downstream provider doc defines a decision the authoritative log never
records. The decision *content* is stated consistently in prose (`azure-implementation.md:213`), so
nothing is lost or contradictory — it's a traceability/single-source-of-truth gap.
*Fix direction:* add the D13 row to `tech.md`'s log. (Independently flagged by 3 of 4 critics —
strong signal.)

### 2. "Idempotency by default" vs deferred concurrency (D12) — Should-fix
`constitution.md` Article 4 phrases idempotency as an absolute default ("any run — scheduled,
on-demand, or retried — is safe to repeat without duplicate cards"). But `tech.md:170-186` (D12)
knowingly **defers** concurrency control and admits the label-dedup "is not atomic — there's a
read→process→relabel race window" that can produce duplicate Trello cards when the on-demand
"process now" path overlaps the timer. The deferral rationale (low volume, single operator) is
reasonable, but a reader taking the constitution literally would conclude the design violates its
own article. *Fix direction:* amend the article to mark D12 as a sanctioned, scoped exception
(single-run/retry idempotency preserved; concurrent-run deferred) — **or** pull D12 option (c)
forward, since the idempotent-create fix is nearly free (ClaimHistory point-read already exists,
`azure-implementation.md:72`) and the overlap it guards against is a v1 feature.

### 3. Stale "Connect Google" label — Should-fix
`product.md:40` states plainly: "No separate 'Connect Google' step." Yet the `tech.md:41` component
diagram still lists "Connect Google" as a frontend capability, and `azure-implementation.md:128`
still describes the operator clicking a **"Connect Google"** button (while its own heading at
`:122` and endpoint at `:146` correctly frame it as one unified login). Leftover pre-D18 wording in
two places. *Fix direction:* align the label with D18's single-consent model.

### 4. Session signing key missing from tech.md secret-store diagram — Should-fix
The D22 JWT flow cannot mint tokens without a session signing key. `azure-implementation.md:105`
correctly lists "session key" in Key Vault, but the `tech.md:49-52` secret-store box lists only
Gmail refresh token, Trello creds, and OAuth client secret — **no session key**. A reader of
tech.md alone wouldn't know it must be stored. *Fix direction:* add it to the tech.md diagram.

### 5–8. Missing operational sections — Should-fix cluster
The docs are disciplined about flagging their *own* unknowns, so the real risks are topics never
written down:
- **5. Failure/retry policy:** no doc says what happens when a step fails mid-run (e.g. PDF ok →
  Trello create fails → email already relabeled). Whether a `failed` email is ever retried is
  undefined. Largest architectural gap.
- **6. Silent-failure alerting:** Smart Detection (D20) fires on exception-rate anomalies, so a
  timer that stops firing (zero exceptions) is invisible. A Heartbeat row exists
  (`azure-implementation.md:68`) but nothing watches it. Also covers the stale-refresh-token
  silent-outage risk.
- **7. Retention/PII:** App Insights retention is set (~90 days) but `ClaimHistory` grows unbounded
  with no purge policy and no PII statement, despite processing claimant personal data.
- **8. Auth-flow test strategy:** the highest-risk surface (`tech.md:64` "the crux") is scoped
  *out* of the skeleton (`spec.md:192`) but never scoped *in* anywhere — no plan to test OAuth
  callback, JWT mint/validate, allowlist enforcement, or token-refresh failure. Sits in tension
  with constitution Article I (tests-before-code).

### 9–12. Nits
- **9.** Decision log (`tech.md:129-153`) is non-sequential (D12, D14, D10, D11, D15… D19-D21 last),
  which is what masked the D13 gap.
- **10.** `tech.md:51` shows "Trello key+token+**ids**" in the secret store, but D23/`azure-
  implementation.md:178-180` deliberately keep board/list IDs in a Table row, not Key Vault.
- **11.** REQ-3 (`spec.md:78`) and REQ-6 (`spec.md:107`) acceptance is "presence/structural review,
  no automated test" — honest, but 2 of 6 skeleton REQs rely on manual verification. Also REQ-5's
  CI gate proves only the green path, never that a *failing* PR is blocked.
- **12.** App Insights absent from the tech.md diagram (acceptable altitude abstraction); CORS
  allows "the SWA origin" but the dynamic `*.azurestaticapps.net` origin isn't pinned
  (`azure-implementation.md:161`); changing the allowlisted operator requires a redeploy (D25).

---

## Clean passes (verified, no action)
- **Cross-reference consistency:** all 20+ D-number citations outside `tech.md` match their master
  meaning — no doc *redefines* a decision (only D13 is missing, not divergent).
- **Markdown links:** every `[text](path)` link resolves to a file on disk; no dangling links.
- **State store, cost model, trigger/on-off switch, secrets flow:** consistent across all docs.
- **Constitution Articles 2, 5, 6, 7, 9:** cleanly honored (serverless-first, least-privilege,
  edge-only auth, trivial control plane, spec-before-code with rigorous RED→GREEN task ordering).
- **Walking-skeleton REQ→task traceability:** every REQ maps to a task and vice-versa.

## Corrections to this audit's own starting assumptions
The plan speculated D16 was "not yet named" and D1–D9 might be orphans. **Both were wrong** —
`tech.md:143` fully defines D16 (Decided), and `tech.md:129-137` explicitly defines D1–D9. The
parallel critics corrected the premise. The one real integrity defect is D13, not D16.

---

## Open questions for Mr Rodriguez
1. **Finding 1 (D13):** confirm the intended text for a D13 row in `tech.md`, or is the master-log
   claim in `azure-implementation.md:16` the thing to soften instead?
2. **Finding 2 (idempotency):** amend Article 4 to sanction D12 as a scoped exception, or pull the
   near-free idempotent-create fix forward now?
3. **Findings 5–8:** which operational gaps (retry policy, silent-failure alert, retention/PII,
   auth test strategy) do you want captured in the steering docs vs. deferred to a later feature
   spec?
4. Do you want me to turn any accepted findings into actual doc edits (a separate, approved step)?
