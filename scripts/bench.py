"""Latency percentiles per execution path (PDF Appendix C section 3, N >= 30)."""
import argparse, json, os, statistics, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.cache import PlanCache


def pct(xs, p):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--cold", type=int, default=0, help="also bench N cold queries")
    a = ap.parse_args()

    cache = PlanCache()
    exact = [l.strip() for l in open("data/input.txt") if l.strip()]
    para = [h["q"] for h in json.load(open("tests/fixtures/heldout_paraphrases.json"))]

    # Warm the encoder so the first call's lazy init is not charged to path timing.
    cache.lookup("warmup query for encoder initialization")

    print(f"{'path':44s} {'n':>4s} {'P50':>8s} {'P95':>8s} {'target':>9s}  verdict")
    print("-" * 86)
    for name, qs, target in [
        ("Cache hit - exact query match", exact, 300),
        ("Cache hit - unseen semantic paraphrase", para, 300),
    ]:
        lat = []
        for i in range(a.n):
            q = qs[i % len(qs)]
            t0 = time.perf_counter()
            cache.lookup(q)
            lat.append((time.perf_counter() - t0) * 1000)
        p50, p95 = pct(lat, 50), pct(lat, 95)
        ok = "PASS" if p95 <= target else "FAIL"
        print(f"{name:44s} {len(lat):4d} {p50:7.1f}ms {p95:7.1f}ms {target:8d}ms  {ok}")

    if a.cold:
        from engine.cold import ColdPath
        cold = ColdPath(cache.encoder)
        rows = json.load(open("data/siis_responses.json"))["responses"]
        docs, seen = [], set()
        for r in rows:
            t = r["siis_response"]["title"]
            if t not in seen:
                seen.add(t); docs.append(r["siis_response"])
        cold.run("warmup", docs[0])          # pay the model load off the clock
        lat, deeplinked, total = [], 0, 0
        for i in range(max(a.cold, len(docs))):
            d = docs[i % len(docs)]
            t0 = time.perf_counter()
            r = cold.run("the screen is not behaving correctly", d)
            lat.append((time.perf_counter() - t0) * 1000)
            for ctxs in (r or {}).get("contexts", []):
                for act in ctxs["actions"]:
                    if act["category"] == "auto":
                        total += 1
                        if any((g.get("actionableDeeplink") or {}).get("deeplink", "").startswith("bixby://masked")
                               for g in act["stepGroups"]):
                            deeplinked += 1
        p50, p95 = pct(lat, 50), pct(lat, 95)
        ok = "PASS" if p95 <= 8000 else "FAIL"
        print(f"{'Cold query - full pipeline':44s} {len(lat):4d} {p50:7.1f}ms {p95:7.1f}ms {8000:8d}ms  {ok}")
        print(f"\ncold-path auto actions with a catalog deeplink: {deeplinked}/{total}"
              f" = {100*deeplinked/max(total,1):.0f}%")


if __name__ == "__main__":
    raise SystemExit(main())
