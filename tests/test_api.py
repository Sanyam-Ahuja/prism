"""API contract and error-boundary tests.

The PDF makes two promises that only hold if the failure paths are tested:
pure-JSON delivery (4.2.4) and graceful fallbacks (8 Phase 4). Before these
tests, an internal exception propagated uncaught and returned a 500 HTML body.
"""
import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

import api.main as m
from api.main import app
from validators.gates import blocking, g0_no_urls, validate_envelope


@pytest.fixture(scope="module")
def client():
    # raise_server_exceptions=False so we observe what a real client would.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _is_envelope(body: str) -> dict:
    d = json.loads(body)                       # must parse: no fence, no preamble
    assert set(d) >= {"query", "query_variations", "response", "meta"}
    assert "contexts" in d["response"]
    assert set(d["meta"]) >= {"latency_ms", "cache_hit", "model", "cost_usd", "fallback"}
    return d


# ------------------------------------------------------------------ happy path
def test_health_reports_subsystems(client):
    r = client.get("/health")
    assert r.status_code in (200, 503)
    b = r.json()
    assert b["status"] in ("ok", "degraded", "down")
    assert b["plans"] == 11
    assert b["catalog_uris"] == 578


def test_demo_page_is_off_by_default(client):
    """The recording aid (docs/DEMO.md) must not widen the graded surface of PDF §5."""
    assert client.get("/demo").status_code == 404
    assert client.get("/demo/presets").status_code == 404


def test_demo_scenes_reference_real_articles():
    """A typo in demo/scenarios.json would otherwise surface mid-recording."""
    from api.demo import presets
    p = json.loads(presets().body)
    ids = {a["id"] for a in p["articles"]}
    for s in p["scenarios"]:
        assert s["query"].strip()
        assert s.get("article") is None or s["article"] in ids, s["id"]
        assert s["expect"] in ("hit", "cold", "fallback")
        if s.get("inject"):
            assert s["inject"] in s["article_override"]["content"]


def test_cache_hit_returns_plan(client):
    r = client.post("/v1/troubleshoot",
                    json={"query": "my phone display is totally black and wont switch on"})
    assert r.status_code == 200
    d = _is_envelope(r.text)
    assert d["meta"]["cache_hit"] is True
    assert d["meta"]["cost_usd"] == 0.0
    assert len(d["response"]["contexts"]) == 1
    assert 8 <= len(d["query_variations"]) <= 10


def test_no_siis_context_fallback(client):
    r = client.post("/v1/troubleshoot",
                    json={"query": "my bluetooth headphones will not pair with the car"})
    assert r.status_code == 200
    d = _is_envelope(r.text)
    assert d["response"]["contexts"] == []
    assert d["meta"]["fallback"] == "no_siis_context"


def test_response_is_bare_json(client):
    r = client.post("/v1/troubleshoot", json={"query": "screen blank"})
    assert r.text.lstrip()[0] == "{"
    assert "```" not in r.text


# -------------------------------------------------------------- error boundary
def test_internal_error_still_returns_json_envelope(client):
    """Regression: this previously propagated uncaught and returned a 500 body."""
    orig = m._state["cache"].lookup_all
    m._state["cache"].lookup_all = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        r = client.post("/v1/troubleshoot", json={"query": "screen blank"})
        d = _is_envelope(r.text)               # the assertion that matters
        assert d["meta"]["fallback"] == "no_match"
    finally:
        m._state["cache"].lookup_all = orig


def test_cold_path_failure_degrades_to_no_match(client):
    cold = m._state["cold"]
    orig = cold.run
    cold.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ollama down"))
    try:
        r = client.post("/v1/troubleshoot",
                        json={"query": "a totally novel complaint about the camera",
                              "siis_response": "## Steps\nTap Settings.\nTap Camera."})
        assert r.status_code == 200
        d = _is_envelope(r.text)
        assert d["response"]["contexts"] == []
        assert d["meta"]["fallback"] == "no_match"
    finally:
        cold.run = orig


def test_cold_path_deadline_is_enforced(client):
    """A stalled extractor must not overrun the 8 s budget (PDF 6.3)."""
    cold = m._state["cold"]
    orig, orig_deadline = cold.run, m.DEADLINE_S
    cold.run = lambda *a, **k: time.sleep(30)
    m.DEADLINE_S = 1.5
    try:
        t0 = time.perf_counter()
        r = client.post("/v1/troubleshoot",
                        json={"query": "novel complaint", "siis_response": "## S\nTap Settings."})
        elapsed = time.perf_counter() - t0
        assert elapsed < 5.0, f"deadline not enforced: {elapsed:.1f}s"
        d = _is_envelope(r.text)
        assert d["meta"]["fallback"] == "no_match"
    finally:
        cold.run, m.DEADLINE_S = orig, orig_deadline


# ------------------------------------------------------------- request limits
def test_oversized_siis_response_is_rejected(client):
    r = client.post("/v1/troubleshoot",
                    json={"query": "x", "siis_response": "A" * 2_000_000})
    assert r.status_code == 422


def test_oversized_query_is_rejected(client):
    r = client.post("/v1/troubleshoot", json={"query": "A" * 5000})
    assert r.status_code == 422


def test_empty_query_is_rejected(client):
    assert client.post("/v1/troubleshoot", json={"query": ""}).status_code == 422


# ------------------------------------------------------ articles (ADR-019)
def _library_article(doc_prefix):
    rows = json.load(open("data/siis_responses.json", encoding="utf-8"))["responses"]
    return next(r["siis_response"] for r in rows if r["siis_response"]["title"].startswith(doc_prefix))


def _cold_stub(calls):
    """A cold path that records its calls and returns a valid plan."""
    plan = next(p for p in m._state["cache"].plans.values() if p["doc"].startswith("Touchscreen"))

    def run(query, siis):
        calls.append(query)
        return {"contexts": [dict(plan["plan"], score=0.78)], "variations": plan["query_variations"],
                "cost_usd": 0.0, "tokens": {"prompt": 600, "completion": 270}}
    return run


def test_known_article_gets_its_compiled_plan_whatever_the_query(client):
    """PDF 4.2.3: with reference text supplied, the plan derives from that text."""
    cold, calls = m._state["cold"], []
    orig, cold.run = cold.run, _cold_stub(calls)
    try:
        body = {"query": "my battery drains really fast",
                "siis_response": _library_article("Blank or black display")}
        d = _is_envelope(client.post("/v1/troubleshoot", json=body).text)
        assert calls == []
        assert d["meta"]["cache_hit"] is True
        assert [c["title"] for c in d["response"]["contexts"]] == ["Blank screen display"]
        # The same article as a bare string, reflowed: still the same document.
        body["siis_response"] = "  " + body["siis_response"]["content"].replace(" ", "\n", 3)
        d = _is_envelope(client.post("/v1/troubleshoot", json=body).text)
        assert calls == [] and d["response"]["contexts"][0]["title"] == "Blank screen display"
    finally:
        cold.run = orig


def test_repeated_article_gets_the_same_plan_without_the_model(client):
    """PDF 6.1: the model's prompt cache made 4 of 11 repeats differ."""
    cold, calls = m._state["cold"], []
    orig, cold.run = cold.run, _cold_stub(calls)
    article = "## Steps\nOpen Settings.\nTap Display.\nTap Touch sensitivity. (test_repeated_article)"
    try:
        first = _is_envelope(client.post("/v1/troubleshoot", json={
            "query": "taps register late", "siis_response": article}).text)
        again = _is_envelope(client.post("/v1/troubleshoot", json={
            "query": "the touchscreen lags", "siis_response": article}).text)
        assert calls == ["taps register late"]
        assert first["meta"]["cache_hit"] is False and again["meta"]["cache_hit"] is True
        assert again["response"]["contexts"] == first["response"]["contexts"]
        assert again["meta"]["tokens"] == {"prompt": 0, "completion": 0}
    finally:
        cold.run = orig


def test_article_outranks_a_resembling_query(client):
    """A complaint that resembles a cached plan must not override its own article."""
    cold, calls = m._state["cold"], []
    orig, cold.run = cold.run, _cold_stub(calls)
    try:
        client.post("/v1/troubleshoot", json={
            "query": "my galaxy s22 touchscreen is laggy and delayed",
            "siis_response": "## Battery\nOpen Settings.\nTap Battery. (test_article_outranks)"})
        assert calls == ["my galaxy s22 touchscreen is laggy and delayed"]
    finally:
        cold.run = orig


# --------------------------------------------- cache scope and multi-intent
def test_out_of_scope_complaint_falls_back(client):
    """PDF section 1's own example used to hit the touchscreen plan (ADR-020)."""
    d = _is_envelope(client.post("/v1/troubleshoot",
                                 json={"query": "My phone got slow after the update"}).text)
    assert d["response"]["contexts"] == []
    assert d["meta"]["fallback"] == "no_siis_context"


def test_two_problems_get_two_plans(client):
    """ADR-021: one plan per separate problem, first-mentioned first."""
    q = ("My Galaxy phone's screen is completely cracked, it's a total crack and I can't use the device. "
         "Also, my Galaxy S22 screen inputs are delayed and the touch responsiveness is laggy.")
    d = _is_envelope(client.post("/v1/troubleshoot", json={"query": q}).text)
    assert [c["title"] for c in d["response"]["contexts"]] == ["Cracked screen damage",
                                                               "Touch response delay"]
    assert blocking(validate_envelope(d, m._state["ctx"])) == []


# ------------------------------------------------------------- URL hygiene
def test_link_in_the_query_is_not_echoed(client):
    """PDF 4.2.1: the response echoes the complaint, so a link in it must go."""
    q = "my phone display is totally black and wont switch on"
    r = client.post("/v1/troubleshoot", json={"query": f"{q} https://example.com/fix"})
    d = _is_envelope(r.text)
    assert g0_no_urls(d) == []
    assert d["query"] == q
    assert d["meta"]["cache_hit"] is True          # looked up on the cleaned complaint


def test_link_in_the_query_never_reaches_the_cold_path(client):
    """The cold path builds paraphrases from the query. A link there made the URL
    gate discard a valid plan, and the fallback still returned the link."""
    cold = m._state["cold"]
    orig, seen = cold.run, []
    cold.run = lambda q, s: seen.append(q)
    try:
        r = client.post("/v1/troubleshoot",
                        json={"query": "a totally novel complaint about the camera www.example.com/help",
                              "siis_response": "## Steps\nTap Settings.\nTap Camera."})
        assert g0_no_urls(_is_envelope(r.text)) == []
        assert seen == ["a totally novel complaint about the camera"]
    finally:
        cold.run = orig


def test_rejections_do_not_echo_the_payload(client):
    """FastAPI's default 422 repeats the input: its links, and all of an oversized article."""
    for body in ({"query": "https://example.com/help"},                    # nothing left once cleaned
                 {"query": "see https://example.com/help " + "A" * 5000},  # too long
                 {"query": "x", "siis_response": "Visit www.example.com. " + "A" * 300_000}):
        r = client.post("/v1/troubleshoot", json=body)
        assert r.status_code == 422
        assert "example.com" not in r.text and len(r.text) < 1000


# -------------------------------------------------------------- concurrency
def test_hot_path_is_correct_under_concurrency(client):
    """The encoder is shared mutable state reached via asyncio.to_thread.

    P95 was measured single-threaded, so this checks that parallel requests
    return the same answers rather than merely not crashing: a data race in the
    encoder would show up as wrong plans, not exceptions.
    """
    import concurrent.futures as cf

    queries = [
        "my phone display is totally black and wont switch on",
        "my galaxy s22 touchscreen is laggy and delayed",
        "my galaxy phone screen is completely cracked",
    ]
    # Ground truth, measured serially first.
    expected = {}
    for q in queries:
        d = client.post("/v1/troubleshoot", json={"query": q}).json()
        expected[q] = d["response"]["contexts"][0]["title"]

    def call(q):
        r = client.post("/v1/troubleshoot", json={"query": q})
        assert r.status_code == 200
        d = r.json()
        return q, d["response"]["contexts"][0]["title"], d["meta"]["cache_hit"]

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(call, queries * 8))

    assert len(results) == 24
    for q, title, hit in results:
        assert hit is True, f"{q!r} missed the cache under load"
        assert title == expected[q], f"{q!r} returned {title!r}, expected {expected[q]!r}"


def test_concurrent_requests_get_distinct_request_ids(client):
    """Each response must be traceable; a shared id would break log correlation."""
    import concurrent.futures as cf

    def call(_):
        return client.post("/v1/troubleshoot",
                           json={"query": "screen blank"}).headers.get("X-Request-ID")

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        ids = list(ex.map(call, range(16)))
    assert all(ids), "missing X-Request-ID"
    assert len(set(ids)) == len(ids), "request ids collided"


def test_meta_always_carries_the_appendix_b_keys(client):
    """Appendix B's four keys plus the 4.2.3-mandated fallback are non-negotiable."""
    d = client.post("/v1/troubleshoot", json={"query": "screen blank"}).json()
    assert set(d["meta"]) >= {"latency_ms", "cache_hit", "model", "cost_usd", "fallback"}


def test_meta_tokens_can_be_switched_off(client, monkeypatch):
    """ADR-016: PRISM_META_TOKENS=0 restores the Appendix B meta shape."""
    import api.main as m
    monkeypatch.setattr(m, "EMIT_TOKENS", False)
    d = client.post("/v1/troubleshoot", json={"query": "screen blank"}).json()
    assert "tokens" not in d["meta"]
    assert set(d["meta"]) == {"latency_ms", "cache_hit", "model", "cost_usd", "fallback"}
