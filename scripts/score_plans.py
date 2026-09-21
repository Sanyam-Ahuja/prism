"""Compute the PDF Appendix C section 2 judged scores. See docs/RUBRIC.md."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.deeplink import DUMMY
from engine.segment import segment
from validators.gates import blocking, load_ctx, validate_envelope

CTX = load_ctx("data/deeplinks.json")
RANK = {"auto": 0, "manual": 1, "critical": 2}


def step_accuracy(plan, seg):
    cands = [c.text for c in seg.candidates]
    used = [s for a in plan["actions"] for g in a["stepGroups"] for s in g["steps"]]

    # Completeness: full marks at >=60% coverage (see RUBRIC.md).
    target = max(1.0, 0.6 * len(cands))
    completeness = min(1.0, len(set(used)) / target)

    traceable = sum(1 for s in used if s in cands) / max(len(used), 1)
    imperative = sum(1 for s in used if s and s[0].isupper() and s.endswith(("." , "!", "?"))) / max(len(used), 1)
    env = {"query": "", "query_variations": [], "response": {"contexts": [plan]}}
    gates_ok = 1.0 if not blocking(validate_envelope(env, CTX)) else 0.0
    correctness = (traceable + imperative + gates_ok) / 3

    ranks = [RANK.get(a["category"], 1) for a in plan["actions"]]
    cat_ok = 1.0 if ranks == sorted(ranks) else 0.0
    order_ok, groups = 0, 0
    for a in plan["actions"]:
        for g in a["stepGroups"]:
            idx = [cands.index(s) for s in g["steps"] if s in cands]
            groups += 1
            order_ok += 1 if idx == sorted(idx) else 0
    ordering = (cat_ok + (order_ok / max(groups, 1))) / 2
    return completeness + correctness + ordering, completeness, correctness, ordering


def main():
    lib = json.load(open("artifacts/plan_library.json"))["plans"]
    rows = json.load(open("data/siis_responses.json"))["responses"]
    docs = {}
    for r in rows:
        docs.setdefault(r["siis_response"]["title"], r["siis_response"]["content"])
    truth = {t["action"]: t for t in
             json.load(open("tests/fixtures/plan_deeplink_truth.json"))["labels"]}

    print(f"{'plan':42s} {'step acc':>9s}  {'compl':>6s}{'corr':>6s}{'order':>6s}")
    print("-" * 74)
    total = 0.0
    for p in lib:
        seg = segment(docs[p["doc"]], p["doc"])
        s, c, co, o = step_accuracy(p["plan"], seg)
        total += s
        print(f"{p['doc'][:40]:42s} {s:6.2f}/3  {c:6.2f}{co:6.2f}{o:6.2f}")
    mean_step = total / len(lib)

    scored, excluded, dl_total = 0, 0, 0.0
    for p in lib:
        for a in p["plan"]["actions"]:
            if a["category"] != "auto":
                continue
            t = truth.get(a["actionName"])
            if t is None:
                continue
            if t["score"] is None:
                excluded += 1
            else:
                dl_total += t["score"]; scored += 1
    mean_dl = dl_total / max(scored, 1)

    print("-" * 74)
    print(f"\nSTEP ACCURACY     {mean_step:.2f} / 3.0   (mean over {len(lib)} plans)")
    print(f"DEEPLINK RELEVANCE {mean_dl:.2f} / 2.0   (mean over {scored} scored auto actions)")
    print(f"  sanctioned dummy_positive excluded: {excluded}"
          f"  ({100*excluded/(scored+excluded):.0f}% of auto actions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
