# Constitution — Claim Automation (Cloud)

Guiding principles for this project. When a decision is ambiguous, these win. Referenced
by `product.md` and `tech.md`.

## Principles

1. **Azure is the chosen provider (D10).** Design in terms of roles and building blocks
   (trigger, worker, secret store, state store, frontend) so the *pure logic* stays
   portable, but concrete Azure services are now authoritative — see
   `../azure-implementation.md`. Other clouds (GCP/AWS) are reference points only, not
   supported targets.

2. **Serverless-first for cost.** Prefer pay-per-use compute over always-on
   infrastructure. Trade latency for cost deliberately and document the trade-off. An
   always-on component must justify itself against the 30-min scheduled model.

3. **Reuse over reinvention.** The claim-processing logic (email parsing, `ClaimType`
   classification, PDF generation, Trello REST calls) already works in
   `../claim_automation`. Port these pure-logic modules unchanged; re-platform only the
   I/O boundaries (auth, triggers, state, secrets, UI).

4. **Idempotency by default.** Preserve Gmail-label-based dedup so any run — scheduled,
   on-demand, or retried — is safe to repeat without duplicate cards.

5. **Least-privilege secrets.** All credentials (Gmail refresh token, OAuth client
   secret, Trello key/token) live in a managed secret store, never in source, config
   files, or the repo. Request the narrowest Gmail scopes the pipeline needs.

6. **Interactive auth only at the edge.** Any step needing a human browser (OAuth consent)
   happens in the dashboard. Backend/worker components must run fully non-interactively.

7. **Control plane stays trivial.** Operational control (on/off) is expressed as data (an
   `enabled` flag), not as infrastructure lifecycle. No process locks, no start/stop of
   containers to manage worker state.

8. **Simplicity first.** Start with the minimal viable set (4 metrics, one operator, two
   triggers). Expand only when a concrete need appears. Favor the simplest design that
   works.

9. **Spec before code.** This is a spec-driven-dev project. Systems design lives in
   `.specs/steering/`; each buildable slice gets its own `.specs/{feature}/spec.md` — a
   **single-file lite spec** (requirements → design → tasks inline) — before
   implementation. *(Amended 2026-07-14: lite format is the standard for all features;
   split into separate requirements/design/tasks files only if a spec.md becomes
   unwieldy.)*

10. **Triangulated review before implementation.** Before a `.specs/{feature}/` slice moves
    from design to implementation — and whenever steering docs change materially — run the
    triangulated multi-critic review gate: the `spec-review` skill fans out into *independent*
    critics (cross-reference integrity, contradiction, constitution alignment, engineering
    risk) whose findings are reconciled and triaged by a human before code is written. The
    gate flags; a human decides. The `.specs`-edit reminder hook surfaces this at the moment
    docs change.

    The cross-reference and link checks within this gate are deterministic and MAY later be
    automated in CI (no LLM needed); until such a checker exists they are covered by the
    review above.

11. **Tests before code (TDD).** No implementation code is written without a failing
    test first — RED → GREEN → REFACTOR. Test tasks precede implementation tasks in every
    spec; per-stack testing conventions live in `structure.md`. *(Added 2026-07-14: this
    principle was cited widely as "Article I" but had never been written down.)*

## Decision authority

- Steering files (`product.md`, `tech.md`, this file) are authoritative for
  project-wide intent.
- Where code and steering conflict, flag the inconsistency and resolve it explicitly —
  do not silently follow one over the other.
