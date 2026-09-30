"""Demo page for screen recordings (docs/DEMO.md).

Mounted only when PRISM_DEMO=1, so the graded surface stays exactly PDF
section 5: POST /v1/troubleshoot and GET /health. The page is a client of that
public endpoint like any other; nothing here touches the engine.
"""
from __future__ import annotations

import glob
import json
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
router = APIRouter(include_in_schema=False)


def _load(rel: str):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def _articles() -> dict:
    """Reference articles the page can send as `siis_response`, keyed by id."""
    out = {}
    for r in _load("data/siis_responses.json")["responses"]:
        s = r["siis_response"]
        out.setdefault("siis:" + s["title"], {
            "title": s["title"], "content": s["content"],
            "source": "Supplied SIIS article"})
    # Probes are team-written, never compiled into the cache: a genuinely unseen
    # issue for the cold path, labelled as synthetic wherever the page shows it.
    for path in sorted(glob.glob(os.path.join(ROOT, "tests/fixtures/probes/*.json"))):
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        out["probe:" + os.path.splitext(os.path.basename(path))[0]] = {
            "title": s["title"], "content": s["content"],
            "source": "Synthetic test article (team-written, not Samsung content)"}
    return out


@router.get("/demo")
def page() -> HTMLResponse:
    with open(os.path.join(ROOT, "demo", "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/demo/presets")
def presets() -> JSONResponse:
    arts = _articles()
    scenarios = _load("demo/scenarios.json")["scenarios"]
    for s in scenarios:
        base = arts.get(s.get("article") or "")
        if base and s.get("inject"):
            # Planted text for the zero-leakage scene: a URL the output must not carry.
            s["article_override"] = dict(base, content=base["content"] + "\n" + s["inject"],
                                         source=base["source"] + " + planted URL")
    return JSONResponse({"scenarios": scenarios,
                         "articles": [dict(id=k, **v) for k, v in arts.items()]})
