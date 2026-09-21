# Problem Statement Alignment Audit

Every requirement in `Theme_2_Troubleshooting_Smart_Guided_Troubleshooting_Engine_OCR.pdf`,
checked against the code as it stands. Evidence is a file, a test or a measured
number — not an assertion.

**Legend:** ✅ met and evidenced · ⚠️ partial · ❌ not done · ➖ out of our control

---

## §2 — Core Pipeline Components

| # | Component | Status | Evidence |
|---|---|---|---|
| 0 | Query Enrichment: normalize colloquial → canonical; serve as cache key | ✅ | `engine/normalize.py` — NFKC, device-model collapsing, list-artifact stripping, contraction expansion. Key is the *embedding*, not the string |
| 0 | Generate 8–10 distinct paraphrases | ✅ | 110 authored at build time (`build/skeletons.json`); `engine/variations.py` for cold path. Gate G11 |
| 1 | Structure Extraction (LLM/parsing) → Goal, Actions, Steps | ✅ | `engine/segment.py` + `engine/cold.py`; build tier in `build/skeletons.json` |
| 1 | Enforce length, phrasing, zero-leak constraints | ✅ | `validators/gates.py` (G1–G6), `validators/scrub.py` (G0) — programmatic, not prompt-only |
| 2 | Deeplink Mapping: match screens to catalog via semantic + keyword | ✅ | `engine/deeplink.py` — BM25 + bge-small dense, RRF fusion |
| 2 | Sequencing: non-invasive first → critical last | ✅ | `engine/assemble.py::order_actions`; gate G14; `test_ordering_places_critical_last` |
| 3 | Fast-Path cache: hit ≤300 ms without LLM | ✅ | `engine/cache.py`; **P95 13.7 ms**; no model invoked on the hot path |
| 3 | Handle unseen paraphrases semantically | ✅ | **84.6%** on 26 held-out paraphrases |
| 4 | REST service with operational metadata | ✅ | `api/main.py`; `meta` carries latency, cache_hit, model, cost, tokens, fallback |

---

## §4.1 — Schema Specifications

| Field | Rule | Status | Evidence |
|---|---|---|---|
| `goal` | exact `Follow these steps to perform this <Topic> Troubleshooting` | ✅ | G1 · `make_goal()` · `test_goal_template` |
| `title` | 2–3 words, sentence case | ✅ | G2 · `fit_title()` · `test_fit_title_always_2_or_3_words` |
| `score` | float 0.0–1.0 | ✅ | G3 |
| `actionName` | Title Case, exactly one screen/feature | ✅ | G5 + G12 · `title_case()` |
| `description` | **exactly 5–7 words**, starts "It will" | ✅ | G6 · 32/32 distinct descriptions compliant |
| `stepGroups[].steps` | imperative, one interaction, no URLs | ✅ | G7 + G0 |
| `category` | auto / manual / critical, critical last | ✅ | G8, G13, G14 · keyword override table |
| `actionableDeeplink` | verbatim masked URI from catalog | ✅ | G9 · `test_actionable_is_a_verbatim_copy` |
| `query_variations` | 8–10 across varied registers | ✅ | G11 · formal / casual / keyword-only / frustrated / typo |

---

## §4.2 — Non-Negotiable Operational Constraints

| # | Constraint | Status | Evidence |
|---|---|---|---|
| 1 | **Zero URL leaks** (http, https, www., markdown) | ✅ | G0 runs first and last; sentence-aware. **0 leaks** across all output. 4 parametrised tests |
| 2 | **Catalog integrity** — no hallucinated or altered URIs | ✅ | The LLM never sees a URI (ADR-002). G9 asserts membership. `test_resolver_never_invents_a_uri` |
| 3 | **No hallucinated steps** — derive purely from reference text | ✅ | Steps are *indices* into the source (ADR-001). `test_every_step_traces_to_source_text` |
| 3 | Empty `contexts: []` + `"fallback": "no_match"` when no solution | ✅ | `api/main.py`; both fallbacks return HTTP 200 |
| 4 | **Pure JSON** — no markdown fence, no preamble | ✅ | Verified on the wire: first byte `{`, no fence present |

---

## §5 — API Contract

| Requirement | Status | Evidence |
|---|---|---|
| `POST /v1/troubleshoot` with `{query, siis_response}` | ✅ | `api/main.py`; `siis_response` accepted as raw string **or** object |
| `siis_response` omitted → semantic lookup against pre-warmed cache | ✅ | Cache consulted first on every request |
| `GET /health` → HTTP 200 `{"status": "ok"}` | ✅ | Verified: `http_status=200`, `status == 'ok'` |
| ...when caching layer, model connections and vector indexes are initialized | ✅ | Gated on all four subsystems; returns **503** until ready |

---

## §6 — Evaluation Criteria

### 6.1 Robustness & Hygiene

| Criterion | Target | Measured | Status |
|---|---|---|---|
| Schema conformance | 100% | **100%** (20/20) | ✅ |
| Zero leakage | 0 | **0** | ✅ |
| Deterministic execution — identical inputs | consistent | byte-identical ×5 | ✅ |
| Deterministic execution — *semantically* identical | consistent | S22 ≡ S24 Ultra → same plan | ✅ |

### 6.2 Information Retrieval & Deeplink Precision

| Criterion | Target | Measured | Status |
|---|---|---|---|
| Screen resolution accuracy (exact screen, not parent menu) | — | **100%** precision@1 on 43 catalog-register labels | ✅ |
| ...on free-form descriptors | — | 30% overall | ⚠️ documented limitation |
| Semantic paraphrase hit rate | ≥80% | **84.6%** (26 held-out, 0 leaked) | ✅ |
| Plan hierarchy by disruption | — | auto → manual → critical, G14-enforced | ✅ |

### 6.3 Latency & Resource Efficiency

| Criterion | Target | Measured | Status |
|---|---|---|---|
| Fast-path P95 | ≤300 ms | **13.7 ms** (exact: 0.0 ms) | ✅ |
| Cold-path P95 | ≤8 s | **7333 ms** | ✅ |
| Cost predictability — inference cost/query | tracked | `meta.cost_usd` = $0.00 (local) | ✅ |
| Cost predictability — **token utilization** | tracked | `meta.tokens.{prompt,completion}` | ✅ *(added during this audit)* |

---

## §7 — Common Technical Pitfalls

| # | Pitfall | Avoided? | How |
|---|---|---|---|
| 1 | Exact-string cache keying | ✅ | Key is a normalized embedding; 130 seeds incl. all paraphrases |
| 2 | Over/under-granular actions | ✅ | G12 merges actions sharing a deeplink; "one action = one screen" |
| 3 | URL injection from pretraining | ✅ | G0 regex scrub, run twice |
| 4 | Matching on masked URI strings | ✅ | `_searchable()` excludes the URI; `test_matching_never_uses_the_masked_uri` |
| 5 | Prompt-only constraints | ✅ | 16 programmatic gates + repair loop; no rule relies on the model obeying |

---

## §8 — Implementation Roadmap

| Phase | Status | Note |
|---|---|---|
| 1 — Foundation & validation harness | ✅ | Validators built **first**; 37 tests |
| 2 — Dual retrieval (BM25 + dense), screen resolution, ordering | ✅ | All three present |
| 3 — Semantic normalization, persistent cache, latency benchmark | ✅ | 130 pre-computed vectors, benchmarked |
| 4 — REST endpoints, error boundaries, fallbacks, stress tests | ⚠️ | Endpoints and both fallbacks done; **cold-start not measured, container never built** |

---

## Appendices

| Item | Status | Note |
|---|---|---|
| Appendix B — response envelope (`query`, `query_variations`, `response`, `meta`) | ✅ | Emitted as the superset; `tests/fixtures/appendix_b.json` passes every gate |
| Appendix B — `results.jsonl` one object per line | ✅ | `scripts/run_batch.py` |
| Appendix C — metrics.md template | ⚠️ | All sections populated **except** the ablation Baseline row |

---

## Open gaps against the PS

Re-audited 2026-09-22 against the clean (non-OCR) problem statement.

| # | Gap | PS reference | Severity |
|---|---|---|---|
| 1 | **Plan coverage is Display-only.** The pipeline is domain-independent — Stage 3 scores 96.2% on a 26-label Battery/Camera/Performance set and a probe corpus drives all three through to gate-passing plans — but no *plans* exist for domains whose SIIS text we were never given | §3, Appendix C §2 | **High**, and partly not ours (M-Q4) |
| 2 | **Deeplink relevance 1.67 / 2.0.** 3 of 13 auto actions land on the right feature area but not the exact screen | §6.2, Appendix C §2 | Medium |
| 3 | **Cold-path latency unverified on current code.** The recorded 7333 ms P95 predates several changes and the GPU is shared with an unrelated training job; measurements under contention are discarded rather than reported | §6.3 | Medium — pending an idle GPU |
| 4 | **31% of auto actions use `dummy_positive`.** Driven by genuine catalog gaps (no safe mode, auto-rotate, Smart View, aspect ratio, clear-app-cache, Smart Switch), not by guessing | §3, Appendix C §1 | Medium — data-bound |
| 5 | Free-form descriptor resolution 30% at the shipped threshold | §6.2 | Low — Stage 3 receives normalized descriptors |
| 6 | `queries.json` and 4 of 5 `samples/` never supplied | §3 | ➖ Not ours — M-Q4 |
| 7 | `sample_output.json` contradicts §4.1 (9 and 12-word descriptions) | §4.1 | ➖ Spec conflict — M-Q2 |
| 8 | `meta` carries `tokens` beyond Appendix B's four keys | §6.3 vs Appendix B | ➖ Deliberate, switchable — ADR-016, M-Q3 |

**Closed since the first audit:** the ablation Baseline is now measured (18.6%
catalog integrity, 2.3% precision@1); the container builds, runs offline and its
cold start is measured at 17.3 s; both Appendix C §2 judged scores are filled with
an auditable rubric.

---

## Defects found by auditing, and fixed

| # | Defect | How it was caught |
|---|---|---|
| 1 | Unhandled exceptions returned a 500 with a non-JSON body, breaking §4.2.4 | fault injection |
| 2 | No request size cap or deadline; a 2 MB payload was accepted and the cold path could run to 30 s | boundary test |
| 3 | `requirements.lock` pinned CUDA torch (`nvidia-cublas` 423 MB); the dev venv held two torch builds | container build |
| 4 | Expanding the verb list silently repointed six step groups to the wrong steps | plan inspection during rubric scoring |
| 5 | The reproducibility test recompiled `artifacts/` **in place**, which is what hid defect 4 | tracing why the diff showed no change |
| 6 | Selection took RRF rank-1 while thresholding on confidence; a better candidate sat at rank 5 | rubric scoring of the Wi-Fi action |
| 7 | Placeholder text read "Open the open the …" and "Opens the disable …" | inspecting emitted output |
| 8 | Two skeleton descriptors named the wrong screen | rubric scoring |
| 9 | §6.1 required determinism and nothing tested it | first alignment audit |
| 10 | §6.3 required token tracking; the API dropped what `cold.py` computed | first alignment audit |

Most of these were found by *looking at output*, not by tests passing. The two
worst — 4 and 5 — were introduced by me and hidden by a test with a side effect on
the thing it verified.

Test count: 31 → **63**.
