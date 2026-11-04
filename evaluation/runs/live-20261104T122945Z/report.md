# Evaluation run `live-20261104T122945Z`

**LIVE RUN** - real Gemini API calls; a failed call is counted as a failure, never replaced by a fixture.

- Model requested: `gemini-3.5-flash-lite`; reported by API: `['gemini-3.5-flash-lite']`
- Commit: `7ddc6c6e1cdcaf8837690457c8a68985259459f7` (dirty: True); ground truth modified in working tree: False
- Started 2026-11-04T12:29:45.984701+00:00, finished 2026-11-04T12:30:53.584766+00:00; SDK google-genai 2.34.0

## Headline: four separate measures

| Measure | Result |
|---|---|
| Extraction - model output, field values correct | 237/260 (91.2%) |
| Extraction - after evidence verification, field values correct | 228/251 (90.8%) |
| Evidence - quotes verbatim on the cited page | 190/190 (100.0%) |
| Evidence - quote supports the correct value | 190/190 (100.0%) |
| Evidence - quote supports the value the model claimed | 190/190 (100.0%) |
| Arithmetic - check status agrees with expected | 33/37 (89.2%) |
| Arithmetic - printed value read by the pipeline matches the PDF | 33/37 (89.2%) |
| Arithmetic - status agrees, excluding checks inheriting an extraction error in their own inputs | 33/37 (89.2%) |
| Rules - exact rule set matches ground truth | 9/10 (90.0%) |
| Rules - exact rule set including the line each per-item rule fired on | 9/10 (90.0%) |
| Line items - fabricated (beyond the answer key) / returned | 0 / 9 |
| **Decision agreement** (processing failures count as wrong) | 9/10 (90.0%) |
| Decision agreement with no other error in the pack | 9/9 (100.0%) |

Decision agreement is not extraction accuracy: a correct decision can rest on a wrong intermediate value. Right decision, wrong intermediate: none.

## Extraction detail (model view)

- Value recall (fields that have a value in the PDF): 199/222 (89.6%)
- Value precision (values returned that are right): 199/199 (100.0%)
- Outcomes: {'CORRECT': 199, 'CORRECT_NULL': 38, 'NOT_EXTRACTED': 23}
- Verifier false accepts / false rejects (judged against the model's CLAIMED value and its quote): 0 / 0; verified fields whose claim is wrong per the key: 0; verified fields whose claim could not be checked automatically: 0

| Field | CORRECT | CORRECT_NULL | WRONG | MISSED | SPURIOUS | NOT_EXTRACTED |
|---|---|---|---|---|---|---|
| advance_payment_pct | 9 | 0 | 0 | 0 | 0 | 1 |
| bin | 8 | 1 | 0 | 0 | 0 | 1 |
| delivery_days | 9 | 0 | 0 | 0 | 0 | 1 |
| description | 9 | 0 | 0 | 0 | 0 | 1 |
| grand_total_bdt | 9 | 0 | 0 | 0 | 0 | 1 |
| item_code | 9 | 0 | 0 | 0 | 0 | 1 |
| line_total_bdt | 9 | 0 | 0 | 0 | 0 | 1 |
| payment_max_days | 9 | 0 | 0 | 0 | 0 | 1 |
| payment_min_days | 9 | 0 | 0 | 0 | 0 | 1 |
| penalty_calculation_basis | 7 | 2 | 0 | 0 | 0 | 1 |
| penalty_full_weeks_only | 1 | 9 | 0 | 0 | 0 | 0 |
| penalty_period_unit | 8 | 1 | 0 | 0 | 0 | 1 |
| penalty_present | 8 | 1 | 0 | 0 | 0 | 1 |
| penalty_rate_pct | 8 | 1 | 0 | 0 | 0 | 1 |
| quantity | 9 | 0 | 0 | 0 | 0 | 1 |
| subtotal_bdt | 8 | 1 | 0 | 0 | 0 | 1 |
| supplier_name | 9 | 0 | 0 | 0 | 0 | 1 |
| tin | 8 | 1 | 0 | 0 | 0 | 1 |
| unit | 9 | 0 | 0 | 0 | 0 | 1 |
| unit_price_bdt | 9 | 0 | 0 | 0 | 0 | 1 |
| validity_days | 9 | 0 | 0 | 0 | 0 | 1 |
| vat_amount_bdt | 8 | 1 | 0 | 0 | 0 | 1 |
| vat_included_in_price | 1 | 9 | 0 | 0 | 0 | 0 |
| vat_price_basis | 0 | 10 | 0 | 0 | 0 | 0 |
| vat_rate_pct | 8 | 1 | 0 | 0 | 0 | 1 |
| vat_shown_separately | 9 | 0 | 0 | 0 | 0 | 1 |

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
| supplier-pack-08.pdf | REJECT | REJECT | yes | NR-08, R-04 | NR-08, R-04 | OK |
| supplier-pack-09.pdf | NEEDS_REVIEW | NEEDS_REVIEW | NO | NR-03, NR-06 | - | ERROR: extraction_invalid_json |
| supplier-pack-10.pdf | REJECT | REJECT | yes | NR-01, NR-05, NR-06, R-01, R-05 | NR-01, NR-05, NR-06, R-01, R-05 | OK |

Confusion (rows = expected, columns = pipeline; processing failures excluded): `{'APPROVE': {'APPROVE': 1, 'NEEDS_REVIEW': 0, 'REJECT': 0}, 'NEEDS_REVIEW': {'APPROVE': 0, 'NEEDS_REVIEW': 3, 'REJECT': 0}, 'REJECT': {'APPROVE': 0, 'NEEDS_REVIEW': 0, 'REJECT': 5}}`

## Failure analysis

Counts: `{'INVALID_JSON': 1, 'DECISION_MISMATCH': 1}`

- **supplier-pack-09.pdf** [processing] INVALID_JSON: ExtractionError: ValidationError: 8 validation errors for RawExtraction
line_items.0.item_code
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='D', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.description
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='Poly bag, 30 x 40 cm', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.quantity
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='50,000', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.unit
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='piece', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.unit_price_bdt
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='6.40', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.line_total_bdt
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='322,000.00', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.vat_included_in_price
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value=None, input_type=NoneType]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.source_quote
  Extra inputs are not permitted [type=extra_forbidden, input_value='D Poly bag, 30 x 40 cm 5...0 piece 6.40 322,000.00', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/extra_forbidden
- **supplier-pack-09.pdf** [decision] DECISION_MISMATCH: pipeline NEEDS_REVIEW vs ground truth NEEDS_REVIEW (ERROR: extraction_invalid_json)
