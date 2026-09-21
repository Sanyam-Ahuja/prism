"""Score the deeplink resolver against the hand-labelled regression set.

Reports precision@1 split by catalog-match vs intended-dummy, so a resolver that
games the score by answering DUMMY everywhere is visibly caught.
"""
import argparse, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.deeplink import DeeplinkResolver, DUMMY


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tau", type=float, default=0.45)
    ap.add_argument("--dense", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--labels", default="tests/fixtures/deeplink_labels.json")
    ap.add_argument("--rerank", default="off", choices=["off", "local", "jev"])
    ap.add_argument("--k", type=int, default=8, help="candidates passed to the reranker")
    ap.add_argument("--min-conf", dest="min_conf", type=float, default=0.0,
                    help="abstain below this Choice confidence (docs suggest 0.5)")
    a = ap.parse_args()

    enc = None
    if a.dense:
        from engine.embed import get_encoder
        enc = get_encoder()

    r = DeeplinkResolver("data/deeplinks.json", encoder=enc)

    rr = None
    if a.rerank != "off":
        from engine.rerank import get_reranker
        rr = get_reranker(a.rerank)
    labels = json.load(open(a.labels))

    hits = misses = 0
    dummy_ok = dummy_bad = 0
    rows = []
    for L in labels:
        if rr is None:
            m = r.resolve(L["descriptor"], tau=a.tau)
            got = "DUMMY" if (m.entry is None or m.entry["deeplink"] == DUMMY) else m.entry["id"]
            score = m.score
        else:
            cands = r.candidates(L["descriptor"], k=a.k)
            if not cands:
                got, score = "DUMMY", 0.0
            else:
                entries = [e for e, _ in cands]
                ranked = rr.rank(L["descriptor"], entries)
                bi, score = ranked[0]
                # Abstain per the docs' confidence bands, or when the model
                # explicitly picked the no-match option.
                conf = getattr(rr, "last_confidence", None)
                abstain = getattr(rr, "chose_none", False) or \
                    (conf is not None and conf < a.min_conf) or score < a.tau
                got = "DUMMY" if abstain else entries[bi]["id"]
        m = type("M", (), {"score": score})()
        ok = got == L["expect"]
        if L["expect"] == "DUMMY":
            dummy_ok += ok; dummy_bad += (not ok)
        else:
            hits += ok; misses += (not ok)
        rows.append((ok, L["descriptor"], L["expect"], got, m.score))

    if a.verbose:
        for ok, d, exp, got, sc in rows:
            if not ok or a.verbose:
                print(f"  {'OK ' if ok else 'BAD'} {d[:46]:46s} exp={exp:9s} got={got:9s} {sc:.4f}")

    n_cat = hits + misses
    n_dum = dummy_ok + dummy_bad
    print(f"\ntau={a.tau}  dense={a.dense}")
    print(f"  catalog-match precision@1 : {hits}/{n_cat} = {hits/max(n_cat,1):.1%}")
    print(f"  intended-dummy correct    : {dummy_ok}/{n_dum} = {dummy_ok/max(n_dum,1):.1%}")
    print(f"  OVERALL                   : {(hits+dummy_ok)}/{len(labels)} = {(hits+dummy_ok)/len(labels):.1%}")


if __name__ == "__main__":
    main()
