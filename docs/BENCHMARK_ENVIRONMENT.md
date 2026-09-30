# Benchmark environment: what was changed on the laptop, and what it did

Every figure in `docs/metrics.md` and `reports/` was measured on one shared
Windows laptop. On 2026-09-30 the same code was benchmarked twice: first during a
working day with other things running, then in the evening on a laptop cleared
for the purpose. This note records what was different between the two runs, so
the numbers can be trusted and the second run can be repeated.

## The machine

Intel Core i5-13420H (8 cores, 12 threads), 15.7 GiB RAM, NVIDIA RTX 4050 Laptop
GPU with 6 GiB, Windows 11 Home, Python 3.13.9, Ollama 0.34.4. Full details are
in `reports/environment.txt`.

## What was running during the earlier runs

- **An unrelated training job** (`unilateral.train`, 6 worker processes, about
  3 GB of RAM, 6 CPU cores) ran for part of the day and left about 0.7 GB of RAM
  free. Runs that overlapped it were thrown away and repeated. The runs that were
  kept still shared the laptop with other work.
- **Leftovers from our own work:**
  - the demo server from an earlier session, idle on port 8000
  - two orphaned Ollama model processes (`llama-server.exe`) whose parent server
    had been restarted. Both were still registered on the GPU.
  - the extractor model pinned in GPU memory (`keep_alive: -1`)
- **Background apps:**
  - FxSound, which kept Windows' audio process busy
  - NVIDIA Share (Instant Replay) and the NVIDIA performance overlay (PresentMon)
  - OneDrive syncing the repository folder, which holds this project

## What was done before the evening run

1. The laptop was freed for benchmarking: the other work was closed, and it was
   left plugged in.
2. The leftover demo server was stopped (4 processes, including its API on port
   8000).
3. The two orphaned `llama-server.exe` processes were stopped.
4. The extractor model was unloaded, so every benchmark started from a fresh
   model load.
5. The state was checked before starting:

   | Check | Result |
   |---|---|
   | GPU memory | 0 MiB used, 0% busy, no GPU processes |
   | Ollama | up, no model loaded |
   | Port 8000 | free |
   | Leftover Python processes | none |
   | Free RAM | 6.2 GB |
   | Power | plugged in |

The background apps were left alone, because they belong to the laptop's owner.
OneDrive stayed active and used about as much CPU as the benchmark while it was
writing reports.

## How the evening run was done

- **One sequential run, 21:30 to 22:05**, one step at a time. Nothing ran in
  parallel.
- **Order:**
  1. the model comparison (all seven models)
  2. the `qwen2.5:1.5b` stage: latency benchmark, batch, deeplink ablations, HTTP
     stress test, determinism
  3. the fixed stage: tests, gates, scoring, retrieval, cache, multi-intent
  4. the startup and step-accuracy breakdown
  5. the schema-bounds A/B
- **Every model was unloaded between stages**, so each stage started from a fresh
  load.
- **The A/B hit the tool's 30-minute background limit.** It was re-run on its own
  straight afterwards. Its report is only written when the run completes, so no
  partial result was kept.
- **Reports were overwritten in place.** No code changed between the two runs.

**Utilisation during the run:** the GPU was 92–96% busy at full clock (about
45 W), with the model entirely in GPU memory. The CPU was 11–23% busy, which is
expected: the model runs on the GPU. The sentence encoder uses the CPU in short
bursts, with 8 threads, one per physical core. The benchmarks time one request at
a time on purpose, because that is the latency a user sees; concurrency is tested
separately by an 8-client burst.

## What improved

| Measure | Earlier runs, laptop shared | Evening run, laptop cleared | Change |
|---|---|---|---|
| Live plan, P50 | 3199 ms | **2576 ms** | −19% |
| Live plan, P95 | 5215–5475 ms | **4389 ms** | −16% or more |
| Live plan over HTTP, P95 | 5413 ms | **4325 ms** | −20% |
| Cache hit, new phrasing, P95 | 28.6 ms | **13.7 ms** | −52% |
| Cache hit, exact, P95 | 0.3 ms | **0.1 ms** | |
| Deeplink resolver, P95 per screen | 24.5 ms | **16.9 ms** | −31% |
| API start-up to `/health` = ok | 21.7 s | **13.6 s** | −37% |
| Encoder load | 18.8 s | **9.0 s** | −52% |
| Model decode speed, `qwen2.5:1.5b` | ~62 tokens/s | **~124 tokens/s** | about 2x |
| Hot path, 8 concurrent clients, P95 | 217–320 ms | 270 ms | within the earlier range |

The 8-client figure did not improve. It is limited by the CPU, and OneDrive was
still using the CPU during the run.

**The model comparison shifted.** All seven models got faster, by 18–29% at the
median. On the
cleared laptop, the 4-bit `llama3.2:1b` ties `qwen2.5:1.5b` at the median (54 ms
apart) and beats it at P95 (3386 ms against 4315 ms), and `llama3.2:3b` now meets
the 8 s budget (P95 6925 ms). `qwen2.5:1.5b` remains the choice, on quality:
- step accuracy 2.97 against 2.87
- 36 action names replaced out of 108, against 63 of 72
- 63 actions treated as settings changes, against 15

See `reports/README.md` §E and ADR-017.

## What did not change

Every accuracy figure reproduced exactly:
- tests: 81 passed, 1 skipped
- step accuracy 2.92 compiled and 2.97 live; deeplink relevance 1.78
- deeplink precision 95.3% on Display and 100% on other domains
- cache hit rate 84.6% on the tuning set and 100% on the held-out set
- out-of-scope false hits: 0 of 12 and 1 of 38
- multi-intent: both plans for 340 of 340
- schema-valid responses: 325 of 325 over HTTP
- determinism: 4 of 11 articles change once the model's prompt is cached, as before

The laptop's state affects speed only, never correctness.

## To reproduce these conditions

1. **Plug the laptop in.**
2. **Close other heavy work.** In Task Manager, check that no training job or
   other Python work is running and that at least 6 GB of RAM is free.
3. **Stop leftovers:**
   - any running demo (`scripts/demo.py`); port 8000 must be free
   - any `llama-server.exe` that isn't a child of the running `ollama.exe`
4. **Unload models** so runs start fresh (`ollama stop <model>`, or a generate
   request with `"keep_alive": 0`). `nvidia-smi` should show 0 MiB used.
5. **Turn off background load:**
   - pause OneDrive (tray icon → Pause syncing)
   - turn off NVIDIA Instant Replay and the overlay
   - close FxSound
6. **Run the stages one at a time**, never in parallel:

   ```powershell
   $env:PYTHONUTF8='1'
   .venv\Scripts\python scripts\run_reports.py models --models qwen2.5:1.5b llama3.2:1b llama3.2:1b-instruct-q4_K_M gemma3:1b llama3.2:3b qwen2.5:3b gemma3:4b
   .venv\Scripts\python scripts\run_reports.py model --model qwen2.5:1.5b
   .venv\Scripts\python scripts\run_reports.py fixed
   .venv\Scripts\python scripts\verify_cold_path.py breakdown
   .venv\Scripts\python scripts\verify_cold_path.py ab
   ```

   The whole sequence takes about 40 minutes.
