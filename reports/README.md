# Measurement reports

Raw output behind every figure in [`docs/metrics.md`](../docs/metrics.md) (PDF
Appendix C), plus the supporting measurements that do not fit that template's
tables. Measured 2026-09-30 on the machine described in `environment.txt`, all in
one sequential run (21:30–22:05) with nothing else running on the laptop; each
`.txt` file starts with the command that produced it. What was cleared on the
laptop for this run, and how much it changed the timings:
[`docs/BENCHMARK_ENVIRONMENT.md`](../docs/BENCHMARK_ENVIRONMENT.md).

## Reproduce

```bash
python scripts/run_reports.py fixed                        # no LLM: tests, scoring, deeplink, cache and multi-intent evals
python scripts/run_reports.py models --models qwen2.5:1.5b llama3.2:1b llama3.2:1b-instruct-q4_K_M llama3.2:3b qwen2.5:3b gemma3:1b gemma3:4b
python scripts/run_reports.py model --model qwen2.5:1.5b   # latency, results.jsonl, Baseline ablation, HTTP stress, determinism
python scripts/verify_cold_path.py breakdown               # startup and step-accuracy breakdown
```

`run_reports.py` forces UTF-8 I/O for every step. Run anything else with
`PYTHONUTF8=1` on Windows: the default code page corrupts the source text and
turns exact cache hits into misses. Benchmark on a quiet machine, because the hot
path's encoder runs on the CPU.

## Where each figure in `docs/metrics.md` comes from

| Section | Figure | File |
|---|---|---|
| Header | hardware, OS, model digests | `environment.txt` |
| §1 | schema-valid lines | `batch.txt` (`results.jsonl` itself is git-ignored as a run output; `python scripts/run_batch.py` regenerates it), `stress_api.txt`, `compare_models.json` |
| §1 | rule compliance, catalog validity | `compile_check.txt`, `compare_models.json` |
| §1 | auto actions with a deeplink | `score_plans.txt`, `bench.txt` and `compare_models.json` (both 19% for the cold path) |
| §2 | step accuracy, deeplink relevance | `score_plans.txt`, `compare_models.json` |
| §3 | latency | `bench.txt`, `stress_api.txt` |
| §4 | tokens, cache hit rate | `compare_models.json`, `cache_shipped.txt`, `cache_test.txt` |
| §5 | ablation | `ablation_generate.txt`, `ablation_select.txt`, `dl_*.txt` |
| §6 | edge cases | `multi_intent.txt`, `cache_*.txt`, `determinism.txt`, `verify_startup_and_steps.txt`, `stress_api.txt`, `dl_display_hybrid.txt` |
| — | regression suite: 81 passed, 1 skipped | `tests.txt` |

The skipped test needs the vendored encoder in `vendor/`, which only the container
build creates.

## A. Latency end to end over HTTP (`stress_api.txt`)

Client-side wall time against a separately launched server; every response
validated. The server runs with the article cache off (`PRISM_ARTICLE_CACHE=0`,
ADR-019), because the cold requests reuse the 11 library articles, which the
article cache would otherwise answer from their compiled plans in under a
millisecond.

| Path | n | P50 (ms) | P95 (ms) | Schema-valid |
|---|---|---|---|---|
| Cache hit - exact | 40 | 1.6 | 2.6 | 40/40 |
| Cache hit - unseen paraphrase | 40 | 20.0 | 24.3 | 40/40 |
| Miss without `siis_response` (fallback) | 12 | 19.7 | 21.5 | 12/12 |
| Cold query - full pipeline | 33 | 2717 | 4325 | 33/33 |
| Hot burst, 8 concurrent clients | 200 | 112.6 | 269.6 | 200/200 |

- **Cold start:** 13.6 s from process launch to `/health` = ok (29.2 s before
  ADR-023; 21.7 s in an earlier run the same day with other work on the laptop).
  In the in-process breakdown (`verify_startup_and_steps.txt`): 9.0 s loading the
  sentence encoder, 0.3 s loading the catalog vectors the build saved (ADR-023; it
  was 7.1 s to embed them at startup), 0.0 s loading the plan cache. The first
  cold request after startup took 3109 ms; the extractor stays resident
  (`keep_alive: -1`).
- **Out-of-scope complaints:** all 12 no-article misses got the `no_siis_context`
  fallback (11 of 12 before ADR-020).
- **Concurrency:** 70 requests/s with 8 clients. Earlier runs of the same test
  measured 217, 232 and 320 ms P95 with no hot-path change in between: the encoder
  runs on the CPU, so this figure moves with whatever else the laptop is running.
- **Malformed payloads:** 3/3 answered with a JSON 422, never a non-JSON body.

## B. Deeplink resolution (`dl_*.txt`, `ablation_*.txt`)

Hand-labelled sets. Stage 3 receives descriptors the extractor writes in catalog
register, so those sets are the representative workload; the free-form set is a
robustness check.

| Set | n | Config | precision@1 (catalog) | intended-dummy | Overall | P95 ms/descriptor |
|---|---|---|---|---|---|---|
| Catalog register, Display | 43 | hybrid, tau 0.52 (shipped) | 94.7% (36/38) | 100% (5/5) | **95.3%** | 16.9 |
| Catalog register, Display | 43 | BM25 only, tau 0.52 | 94.7% (36/38) | 60% (3/5) | 90.7% | 4.7 |
| Battery / Camera / Performance | 26 | hybrid, tau 0.52 | 100% (17/17) | 100% (9/9) | **100%** | 17.7 |
| Free-form paraphrases | 20 | hybrid, tau 0.52 | 6.7% (1/15) | 100% (5/5) | 30% | 17.1 |
| Free-form paraphrases | 20 | hybrid, tau 0.20 | 40% (6/15) | 100% (5/5) | 55% | 16.6 |
| Free-form paraphrases | 20 | BM25 only, tau 0.52 | 0% (0/15) | 100% (5/5) | 25% | 4.6 |

Per-descriptor timings move with the laptop's load: earlier the same day, with
other work running, the same code measured hybrid P95 24.5 ms and BM25 21.3 ms.
On the quiet machine BM25 takes under a third of the hybrid's time (means 4.4 ms
against 14.9 ms on the Display set).

Ranking quality on the free-form set, thresholding aside: BM25 alone reaches
recall@1 13.3% and recall@5 60.0%; BM25 + dense reaches **66.7%** and **93.3%**.

Both Display misses are real. "enable adaptive brightness" resolves to DL-0498, a
TV Settings entry, at confidence 0.93: the appliance filter excludes
refrigerators and washers but not the catalog's 18 TV entries, and even with those
excluded DL-0105 (*extra* brightness) outranks the correct DL-0021. "open reset
options" scores 0.47, under tau. The previous report's 97.4% on this set does not
reproduce on this machine, with the current code or with the code before
`c13e60e`.

The Baseline (full LLM mapping, `qwen2.5:1.5b`) on the same 43 Display labels:

| Mode | Catalog integrity | precision@1 | P50 / P95 ms per descriptor | Tokens per descriptor |
|---|---|---|---|---|
| generate: the model writes the URI | 2.3% (1/43) | 2.3% | 272 / 279 | 197 + 29 |
| select: the model picks among the retriever's top 8 | 100% | 60.5% | 213 / 233 | 222 + 15 |

## C. Semantic cache calibration (`cache_*.txt`)

**Calibration set** (`cache_shipped*.txt`, `cache_single_*.txt`): 26 held-out
paraphrases, verified disjoint from all 130 cache seeds, plus 12 out-of-domain
negatives that must not hit. The thresholds were chosen on this set.

| tau / high / margin | Out-of-scope anchors | Hit rate | Correct routing | False hits (OOD) |
|---|---|---|---|---|
| 0.70 / — / — (single threshold) | off | 88.5% | 84.6% | 16.7% (2/12) |
| 0.78 / — / — (single threshold) | off | 76.9% | 73.1% | 0% |
| 0.70 / 0.80 / 0.04 (two-factor) | off | 84.6% | 80.8% | 8.3% (1/12) |
| **0.70 / 0.80 / 0.04 (two-factor, shipped)** | **on** | **84.6%** | **80.8%** | **0%** |

A single similarity threshold cannot meet both targets: the curves cross.
Out-of-domain queries are weakly similar to *every* plan, so the margin over the
best other plan separates them where raw similarity cannot (ADR-013). The
out-of-scope anchors (ADR-020, `build/out_of_scope.json`) remove the last false
hit, "extremely slow and laggy when switching between apps" → *Touchscreen
issues*, without costing a single hit.

**Test set** (`cache_test*.txt`): written after the thresholds and anchors were
fixed and never used to tune anything — 27 new paraphrases and 38 out-of-scope
complaints across battery, performance, thermal, camera, connectivity, audio, apps
and hardware, including the PDF's own "My phone got slow after the update".

| Out-of-scope anchors | Hit rate | Correct routing | False hits (OOD) | Extra plans on single-problem queries |
|---|---|---|---|---|
| off | 100% (27/27) | 92.6% | 5.3% (2/38) | — |
| **on (shipped)** | **100% (27/27)** | **92.6%** | **2.6% (1/38)** | **0/27** |

The anchors fix the PDF's example. The remaining false hit, "Always On Display
doesn't show the clock anymore" → *Screen flickers when using the Camera*, is on a
topic deliberately left out of the anchor list, so the test shows what an uncovered
topic does. The two wrong-plan hits route Smart Switch complaints to the Smart
Switch QR-transfer plan instead of *Some things to check first*; both documents are
about a blank screen blocking a Smart Switch transfer.

## D. Multi-intent complaints (`multi_intent.txt`)

Looked up exactly as the API does (`PlanCache.lookup_all`, ADR-021). On their own,
all 20 supplied queries and 25 of the 27 test paraphrases reach exactly their own
plan.

| Complaint | n | What comes back | Before ADR-021 |
|---|---|---|---|
| Two supplied complaints about different documents, joined (ordered pairs) | 340 | **both plans 100%** | first-mentioned only 95.6%, second only 4.4%, both 0% |
| Two held-out test paraphrases about different documents, joined | 648 | **both plans 85.0%**; 549 of 552 (99.5%) where each half routes correctly alone | — |
| A supplied complaint plus a problem no document covers | 60 | the known plan only, 100%; the other problem is dropped without notice | the same |
| "Screen flickers and the battery dies fast" (PDF §1) | 1 | *Screen flickers when using the Camera*, 0.832; the battery half has no plan | the same |
| "My phone got slow after the update" (PDF §1) | 1 | **no plan → `no_siis_context`** (ADR-020) | *Touchscreen issues*, 0.725 |
| "Swipe gestures go the wrong way after installing an app" (PDF §1) | 1 | *Touchscreen issues*, 0.732, a plan that includes the navigation-bar action | the same |

Nearly every paraphrase-pair failure involves the two test paraphrases the cache
already misroutes on their own (§C), not the pairing.

## E. Runtime extractor comparison (`compare_models.json`, ADR-017)

Same inputs for every model: all 11 SIIS documents, three passes each on a fresh
model load (N = 33), every plan graded, all seven in one run on the final code
(grounding check, bounded schema and step-target probes included). The two
`llama3.2:1b` rows answer "is a 1B model enough?": Ollama's default tag is 8-bit,
so the 4-bit build was measured too, for a like-for-like comparison with the
4-bit `qwen2.5:1.5b`.

| Model | Params · quant | P50 (ms) | P95 (ms) | Plans passing all gates | Step accuracy | Auto actions · linked · agreeing with compiled plan | Names the guard replaced | Tokens (prompt / completion) |
|---|---|---|---|---|---|---|---|---|
| **qwen2.5:1.5b (shipped)** | 1.5B · Q4_K_M | **2516** | 4315 | **33/33** | **2.97** | 63 · 19% · 75% | 36 of 108 actions | 606 / 272 |
| llama3.2:1b | 1.2B · Q8_0 | 2783 | 4690 | 33/33 | 2.94 | 18 · 50% · 67% | 27 of 84 | 594 / 325 |
| llama3.2:1b (4-bit) | 1.2B · Q4_K_M | 2570 | **3386** | 33/33 | 2.87 | 15 · 40% · 50% | 63 of 72 | 594 / 373 |
| gemma3:1b | 1.0B · Q4_K_M | 3090 | 4188 | 24/33 (3 of 11 docs empty) | 2.96 | 27 · 22% · 100% | 51 of 69 | 483 / 254 |
| llama3.2:3b | 3.2B · Q4_K_M | 3848 | 6925 | 33/33 | 2.91 | 57 · 37% · 71% | 21 of 123 | 594 / 243 |
| qwen2.5:3b | 3.1B · Q4_K_M | 3797 | 5381 | 33/33 | 2.78 | 36 · 42% · 60% | 78 of 60 | 606 / 262 |
| gemma3:4b | 4.3B · Q4_K_M | 5624 | 8237 | 30/33 (3 fail G2) | 2.86 | 63 · 19% · 100% | 9 of 156 | 607 / 268 |

`qwen2.5:1.5b` stays: every plan valid, the best step accuracy, and the fastest
median, though only just. On this quiet-machine run the 4-bit `llama3.2:1b` is
54 ms (2%) slower at the median, inside run-to-run noise, and has the lowest P95
(3386 ms against 4315 ms). It loses on quality, not speed: it treats only 15 of 72
actions as settings changes, the guard had to replace 63 of its 72 action names,
its step accuracy is 2.87, and it writes 37% more tokens. The 3B models link more
actions (about 21 for `llama3.2:3b` and 15 for `qwen2.5:3b`, against 12) but cost
about 1.3 s more at the median. `gemma3:1b` still returns no plan for 3 of 11
documents. On the quiet machine `llama3.2:3b` meets the 8 s budget (P95 6.9 s);
`gemma3:4b` still misses it (8.2 s). It names its actions best (9 replacements)
and is the organizer-encouraged family: the natural upgrade on a faster GPU, via
`PRISM_EXTRACT_MODEL` with no code change.

"Names the guard replaced" can exceed the actions kept (`qwen2.5:3b`) because
replaced names that coincide are merged into one action.

The `qwen2.5:1.5b` row here and `docs/metrics.md` §3 are separate runs of the same
code; §3 is the official figure. In this run they agree (P95 4315 ms here, 4389 ms
in `bench.txt`). Earlier the same day, with other work running on the laptop, its
P95 ranged 5215–5475 ms.

An earlier `llama3.2:3b` run measured P95 30 s with two timeouts. The A/B in
`verify_schema_bounds_ab.txt` shows decode speed is the same with and without the
schema bounds (72.9 vs 73.4 tok/s for `llama3.2:3b`, 123.9 vs 123.7 for
`qwen2.5:1.5b`), and clean re-runs gave figures like the ones above: the slowdown
was the machine, not the model. The same A/B earlier in the day, with other work
running, decoded at about half these speeds (41.7 and 62.0 tok/s).

## F. Step accuracy by component (`verify_startup_and_steps.txt`)

| | Completeness | Correctness | Ordering | Step accuracy |
|---|---|---|---|---|
| Compiled, 11 plans | 0.93 | 1.00 | 0.99 | 2.92 |
| Cold path, 11 plans on a fresh load | 0.99 | 1.00 | 0.97 | 2.97 |

Steps are indices into the source document (ADR-001), so traceability is 1.00 on
every plan. The cold path scores higher only because the rubric's completeness
term rewards using more source steps and the local model selects more of them; it
orders slightly worse, and it is not the better plan.

## G. Cold-path determinism (`determinism.txt`)

Each document was extracted on a freshly loaded model, then again in the same
process once the llama.cpp server had cached that exact prompt: 4 of 11 plans
differ (the four longest documents). Repeating one document back to back, or after
five other documents, gives identical output. Restoring a cached prompt
re-evaluates it in a different batch shape, which changes GPU floating-point
results enough to flip near-tied tokens. It shows in quality too: before the
step-target probe, one long-running process linked 2 of 66 auto actions where
three fresh loads linked 6.

This measures the model, below the API. The API now keeps the first validated plan
for each article and returns it for every repeat (ADR-019,
`test_repeated_article_gets_the_same_plan_without_the_model`), so a repeat request
never reaches the model and cannot differ.

## H. Changes since the previous report

- **Cold P95 7333 → 5215 ms.** The earlier figure was `qwen2.5vl:7b` on an RTX
  4060 at n = 11, where the script's P95 is simply the slowest run. The new one is
  N = 33 with a model about 5x smaller, on a weaker GPU. That took the smaller
  extractor plus fixes for two cold-path defects found while benchmarking: a new
  HTTP client per Ollama call (~3 s per request on this machine) and one encoder
  call per deeplink probe (~1 s per plan).
- **Grounding check (ADR-018).** Rehearsing the demo showed the 1.5B model
  copying the prompt's example screen names into unrelated plans, with deeplinks to
  match. Every model-written phrase is now checked against its own steps. Before
  the check, 23% of cold-path auto actions linked a specific screen but only 20% of
  those agreed with the compiled plan; after it, 9% link and half agree.
- **Compiled deeplink coverage 77% → 69%.** The previous 77% (10 of 13) was not
  updated after `c13e60e` turned the aspect-ratio action's wrong screen into a
  placeholder; that report's own deeplink section already counted 4 placeholders.
- **Later the same day (ADR-019 to ADR-023):**
  - with reference text supplied, the article decides: a library article gets its
    compiled plan, a repeated article the plan it got before, and a Display plan
    the complaint merely resembles can no longer override the caller's article;
  - out-of-scope anchors: the PDF's "My phone got slow after the update" falls
    back instead of hitting the touchscreen plan (§C);
  - one plan per problem named: two known problems joined get both plans (§D);
  - the step-target probe: cold-path auto actions with a specific screen 6% →
    19%, agreeing with the compiled plan 50% → 75%; it also exposed a labelling
    error of ours (DL-0270, deeplink relevance 1.67 → 1.78) and a latent merge
    bug that failed 3 of 33 plans until fixed;
  - the build embeds the catalog: cold start 29.2 s → 21.7 s (§A);
  - a 1B extractor was tried at 8-bit and 4-bit and rejected (§E).
- **Full re-run on a quiet machine (evening of 2026-09-30).** Every report was
  regenerated in one sequential run with no other work on the laptop and no code
  change. Every accuracy figure reproduced exactly: tests, step accuracy, deeplink
  relevance and precision, cache hit rates, out-of-scope false hits, multi-intent
  and determinism. Only timings moved: cold P95 5215 → 4389 ms, paraphrase-hit P95
  28.6 → 13.7 ms, cold start 21.7 → 13.6 s, decode speed about 2x. In the model
  comparison the 4-bit `llama3.2:1b` now ties `qwen2.5:1.5b` at the median and
  beats it at P95, and `llama3.2:3b` now meets the 8 s budget; the choice of
  `qwen2.5:1.5b` stands on quality (§E).
