"""Stage 1: fast-path semantic cache. See docs/ARCHITECTURE.md section 5.

Brute-force cosine over a single float32 matrix. At N~121 (and up to ~50k) a
numpy matmul beats an ANN index and has no recall cliff (ADR-006).
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

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
# Out of scope when an anchor is at least this close relative to the best plan
# (ADR-020). 0 means plain nearest neighbour between plans and anchors.
TAU_OOD = float(os.environ.get("PRISM_TAU_OOD", "0.0"))
# Plans returned for one complaint that names several problems (ADR-021).
MAX_PLANS = int(os.environ.get("PRISM_MAX_PLANS", "3"))

# Markers that start a second, separate problem. A bare "and" is not one:
# "flickers and goes black" is a single problem, and the supplied complaints
# join the symptoms of one problem with "and" throughout.
_CLAUSE_SPLIT = re.compile(
    r"(?<=[.!?;])\s+"
    r"|,?\s+(?:and\s+)?(?:also|plus|as well as|additionally|in addition|on top of that|besides that)\b,?\s+"
    r"|,\s+and\s+(?=(?:the|my|its)\b)",
    re.I,
)
_LEADING_MARKER = re.compile(r"^(?:and\s+)?(?:also|plus|additionally|besides)\b,?\s*", re.I)
MIN_CLAUSE_WORDS = 3


def clauses(query: str) -> list[str]:
    """The separate problems a complaint names, in the order it names them."""
    parts = (_LEADING_MARKER.sub("", p.strip(" ,;")) for p in _CLAUSE_SPLIT.split(query or ""))
    return [p for p in parts if len(p.split()) >= MIN_CLAUSE_WORDS]


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
        # Complaints no plan covers (ADR-020): rows that map to "no plan".
        ood = f"{art}/ood_vectors.npy"
        self.ood = (np.load(ood) if os.path.exists(ood)
                    else np.zeros((0, self.vectors.shape[1]), np.float32))
        self.encoder = get_encoder()

    def _encode(self, texts: list[str]) -> np.ndarray:
        return self.encoder.encode([QUERY_PREFIX + t for t in texts],
                                   normalize_embeddings=True).astype(np.float32)

    def decide(self, qv, tau: float = TAU_HIT, tau_high: float = TAU_HIGH,
               margin: float = TAU_MARGIN, ood: Optional[float] = TAU_OOD):
        """(plan or None, best plan similarity, reason) for one query vector.

        ood=None switches the out-of-scope check off, for ablation only.
        """
        sims = self.vectors @ qv
        j = int(np.argmax(sims))
        best = float(sims[j])
        pid = int(self.plan_ids[j])
        if best < tau:
            return None, best, "below floor"
        # Nearer to a complaint no plan covers than to any plan's seeds: out of
        # scope, however high the raw similarity. "My phone got slow after the
        # update" scored 0.725 against the touchscreen plan.
        if ood is not None and self.ood.size and float((self.ood @ qv).max()) >= best - ood:
            return None, best, "out of scope"
        if best >= tau_high:
            return self.plans[pid], best, "high"
        other = sims[self.plan_ids != pid]
        gap = best - (float(other.max()) if other.size else 0.0)
        if gap >= margin:
            return self.plans[pid], best, "margin"
        return None, best, "ambiguous"

    def lookup(self, query: str, tau: float = TAU_HIT, tau_high: float = TAU_HIGH,
               margin: float = TAU_MARGIN, ood: Optional[float] = TAU_OOD):
        """Return (plan, similarity) on a hit, else (None, best_similarity)."""
        norm = normalize(query)
        if norm in self._exact:
            return self.plans[self._exact[norm]], 1.0
        plan, sim, _ = self.decide(self._encode([norm])[0], tau, tau_high, margin, ood)
        return plan, sim

    def lookup_all(self, query: str, max_plans: int = MAX_PLANS) -> list[tuple[dict, float]]:
        """One plan per problem the complaint names, first-mentioned first (ADR-021).

        The whole complaint is looked up as before; each separate clause is also
        looked up, all in one batched encode. A clause adds a plan only by
        passing the same acceptance rules as a whole query, including the
        out-of-scope check, so "...and the battery dies fast" adds nothing.
        """
        norm = normalize(query)
        if norm in self._exact:
            return [(self.plans[self._exact[norm]], 1.0)]
        parts = [normalize(c) for c in clauses(query)]
        parts = [p for p in parts if p and p != norm] if len(parts) > 1 else []
        vecs = self._encode([norm] + parts)

        found: list[tuple[dict, float]] = []
        for p, v in zip(parts, vecs[1:]):
            plan, sim = ((self.plans[self._exact[p]], 1.0) if p in self._exact
                         else self.decide(v)[:2])
            if plan is not None and all(plan["id"] != q["id"] for q, _ in found):
                found.append((plan, sim))
        whole, sim, _ = self.decide(vecs[0])
        if whole is not None and all(whole["id"] != q["id"] for q, _ in found):
            found.insert(0, (whole, sim))
        return found[:max_plans]
