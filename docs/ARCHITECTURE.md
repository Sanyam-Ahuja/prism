# Smart Guided Troubleshooting Engine — Architecture

Theme 02, Samsung PRISM GenAI Hackathon 3.0.
Authoritative spec: `Theme_2_Troubleshooting_Smart_Guided_Troubleshooting_Engine_OCR.pdf` (the mother doc).
This document describes *how* the system satisfies that spec. Where this document and the PDF disagree, the PDF wins.

---

## 1. Constraints the design must satisfy

| # | Constraint | Source | Enforcement point |
|---|---|---|---|
| C1 | Output validates against `schema.py` | PDF §4.1 | Stage 5 |
| C2 | Zero web URLs (`http`, `https`, `www.`, markdown links) | PDF §4.2.1 | Stage 5 scrubber |
| C3 | Deeplink URIs copied verbatim from catalog; never synthesized | PDF §4.2.2 | Stage 3 (LLM never emits a URI) |
| C4 | No steps absent from the source reference text | PDF §4.2.3 | Stage 2 (index selection) |
| C5 | Pure JSON, no markdown fence, no preamble | PDF §4.2.4 | API serializer |
| C6 | P95 ≤ 300 ms on cache hit | PDF §6.3 | Stage 1 (no model inference) |
| C7 | P95 ≤ 8000 ms cold | PDF §6.3 | Stage 2 token budget |
| C8 | ≥ 80% semantic cache hit on unseen paraphrases | PDF §6.2 | Stage 0 + variation seeding |
| C9 | Deterministic output for identical/equivalent input | PDF §6.1 | Frozen plan library, temp 0 |
| C10 | One Action = One Screen | PDF §7.2 | Stage 2 grouping + Stage 5 gate |

---

## 2. Three invariants

Everything below follows from these. If a change violates one, reject the change.

**I1 — The LLM never writes user-facing text.**
It selects and groups; deterministic code renders. Steps are *indices into the source document*, not generated prose. This makes C4 structurally impossible to violate and collapses the output token count (see §8).

**I2 — The LLM never sees or emits a URI.**
It emits a natural-language *screen descriptor*. A deterministic retriever resolves descriptor → catalog entry. This makes C3 structurally impossible to violate.

**I3 — One pipeline, two invocation times.**
The warm cache is literally `pipeline(doc)` executed at build time and frozen. There is no separate hand-authored answer path. Required by PDF Phase 3 ("pre-computed query variations"), and it is what keeps us clean under the hackathon's manual anti-hardcoding review.

---

## 3. System diagram

```
                      BUILD TIME (offline, network OK, no clock)
  siis_responses.json ──┐
  deeplinks.json ───────┤
                        ▼
              ┌───────────────────────┐
              │  compile_plans.py     │  hosted LLM (quality tier)
              │  = pipeline(doc)      │  → Stage 2..5, full repair loop
              └───────────┬───────────┘
                          ▼
        plan_library.json + cache/out-of-scope/catalog vectors   ← committed artifacts
                          │
══════════════════════════╪═══════════════════════════════════════════════
                          ▼            RUNTIME (clock running)
  POST /v1/troubleshoot ──┐
                          ▼
              ┌───────────────────────┐   no            ┌───────────────────────┐
              │ siis_response present?├───────────────► │ Stage 0+1: normalize, │ HIT (one plan per
              └───────────┬───────────┘                 │ split into problems,  │ problem) ──► 200
                          ▼ yes                          │ semantic cache lookup │ MISS or out of
              ┌───────────────────────┐   HIT            └───────────────────────┘ scope ──► no_siis_context
              │ article-keyed plans   ├──────────────► 200  (compiled plan for a library article,
              │ (ADR-019)             │                      or the plan this article got before)
              └───────────┬───────────┘
                    unseen article
                          ▼
              ┌───────────────────────┐
   Stage 2    │ structure extraction  │  local LLM, resident, ~150 out tok
              │ (index selection)     │
              └───────────┬───────────┘
                          ▼
   Stage 3    │ deeplink resolution   │  deterministic, hybrid retrieval
                          ▼
   Stage 4    │ ordering + assembly   │  deterministic
                          ▼
   Stage 5    │ validate + repair     │  deterministic, ≤2 repair rounds
                          ▼
                     serialize ──► 200   (or fallback: no_match)
```

---

## 4. Stage 0 — Query normalization and cache key

Purpose: map diverse colloquial phrasings onto one semantic representation (PDF §7.1: raw string keys cause paraphrase misses).

Deterministic normalization, in order:

1. Lowercase; NFKC unicode normalization.
2. Strip list-item artifacts (`1.`, `2)`, surrounding quotes) — required by `input.txt` rows 16 and 17.
3. Redact device model tokens to a `<device>` placeholder (`A115G`, `S22`, `Z Flip 7`, `S26 Ultra`, `S*****`). Device model is almost never plan-relevant; leaving it in fragments the cache.
4. Expand contractions; strip filler (`my`, `please`, `i need help with`).
5. Collapse whitespace.

The normalized string is embedded (see §6). **The cache key is the embedding, not the string.** The normalized string is retained only for logging and exact-match short-circuit.

Device tokens are stripped from the key but preserved in the request record, because `category: manual` actions may reference form factor (foldable inner vs cover screen — rows 8, 10, 17).

---

## 5. Stage 1 — Semantic cache

Corpus: for each compiled plan, one vector per canonical query **plus one per generated variation**. With 11 plans × (1 + 10) ≈ 121 vectors today.

`query_variations` are therefore not merely an output field — they are **cache seeds**. This is the mechanism that buys C8.

Lookup is brute-force cosine over a single `(N, 384)` float32 matrix: one `numpy` matmul. At N ≈ 121, or even N ≈ 5000, this is sub-millisecond. **No FAISS, no SQLite, no Redis.** An ANN index at this scale adds a dependency, a build step and a recall cliff, and buys nothing.

Decision rule for one query vector (`engine/cache.py::PlanCache.decide`):

| condition | action |
|---|---|
| best cosine < `TAU_HIT` (0.70) | miss |
| an out-of-scope anchor is at least as close as the best plan seed | miss: out of scope (ADR-020) |
| best cosine ≥ `TAU_HIGH` (0.80) | hit |
| otherwise, margin over the best *other* plan ≥ `TAU_MARGIN` (0.04) | hit, else miss |

A single threshold cannot reach the ≥80% paraphrase hit rate without false hits on
out-of-domain queries; the margin and the anchors separate them where raw
similarity cannot (ADR-013, ADR-020; calibration tables in `reports/README.md` §C).

The cache is consulted **only when no `siis_response` is supplied**. With reference
text, the article decides (ADR-019): a battery complaint sent with a battery
article must never get a Display plan because its wording resembled one.

A complaint naming several problems ("…cracked. Also, every tap lags") is split at
explicit problem boundaries and each clause is looked up with the same rule; every
distinct plan found is returned, first-mentioned first (ADR-021, §9.3).

---

## 6. Embeddings

`sentence-transformers` with `BAAI/bge-small-en-v1.5` (384-dim, ~130 MB, CPU).

- CPU-only by design: the GPU is reserved for the Stage 2 model, and 8 GiB VRAM is contended.
- Weights vendored into the image; no network at runtime.
- `model.eval()`, `torch.inference_mode()`, fixed seed → bitwise-reproducible vectors → C9.
- Encode latency for a ~20-token query: single-digit ms. Measure and record; do not assume.

The same encoder serves the query cache (§5) and the deeplink catalog index (§8). One model, loaded once.

---

## 7. Stage 2 — Structure extraction (index selection)

**This is the core of the design.** Naive generation of a full plan is ~800 output tokens; at the measured 43.5 tok/s on the RTX 4060 that is ~18 s, over the 8 s budget (C7). The fix is to make the model emit almost nothing.

### 7.1 Pre-processing (deterministic)

The SIIS `content` is segmented into atomic candidate steps and indexed:

1. Split on markdown headings (`#`, `##`, `###`) → section boundaries with titles.
2. Split section bodies into sentences.
3. Drop non-actionable sentences (no imperative verb; pure narration such as *"We understand that dealing with a cracked screen can be frustrating."*).
4. Emit a numbered candidate list, grouped under its heading.

For `Blank or black display` this yields ~18 candidates under 4 headings.

### 7.2 Model call

Prompt = the numbered candidate list + the heading structure. Output is grammar-constrained via Ollama's `format` parameter (JSON Schema → constrained decoding), so unparseable output is impossible.

```jsonc
{
  "topic": "Blank Screen",              // → goal template
  "title": "Blank screen display",      // 2–3 words
  "actions": [
    {
      "name": "Inspect Charging Port",
      "screen": null,                   // null ⇒ no Settings screen ⇒ manual
      "category": "manual",
      "benefit": "detect liquid or physical damage",
      "steps": [1, 2, 3]                // INDICES, not text
    },
    {
      "name": "Force Restart Device",
      "screen": "restart device when needed",
      "category": "critical",
      "benefit": "recover an unresponsive device",
      "steps": [7, 8]
    }
  ]
}
```

Output size: ~150 tokens ⇒ ~3.4 s generation. Within budget.

### 7.3 Post-processing (deterministic)

- `steps: [7, 8]` → the verbatim candidate sentences at those indices, normalized to imperative form. **A step that is not an index into the source cannot exist.** (C4)
- `benefit` → `"It will " + benefit`, trimmed/padded to exactly 5–7 words.
- `name` → Title Case.
- `topic` → `"Follow these steps to perform this {topic} Troubleshooting"`.
- `screen` → Stage 3.

### 7.4 Grouping rule (C10)

Model instruction: *one action per physical screen or per physical intervention.* Enforced afterward in Stage 5 — if two actions resolve to the same deeplink, merge their step groups; if one action's steps span two resolved deeplinks, split it.

---

## 8. Stage 3 — Deeplink resolution

Input: a screen descriptor string + an intent verb. Output: a catalog entry, or `bixby://dummy_positive`. **Purely deterministic — no model call.**

### 8.1 Index

Built once at startup over all 578 entries. Each entry's searchable text is `description + " " + message + " " + qna_description`. **Never the URI** (PDF §7.4).

Two retrievers, score-fused:

- **BM25** (`rank_bm25`) — catches exact feature names (*"Edge panels"*, *"Motion smoothness"*).
- **Dense** (bge-small, §6) — catches paraphrase.

Fusion: Reciprocal Rank Fusion, `k=60`. RRF needs no score normalization between the two scales.

### 8.2 Polarity gate

Applied **before** ranking, as a hard filter on `originalType`. This is the single highest-leverage precision mechanism and directly targets the parent-menu penalty (PDF §6.2).

| descriptor intent | required `originalType` | example |
|---|---|---|
| enable / turn on / activate | `onURL` | DL-0542 Enable Back up data |
| disable / turn off / remove | `offURL` | DL-0095 Disable Edge panels |
| set / adjust / change to *value* | `updateURL` | DL-0220 Adjust Timeout |
| open / go to / navigate / view | `onClickURL` | DL-0549 View Screen zoom |

Intent is derived from the descriptor's leading verb by a rule table, not by a model.

### 8.3 Noise filter

The catalog contains SmartThings appliance entries irrelevant to phone troubleshooting (`DL-0468` air conditioner mode, `DL-0469`/`DL-0473` refrigerator). A denylist of appliance terms removes these from the phone-domain candidate pool.

Also note `DL-0294`/`DL-0295` carry malformed `message` values (`"Offurl"`, `"Onurl"`) and a `validation.key` equal to `"offURL"`/`"onURL"`. Treat as corrupt: usable `deeplink`, unusable metadata. Do not copy their `message` into output.

### 8.4 Thresholds and the dummy escape hatch

```
score = RRF(bm25, dense)  after polarity + noise filtering

score ≥ TAU_LINK (tunable, calibrate on the labelled set)  → catalog entry, copied verbatim
score <  TAU_LINK                                           → bixby://dummy_positive
```

**The catalog has real coverage holes.** Verified absent: *safe mode*, *auto-rotate*, *screen mirroring / Smart View*, *software update*, *clear app cache* — all of which the SIIS documents explicitly instruct. `dummy_positive` exists for exactly this (PDF §3).

Both failure directions are penalized, so `TAU_LINK` is a calibrated trade-off, not a default:

- Too low → wrong/parent-menu links → loses *Deeplink relevance* (0–2).
- Too high → excess `dummy_positive` → loses *Auto actions carrying valid actionable deeplink* (≥90%).

When `dummy_positive` is selected, `description` and `message` are **written by us**, naming the concrete screen from the steps, per the catalog's own `DL-DUMMY.qna_description` instruction.

### 8.5 Field copying

Verified against `sample_output.json`: the actionable deeplink is a field-for-field verbatim copy.

```python
actionableDeeplink = {
    "deeplink":     e["deeplink"],
    "description":  e["description"],
    "message":      e["message"],
    "originalType": e["originalType"],
}
validationDeeplink = e["validation"]   # verbatim, or None
```

`validation` is present on 570/578 entries; only 138 carry `resultType`/`condition`/`value`. Copy whatever is there. **Never synthesize a validation block** — a fabricated `bixby://masked/val/...` violates C3 exactly as a fabricated action URI does.

`Deeplink.classes` is in the schema but absent from the catalog and unused in the sample. Always omit.

---

## 9. Stage 4 — Ordering and assembly

### 9.1 Category assignment

| category | definition (PDF §4.1) | may carry actionableDeeplink |
|---|---|---|
| `auto` | standard Settings screen reachable by deeplink | yes — required |
| `critical` | disruptive/irreversible: factory reset, restart, firmware update, safe mode | optional |
| `manual` | physical: cleaning ports, hardware replacement, service centre visit | **no — hard gate** |

The model proposes; a deterministic rule table overrides. A `critical` keyword (`reset`, `restart`, `safe mode`, `update firmware`) in the step text forces `critical` regardless of what the model said.

### 9.2 Sort

Stable sort by `(category_rank, disruption_score, source_order)`:

```
category_rank: auto(0) < manual(1) < critical(2)
```

`critical` last is mandated (PDF §4.1). `source_order` as the final key preserves the SIIS document's own sequence and guarantees C9.

### 9.3 Multi-goal policy

`contexts` is `List[Goal]`. Policy: **one Goal per distinct matched plan**, in the order the complaint mentions the problems (ADR-021). Implemented only on the hot path: a cold plan derives from one supplied article.

Rationale from the data: `input.txt` row 17 bundles three symptoms (cracked at fold / touch dead in places / can barely see) that share one root cause and resolve to one SIIS document (*Cracked or bleeding screen*), so it yields **one** Goal. So the complaint is split only at explicit problem boundaries — sentence ends, "also", "plus", ", and the/my" — never at a bare "and", and each clause must pass the full acceptance rule including the out-of-scope check. Measured: 0 extra plans on 53 single-problem held-out paraphrases; both plans returned for 100% of 340 joined pairs of supplied complaints (`reports/multi_intent.txt`). A second problem no plan covers is dropped rather than guessed.

### 9.4 `score`

Retrieval confidence for that Goal, in `[0.0, 1.0]`:
- cache hit → the frozen compile-time score.
- cold path → normalized fused retrieval score of the query against the matched SIIS document.

---

## 10. Stage 5 — Validation and repair

A deterministic layer, not a prompt. PDF §7.5 names prompt-only constraint enforcement as a pitfall.

Order of operations:

1. **Scrub** — regex-strip URLs and markdown links from every string field (C2). Runs first so later gates see clean text.
2. **Coerce** — goal template, title 2–3 words sentence case, description 5–7 words starting `It will`, actionName Title Case.
3. **Structural gates** — one action = one screen; manual carries no actionable deeplink; every emitted URI ∈ catalog ∪ `{dummy_positive}`; `critical` sorted last.
4. **Pydantic validate** against `schema.py`.
5. **Repair** — on failure, one targeted retry (LLM sees the specific violation), then a deterministic trim.
6. **Drop** — if an action still fails, drop that action rather than emit invalid JSON. If zero actions survive, return the `no_match` fallback.

Full gate list with severities: `DATA_CONTRACT.md`.

---

## 11. Runtime paths and fallbacks

| path | condition | `cache_hit` | `cost_usd` | target P95 |
|---|---|---|---|---|
| Hot | no `siis_response`, cache hit (one plan per problem) | `true` | `0.0` | ≤ 300 ms |
| Known article | `siis_response` is a library article, as sent or reformatted | `true` | `0.0` | ≤ 300 ms |
| Repeated article | `siis_response` the cold path has already planned | `true` | `0.0` | ≤ 300 ms |
| Cold | `siis_response` is an unseen article | `false` | tracked | ≤ 8000 ms |
| Fallback `no_siis_context` | no `siis_response` and no plan in scope | `false` | `0.0` | ≤ 300 ms |
| Fallback `no_match` | pipeline yields zero valid actions | `false` | tracked | — |

Both fallbacks return `{"contexts": []}` with the reason in `meta.fallback` (PDF §4.2.3).

`GET /health` returns 200 only once the encoder, the catalog index, the plan library **and** the Ollama connection are all initialized — not merely that the process is up (PDF §5).

---

## 12. Latency budget

Measured on this machine: RTX 4060 Laptop (7.5 GiB usable VRAM), 22 GB RAM, 16 cores.

Benchmark, `qwen2.5vl:7b`, cold, n=1 — **indicative only, re-measure warm with the chosen model**:

```
load        12.94 s     ← eliminated by keeping the model resident
prompt eval  6.61 s     ← cold-start inflated; re-measure warm
generation   7.47 s / 325 tok  →  43.5 tok/s
total       27.03 s
```

Derived budgets:

| path | component | budget |
|---|---|---|
| Hot | normalize | < 1 ms |
| | embed (CPU, bge-small) | ~8 ms |
| | cosine over N≈121 | < 1 ms |
| | serialize | < 1 ms |
| | **total** | **~15 ms** (C6 has 20× headroom) |
| Cold | segment + index SIIS | ~5 ms |
| | prompt eval (~700 tok, warm) | ~1.5 s |
| | generation (~150 tok @ 43.5 tok/s) | ~3.4 s |
| | deeplink resolution (k actions) | ~20 ms |
| | validate + repair | ~5 ms |
| | **total** | **~5 s** (C7 met, ~3 s margin) |

The margin is why §7 emits indices instead of prose. Under naive full-text generation the cold path is ~18 s and **fails C7**.

**Measured, 2026-09-30** (RTX 4050 Laptop 6 GiB, `qwen2.5:1.5b`, warm; ADR-017, `docs/metrics.md` §3). The derived budget above underestimated two components and missed one:

| component | measured |
|---|---|
| prompt eval (~600 tok) | 0.1–0.5 s |
| generation | 84–500 tok (mean 272) at ~60–124 tok/s under the JSON grammar (~124 on a quiet machine, about half that with other work running) → 1–5 s; the 500-token cap sets the tail |
| deeplink resolution | ~0.2 s per plan with probes batch-encoded; it was ~1 s at one encoder call (~70 ms) per probe |
| HTTP to Ollama | ~5 ms with one pooled client; it was ~3 s per request on Windows with a client per call and `localhost` → IPv6 fallback |
| **end to end** | **P50 2576 ms · P95 4389 ms** (N=33, final code, quiet machine; 3199 / 5215 ms with other work running) |

Two hard requirements follow:
- `OLLAMA_KEEP_ALIVE=-1` — the 12.9 s load must never land on a request.
- The Stage 2 model must fit in 7.5 GiB VRAM. `gemma4:26b` (18 GB) **does not** and would spill to CPU. See TECH_PLAN.md §4.

---

## 13. Determinism (C9)

| source of nondeterminism | mitigation |
|---|---|
| LLM sampling | `temperature: 0`, fixed `seed`, `top_k: 1` |
| Model server prompt cache (4 of 11 articles differed once cached, `reports/determinism.txt`) | the first validated plan for an article is kept and returned for every later request with it (ADR-019) |
| Hot path | frozen plan library — byte-identical by construction |
| Embedding drift | pinned model revision, vendored weights, `inference_mode` |
| Dict/set iteration | explicit sorts everywhere; no `set` in ordering paths |
| Tie-breaking in retrieval | final sort key is catalog `id`, never insertion order |
| Float accumulation | fixed dtype (`float32`), fixed operation order |

Grading takes a median over repetitions, but an engine that is only sometimes right still loses judged accuracy points. Determinism is a correctness property here, not a nicety.
