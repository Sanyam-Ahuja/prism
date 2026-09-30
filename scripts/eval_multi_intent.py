"""Multi-intent complaints on the hot path (PDF Appendix C section 6).

A response carries one plan, so a complaint naming two problems is answered for
one of them at best. This measures what actually happens, looking each
complaint up exactly as the API does:

  known + known    every ordered pair of supplied queries whose source documents
                   differ, joined into one complaint
  known + unknown  every supplied query plus a problem no document covers
  PDF examples     the three complaints quoted in PDF section 1
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


def main():
    cache = PlanCache()
    rows = json.load(open("data/siis_responses.json"))["responses"]
    known = [(re.sub(r"^\d+\.\s*", "", r["original_query"]).strip(), r["siis_response"]["title"])
             for r in rows]
    cache.lookup("warmup query for encoder initialization")

    def doc(q):
        plan, _ = cache.lookup(q)
        return plan["doc"] if plan else None

    # Each supplied query alone must reach its own plan, or the pairs below
    # would measure the cache's single-intent misses instead.
    alone = sum(doc(q) == d for q, d in known)
    print(f"supplied queries alone reaching their own plan: {alone}/{len(known)}\n")

    out = collections.Counter()
    for q1, d1 in known:
        for q2, d2 in known:
            if d1 == d2:
                continue
            got = doc(join(q1, q2))
            out["first" if got == d1 else "second" if got == d2 else
                "miss" if got is None else "other"] += 1
    n = sum(out.values())
    print(f"known + known: {n} complaints (ordered pairs, different documents)")
    for key, label in [("first", "plan for the first-mentioned problem"),
                       ("second", "plan for the second-mentioned problem"),
                       ("other", "plan for neither (a third document)"),
                       ("miss", "cache miss -> no_siis_context fallback")]:
        print(f"  {label:40s}: {out[key]:4d} = {out[key] / n:.1%}")
    print(f"  {'both problems answered':40s}:    0 (a response holds one plan)\n")

    out = collections.Counter()
    for q, d in known:
        for u in UNKNOWN:
            got = doc(join(q, u))
            out["own" if got == d else "miss" if got is None else "other"] += 1
    n = sum(out.values())
    print(f"known + unknown: {n} complaints (each supplied query + {len(UNKNOWN)} uncovered problems)")
    for key, label in [("own", "plan for the known problem, other dropped"),
                       ("other", "plan for a different document"),
                       ("miss", "cache miss -> no_siis_context fallback")]:
        print(f"  {label:40s}: {out[key]:4d} = {out[key] / n:.1%}")

    print("\nPDF section 1 examples:")
    for q in PDF_EXAMPLES:
        plan, sim = cache.lookup(q)
        verdict = f"hit  {plan['doc']}" if plan else "miss -> no_siis_context"
        print(f"  {q:58s} sim={sim:.3f}  {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
