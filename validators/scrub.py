"""URL scrubbing. Gate G0 (PDF 4.2.1: absolute prohibition of web URLs).

Runs first so later gates see clean text, and again last so repairs cannot
reintroduce a URL.

Strategy is sentence-aware: a sentence that exists to point at a link is dropped
whole. Stripping the URL inline instead leaves dangling fragments such as
"You can find instructions at", which are not valid UI steps.
"""
import re

_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\(\s*[^)]*\s*\)")
_SCHEME_URL = re.compile(r"\b(?:https?|ftp)://[^\s<>\"')\]]+", re.I)
_WWW_URL = re.compile(r"\bwww\.[^\s<>\"')\]]+", re.I)
_BARE_DOMAIN = re.compile(
    r"\b(?:[a-z0-9-]+\.)*(?:samsung|google|microsoft|apple)\.(?:com|net|org|co\.[a-z]{2})"
    r"(?:/[^\s<>\"')\]]*)?",
    re.I,
)
# Referral phrasing left behind by the SIIS source text once its link is gone.
_REFERRAL = re.compile(
    r"^\s*(?:you can (?:find|learn|read)|visit|see|refer to|learn more|check out|"
    r"for more information|more information|instructions (?:are|can be) found)\b",
    re.I,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_BIXBY_OK = re.compile(r"^bixby://", re.I)


def _strip_urls_inline(text: str) -> str:
    out = _MARKDOWN_LINK.sub(r"\1", text)
    out = _SCHEME_URL.sub("", out)
    out = _WWW_URL.sub("", out)
    out = _BARE_DOMAIN.sub("", out)
    return out


def _tidy(text: str) -> str:
    """Close the gaps a removed URL leaves: stray spaces, empty brackets."""
    out = re.sub(r"\s+([.,;:!?])", r"\1", text)
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


def has_url(text: str) -> bool:
    """True if any prohibited web URL form is present."""
    if not text:
        return False
    return bool(
        _SCHEME_URL.search(text)
        or _WWW_URL.search(text)
        or _MARKDOWN_LINK.search(text)
        or _BARE_DOMAIN.search(text)
    )


def scrub(text: str) -> str:
    """Remove every prohibited URL form, preserving readable prose.

    A sentence carrying a URL, or opening with referral phrasing, is dropped
    entirely. Any stray URL in surviving text is removed inline as a backstop.
    """
    if not text:
        return text

    sentences = _SENTENCE_SPLIT.split(text.strip())
    kept = []
    for s in sentences:
        if not s.strip():
            continue
        if has_url(s) or _REFERRAL.match(s):
            continue
        kept.append(s.strip())

    out = " ".join(kept)
    # Backstop: single-sentence input that was all referral, or a URL the split missed.
    if has_url(out):
        out = _strip_urls_inline(out)
    return _tidy(out)


def strip_urls(text: str) -> str:
    """Remove only the URLs from text the user wrote, keeping every other word.

    For the complaint, which the response echoes back: dropping the whole
    sentence around a link, as scrub() does, would discard the problem
    description itself. Text without a URL is returned unchanged.
    """
    if not has_url(text):
        return text
    return _tidy(_strip_urls_inline(text))


def scrub_deep(obj):
    """Recursively scrub every string in a nested structure.

    bixby:// URIs are deeplinks, not web URLs, and are left untouched.
    """
    if isinstance(obj, str):
        return obj if _BIXBY_OK.match(obj) else scrub(obj)
    if isinstance(obj, list):
        return [scrub_deep(v) for v in obj]
    if isinstance(obj, dict):
        return {k: scrub_deep(v) for k, v in obj.items()}
    return obj
