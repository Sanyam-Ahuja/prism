"""Honest paraphrase hit rate: held-out phrasings, disjoint from every cache seed.

Reports hit rate AND routing correctness. A confident hit on the wrong plan is
worse than a miss, so both are measured.
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.cache import PlanCache
from engine.normalize import normalize


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tau", type=float, default=0.70)
    ap.add_argument("--high", type=float, default=0.80)
    ap.add_argument("--margin", type=float, default=0.04)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    cache = PlanCache()
    seeds = {normalize(t) for t in cache.texts}
    held = json.load(open("tests/fixtures/heldout_paraphrases.json"))

    leaked = [h for h in held if normalize(h["q"]) in seeds]
    if leaked:
        print(f"!! {len(leaked)} held-out items leaked into the cache seeds; test is invalid")
        return 1

    hit = correct = 0
    for h in held:
        plan, sim = cache.lookup(h["q"], tau=a.tau, tau_high=a.high, margin=a.margin)
        if plan is not None:
            hit += 1
            ok = plan["doc"] == h["doc"]
            correct += ok
            if a.verbose and not ok:
                print(f"  WRONG-PLAN sim={sim:.3f} {h['q'][:52]}\n      got={plan['doc'][:50]}\n      exp={h['doc'][:50]}")
        elif a.verbose:
            print(f"  MISS       sim={sim:.3f} {h['q'][:62]}")

    # Negatives: out-of-domain queries that must NOT hit. All compiled plans are
    # Display; a battery question must not confidently return a screen plan.
    negs = json.load(open("tests/fixtures/heldout_negatives.json"))
    false_hits = 0
    for q in negs:
        plan, sim = cache.lookup(q, tau=a.tau, tau_high=a.high, margin=a.margin)
        if plan is not None:
            false_hits += 1
            if a.verbose:
                print(f"  FALSE-HIT  sim={sim:.3f} {q[:56]} -> {plan['doc'][:40]}")

    n = len(held)
    print(f"tau={a.tau} high={a.high} margin={a.margin}  held-out n={n} (0 leaked)  negatives n={len(negs)}")
    print(f"  hit rate          : {hit}/{n} = {hit/n:.1%}   (target >= 80%)")
    print(f"  correct routing   : {correct}/{n} = {correct/n:.1%}")
    print(f"  wrong-plan hits   : {hit-correct}")
    print(f"  FALSE hits on OOD : {false_hits}/{len(negs)} = {false_hits/len(negs):.1%}  (target 0%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
