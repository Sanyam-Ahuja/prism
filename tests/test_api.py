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
    orig = m._state["cache"].lookup
    m._state["cache"].lookup = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        r = client.post("/v1/troubleshoot", json={"query": "screen blank"})
        d = _is_envelope(r.text)               # the assertion that matters
        assert d["meta"]["fallback"] == "no_match"
    finally:
        m._state["cache"].lookup = orig


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
