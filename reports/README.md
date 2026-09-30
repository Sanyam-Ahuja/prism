# Measurement reports

Raw output behind every figure in [`docs/metrics.md`](../docs/metrics.md) (PDF
Appendix C), plus the supporting measurements that do not fit that template's
tables. Measured 2026-09-30 on the machine described in `environment.txt`; each
`.txt` file starts with the command that produced it.

## Reproduce

```bash
python scripts/run_reports.py fixed                        # no LLM: tests, scoring, deeplink, cache and multi-intent evals
python scripts/run_reports.py models --models qwen2.5:1.5b llama3.2:3b qwen2.5:3b gemma3:1b gemma3:4b
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
| §1 | auto actions with a deeplink | `score_plans.txt`, `compare_models.json` (9%), `bench.txt` (3%) |
| §2 | step accuracy, deeplink relevance | `score_plans.txt`, `compare_models.json` |
| §3 | latency | `bench.txt`, `stress_api.txt` |
| §4 | tokens, cache hit rate | `compare_models.json`, `cache_shipped.txt` |
| §5 | ablation | `ablation_generate.txt`, `ablation_select.txt`, `dl_*.txt` |
| §6 | edge cases | `multi_intent.txt`, `determinism.txt`, `verify_startup_and_steps.txt`, `dl_display_hybrid.txt`, `cache_shipped.txt` |
| — | regression suite: 71 passed, 1 skipped | `tests.txt` |

The skipped test needs the vendored encoder in `vendor/`, which only the container
build creates.

## A. Latency end to end over HTTP (`stress_api.txt`)

Client-side wall time against a separately launched server; every response
validated.

| Path | n | P50 (ms) | P95 (ms) | Schema-valid |
|---|---|---|---|---|
| Cache hit - exact | 40 | 10.5 | 17.9 | 40/40 |
| Cache hit - unseen paraphrase | 40 | 24.0 | 29.6 | 40/40 |
| Miss without `siis_response` (fallback) | 12 | 24.2 | 28.7 | 12/12 |
| Cold query - full pipeline | 33 | 3351 | 5263 | 33/33 |
| Hot burst, 8 concurrent clients | 200 | 130.1 | 217.3 | 200/200 |

- **Cold start:** 29.2 s from process launch to `/health` = ok: 23.2 s loading
  the sentence encoder, 7.1 s embedding the 578-entry catalog, 0.1 s loading the
  plan cache (`verify_startup_and_steps.txt`). The first cold request after
  startup took 4247 ms; the extractor stays resident (`keep_alive: -1`).
- **Concurrency:** 77 requests/s with 8 clients. An earlier run of the same test
  measured 320 ms P95 with no hot-path change in between: the encoder runs on the
  CPU, so this figure moves with whatever else the laptop is running.
- **Malformed payloads:** 3/3 answered with a JSON 422, never a non-JSON body.

## B. Deeplink resolution (`dl_*.txt`, `ablation_*.txt`)

Hand-labelled sets. Stage 3 receives descriptors the extractor writes in catalog
register, so those sets are the representative workload; the free-form set is a
robustness check.

| Set | n | Config | precision@1 (catalog) | intended-dummy | Overall | P95 ms/descriptor |
|---|---|---|---|---|---|---|
| Catalog register, Display | 43 | hybrid, tau 0.52 (shipped) | 94.7% (36/38) | 100% (5/5) | **95.3%** | 37.8 |
| Catalog register, Display | 43 | BM25 only, tau 0.52 | 94.7% (36/38) | 60% (3/5) | 90.7% | 10.7 |
| Battery / Camera / Performance | 26 | hybrid, tau 0.52 | 100% (17/17) | 100% (9/9) | **100%** | 38.0 |
| Free-form paraphrases | 20 | hybrid, tau 0.52 | 6.7% (1/15) | 100% (5/5) | 30% | 35.0 |
| Free-form paraphrases | 20 | hybrid, tau 0.20 | 40% (6/15) | 100% (5/5) | 55% | 41.1 |
| Free-form paraphrases | 20 | BM25 only, tau 0.52 | 0% (0/15) | 100% (5/5) | 25% | 11.3 |

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
| generate: the model writes the URI | 2.3% (1/43) | 2.3% | 342 / 427 | 197 + 29 |
| select: the model picks among the retriever's top 8 | 100% | 60.5% | 217 / 241 | 222 + 15 |

## C. Semantic cache calibration (`cache_*.txt`)

26 held-out paraphrases, verified disjoint from all 130 cache seeds, plus 12
out-of-domain negatives that must not hit.

| tau / high / margin | Hit rate | Correct routing | False hits (OOD) |
|---|---|---|---|
| 0.70 / — / — (single threshold) | 88.5% | 84.6% | 16.7% |
| 0.78 / — / — (single threshold) | 76.9% | 73.1% | 0% |
| **0.70 / 0.80 / 0.04 (two-factor, shipped)** | **84.6%** | **80.8%** | **8.3%** |

A single similarity threshold cannot meet both targets: the curves cross.
Out-of-domain queries are weakly similar to *every* plan, so the margin over the
best other plan separates them where raw similarity cannot (ADR-013). The one
remaining false hit is "extremely slow and laggy when switching between apps" →
*Touchscreen issues*.

## D. Multi-intent complaints (`multi_intent.txt`)

Looked up exactly as the API does. Each of the 20 supplied queries alone reaches
its own plan.

| Complaint | n | What comes back |
|---|---|---|
| Two supplied complaints about different documents, joined (ordered pairs) | 340 | plan for the first-mentioned problem 95.6% · the second's 4.4% · a third plan 0% · miss 0% · both 0% |
| A supplied complaint plus a problem no document covers | 60 | the known plan 100%; the other problem is dropped without notice |
| "Screen flickers and the battery dies fast" (PDF §1) | 1 | *Screen flickers when using the Camera*, similarity 0.832 |
| "My phone got slow after the update" (PDF §1) | 1 | *Touchscreen issues*, 0.725: a false hit, as no Performance plan exists |
| "Swipe gestures go the wrong way after installing an app" (PDF §1) | 1 | *Touchscreen issues*, 0.732, a plan that contains the navigation-bar action |

## E. Runtime extractor comparison (`compare_models.json`, ADR-017)

Same inputs for every model: all 11 SIIS documents, three passes each on a fresh
model load (N = 33), every plan graded, on the final code (grounding check and
bounded schema included).

| Model | Params | P50 (ms) | P95 (ms) | Plans passing all gates | Step accuracy | Auto actions linked · agreeing with compiled plan | Names the guard replaced | Tokens (prompt / completion) |
|---|---|---|---|---|---|---|---|---|
| **qwen2.5:1.5b (shipped)** | 1.5B | 3128 | 5223 | **33/33** | **2.97** | 9% · 50% | 36 (plans kept 111 actions) | 606 / 272 |
| llama3.2:3b | 3.2B | 5196 | 8486 | 33/33 | 2.91 | 16% · 33% | 21 (123) | 594 / 243 |
| qwen2.5:3b | 3.1B | 4661 | 6434 | 33/33 | 2.75 | 17% · 100% | 79 (59) | 606 / 262 |
| gemma3:1b | 1.0B | 4257 | 5757 | 21/33 (3 of 11 docs empty; 3 fail G12) | 2.90 | 10% · 0% | 51 (69) | 483 / 256 |
| gemma3:4b | 4.3B | 6968 | 10061 | 30/33 (3 fail G2) | 2.86 | 10% · 50% | 9 (156) | 607 / 268 |

`qwen2.5:1.5b` is the only model that is both fastest and fully valid, and it has
the best step accuracy. `qwen2.5:3b` links more precisely, but the guard had to
replace more of its names than it kept actions, and it is slower with lower step
accuracy. `llama3.2:3b` and `gemma3:4b` miss the 8 s budget on this GPU;
`gemma3:1b` returns no plan for 3 of 11 documents. `gemma3:4b` names its actions
best (9 replacements) and is the organizer-encouraged family: the natural upgrade
on a faster GPU, via `PRISM_EXTRACT_MODEL` with no code change.

The `qwen2.5:1.5b` row here (P95 5223 ms) and `docs/metrics.md` §3 (5345 ms) are
separate runs of the same code; §3 is the official figure.

One `llama3.2:3b` run measured P95 30 s with two timeouts. The A/B in
`verify_schema_bounds_ab.txt` showed decode speed is the same with and without the
schema bounds (41.7 vs 43.9 tok/s), and a clean re-run gave the figures above: the
slowdown was the machine, not the model.

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
results enough to flip near-tied tokens. It shows in quality too: one long-running
process linked 2 of 66 auto actions where three fresh loads linked 6.

## H. Changes since the previous report

- **Cold P95 7333 → 5345 ms.** The earlier figure was `qwen2.5vl:7b` on an RTX
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
