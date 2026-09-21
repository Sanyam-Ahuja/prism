"""Replay input.txt through the engine, emit results.jsonl (PDF Appendix B)."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.cache import TAU_HIT, PlanCache
from validators.gates import blocking, load_ctx, validate_envelope


def main():
    queries = [l.strip() for l in open("data/input.txt") if l.strip()]
    cache, ctx = PlanCache(), load_ctx("data/deeplinks.json")
    siis = {r["original_query"]: r["siis_response"]
            for r in json.load(open("data/siis_responses.json"))["responses"]}

    cold = None
    hits = valid = 0
    with open("results.jsonl", "w") as out:
        for q in queries:
            t0 = time.perf_counter()
            plan, sim = cache.lookup(q, tau=TAU_HIT)
            if plan is not None:
                env = {"query": q, "query_variations": plan["query_variations"],
                       "response": {"contexts": [dict(plan["plan"], score=round(min(1.0, sim), 3))]},
                       "meta": {"latency_ms": round((time.perf_counter()-t0)*1000, 2),
                                "cache_hit": True, "model": None, "cost_usd": 0.0,
                                "fallback": None}}
                hits += 1
            else:
                if cold is None:
                    from engine.cold import ColdPath
                    cold = ColdPath(cache.encoder)
                ref = next((v for k, v in siis.items() if q[:60] in k), None)
                r = cold.run(q, ref) if ref else None
                env = {"query": q, "query_variations": r["variations"] if r else [],
                       "response": {"contexts": r["contexts"] if r else []},
                       "meta": {"latency_ms": round((time.perf_counter()-t0)*1000, 2),
                                "cache_hit": False, "model": cold.model,
                                "cost_usd": 0.0,
                                "fallback": None if (r and r["contexts"]) else "no_match"}}
            bad = blocking(validate_envelope(env, ctx))
            valid += not bad
            if bad:
                print(f"  INVALID: {q[:50]} -> {bad[0].gate} {bad[0].message[:70]}")
            out.write(json.dumps(env) + "\n")

    n = len(queries)
    print(f"queries={n}  cache_hits={hits} ({hits/n:.0%})  schema_valid={valid}/{n} ({valid/n:.0%})")


if __name__ == "__main__":
    raise SystemExit(main())
