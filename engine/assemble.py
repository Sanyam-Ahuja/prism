"""Stage 4: category assignment, ordering, and field coercion.

Deterministic. The extractor proposes; this module decides. See
docs/DATA_CONTRACT.md sections 2-3.
"""
from __future__ import annotations

import re

from validators.scrub import scrub

CATEGORY_RANK = {"auto": 0, "manual": 1, "critical": 2}

# Keyword overrides. critical beats manual where both fire.
_CRITICAL = re.compile(
    r"\b(factory reset|reset|restart|reboot|power off|power cycle|safe mode|"
    r"firmware update|software update|wipe|erase|force stop|clear data)\b", re.I)
_MANUAL = re.compile(
    r"\b(service cent(?:er|re)|authorized|authorised|repair|technician|"
    r"inspect|clean|cable|charger|ejector tool|ldi|liquid damage|physical damage|"
    r"walk-in|mail-in|warranty|replace)\b", re.I)

_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of",
                "on", "or", "the", "to", "via", "with"}

# Function words a truncated description must never end on.
_DANGLING = _SMALL_WORDS | {"your", "their", "its", "this", "that", "into",
                            "onto", "up", "out", "off", "over", "through"}

# Words that open a trailing clause; an over-long benefit is cut before one.
_BREAK = _SMALL_WORDS | {"by", "through", "when", "while", "so", "that", "which",
                         "because", "before", "after", "during", "until", "without", "if"}

_PROPER = {"samsung", "galaxy", "bixby", "android", "gmail", "wi-fi", "usb",
           "sim", "ldi", "qr", "hdr", "tv", "pin"}


# Explicit in-Settings navigation. Without this guard the manual keyword table is
# too blunt across domains: a Battery step like "Go to Settings, tap Battery, and
# enable fast charging when a charger is connected" matches /charger/ and would be
# demoted to manual, losing its deeplink to G13.
_SETTINGS_CUE = re.compile(
    r"\b(?:navigate to and open settings|go to settings|open settings|"
    r"tap (?:on )?(?:settings|display|battery|apps|connections|general management)|"
    r"toggle|switch next to|select the switch)\b", re.I)


def categorize(proposed: str, step_text: str) -> str:
    """Assign a category. Keyword evidence in the steps overrides the proposal.

    Precedence: critical > (manual unless the step is clearly in-Settings) >
    whatever the extractor proposed.
    """
    blob = step_text or ""
    if _CRITICAL.search(blob):
        return "critical"
    if _MANUAL.search(blob) and not _SETTINGS_CUE.search(blob):
        return "manual"
    return proposed if proposed in CATEGORY_RANK else "manual"


def title_case(text: str) -> str:
    """Title Case for actionName, preserving acronyms and lowering small words."""
    toks = (text or "").split()
    out = []
    for i, t in enumerate(toks):
        if t.isupper() and len(t) > 1:
            out.append(t)
        elif i > 0 and t.lower() in _SMALL_WORDS:
            out.append(t.lower())
        else:
            out.append(t[:1].upper() + t[1:].lower() if t else t)
    return " ".join(out)


def sentence_case(text: str) -> str:
    """Sentence case for title: first word capitalized, rest lower unless proper."""
    toks = (text or "").split()
    out = []
    for i, t in enumerate(toks):
        low = t.lower().strip(".,:;")
        if t.isupper() and len(t) > 1:
            out.append(t)
        elif low in _PROPER:
            out.append(t[:1].upper() + t[1:].lower())
        elif i == 0:
            out.append(t[:1].upper() + t[1:].lower())
        else:
            out.append(t.lower())
    return " ".join(out)


def make_goal(topic: str) -> str:
    """Exact template from PDF 4.1."""
    topic = title_case(scrub(topic or "Device").strip(" .")) or "Device"
    return f"Follow these steps to perform this {topic} Troubleshooting"


def fit_title(text: str) -> str:
    """Coerce to 2-3 words, sentence case (G2)."""
    words = [w for w in scrub(text or "").split() if w]
    words = [w.strip(".,:;") for w in words if w.strip(".,:;")]
    if len(words) > 3:
        # Drop leading small words first, then truncate from the right.
        while len(words) > 3 and words[0].lower() in _SMALL_WORDS:
            words.pop(0)
        words = words[:3]
        # Nor may it end on one: "Quick troubleshooting for battery drain" must
        # not become "Quick troubleshooting for".
        while len(words) > 2 and words[-1].lower() in _DANGLING:
            words.pop()
    while len(words) < 2:
        words.append("issue")
    return sentence_case(" ".join(words))


def fit_description(text: str) -> str:
    """Coerce to exactly 5-7 words starting 'It will' (G6).

    Trimming is deterministic and happens after the extractor's own attempt; the
    repair loop gives the model one chance to fix its own wording first.
    """
    t = scrub(text or "").strip()
    t = re.sub(r"^it will\s+", "", t, flags=re.I).strip(" .")
    # Models return "Improves touch sensitivity"; the template needs the bare
    # infinitive in lower case so "It will <body>" reads as English.
    if t:
        t = t[0].lower() + t[1:]
    first, _, rest = t.partition(" ")
    if first.endswith("s") and not first.endswith(("ss", "us", "is")):
        singular = first[:-2] + "y" if first.endswith("ies") else first.rstrip("s")
        if len(singular) > 2:
            t = (singular + " " + rest).strip()
    words = [w for w in t.split() if w]
    # "It will" is 2 of the 5-7, so the body carries 3-5.
    if len(words) > 5:
        # End the clause at a natural break ("reduce battery drain | by lowering
        # power use") rather than mid-phrase at word five ("...by lowering").
        cut = next((i for i in range(3, 6) if words[i].lower() in _BREAK), None)
        words = words[:cut] if cut else words[:5]
        # Truncation must not leave a dangling function word ("...side by").
        while len(words) > 3 and words[-1].lower() in _DANGLING:
            words.pop()
    while len(words) < 3:
        words.append("correctly")
    body = " ".join(words)
    return f"It will {body}"


def order_actions(actions: list[dict]) -> list[dict]:
    """Stable sort: auto < manual < critical, preserving source order within a tier."""
    return [
        a for _, a in sorted(
            enumerate(actions),
            key=lambda pair: (CATEGORY_RANK.get(pair[1].get("category"), 1), pair[0]),
        )
    ]


def merge_duplicate_screens(actions: list[dict]) -> list[dict]:
    """G12: one action = one screen. Merge actions sharing a resolved deeplink."""
    from engine.deeplink import DUMMY
    seen: dict[str, dict] = {}
    out: list[dict] = []
    for a in actions:
        uris = [
            (sg.get("actionableDeeplink") or {}).get("deeplink")
            for sg in a.get("stepGroups", [])
        ]
        key = next((u for u in uris if u and u != DUMMY), None)
        if key and key in seen:
            seen[key]["stepGroups"].extend(a.get("stepGroups", []))
            continue
        if key:
            seen[key] = a
        out.append(a)
    return out
