"""Build-time pipeline: skeletons -> validated plan library + cache vectors.

Everything here is deterministic. The only non-deterministic input is
build/skeletons.json, which is the captured build-tier LLM output. Run:

    python scripts/compile_plans.py

Fails the build on any unresolved blocking gate: a dirty artifact must never ship.
"""
from __future__ import annotations

import hashlib
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from engine.assemble import (categorize, fit_description, fit_title, make_goal,
                             merge_duplicate_screens, order_actions, title_case)
from engine.deeplink import DUMMY, DeeplinkResolver, build_actionable, build_validation
from engine.embed import get_encoder, QUERY_PREFIX
from engine.segment import segment
from validators.gates import Ctx, blocking, load_ctx, validate_envelope
from validators.scrub import scrub_deep

ART = "artifacts"
TAU_LINK = float(os.environ.get("PRISM_TAU_LINK", "0.52"))


class AnchorDrift(RuntimeError):
    """A skeleton's step indices no longer point at the steps they were authored against."""


def _check_anchors(g: dict, steps: list[str], doc: str, action: str) -> None:
    """Fail the build if segmentation renumbering repointed a step reference.

    Step indices are positions in the segmenter's candidate list. Changing
    is_actionable inserts or removes candidates and shifts every later index, so
    without this check a skeleton would silently start selecting different steps.
    """
    anchors = g.get("anchors")
    if not anchors:
        return
    actual = [hashlib.sha1(t.encode()).hexdigest()[:8] for t in steps]
    if actual != anchors:
        raise AnchorDrift(
            f"{doc} / {action}: step indices {g['steps']} no longer resolve to the "
            f"authored steps (anchors {anchors} != {actual}). Re-derive the skeleton "
            f"against the current segmenter output.")


def build_action(sk_action: dict, seg, resolver: DeeplinkResolver) -> dict | None:
    """Expand one skeleton action into a schema-shaped Action."""
    groups = []
    all_text = []
    for g in sk_action["stepGroups"]:
        steps = seg.texts(g["steps"])
        if not steps:
            continue
        _check_anchors(g, steps, seg.title, sk_action.get("name", "?"))
        all_text.append(" ".join(steps))
        groups.append({"screen": g.get("screen"), "steps": steps})
    if not groups:
        return None

    # The extractor proposes; the keyword table decides (DATA_CONTRACT 3).
    category = categorize(sk_action.get("category", "manual"), " ".join(all_text))

    out_groups = []
    for g in groups:
        adl = vdl = None
        # G13: a manual action can never carry an actionable deeplink.
        if category != "manual" and g["screen"]:
            m = resolver.resolve(g["screen"], tau=TAU_LINK)
            if m.entry is not None:
                if m.entry["deeplink"] == DUMMY:
                    adl = build_actionable(
                        m.entry,
                        fallback_desc=f"Open the {g['screen'].strip()} on the device",
                        fallback_msg=title_case(g["screen"].strip())[:60],
                    )
                else:
                    adl = build_actionable(m.entry)
                    vdl = build_validation(m.entry)
        out_groups.append({
            "steps": g["steps"],
            "actionableDeeplink": adl,
            "validationDeeplink": vdl,
        })

    return {
        "actionName": title_case(sk_action["name"]),
        "description": fit_description(sk_action["benefit"]),
        "stepGroups": out_groups,
        "category": category,
    }


def build_plan(sk: dict, seg, resolver: DeeplinkResolver) -> dict:
    actions = [a for a in (build_action(x, seg, resolver) for x in sk["actions"]) if a]
    actions = merge_duplicate_screens(actions)
    actions = order_actions(actions)
    return {
        "goal": make_goal(sk["topic"]),
        "title": fit_title(sk["title"]),
        "score": round(float(sk.get("score", 0.92)), 3),
        "actions": actions,
    }


def main() -> int:
    rows = json.load(open("data/siis_responses.json"))["responses"]
    docs, canon, all_queries = {}, {}, {}
    for r in rows:
        t = r["siis_response"]["title"]
        docs.setdefault(t, r["siis_response"]["content"])
        canon.setdefault(t, r["original_query"])
        # Every known query routing to a document is a legitimate cache seed, not
        # just the first: 6 of the 20 map to "Blank or black display" alone.
        all_queries.setdefault(t, []).append(r["original_query"])

    skeletons = json.load(open("build/skeletons.json"))["skeletons"]
    resolver = DeeplinkResolver("data/deeplinks.json", encoder=get_encoder())
    ctx: Ctx = load_ctx("data/deeplinks.json")

    library, cache_rows, failures = [], [], []
    dummy_n = auto_n = 0

    for sk in skeletons:
        doc = sk["doc"]
        if doc not in docs:
            failures.append(f"{doc}: no matching SIIS document")
            continue
        seg = segment(docs[doc], doc)
        plan = build_plan(sk, seg, resolver)

        for a in plan["actions"]:
            if a["category"] == "auto":
                auto_n += 1
                if any((g.get("actionableDeeplink") or {}).get("deeplink") == DUMMY
                       for g in a["stepGroups"]):
                    dummy_n += 1

        env = scrub_deep({
            "query": canon[doc],
            "query_variations": sk["query_variations"],
            "response": {"contexts": [plan]},
            "meta": {"latency_ms": 0, "cache_hit": True, "model": None,
                     "cost_usd": 0.0, "fallback": None},
        })
        v = validate_envelope(env, ctx)
        bad = blocking(v)
        if bad:
            failures.append(f"{doc}: " + "; ".join(f"[{x.gate}] {x.path}: {x.message}" for x in bad[:4]))
            continue

        pid = len(library)
        library.append({"id": pid, "doc": doc, "canonical_query": canon[doc],
                        "query_variations": sk["query_variations"],
                        "plan": plan})
        for q in all_queries[doc]:
            cache_rows.append((pid, q))
        for var in sk["query_variations"]:
            cache_rows.append((pid, var))

    if failures:
        print("BUILD FAILED - blocking gate violations:\n")
        for f in failures:
            print("  " + f)
        return 1

    # Embed canonical queries and every variation: variations are cache seeds (ADR-004).
    from engine.normalize import normalize
    enc = get_encoder()
    texts = [QUERY_PREFIX + normalize(t) for _, t in cache_rows]
    vecs = enc.encode(texts, normalize_embeddings=True).astype(np.float32)

    os.makedirs(ART, exist_ok=True)
    json.dump({"count": len(library), "plans": library},
              open(f"{ART}/plan_library.json", "w"), indent=1, sort_keys=True)
    np.save(f"{ART}/cache_vectors.npy", vecs)
    json.dump({"rows": [{"plan_id": p, "text": t} for p, t in cache_rows],
               "tau_link": TAU_LINK, "dim": int(vecs.shape[1])},
              open(f"{ART}/cache_manifest.json", "w"), indent=1)

    # Two readings of Appendix C section 1 ("auto actions carrying valid
    # actionable deeplink >= 90%"). bixby://dummy_positive IS a catalog entry and
    # is the sanctioned answer for a screen the catalog does not index (PDF
    # section 3), so under the literal reading it counts. The stricter reading
    # wants a specific screen. We report both rather than pick the flattering one.
    specific = 100 * (auto_n - dummy_n) / max(auto_n, 1)
    valid = 100.0 if auto_n else 0.0
    print(f"OK  plans={len(library)}  cache_vectors={vecs.shape}  auto_actions={auto_n}")
    print(f"    deeplink coverage: {specific:.0f}% specific catalog entry, "
          f"{valid:.0f}% valid URI (incl. {dummy_n} sanctioned dummy_positive)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
