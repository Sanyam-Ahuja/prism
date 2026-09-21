"""Stage 0: query normalization. See docs/ARCHITECTURE.md section 4.

Maps diverse colloquial phrasings onto one semantic representation so that
paraphrases do not fragment the cache (PDF 7.1).
"""
from __future__ import annotations

import re
import unicodedata

# Device models are almost never plan-relevant and fragment the cache badly.
_DEVICE = re.compile(
    r"\b(?:galaxy\s+)?(?:z\s+)?(?:flip|fold|note|tab)\s*\d*\s*(?:ultra|plus|\+|fe)?\b"
    r"|\b[as]\d{2,3}[a-z]?\d?[a-z]?\s*(?:ultra|plus|\+|fe)?\b"
    r"|\bs\*+\s*(?:ultra|plus)?\b"
    r"|\bgalaxy\b|\bsamsung\b",
    re.I,
)
_LIST_ITEM = re.compile(r"^\s*\d+[.)]\s*")
_FILLER = re.compile(
    r"\b(?:please|kindly|i need help with|help me|can you|could you|i want to|"
    r"i would like to|my|the|a|an)\b", re.I)
_CONTRACTIONS = {
    "can't": "cannot", "won't": "will not", "doesn't": "does not",
    "don't": "do not", "isn't": "is not", "didn't": "did not",
    "it's": "it is", "i'm": "i am", "i've": "i have", "wasn't": "was not",
}


def normalize(query: str) -> str:
    """Deterministic normalization. The cache key is the embedding of this."""
    s = unicodedata.normalize("NFKC", query or "").strip()
    s = s.strip('"“”')
    # input.txt rows 16 and 17 arrive as numbered, quoted multi-part strings.
    parts = [p for p in re.split(r'\s*\d+[.)]\s*"', s) if p.strip()]
    if len(parts) > 1:
        s = " ".join(p.strip().strip('"') for p in parts)
    s = _LIST_ITEM.sub("", s)
    s = s.lower()
    for k, v in _CONTRACTIONS.items():
        s = s.replace(k, v)
    s = _DEVICE.sub(" <device> ", s)
    s = _FILLER.sub(" ", s)
    s = re.sub(r"[^\w\s<>]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()
