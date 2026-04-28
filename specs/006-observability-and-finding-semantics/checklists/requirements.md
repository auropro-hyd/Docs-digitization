# Pre-merge Requirements Checklist — Spec 006

All items must be ticked and linked to the commit / test / dashboard that proves them before merging Feature 006.

---

## Spec coverage

- [ ] **FR-001** `traceparent` + `X-Request-Id` on every response — verified by `test_middleware.py::test_outbound_headers`.
- [ ] **FR-002** `trace_id` on every log line across threads — `test_context_survives_executor.py`.
- [ ] **FR-003** Structured log schema — JSON mode test + dev mode parse test.
- [ ] **FR-004** `/metrics` endpoint reachable, p95 ≤ 200 ms — `test_metrics_endpoint.py` with load assertion.
- [ ] **FR-005** Closed metric set — `test_metrics_catalogue.py` drift test.
- [ ] **FR-006** Automatic context binding at scope entry — `test_bind_context_at_stage_entry.py`.
- [ ] **FR-007** Fail-open on observability errors — fault-injection test.
- [ ] **FR-008** Redaction of PII / oversized payloads — `test_redaction.py` fuzz.
- [ ] **FR-009** `/health` + `/health/ready` respond correctly — `test_health_endpoints.py`.
- [ ] **FR-010** BMR WS events carry `trace_id` — `tests/bmr/workflow/test_events_ws.py` updated.
- [ ] **FR-011** UI label "Auto-approved" → "System-confirmed" — frontend test asserts label text.
- [ ] **FR-012** `system_confirmed` never styled with success palette — `findings-table.test.tsx::forbidden_palette`.
- [ ] **FR-013** Explicit `unknown` state (server + client) — server test `test_missing_hitl_not_silently_approved`, client test `renders unknown badge`.
- [ ] **FR-014** Attribution-preserving dedup — `test_dedup_attribution.py` all three modes.
- [ ] **FR-015** Dedup observability — log event + metric asserted by test.
- [ ] **FR-016** `/metrics`, `/health*` outside auth gate — test with missing `X-Actor-Id` returns 200.
- [ ] **FR-017** Review-adjusted scoring unchanged on existing reports — regression test loads `f9f7e1b6-…` and asserts score is identical pre/post.

## Success criteria

- [ ] **SC-001** `grep trace_id=<hex>` returns whole request timeline — verified manually against a real run + `test_trace_propagation.py`.
- [ ] **SC-002** `/metrics` p95 ≤ 200 ms under 2× scrape — `test_metrics_endpoint.py`.
- [ ] **SC-003** No `text-success` / `bg-success/*` classes on `system_confirmed` badges — `findings-table.test.tsx::forbidden_palette`.
- [ ] **SC-004** No `auto_approved` fallback on missing state — `test_missing_hitl_not_silently_approved.py` + `renders unknown badge`.
- [ ] **SC-005** `sum(ar.total_findings) == |{f ∈ report.findings : f.agent≠None}|` in both dedup modes — `test_dedup_attribution.py`.
- [ ] **SC-006** Overhead benchmark within NFR-001 budget — `test_overhead_benchmark.py`.
- [ ] **SC-007** OTLP swap is one config line — demonstrated in `quickstart.md §6` against a local Jaeger (out-of-band validation).

## Non-functional

- [ ] **NFR-001** p95 overhead ≤ 5 ms — `test_overhead_benchmark.py` ran on a clean environment.
- [ ] **NFR-002** No unbounded label — `test_metrics_catalogue.py::no_banned_labels`.
- [ ] **NFR-003** No FastAPI imports in `app/bmr/` or `app/compliance/` — grep CI check.
- [ ] **NFR-004** Test isolation — `conftest.py` fixture resets registry; two concurrent tests cannot share counter state.
- [ ] **NFR-005** Docs current — `contracts/` matches code; drift tests green.

## Constitution

- [ ] Principle I (Leverage-first) — no compliance / BMR subsystem is replaced.
- [ ] Principle III (Capability-first) — observability exposes small, named capabilities; no god object.
- [ ] Principle VII (Backbone) — existing tests green; no new FastAPI dep in domain.
- [ ] Principle VIII (Audit trail) — `actor_id` / `ts` / immutable context on every log.
- [ ] Principle IX (Rule-as-data) — no client-specific labels/hues in Python; palette and labels in config / frontend only.

## Operational

- [ ] `/metrics` example scrape output captured in `quickstart.md`.
- [ ] OTLP migration path documented (`quickstart.md` §6) and verified on a local Jaeger in a follow-up experiment (not blocking merge).
- [ ] Ruff + pyright clean on backend; `npm run typecheck` clean on frontend.
- [ ] CHANGELOG entry added for the HITL display rename.
- [ ] Pre-existing SIM105 hints in `app/bmr/events/__init__.py` not introduced by this PR (carryover from PR #2).
