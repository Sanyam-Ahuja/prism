"""Measure the cosine separation between correct matches and unrelated pairs.

Thresholds are calibrated from this, not guessed. Run after any encoder change.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from engine.deeplink import DeeplinkResolver
from engine.embed import get_encoder, QUERY_PREFIX

r = DeeplinkResolver("data/deeplinks.json", encoder=get_encoder())
by_id = {e["id"]: i for i, e in enumerate(r.entries)}

pos, neg = [], []
for f in ("tests/fixtures/deeplink_labels.json",
          "tests/fixtures/deeplink_labels_paraphrase.json"):
    for L in json.load(open(f)):
        qv = r.encoder.encode([QUERY_PREFIX + L["descriptor"]], normalize_embeddings=True)[0]
        sims = r._dense @ qv
        if L["expect"] == "DUMMY":
            neg.append(float(sims.max()))          # best available wrong answer
        elif L["expect"] in by_id:
            pos.append(float(sims[by_id[L["expect"]]]))

pos, neg = np.array(pos), np.array(neg)
print(f"correct match cosine : n={len(pos)} min={pos.min():.3f} p10={np.percentile(pos,10):.3f} "
      f"median={np.median(pos):.3f} max={pos.max():.3f}")
print(f"no-valid-entry cosine: n={len(neg)} min={neg.min():.3f} median={np.median(neg):.3f} "
      f"p90={np.percentile(neg,90):.3f} max={neg.max():.3f}")
print(f"\nsuggested COS_FLOOR (p90 of negatives) = {np.percentile(neg,90):.3f}")
print(f"suggested COS_CEIL  (p90 of positives) = {np.percentile(pos,90):.3f}")
