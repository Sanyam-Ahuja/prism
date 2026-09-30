"""Honest paraphrase hit rate: held-out phrasings, disjoint from every cache seed.

Reports hit rate AND routing correctness. A confident hit on the wrong plan is
worse than a miss, so both are measured.

Two sets:
  calibration  tests/fixtures/heldout_*.json - 26 paraphrases and 12 out-of-scope
               complaints, used to choose the thresholds and the anchor rule
  test         tests/fixtures/test_*.json - 27 paraphrases and 38 out-of-scope
               complaints written afterwards and never used to tune anything
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.cache import TAU_OOD, PlanCache
from engine.normalize import normalize

SETS = {
    "calibration": ("tests/fixtures/heldout_paraphrases.json", "tests/fixtures/heldout_negatives.json"),
    "test": ("tests/fixtures/test_paraphrases.json", "tests/fixtures/test_negatives.json"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=sorted(SETS), default="calibration")
    ap.add_argument("--tau", type=float, default=0.70)
    ap.add_argument("--high", type=float, default=0.80)
    ap.add_argument("--margin", type=float, default=0.04)
    ap.add_argument("--no-ood", action="store_true",
                    help="switch the out-of-scope anchors off (ablation)")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    ood = None if a.no_ood else TAU_OOD

    cache = PlanCache()
    seeds = {normalize(t) for t in cache.texts}
    pos_path, neg_path = SETS[a.set]
    held = json.load(open(pos_path))

    leaked = [h for h in held if normalize(h["q"]) in seeds]
    if leaked:
        print(f"!! {len(leaked)} held-out items leaked into the cache seeds; test is invalid")
        return 1

    hit = correct = multi = 0
    for h in held:
        plan, sim = cache.lookup(h["q"], tau=a.tau, tau_high=a.high, margin=a.margin, ood=ood)
        if plan is not None:
            hit += 1
            ok = plan["doc"] == h["doc"]
            correct += ok
            if a.verbose and not ok:
                print(f"  WRONG-PLAN sim={sim:.3f} {h['q'][:52]}\n      got={plan['doc'][:50]}\n      exp={h['doc'][:50]}")
        elif a.verbose:
            print(f"  MISS       sim={sim:.3f} {h['q'][:62]}")
        # One problem per query here: the multi-plan path must not add a second.
        if not a.no_ood and len(cache.lookup_all(h["q"])) > 1:
            multi += 1
            if a.verbose:
                print(f"  EXTRA-PLAN {h['q'][:62]}")

    # Negatives: out-of-domain queries that must NOT hit. All compiled plans are
    # Display; a battery question must not confidently return a screen plan.
    negs = json.load(open(neg_path))
    false_hits = 0
    for q in negs:
        plan, sim = cache.lookup(q, tau=a.tau, tau_high=a.high, margin=a.margin, ood=ood)
        if plan is not None:
            false_hits += 1
            if a.verbose:
                print(f"  FALSE-HIT  sim={sim:.3f} {q[:56]} -> {plan['doc'][:40]}")

    n = len(held)
    print(f"set={a.set}  tau={a.tau} high={a.high} margin={a.margin}  "
          f"out-of-scope anchors={'off' if a.no_ood else 'on'}  "
          f"held-out n={n} (0 leaked)  negatives n={len(negs)}")
    print(f"  hit rate          : {hit}/{n} = {hit/n:.1%}   (target >= 80%)")
    print(f"  correct routing   : {correct}/{n} = {correct/n:.1%}")
    print(f"  wrong-plan hits   : {hit-correct}")
    print(f"  FALSE hits on OOD : {false_hits}/{len(negs)} = {false_hits/len(negs):.1%}  (target 0%)")
    if not a.no_ood:
        print(f"  extra plans on single-problem queries: {multi}/{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
