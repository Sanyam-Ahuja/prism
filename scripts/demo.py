"""One command to a recordable demo (docs/DEMO.md).

Makes sure Ollama is up and the extractor is loaded, starts the API with the
demo page enabled, waits for /health to report ok, warms every execution path
so the first request on camera is not the slow one, then opens the page.

    python scripts/demo.py [--port 8000] [--no-browser]
"""
import argparse, os, shutil, socket, subprocess, sys, time, webbrowser
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from engine.cold import MODEL, OLLAMA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "demo.log")


def say(msg):
    print(msg, flush=True)


def ollama_up():
    try:
        return httpx.get(f"{OLLAMA}/api/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def start_ollama():
    exe = shutil.which("ollama") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    if not os.path.exists(exe):
        return False
    say("  starting `ollama serve` ...")
    kw = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
          if os.name == "nt" else {"start_new_session": True})
    subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    for _ in range(60):
        if ollama_up():
            return True
        time.sleep(1)
    return False


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"

    say("[1/5] Ollama")
    if not (ollama_up() or start_ollama()):
        say("  Ollama is not running and could not be started. Install it from ollama.com,\n"
            "  start it, then run this again.")
        return 1
    names = {m["name"] for m in httpx.get(f"{OLLAMA}/api/tags", timeout=5).json()["models"]}
    if MODEL not in names:
        say(f"  model {MODEL} is not downloaded. Run:  ollama pull {MODEL}")
        return 1

    say(f"[2/5] Loading {MODEL} onto the GPU (kept resident)")
    httpx.post(f"{OLLAMA}/api/generate", json={"model": MODEL, "keep_alive": -1}, timeout=300)

    if port_busy(a.port):
        say(f"  port {a.port} is already in use. Stop that server or pass --port 8010.")
        return 1
    say(f"[3/5] Starting the API on {base} (logs: {os.path.relpath(LOG, ROOT)})")
    env = dict(os.environ, PYTHONUTF8="1", PRISM_DEMO="1", PRISM_EXTRACT_MODEL=MODEL)
    log = open(LOG, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1",
                             "--port", str(a.port)], cwd=ROOT, env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    try:
        t0 = time.perf_counter()
        while True:
            if proc.poll() is not None:
                say(f"  the API exited (code {proc.returncode}); see {LOG}")
                return 1
            try:
                h = httpx.get(f"{base}/health", timeout=2)
                if h.status_code == 200 and h.json().get("status") == "ok":
                    break
            except httpx.HTTPError:
                pass
            if time.perf_counter() - t0 > 240:
                say(f"  /health did not report ok within 240 s; see {LOG}")
                return 1
            time.sleep(1)
        say(f"  healthy after {time.perf_counter() - t0:.0f} s")

        say("[4/5] Warming every path off camera")
        with httpx.Client(base_url=base, timeout=60) as c:
            # The performance article is used by no scene, so warming the cold
            # path with it leaves the scenes' own prompts untouched.
            arts = {x["id"]: x for x in c.get("/demo/presets").json()["articles"]}
            perf = {k: arts["probe:performance"][k] for k in ("title", "content")}
            for label, body in [
                ("semantic", {"query": "my touch screen is slow to respond"}),
                ("fallback", {"query": "warm-up request with no article"}),
                ("cold path", {"query": "warm-up request for the demo", "siis_response": perf}),
            ]:
                t = time.perf_counter()
                meta = c.post("/v1/troubleshoot", json=body).json()["meta"]
                say(f"  {label:9s} {1000 * (time.perf_counter() - t):7.0f} ms"
                    f"  (cache_hit={meta['cache_hit']}, fallback={meta['fallback']})")

        url = f"{base}/demo"
        say(f"[5/5] READY  ->  {url}\n"
            "  Scenes: click them on the left, or press Alt+1 ... Alt+7. Ctrl+Enter runs.\n"
            "  Ctrl+C here stops the API (Ollama keeps running).")
        if not a.no_browser:
            webbrowser.open(url)
        proc.wait()
    except KeyboardInterrupt:
        say("\nstopping the API ...")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
