"""Second-stage reranking over retrieval candidates.

Motivation, measured: our BM25+dense retrieval puts the correct catalog entry in
the top 5 for 87% of free-form descriptors but ranks it first only 53% of the
time. That 34-point gap is a "choose among K" problem, which is what a reranker
is for.

Two backends behind one interface:

  local  - a CPU cross-encoder. Offline, deterministic, free. This is the one
           that ships: it preserves the no-network, $0.00 runtime guarantee in
           ADR-009.
  jev    - TypeSafe's hosted decision model via OpenRouter. Build-time
           comparison only. Never on the shipped path: it is hosted-only (no
           weights published), and its p95 of 0.4-1.8 s is 30-130x our 13.7 ms
           hot path.

Selected with PRISM_RERANK=local|jev|off (default off, so nothing changes unless
asked for).
"""
from __future__ import annotations

import functools
import json
import os
from typing import Optional, Protocol

MODE = os.environ.get("PRISM_RERANK", "off")
LOCAL_MODEL = os.environ.get("PRISM_RERANK_MODEL", "BAAI/bge-reranker-base")
# Native TypeSafe endpoint by default; OpenRouter is a fallback with a different
# model slug ("~typesafe/jev-latest") and path.
JEV_URL = os.environ.get("PRISM_JEV_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("PRISM_JEV_MODEL", "jev-latest")
OR_URL = "https://openrouter.ai/api/alpha/decisions"
OR_MODEL = "~typesafe/jev-latest"

# Explicit no-match option key for Choice.
NONE_KEY = "none_of_these"


class Reranker(Protocol):
    def rank(self, descriptor: str, candidates: list[dict]) -> list[tuple[int, float]]:
        """Return [(candidate_index, score)] best first. Scores are in [0,1]."""


class LocalReranker:
    """CPU cross-encoder. Deterministic, offline, no cost."""

    name = "local"

    def __init__(self, model: str = LOCAL_MODEL):
        self.model = model

    @functools.cached_property
    def _ce(self):
        import torch
        from sentence_transformers import CrossEncoder
        torch.manual_seed(0)
        m = CrossEncoder(self.model, device="cpu")
        m.model.eval()
        return m

    @staticmethod
    def _doc(e: dict) -> str:
        parts = [e.get("description") or "", e.get("message") or "",
                 e.get("qna_description") or ""]
        return " ".join(p for p in parts if p)

    def rank(self, descriptor: str, candidates: list[dict]) -> list[tuple[int, float]]:
        import torch
        if not candidates:
            return []
        pairs = [(descriptor, self._doc(e)) for e in candidates]
        with torch.inference_mode():
            raw = self._ce.predict(pairs, show_progress_bar=False)
        # Cross-encoder logits are unbounded; squash to [0,1] for a comparable scale.
        scores = [1.0 / (1.0 + pow(2.718281828, -float(s))) for s in raw]
        order = sorted(range(len(candidates)),
                       key=lambda i: (-scores[i], candidates[i]["id"]))
        return [(i, scores[i]) for i in order]


class JevReranker:
    """TypeSafe Jev via OpenRouter. Build-time comparison only.

    One `choice` question over the candidates plus a `noul` for "does any of
    these actually match", which is the calibrated replacement for our
    hand-tuned confidence threshold.
    """

    name = "jev"

    def __init__(self, model: str = None):
        ts = os.environ.get("TYPESAFE_API_KEY")
        if ts:
            self.key, self.url, self.model = ts, JEV_URL, model or JEV_MODEL
        else:
            self.key = os.environ.get("OPENROUTER_API_KEY")
            self.url, self.model = OR_URL, model or OR_MODEL
        self.last_none_prob: Optional[float] = None
        self.last_confidence: Optional[float] = None
        self.chose_none = False
        self.tokens = {"input": 0, "output": 0}
        self.calls = 0

    @staticmethod
    def _key(e: dict, i: int) -> str:
        """Meaningful, unique option key.

        The docs' examples use semantically loaded keys ("billing"), so opaque
        c0/c1 keys throw away signal. Catalog ids are equally opaque, so we
        slugify the message and suffix the index for uniqueness.
        """
        import re
        base = re.sub(r"[^a-z0-9]+", "_", (e.get("message") or "option").lower()).strip("_")
        return f"{base[:40]}__{i}"

    def rank(self, descriptor: str, candidates: list[dict]) -> list[tuple[int, float]]:
        import httpx, time
        if not candidates:
            return []
        if not self.key:
            raise RuntimeError("set TYPESAFE_API_KEY (or OPENROUTER_API_KEY)")

        # Jev caps a Choice at 255 options; our K is far below, which also avoids
        # its documented weakness on large label sets.
        cands = candidates[:30]
        keys = [self._key(e, i) for i, e in enumerate(cands)]
        criteria = {k: LocalReranker._doc(e) for k, e in zip(keys, cands)}
        # The skill is explicit: include a no-match outcome when nothing may fit.
        # A separate presence Noul proved unreliable here (0.55 on a case whose
        # answer was present), and Choice alone cannot say "none of these".
        criteria[NONE_KEY] = ("No option describes this screen; the catalog has no "
                              "entry for it.")

        payload = {
            "model": self.model,
            "state": {"target_screen": descriptor},
            "questions": {
                "best_match": {
                    "type": "choice",
                    "instructions": ("Which Samsung Settings screen description refers to "
                                     "the same screen as `target_screen`? Match the exact "
                                     "screen and the same on/off direction, not a parent menu."),
                    "criteria": criteria,
                },
            },
        }
        for attempt in range(5):
            r = httpx.post(self.url, json=payload, timeout=30.0,
                           headers={"Authorization": f"Bearer {self.key}",
                                    "Content-Type": "application/json"})
            if r.status_code in (429, 529):
                time.sleep(2 ** attempt)
                continue
            break
        r.raise_for_status()
        body = r.json()
        self.calls += 1
        u = body.get("usage") or {}
        self.tokens["input"] += int(u.get("input_tokens", 0))
        self.tokens["output"] += int(u.get("output_tokens", 0))

        ans = body["answers"]["best_match"]
        probs = ans.get("probabilities") or {}
        self.last_confidence = ans.get("confidence")
        self.last_none_prob = float(probs.get(NONE_KEY, 0.0))
        self.chose_none = ans.get("choice") == NONE_KEY

        scored = [(i, float(probs.get(k, 0.0))) for i, k in enumerate(keys)]
        # Deterministic tie-break on catalog id: Jev shows ~25% order sensitivity.
        scored.sort(key=lambda t: (-t[1], cands[t[0]]["id"]))
        return scored


def get_reranker(mode: str = None) -> Optional[Reranker]:
    mode = (mode or MODE or "off").lower()
    if mode == "local":
        return LocalReranker()
    if mode == "jev":
        return JevReranker()
    return None
