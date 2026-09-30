"""Stage 3: deeplink resolution. See docs/ARCHITECTURE.md section 8.

Deterministic. The LLM never sees or emits a URI (ADR-002); it emits a screen
descriptor, and this module resolves descriptor -> catalog entry.

Retrieval is BM25 + dense embeddings fused with RRF, behind a hard polarity
pre-filter on originalType. The polarity gate is the highest-leverage precision
mechanism here: near-duplicate pairs like DL-0020/DL-0021 (disable/enable
adaptive brightness) are nearly identical to an encoder and only originalType
separates them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

from rank_bm25 import BM25Okapi

DUMMY = "bixby://dummy_positive"

# Empirical bge-small similarity band, calibrated in scripts/calibrate.py.
COS_FLOOR = 0.68
COS_CEIL = 0.89
# Confidence weights. Polarity is a prior, not a gate: see _candidate_mask.
W_COS = 0.55
W_COV = 0.25
W_POL = 0.20
# Rank bonus for an entry whose originalType matches the descriptor's intent.
POLARITY_RANK_BONUS = 0.010
# Candidates re-scored by confidence before selection.
SELECT_K = 10

# Boilerplate present in nearly every catalog description. Left in, it dominates
# BM25 term frequencies ("device" appears 1126x across 578 entries) and destroys
# discrimination.
_BOILERPLATE = re.compile(
    r"\b(?:via|in|on|from)?\s*device settings(?:\s+on\s+the\s+device)?\b"
    r"|\bsettings page in device settings\b"
    r"|\bon the device\b"
    r"|\bopens the\b|\bsettings page\b",
    re.I,
)

_STOP = {
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "your", "you",
    "is", "are", "be", "it", "this", "that", "with", "via", "from", "at", "by",
    "so", "as", "if", "when", "device", "settings", "setting", "page", "opens",
}

# SmartThings appliance entries: irrelevant to phone troubleshooting and a live
# false-positive risk (DL-0468 air conditioner, DL-0469/DL-0473 refrigerator).
_APPLIANCE = re.compile(
    r"\b(refrigerator|air conditioner|washer|dryer|dishwasher|oven|freezer|"
    r"cooktop|microwave|air purifier|robot cleaner|thermostat)\b",
    re.I,
)

# Catalog entries with corrupt metadata: usable URI, unusable message/key.
CORRUPT_IDS = {"DL-0294", "DL-0295"}

# Verb -> required originalType. Order matters: longest/most specific first.
_POLARITY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(turn off|switch off|disable|deactivate|stop|prevent|remove|"
                r"uncheck|opt out)\b", re.I), "offURL"),
    (re.compile(r"\b(turn on|switch on|enable|activate|allow|check|opt in)\b", re.I), "onURL"),
    (re.compile(r"\b(set|adjust|change|update|increase|decrease|slide|drag)\b", re.I), "updateURL"),
    (re.compile(r"\b(open|go to|navigate|view|tap|select|check|inspect|review)\b", re.I), "onClickURL"),
]


def _tokenize(text: str) -> list[str]:
    text = _BOILERPLATE.sub(" ", text or "")
    toks = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in toks if t not in _STOP and len(t) > 1]


def _stem(t: str) -> str:
    """Crude suffix stripper so enable/enables/enabling collapse to one token."""
    for suf in ("ings", "ing", "ies", "es", "ed", "s"):
        if len(t) > len(suf) + 2 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def _stems(text: str) -> set[str]:
    return {_stem(t) for t in _tokenize(text)}


def polarity_of(descriptor: str) -> Optional[str]:
    """Required originalType for a descriptor, or None if undetermined."""
    for pat, otype in _POLARITY_RULES:
        if pat.search(descriptor or ""):
            return otype
    return None


@dataclass
class Match:
    entry: Optional[dict]
    score: float
    used_dummy: bool
    reason: str


class DeeplinkResolver:
    def __init__(self, path: str = "data/deeplinks.json", encoder=None,
                 vectors: Optional[str] = None):
        with open(path) as f:
            raw = json.load(f)["deeplinks"]

        self.entries = [e for e in raw if e["deeplink"] != DUMMY]
        self.dummy = next(e for e in raw if e["deeplink"] == DUMMY)
        self.by_uri = {e["deeplink"]: e for e in raw}

        self.searchable = [self._searchable(e) for e in self.entries]
        self.corpus = [_tokenize(s) for s in self.searchable]
        self.bm25 = BM25Okapi(self.corpus)

        self.encoder = encoder
        self._dense = None
        if encoder is not None:
            import numpy as np
            self._np = np
            if vectors:
                self._dense = self._load_vectors(vectors)
            if self._dense is None:
                self._dense = encoder.encode(self.searchable, normalize_embeddings=True)

    def fingerprint(self) -> str:
        """Identifies the exact catalog text the dense vectors embed."""
        h = hashlib.sha256()
        for s in self.searchable:
            h.update(s.encode("utf-8") + b"\n")
        return h.hexdigest()

    def save_vectors(self, path: str) -> None:
        """Written at build time by scripts/compile_plans.py."""
        import numpy as np
        np.save(path, np.asarray(self._dense, dtype=np.float32))
        with open(os.path.splitext(path)[0] + ".json", "w") as f:
            json.dump({"fingerprint": self.fingerprint(), "rows": len(self.searchable)}, f)

    def _load_vectors(self, path: str):
        """Catalog vectors embedded at build time, or None to embed them now.

        Embedding all 578 entries at startup took 7.1 s on CPU. The saved file is
        used only if it embeds this exact catalog text, and one entry re-encoded
        now must match it, so a changed catalog or encoder is recomputed rather
        than trusted stale.
        """
        import numpy as np
        try:
            with open(os.path.splitext(path)[0] + ".json") as f:
                meta = json.load(f)
            vecs = np.load(path)
        except (OSError, ValueError):
            return None
        if meta.get("fingerprint") != self.fingerprint() or len(vecs) != len(self.searchable):
            return None
        check = self.encoder.encode(self.searchable[:1], normalize_embeddings=True)[0]
        if float(np.dot(check, vecs[0])) < 0.999:
            return None
        return vecs

    @staticmethod
    def _searchable(e: dict) -> str:
        """Matching runs on descriptive metadata only, never the masked URI (PDF 7.4)."""
        parts = [e.get("description") or "", e.get("qna_description") or ""]
        if e.get("id") not in CORRUPT_IDS:
            parts.insert(1, e.get("message") or "")
        return " ".join(p for p in parts if p)

    def _candidate_mask(self, descriptor: str, allow_appliance: bool) -> list[int]:
        """Hard pre-filters applied before ranking.

        Polarity is deliberately NOT a hard filter. The catalog does not always
        offer the entry type the user's phrasing implies: "turn on smoother
        scrolling" resolves to DL-0228, an updateURL, and "turn off usage
        sharing" only has DL-0083, an onClickURL. Excluding on polarity makes
        those correct answers unreachable, so it is applied as a ranking and
        confidence prior instead.
        """
        idx = []
        for i, e in enumerate(self.entries):
            if not allow_appliance and _APPLIANCE.search(self.searchable[i]):
                continue
            # originalType null entries are read-only diagnostics: never actionable.
            if e.get("originalType") in (None, "placeholder"):
                continue
            idx.append(i)
        return idx

    def encode_queries(self, descriptors: list[str]) -> dict:
        """Batch-encode descriptors once, for resolve(..., qv=...).

        One batched encode for a whole plan's probes is ~6x cheaper on CPU than
        one encode per probe (18 probes: 212 ms vs ~1.2 s, measured), and the
        cold path resolves up to three probes per action.
        """
        if self._dense is None:
            return {}
        uniq = list(dict.fromkeys(d for d in descriptors if (d or "").strip()))
        if not uniq:
            return {}
        from engine.embed import QUERY_PREFIX
        vecs = self.encoder.encode([QUERY_PREFIX + d for d in uniq],
                                   normalize_embeddings=True)
        return dict(zip(uniq, vecs))

    def _rank(self, descriptor: str, allow_appliance: bool, qv=None):
        """Shared ranking pass. Returns (ordered_indices, sims, want) or None."""
        cand = self._candidate_mask(descriptor, allow_appliance)
        if not cand:
            return None
        q = _tokenize(descriptor)
        if not q:
            return None

        want = polarity_of(descriptor)
        bm = self.bm25.get_scores(q)
        K = 60.0
        ranks: dict[int, float] = {}
        for r, i in enumerate(sorted(cand, key=lambda i: -bm[i])):
            ranks[i] = ranks.get(i, 0.0) + 1.0 / (K + r + 1)

        sims = None
        if self._dense is not None:
            if qv is None:
                from engine.embed import QUERY_PREFIX
                qv = self.encoder.encode([QUERY_PREFIX + descriptor],
                                         normalize_embeddings=True)[0]
            sims = self._dense @ qv
            for r, i in enumerate(sorted(cand, key=lambda i: -sims[i])):
                ranks[i] = ranks.get(i, 0.0) + 1.0 / (K + r + 1)

        if want:
            for i in cand:
                if self.entries[i].get("originalType") == want:
                    ranks[i] = ranks.get(i, 0.0) + POLARITY_RANK_BONUS

        # Deterministic tie-break on catalog id, never insertion order.
        order = [i for i, _ in sorted(ranks.items(),
                                      key=lambda kv: (-kv[1], self.entries[kv[0]]["id"]))]
        return order, sims, want

    def candidates(self, descriptor: str, k: int = 10,
                   allow_appliance: bool = False) -> list[tuple[dict, float]]:
        """Top-k (entry, confidence), best first.

        Exposed for rerankers: our retrieval puts the correct entry in the top 5
        for 93% of free-form descriptors but ranks it first only 67% of the time
        (reports/dl_paraphrase_hybrid.txt), so a second-stage reranker has real
        headroom to work with.
        """
        if not (descriptor or "").strip():
            return []
        r = self._rank(descriptor, allow_appliance)
        if r is None:
            return []
        order, sims, want = r
        return [(self.entries[i], self._confidence(descriptor, i, sims, want))
                for i in order[:k]]

    def resolve(self, descriptor: str, tau: float = 0.52,
                allow_appliance: bool = False, debug: bool = False,
                qv=None) -> Match:
        """Resolve a screen descriptor to a catalog entry, or fall back to dummy.

        Ranking and confidence are deliberately separate. RRF is rank-derived, so
        the winner always scores 1/(K+1) no matter how good the match is - fine
        for ordering, useless for thresholding. Confidence is therefore computed
        from absolute signals (token coverage, cosine) on the winning candidate.
        """
        if not (descriptor or "").strip():
            return Match(None, 0.0, True, "empty descriptor")

        r = self._rank(descriptor, allow_appliance, qv)
        if r is None:
            return Match(self.dummy, 0.0, True, "no candidate after filtering")
        order, sims, want = r
        # Select by confidence among the RRF top-K, not by rank alone.
        # RRF orders candidates but carries no quality signal, so rank 1 can be
        # worse than rank 5: for "open the Wi-Fi connection settings page" RRF put
        # Notification Settings first (conf 0.626) while a WiFi entry sat at rank
        # 5 with conf 0.749. Confidence is the quality signal, so it decides.
        pool = order[:SELECT_K]
        i = max(pool, key=lambda j: (self._confidence(descriptor, j, sims, want),
                                     # deterministic tie-break, never insertion order
                                     [-ord(c) for c in self.entries[j]["id"]]))
        conf = self._confidence(descriptor, i, sims, want)

        if debug:
            for j in order[:5]:
                print(f"    {self.entries[j]['id']} conf={self._confidence(descriptor, j, sims, want):.3f} "
                      f"{self.entries[j]['message']!r}")

        if conf < tau:
            return Match(self.dummy, conf, True, f"confidence {conf:.3f} < tau={tau}")
        return Match(self.entries[i], conf, False, "matched")

    def _confidence(self, descriptor: str, i: int, sims, want: Optional[str] = None) -> float:
        """Absolute match confidence in [0,1] for candidate i."""
        qs = _stems(descriptor)
        if not qs:
            return 0.0
        es = _stems(self.searchable[i])
        coverage = len(qs & es) / len(qs)
        pol = 1.0 if (want and self.entries[i].get("originalType") == want) else 0.0
        if sims is None:
            return (1 - W_POL) * coverage + W_POL * pol
        # bge cosines sit around 0.6-0.7 even for unrelated text, so the raw value
        # is a poor [0,1] confidence. Rescale so COS_FLOOR maps to 0 and 1.0 to 1,
        # putting it on the same scale as token coverage.
        cosine = (float(sims[i]) - COS_FLOOR) / (COS_CEIL - COS_FLOOR)
        cosine = min(1.0, max(0.0, cosine))
        # Cosine-primary. Coverage is a lexical bonus, not a requirement: a
        # verbose paraphrase that shares no catalog vocabulary is still a valid
        # match, and coverage-dominated scoring rejects exactly those.
        return W_COS * cosine + W_COV * coverage + W_POL * pol


def dummy_text(screen: str) -> tuple[str, str]:
    """Compose the description and message for a dummy_positive placeholder.

    The catalog requires us to write these ourselves (DL-DUMMY.qna_description).
    We mirror the catalog's own phrasing so a placeholder reads like a real entry:
    "Opens the X settings page ...", "Enables X via ...", "Disables X via ...".
    Naive prefixing produced "Open the open the phone aspect ratio settings page"
    and "Opens the disable Super steady video stabilization".
    """
    text = (screen or "").strip()
    text = re.sub(r"\s+on the device$", "", text, flags=re.I).strip()

    verb_forms = [
        (r"^(?:enable|turn on|switch on|activate)\s+(?:the\s+)?", "Enables {} via device Settings on the device."),
        (r"^(?:disable|turn off|switch off|deactivate)\s+(?:the\s+)?", "Disables {} via device Settings on the device."),
        (r"^(?:adjust|set|change|update)\s+(?:the\s+)?", "Updates the {} via device Settings on the device."),
        (r"^(?:open|view|go to|navigate to)\s+(?:the\s+)?", "Opens the {} in device Settings on the device."),
    ]
    body, template = text, "Opens the {} in device Settings on the device."
    for pat, tmpl in verb_forms:
        m = re.match(pat, text, re.I)
        if m:
            body, template = text[m.end():].strip(), tmpl
            break
    if not body:
        body = "relevant settings screen"
    words = body.split()
    msg = " ".join(words[:7])
    return template.format(body), (msg[:1].upper() + msg[1:])


def build_actionable(entry: dict, fallback_desc: str = "", fallback_msg: str = "") -> dict:
    """Verbatim field copy (DATA_CONTRACT 4.1). classes is always omitted."""
    if entry["deeplink"] == DUMMY:
        return {
            "deeplink": DUMMY,
            "description": fallback_desc or entry["description"],
            "message": fallback_msg or entry["message"],
        }
    return {
        "deeplink": entry["deeplink"],
        "description": entry["description"],
        "message": entry["message"],
        "originalType": entry["originalType"],
    }


def build_validation(entry: dict) -> Optional[dict]:
    """Verbatim copy of the catalog validation block. Never synthesized."""
    if entry["deeplink"] == DUMMY:
        return None
    v = entry.get("validation")
    return dict(v) if v else None
