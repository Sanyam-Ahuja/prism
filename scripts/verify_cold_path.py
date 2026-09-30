"""Cold-path checks cited in docs/metrics.md (reports/verify_*.txt).

  ab         JSON-schema length bounds on vs off, same prompts: do they cost decode speed?
  breakdown  startup timing, step-accuracy components, documents hitting the output cap

    python scripts/verify_cold_path.py ab | breakdown
"""
import copy, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import engine.cold as ec
from engine.segment import segment

docs, seen = [], set()
for r in json.load(open("data/siis_responses.json"))["responses"]:
    if r["siis_response"]["title"] not in seen:
        seen.add(r["siis_response"]["title"]); docs.append(r["siis_response"])


def strip_bounds(s):
    s = copy.deepcopy(s)
    def walk(n):
        if isinstance(n, dict):
            n.pop("maxLength", None); n.pop("maxItems", None)
            for v in n.values(): walk(v)
    walk(s); return s


def gen(model, prompt, schema):
    p = {"model": model, "prompt": prompt, "format": schema, "stream": False, "keep_alive": -1,
         "options": {"temperature": 0, "top_k": 1, "seed": 42, "num_predict": 500}}
    t = time.perf_counter(); d = httpx.post(f"{ec.OLLAMA}/api/generate", json=p, timeout=180).json()
    return time.perf_counter() - t, d.get("eval_count", 0), d.get("eval_duration", 1) / 1e9


if sys.argv[1] == "ab":
    print("# A/B: identical prompts, JSON schema with vs without maxLength/maxItems bounds")
    for model in ["qwen2.5:1.5b", "llama3.2:3b"]:
        httpx.post(f"{ec.OLLAMA}/api/generate", json={"model": model, "keep_alive": -1}, timeout=300)
        for label, schema in [("bounded", ec.SKELETON_SCHEMA), ("unbounded", strip_bounds(ec.SKELETON_SCHEMA))]:
            wall = tok = dur = 0.0
            for d in docs[:6]:
                seg = segment(d["content"], d["title"])
                w, n, ed = gen(model, ec.PROMPT.format(title=seg.title, numbered=seg.numbered()), schema)
                wall += w; tok += n; dur += ed
            print(f"{model:14s} {label:9s}: 6 docs, total {wall:6.1f} s, {tok:5.0f} tokens, "
                  f"decode {tok / dur:6.1f} tok/s")
        httpx.post(f"{ec.OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)

else:
    from engine.embed import get_encoder
    t0 = time.perf_counter(); enc = get_encoder(); t1 = time.perf_counter()
    from engine.cache import PlanCache; PlanCache(); t2 = time.perf_counter()
    cold = ec.ColdPath(enc, model="qwen2.5:1.5b"); t3 = time.perf_counter()
    print(f"STARTUP  encoder load {t1 - t0:5.1f}s | plan cache {t2 - t1:4.1f}s | "
          f"ColdPath (catalog vectors from the build) {t3 - t2:5.1f}s | total {t3 - t0:5.1f}s")
    from scripts.score_plans import step_accuracy
    lib = {p["doc"]: p["plan"] for p in json.load(open("artifacts/plan_library.json"))["plans"]}
    httpx.post(f"{ec.OLLAMA}/api/generate", json={"model": cold.model, "keep_alive": 0}, timeout=60)
    httpx.post(f"{ec.OLLAMA}/api/generate", json={"model": cold.model, "keep_alive": -1}, timeout=300)
    agg = {"compiled": [0.0] * 4, "cold": [0.0] * 4}
    capped = []
    for d in docs:
        seg = segment(d["content"], d["title"])
        r = cold.run("the screen is not behaving correctly", d)
        if r["tokens"]["completion"] >= 500:
            capped.append(d["title"][:40])
        for k, plan in (("compiled", lib[d["title"]]), ("cold", r["contexts"][0] if r["contexts"] else None)):
            if plan:
                s = step_accuracy(plan, seg)
                for i in range(4): agg[k][i] += s[i]
    for k, v in agg.items():
        print(f"{k:8s} step accuracy {v[0] / 11:.2f} = completeness {v[1] / 11:.2f} + "
              f"correctness {v[2] / 11:.2f} + ordering {v[3] / 11:.2f}")
    print(f"documents reaching the 500-token output cap: {len(capped)}/11 {capped}")
