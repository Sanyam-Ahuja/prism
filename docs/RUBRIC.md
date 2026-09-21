# Scoring Rubric

PDF Appendix C §2 asks for two scores without defining how to compute them.
This is our definition, so the numbers in `metrics.md` are auditable rather than
asserted. `scripts/score_plans.py` implements it; ground truth for deeplinks is
hand-labelled in `tests/fixtures/plan_deeplink_truth.json`.

---

## Step accuracy — 0.0 to 3.0

Three independent sub-scores of 1.0 each, computed mechanically.

### Completeness (0–1)
Fraction of the source document's actionable candidate steps that the plan uses.

Not every candidate belongs in a plan — source documents contain alternative
routes to the same screen and steps outside the complaint's scope — so full marks
are awarded at **≥60% coverage**, scaling linearly below that.
`score = min(1.0, used / (0.6 × candidates))`

### Correctness (0–1)
Equal thirds:
- every step traceable verbatim to a source candidate (ADR-001);
- every step imperative and single-interaction (gate G7);
- the plan passes every blocking gate (G0–G15).

### Ordering (0–1)
Equal halves:
- action categories non-decreasing under `auto < manual < critical` (gate G14);
- within each step group, steps appear in source-document order.

---

## Deeplink relevance — 0.0 to 2.0

Scored per `auto` action, then averaged. `manual` actions are excluded: they are
forbidden a deeplink (G13), so including them would inflate the score.

| score | meaning |
|---|---|
| **2.0** | the exact target screen, or the closest entry the catalog actually contains |
| **1.0** | correct feature area but not the exact screen or toggle (the "parent menu" case PDF §6.2 penalises) |
| **0.0** | wrong feature area, or a real entry returned where the correct answer was `dummy_positive` |

**Sanctioned placeholders are excluded from the average and reported separately.**
When a step's screen is genuinely absent from the catalog, `bixby://dummy_positive`
is the specified answer (PDF §3), so scoring it as a miss would penalise correct
behaviour — and scoring it 2.0 would let placeholders inflate the result. Neither
is honest, so it sits outside the denominator with its rate reported alongside.

A placeholder used where a real entry **did** exist scores 0.0, not excluded.
