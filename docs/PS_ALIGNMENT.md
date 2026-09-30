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
| 3 | Fast-Path cache: hit ≤300 ms without LLM | ✅ | `engine/cache.py`; **P95 28.6 ms** paraphrase / 0.3 ms exact in-process (30.9 / 7.9 ms over HTTP); no model invoked on the hot path |
| 3 | Cache miss: "validate schema, **write to cache**" | ⚠️ | Each validated cold plan is kept under its article (ADR-019): a repeat of that article gets it without the model. Writing it into the *semantic* cache, where other callers' similar queries would get it, is deliberately not built — a plan built from one caller's `siis_response` would be served to others (see open gaps) |
| 3 | Handle unseen paraphrases semantically | ✅ | **84.6%** on the 26 calibration paraphrases · **100%** (92.6% to the right plan) on 27 test paraphrases written afterwards |
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
| 1 | **Zero URL leaks** (http, https, www., markdown) | ✅ | G0 runs first and last; sentence-aware. The complaint is stripped of links on input, since the response echoes it, and 422 errors do not echo the payload. **0 leaks** across all output. 4 parametrised tests + 4 for the complaint path |
| 2 | **Catalog integrity** — no hallucinated or altered URIs | ✅ | The LLM never sees a URI (ADR-002). G9 asserts membership. `test_resolver_never_invents_a_uri` |
| 3 | **No hallucinated steps** — derive purely from reference text | ✅ | Steps are *indices* into the source (ADR-001). `test_every_step_traces_to_source_text` |
| 3 | Empty `contexts: []` + `"fallback": "no_match"` when no solution | ✅ | `api/main.py`; both fallbacks return HTTP 200 |
| 4 | **Pure JSON** — no markdown fence, no preamble | ✅ | Verified on the wire: first byte `{`, no fence present |

---

## §5 — API Contract

| Requirement | Status | Evidence |
|---|---|---|
| `POST /v1/troubleshoot` with `{query, siis_response}` | ✅ | `api/main.py`; `siis_response` accepted as raw string **or** object |
| `siis_response` omitted → semantic lookup against pre-warmed cache | ✅ | Without `siis_response` the semantic cache answers, one plan per problem named (ADR-021). With it, the article decides (ADR-019) — the plan must derive from the provided text (§4.2.3) |
| `GET /health` → HTTP 200 `{"status": "ok"}` | ✅ | Verified: `http_status=200`, `status == 'ok'` |
| ...when caching layer, model connections and vector indexes are initialized | ✅ | Gated on all four subsystems; returns **503** until ready |

---

## §6 — Evaluation Criteria

### 6.1 Robustness & Hygiene

| Criterion | Target | Measured | Status |
|---|---|---|---|
| Schema conformance | 100% | **100%** (20/20 batch · 325/325 over HTTP) | ✅ |
| Zero leakage | 0 | **0** | ✅ |
| Deterministic execution — identical inputs | consistent | byte-identical ×5 | ✅ |
| Deterministic execution — *semantically* identical | consistent | S22 ≡ S24 Ultra → same plan | ✅ |
| Deterministic execution — cold path | consistent | the first validated plan for an article is returned for every repeat of it (ADR-019, `test_repeated_article_gets_the_same_plan_without_the_model`). Underneath, the model still gives 4 of 11 articles a different plan once its server has cached their prompt (`reports/determinism.txt`), which now only affects the first request per article after a restart | ✅ |

### 6.2 Information Retrieval & Deeplink Precision

| Criterion | Target | Measured | Status |
|---|---|---|---|
| Screen resolution accuracy (exact screen, not parent menu) | — | **94.7%** precision@1 (95.3% overall) on 43 Display labels · **100%** on 26 Battery/Camera/Performance labels | ✅ |
| ...on free-form descriptors | — | 30% overall | ⚠️ documented limitation |
| Semantic paraphrase hit rate | ≥80% | **84.6%** (26 calibration paraphrases, 0 leaked) · **100%** (27 test paraphrases, 92.6% to the right plan) | ✅ |
| Out-of-scope complaints kept out of the cache | — | false hits 0/12 calibration · 1/38 test, with the out-of-scope anchors (ADR-020); 1/12 and 2/38 without | ✅ |
| Plan hierarchy by disruption | — | auto → manual → critical, G14-enforced | ✅ |

### 6.3 Latency & Resource Efficiency

| Criterion | Target | Measured | Status |
|---|---|---|---|
| Fast-path P95 | ≤300 ms | **28.6 ms** (exact: 0.3 ms), sequential · 232 ms with 8 concurrent clients | ✅ |
| Cold-path P95 | ≤8 s | **5215 ms** (`qwen2.5:1.5b`, N=33; 5413 ms over HTTP) | ✅ |
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
| 4 — REST endpoints, error boundaries, fallbacks, stress tests | ✅ | `scripts/stress_api.py`: 325/325 schema-valid responses over HTTP, cold start 21.7 s (29.2 s before ADR-023), malformed input answered with JSON 422; container cold start 17.3 s (measured earlier on Linux) |

---

## Appendices

| Item | Status | Note |
|---|---|---|
| Appendix B — response envelope (`query`, `query_variations`, `response`, `meta`) | ✅ | Emitted as the superset; `tests/fixtures/appendix_b.json` passes every gate |
| Appendix B — `results.jsonl` one object per line | ✅ | `scripts/run_batch.py` |
| Appendix C — metrics.md template | ✅ | Follows the template verbatim: same sections, tables, rows and columns, every cell filled from `reports/` (re-measured 2026-09-30). Supporting tables in `reports/README.md` |

---

## Open gaps against the PS

Re-audited 2026-09-22 against the clean (non-OCR) problem statement; numbers
refreshed 2026-09-30 after the extractor change (ADR-017).

| # | Gap | PS reference | Severity |
|---|---|---|---|
| 1 | **Plan coverage is Display-only.** The pipeline is domain-independent — Stage 3 scores 100% on a 26-label Battery/Camera/Performance set and a probe corpus drives all three through to gate-passing plans — but no *plans* exist for domains whose SIIS text we were never given | §3, Appendix C §2 | **High**, and partly not ours (M-Q4) |
| 2 | **Cold plans are not written back to the *semantic* cache.** They are kept per article (ADR-019), so the same reference text always gets the same plan, but the PDF's miss path ends "write to cache": a shared semantic cache would serve a plan built from one caller's `siis_response` to other callers' similar queries, so that part needs a decision, not just code | §2 [3] | **High** — needs a decision |
| 3 | **Cold-path deeplink coverage is 19%** (12 of 63 auto actions link a specific screen, 75% of them agreeing with the compiled plan; 6% before the step-target probe, ADR-022) against 69% for compiled plans. The 1.5B extractor rarely names a specific screen, and the grounding check refuses the ones it copies from the prompt (ADR-018) | Appendix C §1, §6.2 | Medium |
| 5 | **Deeplink relevance 1.78 / 2.0.** 2 of 13 compiled auto actions land on the right feature area but not the exact screen (Edge panels: the catalog has only enable/disable entries) | §6.2, Appendix C §2 | Low |
| 6 | **TV entries are not filtered.** "enable adaptive brightness" resolves to DL-0498, a TV Settings entry; the appliance filter misses the catalog's 18 TV entries | §6.2 | Medium |
| 7 | **31% of auto actions use `dummy_positive`.** Driven by genuine catalog gaps (no safe mode, auto-rotate, Smart View, aspect ratio, clear-app-cache, Smart Switch), not by guessing | §3, Appendix C §1 | Medium — data-bound |
| 8 | **Hot path shares the CPU:** 8-client P95 217 ms on a quiet machine, 232 ms in the final run, 320 ms in a busier run with no code change; sequential P95 is 29 ms | §6.3 | Low |
| 9 | Free-form descriptor resolution 30% at the shipped threshold | §6.2 | Low — Stage 3 receives normalized descriptors |
| 10 | `queries.json` and 4 of 5 `samples/` never supplied | §3 | ➖ Not ours — M-Q4 |
| 11 | `sample_output.json` contradicts §4.1 (9 and 12-word descriptions) | §4.1 | ➖ Spec conflict — M-Q2 |
| 12 | `meta` carries `tokens` beyond Appendix B's four keys | §6.3 vs Appendix B | ➖ Deliberate, switchable — ADR-016, M-Q3 |
| 13 | **A second problem no plan covers is dropped without notice.** Complaints naming several known problems get one plan each (ADR-021), but "…and the battery dies fast" adds nothing and the response does not say so; saying it would need a `meta` key beyond Appendix B's (M-Q3) | Appendix C §6 | Low |
| 14 | **Out-of-scope rejection generalises by topic, not wording.** A complaint on a topic outside `build/out_of_scope.json` relies on the thresholds alone: 1 of 38 test complaints ("Always On Display doesn't show the clock") still hits a plan (ADR-020) | §6.2 | Low |

**Closed since the first audit:** the ablation Baseline is measured (re-measured
with `qwen2.5:1.5b`: 2.3% catalog integrity when the model writes URIs, 60.5%
precision@1 when it selects among candidates, against 95.3% for the shipped
resolver); the container builds, runs offline and its cold start is measured at
17.3 s; both Appendix C §2 judged scores are filled with an auditable rubric; the
cold-path latency is re-measured on current code and the HTTP stress test is in
place. **2026-09-30:** a repeated article gets the identical plan (ADR-019, was
gap 4); a complaint naming two known problems gets both plans (ADR-021); the PDF's
own "My phone got slow after the update" falls back instead of hitting the
touchscreen plan (ADR-020); startup no longer embeds the catalog (ADR-023).

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
| 11 | A new HTTP client per Ollama call, plus `localhost` resolving to IPv6 first: ~3 s of every cold request on Windows | profiling one cold call against Ollama's own timings |
| 12 | One encoder call per deeplink probe: ~1 s per cold plan | the same profile |
| 13 | Extractor output cut off at `num_predict` was discarded as "no plan" | surfacing the exceptions `_extract` swallowed |
| 14 | The probe-corpus test split paths on `/`, failing on Windows | running the suite on Windows |
| 15 | The 1.5B extractor copied the prompt's example screen names into unrelated plans (13 of 57 actions; "Enable Touch Sensitivity" on a battery article, deeplinked there). Fixed by a grounding check in code plus schema length bounds (ADR-018); removing the examples instead was tried and made things worse | rehearsing the demo |
| 16 | Trimming left titles and descriptions ending mid-phrase ("Quick troubleshooting for", "…by lowering") | rehearsing the demo |
| 17 | A URL typed into the complaint came back out: echoed in `query` on every path, and on the cold path copied into the generated `query_variations`, where the URL gate then discarded a valid plan while the `no_match` fallback still returned the link. FastAPI's default 422 body also echoed rejected input, links and all. Fixed: the complaint is stripped of links on input (only the link, not the sentence around it), and 422s no longer echo the payload | writing the Appendix C §6 edge cases |

| 18 | With `siis_response` supplied, the API still looked the query up in the semantic cache first, so a complaint resembling a cached Display plan got that plan and the caller's own article was ignored (against §4.2.3). Fixed: the article decides (ADR-019) | measuring multi-intent complaints: the PDF's own "My phone got slow after the update" hit the touchscreen plan |
| 19 | Our ground truth said the "Swipe for split screen" switch was not in the catalog and accepted a neighbouring entry at 1.0; DL-0270 is that exact switch. Descriptor and label corrected, deeplink relevance 1.67 → 1.78 | the step-target probe (ADR-022) returned DL-0270 at full confidence |
| 20 | `merge_duplicate_screens` merged two actions sharing a screen by appending a second step group with the same deeplink, which G12 rejects. Latent until the step-target probe linked more actions: 3 of 33 cold plans then failed their gates. Fixed: steps join the screen's one group, and the merged action keeps the more disruptive category | the extractor comparison, before shipping |

Most of these were found by *looking at output*, not by tests passing. The two
worst — 4 and 5 — were introduced by me and hidden by a test with a side effect on
the thing it verified.

Test count: 31 → 63 → 68 → 72 → **82** (81 pass; 1 skips without the vendored encoder).
