import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.segment import segment

rows = json.load(open("data/siis_responses.json"))["responses"]
docs, qs = {}, {}
for r in rows:
    t = r["siis_response"]["title"]
    docs.setdefault(t, r["siis_response"]["content"])
    qs.setdefault(t, []).append(r["original_query"])
for i, (t, c) in enumerate(docs.items(), 1):
    s = segment(c, t)
    print(f"\n{'='*78}\nDOC {i}: {t}\n  queries routed here: {len(qs[t])}")
    for q in qs[t][:2]:
        print(f"    - {q[:100]}")
    print(f"{'='*78}")
    print(s.numbered())
