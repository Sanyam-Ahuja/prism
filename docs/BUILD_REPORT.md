# Build Report

What was built in this session, what it measures, and what is not done.
Written to match the code as it stands; every number here is reproduced by
`make build && make test && make bench && make eval`.

**Status:** pipeline complete and verified · **repository not submission-ready** (see §7)

---

## 1. Summary

| | |
|---|---|
| Python written | **2,177 lines** across 18 modules |
| Documentation written | **1,319 lines** across 6 files (excl. this report) |
| Test cases | **31** (25 functions, parametrised) — all passing |
| Validation gates | **16** (G0–G15) |
| Hand-labelled fixtures | **101 items** across 4 sets |
| Compiled plans | **11** (one per distinct SIIS document) |
| Environment | toolbox `prism-dev`, Python **3.12.13** (host is 3.14.7, out of submission range) |

---

## 2. What exists

### 2.1 Engine (`engine/`, 983 lines)

| module | lines | responsibility |
|---|---|---|
| `deeplink.py` | 254 | Stage 3. BM25 + dense retrieval, RRF ranking, separate confidence scoring, polarity prior, appliance denylist, verbatim field copying |
| `cold.py` | 185 | Cold path. Ollama constrained-JSON extraction, multi-probe screen resolution, runs the same deterministic stages as the compiler |
| `segment.py` | 177 | SIIS document → numbered candidate steps. The substrate for ADR-001 |
| `assemble.py` | 152 | Stage 4. Category override table, ordering, goal/title/description coercion |
| `variations.py` | 70 | Deterministic 8–10 paraphrase generation (cold path) |
| `cache.py` | 60 | Stage 1. Brute-force cosine, two-factor acceptance, exact-match short circuit |
| `normalize.py` | 46 | Stage 0. Device-model collapsing, list-artifact stripping, contraction expansion |
| `embed.py` | 39 | Shared bge-small encoder with the required query-side instruction prefix |

### 2.2 Validation (`validators/`, 403 lines)

| module | lines | responsibility |
|---|---|---|
| `gates.py` | 313 | G0–G15 as pure functions returning violations; never mutates |
| `scrub.py` | 90 | G0. Sentence-aware URL removal — a sentence that exists to point at a link is dropped whole |

### 2.3 API and scripts

| file | lines | purpose |
|---|---|---|
| `api/main.py` | 104 | `POST /v1/troubleshoot`, `GET /health` (gated on all four subsystems) |
| `scripts/compile_plans.py` | 169 | Build pipeline. Fails the build on any blocking gate |
| `scripts/bench.py` | 74 | Latency percentiles per execution path |
| `scripts/eval_cache.py` | 62 | Held-out hit rate + out-of-domain false-hit rate |
| `scripts/eval_deeplinks.py` | 55 | precision@1 split by catalog-match vs intended-dummy |
| `scripts/run_batch.py` | 51 | `data/input.txt` → `results.jsonl` |
| `scripts/calibrate.py` | 31 | Derives the cosine band from labelled data |
| `scripts/dump_segments.py` | 17 | Segmentation inspection |
| `tests/test_pipeline.py` | 228 | 31 test cases |

### 2.4 Documentation (1,319 lines)

`ARCHITECTURE.md` 385 · `TECH_PLAN.md` 284 · `DECISIONS.md` 237 (14 ADRs + 9 mentor questions) ·
`DATA_CONTRACT.md` 204 · `metrics.md` 149 · `README.md` 60

### 2.5 Authored content

| item | count |
|---|---|
| Stage-2 skeletons (build-tier LLM output, `build/skeletons.json`) | 11 plans |
| Actions authored | 34 |
| Source step indices selected | 93 |
| Query variations authored | 110 |
| Deeplink labels — catalog register | 43 |
| Deeplink labels — free-form paraphrase | 20 |
| Held-out cache paraphrases | 26 |
| Out-of-domain negatives | 12 |

### 2.6 Generated artifacts

`plan_library.json` (36K, 11 plans) · `cache_vectors.npy` (130 × 384 float32) ·
`cache_manifest.json` (16K). Regenerated only by `compile_plans.py`; never hand-edited.

---

## 3. What the compiled output contains

| | |
|---|---|
| Plans | 11 |
| Actions | 34 — `auto` 13, `manual` 13, `critical` 8 |
| Steps emitted | 93, every one traceable to its source document (test-enforced) |
| StepGroups with a catalog deeplink | 14 |
| StepGroups with `dummy_positive` | 3 |
| StepGroups with no deeplink (manual) | 18 |
| Distinct catalog URIs used | 13 |
| Validation blocks copied verbatim | 14 |

---

## 4. Measured results

### 4.1 Compliance

| Metric | Target | Measured |
|---|---|---|
| Schema-valid output | ≥99% | **100%** (20/20) |
| Rule compliance (goal/title/description) | ≥95% | **100%** |
| URL leaks | 0 | **0** |
| Catalog URI validity | 100% | **100%** |
| Auto actions with a real deeplink | ≥90% | **92%** compiled (12/13) · 65% cold |
| Regression suite | — | **31/31** |

### 4.2 Latency

| Path | Target P95 | P50 | P95 | Verdict |
|---|---|---|---|---|
| Cache hit — exact | ≤300 ms | 0.0 ms | **0.0 ms** | PASS |
| Cache hit — paraphrase | ≤300 ms | 12.2 ms | **13.7 ms** | PASS |
| Cold — full pipeline (n=11, warm) | ≤8000 ms | 4401 ms | **7333 ms** | PASS |

Measured on RTX 4060 Laptop (7.5 GiB usable VRAM), 16 vCPU, 22 GB RAM. The PDF's
targets describe a production service; the hot path has ~22× margin so the gap is
immaterial there, the cold path is GPU-bound and would improve on server hardware.

### 4.3 Retrieval

| Set | n | precision@1 | dummy correct | overall |
|---|---|---|---|---|
| Catalog-register descriptors | 43 | **100%** | 100% | **100%** |
| Free-form paraphrases (shipped τ=0.45) | 20 | 6.7% | 100% | 30% |

Ranking only, free-form set: BM25 recall@1 **13%** / recall@5 60% · BM25+dense
recall@1 **53%** / recall@5 **87%**.

### 4.4 Cache

26 held-out paraphrases (verified disjoint from all 130 seeds) + 12 out-of-domain negatives.

| config | hit rate | correct routing | false OOD hits |
|---|---|---|---|
| single threshold 0.70 | 88.5% | 84.6% | 16.7% |
| single threshold 0.78 | 76.9% | 73.1% | 0% |
| **two-factor 0.70/0.80/0.04 (shipped)** | **84.6%** | **80.8%** | **8.3%** |

---

## 5. Defects found and fixed during the build

Each of these invalidated a design assumption made before measuring.

| # | Defect | Effect | Fix |
|---|---|---|---|
| 1 | Thresholded on the RRF score, which is rank-derived — every query scored exactly `1/61` | Deeplink precision **11.6%**, everything fell to dummy | Separated ranking from confidence (ADR-011) → **100%** |
| 2 | Polarity used as a hard `originalType` filter | Excluded correct answers where the catalog offers a different entry type (DL-0228, DL-0083) | Demoted to a rank/confidence prior (ADR-012) |
| 3 | First deeplink label set authored in catalog vocabulary | Scored 100% with BM25 alone — measured lexical overlap, not retrieval | Added a 20-item free-form set; dense justified honestly |
| 4 | Cold extractor emits parent menus in `screen` ("display settings") | **0 of 8** cold auto actions resolved | Multi-probe over `name`/`screen`/both + required schema field (ADR-014) → **65%** |
| 5 | `_LEAD` stripped only one discourse marker | "Now, please connect…" rejected; whole SIIS steps lost | Repeated stripping + modal handling; Blank-display recall 8 → 11 candidates |
| 6 | URL scrub was inline, not sentence-aware | Left fragments like "You can find instructions at" | Drop the whole referral sentence |
| 7 | `normalize` left "ultra" after collapsing "S24 Ultra" | S22 and S24 Ultra keyed differently — cache fragmentation | Suffix absorbed into the model-number branch |
| 8 | Compiler seeded only the first query per document | 2 known queries missed the cache | Seed all 20 known queries → 130 vectors, 100% |
| 9 | Authored 6-word benefits against a 5-word body budget | 11 of 32 descriptions truncated mid-phrase | Rewrote benefits + dangling-function-word guard |

---

## 6. Findings about the supplied data

1. **One source document is corrupted.** "Some things to check first" has had its
   whitespace stripped (`InteventyouhaveenteredtheincorrectPINfivetimesinrow`).
   **3 of 20 queries** route to it; step recall there is 2 candidates. → M-Q9.
2. **The kit README's "no URLs" claim is false.** That same document embeds a
   contact address. G0 catches it.
3. **`sample_output.json` violates the PDF's own rule** — descriptions of 9 and 12
   words against a stated 5–7. We enforce the PDF (ADR-007); one test pins this
   and is the single place to invert if the organizers rule otherwise. → M-Q2.
4. **Catalog coverage holes**: no safe mode, auto-rotate, Smart View, software
   update or clear-app-cache — all instructed by SIIS documents. These are what
   `dummy_positive` exists for.
5. **Catalog noise**: SmartThings appliance entries (DL-0468/0469/0473) and
   corrupt metadata (DL-0294/0295, `message: "Offurl"`). Both filtered.
6. **20 queries collapse to 11 documents** — the structural fact the whole cache
   design rests on.
7. **All 20 supplied queries are Display.** The PDF names four domains.

---

## 7. Not done

### Blocking submission

| # | Item | Detail |
|---|---|---|
| 1 | **Not a git repository** | No `git init`, no commits, no `PRISM_GENAI_HACKATHON_Y2026` tag |
| 2 | **Containerfile will not build** | References `vendor/bge-small-en-v1.5`, which does not exist. Written but never built or run |
| 3 | **No dependency lock** | `TECH_PLAN.md` claims `requirements.lock` is committed; the Makefile installs from `requirements.txt`. Nothing is version-pinned |

### Planned, not started

| # | Item | Why it matters |
|---|---|---|
| 4 | **M6 — generalization** | All training data is Display; the hidden set spans Battery, Camera, Performance. Largest scoring risk |
| 5 | **M7 — ablation Baseline row** | Appendix C asks for three variants. A and B measured; Baseline (full-LLM URI mapping) argued-away rather than measured |
| 6 | **`gemma3:4b` not installed** | Three download attempts failed (`registry.ollama.ai` EOF, stalled at 65%). Cold numbers are attributed to `qwen2.5vl:7b`, which fits the same VRAM budget |

### Known-weak, documented

7. Free-form descriptor resolution is 30% at the shipped threshold — a deliberate
   trade for the catalog-register descriptors Stage 3 actually receives.
8. One residual false cache hit: "extremely slow and laggy" → *Touchscreen issues*.
9. Segmentation recall on the corrupted document is poor by necessity.

---

## 8. Open questions for the mentor

Nine are recorded in `docs/DECISIONS.md`. The three worth asking first:

- **M-Q1** — exact submission format for Theme 02 (repo + tag? container? `submission.yaml`?). Theme 02's kit ships no harness, unlike Theme 05's.
- **M-Q2** — is the PDF or `sample_output.json` authoritative on the 5–7-word rule?
- **M-Q3** — does the grader expect `query_variations` and `meta` (Appendix B) or just `{query, response}` (the sample)?
