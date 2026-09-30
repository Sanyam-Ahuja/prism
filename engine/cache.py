"""Stage 1: fast-path semantic cache. See docs/ARCHITECTURE.md section 5.

Brute-force cosine over a single float32 matrix. At N~121 (and up to ~50k) a
numpy matmul beats an ANN index and has no recall cliff (ADR-006).
"""
from __future__ import annotations

import json
import os

import numpy as np

from engine.embed import QUERY_PREFIX, get_encoder
from engine.normalize import normalize

# Two-factor acceptance. A single similarity threshold cannot satisfy both the
# >=80% paraphrase hit rate and zero false hits on out-of-domain queries: the
# curves cross (reports/README.md, section C). Out-of-domain queries are weakly
# similar to every plan, so the margin over the best OTHER plan discriminates
# them where raw similarity cannot.
TAU_HIT = float(os.environ.get("PRISM_TAU_HIT", "0.70"))      # floor
TAU_HIGH = float(os.environ.get("PRISM_TAU_HIGH", "0.80"))    # accept regardless of margin
TAU_MARGIN = float(os.environ.get("PRISM_TAU_MARGIN", "0.04"))


class PlanCache:
    def __init__(self, art: str = "artifacts"):
        self.plans = {p["id"]: p for p in
                      json.load(open(f"{art}/plan_library.json"))["plans"]}
        self.vectors = np.load(f"{art}/cache_vectors.npy")
        man = json.load(open(f"{art}/cache_manifest.json"))
        self.plan_ids = np.array([r["plan_id"] for r in man["rows"]], dtype=np.int32)
        self.texts = [r["text"] for r in man["rows"]]
        # Exact-match short circuit on the normalized string.
        self._exact = {}
        for i, t in enumerate(self.texts):
            self._exact.setdefault(normalize(t), int(self.plan_ids[i]))
        self.encoder = get_encoder()

    def lookup(self, query: str, tau: float = TAU_HIT,
               tau_high: float = TAU_HIGH, margin: float = TAU_MARGIN):
        """Return (plan, similarity) on a hit, else (None, best_similarity)."""
        norm = normalize(query)
        if norm in self._exact:
            return self.plans[self._exact[norm]], 1.0
        qv = self.encoder.encode([QUERY_PREFIX + norm],
                                 normalize_embeddings=True).astype(np.float32)[0]
        sims = self.vectors @ qv
        j = int(np.argmax(sims))
        best = float(sims[j])
        pid = int(self.plan_ids[j])
        if best < tau:
            return None, best
        if best >= tau_high:
            return self.plans[pid], best
        other = sims[self.plan_ids != pid]
        gap = best - (float(other.max()) if other.size else 0.0)
        if gap >= margin:
            return self.plans[pid], best
        return None, best
