"""Is the cold-path extraction deterministic? (PDF §6.1)

Compares a hash of the raw skeleton the extractor returns, three ways:
  1. fresh vs cache-warm: every document on a freshly loaded model, then every
     document again in the same process, when the server's prompt cache already
     holds each exact prompt;
  2. back-to-back: one document four times in a row;
  3. interleaved: the same document after each of five different documents.

    PRISM_EXTRACT_MODEL=qwen2.5:1.5b python scripts/determinism_probe.py
"""
import hashlib, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from engine.cold import OLLAMA, ColdPath
from engine.segment import segment


def main():
    cold = ColdPath(None)
    docs, seen = [], set()
    for r in json.load(open("data/siis_responses.json"))["responses"]:
        if r["siis_response"]["title"] not in seen:
            seen.add(r["siis_response"]["title"]); docs.append(r["siis_response"])

    def skeleton_hash(doc):
        sk, _, _ = cold._extract(segment(doc["content"], doc["title"]))
        return hashlib.sha1(json.dumps(sk, sort_keys=True).encode()).hexdigest()[:10] if sk else "NONE"

    # Fresh process: unload, then load with an empty prompt (caches nothing).
    httpx.post(f"{OLLAMA}/api/generate", json={"model": cold.model, "keep_alive": 0}, timeout=60)
    httpx.post(f"{OLLAMA}/api/generate", json={"model": cold.model, "keep_alive": -1}, timeout=300)
    fresh = [skeleton_hash(d) for d in docs]
    warm = [skeleton_hash(d) for d in docs]

    a = docs[1]
    back = [skeleton_hash(a) for _ in range(4)]
    inter = []
    for other in docs[2:7]:
        skeleton_hash(other)
        inter.append(skeleton_hash(a))

    print(f"model={cold.model}")
    print(f"{'document':42s} {'fresh':>10s} {'cache-warm':>10s}")
    for d, f, w in zip(docs, fresh, warm):
        print(f"{d['title'][:40]:42s} {f:>10s} {w:>10s}{'' if f == w else '   DIFFERS'}")
    same = sum(f == w for f, w in zip(fresh, warm))
    print(f"\nfresh vs cache-warm : {same}/{len(docs)} documents identical")
    print(f"back-to-back        : {len(set(back))} distinct of {len(back)}  ({a['title'][:40]})")
    print(f"interleaved         : {len(set(inter))} distinct of {len(inter)}  (after 5 different documents)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
