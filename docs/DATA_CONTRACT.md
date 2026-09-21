# Data Contract & Validation Gates

Normative reference for every field the engine emits, and the gate that enforces it.
Source of truth: PDF §4 and Appendix A/B. `schema.py` is the type contract; PDF §4.1 is the *rule* contract, and it is stricter than the types.

---

## 1. Response envelope

`schema.py` defines only `ContextDeeplinkResponse`. The PDF's Appendix B shows a wider envelope. We emit the **superset**, because `query_variations` and `meta` are explicitly required elsewhere in the spec (§4.1, §6.3) and a consumer reading only `response.contexts` is unaffected.

```jsonc
{
  "query": "<original, unmodified request query>",
  "query_variations": ["...", "..."],        // 8–10, PDF §4.1
  "response": {
    "contexts": [ /* Goal */ ]               // ContextDeeplinkResponse
  },
  "meta": {
    "latency_ms": 212,
    "cache_hit": true,
    "model": "<model id or null on hot path>",
    "cost_usd": 0.0,
    "fallback": null                          // "no_match" | "no_siis_context" | null
  }
}
```

`response.contexts` must validate against `ContextDeeplinkResponse` unchanged. No markdown fence, no preamble (PDF §4.2.4).

> **Open spec question (M-Q3).** `sample_output.json` emits only `{query, response}` — no `query_variations`, no `meta`. Appendix B emits all four. We follow Appendix B. Confirm with the mentor.

---

## 2. Field rules

| field | rule | gate | severity |
|---|---|---|---|
| `goal` | exactly `Follow these steps to perform this <Topic> Troubleshooting` (or `... <Topic> Configuration`) | G1 | blocking |
| `title` | 2–3 words, sentence case (first word capitalized, rest lower unless proper noun) | G2 | blocking |
| `score` | float, `0.0 ≤ score ≤ 1.0` | G3 | blocking |
| `actions` | ≥ 1 for a non-fallback Goal | G4 | blocking |
| `actionName` | Title Case; exactly one physical screen or one physical intervention | G5 | blocking |
| `description` | **exactly 5–7 words**, starts `It will` | G6 | blocking |
| `stepGroups[].steps` | imperative; one physical interaction per step; no URLs; each derived from source text | G7 | blocking |
| `category` | `auto` \| `manual` \| `critical` | G8 | blocking |
| `actionableDeeplink` | verbatim catalog copy, or `dummy_positive`, or `null` | G9 | blocking |
| `validationDeeplink` | verbatim catalog `validation` object, or `null` | G10 | blocking |
| `query_variations` | 8–10 distinct strings, varied register | G11 | non-blocking |

`description` word count is `len(text.split())`. `It will let you choose navigation type` = 7 ✓.

> ⚠️ **Known conflict (M-Q2).** `sample_output.json` violates G6: its two descriptions are **9** and **12** words (`It will facilitate secure data transfer between your devices`; `It will help you locate the nearest Samsung service center and schedule`). Appendix B complies (7 words). **We enforce the PDF rule**, on the assumption the automated gate checks the spec rather than the sample. Confirm with the mentor.

---

## 3. Category rules

| category | definition | actionableDeeplink | ordering |
|---|---|---|---|
| `auto` | standard Settings screen reachable via deeplink | **required** (≥90% target, PDF Appendix C §1) | first |
| `manual` | physical intervention: port cleaning, hardware replacement, service centre | **forbidden — must be `null`** | middle |
| `critical` | disruptive/irreversible: factory reset, restart, firmware update, safe mode | optional | **last** |

Keyword override table — if any fires on step text, category is forced regardless of model output:

```
critical : factory reset, reset, restart, reboot, power off, safe mode,
           firmware update, software update, wipe, erase
manual   : service center, service centre, authorized, repair, technician,
           inspect, clean, cable, charger, ejector tool, LDI, physical damage
```

`critical` overrides `manual` where both fire.

---

## 4. Deeplink object construction

### 4.1 Actionable — catalog match

```python
{
  "deeplink":     entry["deeplink"],       # verbatim
  "description":  entry["description"],    # verbatim
  "message":      entry["message"],        # verbatim
  "originalType": entry["originalType"],   # verbatim
}
```
Omit `classes` always — absent from the catalog, unused in the sample.

### 4.2 Actionable — `dummy_positive`

Used only when a step opens a Settings screen and no catalog entry clears `TAU_LINK`.

```python
{
  "deeplink":    "bixby://dummy_positive",
  "description": "<we write: names the concrete screen from the steps>",
  "message":     "<we write: 5–7 words>",
}
```
Per `DL-DUMMY.qna_description`, these two strings are authored by us, not copied.

### 4.3 Validation

```python
validationDeeplink = entry["validation"]   # verbatim dict, or None
```

Catalog reality: 570/578 entries carry `validation`; of those, **138** include the full `resultType`/`condition`/`value` triple (these are the `onURL`/`offURL` toggles). The remaining 432 carry only `{deeplink, key}` — that is schema-valid, since those three fields are `Optional`.

**Never synthesize a validation block.** A fabricated `bixby://masked/val/...` breaches catalog integrity (C3) exactly as a fabricated action URI does.

`dummy_positive` has `validation: null` → `validationDeeplink: null`.

---

## 5. Catalog hazards

Verified against `deeplinks.json` (578 entries). Encode each as a test.

| hazard | instance | handling |
|---|---|---|
| Substring false positives | `DL-0349` *TalkBack verbosity **pre**set* matches `/reset/` | token-level matching; never bare substring |
| Appliance noise | `DL-0468` air conditioner, `DL-0469`/`DL-0473` refrigerator | domain denylist before ranking |
| Corrupt metadata | `DL-0294`/`DL-0295` `message` = `"Offurl"`/`"Onurl"`, `validation.key` = `"offURL"`/`"onURL"` | usable URI, unusable metadata — never copy their `message` |
| Coverage holes | no *safe mode*, *auto-rotate*, *screen mirroring / Smart View*, *software update*, *clear app cache* | `dummy_positive` |
| Near-duplicate pairs | `DL-0020`/`DL-0021` adaptive brightness on/off | polarity gate disambiguates |
| Read-only diagnostics | `DL-0470`–`DL-0476` (`originalType: null`), incl. Diagnose Battery Drain / Slow Performance / Overheating | never an `actionableDeeplink`; candidates for `validationDeeplink` only |

`originalType` distribution: `onClickURL` 254, `onURL` 138, `offURL` 138, `updateURL` 36, `null` 11, `placeholder` 1.

---

## 6. Fallback payloads

Per PDF §4.2.3, an unanswerable query returns an empty context list with fallback metadata.

```jsonc
// no reference text supplied and no cache hit
{ "query": "...", "query_variations": [...],
  "response": {"contexts": []},
  "meta": {"latency_ms": 14, "cache_hit": false, "model": null,
           "cost_usd": 0.0, "fallback": "no_siis_context"} }

// reference text supplied but no viable plan survived validation
{ "query": "...", "query_variations": [...],
  "response": {"contexts": []},
  "meta": {"latency_ms": 4820, "cache_hit": false, "model": "<id>",
           "cost_usd": 0.0002, "fallback": "no_match"} }
```

Both return **HTTP 200**. An empty plan is a valid answer, not an error. Reserve 4xx for malformed requests and 5xx for genuine faults.

---

## 7. Gate reference

Implemented in `validators/`. Every gate is a pure function `(obj) -> list[Violation]`.

| id | gate | on failure |
|---|---|---|
| G0 | **URL scrub**: no `http://`, `https://`, `www.`, `[...](...)` in any string | strip, then re-run |
| G1 | goal matches template regex | coerce from `topic` |
| G2 | title word count ∈ [2,3], sentence case | compress, then truncate |
| G3 | score ∈ [0.0, 1.0] | clamp |
| G4 | ≥ 1 action per non-fallback Goal | → `no_match` |
| G5 | actionName Title Case, non-empty | coerce |
| G6 | description word count ∈ [5,7], prefix `It will` | one LLM retry, then deterministic trim |
| G7 | steps non-empty, imperative, single-interaction | split multi-interaction; drop empty |
| G8 | category ∈ enum | default `manual` |
| G9 | every emitted URI ∈ catalog ∪ `{bixby://dummy_positive}` | **drop action** (never rewrite a URI) |
| G10 | validationDeeplink matches its entry's `validation` exactly, or is null | null it |
| G11 | 8–10 distinct variations | regenerate at build time |
| G12 | **one action = one screen**: no two actions share a resolved deeplink | merge stepGroups |
| G13 | `manual` ⇒ `actionableDeeplink is None` | null it |
| G14 | `critical` actions sorted last | re-sort |
| G15 | Pydantic `ContextDeeplinkResponse` validates | drop offending action, re-run |
| G16 | serialized output is bare JSON (no fence, no preamble) | serializer-enforced |

**Repair budget: 2 rounds.** After that, drop the offending action. After that, `no_match`. Never emit invalid JSON.

G0 runs first (so later gates see clean text) and again last (so repairs cannot reintroduce a URL).

---

## 8. Source-data facts the tests must pin

Derived from the input kit; assert these so silent data swaps are caught.

| fact | value |
|---|---|
| `deeplinks.json` entries | 578 |
| entries with `validation` | 570 |
| entries with full validation triple | 138 |
| `input.txt` queries | 20 |
| `siis_responses.json` rows | 20 (ids `row_1`…`row_22`; `row_6`, `row_18` absent) |
| **distinct SIIS documents** | **11** |
| most frequent document | *Blank or black display…* ×6 |
| URLs/contact details in SIIS source | **3 rows** (`row_3`, `row_11`, `row_17`) — the kit's README claims none; G0 catches them |
| domain coverage of public queries | **Display only** |

The last row is the largest generalization risk: the PDF names **Battery, Display, Camera, Performance**, and the public set exercises one of the four. Nothing in the pipeline may be display-specific. See TECH_PLAN.md M6.
