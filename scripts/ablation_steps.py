"""Step-level ablation: does the extractor select source steps or write them?

ADR-001 has the model return step NUMBERS and code copy those steps verbatim.
The obvious alternative, and what a typical RAG pipeline does, is to hand the
same model the article and let it write the plan's steps as text. This measures
both with the same model, decoding settings and articles, and grades every
output step with the same checker:

  verbatim    - the step text is one of the article's sentences or segmented
                steps, or (12+ characters) occurs inside the article, after
                normalising case, whitespace and edge punctuation
  paraphrase  - not verbatim, but its nearest article sentence has cosine
                >= TAU_CLOSE (bge-small)
  loose       - nearest article sentence cosine in [TAU_LOOSE, TAU_CLOSE)
  unsupported - nearest article sentence cosine < TAU_LOOSE: no sentence in
                the source says this

The thresholds are a judgement call, so the report also gives the unsupported
share at other cut-offs and prints every unsupported step for inspection.

    python scripts/ablation_steps.py
"""
import json, os, re, statistics, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import numpy as np

from engine.cold import MODEL, OLLAMA, SKELETON_SCHEMA, PROMPT
from engine.embed import get_encoder
from engine.segment import segment
from scripts.bench import pct

TAU_CLOSE, TAU_LOOSE = 0.85, 0.75
SWEEP = (0.70, 0.75, 0.80, 0.85, 0.90)
URL = re.compile(r"https?://|www\.", re.I)
_HTTP = httpx.Client(base_url=OLLAMA, timeout=120.0)
OPTIONS = {"temperature": 0, "top_k": 1, "seed": 42}

# Same shape and bounds as SKELETON_SCHEMA, except steps are text, not numbers.
GEN_SCHEMA = {
    "type": "object", "required": ["title", "actions"],
    "properties": {
        "title": {"type": "string", "maxLength": 40},
        "actions": {"type": "array", "maxItems": 8, "items": {
            "type": "object", "required": ["name", "category", "steps"],
            "properties": {
                "name": {"type": "string", "maxLength": 50},
                "category": {"type": "string", "enum": ["auto", "manual", "critical"]},
                "steps": {"type": "array", "maxItems": 12,
                          "items": {"type": "string", "maxLength": 200}}}}}}}

GEN_PROMPT = """You write troubleshooting plans from a support document.

Support document "{title}":
{content}

Rules:
- Group the fix into actions: one action = one settings screen, or one physical action.
- For each action write its steps as short instructions the user follows.
- Use only information from the document.
- category: "auto" for a Settings change, "critical" for restart / safe mode /
  factory reset, "manual" for physical work or a service visit.
- Produce at most 6 actions.
- "title": 2 to 3 words naming the issue.

Return JSON only."""


def call(prompt, schema, npred):
    t0 = time.perf_counter()
    d = _HTTP.post("/api/generate", json={"model": MODEL, "prompt": prompt, "format": schema,
                                          "stream": False, "keep_alive": -1,
                                          "options": dict(OPTIONS, num_predict=npred)}).json()
    ms = (time.perf_counter() - t0) * 1000
    raw = d.get("response", "")
    try:
        out = json.loads(raw)
    except ValueError:
        out = None
    return out, raw, ms, d.get("prompt_eval_count", 0), d.get("eval_count", 0), d.get("done_reason")


def norm(s):
    return re.sub(r"\s+", " ", s.lower()).strip(" .,:;!\"'*-")


def sentences(content):
    parts = re.split(r"(?<=[.!?])\s+|\n+", content)
    return [p.strip(" #*-\t") for p in parts if len(p.strip(" #*-\t")) >= 8]


def main():
    rows = json.load(open("data/siis_responses.json", encoding="utf-8"))["responses"]
    docs, seen = [], set()
    for r in rows:
        if r["siis_response"]["title"] not in seen:
            seen.add(r["siis_response"]["title"]); docs.append(r["siis_response"])
    enc = get_encoder()
    call("Return JSON.", {"type": "object"}, 5)            # model load off the clock

    graded = {"select": [], "generate": []}                 # (step, verbatim, best cosine, doc)
    stats = {m: {"ms": [], "pt": [], "ct": [], "invalid_json": 0, "truncated": 0,
                 "url_outputs": 0, "bad_index": 0} for m in graded}
    for doc in docs:
        title, content = doc["title"], doc["content"]
        seg = segment(content, title)
        src = sentences(content) + [c.text for c in seg.candidates]
        src_vec = enc.encode(src, normalize_embeddings=True)
        haystack = norm(" ".join([content] + [c.text for c in seg.candidates]))
        exact = {norm(s) for s in src}
        cands = {c.idx: c.text for c in seg.candidates}

        for mode in graded:
            if mode == "select":
                out, raw, ms, pt, ct, why = call(PROMPT.format(title=seg.title, numbered=seg.numbered()),
                                                 SKELETON_SCHEMA, 500)
                steps = []
                for a in (out or {}).get("actions", []):
                    for i in a.get("steps", []):
                        if isinstance(i, int) and i in cands:
                            steps.append(cands[i])
                        else:
                            stats[mode]["bad_index"] += 1   # dropped by code, never shown
            else:
                out, raw, ms, pt, ct, why = call(GEN_PROMPT.format(title=title, content=content),
                                                 GEN_SCHEMA, 1500)
                steps = [s for a in (out or {}).get("actions", []) for s in a.get("steps", [])
                         if isinstance(s, str) and s.strip()]
            st = stats[mode]
            st["ms"].append(ms); st["pt"].append(pt); st["ct"].append(ct)
            st["invalid_json"] += out is None
            st["truncated"] += why == "length"
            st["url_outputs"] += bool(URL.search(raw))
            if steps:
                sims = enc.encode(steps, normalize_embeddings=True) @ src_vec.T
                for s, row in zip(steps, sims):
                    n = norm(s)
                    # A short step counts only as a whole sentence of the source,
                    # so "Tap Restart" cannot match inside an unrelated sentence.
                    verbatim = n in exact or (len(n) >= 12 and n in haystack)
                    graded[mode].append((s, verbatim, float(row.max()), title))

    print(f"Step-level ablation   model={MODEL}  articles={len(docs)}  (temperature 0, seed 42)\n")
    for mode, g in graded.items():
        n = len(g) or 1
        verb = sum(v for _, v, _, _ in g)
        para = sum(not v and c >= TAU_CLOSE for _, v, c, _ in g)
        loose = sum(not v and TAU_LOOSE <= c < TAU_CLOSE for _, v, c, _ in g)
        uns = sum(not v and c < TAU_LOOSE for _, v, c, _ in g)
        st = stats[mode]
        label = "select step numbers (shipped, ADR-001)" if mode == "select" else "write steps as text (baseline)"
        print(f"== {mode}: {label}")
        print(f"  steps in plans               : {len(g)}")
        print(f"  verbatim from the article    : {verb}/{len(g)} = {verb/n:.1%}")
        print(f"  paraphrase (cos >= {TAU_CLOSE})     : {para}/{len(g)} = {para/n:.1%}")
        print(f"  loose ({TAU_LOOSE} <= cos < {TAU_CLOSE})    : {loose}/{len(g)} = {loose/n:.1%}")
        print(f"  unsupported (cos < {TAU_LOOSE})     : {uns}/{len(g)} = {uns/n:.1%}")
        print("  unsupported at other cut-offs: " + ", ".join(
            f"<{t}: {sum(not v and c < t for _, v, c, _ in g)/n:.1%}" for t in SWEEP))
        print(f"  outputs with a web URL       : {st['url_outputs']}/{len(docs)}")
        print(f"  invalid JSON / truncated     : {st['invalid_json']} / {st['truncated']}")
        if mode == "select":
            print(f"  out-of-range step numbers    : {st['bad_index']} (dropped by code)")
        print(f"  latency P50 / P95            : {pct(st['ms'], 50):.0f} / {pct(st['ms'], 95):.0f} ms")
        print(f"  tokens (mean)                : {statistics.mean(st['pt']):.0f} prompt"
              f" + {statistics.mean(st['ct']):.0f} completion\n")

    print("== every generated step below the loose cut-off, for inspection")
    for s, v, c, t in sorted(graded["generate"], key=lambda x: x[2]):
        if not v and c < TAU_LOOSE:
            print(f"  {c:.2f}  [{t[:40]}]  {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
