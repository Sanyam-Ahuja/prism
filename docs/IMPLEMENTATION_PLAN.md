# Production Implementation Plan

Derived from a full re-read of the clean (non-OCR) problem statement on 2026-09-21,
audited against the repo as it stands. Every gap below is evidenced by a test, a
measurement or a line of code — not inferred.

**Current state:** engine complete and measured; 37 tests; all §6 latency and
hygiene gates met. **Not submittable**, and materially under-covered on the
dimension the PS weights most.

---

## 0. What the clean re-read changed

The OCR copy I originally worked from mangled several passages. Re-reading the
clean scan confirmed the schema rules, the API contract and the pitfalls exactly
as implemented, and corrected three things:

1. **Appendix C §2 is explicit**: accuracy is "evaluated against reference ground
   truth scenarios across **Battery, Display, Camera, and Performance**". Our plan
   library covers **one of four**. This is the dominant scoring gap.
2. **Appendix C §2 names two judged metrics** — step accuracy (0.0–3.0) and
   deeplink relevance (0.0–2.0). `docs/metrics.md` never scores either; it
   substitutes our own retrieval tables. Good engineering, wrong rows.
3. **Appendix B's `meta` carries exactly four keys** (`latency_ms`, `cache_hit`,
   `model`, `cost_usd`). We emit six. `fallback` is mandated by §4.2.3; `tokens`
   is ours, justified by §6.3 but not present in the reference envelope.

---

## P0 — Submission validity

Without these the entry is either unsubmittable or violates a stated constraint.

### P0.1 Error boundaries (§4.2.4, §8 Phase 4) — **defect**

**Evidence:** injecting a `RuntimeError` into the cache lookup propagates
**uncaught** out of `POST /v1/troubleshoot`. In production that returns a 500 with
a non-JSON body, breaking "Pure JSON Delivery" and the Phase 4 requirement for
graceful fallbacks.

- Add a `@app.exception_handler(Exception)` returning the standard envelope with
  `fallback: "internal_error"`, HTTP 200 for a degraded answer or 503 for a
  genuinely unavailable subsystem — never a stack trace.
- Wrap the cold path so a mid-request Ollama failure degrades to `no_match`
  rather than propagating.

**Acceptance:** a fault-injection test asserts every response body parses as JSON
and matches the envelope, for failures in cache, segmenter, resolver and extractor.

### P0.2 Request limits — **defect**

**Evidence:** a 2 MB `siis_response` is accepted with HTTP 200. No size cap, no
end-to-end deadline; the cold path can run to httpx's 30 s timeout, far past the
8 s budget.

- `max_length` on `query` (1 KB) and `siis_response` (256 KB) via pydantic.
- A hard request deadline (~9 s) that returns `no_match` rather than overrunning.

**Acceptance:** oversized input returns 422 with a JSON body; a stalled extractor
returns inside the deadline with a valid envelope.

### P0.3 Container actually builds (§2 component 4, §8 Phase 4)

**Evidence:** `Containerfile` copies `vendor/bge-small-en-v1.5`, which does not
exist. It has never been built.

- Add `scripts/vendor_models.py` to snapshot the encoder into `vendor/`.
- Build under podman, run the image, hit `/health` and `/v1/troubleshoot`.
- **Measure cold start** — the PS names "dependable cold-start handling" and it is
  currently unmeasured.

**Acceptance:** `podman build` succeeds from a clean clone; container reaches
`/health` 200 with no network; cold-start seconds recorded in `metrics.md` §3.

### P0.4 Repository and reproducibility

- `git init`, `.gitignore` (exclude `.venv/`, `vendor/`, `results.jsonl`,
  `__pycache__/`), initial commit, submission tag.
- Generate `requirements.lock` (`uv pip compile`); reconcile `TECH_PLAN.md`, which
  currently claims a lock file that does not exist.
- Secret hygiene sweep before the first commit.

**Acceptance:** clean clone → `make venv && make build && make test` green, and
`artifacts/` reproduces byte-for-byte.

---

## P1 — The dominant scoring gap

### P1.1 Four-domain generalization (§3, Appendix C §2)

All 20 supplied queries are Display. Judged accuracy spans four domains.

**What we will not do:** author fake Battery/Camera/Performance SIIS documents and
compile them into the shipped cache. That manufactures coverage we were not given
data for, and is precisely the pattern the event reviews for. Plan coverage is a
function of supplied SIIS text (see M-Q4).

**What we will do:**

1. **Prove domain-independence.** Audit every module for Display-specific
   assumptions: the `_CRITICAL`/`_MANUAL` keyword tables, the appliance denylist,
   `_IMPERATIVE_VERBS`, topic templates, the device-model regex.
2. **Probe corpus.** Author Battery/Camera/Performance *probe* documents in
   `tests/fixtures/probes/`, clearly marked as synthetic test input, never
   compiled into `artifacts/`. Drive them through the cold path and assert valid,
   gate-passing plans.
3. **Wire the domain diagnostics.** `DL-0474/0475/0476` (Diagnose Battery Drain /
   Slow Performance / Overheating) are `originalType: null` read-only entries,
   currently excluded from candidacy. They are legitimate **validation** targets
   for those domains and should be reachable as such.
4. **Extend the catalog labels** with Battery/Camera/Performance descriptors so
   Stage 3 precision is measured outside Display.

**Acceptance:** cold path produces gate-passing plans in all four domains; deeplink
label set covers all four; `metrics.md` reports per-domain results.

### P1.2 Judged-metric self-assessment (Appendix C §2)

The two rubric scores the template asks for are unfilled.

- Define a written rubric: **step accuracy 0–3** (completeness, correctness,
  ordering) and **deeplink relevance 0–2** (exact screen = 2, parent menu = 1,
  wrong/absent = 0).
- Score all 11 compiled plans against their source documents, publish per-plan
  scores and the mean, and record the rubric so the number is auditable.
- Report `dummy_positive` rate separately — it is neither a hit nor a miss.

**Acceptance:** `metrics.md` §2 contains the template's two rows with real scores
and a link to the rubric and per-plan detail.

---

## P2 — Template completeness

### P2.1 Ablation Baseline variant (Appendix C §5)

The template names three variants. A and B are measured; **Baseline (full-LLM
deeplink mapping)** is currently argued away rather than run — the weakest part of
our report.

- Implement it behind a flag: let the extractor emit URIs directly, no retrieval.
- Measure on the same labelled sets. The expected result is catastrophic catalog
  integrity, and *demonstrating* that is far stronger evidence for our design than
  asserting it.
- Align the table's first column to the template's **Step Accuracy**, reporting
  deeplink relevance alongside rather than in place of it.

### P2.2 Envelope decision

Decide and document whether `meta.tokens` stays. §6.3 requires tracking token
utilization per query; Appendix B's envelope does not show it. Recommendation:
keep it (additive, satisfies §6.3 directly) and note the deviation explicitly in
`PS_ALIGNMENT.md`. Revisit if M-Q3 comes back saying the grader validates `meta`
strictly.

---

## P3 — Production hardening

| item | why |
|---|---|
| Replace deprecated `@app.on_event("startup")` with a `lifespan` handler | FastAPI deprecation; also lets startup failure serve 503 instead of crashing |
| Structured logging with a request id; log cache hit/miss, latency, fallback reason | Nothing is currently observable in production |
| Concurrency check: N parallel requests against the hot path | P95 is measured single-threaded; the encoder is shared mutable state |
| `/health` distinguishes degraded (cache up, extractor down) from down | Today the extractor being unreachable fails the whole check, though the hot path still works |
| Pin the encoder revision explicitly | Reproducibility; today it resolves to whatever HF serves |

---

## Sequencing

```
P0.4 git ──┬─► P0.1 error boundaries ──┐
           ├─► P0.2 request limits ────┼─► P0.3 container + cold start
           └─► P3 lifespan ────────────┘
                                        │
P1.1 domain audit ──► P1.1 probes ──────┴─► P1.2 rubric scoring ──► P2.1 baseline ablation
                                                                  └─► final metrics.md
```

P0 is a single focused pass and unblocks everything. P1.1 is the largest body of
work and the only item that moves the dominant scoring dimension. P2.1 is cheap
and materially strengthens the report.

## Definition of done

1. Clean clone builds, tests green, artifacts byte-reproducible.
2. Container builds and serves with no network; cold start measured.
3. No response path can emit a non-JSON body or an unhandled exception.
4. Gate-passing plans demonstrated in all four PS domains.
5. `metrics.md` complete per Appendix C, including both judged rubric scores and
   all three ablation variants.
6. `PS_ALIGNMENT.md` shows no ❌ rows, with every ⚠️ carrying a stated reason.
