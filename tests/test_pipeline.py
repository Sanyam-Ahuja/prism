"""Regression suite. Pins the contract, the data facts, and the known conflicts."""
import glob
import json
import subprocess
import sys

import pytest

sys.path.insert(0, ".")

from engine.assemble import categorize, fit_description, fit_title, make_goal, order_actions
from engine.deeplink import DUMMY, DeeplinkResolver, build_actionable, build_validation
from engine.normalize import normalize
from engine.segment import is_actionable, segment
from engine.variations import generate
from validators.gates import (Ctx, blocking, g6_description, load_ctx,
                              validate_envelope)
from validators.scrub import has_url, scrub, scrub_deep

CTX = load_ctx("data/deeplinks.json")


# ---------------------------------------------------------------- data pinning
def test_catalog_facts():
    d = json.load(open("data/deeplinks.json"))["deeplinks"]
    assert len(d) == 578
    assert sum(1 for e in d if e.get("validation")) == 570
    assert sum(1 for e in d if (e.get("validation") or {}).get("resultType")) == 138


def test_siis_facts():
    r = json.load(open("data/siis_responses.json"))["responses"]
    assert len(r) == 20
    assert len({x["siis_response"]["title"] for x in r}) == 11


def test_scrubber_neutralizes_contact_details_in_source():
    """The SIIS text is not fully pre-cleaned.

    "Some things to check first" (rows 3, 11, 17) embeds a contact address in its
    whitespace-stripped run-on text. The kit's README claims the text carries no
    URLs; it does. G0 must catch it regardless of what the source claims.
    """
    r = json.load(open("data/siis_responses.json"))["responses"]
    dirty = [x["id"] for x in r if has_url(x["siis_response"]["content"])]
    assert dirty == ["row_3", "row_11", "row_17"]
    for x in r:
        assert not has_url(scrub(x["siis_response"]["content"]))


# ------------------------------------------------------------------- scrubbing
@pytest.mark.parametrize("text", [
    "Visit samsung.com/support for help.",
    "See [Samsung Repair](https://samsung.com/repair) for options.",
    "Go to Settings. You can find instructions at www.samsung.com/x.",
    "Tap Apps. Visit https://x.com for more. Tap Clear cache.",
])
def test_scrub_removes_all_urls(text):
    assert not has_url(scrub(text))


def test_scrub_preserves_bixby_uris():
    obj = {"deeplink": "bixby://masked/act/abc123"}
    assert scrub_deep(obj)["deeplink"] == "bixby://masked/act/abc123"


def test_scrub_preserves_valid_steps():
    s = "Tap Storage, then tap Clear cache."
    assert scrub(s) == s


# ----------------------------------------------------------------- known gates
def test_appendix_b_passes_every_gate():
    env = json.load(open("tests/fixtures/appendix_b.json"))
    assert blocking(validate_envelope(env, CTX)) == []


def test_shipped_sample_violates_description_rule():
    """sample_output.json is 9 and 12 words against the PDF's 5-7 (ADR-007).

    This asserts the gate catches it. If the organizers rule the sample
    authoritative (M-Q2), this test is the thing to invert.
    """
    s = json.load(open("data/sample_output.json"))
    env = {"query": s["query"], "query_variations": [], "response": s["response"]}
    g6 = [v for v in validate_envelope(env, CTX) if v.gate == "G6"]
    assert len(g6) == 2


# ----------------------------------------------------------------- coercion
@pytest.mark.parametrize("raw", [
    "facilitate secure data transfer between your devices",
    "help you locate the nearest Samsung service center and schedule",
    "recover an unresponsive device",
    "a",
])
def test_fit_description_always_satisfies_g6(raw):
    assert g6_description(fit_description(raw), "x") == []


def test_fit_title_always_2_or_3_words():
    for raw in ["Blank or black display on a Samsung phone", "screen", ""]:
        assert 2 <= len(fit_title(raw).split()) <= 3


def test_goal_template():
    assert make_goal("blank screen") == \
        "Follow these steps to perform this Blank Screen Troubleshooting"


def test_critical_keywords_override_proposal():
    assert categorize("auto", "Press and hold Power to restart the device") == "critical"
    assert categorize("auto", "Visit a Samsung service center") == "manual"
    assert categorize("auto", "Tap Display then tap Brightness") == "auto"


def test_ordering_places_critical_last():
    acts = [{"category": "critical"}, {"category": "auto"},
            {"category": "manual"}, {"category": "auto"}]
    assert [a["category"] for a in order_actions(acts)] == \
        ["auto", "auto", "manual", "critical"]


# ----------------------------------------------------------------- deeplinks
def test_resolver_never_invents_a_uri():
    r = DeeplinkResolver("data/deeplinks.json")
    for d in ["enable touch sensitivity", "boot into safe mode", "asdfqwerzxcv", ""]:
        m = r.resolve(d)
        if m.entry:
            assert m.entry["deeplink"] in CTX.catalog_uris or m.entry["deeplink"] == DUMMY


def test_actionable_is_a_verbatim_copy():
    r = DeeplinkResolver("data/deeplinks.json")
    e = next(x for x in r.entries if x["id"] == "DL-0542")
    a = build_actionable(e)
    assert a == {"deeplink": e["deeplink"], "description": e["description"],
                 "message": e["message"], "originalType": e["originalType"]}
    assert "classes" not in a


def test_validation_is_verbatim_or_none():
    r = DeeplinkResolver("data/deeplinks.json")
    e = next(x for x in r.entries if x["id"] == "DL-0542")
    assert build_validation(e) == e["validation"]
    assert build_validation(r.dummy) is None


def test_lexical_labels_precision():
    """Stage 3 receives catalog-register descriptors; this is its real workload."""
    from engine.embed import get_encoder
    r = DeeplinkResolver("data/deeplinks.json", encoder=get_encoder())
    labels = json.load(open("tests/fixtures/deeplink_labels.json"))
    ok = 0
    for L in labels:
        m = r.resolve(L["descriptor"], tau=0.45)
        got = "DUMMY" if (not m.entry or m.entry["deeplink"] == DUMMY) else m.entry["id"]
        ok += got == L["expect"]
    assert ok / len(labels) >= 0.95


# ----------------------------------------------------------------- segmentation
def test_segment_indices_are_stable_and_expandable():
    rows = json.load(open("data/siis_responses.json"))["responses"]
    doc = next(r for r in rows if r["siis_response"]["title"].startswith("Blank"))
    s = segment(doc["siis_response"]["content"], doc["siis_response"]["title"])
    assert len(s.candidates) >= 10
    assert s.texts([1, 2]) == [s.candidates[0].text, s.candidates[1].text]
    assert s.texts([9999]) == []


def test_narration_is_not_actionable():
    assert not is_actionable("There are a few common ways screens can be damaged:")
    assert not is_actionable("Bleeding pixels are typically caused by an impact.")
    assert is_actionable("Tap Storage, then tap Clear cache.")
    assert is_actionable("Now, please connect your phone to its charger.")


# ----------------------------------------------------------------- normalize
def test_normalize_collapses_device_models():
    a = normalize("My Galaxy S22 screen is blank")
    b = normalize("My Galaxy S24 Ultra screen is blank")
    assert a == b


def test_normalize_strips_list_artifacts():
    assert normalize('1. "My screen is cracked."').startswith("screen is cracked")


# ----------------------------------------------------------------- variations
def test_variations_satisfy_g11():
    v = generate("My Galaxy S22 screen is completely blank and will not turn on")
    assert 8 <= len(v) <= 10
    assert len(set(v)) == len(v)


# ----------------------------------------------------------------- artifacts
def test_compiled_library_is_valid():
    lib = json.load(open("artifacts/plan_library.json"))["plans"]
    assert len(lib) == 11
    for p in lib:
        env = {"query": p["canonical_query"],
               "query_variations": p["query_variations"],
               "response": {"contexts": [p["plan"]]}}
        assert blocking(validate_envelope(env, CTX)) == [], p["doc"]


def test_no_manual_action_carries_a_deeplink():
    lib = json.load(open("artifacts/plan_library.json"))["plans"]
    for p in lib:
        for a in p["plan"]["actions"]:
            if a["category"] == "manual":
                for g in a["stepGroups"]:
                    assert g["actionableDeeplink"] is None


def test_every_step_traces_to_source_text():
    """ADR-001: no step may exist that is absent from the SIIS document."""
    rows = json.load(open("data/siis_responses.json"))["responses"]
    docs = {}
    for r in rows:
        docs.setdefault(r["siis_response"]["title"], r["siis_response"]["content"])
    lib = json.load(open("artifacts/plan_library.json"))["plans"]
    for p in lib:
        allowed = {c.text for c in segment(docs[p["doc"]], p["doc"]).candidates}
        for a in p["plan"]["actions"]:
            for g in a["stepGroups"]:
                for s in g["steps"]:
                    assert s in allowed, f"{p['doc']}: untraceable step {s!r}"


# ----------------------------------------------------------------- determinism
def test_identical_input_is_byte_identical(tmp_path):
    """PDF 6.1: consistent plans for identical inputs."""
    from engine.cache import PlanCache
    c = PlanCache()
    q = "My Galaxy S22 screen is completely blank"
    outs = [json.dumps(c.lookup(q)[0]["plan"], sort_keys=True) for _ in range(5)]
    assert len(set(outs)) == 1


def test_semantically_identical_inputs_agree():
    """PDF 6.1: consistent plans for *semantically* identical inputs."""
    from engine.cache import PlanCache
    c = PlanCache()
    pairs = [
        ("My Galaxy S22 screen is completely blank",
         "my galaxy s22 screen is completely blank."),
        ("My Galaxy S24 Ultra screen is completely black and won't turn on",
         "My Galaxy S22 screen is completely black and won't turn on"),
    ]
    for a, b in pairs:
        pa, pb = c.lookup(a)[0], c.lookup(b)[0]
        assert pa is not None and pb is not None
        assert pa["id"] == pb["id"], f"{a!r} vs {b!r}"


def test_deeplink_resolution_is_deterministic():
    from engine.embed import get_encoder
    r = DeeplinkResolver("data/deeplinks.json", encoder=get_encoder())
    for d in ["enable touch sensitivity", "open the navigation bar settings page"]:
        ids = {(r.resolve(d).entry or {}).get("id") for _ in range(5)}
        assert len(ids) == 1


def test_compiled_artifacts_are_reproducible(tmp_path):
    """ADR-003: artifacts must rebuild byte-for-byte from a clean checkout.

    Compiles into a temp directory. An earlier version of this test recompiled
    into artifacts/ in place, so running the suite silently rewrote the shipped
    artifacts - which is exactly how a corrupted rebuild escaped notice.
    """
    import hashlib
    import os
    import subprocess

    before = hashlib.sha256(open("artifacts/plan_library.json", "rb").read()).hexdigest()
    env = dict(os.environ, PRISM_ARTIFACT_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, "scripts/compile_plans.py"],
                       capture_output=True, env=env, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    after = hashlib.sha256((tmp_path / "plan_library.json").read_bytes()).hexdigest()
    assert before == after, "compile_plans.py is not reproducible"
    # The shipped artifacts must be untouched by running the test.
    assert hashlib.sha256(open("artifacts/plan_library.json", "rb").read()).hexdigest() == before


def test_anchor_drift_is_detected(tmp_path):
    """A repointed step reference must fail the build, not pass silently.

    Step indices are positions in the segmenter output; changing is_actionable
    renumbers them. Without anchors a skeleton would keep building, selecting
    different steps than it was authored against.
    """
    import hashlib

    from scripts.compile_plans import AnchorDrift, _check_anchors

    good = "Navigate to and open Settings."
    g = {"steps": [1], "anchors": [hashlib.sha1(good.encode()).hexdigest()[:8]]}
    _check_anchors(g, [good], "doc", "action")          # matching: no raise

    with pytest.raises(AnchorDrift):
        _check_anchors(g, ["Tap something completely different."], "doc", "action")


def test_matching_never_uses_the_masked_uri():
    """PDF 7.4: masked URIs are opaque tokens; match on metadata only."""
    r = DeeplinkResolver("data/deeplinks.json")
    for i, e in enumerate(r.entries):
        assert e["deeplink"] not in r.searchable[i]
        assert "bixby://" not in r.searchable[i]
    # A query made of URI fragments must not retrieve its own entry.
    target = next(e for e in r.entries if e["id"] == "DL-0542")
    frag = target["deeplink"].rsplit("/", 1)[-1]
    m = r.resolve(frag)
    assert m.entry is None or m.entry["deeplink"] == DUMMY


def test_corrupt_catalog_metadata_is_not_copied():
    """DL-0294/0295 carry message 'Offurl'/'Onurl'; usable URI, unusable metadata."""
    r = DeeplinkResolver("data/deeplinks.json")
    for i, e in enumerate(r.entries):
        if e["id"] in {"DL-0294", "DL-0295"}:
            assert e["message"] not in r.searchable[i]


def test_encoder_revision_is_pinned():
    """An unpinned encoder would silently move every cached vector.

    The thresholds (TAU_HIT, TAU_LINK, COS_FLOOR/CEIL) are calibrated against
    this specific model's geometry, so tracking 'main' would invalidate them
    without any test failing.
    """
    import re

    from engine.embed import MODEL_REV

    assert re.fullmatch(r"[0-9a-f]{40}", MODEL_REV), f"not a pinned sha: {MODEL_REV}"

    meta = glob.glob("vendor/bge-small-en-v1.5/.cache/huggingface/download/*.metadata")
    if not meta:
        pytest.skip("vendor/ not present (regenerate with scripts/vendor_models.py)")
    shas = {open(m).readline().strip() for m in meta}
    assert MODEL_REV in shas, f"pin {MODEL_REV} not among vendored revisions {shas}"
