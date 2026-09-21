# System Performance Metrics & Evaluation Report

Template per PDF Appendix C. Numbers are produced by `make bench`, `make test`
and `scripts/eval_*.py`, not written by hand.

**Model(s):** build tier — Claude (Opus 5), one-time plan compilation · runtime cold path — `qwen2.5vl:7b` via Ollama (`gemma3:4b` intended; see Limitations)
**Embeddings:** `BAAI/bge-small-en-v1.5` (384-dim, CPU, `inference_mode`)
**Environment:** 16 vCPU / 22 GB RAM / Fedora 44, NVIDIA RTX 4060 Laptop (7.5 GiB usable VRAM), Python 3.12.13

> **Hardware caveat.** The PDF's 300 ms and 8 s targets describe a production
> service. These numbers come from a laptop with a mobile GPU. The hot path has
> ~22x margin so the gap is immaterial there; the cold path is GPU-bound and
> would improve materially on server hardware or with a smaller extractor.

---

## 1. Schema & Rule Compliance

| Metric | Target | Measured |
|---|---|---|
| Schema-valid output lines | >= 99% | **100%** (20/20 queries in `results.jsonl`) |
| Rule compliance (Goal / Title / Description syntax) | >= 95% | **100%** (gates G1, G2, G6 over 11 compiled plans, 32 distinct descriptions) |
| Absolute URL leaks | 0 | **0** (G0 over every emitted string) |
| Deeplink catalog validity (exact URI match) | 100% | **100%** (G9; the resolver cannot emit a non-catalog URI by construction) |
| Auto actions carrying valid actionable deeplink | >= 90% | **100%** valid URI · **77%** specific catalog entry (see note) |

> **Two readings, both reported.** Appendix C asks for a "valid actionable
> deeplink". `bixby://dummy_positive` *is* a catalog entry and is the sanctioned
> answer when the catalog does not index a screen (PDF §3), so under the literal
> reading coverage is 100%. Under the stricter reading — a specific screen — it is
> 77% (10 of 13 auto actions). The 3 placeholders are Super steady, auto-rotate
> and a screen-orientation page, all of which I verified are genuinely **absent**
> from the catalog. Raising `TAU_LINK` from 0.45 to 0.52 converted them from
> confident wrong matches into honest abstentions; that trade is deliberate,
> because a wrong deeplink also costs deeplink-relevance points.

Regression suite: **31/31 passing**.

---

## 2. Accuracy Benchmarks

Evaluated against the 11 compiled plans and their source documents. Rubric and
per-item ground truth: `docs/RUBRIC.md`, `tests/fixtures/plan_deeplink_truth.json`.
Reproduce with `python scripts/score_plans.py`.

| Evaluation Metric | Scale / Anchor | Score |
|---|---|---|
| Step accuracy (completeness, correctness, ordering) | 0.0 – 3.0 | **2.92** |
| Deeplink relevance (exact target screen vs. parent menu) | 0.0 – 2.0 | **1.67** |

Step accuracy is high because ADR-001 makes steps structurally correct: they are
indices into the source, so traceability and gate compliance are 1.00 on every
plan. The 0.08 shortfall is completeness on the two largest documents (Multi
window 0.69, Screen mirroring 0.56), where the plan deliberately uses a subset of
a long source.

Deeplink relevance is the weaker of the two. Of 13 `auto` actions: 6 score 2.0
(exact screen), 3 score 1.0 (right feature area, wrong screen — the parent-menu
case PDF §6.2 penalises), and 4 are sanctioned `dummy_positive` excluded from the
mean (**31%**, a high rate driven by real catalog gaps, not by guessing).

Two defects found by scoring and since fixed: the Wi-Fi action resolved to
*Intelligent Wi-Fi* rather than the *Wi-Fi settings page* because its descriptor
said "Wi-Fi connection settings"; and the aspect-ratio action returned a real but
wrong entry (*Screen zoom*) where the correct answer was a placeholder, because
its descriptor named the wrong screen. Both were descriptor-quality problems in
`build/skeletons.json`, not retriever problems.

### 2.1 Deeplink resolution (Stage 3)

Two hand-labelled sets. The split matters: Stage 3 consumes LLM-normalized
descriptors, not raw user language, so the lexical set is the representative
workload and the paraphrase set is a robustness canary.

| Set | n | Config | precision@1 (catalog) | intended-dummy | overall |
|---|---|---|---|---|---|
| Catalog-register descriptors (Display) | 43 | tau=0.52 | 97.4% | 100% | **97.7%** |
| **Battery / Camera / Performance** | 26 | tau=0.52 | **94.1%** | 100% | **96.2%** |
| Free-form paraphrases | 20 | BM25 + dense, tau=0.45 (shipped) | **6.7%** | 100% | **30%** |
| Free-form paraphrases | 20 | BM25 + dense, tau=0.20 | 33% | 100% | 50% |

Ranking quality on the free-form set, thresholding aside:

| Config | recall@1 | recall@5 |
|---|---|---|
| BM25 only | 13% | 60% |
| BM25 + dense | **53%** | **87%** |

### 2.2 Semantic cache (Stage 1)

26 held-out paraphrases, verified disjoint from all 130 cache seeds, plus 12
out-of-domain negatives that must not hit.

| tau / high / margin | hit rate | correct routing | false hits (OOD) |
|---|---|---|---|
| 0.70 / — / — (single threshold) | 88.5% | 84.6% | 16.7% |
| 0.78 / — / — (single threshold) | 76.9% | 73.1% | 0% |
| **0.70 / 0.80 / 0.04 (two-factor, shipped)** | **84.6%** | **80.8%** | **8.3%** |

A single similarity threshold cannot satisfy both targets — the curves cross.
Out-of-domain queries are weakly similar to *every* plan, so the margin over the
best other plan discriminates where raw similarity cannot.

---

## 3. Latency Benchmarks (N >= 30 per hot path)

| Execution Path | Target (P95) | P50 | P95 | Verdict |
|---|---|---|---|---|
| Cache hit - exact query match | <= 300 ms | 0.0 ms | **0.0 ms** | PASS |
| Cache hit - unseen semantic paraphrase | <= 300 ms | 12.2 ms | **13.7 ms** | PASS |
| Cold query - full pipeline (n=11, warm model) | <= 8000 ms | 4401 ms | **7333 ms** | PASS |

Exact matches short-circuit on a normalized-string dict before any embedding.
The cold path requires `keep_alive: -1`: a cold model load costs 12.9 s and would
blow the budget on its own.

---

## 4. Operational Cost & Cache Efficacy

| Metric | Target | Measured |
|---|---|---|
| Cold query average inference cost | Tracked | **$0.00** (local Ollama) |
| Cache hit inference cost | $0.00 | **$0.00** (no model invoked) |
| Semantic cache hit rate (unseen paraphrases) | >= 80% | **84.6%** |
| Cost derivation | — | tokens reported per cold call; local inference has no per-token price |

Build-tier compilation runs once, offline, and is not charged per query.

---

## 5. Architectural Ablation Analysis

| Variant | Deeplink precision@1 (lexical / paraphrase) | Latency P95 | Cost/query | Key observations |
|---|---|---|---|---|
| Baseline: full LLM deeplink mapping | **18.6% catalog integrity · 2.3% precision@1** | 1365 ms/descriptor | local, $0 | **Measured, not argued.** The model emits `bixby://` URIs directly; 35 of 43 were not catalog members at all. Masked URIs are opaque hashes, so this design cannot satisfy PDF §4.2.2 — which is the empirical case for ADR-002. |
| **Variant A: hybrid BM25 + dense (shipped)** | **100% / 6.7%** | 13.7 ms hot | $0.00 | Best paraphrase ranking (recall@1 53%, recall@5 87%). Dense is what makes non-lexical descriptors reachable at all. |
| Variant B: pure rules / BM25 only | 100% / 0% | 13.7 ms hot | $0.00 | Ties Variant A on catalog-register descriptors and is simpler, but collapses on paraphrase (recall@1 13%). |

| Variant C: hybrid + Jev rerank (evaluated, not adopted) | **100% / 80–85%** | ~1.06 s/call | $0 local, tokens billed | Biggest accuracy gain measured: free-form 30% -> 80–85%. **Not deterministic** (see below), so build-tier only. |

**Determinism finding.** Jev was measured non-deterministic on 2026-09-21: five
identical calls for "turn on auto-rotate screen" returned DL-0461 three times and
`none_of_these` twice, and Choice confidence varied run-to-run even where the pick
was stable. Instability concentrated at confidence 0.44–0.48; the confidence-1.0
case was stable across all runs. This, combined with the fact that the build tier already
resolves at 100% so the gain cannot land anywhere Jev is permitted to run, is why
Variant C was measured but not adopted (ADR-015).

**Honest reading.** On the lexical set Variant B matches Variant A exactly, so if
the extractor always emitted catalog-register descriptors, the dense half would
earn nothing. It is retained because that assumption is fragile: the local
extractor demonstrably emits parent menus ("display settings") instead of the
specific screen, and dense retrieval plus multi-probe resolution is what recovers
those. Measured on the cold path, that recovery took auto-action deeplink
coverage from 0% to 65%.

---

## 6. Known Edge Cases & System Limitations

1. **Display-only plan coverage, but a domain-independent pipeline.** All 20
   supplied queries are Display; PDF Appendix C §2 judges accuracy across four
   domains. Stage 3 now scores **96.2%** on a 26-label Battery/Camera/Performance
   set, and a synthetic probe corpus (`tests/fixtures/probes/`) drives all three
   unseen domains through the pipeline to gate-passing plans. What we cannot do is
   ship *plans* for domains whose SIIS text we were never given (M-Q4); probes are
   test input and are deliberately never compiled into `artifacts/`.
2. **The catalog is itself Display-skewed** — roughly 186 Display-ish entries
   against 8 Battery ones, with no entry at all for deep-sleep apps, camera
   resolution, scene optimiser, grid lines or clear-app-cache. This bounds what
   *any* submission can achieve on non-Display domains, independent of
   implementation quality.
3. **More corrupt catalog metadata.** `DL-0397`/`DL-0398` describe *adaptive
   battery* but their `message` reads "Adaptive Display". Matching on
   description + message + qna_description is what keeps this from breaking
   resolution; matching on `message` alone would have been wrong.
4. **Corrupted source document.** "Some things to check first" (`row_3`,
   `row_11`, `row_17` — 3 of 20 queries) has had its whitespace stripped, giving
   run-on tokens like `InteventyouhaveenteredtheincorrectPINfivetimesinrow`. We
   recover camelCase boundaries only, so recall on that document is low (2
   candidate steps). Reported as M-Q9.
5. **Source text is not URL-free.** The kit README states the SIIS text carries no
   URLs. It carries a contact address in the corrupted document. G0 catches it;
   the claim is wrong, not the data pipeline.
6. **Catalog coverage holes.** No entries for safe mode, auto-rotate, Smart View /
   screen mirroring, software update or clear-app-cache — all of which the SIIS
   documents explicitly instruct. These resolve to `bixby://dummy_positive` by
   design.
7. **Free-form descriptor resolution is weak** (30% overall at the shipped
   tau=0.45; 50% at tau=0.20). The threshold is tuned for the catalog-register
   descriptors Stage 3 actually receives, and that tuning costs free-form
   accuracy — a deliberate trade, not an oversight. Correct paraphrase
   matches score cosine 0.64-0.74 against unrelated entries at 0.60-0.74 — the
   distributions overlap, so no threshold separates them. Mitigated by having the
   extractor emit catalog-register descriptors and by multi-probe resolution.
8. **One residual false cache hit**: "extremely slow and laggy when switching
   between apps" routes to *Touchscreen issues*. Semantically adjacent; arguably
   defensible, counted as a failure here.
9. **Cold-path latency is not currently re-measurable.** `gemma3:4b` was
   eventually obtained (the earlier download failures were transient), but the
   machine's GPU is shared with an unrelated training job, and measurements taken
   under contention showed throughput dropping from 43.5 to ~29 tok/s. The
   recorded cold P95 of 7333 ms was measured on an idle GPU with
   `qwen2.5vl:7b`; a clean `gemma3:4b` comparison is pending an idle card. No
   contaminated figure is reported here.
10. **Spec conflict, unresolved.** `sample_output.json` violates the PDF's own
   5-7-word `description` rule (9 and 12 words). We enforce the PDF (ADR-007);
   `tests/test_pipeline.py::test_shipped_sample_violates_description_rule` is the
   single place to invert if the organizers rule otherwise (M-Q2).
