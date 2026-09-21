"""REST API. See docs/ARCHITECTURE.md section 11 and PDF section 5.

Responses are pure JSON: no markdown wrapping, no preamble (PDF 4.2.4).
"""
from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from engine.cache import TAU_HIT, PlanCache
from validators.gates import Ctx, blocking, load_ctx, validate_envelope

app = FastAPI(title="Smart Guided Troubleshooting Engine", version="1.0")

_state: dict[str, Any] = {"cache": None, "ctx": None, "cold": None, "ready": False}


class TroubleshootRequest(BaseModel):
    query: str
    siis_response: Optional[Any] = None


@app.on_event("startup")
def _startup() -> None:
    # Everything loads here, off the request clock.
    _state["cache"] = PlanCache()
    _state["ctx"] = load_ctx("data/deeplinks.json")
    from engine.cold import ColdPath
    _state["cold"] = ColdPath(_state["cache"].encoder)
    _state["ready"] = True


@app.get("/health")
def health() -> JSONResponse:
    """200 only once every subsystem is initialized, not merely that we are up."""
    c = _state.get("cache")
    ok = bool(
        _state.get("ready")
        and c is not None
        and len(c.plans) > 0
        and c.vectors.size > 0
        and _state.get("ctx") is not None
        and _state["cold"].available()
    )
    body = {
        "status": "ok" if ok else "initializing",
        "plans": len(c.plans) if c else 0,
        "cache_vectors": int(c.vectors.shape[0]) if c else 0,
        "catalog_uris": len(_state["ctx"].catalog_uris) if _state.get("ctx") else 0,
        "extractor": _state["cold"].model if _state.get("cold") else None,
    }
    return JSONResponse(body, status_code=200 if ok else 503)


def _envelope(query, variations, contexts, t0, cache_hit, model, cost, fallback,
              tokens=None):
    return {
        "query": query,
        "query_variations": variations,
        "response": {"contexts": contexts},
        "meta": {
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            "cache_hit": cache_hit,
            "model": model,
            "cost_usd": cost,
            # PDF 6.3 requires token utilisation to be tracked, not just cost.
            "tokens": tokens or {"prompt": 0, "completion": 0},
            "fallback": fallback,
        },
    }


@app.post("/v1/troubleshoot")
def troubleshoot(req: TroubleshootRequest) -> JSONResponse:
    t0 = time.perf_counter()
    cache: PlanCache = _state["cache"]
    ctx: Ctx = _state["ctx"]

    plan, sim = cache.lookup(req.query, tau=TAU_HIT)
    if plan is not None:
        ctxs = [dict(plan["plan"], score=round(min(1.0, sim), 3))]
        return JSONResponse(_envelope(req.query, plan["query_variations"], ctxs,
                                      t0, True, None, 0.0, None))

    # Cache miss with no reference text: nothing to ground a plan in.
    if not req.siis_response:
        return JSONResponse(_envelope(req.query, [], [], t0, False, None, 0.0,
                                      "no_siis_context"))

    cold = _state["cold"]
    result = cold.run(req.query, req.siis_response)
    if result is None or not result["contexts"]:
        return JSONResponse(_envelope(req.query, result["variations"] if result else [],
                                      [], t0, False, cold.model,
                                      result["cost_usd"] if result else 0.0, "no_match",
                                      tokens=(result or {}).get("tokens")))

    env = _envelope(req.query, result["variations"], result["contexts"], t0,
                    False, cold.model, result["cost_usd"], None,
                    tokens=result.get("tokens"))
    # Never emit an invalid payload: degrade to no_match instead.
    if blocking(validate_envelope(env, ctx)):
        return JSONResponse(_envelope(req.query, result["variations"], [], t0,
                                      False, cold.model, result["cost_usd"], "no_match",
                                      tokens=result.get("tokens")))
    return JSONResponse(env)
