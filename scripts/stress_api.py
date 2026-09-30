"""End-to-end stress test over real HTTP (PDF section 8, Phase 4).

Starts the API as a separate process so cold start is measured the way an
operator sees it: process launch -> /health 200 with status "ok". Then drives
every execution path over the wire and validates every response body against
the schema gates. Latencies here include HTTP, the cache lookup in front of the
cold path and gate validation, all of which scripts/bench.py (in-process)
excludes.

    python scripts/stress_api.py --cold 33
"""
import argparse, concurrent.futures as cf, json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from scripts.bench import pct
from validators.gates import blocking, load_ctx, validate_envelope

CTX = load_ctx("data/deeplinks.json")
# Deliberately unlike every cache seed, so each request takes the cold path.
COLD_QUERY = "need help fixing this problem on my phone"


def call(client, body):
    t0 = time.perf_counter()
    r = client.post("/v1/troubleshoot", json=body)
    ms = (time.perf_counter() - t0) * 1000
    raw = r.text
    try:
        env = r.json()
    except ValueError:
        env = None
    pure = raw.lstrip().startswith("{") and "```" not in raw
    valid = env is not None and r.status_code == 200 and not blocking(validate_envelope(env, CTX))
    return ms, r.status_code, env, pure, valid


def report(name, rows, target):
    lat = [r[0] for r in rows]
    valid = sum(r[4] for r in rows)
    pure = sum(r[3] for r in rows)
    p50, p95 = pct(lat, 50), pct(lat, 95)
    ok = "PASS" if p95 <= target else "FAIL"
    print(f"{name:40s} {len(rows):4d} {p50:8.1f}ms {p95:8.1f}ms {target:7d}ms  {ok}"
          f"   schema-valid {valid}/{len(rows)}  pure-JSON {pure}/{len(rows)}")
    return valid, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--hot", type=int, default=40)
    ap.add_argument("--cold", type=int, default=33)
    ap.add_argument("--burst", type=int, default=200, help="concurrent hot requests")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--server-log", default="reports/stress_api_server.log")
    a = ap.parse_args()

    exact = [l.strip() for l in open("data/input.txt") if l.strip()]
    para = [h["q"] for h in json.load(open("tests/fixtures/heldout_paraphrases.json"))]
    negs = json.load(open("tests/fixtures/heldout_negatives.json"))
    docs, seen = [], set()
    for r in json.load(open("data/siis_responses.json"))["responses"]:
        if r["siis_response"]["title"] not in seen:
            seen.add(r["siis_response"]["title"]); docs.append(r["siis_response"])

    os.makedirs(os.path.dirname(a.server_log) or ".", exist_ok=True)
    log = open(a.server_log, "w")
    t_launch = time.perf_counter()
    # The cold requests below reuse the 11 library articles. With the article
    # cache on (ADR-019) the API answers those from their compiled plans without
    # the model, so it is switched off here to time the model path itself.
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1",
         "--port", str(a.port)], stdout=log, stderr=subprocess.STDOUT,
        env=dict(os.environ, PYTHONUTF8="1", PRISM_ARTICLE_CACHE="0"))
    base = f"http://127.0.0.1:{a.port}"
    try:
        # ---- cold start: launch -> first 200, and -> status "ok" (extractor up)
        first_200 = ready = None
        health = {}
        # One client for polling: a fresh one per poll costs ~0.5 s on Windows,
        # which would be charged to the cold-start figure.
        poll = httpx.Client(timeout=2)
        while time.perf_counter() - t_launch < 300:
            try:
                h = poll.get(f"{base}/health")
                if h.status_code == 200 and first_200 is None:
                    first_200 = time.perf_counter() - t_launch
                if h.status_code == 200 and h.json().get("status") == "ok":
                    ready = time.perf_counter() - t_launch
                    health = h.json()
                    break
            except httpx.HTTPError:
                pass
            if proc.poll() is not None:
                print(f"server exited with code {proc.returncode}; see {a.server_log}")
                return 1
            time.sleep(0.1)
        if ready is None:
            print("server never reported status ok within 300 s")
            return 1
        print(f"cold start: process launch -> /health 200 {first_200:.1f} s, "
              f"-> status ok {ready:.1f} s")
        print(f"health: {json.dumps(health)}\n")

        client = httpx.Client(base_url=base, timeout=60)
        print(f"{'path':40s} {'n':>4s} {'P50':>10s} {'P95':>10s} {'target':>9s}"
              f"          (client-side wall time, over HTTP)")
        print("-" * 118)
        totals = []

        rows = [call(client, {"query": exact[i % len(exact)]}) for i in range(a.hot)]
        totals.append(report("Cache hit - exact query match", rows, 300))
        hits = sum(1 for r in rows if r[2] and r[2]["meta"]["cache_hit"])

        rows = [call(client, {"query": para[i % len(para)]}) for i in range(a.hot)]
        totals.append(report("Cache hit - unseen paraphrase (any path)", rows, 300))

        rows = [call(client, {"query": q}) for q in negs]
        fb = sum(1 for r in rows if r[2] and r[2]["meta"].get("fallback") == "no_siis_context")
        totals.append(report("Miss, no siis_response (fallback)", rows, 300))

        # First cold request after startup is reported apart: it may pay the
        # extractor model load, which is a deployment property, not steady state.
        first = call(client, {"query": COLD_QUERY, "siis_response": docs[0]})
        rows = [call(client, {"query": COLD_QUERY, "siis_response": docs[i % len(docs)]})
                for i in range(a.cold)]
        totals.append(report("Cold query - full pipeline", rows, 8000))
        cold_true = sum(1 for r in rows if r[2] and not r[2]["meta"]["cache_hit"])
        planned = sum(1 for r in rows if r[2] and r[2]["response"]["contexts"])
        server = [r[2]["meta"]["latency_ms"] for r in rows if r[2]]

        # Concurrent burst on the hot path: does it hold 300 ms under load?
        mix = [exact[i % len(exact)] if i % 2 else para[i % len(para)] for i in range(a.burst)]
        t0 = time.perf_counter()
        with cf.ThreadPoolExecutor(a.workers) as ex:
            rows = list(ex.map(lambda q: call(client, {"query": q}), mix))
        wall = time.perf_counter() - t0
        totals.append(report(f"Hot burst, {a.workers} concurrent clients", rows, 300))

        # Malformed input must still get a JSON body, never a crash or HTML.
        bad = [client.post("/v1/troubleshoot", json=b) for b in
               ({"query": ""}, {"query": "x" * 5000}, {"nope": 1})]
        bad_json = sum(1 for r in bad if r.headers.get("content-type", "").startswith("application/json"))

        v = sum(t[0] for t in totals); n = sum(t[1] for t in totals)
        print(f"\nfirst cold request after startup : {first[0]:.0f} ms "
              f"(contexts={len(first[2]['response']['contexts']) if first[2] else 0})")
        print(f"cold requests that missed cache  : {cold_true}/{len(server)}   "
              f"returned a plan: {planned}/{len(server)}")
        print(f"cold server-side P50/P95         : {pct(server, 50):.0f} / {pct(server, 95):.0f} ms")
        print(f"exact-match requests served hot  : {hits}/{a.hot}")
        print(f"no-siis misses given the fallback: {fb}/{len(negs)}")
        print(f"hot burst throughput             : {a.burst / wall:.0f} req/s "
              f"({a.burst} requests, {a.workers} workers)")
        print(f"malformed payloads answered JSON : {bad_json}/{len(bad)} "
              f"(status {[r.status_code for r in bad]})")
        print(f"SCHEMA-VALID OVERALL             : {v}/{n} = {100 * v / n:.1f}%")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
