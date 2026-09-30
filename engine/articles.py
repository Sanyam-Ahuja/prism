"""Plans keyed by the reference article they were built from (ADR-019).

When a request supplies siis_response, the plan must derive from that text
(PDF 4.2.3), so the article decides before the query does:

  - an article the plan library was compiled from gets its compiled plan;
  - an article the cold path has already planned gets that same plan back, so
    identical input gives identical output (PDF 6.1). The model server's
    prompt cache made 4 of 11 repeats differ (reports/determinism.txt);
  - only an unseen article reaches the model.

A query that merely resembles a cached plan never overrides the article: a
battery complaint sent with a battery article must not get a Display plan.
"""
from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict

_SPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9]+")
# Token-set overlap above which a supplied article is the library's article
# reformatted (whitespace, punctuation, a stray line) rather than a new one.
NEAR_DUPLICATE = 0.9


def article_text(siis) -> str:
    """The article body, whether siis_response arrived as a string or an object."""
    if isinstance(siis, dict):
        return str(siis.get("content") or "")
    return str(siis or "")


def article_key(text: str) -> str:
    return hashlib.sha256(_SPACE.sub(" ", text).strip().encode("utf-8")).hexdigest()


def _tokens(text: str) -> frozenset:
    return frozenset(_TOKEN.findall(text.lower()))


class ArticlePlans:
    def __init__(self, plans: dict, rows: list[dict], max_memo: int = 256):
        """plans: PlanCache.plans. rows: the responses in data/siis_responses.json."""
        by_doc = {p["doc"]: p for p in plans.values()}
        self._compiled: dict[str, dict] = {}
        self._compiled_tokens: list[tuple[frozenset, dict]] = []
        for r in rows:
            s = r["siis_response"]
            plan = by_doc.get(s.get("title"))
            key = article_key(s.get("content") or "")
            if plan is not None and key not in self._compiled:
                self._compiled[key] = plan
                self._compiled_tokens.append((_tokens(s["content"]), plan))
        self._memo: OrderedDict[str, list] = OrderedDict()
        self._max = max_memo
        self._lock = threading.Lock()

    def compiled(self, text: str) -> dict | None:
        """The library plan compiled from this article, as sent or reformatted."""
        plan = self._compiled.get(article_key(text))
        if plan is not None:
            return plan
        toks = _tokens(text)
        for known, plan in self._compiled_tokens:
            if toks and len(toks & known) / len(toks | known) >= NEAR_DUPLICATE:
                return plan
        return None

    def recall(self, text: str) -> list | None:
        """The contexts the cold path produced for this exact article, if any."""
        key = article_key(text)
        with self._lock:
            contexts = self._memo.get(key)
            if contexts is not None:
                self._memo.move_to_end(key)
            return contexts

    def remember(self, text: str, contexts: list) -> list:
        """Keep the first validated plan for an article and return the kept one.

        First writer wins: when two requests for a new article race, both get
        whichever plan was stored first, never two different plans.
        """
        key = article_key(text)
        with self._lock:
            kept = self._memo.setdefault(key, contexts)
            self._memo.move_to_end(key)
            while len(self._memo) > self._max:
                self._memo.popitem(last=False)
            return kept
