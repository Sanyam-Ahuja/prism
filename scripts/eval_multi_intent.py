"""Multi-intent complaints on the hot path (PDF Appendix C section 6, ADR-021).

Each complaint is looked up exactly as the API does (PlanCache.lookup_all),
which returns one plan per separate problem it can match:

  known + known        every ordered pair of supplied queries whose source
                       documents differ, joined into one complaint
  paraphrase pairs     the same with the held-out test paraphrases, which are
                       not cache seeds, so every half must match semantically
  known + unknown      every supplied query plus a problem no document covers
  PDF examples         the three complaints quoted in PDF section 1
"""
import collections, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.cache import PlanCache

# Problems outside every supplied SIIS document (Battery, Performance, Camera).
UNKNOWN = [
    "the battery drains really fast",
    "the phone has become slow since the last update",
    "photos from the camera come out blurry",
]
PDF_EXAMPLES = [
    "Screen flickers and the battery dies fast",
    "My phone got slow after the update",
    "Swipe gestures go the wrong way after installing an app",
]


def join(a, b):
    return f"{a} Also, {b[0].lower()}{b[1:]}"


def pairs(cache, items, label):
    # A half the cache already misroutes on its own will misroute when paired
    # too; counting those separately shows what the pairing itself costs.
    alone = {q: [p["doc"] for p, _ in cache.lookup_all(q)] == [d] for q, d in items}
    out, clean, clean_both = collections.Counter(), 0, 0
    for q1, d1 in items:
        for q2, d2 in items:
            if d1 == d2:
                continue
            got = [p["doc"] for p, _ in cache.lookup_all(join(q1, q2))]
            wrong = [g for g in got if g not in (d1, d2)]
            key = ("wrong" if wrong else "both" if d1 in got and d2 in got else
                   "first" if d1 in got else "second" if d2 in got else "miss")
            out[key] += 1
            if alone[q1] and alone[q2]:
                clean += 1
                clean_both += key == "both"
    n = sum(out.values())
    print(f"{label}: {n} complaints (ordered pairs, different documents)")
    for key, text in [("both", "both problems answered"),
                      ("first", "only the first-mentioned problem"),
                      ("second", "only the second-mentioned problem"),
                      ("wrong", "includes a plan for neither problem"),
                      ("miss", "no plan -> no_siis_context fallback")]:
        print(f"  {text:40s}: {out[key]:4d} = {out[key] / n:.1%}")
    print(f"  {'both, when each half routes right alone':40s}: {clean_both:4d} / {clean} = "
          f"{clean_both / max(clean, 1):.1%}")
    print()


def main():
    cache = PlanCache()
    rows = json.load(open("data/siis_responses.json"))["responses"]
    known = [(re.sub(r"^\d+\.\s*", "", r["original_query"]).strip(), r["siis_response"]["title"])
             for r in rows]
    test = [(h["q"], h["doc"]) for h in json.load(open("tests/fixtures/test_paraphrases.json"))]
    cache.lookup("warmup query for encoder initialization")

    # Each half alone must reach its own plan, or the pairs would measure the
    # cache's single-problem misses instead.
    alone = sum([p["doc"] for p, _ in cache.lookup_all(q)] == [d] for q, d in known)
    print(f"supplied queries alone reaching exactly their own plan: {alone}/{len(known)}")
    alone = sum([p["doc"] for p, _ in cache.lookup_all(q)] == [d] for q, d in test)
    print(f"test paraphrases alone reaching exactly their own plan: {alone}/{len(test)}\n")

    pairs(cache, known, "known + known")
    pairs(cache, test, "paraphrase pairs")

    out = collections.Counter()
    for q, d in known:
        for u in UNKNOWN:
            got = [p["doc"] for p, _ in cache.lookup_all(join(q, u))]
            out["own" if got == [d] else "miss" if not got else "wrong"] += 1
    n = sum(out.values())
    print(f"known + unknown: {n} complaints (each supplied query + {len(UNKNOWN)} uncovered problems)")
    for key, text in [("own", "the known plan only, other dropped"),
                      ("wrong", "a plan for neither, or an extra one"),
                      ("miss", "no plan -> no_siis_context fallback")]:
        print(f"  {text:40s}: {out[key]:4d} = {out[key] / n:.1%}")

    print("\nPDF section 1 examples:")
    for q in PDF_EXAMPLES:
        got = cache.lookup_all(q)
        verdict = " + ".join(f"{p['doc'][:44]} ({s:.3f})" for p, s in got) or "no plan -> no_siis_context"
        print(f"  {q:58s} {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
