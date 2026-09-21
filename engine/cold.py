"""Cold path: novel query + reference text -> validated plan.

Runs the same deterministic stages as the build-time compiler; only the
extractor differs (local Ollama model instead of the hosted build tier). Neither
model is trusted with formatting or URIs, which is what keeps the two tiers
structurally identical (ADR-009).
"""
from __future__ import annotations

import json
import os

import httpx

from engine.assemble import (categorize, fit_description, fit_title, make_goal,
                             merge_duplicate_screens, order_actions, title_case)
from engine.deeplink import DUMMY, DeeplinkResolver, build_actionable, build_validation
from engine.segment import segment
from engine.variations import generate as gen_variations
from validators.scrub import scrub_deep

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("PRISM_EXTRACT_MODEL", "gemma3:4b")
TAU_LINK = float(os.environ.get("PRISM_TAU_LINK", "0.45"))
TIMEOUT = float(os.environ.get("PRISM_EXTRACT_TIMEOUT", "30"))

# Grammar-constrained decoding: unparseable output becomes impossible.
SKELETON_SCHEMA = {
    "type": "object",
    "required": ["topic", "title", "actions"],
    "properties": {
        "topic": {"type": "string"},
        "title": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "screen", "category", "benefit", "steps"],
                "properties": {
                    "name": {"type": "string"},
                    # Required: an empty string is the explicit "no screen" answer.
                    # Optional fields get silently omitted, which skips Stage 3
                    # entirely and costs the deeplink-relevance score.
                    "screen": {"type": "string"},
                    "category": {"type": "string", "enum": ["auto", "manual", "critical"]},
                    "benefit": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "integer"}},
                },
            },
        },
    },
}

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


class ColdPath:
    def __init__(self, encoder=None, model: str = MODEL):
        self.model = model
        self.resolver = DeeplinkResolver("data/deeplinks.json", encoder=encoder)

    def available(self) -> bool:
        try:
            r = httpx.get(f"{OLLAMA}/api/tags", timeout=2.0)
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
            r = httpx.post(f"{OLLAMA}/api/generate", json=payload, timeout=TIMEOUT)
            d = r.json()
            return json.loads(d["response"]), d.get("prompt_eval_count", 0), d.get("eval_count", 0)
        except Exception:
            return None, 0, 0

    def _best_screen(self, name: str, screen: str):
        """Resolve using the best of several descriptor spellings.

        Models reliably put the parent menu in `screen` ("display settings") and
        the specific feature in `name` ("enable touch sensitivity"). Parent-menu
        matching is the exact failure PDF section 6.2 penalises, so we probe the
        name and the combination too and keep the most confident result rather
        than trusting the model's field discipline.
        """
        best = None
        probes = [p for p in (name, screen, f"{name} {screen}".strip()) if p.strip()]
        for probe in probes:
            m = self.resolver.resolve(probe, tau=TAU_LINK)
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

        actions = []
        for a in sk.get("actions", []):
            steps = seg.texts([i for i in a.get("steps", []) if isinstance(i, int)])
            if not steps:
                continue
            category = categorize(a.get("category", "manual"), " ".join(steps))
            adl = vdl = None
            screen = (a.get("screen") or "").strip()
            if category != "manual" and (screen or a.get("name")):
                m = self._best_screen(a.get("name", ""), screen)
                if m is not None and m.entry is not None:
                    label = screen or a.get("name", "the relevant settings")
                    if m.entry["deeplink"] == DUMMY:
                        adl = build_actionable(m.entry,
                                               fallback_desc=f"Open the {label} on the device",
                                               fallback_msg=title_case(label)[:60])
                    else:
                        adl = build_actionable(m.entry)
                        vdl = build_validation(m.entry)
            actions.append({
                "actionName": title_case(a.get("name", "Troubleshooting Step")),
                "description": fit_description(a.get("benefit", "resolve the reported issue")),
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
        }
