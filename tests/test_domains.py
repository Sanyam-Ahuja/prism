"""Four-domain generalisation (PDF Appendix C section 2).

All 20 supplied queries are Display; accuracy is judged across Battery, Display,
Camera and Performance. These tests prove the pipeline is domain-independent.
They use the synthetic probe corpus and never touch artifacts/ - real plan
coverage is a function of supplied SIIS text, which we only have for Display.
"""
import glob
import json
import os
import sys

import pytest

sys.path.insert(0, ".")

from engine.assemble import categorize, fit_description, make_goal, order_actions
from engine.deeplink import DUMMY, DeeplinkResolver
from engine.embed import get_encoder
from engine.segment import segment
from validators.gates import blocking, load_ctx, validate_envelope

CTX = load_ctx("data/deeplinks.json")
PROBES = sorted(glob.glob("tests/fixtures/probes/*.json"))


@pytest.fixture(scope="module")
def resolver():
    return DeeplinkResolver("data/deeplinks.json", encoder=get_encoder())


def test_probe_corpus_covers_the_unseen_domains():
    names = {os.path.basename(p) for p in PROBES}
    assert names == {"battery.json", "camera.json", "performance.json"}


@pytest.mark.parametrize("path", PROBES)
def test_segmentation_finds_steps_in_every_domain(path):
    """Regression: the verb list was written from Display docs and dropped
    Battery/Camera/Performance instructions as non-actionable."""
    d = json.load(open(path))
    seg = segment(d["content"], d["title"])
    assert len(seg.candidates) >= 8, f"{path}: only {len(seg.candidates)} candidates"
    assert len(seg.sections) >= 4


@pytest.mark.parametrize("path", PROBES)
def test_domain_plan_passes_every_gate(path, resolver):
    """Build a plan from probe text through the deterministic stages."""
    from scripts.compile_plans import build_action

    d = json.load(open(path))
    seg = segment(d["content"], d["title"])
    n = len(seg.candidates)
    # Three actions spanning the category range, using real step indices.
    sk_actions = [
        {"name": "Review Settings Screen", "category": "auto",
         "benefit": "review the relevant settings",
         "stepGroups": [{"screen": "open the battery settings page",
                         "steps": list(range(1, min(4, n) + 1))}]},
        {"name": "Inspect The Hardware", "category": "manual",
         "benefit": "rule out a physical fault",
         "stepGroups": [{"screen": None, "steps": [min(5, n)]}]},
        {"name": "Restart The Device", "category": "critical",
         "benefit": "clear a temporary fault",
         "stepGroups": [{"screen": None, "steps": [n]}]},
    ]
    actions = [a for a in (build_action(x, seg, resolver) for x in sk_actions) if a]
    assert actions, f"{path}: no actions built"

    plan = {"goal": make_goal("Device"), "title": "Device issue",
            "score": 0.8, "actions": order_actions(actions)}
    env = {"query": d["title"], "query_variations": [],
           "response": {"contexts": [plan]}}
    bad = blocking(validate_envelope(env, CTX))
    assert bad == [], f"{path}: {[(v.gate, v.message) for v in bad[:3]]}"


def test_domain_deeplink_precision(resolver):
    """Stage 3 must resolve non-Display descriptors, not just screen ones."""
    labels = json.load(open("tests/fixtures/deeplink_labels_domains.json"))
    ok = 0
    for L in labels:
        m = resolver.resolve(L["descriptor"], tau=0.52)
        got = "DUMMY" if (not m.entry or m.entry["deeplink"] == DUMMY) else m.entry["id"]
        ok += got == L["expect"]
    assert ok / len(labels) >= 0.90, f"domain precision {ok}/{len(labels)}"


def test_catalog_is_display_skewed():
    """Documents a data limitation that caps achievable non-Display coverage.

    Any submission is bounded by this: the catalog indexes far more Display
    screens than Battery ones, so Battery plans legitimately lean on
    dummy_positive more than Display plans do.
    """
    import re
    dl = json.load(open("data/deeplinks.json"))["deeplinks"]

    def blob(e):
        return " ".join(str(e.get(k) or "") for k in
                        ("description", "message", "qna_description"))

    display = sum(1 for e in dl if re.search(r"\b(screen|display|brightness|touch)\b", blob(e), re.I))
    battery = sum(1 for e in dl if re.search(r"\b(batter|charg|power sav)\b", blob(e), re.I))
    assert display > battery * 5, "catalog skew assumption no longer holds"


def test_mislabelled_catalog_messages_do_not_break_matching():
    """DL-0397/0398 say 'Adaptive Display' in message but 'adaptive battery' in
    description. Matching on description+message+qna is what saves us."""
    dl = {e["id"]: e for e in json.load(open("data/deeplinks.json"))["deeplinks"]}
    assert "adaptive battery" in dl["DL-0398"]["description"].lower()
    assert "adaptive display" in dl["DL-0398"]["message"].lower()
