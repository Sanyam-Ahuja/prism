"""Appendix C section 5 Baseline: full LLM deeplink mapping.

Measures what happens when the model is trusted with URIs instead of a
deterministic retriever - the design ADR-002 rejects. Two readings of
"full LLM deeplink mapping" are tested, because only measuring the naive one
would be a straw man:

  generate  - the model emits a bixby:// URI itself. Tests the catalog-integrity
              claim in PDF 4.2.2 directly.
  select    - the model is shown candidate catalog entries as text and returns an
              id. This is what a competent team would actually build, and is the
              fair comparison against our hybrid retriever.
"""
import argparse, json, os, re, statistics, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from engine.cold import MODEL, OLLAMA
from engine.deeplink import DUMMY, DeeplinkResolver
from engine.embed import get_encoder
from scripts.bench import pct

# Pooled, as in the cold path: a client per call would charge the Baseline with
# connection setup the shipped design does not pay.
_HTTP = httpx.Client(base_url=OLLAMA, timeout=60.0)

GEN_SCHEMA = {"type": "object", "required": ["deeplink"],
              "properties": {"deeplink": {"type": "string"}}}
SEL_SCHEMA = {"type": "object", "required": ["id"],
              "properties": {"id": {"type": "string"}}}


def ask(prompt, schema, npred=80):
    """Return (parsed JSON, prompt tokens, completion tokens)."""
    try:
        r = _HTTP.post("/api/generate", json={
            "model": MODEL, "prompt": prompt, "format": schema, "stream": False,
            "keep_alive": -1,
            "options": {"temperature": 0, "top_k": 1, "seed": 42, "num_predict": npred}})
        j = r.json()
        return json.loads(j["response"]), j.get("prompt_eval_count", 0), j.get("eval_count", 0)
    except Exception:
        return {}, 0, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["generate", "select"], default="generate")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--labels", default="tests/fixtures/deeplink_labels.json")
    a = ap.parse_args()

    r = DeeplinkResolver("data/deeplinks.json", encoder=get_encoder())
    catalog = {e["deeplink"] for e in r.entries} | {DUMMY}
    by_id = {e["id"]: e for e in r.entries}
    labels = json.load(open(a.labels))

    # A few real URIs so the model knows the surface form it must produce.
    examples = "\n".join(f'  {e["deeplink"]}  = {e["description"][:60]}'
                         for e in r.entries[:4])

    # First-call overhead off the clock, as in eval_deeplinks.py.
    r.candidates("warm up the encoder", k=a.k)
    ask("Return JSON.", GEN_SCHEMA if a.mode == "generate" else SEL_SCHEMA)

    valid = correct = 0
    lat, p_tok, c_tok = [], [], []
    for L in labels:
        d = L["descriptor"]
        t0 = time.perf_counter()
        if a.mode == "generate":
            out, pt, ct = ask(f"""Samsung Settings deeplink catalog uses masked URIs, for example:
{examples}

Return the bixby:// deeplink for this Settings screen: "{d}"
Return JSON only.""", GEN_SCHEMA)
            uri = (out or {}).get("deeplink", "")
            got_id = next((i for i, e in by_id.items() if e["deeplink"] == uri), None)
        else:
            cands = r.candidates(d, k=a.k)
            listing = "\n".join(f'  {e["id"]}: {e["description"][:70]}' for e, _ in cands)
            out, pt, ct = ask(f"""Pick the catalog entry describing this Settings screen: "{d}"

{listing}
  NONE: no entry matches

Return JSON with the chosen id.""", SEL_SCHEMA)
            got_id = (out or {}).get("id", "").strip()
            uri = by_id.get(got_id, {}).get("deeplink", DUMMY if got_id == "NONE" else "")
        lat.append((time.perf_counter() - t0) * 1000)
        p_tok.append(pt); c_tok.append(ct)

        valid += uri in catalog
        expect = L["expect"]
        got = "DUMMY" if uri == DUMMY else (got_id or "?")
        correct += got == expect

    n = len(labels)
    print(f"\nBaseline / {a.mode}   model={MODEL}  n={n}")
    print(f"  catalog integrity (URI in catalog) : {valid}/{n} = {valid/n:.1%}")
    print(f"  precision@1 vs labels              : {correct}/{n} = {correct/n:.1%}")
    print(f"  mean latency                       : {statistics.mean(lat):.0f} ms/descriptor")
    print(f"  P50 / P95 latency                  : {pct(lat, 50):.0f} / {pct(lat, 95):.0f} ms/descriptor")
    print(f"  tokens per descriptor (mean)       : {statistics.mean(p_tok):.0f} prompt"
          f" + {statistics.mean(c_tok):.0f} completion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
