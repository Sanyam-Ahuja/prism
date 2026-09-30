"""Cold-path extractor comparison: latency AND quality per candidate model.

Latency alone would favour the smallest model that emits valid JSON, so every
cold plan is also graded: gate compliance, the Appendix C step-accuracy rubric
(scripts/score_plans.py), catalog deeplink coverage of auto actions, and
agreement of emitted deeplinks with the compiled reference plan for the same
document.

Each pass runs on a fresh model load. Output is byte-identical within one load
(temperature 0, fixed seed), but measured to differ between loads, so passes on
a single load would repeat one sample three times and hide that variance.

    python scripts/compare_models.py --models qwen2.5:3b gemma3:4b --passes 3
"""
import argparse, json, os, sys, time
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from engine.cold import OLLAMA, ColdPath
from engine.embed import get_encoder
from engine.segment import segment
from scripts.bench import pct
from scripts.score_plans import step_accuracy
from validators.gates import blocking, load_ctx, validate_envelope

CTX = load_ctx("data/deeplinks.json")
QUERY = "the screen is not behaving correctly"


def ref_links(lib):
    out = {}
    for p in lib:
        out.setdefault(p["doc"], set()).update(
            g["actionableDeeplink"]["deeplink"]
            for a in p["plan"]["actions"] for g in a["stepGroups"]
            if (g.get("actionableDeeplink") or {}).get("deeplink", "").startswith("bixby://masked"))
    return out


def unload(model):
    try:
        httpx.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
    except httpx.HTTPError:
        pass


def grade_pass(cold, docs, refs, lat, ptok, ctok, gate_fail, empty_docs):
    """One pass over every document on the currently loaded model."""
    s = {"plans": 0, "gate_pass": 0, "auto": 0, "linked": 0, "agree": 0, "steps": [],
         "actions": 0, "names_repaired": 0, "screens_repaired": 0}
    for d in docs:
        t0 = time.perf_counter()
        r = cold.run(QUERY, d)
        lat.append((time.perf_counter() - t0) * 1000)
        rep = (r or {}).get("repaired") or {}
        s["names_repaired"] += rep.get("names", 0)
        s["screens_repaired"] += rep.get("screens", 0)
        ptok.append((r or {}).get("tokens", {}).get("prompt", 0))
        ctok.append((r or {}).get("tokens", {}).get("completion", 0))
        ctxs = (r or {}).get("contexts", [])
        if not ctxs:
            empty_docs[d["title"][:40]] += 1
            continue
        plan = ctxs[0]
        s["plans"] += 1
        env = {"query": "q", "query_variations": r["variations"], "response": {"contexts": ctxs}}
        bad = blocking(validate_envelope(env, CTX))
        s["gate_pass"] += not bad
        gate_fail.update({v.gate for v in bad})
        s["steps"].append(step_accuracy(plan, segment(d["content"], d["title"]))[0])
        s["actions"] += len(plan["actions"])
        for a in plan["actions"]:
            if a["category"] != "auto":
                continue
            s["auto"] += 1
            masked = {(g.get("actionableDeeplink") or {}).get("deeplink", "")
                      for g in a["stepGroups"]}
            masked = {u for u in masked if u.startswith("bixby://masked")}
            s["linked"] += bool(masked)
            s["agree"] += bool(masked & refs.get(d["title"], set()))
    return s


def run_model(model, docs, refs, encoder, passes):
    cold = ColdPath(encoder, model=model)
    if not cold.available():
        return {"model": model, "error": "not pulled"}
    lat, ptok, ctok = [], [], []
    gate_fail, empty_docs = Counter(), Counter()
    loads = []
    for p in range(passes):
        if p:
            unload(model)
        cold.run("warmup", docs[0])                   # (re)load off the clock
        loads.append(grade_pass(cold, docs, refs, lat, ptok, ctok, gate_fail, empty_docs))

    def tot(k):
        return sum(s[k] for s in loads)

    steps = [x for s in loads for x in s["steps"]]
    n = len(lat)
    return {
        "model": model, "n": n, "loads": passes,
        "p50_ms": round(pct(lat, 50)), "p95_ms": round(pct(lat, 95)), "max_ms": round(max(lat)),
        "plans_emitted": tot("plans"), "gate_pass": tot("gate_pass"),
        "step_accuracy": round(sum(steps) / max(len(steps), 1), 2),
        "auto_actions": tot("auto"),
        "auto_with_catalog_link_pct": round(100 * tot("linked") / max(tot("auto"), 1)),
        "link_agrees_with_reference_pct": round(100 * tot("agree") / max(tot("linked"), 1)),
        # Spread across independent loads, per load: gate-passing plans / 11,
        # and % of auto actions carrying a catalog deeplink.
        "per_load_gate_pass": [s["gate_pass"] for s in loads],
        "per_load_link_pct": [round(100 * s["linked"] / max(s["auto"], 1)) for s in loads],
        "mean_prompt_tokens": round(sum(ptok) / n), "mean_completion_tokens": round(sum(ctok) / n),
        # Model-written names and screens its own steps did not support, replaced
        # by the grounding guard in engine/cold.py (per extracted action).
        "names_repaired": tot("names_repaired"), "screens_repaired": tot("screens_repaired"),
        "actions_emitted": tot("actions"),
        "gate_failures": dict(gate_fail), "empty_docs": dict(empty_docs),
    }


def model_size(model):
    try:
        for m in httpx.get(f"{OLLAMA}/api/tags", timeout=5).json()["models"]:
            if m["name"] == model:
                return m["details"].get("parameter_size"), m["details"].get("quantization_level")
    except Exception:
        pass
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--out", default="bench_models.json")
    a = ap.parse_args()

    rows = json.load(open("data/siis_responses.json"))["responses"]
    docs, seen = [], set()
    for r in rows:
        if r["siis_response"]["title"] not in seen:
            seen.add(r["siis_response"]["title"]); docs.append(r["siis_response"])
    refs = ref_links(json.load(open("artifacts/plan_library.json"))["plans"])
    enc = get_encoder()

    # Merge by model name, so candidates can be benchmarked one at a time as
    # they finish downloading and still land in a single results file.
    results = {}
    if os.path.exists(a.out):
        results = {r["model"]: r for r in json.load(open(a.out))}
    for m in a.models:
        res = run_model(m, docs, refs, enc, a.passes)
        res["params"], res["quant"] = model_size(m)
        results[m] = res
        print(json.dumps(res), flush=True)
        unload(m)                                     # next candidate gets the whole GPU
        json.dump(list(results.values()), open(a.out, "w"), indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
