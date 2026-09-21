"""Gates G0-G16. See docs/DATA_CONTRACT.md section 7.

Every gate is a pure function (obj, ctx) -> list[Violation]. Gates never mutate;
repair.py owns mutation. This split keeps the scoreboard honest: we can always
ask "what is wrong" separately from "what did we change".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from validators.scrub import has_url

DUMMY = "bixby://dummy_positive"

GOAL_RE = re.compile(
    r"^Follow these steps to perform this (.+?) (Troubleshooting|Configuration)$"
)
DESC_RE = re.compile(r"^It will\b")
CATEGORIES = {"auto", "manual", "critical"}
CATEGORY_RANK = {"auto": 0, "manual": 1, "critical": 2}


@dataclass
class Violation:
    gate: str
    path: str
    message: str
    blocking: bool = True


@dataclass
class Ctx:
    """Catalog-derived facts the gates check against."""
    catalog_uris: set[str] = field(default_factory=set)
    validation_by_uri: dict[str, Any] = field(default_factory=dict)


def _words(text: str) -> list[str]:
    return [w for w in (text or "").split() if w]


def _is_title_case(text: str) -> bool:
    """Title Case allowing lowercase joining words after the first token."""
    small = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of",
             "on", "or", "the", "to", "via", "with"}
    toks = _words(text)
    if not toks:
        return False
    for i, t in enumerate(toks):
        core = t.strip("()[[],.:")
        if not core or not core[0].isalpha():
            continue
        if core.isupper():          # acronym, e.g. LDI, USB, QR
            continue
        if i > 0 and core.lower() in small:
            continue
        if not core[0].isupper():
            return False
    return True


def _is_sentence_case(text: str) -> bool:
    toks = _words(text)
    if not toks:
        return False
    first = toks[0].strip("()[],.:")
    if not first or not first[0].isupper():
        return False
    # Remaining tokens lowercase, unless an acronym or a known proper noun.
    proper = {"samsung", "galaxy", "bixby", "wi-fi", "gmail", "android"}
    for t in toks[1:]:
        core = t.strip("()[],.:")
        if not core or not core[0].isalpha():
            continue
        if core.isupper():
            continue
        if core.lower() in proper:
            continue
        if core[0].isupper():
            return False
    return True


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------

def g0_no_urls(obj: Any, path: str = "") -> list[Violation]:
    """G0: no web URL anywhere in the payload."""
    out: list[Violation] = []
    if isinstance(obj, str):
        if not obj.startswith("bixby://") and has_url(obj):
            out.append(Violation("G0", path, f"web URL present: {obj[:60]!r}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += g0_no_urls(v, f"{path}[{i}]")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out += g0_no_urls(v, f"{path}.{k}" if path else k)
    return out


def g1_goal(goal: str, path: str) -> list[Violation]:
    if not GOAL_RE.match(goal or ""):
        return [Violation("G1", path,
                          f"goal must match 'Follow these steps to perform this "
                          f"<Topic> Troubleshooting', got {goal!r}")]
    return []


def g2_title(title: str, path: str) -> list[Violation]:
    out = []
    n = len(_words(title))
    if not 2 <= n <= 3:
        out.append(Violation("G2", path, f"title must be 2-3 words, got {n}: {title!r}"))
    if title and not _is_sentence_case(title):
        out.append(Violation("G2", path, f"title must be sentence case: {title!r}"))
    return out


def g3_score(score: Any, path: str) -> list[Violation]:
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return [Violation("G3", path, f"score must be a float, got {type(score).__name__}")]
    if not 0.0 <= float(score) <= 1.0:
        return [Violation("G3", path, f"score must be in [0,1], got {score}")]
    return []


def g4_has_actions(actions: list, path: str) -> list[Violation]:
    if not actions:
        return [Violation("G4", path, "goal has no actions")]
    return []


def g5_action_name(name: str, path: str) -> list[Violation]:
    out = []
    if not (name or "").strip():
        out.append(Violation("G5", path, "actionName is empty"))
    elif not _is_title_case(name):
        out.append(Violation("G5", path, f"actionName must be Title Case: {name!r}"))
    return out


def g6_description(desc: str, path: str) -> list[Violation]:
    out = []
    if not DESC_RE.match(desc or ""):
        out.append(Violation("G6", path, f"description must start 'It will': {desc!r}"))
    n = len(_words(desc))
    if not 5 <= n <= 7:
        out.append(Violation("G6", path, f"description must be 5-7 words, got {n}: {desc!r}"))
    return out


def g7_steps(steps: list, path: str) -> list[Violation]:
    out = []
    if not steps:
        out.append(Violation("G7", path, "stepGroup has no steps"))
    for i, s in enumerate(steps or []):
        if not isinstance(s, str) or not s.strip():
            out.append(Violation("G7", f"{path}[{i}]", "step is empty"))
    return out


def g8_category(cat: Any, path: str) -> list[Violation]:
    if cat not in CATEGORIES:
        return [Violation("G8", path, f"category must be one of {sorted(CATEGORIES)}, got {cat!r}")]
    return []


def g9_uri_in_catalog(dl: dict | None, path: str, ctx: Ctx) -> list[Violation]:
    """G9: every emitted URI is a catalog member. Never rewrite - drop instead."""
    if not dl:
        return []
    uri = dl.get("deeplink")
    if uri == DUMMY:
        return []
    if uri not in ctx.catalog_uris:
        return [Violation("G9", path, f"URI not in catalog: {uri!r}")]
    return []


def g10_validation(vdl: dict | None, adl: dict | None, path: str, ctx: Ctx) -> list[Violation]:
    """G10: validationDeeplink matches its entry's validation block verbatim."""
    if vdl is None:
        return []
    if not adl:
        return [Violation("G10", path, "validationDeeplink present without actionableDeeplink")]
    expected = ctx.validation_by_uri.get(adl.get("deeplink"))
    if expected is None:
        return [Violation("G10", path,
                          f"no validation exists for {adl.get('deeplink')!r}; must be null")]
    for k, v in expected.items():
        if vdl.get(k) != v:
            return [Violation("G10", path,
                              f"validation field {k!r} altered: {vdl.get(k)!r} != {v!r}")]
    return []


def g11_variations(variations: list, path: str) -> list[Violation]:
    n = len(variations or [])
    out = []
    if not 8 <= n <= 10:
        out.append(Violation("G11", path, f"need 8-10 query_variations, got {n}", blocking=False))
    if len(set(variations or [])) != n:
        out.append(Violation("G11", path, "query_variations contain duplicates", blocking=False))
    return out


def g12_one_action_one_screen(actions: list, path: str) -> list[Violation]:
    """G12: no two actions resolve to the same deeplink (PDF 7.2)."""
    seen: dict[str, int] = {}
    out = []
    for i, a in enumerate(actions or []):
        for sg in a.get("stepGroups", []):
            dl = (sg.get("actionableDeeplink") or {}).get("deeplink")
            if not dl or dl == DUMMY:
                continue
            if dl in seen:
                out.append(Violation("G12", f"{path}.actions[{i}]",
                                     f"deeplink {dl!r} already used by action "
                                     f"[{seen[dl]}]; one action = one screen"))
            else:
                seen[dl] = i
    return out


def g13_manual_no_deeplink(action: dict, path: str) -> list[Violation]:
    """G13: manual actions cannot carry an actionable deeplink (PDF 4.1)."""
    if action.get("category") != "manual":
        return []
    out = []
    for i, sg in enumerate(action.get("stepGroups", [])):
        if sg.get("actionableDeeplink"):
            out.append(Violation("G13", f"{path}.stepGroups[{i}]",
                                 "manual action carries an actionableDeeplink"))
    return out


def g14_critical_last(actions: list, path: str) -> list[Violation]:
    ranks = [CATEGORY_RANK.get(a.get("category"), 1) for a in actions or []]
    if ranks != sorted(ranks):
        return [Violation("G14", path,
                          f"actions not ordered auto < manual < critical: {ranks}")]
    return []


def g15_schema(payload: dict, path: str = "response") -> list[Violation]:
    """G15: validates against the supplied schema.py, unmodified."""
    try:
        from schema import ContextDeeplinkResponse
    except ImportError as e:            # pragma: no cover
        return [Violation("G15", path, f"cannot import schema.py: {e}")]
    try:
        ContextDeeplinkResponse(**payload)
    except Exception as e:
        return [Violation("G15", path, f"pydantic validation failed: {e}")]
    return []


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def validate_envelope(env: dict, ctx: Ctx) -> list[Violation]:
    """Run every gate over a full response envelope. Returns all violations."""
    out: list[Violation] = []
    out += g0_no_urls(env)
    out += g11_variations(env.get("query_variations", []), "query_variations")

    contexts = (env.get("response") or {}).get("contexts", [])
    for gi, goal in enumerate(contexts):
        gp = f"contexts[{gi}]"
        out += g1_goal(goal.get("goal", ""), f"{gp}.goal")
        out += g2_title(goal.get("title", ""), f"{gp}.title")
        out += g3_score(goal.get("score"), f"{gp}.score")
        actions = goal.get("actions", [])
        out += g4_has_actions(actions, f"{gp}.actions")
        out += g12_one_action_one_screen(actions, gp)
        out += g14_critical_last(actions, gp)

        for ai, a in enumerate(actions):
            ap = f"{gp}.actions[{ai}]"
            out += g5_action_name(a.get("actionName", ""), f"{ap}.actionName")
            out += g6_description(a.get("description", ""), f"{ap}.description")
            out += g8_category(a.get("category"), f"{ap}.category")
            out += g13_manual_no_deeplink(a, ap)
            for si, sg in enumerate(a.get("stepGroups", [])):
                sp = f"{ap}.stepGroups[{si}]"
                out += g7_steps(sg.get("steps", []), f"{sp}.steps")
                adl = sg.get("actionableDeeplink")
                out += g9_uri_in_catalog(adl, f"{sp}.actionableDeeplink", ctx)
                out += g10_validation(sg.get("validationDeeplink"), adl,
                                      f"{sp}.validationDeeplink", ctx)

    out += g15_schema(env.get("response") or {"contexts": []})
    return out


def blocking(violations: Iterable[Violation]) -> list[Violation]:
    return [v for v in violations if v.blocking]


def load_ctx(deeplinks_path: str = "data/deeplinks.json") -> Ctx:
    import json
    with open(deeplinks_path) as f:
        entries = json.load(f)["deeplinks"]
    return Ctx(
        catalog_uris={e["deeplink"] for e in entries},
        validation_by_uri={e["deeplink"]: e.get("validation") for e in entries
                           if e.get("validation")},
    )
