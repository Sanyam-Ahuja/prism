"""Run every PDF Appendix C measurement and keep the raw output.

Cross-platform stand-in for the Makefile targets, which assume the Fedora
toolbox. Each step runs under the current interpreter with UTF-8 I/O forced on:
the source data holds non-ASCII text that the Windows default code page
silently corrupts, which turns an exact cache hit into a cold-path miss. Every
step's output lands in reports/<step>.txt, so each number in docs/metrics.md
traces to a file.

    python scripts/run_reports.py fixed                    # no LLM needed
    python scripts/run_reports.py models --models A B C    # extractor comparison
    python scripts/run_reports.py model --model qwen2.5:3b # chosen extractor
"""
import argparse, datetime, json, os, platform, subprocess, sys, tempfile, time

import httpx

OUT = "reports"
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
PARA = "tests/fixtures/deeplink_labels_paraphrase.json"
DOMAINS = "tests/fixtures/deeplink_labels_domains.json"

FIXED = [
    ("tests", ["-m", "pytest", "tests/", "-q"]),
    ("compile_check", ["scripts/compile_plans.py"]),
    ("score_plans", ["scripts/score_plans.py"]),
    ("dl_display_hybrid", ["scripts/eval_deeplinks.py", "--dense"]),
    ("dl_display_bm25", ["scripts/eval_deeplinks.py"]),
    ("dl_domains_hybrid", ["scripts/eval_deeplinks.py", "--dense", "--labels", DOMAINS]),
    ("dl_paraphrase_hybrid", ["scripts/eval_deeplinks.py", "--dense", "--recall", "--labels", PARA]),
    ("dl_paraphrase_hybrid_tau020", ["scripts/eval_deeplinks.py", "--dense", "--tau", "0.20", "--labels", PARA]),
    ("dl_paraphrase_bm25", ["scripts/eval_deeplinks.py", "--recall", "--labels", PARA]),
    ("cache_shipped", ["scripts/eval_cache.py"]),
    ("cache_shipped_no_ood", ["scripts/eval_cache.py", "--no-ood"]),
    ("cache_single_070", ["scripts/eval_cache.py", "--tau", "0.70", "--high", "0.70", "--no-ood"]),
    ("cache_single_078", ["scripts/eval_cache.py", "--tau", "0.78", "--high", "0.78", "--no-ood"]),
    ("cache_test", ["scripts/eval_cache.py", "--set", "test"]),
    ("cache_test_no_ood", ["scripts/eval_cache.py", "--set", "test", "--no-ood"]),
    ("multi_intent", ["scripts/eval_multi_intent.py"]),
]

MODEL = [
    ("bench", ["scripts/bench.py", "--n", "40", "--cold", "33"]),
    ("batch", ["scripts/run_batch.py"]),
    ("ablation_generate", ["scripts/ablation_baseline.py", "--mode", "generate"]),
    ("ablation_select", ["scripts/ablation_baseline.py", "--mode", "select"]),
    ("ablation_steps", ["scripts/ablation_steps.py"]),
    ("stress_api", ["scripts/stress_api.py", "--cold", "33"]),
    ("determinism", ["scripts/determinism_probe.py"]),
]


def _ram_gb():
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                            ("ext", ctypes.c_ulonglong)]
            m = MS(); m.len = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except Exception:
        return None


def _cmd(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def environment():
    """The Appendix C header fields, measured rather than typed."""
    import sentence_transformers, torch
    ram = _ram_gb()
    lines = [
        f"date        : {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"commit      : {_cmd(['git', 'rev-parse', '--short', 'HEAD'])}"
        f"{' (dirty)' if _cmd(['git', 'status', '--porcelain']) else ''}",
        f"os          : {platform.platform()}",
        f"cpu         : {platform.processor() or platform.machine()}, {os.cpu_count()} logical cores",
        f"ram         : {ram:.1f} GiB" if ram else "ram         : unknown",
        f"gpu         : {_cmd(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader']) or 'none'}",
        f"python      : {platform.python_version()}",
        f"torch       : {torch.__version__}   sentence-transformers {sentence_transformers.__version__}",
    ]
    try:
        lines.append(f"ollama      : {httpx.get(f'{OLLAMA}/api/version', timeout=3).json()['version']}")
        for m in httpx.get(f"{OLLAMA}/api/tags", timeout=3).json()["models"]:
            d = m["details"]
            lines.append(f"  model     : {m['name']:16s} {d.get('parameter_size', '?'):>6s} "
                         f"{d.get('quantization_level', '?'):8s} {m['size'] / 1e9:.2f} GB  "
                         f"digest {m['digest'][:12]}")
    except Exception:
        lines.append("ollama      : not reachable")
    return "\n".join(lines) + "\n"


def run(name, args, env):
    t0 = time.perf_counter()
    r = subprocess.run([sys.executable, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    el = time.perf_counter() - t0
    with open(f"{OUT}/{name}.txt", "w", encoding="utf-8") as f:
        f.write(f"# {' '.join(['python', *args])}\n")
        if env.get("PRISM_EXTRACT_MODEL"):
            f.write(f"# PRISM_EXTRACT_MODEL={env['PRISM_EXTRACT_MODEL']}\n")
        f.write(f"# exit={r.returncode}  elapsed={el:.1f}s\n\n")
        f.write(r.stdout)
        if r.returncode != 0:
            f.write("\n# ---- stderr ----\n" + r.stderr[-6000:])
    status = "ok  " if r.returncode == 0 else f"FAIL({r.returncode})"
    print(f"  {status} {name:32s} {el:7.1f}s  -> {OUT}/{name}.txt", flush=True)
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["fixed", "models", "model"])
    ap.add_argument("--models", nargs="+", default=[])
    ap.add_argument("--model")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--only", nargs="+", help="run just these step names")
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               HF_HUB_DISABLE_PROGRESS_BARS="1")
    with open(f"{OUT}/environment.txt", "w", encoding="utf-8") as f:
        f.write(environment())

    if a.stage == "fixed":
        steps = FIXED
    elif a.stage == "models":
        if not a.models:
            ap.error("--models is required for the models stage")
        # One step per model, each merging into the same results file.
        steps = [(f"compare_{m.replace(':', '_')}",
                  ["scripts/compare_models.py", "--models", m, "--passes", str(a.passes),
                   "--out", f"{OUT}/compare_models.json"]) for m in a.models]
    else:
        if not a.model:
            ap.error("--model is required for the model stage")
        env["PRISM_EXTRACT_MODEL"] = a.model
        steps = MODEL

    if a.only:
        steps = [s for s in steps if s[0] in a.only]
    print(f"stage={a.stage}  steps={len(steps)}", flush=True)
    ok = 0
    for n, args in steps:
        # compile_check must never touch the shipped artifacts/.
        e = dict(env, PRISM_ARTIFACT_DIR=tempfile.mkdtemp(prefix="prism_compile_")) \
            if n == "compile_check" else env
        ok += run(n, args, e)
    print(f"done: {ok}/{len(steps)} steps succeeded")
    return 0 if ok == len(steps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
