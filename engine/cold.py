"""Cold path: novel query + reference text -> validated plan.

Runs the same deterministic stages as the build-time compiler; only the
extractor differs (local Ollama model instead of the hosted build tier). Neither
model is trusted with formatting or URIs, which is what keeps the two tiers
structurally identical (ADR-009).
"""
from __future__ import annotations

import json
import os
import re

import httpx

from engine.assemble import (CATEGORY_RANK, categorize, fit_description, fit_title,
                             make_goal, merge_duplicate_screens, order_actions, title_case)
from engine.deeplink import (DUMMY, DeeplinkResolver, build_actionable,
                             build_validation, dummy_text)
from engine.segment import segment
from engine.variations import generate as gen_variations
from validators.scrub import scrub_deep

# 127.0.0.1, not localhost: on Windows localhost resolves to ::1 first, Ollama
# binds IPv4 only by default, and the refused IPv6 connect costs ~2 s before the
# IPv4 fallback - on every new connection.
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
# Chosen by scripts/compare_models.py over five local models (ADR-017).
MODEL = os.environ.get("PRISM_EXTRACT_MODEL", "qwen2.5:1.5b")
TAU_LINK = float(os.environ.get("PRISM_TAU_LINK", "0.52"))
TIMEOUT = float(os.environ.get("PRISM_EXTRACT_TIMEOUT", "30"))

# Grammar-constrained decoding: unparseable output becomes impossible, and the
# length bounds make runaway output impossible too. Without them the 1.5B model
# can loop inside one string ("Removes debris ... Reinstalls touch sensitivity
# ..." until num_predict), so no action ever closes and the plan is lost.
SKELETON_SCHEMA = {
    "type": "object",
    "required": ["topic", "title", "actions"],
    "properties": {
        "topic": {"type": "string", "maxLength": 30},
        "title": {"type": "string", "maxLength": 40},
        "actions": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["name", "screen", "category", "benefit", "steps"],
                "properties": {
                    "name": {"type": "string", "maxLength": 50},
                    # Required: an empty string is the explicit "no screen" answer.
                    # Optional fields get silently omitted, which skips Stage 3
                    # entirely and costs the deeplink-relevance score.
                    "screen": {"type": "string", "maxLength": 60},
                    "category": {"type": "string", "enum": ["auto", "manual", "critical"]},
                    "benefit": {"type": "string", "maxLength": 50},
                    "steps": {"type": "array", "items": {"type": "integer"}, "maxItems": 12},
                },
            },
        },
    },
}

# The concrete examples anchor catalog register and brevity; without them the
# model's screen names stopped matching the catalog and its strings ran away.
# The model does sometimes copy an example into a plan it does not belong to
# ("enable touch sensitivity" on a battery article); grounded() below rejects
# that in code rather than trusting the prompt to prevent it (ADR-018).
PROMPT = """You group pre-written troubleshooting steps into actions.

Numbered steps from the support document "{title}":
{numbered}

Rules:
- Select step NUMBERS only. Never write step text.
- One action = one settings screen, or one physical action.
- category: "auto" if the steps change a Settings screen, "critical" for restart /
  safe mode / factory reset, "manual" for physical work or a service visit.
- "screen" is REQUIRED on every action. For an auto action name the Settings
  screen in catalog style, using the wording of the document's own domain:
  "enable touch sensitivity", "enable adaptive battery", "open the camera
  settings page", "adjust the motion smoothness value". For manual and critical
  actions use an empty string "".
- "benefit": 3 to 5 words, lower case, starting with a base-form verb
  ("improve touch response", not "Improves touch sensitivity"). No "It will".
- Produce at most 6 actions. Merge steps that share one screen.
- Name the SPECIFIC screen, never a parent menu. Write "enable touch sensitivity",
  not "display settings". Write "enable adaptive battery", not "battery settings".
  Write "open the navigation bar settings page", not "settings".
- "title": 2 to 3 words naming the issue.
- "topic": 1 to 2 words for the plan heading.

Return JSON only."""

# Words that say nothing about which screen or step an action concerns.
_GENERIC = {
    "and", "the", "for", "with", "your", "off", "into", "from", "will",
    "enable", "disable", "open", "adjust", "set", "turn", "configure", "change", "use",
    "setting", "settings", "page", "screen", "value", "menu", "option", "feature",
    "device", "phone", "tablet", "galaxy", "samsung", "app",
}
_WORD = re.compile(r"[a-z]+")
_STEP_HEADING = re.compile(r"^\s*(?:step\s*)?\d+\s*[:.)\-]\s*", re.I)

# The screens the steps themselves navigate to: "tap Navigation bar", "the switch
# next to Touch sensitivity", "Settings > Security and privacy > Screen lock".
# SIIS text writes UI labels in sentence case, so a label starts with a capital.
# "Select" is left out: it picks an option on a screen ("Select Buttons"), not
# the screen itself.
_NAV = re.compile(
    r"\b(?P<verb>(?i:tap|open|go to|navigate to|turn on|turn off|enable|disable))\s+"
    r"(?i:on\s+)?(?i:the\s+)?(?P<switch>(?i:switch(?:es)?\s+next\s+to\s+))?"
    r"(?P<label>[A-Z][\w\-]*(?:\s+(?!(?:and|then|to|when|if|again|or|from|in|on|at|with|there)\b)[\w\-]+){0,4})")
_PATH = re.compile(r"\bSettings\s*>\s*([^.,;]+)")
# Menus every path passes through, and dialog buttons: never the target screen.
_NAV_GENERIC = {"settings", "apps", "display", "connections", "advanced features",
                "general management", "security and privacy", "device care", "all apps",
                "delete all", "reset", "restart", "power off", "ok", "allow", "done",
                "cancel", "confirm", "back", "home", "search", "edit"}
# Honest names for an action whose own name was discarded and whose steps sit
# under no heading of their own.
_FALLBACK_NAME = {"auto": "Review Device Settings", "manual": "Check the Hardware",
                  "critical": "Last Resort Recovery"}


def _content(text: str) -> set[str]:
    return {w.rstrip("s") for w in _WORD.findall((text or "").lower())
            if len(w) > 2 and w not in _GENERIC}


def grounded(phrase: str, evidence: str) -> bool:
    """False when a phrase names something its own steps never mention.

    With concrete examples in the prompt, 13 of 57 cold-path actions were named
    after those examples and deeplinked to their screens. A name, screen or
    benefit with no content word in common with its steps and their heading is
    not about those steps. A phrase made only of generic words claims nothing.
    """
    words = _content(phrase)
    return not words or bool(words & _content(evidence))


def step_target(steps: list[str]) -> str | None:
    """The deepest specific screen the steps navigate to, as a catalog-style probe.

    The model often names a parent menu or nothing usable (3-9% of its auto
    actions linked a specific screen), but the steps it selected usually spell
    out the exact screen. Taken from the source text, so always grounded.

    Written in catalog register, which is what resolves: "open Wi-Fi" matched
    the Wi-Fi *notifications* page, "open the Wi-Fi settings page" the Wi-Fi
    page. A switch keeps its verb: "enable Touch sensitivity" is the entry.
    """
    found = []
    for s in steps:
        hits = [(m.start(1) + i, "open", seg.strip())
                for m in _PATH.finditer(s) for i, seg in enumerate(m.group(1).split(">"))]
        for m in _NAV.finditer(s):
            verb = m.group("verb").lower()
            v = ("enable" if m.group("switch") or verb in ("turn on", "enable") else
                 "disable" if verb in ("turn off", "disable") else "open")
            hits.append((m.start(), v, m.group("label").strip()))
        found += [(v, label) for _, v, label in sorted(hits)]
    for verb, label in reversed(found):
        if len(label) > 2 and label.lower() not in _NAV_GENERIC:
            return f"open the {label} settings page" if verb == "open" else f"{verb} {label}"
    return None


def heading_name(section: str, title: str) -> str | None:
    """A name taken from the source's own heading, e.g. 'Step 2: Review Battery Usage'."""
    if not section or section == title:
        return None
    return _STEP_HEADING.sub("", section).strip(" :.") or None


def salvage(raw: str) -> dict | None:
    """Recover the complete actions from output cut off at num_predict.

    Grammar-constrained output is valid JSON up to the cut, and the schema emits
    topic and title before actions, so closing the array after the last complete
    action object gives a valid skeleton minus the unfinished action. Without
    this, one over-long document ("Use Multi window", 500+ tokens) returned no
    plan at all.
    """
    for i in range(len(raw) - 1, 0, -1):
        if raw[i] == "}":
            try:
                sk = json.loads(raw[: i + 1] + "]}")
            except ValueError:
                continue
            return sk if sk.get("actions") else None
    return None


class ColdPath:
    def __init__(self, encoder=None, model: str = MODEL,
                 vectors: str | None = "artifacts/catalog_vectors.npy"):
        self.model = model
        # Catalog vectors from the build: embedding them here cost 7.1 s of startup.
        self.resolver = DeeplinkResolver("data/deeplinks.json", encoder=encoder,
                                         vectors=vectors)
        # One pooled client for the process. httpx.post() builds and discards a
        # client per call (~0.4 s of TLS-context setup, even for plain HTTP) and
        # reconnects every time; measured at ~3 s of each cold call on Windows.
        self.http = httpx.Client(base_url=OLLAMA, timeout=TIMEOUT)

    def available(self) -> bool:
        try:
            r = self.http.get("/api/tags", timeout=2.0)
            names = [m["name"] for m in r.json().get("models", [])]
            return any(n.split(":")[0] == self.model.split(":")[0] for n in names)
        except Exception:
            return False

    def _extract(self, seg) -> tuple[dict | None, int, int]:
        payload = {
            "model": self.model,
            "prompt": PROMPT.format(title=seg.title, numbered=seg.numbered()),
            "format": SKELETON_SCHEMA,
            "stream": False,
            "keep_alive": -1,          # never pay the model load cost on a request
            "options": {"temperature": 0, "top_k": 1, "seed": 42, "num_predict": 500},
        }
        try:
            d = self.http.post("/api/generate", json=payload).json()
        except Exception:
            return None, 0, 0
        ptok, ctok = d.get("prompt_eval_count", 0), d.get("eval_count", 0)
        try:
            return json.loads(d["response"]), ptok, ctok
        except (KeyError, ValueError):
            sk = salvage(d.get("response", "")) if d.get("done_reason") == "length" else None
            return sk, ptok, ctok

    @staticmethod
    def _probes(name: str, screen: str, steps: list[str] = ()) -> list[str]:
        probes = (name, screen, f"{name} {screen}".strip(), step_target(steps) or "")
        return [p for p in dict.fromkeys(probes) if p.strip()]

    def _best_screen(self, name: str, screen: str, qvecs=None, steps: list[str] = ()):
        """Resolve using the best of several descriptor spellings.

        Models reliably put the parent menu in `screen` ("display settings") and
        the specific feature in `name` ("enable touch sensitivity"). Parent-menu
        matching is the exact failure PDF section 6.2 penalises, so we probe the
        name, the combination and the screen the steps themselves navigate to,
        and keep the most confident result rather than trusting the model's
        field discipline.
        """
        best = None
        for probe in self._probes(name, screen, steps):
            m = self.resolver.resolve(probe, tau=TAU_LINK, qv=(qvecs or {}).get(probe))
            if m.entry is None:
                continue
            if best is None or m.score > best.score:
                best = m
        return best

    def run(self, query: str, siis_response) -> dict | None:
        content = (siis_response or {}).get("content") if isinstance(siis_response, dict) else str(siis_response)
        title = (siis_response or {}).get("title", "") if isinstance(siis_response, dict) else ""
        if not content:
            return None

        seg = segment(content, title)
        variations = gen_variations(query)
        if not seg.candidates:
            return {"contexts": [], "variations": variations, "cost_usd": 0.0}

        sk, ptok, ctok = self._extract(seg)
        if not sk:
            return {"contexts": [], "variations": variations, "cost_usd": 0.0}

        # Check every model-written phrase against the steps it labels before
        # anything is resolved: an ungrounded name falls back to the source's own
        # heading, and an ungrounded screen is never used to find a deeplink.
        cands = {c.idx: c for c in seg.candidates}
        drafts: dict[str, dict] = {}
        repaired = {"names": 0, "screens": 0, "benefits": 0}
        for a in sk.get("actions", []):
            idx = [i for i in a.get("steps", []) if isinstance(i, int) and i in cands]
            if not idx:
                continue
            steps = [cands[i].text for i in idx]
            sections = list(dict.fromkeys(cands[i].section for i in idx))
            evidence = " ".join(steps + sections)
            category = categorize(a.get("category", "manual"), " ".join(steps))
            name = (a.get("name") or "").strip()
            if not name or not grounded(name, evidence):
                name = heading_name(sections[0], seg.title) or _FALLBACK_NAME[category]
                repaired["names"] += 1
            screen = (a.get("screen") or "").strip()
            if not grounded(screen, evidence):
                screen = ""
                repaired["screens"] += 1
            benefit = a.get("benefit") or ""
            if not grounded(benefit, evidence):
                benefit = "resolve the reported issue"
                repaired["benefits"] += 1
            # The model sometimes splits one action in two under the same name
            # ("Clear the Camera App Cache" as manual and as critical): merge them,
            # keeping the more disruptive category so ordering stays safe.
            d = drafts.get(name.lower())
            if d is None:
                drafts[name.lower()] = {"name": name, "screen": screen, "benefit": benefit,
                                        "category": category, "steps": steps}
            else:
                d["steps"] += [s for s in steps if s not in d["steps"]]
                if CATEGORY_RANK[category] > CATEGORY_RANK[d["category"]]:
                    d["category"] = category
                d["screen"] = d["screen"] or screen

        # Every probe for the plan in one batched encode. The batch is a pure
        # function of the skeleton, so identical inputs still give identical plans.
        qvecs = self.resolver.encode_queries(
            [p for d in drafts.values() if d["category"] != "manual"
             for p in self._probes(d["name"], d["screen"], d["steps"])])

        actions = []
        for d in drafts.values():
            name, screen, benefit, category, steps = (d["name"], d["screen"], d["benefit"],
                                                      d["category"], d["steps"])
            adl = vdl = None
            if category != "manual":
                m = self._best_screen(name, screen, qvecs, steps)
                if m is not None and m.entry is not None:
                    if m.entry["deeplink"] == DUMMY:
                        # A parent-menu screen ("settings") makes a meaningless
                        # label; the action's own name says which screen it is.
                        d_desc, d_msg = dummy_text(screen if _content(screen) else name)
                        adl = build_actionable(m.entry, fallback_desc=d_desc,
                                               fallback_msg=d_msg)
                    else:
                        adl = build_actionable(m.entry)
                        vdl = build_validation(m.entry)
            actions.append({
                "actionName": title_case(name),
                "description": fit_description(benefit),
                "stepGroups": [{"steps": steps, "actionableDeeplink": adl,
                                "validationDeeplink": vdl}],
                "category": category,
            })

        actions = order_actions(merge_duplicate_screens(actions))
        if not actions:
            return {"contexts": [], "variations": variations, "cost_usd": 0.0}

        plan = {
            "goal": make_goal(sk.get("topic") or title or "Device"),
            "title": fit_title(sk.get("title") or title or "device issue"),
            "score": 0.78,          # cold-path plans are less certain than compiled ones
            "actions": actions,
        }
        return {
            "contexts": scrub_deep([plan]),
            "variations": variations,
            "cost_usd": 0.0,        # local inference
            "tokens": {"prompt": ptok, "completion": ctok},
            # How often grounding overrode the model; reported by compare_models.
            "repaired": repaired,
        }
