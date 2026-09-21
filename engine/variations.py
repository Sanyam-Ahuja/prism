"""Deterministic query paraphrase generation (PDF 4.1: 8-10, varied register).

Used on the cold path. Generating paraphrases with the LLM would roughly double
the output token budget, and ADR-001 exists precisely to keep that budget small;
variations mainly serve as cache seeds (ADR-004), so deterministic templates
are sufficient and are reproducible for free.
"""
from __future__ import annotations

import re

from engine.normalize import normalize

_STRIP_LEAD = re.compile(r"^(?:my|the|a|an)\s+", re.I)


def _core(query: str) -> str:
    s = re.sub(r"\s+", " ", (query or "").strip().rstrip(".!?"))
    return _STRIP_LEAD.sub("", s)


def _keywords(query: str, n: int = 7) -> str:
    toks = [t for t in normalize(query).split() if t != "<device>"]
    seen, out = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t); out.append(t)
    return " ".join(out[:n])


def _typo(text: str) -> str:
    """One deterministic transposition in the longest word."""
    words = text.split()
    if not words:
        return text
    i = max(range(len(words)), key=lambda k: len(words[k]))
    w = words[i]
    if len(w) >= 5:
        j = len(w) // 2
        words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:]
    return " ".join(words)


def generate(query: str, n: int = 10) -> list[str]:
    """8-10 distinct paraphrases across formal, casual, keyword and typo registers."""
    c = _core(query)
    lower = c[:1].lower() + c[1:] if c else c
    kw = _keywords(query)
    cands = [
        c if c.endswith((".", "?")) else c + ".",
        f"Why does {lower}?",
        kw,
        f"I am having an issue where {lower}.",
        f"{c[:1].upper() + c[1:]} and I need help resolving it.",
        f"how do i fix it when {lower}?",
        f"This is really frustrating - {lower} and nothing works.",
        f"Issue reported: {lower}.",
        _typo(lower) + ".",
        f"What should I do if {lower}?",
    ]
    out, seen = [], set()
    for x in cands:
        x = re.sub(r"\s{2,}", " ", x).strip()
        if x and x.lower() not in seen:
            seen.add(x.lower()); out.append(x)
        if len(out) >= n:
            break
    while len(out) < 8:
        out.append(f"{kw} issue {len(out)}")
    return out[:n]
