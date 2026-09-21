"""Stage 2 pre-processing: SIIS document -> numbered candidate steps.

This is the substrate for ADR-001. The extractor selects steps by index into
this list, so a step that does not appear here cannot appear in the output.
Segmentation quality therefore caps plan quality, which makes this module a
high-value test target.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from validators.scrub import scrub, has_url

# Leading discourse markers to strip before testing for an imperative.
_LEAD = re.compile(
    r"^\s*(?:first|next|then|now|also|finally|lastly|alternatively|afterwards?|"
    r"additionally|please|kindly|simply|just|to (?:do this|begin|start))\b[,:]?\s*",
    re.I,
)

# Verbs that open a UI instruction in Samsung support prose.
_IMPERATIVE_VERBS = {
    "tap", "touch", "press", "hold", "navigate", "go", "open", "select", "choose",
    "swipe", "scroll", "drag", "slide", "click", "double-tap", "long-press",
    "turn", "switch", "toggle", "enable", "disable", "activate", "deactivate",
    "check", "verify", "confirm", "ensure", "make", "review", "inspect", "examine",
    "look", "shine", "insert", "eject", "remove", "detach", "disconnect", "connect",
    "plug", "unplug", "charge", "restart", "reboot", "reset", "clear", "delete",
    "uninstall", "install", "update", "upgrade", "back", "backup", "restore",
    "contact", "visit", "schedule", "provide", "try", "attempt", "wait", "allow",
    "let", "keep", "set", "adjust", "change", "enter", "type", "sign", "log",
    "close", "exit", "return", "repeat", "perform", "run", "start", "stop", "use",
    "find", "locate", "search", "scan", "boot", "power", "place", "put", "hold",
    # Added during the four-domain audit: the original list was written while
    # reading Display documents, so Battery/Camera/Performance instructions were
    # silently dropped as non-actionable.
    "optimize", "optimise", "calibrate", "recalibrate", "recharge", "cool",
    "force", "limit", "restrict", "launch", "record", "capture", "focus",
    "zoom", "wipe", "pause", "resume", "reduce", "increase", "lower", "raise",
    "free", "end", "kill", "unload", "cache", "defragment", "refresh",
}

# Lead-ins that introduce a following list rather than instructing anything.
# "You can X" / "You should X" is instruction in Samsung support register, not
# narration. Strip the modal wrapper and test the verb underneath.
_MODAL = re.compile(
    r"^\s*(?:you (?:can|could|should|may|might|will need to|need to|must)|"
    r"we(?:'ll| will)|let's|it(?:'s| is) important to)\s+",
    re.I,
)

_LEADIN = re.compile(
    r"(?:here(?:'s| is| are)\b|there are\b|follow(?:ing)? (?:these|the)\b|"
    r"the following\b|as follows\b|steps? (?:to|for)\b.*:$|:\s*$)",
    re.I,
)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

# One source document ("Some things to check first") has had its whitespace
# stripped, producing run-on tokens such as
# "InteventyouhaveenteredtheincorrectPINfivetimesinrow". This is a defect in the
# supplied kit, not in our parsing; see docs/DECISIONS.md M-Q9. We recover the
# camelCase boundaries we can and accept reduced recall on the rest.
_RUNON = re.compile(r"\b[a-zA-Z]{18,}\b")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def repair_runon(text: str) -> str:
    """Re-space run-on tokens at camelCase boundaries where they exist."""
    def fix(m):
        return _CAMEL.sub(" ", m.group(0))
    return _RUNON.sub(fix, text or "")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")


@dataclass
class Candidate:
    idx: int
    text: str
    section: str


@dataclass
class Segmented:
    title: str
    sections: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)

    def numbered(self) -> str:
        """Render for the extractor prompt, grouped by section."""
        out, cur = [], None
        for c in self.candidates:
            if c.section != cur:
                cur = c.section
                out.append(f"\n[{cur}]")
            out.append(f"  {c.idx}. {c.text}")
        return "\n".join(out).strip()

    def texts(self, indices: list[int]) -> list[str]:
        """Expand selected indices back to verbatim step text."""
        by_idx = {c.idx: c.text for c in self.candidates}
        return [by_idx[i] for i in indices if i in by_idx]


def _strip_lead(s: str) -> str:
    """Strip stacked discourse markers: 'Now, please connect' -> 'connect'."""
    prev = None
    while prev != s:
        prev = s
        s = _LEAD.sub("", s)
        s = _MODAL.sub("", s)
    return s.strip()


def is_actionable(sentence: str) -> bool:
    """True if the sentence reads as a UI instruction rather than narration."""
    s = sentence.strip()
    if len(s) < 8:
        return False
    if _LEADIN.search(s):
        return False
    core = _strip_lead(s)
    if not core:
        return False
    # A conditional clause ("If the device has been exposed...") is context, but
    # the consequent may still instruct; keep it only if a verb follows the comma.
    if core.lower().startswith("if "):
        parts = core.split(",", 1)
        if len(parts) < 2:
            return False
        core = _strip_lead(parts[1])
    first = re.split(r"[\s,]", core, 1)[0].lower().strip(".:;")
    return first in _IMPERATIVE_VERBS


def normalize_step(sentence: str) -> str:
    """Normalize a source sentence into a clean imperative UI step."""
    s = _strip_lead(scrub(sentence.strip()))
    if not s:
        return s
    s = s[0].upper() + s[1:]
    if not s.endswith((".", "!", "?")):
        s += "."
    return s


def segment(content: str, title: str = "") -> Segmented:
    """Split SIIS content into sections and numbered actionable candidates."""
    seg = Segmented(title=title)
    section = title or "General"
    idx = 1

    # The SIIS payload prefixes a category blob before the first heading; drop it.
    body = content
    m = re.search(r"^#{1,6}\s", body, re.M)
    if m:
        body = body[m.start():]

    body = repair_runon(body)

    for line in body.splitlines():
        h = _HEADING.match(line)
        if h:
            section = h.group(2).strip()
            if section not in seg.sections:
                seg.sections.append(section)
            continue
        line = line.strip()
        if not line:
            continue
        for sent in _SENT_SPLIT.split(line):
            sent = sent.strip()
            if not sent or has_url(sent):
                continue
            if not is_actionable(sent):
                continue
            text = normalize_step(sent)
            if not text or len(text) < 8:
                continue
            seg.candidates.append(Candidate(idx, text, section))
            idx += 1
    return seg
