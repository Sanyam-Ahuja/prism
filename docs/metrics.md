# System Performance Metrics & Evaluation Report
**Model(s):** Ollama `qwen2.5:1.5b` (Q4_K_M, 1.5B) — runtime cold-path extractor · Anthropic Claude Opus 5 — build tier, one-time offline plan compilation, never called per query  
**Embeddings:** `BAAI/bge-small-en-v1.5` (384-dim, CPU, revision `5c38ec7`)  
**Environment:** 12 vCPU (Intel Core i5-13420H) / 15.7 GiB RAM / Windows 11 Home (build 26200) · NVIDIA RTX 4050 Laptop GPU, 6 GiB · Python 3.13.9 · Ollama 0.34.4 · measured 2026-09-30

---

## 1. Schema & Rule Compliance
Evaluated on sample datasets and held-out validation scenarios.

| Metric | Target | Measured Value |
| :--- | :--- | :--- |
| Schema-valid output lines | >= 99% | 100% — 20/20 `results.jsonl` lines · 325/325 HTTP responses (exact, paraphrase, fallback, cold, 8-client burst) · 33/33 cold-path plans |
| Rule compliance (Goal / Title / Description syntax) | >= 95% | 100% — 11/11 compiled plans and 33/33 cold-path plans pass the goal (G1), title (G2) and description (G6) gates |
| Absolute URL leaks | 0 | 0 — in all of the above, and when a URL is planted in the article or typed into the complaint (regression-tested) |
| Deeplink catalog validity (exact URI match) | 100% | 100% — every URI is copied verbatim from `deeplinks.json` (G9); no model ever writes one |
| Auto actions carrying valid actionable deeplink | >= 90% | 100% on both paths, counting `bixby://dummy_positive` for screens the catalog does not index · a specific screen: 69% compiled (9/13), 3–9% cold path (§6) |

---

## 2. Accuracy Benchmarks
Evaluated against reference ground truth scenarios across Battery, Display, Camera, and Performance. The supplied scenarios are all Display, so both scores are Display; Battery, Camera and Performance are scored at the deeplink level only (§5, Variant A) and listed as a gap in §6. Rubric: `docs/RUBRIC.md`.

| Evaluation Metric | Scale / Anchor | Score |
| :--- | :--- | :--- |
| Step accuracy (completeness, correctness, ordering) | 0.0 - 3.0 | 2.92 — 11 compiled plans · 2.97 — 33 cold-path plans (the model selects more source steps but orders them slightly worse) |
| Deeplink relevance (exact target screen vs. parent menu) | 0.0 - 2.0 | 1.67 — 9 scored auto actions: 6 exact screen (2.0), 3 adjacent screen in the right area (1.0); 4 `dummy_positive` placeholders excluded |

---

## 3. Latency Benchmarks (N >= 30 requests per path)
Measured in-process, N = 40 / 40 / 33 (`reports/bench.txt`). End to end over HTTP the P95s are 17.9 / 29.6 / 5263 ms (`reports/stress_api.txt`).

| Execution Path | Target (P95) | P50 (ms) | P95 (ms) |
| :--- | :--- | :--- | :--- |
| Cache hit - exact query match | <= 300 ms | 0.4 | 0.6 |
| Cache hit - unseen semantic paraphrase | <= 300 ms | 22.0 | 28.6 |
| Cold query - full pipeline extraction & mapping | <= 8000 ms | 3343 | 5345 |

---

## 4. Operational Cost & Cache Efficacy

| Metric Item | Target | Measured Value |
| :--- | :--- | :--- |
| Cold query average inference cost | Tracked | $0.00 — local model; 878 tokens per cold query on average (606 prompt + 272 completion), returned in each response's `meta.tokens` |
| Cache hit inference cost | $0.00 | $0.00 — no model call |
| Semantic cache hit rate (on unseen paraphrases) | >= 80% | 84.6% — 22/26 held-out paraphrases hit; 21 of the 22 reach the correct plan (80.8%) |
| Cost derivation method | - | (prompt tokens + completion tokens) x rate; rate = $0 for local inference through Ollama |

---

## 5. Architectural Ablation Analysis

| Architecture Variant | Step Accuracy | Latency (P95) | Cost / Query | Key Observations |
| :--- | :--- | :--- | :--- | :--- |
| Baseline: Full LLM Deeplink Mapping | 2.92 — the same for all three: steps are chosen before mapping | 427 ms per descriptor (`qwen2.5:1.5b`) | $0.00 locally; +1 model call (226 tokens) per auto action | The model writes the URI: 1 of 43 is even in the catalog (precision@1 2.3%). Masked URIs are opaque, so this cannot meet PDF §4.2.2. Letting the model pick among the retriever's top 8 instead keeps every URI valid but reaches only 60.5% (241 ms P95, 237 tokens). |
| Variant A: Hybrid BM25 + Dense Embedding Retrieval | 2.92 | 37.8 ms per descriptor | $0.00 | Shipped. Precision@1 95.3% on 43 labelled Display screens and 100% on 26 Battery/Camera/Performance screens. Best on free-form wording: recall@1 66.7%, recall@5 93.3%. |
| Variant B: Pure Rules-Based Deeplink Mapping | 2.92 | 10.7 ms per descriptor | $0.00 | BM25 keywords with the same polarity, appliance and threshold rules, no embeddings. Equal on catalog-worded screens (94.7%), but links 2 of 5 unindexed screens to wrong entries (90.7% overall) and collapses on free-form wording (recall@1 13.3%). |

---

## 6. Known Edge Cases & System Limitations
* **Multi-intent — one plan per complaint.** A response carries one Goal, so a complaint naming two problems is answered for one at most. Joining two supplied complaints about different documents (340 ordered pairs), the plan for the first-mentioned problem came back 95.6% of the time and the second's 4.4%; never both (`reports/multi_intent.txt`). `contexts` is a list, so one Goal per intent would fit the schema; it is not built.
* **Multi-intent — an uncovered second problem is dropped silently.** Each supplied complaint plus a problem no document covers ("…the battery drains really fast"): 60/60 returned the known plan, with nothing saying the other problem went unanswered. The PDF's own "Screen flickers and the battery dies fast" returns the camera-flicker plan (similarity 0.83) and drops the battery half.
* **Multi-intent on the cold path follows the article, not the complaint.** The extractor's prompt holds only the SIIS text, never the query, so a cold plan covers whatever the supplied article covers.
* **Domain gap — plans exist for Display only.** All 20 supplied queries and 11 SIIS documents are Display. Battery, Camera and Performance are served only when a `siis_response` is supplied (team-written probe articles in `tests/fixtures/probes/` give gate-passing plans for all three); none of their plans is cached.
* **Domain gap — an out-of-domain complaint can hit a Display plan.** "My phone got slow after the update" (PDF §1) returns *Touchscreen issues* at similarity 0.725 instead of the `no_siis_context` fallback, and so does 1 of the 12 out-of-domain negatives in the cache evaluation ("extremely slow and laggy when switching between apps"). Both pass the two-factor rule on margin: "slow" complaints sit close to that plan's touch-lag phrasings. "Swipe gestures go the wrong way after installing an app" lands on the same plan (0.732), which does include a navigation-bar action, the fix Appendix B's worked example gives.
* **Domain gap — the catalog is Display-skewed:** about 186 Display entries against 8 Battery, and none for deep-sleeping apps, camera resolution, scene optimiser, grid lines or clear-app-cache.
* **Settings hierarchy — adjacent screen instead of the exact one.** 3 of 9 scored compiled auto actions link the nearest indexed entry because the exact screen is not in the catalog: *Enable Multi Window Gestures* → the multi-window toggle (DL-0168) where the steps target *Swipe for split screen*; *Customize Edge Panel* → enable Edge panels (DL-0096); *Remove Apps Edge Shortcuts* → *Show as edge icon* (DL-0326). These are the 1.0 scores in §2.
* **Settings hierarchy — screens the catalog does not index** get `bixby://dummy_positive` by design: Super steady, screen orientation, aspect ratio and Smart Switch in compiled plans (4 of 13 auto actions). Safe mode, Smart View, software update and clear-app-cache are also absent, though SIIS documents instruct them.
* **Settings hierarchy — TV entries sit in the phone catalog.** The appliance filter drops refrigerators, washers and air conditioners but not the catalog's 18 TV entries ("via TV Settings"), so "enable adaptive brightness" resolves to DL-0498, a TV entry: one of Variant A's two misses on the 43 Display labels.
* **Settings hierarchy — mislabelled entries are copied verbatim.** DL-0397/DL-0398 describe adaptive battery, but their `message` reads "Adaptive Display"; a battery plan shows that label.
* **Cold-path deeplink coverage is low.** 3–9% of cold-path auto actions link a specific screen, against 69% for compiled plans with the same resolver. The 1.5B extractor rarely names a catalog-worded screen, and the grounding check (ADR-018) rejects the screens it copies from its prompt; the resolver is not the bottleneck.
* **Cold-path output depends on the model server's prompt cache.** 4 of 11 documents give a different plan once Ollama has cached their prompt (`reports/determinism.txt`); repeats after that are identical, and the hot path is exactly deterministic. Fix identified, not applied: memoize cold plans by a hash of the article text.
* **Cold plans are not written back to the cache** (PDF §2, step [3]). They are built from caller-supplied text, and a shared cache would serve one caller's plan to other users' similar queries — a poisoning risk that needs a decision first.
* **Long articles hit the output cap.** 2 of 11 documents reach the 500-token limit on the cold path; the completed actions are kept and the unfinished last one, often the most disruptive, is dropped.
* **The small extractor names actions poorly.** Across 33 cold plans the grounding check replaced 36 model-written action names with the article's own section headings (ADR-018): grounded, but some read as symptoms ("Nothing Is Visible on the Screen").
* **Free-form screen descriptions resolve poorly:** 30% on 20 free-form paraphrases at the shipped threshold, against 95.3% for the catalog-worded descriptors the extractor is prompted to write.
* **Source data defects.** "Some things to check first" (3 of 20 queries) has its whitespace stripped, so few of its steps are recovered, and it embeds a contact URL despite the kit README; G0 removes it. `sample_output.json` has 9- and 12-word descriptions against the PDF's 5–7; we enforce the PDF (ADR-007).
* **Cold start is 29.2 s** to `/health` = ok: 23.2 s loading the encoder, 7.1 s embedding the catalog.
* **Measured on a shared Windows laptop.** With no code change the 8-client hot-path P95 was 217 ms in one run and 320 ms in another; runs that overlapped an unrelated heavy job were discarded and repeated. The host runs Python 3.13; the container pins 3.12.

---

Raw output behind every figure, and the supporting tables (HTTP latency, deeplink and cache calibration, extractor comparison): [`reports/README.md`](../reports/README.md).
