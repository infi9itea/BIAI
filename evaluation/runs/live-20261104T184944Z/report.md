# Evaluation run `live-20261104T184944Z`

**LIVE RUN** - real Gemini API calls; a failed call is counted as a failure, never replaced by a fixture.

- Model requested: `gemini-3.5-flash-lite`; reported by API: `['gemini-3.5-flash-lite']`
- Commit: `36147423f0cdd06debcf34880b4fe96ddc25e852` (dirty: False); ground truth modified in working tree: False
- Started 2026-11-04T18:49:44.811666+00:00, finished 2026-11-04T18:52:23.647462+00:00; SDK google-genai 2.34.0

## Headline: four separate measures

| Measure | Result |
|---|---|
| Extraction - model output, field values correct | 259/260 (99.6%) |
| Extraction - after evidence verification, field values correct | 249/250 (99.6%) |
| Evidence - quotes verbatim on the cited page | 211/211 (100.0%) |
| Evidence - quote supports the correct value | 211/211 (100.0%) |
| Evidence - quote supports the value the model claimed | 211/211 (100.0%) |
| Arithmetic - check status agrees with expected | 37/37 (100.0%) |
| Arithmetic - printed value read by the pipeline matches the PDF | 37/37 (100.0%) |
| Arithmetic - status agrees, excluding checks inheriting an extraction error in their own inputs | 37/37 (100.0%) |
| Rules - exact rule set matches ground truth | 9/10 (90.0%) |
| Rules - exact rule set including the line each per-item rule fired on | 9/10 (90.0%) |
| Line items - fabricated (beyond the answer key) / returned | 0 / 10 |
| **Decision agreement** (processing failures count as wrong) | 10/10 (100.0%) |
| Decision agreement with no other error in the pack | 9/10 (90.0%) |

Decision agreement is not extraction accuracy: a correct decision can rest on a wrong intermediate value. Right decision, wrong intermediate: ['supplier-pack-08.pdf'].

## Extraction detail (model view)

- Value recall (fields that have a value in the PDF): 221/222 (99.5%)
- Value precision (values returned that are right): 221/221 (100.0%)
- Outcomes: {'CORRECT': 221, 'CORRECT_NULL': 38, 'MISSED': 1}
- Verifier false accepts / false rejects (judged against the model's CLAIMED value and its quote): 0 / 0; verified fields whose claim is wrong per the key: 0; verified fields whose claim could not be checked automatically: 0

| Field | CORRECT | CORRECT_NULL | WRONG | MISSED | SPURIOUS | NOT_EXTRACTED |
|---|---|---|---|---|---|---|
| advance_payment_pct | 10 | 0 | 0 | 0 | 0 | 0 |
| bin | 9 | 1 | 0 | 0 | 0 | 0 |
| delivery_days | 9 | 0 | 0 | 1 | 0 | 0 |
| description | 10 | 0 | 0 | 0 | 0 | 0 |
| grand_total_bdt | 10 | 0 | 0 | 0 | 0 | 0 |
| item_code | 10 | 0 | 0 | 0 | 0 | 0 |
| line_total_bdt | 10 | 0 | 0 | 0 | 0 | 0 |
| payment_max_days | 10 | 0 | 0 | 0 | 0 | 0 |
| payment_min_days | 10 | 0 | 0 | 0 | 0 | 0 |
| penalty_calculation_basis | 8 | 2 | 0 | 0 | 0 | 0 |
| penalty_full_weeks_only | 1 | 9 | 0 | 0 | 0 | 0 |
| penalty_period_unit | 9 | 1 | 0 | 0 | 0 | 0 |
| penalty_present | 9 | 1 | 0 | 0 | 0 | 0 |
| penalty_rate_pct | 9 | 1 | 0 | 0 | 0 | 0 |
| quantity | 10 | 0 | 0 | 0 | 0 | 0 |
| subtotal_bdt | 9 | 1 | 0 | 0 | 0 | 0 |
| supplier_name | 10 | 0 | 0 | 0 | 0 | 0 |
| tin | 9 | 1 | 0 | 0 | 0 | 0 |
| unit | 10 | 0 | 0 | 0 | 0 | 0 |
| unit_price_bdt | 10 | 0 | 0 | 0 | 0 | 0 |
| validity_days | 10 | 0 | 0 | 0 | 0 | 0 |
| vat_amount_bdt | 9 | 1 | 0 | 0 | 0 | 0 |
| vat_included_in_price | 1 | 9 | 0 | 0 | 0 | 0 |
| vat_price_basis | 0 | 10 | 0 | 0 | 0 | 0 |
| vat_rate_pct | 9 | 1 | 0 | 0 | 0 | 0 |
| vat_shown_separately | 10 | 0 | 0 | 0 | 0 | 0 |

## Per pack

| Pack | Expected | Pipeline | Agrees | Expected rules | Triggered rules | Status |
|---|---|---|---|---|---|---|
| supplier-pack-01.pdf | NEEDS_REVIEW | NEEDS_REVIEW | yes | NR-03 | NR-03 | OK |
| supplier-pack-02.pdf | APPROVE | APPROVE | yes | - | - | OK |
| supplier-pack-03.pdf | REJECT | REJECT | yes | R-01 | R-01 | OK |
| supplier-pack-04.pdf | NEEDS_REVIEW | NEEDS_REVIEW | yes | NR-01 | NR-01 | OK |
| supplier-pack-05.pdf | REJECT | REJECT | yes | R-02 | R-02 | OK |
| supplier-pack-06.pdf | REJECT | REJECT | yes | R-03 | R-03 | OK |
| supplier-pack-07.pdf | NEEDS_REVIEW | NEEDS_REVIEW | yes | NR-02 | NR-02 | OK |
| supplier-pack-08.pdf | REJECT | REJECT | yes | NR-08, R-04 | NR-04, NR-08, R-04, R-05 | OK |
| supplier-pack-09.pdf | NEEDS_REVIEW | NEEDS_REVIEW | yes | NR-03, NR-06 | NR-03, NR-06 | OK |
| supplier-pack-10.pdf | REJECT | REJECT | yes | NR-01, NR-05, NR-06, R-01, R-05 | NR-01, NR-05, NR-06, R-01, R-05 | OK |

Confusion (rows = expected, columns = pipeline; processing failures excluded): `{'APPROVE': {'APPROVE': 1, 'NEEDS_REVIEW': 0, 'REJECT': 0}, 'NEEDS_REVIEW': {'APPROVE': 0, 'NEEDS_REVIEW': 4, 'REJECT': 0}, 'REJECT': {'APPROVE': 0, 'NEEDS_REVIEW': 0, 'REJECT': 5}}`

## Failure analysis

Counts: `{'MISSED_VALUE': 1, 'RULES_DIFFER_AFTER_UPSTREAM_ERROR': 1, 'RIGHT_DECISION_WRONG_INTERMEDIATE': 1}`

- **supplier-pack-08.pdf** [extraction] MISSED_VALUE: doc.delivery_days: expected 18; model returned nothing
- **supplier-pack-08.pdf** [rules] RULES_DIFFER_AFTER_UPSTREAM_ERROR: missing [], extra ['NR-04', 'R-05']
- **supplier-pack-08.pdf** [decision] RIGHT_DECISION_WRONG_INTERMEDIATE: decision agrees but an intermediate result above is wrong; the agreement is not evidence of correct extraction
