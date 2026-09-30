# Demo video runbook

One command, six scenes, about four minutes. Written for whoever records the demo.

## 1. Before recording (about 5 minutes)

1. **Plug the laptop in.** On battery the GPU clocks down and the live plan gets slower.
2. Close other heavy apps, especially anything using the GPU.
3. From the repo root:

   ```powershell
   .venv\Scripts\python scripts\demo.py
   ```

   It checks Ollama (and starts it if it is not running), loads `qwen2.5:1.5b` onto
   the GPU, starts the API with the demo page switched on, waits for `/health` to
   report ok (about 40 s, almost all of it loading the sentence encoder), warms every
   path so the first request on camera is not a slow one, and opens
   <http://127.0.0.1:8000/demo>. **Wait for `READY`.**
4. In the browser press **F11** (full screen) at 100% zoom. At 1920×1080 the whole
   page fits without scrolling. The **Theme** button in the header switches between
   light and dark.
5. Turn on Windows **Do not disturb** so notifications stay out of the shot.
6. Rehearse once. If a scene takes a different path from the one it is meant to
   show, the page says so in an orange "Rehearsal note".

**Keys:** `Alt+1` … `Alt+6` pick a scene · `Ctrl+Enter` runs it (or click
*Build troubleshooting plan*).

## 2. Run of show

| # | Scene | On screen | What to say |
|---|---|---|---|
| 0 | *(page idle)* | "How it works" panel; health bar top right | Galaxy users describe problems vaguely. Today an agent reads the knowledge base, picks steps and orders them — about 15 minutes per case. This engine turns the complaint into a validated, one-tap plan. Point at the health bar: 11 validated plans, 130 cached phrasings, 578 catalog deeplinks, and a 1.5-billion-parameter model running **locally**. |
| 1 | **Known issue** `Alt+1` | *Cache hit*, a few ms, $0.00 | A complaint we have seen before returns a pre-validated plan in a few milliseconds, with no model call. Point at the order: settings screens first, hands-on checks next, factory reset last. Click **Open › Enable Touch sensitivity**: every settings step carries a deeplink from the provided Settings catalog — on a phone, one tap opens that screen. |
| 2 | **Same issue, new words** `Alt+2` | *Cache hit*, about 30 ms | Different words, almost no keywords in common — same plan. The cache matches meaning, not strings. |
| 3 | **Never-seen issue** `Alt+3` | Timer counts up: "No cached plan — building one with the local model…", then *Live plan* in about 4–5 s | A new domain: battery. Only display articles were supplied, so this article is a **sample we wrote** (the page labels it). The model only picks step *numbers*; the steps you see are taken from the article itself, so it cannot invent one. Each settings step is matched to a catalog deeplink by a separate retriever — the model never sees or writes a URI. Point at the token count and $0.00: local inference. |
| 4 | **Out of scope** `Alt+4` | *Fallback · no_siis_context*, about 30 ms | No article and no known plan, so it returns an empty plan with a reason instead of guessing. |
| 5 | **Planted URL** `Alt+5` | Red URL in the article summary; after running, "Leak check: 1 URL in the article → 0 in the response ✓" | We planted a web link in the article. Nothing that leaves the API may contain one — a validation gate scrubs every string. |
| 6 | *(any result)* | Expand **Raw JSON response** | Every response is plain JSON in the problem statement's schema — goal, title, actions, step groups, deeplinks — plus metadata: latency, cache hit, model, cost and tokens. |
| — | **Spare: camera** `Alt+6` | *Live plan* | A second live example, if scene 3 needs a retake. |

Close on the numbers in section 3, or on `docs/metrics.md` §3 and §5.

## 3. Numbers you can quote

All measured; sources in `docs/metrics.md`.

| Claim | Value |
|---|---|
| Live (cold) plan, 95th percentile | **5.3 s** against an 8 s budget (33 runs, `qwen2.5:1.5b` on an RTX 4050 laptop GPU) |
| Cached plan, 95th percentile | **under 1 ms** exact match · **about 30 ms** new phrasing, against 300 ms |
| Schema-valid responses | **325/325** over HTTP, **100%** of `results.jsonl` |
| URL leaks | **0** |
| New phrasings that find the right cached plan | **84.6%** of 26 held-out paraphrases |
| Deeplink resolver accuracy | **95.3%** on 43 labelled display screens · **100%** on 26 battery/camera/performance screens |
| Cost per query | **$0.00** (local model) · about 880 tokens per live plan |
| Model | `qwen2.5:1.5b`, the smallest of five local models that passed every check (`reports/README.md` §E) |

## 4. Say it accurately

- The battery and camera articles are **synthetic, written by the team**, not
  Samsung content. Call them "a sample article".
- Only **display** issues have prebuilt plans. Other domains work through the live
  path, as in scene 3 — do not claim cached coverage of battery or camera.
- The **Open ›** buttons show the deeplink; on the laptop they only show a toast.
  On a phone they would open the Settings screen.
- A grey **Open Settings ›** button is the sanctioned placeholder for a screen the
  catalog does not index — it is honest, not a bug.
- In the battery plan, **Enable Adaptive Battery** shows a button reading "Enable
  Adaptive Display". That is the catalog's own label on its adaptive-battery
  entry (a known data defect, `docs/metrics.md` §6), shown verbatim; the
  link is the right screen. Don't click it on camera, or explain it if you do.
- Don't use the PDF's own "My phone got slow after the update" as an
  out-of-scope example: it hits the *Touchscreen issues* plan (a known false hit,
  `docs/metrics.md` §6). Scene 4's Bluetooth complaint falls back as intended.
- Live plans are not guaranteed identical across retakes: once the model server
  has cached an article's prompt, a few long articles can come out differently
  (`docs/metrics.md` §6). For a clean retake, run `ollama stop qwen2.5:1.5b` and
  start `scripts\demo.py` again.

## 5. If something goes wrong

| Symptom | Fix |
|---|---|
| `port 8000 is already in use` | An earlier demo is still running: close its terminal, or run `scripts\demo.py --port 8010` |
| `Ollama is not running and could not be started` | Start Ollama from the Start menu, then run the script again |
| `model qwen2.5:1.5b is not downloaded` | `ollama pull qwen2.5:1.5b` (about 1 GB) |
| Header shows a red dot | Ollama stopped. Cached scenes still work; restart Ollama and the script |
| A live scene takes much longer than usual | The model was unloaded (long idle, or another model ran). Run the script again — it reloads and warms the model |
| Page shows "API not reachable" | The terminal running `scripts\demo.py` was closed; run it again |

## 6. Optional: show it is a real API

In a second terminal, while the demo is running:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/troubleshoot `
  -ContentType 'application/json' -Body '{"query":"my touch screen is slow to respond"}' |
  ConvertTo-Json -Depth 10
```

The demo page is only mounted when `PRISM_DEMO=1` (which `scripts/demo.py` sets), so
the graded API stays exactly `POST /v1/troubleshoot` and `GET /health`.
