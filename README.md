# Smart Guided Troubleshooting Engine

Samsung PRISM GenAI Hackathon 3.0 — **Theme 02**.

Turns a vague Galaxy complaint into a schema-valid, deeplinked troubleshooting
plan: **13.7 ms P95** on a cache hit, **7.3 s P95** cold, zero URL leaks, zero
hallucinated URIs.

## Quickstart

```bash
make venv          # pinned Python 3.12 + deps
make build         # compile plans -> artifacts/
make test          # 31 regression tests
make bench         # latency percentiles
make serve         # REST API on :8000
```

Needs Ollama for the cold path only; the hot path invokes no model.

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/v1/troubleshoot \
  -H 'Content-Type: application/json' \
  -d '{"query":"my phone display is totally black and wont switch on"}'
```

## Design in three lines

1. **The LLM never writes user-facing text.** It selects source step *indices*;
   deterministic code renders them. A step absent from the source cannot exist.
2. **The LLM never sees or emits a URI.** It emits a screen descriptor; a hybrid
   BM25+dense retriever resolves it against the catalog. URI hallucination is
   structurally impossible.
3. **One pipeline, two invocation times.** The warm cache is `pipeline(doc)` run
   at build time and frozen — not a separate hand-authored path.

## Docs

| file | contents |
|---|---|
| `docs/ARCHITECTURE.md` | stage-by-stage design, invariants, latency budget |
| `docs/DATA_CONTRACT.md` | field rules, gates G0-G16, catalog hazards |
| `docs/TECH_PLAN.md` | stack, repo layout, milestones, anti-hardcoding posture |
| `docs/DECISIONS.md` | ADRs with rationale + open questions for the mentor |
| `docs/metrics.md` | measured results per PDF Appendix C |

## Layout

```
engine/      normalize, segment, extract (cold), deeplink, assemble, cache, embed
validators/  scrub (G0) + gates (G1-G16)
api/         FastAPI: POST /v1/troubleshoot, GET /health
scripts/     compile_plans, run_batch, bench, eval_deeplinks, eval_cache, calibrate
build/       skeletons.json - captured build-tier LLM output
artifacts/   generated: plan_library, cache_vectors, cache_manifest
```

`artifacts/` is **generated only** by `scripts/compile_plans.py`. Never hand-edit
it; fix the pipeline and rebuild.
