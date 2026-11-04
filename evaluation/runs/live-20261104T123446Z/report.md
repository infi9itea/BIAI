# Evaluation run `live-20261104T123446Z`

**LIVE RUN** - real Gemini API calls; a failed call is counted as a failure, never replaced by a fixture.

- Model requested: `gemini-3.5-flash-lite`; reported by API: `['gemini-3.5-flash-lite']`
- Commit: `7ddc6c6e1cdcaf8837690457c8a68985259459f7` (dirty: True); ground truth modified in working tree: False
- Started 2026-11-04T12:34:46.186148+00:00, finished 2026-11-04T12:36:17.409725+00:00; SDK google-genai 2.34.0

## Headline: four separate measures

| Measure | Result |
|---|---|
| Extraction - model output, field values correct | 193/260 (74.2%) |
| Extraction - after evidence verification, field values correct | 186/253 (73.5%) |
| Evidence - quotes verbatim on the cited page | 148/148 (100.0%) |
| Evidence - quote supports the correct value | 148/148 (100.0%) |
| Evidence - quote supports the value the model claimed | 148/148 (100.0%) |
| Arithmetic - check status agrees with expected | 28/37 (75.7%) |
| Arithmetic - printed value read by the pipeline matches the PDF | 28/37 (75.7%) |
| Arithmetic - status agrees, excluding checks inheriting an extraction error in their own inputs | 28/37 (75.7%) |
| Rules - exact rule set matches ground truth | 7/10 (70.0%) |
| Rules - exact rule set including the line each per-item rule fired on | 7/10 (70.0%) |
| Line items - fabricated (beyond the answer key) / returned | 0 / 7 |
| **Decision agreement** (processing failures count as wrong) | 7/10 (70.0%) |
| Decision agreement with no other error in the pack | 7/7 (100.0%) |

Decision agreement is not extraction accuracy: a correct decision can rest on a wrong intermediate value. Right decision, wrong intermediate: none.

## Extraction detail (model view)

- Value recall (fields that have a value in the PDF): 155/222 (69.8%)
- Value precision (values returned that are right): 155/155 (100.0%)
- Outcomes: {'CORRECT': 155, 'CORRECT_NULL': 38, 'NOT_EXTRACTED': 67}
- Verifier false accepts / false rejects (judged against the model's CLAIMED value and its quote): 0 / 0; verified fields whose claim is wrong per the key: 0; verified fields whose claim could not be checked automatically: 0

| Field | CORRECT | CORRECT_NULL | WRONG | MISSED | SPURIOUS | NOT_EXTRACTED |
|---|---|---|---|---|---|---|
| advance_payment_pct | 7 | 0 | 0 | 0 | 0 | 3 |
| bin | 6 | 1 | 0 | 0 | 0 | 3 |
| delivery_days | 7 | 0 | 0 | 0 | 0 | 3 |
| description | 7 | 0 | 0 | 0 | 0 | 3 |
| grand_total_bdt | 7 | 0 | 0 | 0 | 0 | 3 |
| item_code | 7 | 0 | 0 | 0 | 0 | 3 |
| line_total_bdt | 7 | 0 | 0 | 0 | 0 | 3 |
| payment_max_days | 7 | 0 | 0 | 0 | 0 | 3 |
| payment_min_days | 7 | 0 | 0 | 0 | 0 | 3 |
| penalty_calculation_basis | 5 | 2 | 0 | 0 | 0 | 3 |
| penalty_full_weeks_only | 1 | 9 | 0 | 0 | 0 | 0 |
| penalty_period_unit | 6 | 1 | 0 | 0 | 0 | 3 |
| penalty_present | 6 | 1 | 0 | 0 | 0 | 3 |
| penalty_rate_pct | 6 | 1 | 0 | 0 | 0 | 3 |
| quantity | 7 | 0 | 0 | 0 | 0 | 3 |
| subtotal_bdt | 7 | 1 | 0 | 0 | 0 | 2 |
| supplier_name | 7 | 0 | 0 | 0 | 0 | 3 |
| tin | 6 | 1 | 0 | 0 | 0 | 3 |
| unit | 7 | 0 | 0 | 0 | 0 | 3 |
| unit_price_bdt | 7 | 0 | 0 | 0 | 0 | 3 |
| validity_days | 7 | 0 | 0 | 0 | 0 | 3 |
| vat_amount_bdt | 7 | 1 | 0 | 0 | 0 | 2 |
| vat_included_in_price | 0 | 9 | 0 | 0 | 0 | 1 |
| vat_price_basis | 0 | 10 | 0 | 0 | 0 | 0 |
| vat_rate_pct | 7 | 1 | 0 | 0 | 0 | 2 |
| vat_shown_separately | 7 | 0 | 0 | 0 | 0 | 3 |

## Per pack

| Pack | Expected | Pipeline | Agrees | Expected rules | Triggered rules | Status |
|---|---|---|---|---|---|---|
| supplier-pack-01.pdf | NEEDS_REVIEW | NEEDS_REVIEW | yes | NR-03 | NR-03 | OK |
| supplier-pack-02.pdf | APPROVE | APPROVE | yes | - | - | OK |
| supplier-pack-03.pdf | REJECT | REJECT | yes | R-01 | R-01 | OK |
| supplier-pack-04.pdf | NEEDS_REVIEW | NEEDS_REVIEW | yes | NR-01 | NR-01 | OK |
| supplier-pack-05.pdf | REJECT | NEEDS_REVIEW | NO | R-02 | - | ERROR: extraction_invalid_json |
| supplier-pack-06.pdf | REJECT | REJECT | yes | R-03 | R-03 | OK |
| supplier-pack-07.pdf | NEEDS_REVIEW | NEEDS_REVIEW | yes | NR-02 | NR-02 | OK |
| supplier-pack-08.pdf | REJECT | REJECT | yes | NR-08, R-04 | NR-08, R-04 | OK |
| supplier-pack-09.pdf | NEEDS_REVIEW | NEEDS_REVIEW | NO | NR-03, NR-06 | - | ERROR: extraction_invalid_json |
| supplier-pack-10.pdf | REJECT | NEEDS_REVIEW | NO | NR-01, NR-05, NR-06, R-01, R-05 | - | ERROR: extraction_invalid_json |

Confusion (rows = expected, columns = pipeline; processing failures excluded): `{'APPROVE': {'APPROVE': 1, 'NEEDS_REVIEW': 0, 'REJECT': 0}, 'NEEDS_REVIEW': {'APPROVE': 0, 'NEEDS_REVIEW': 3, 'REJECT': 0}, 'REJECT': {'APPROVE': 0, 'NEEDS_REVIEW': 0, 'REJECT': 3}}`

## Failure analysis

Counts: `{'INVALID_JSON': 3, 'DECISION_MISMATCH': 3}`

- **supplier-pack-05.pdf** [processing] INVALID_JSON: ExtractionError: ValidationError: 8 validation errors for RawExtraction
line_items.0.item_code
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='E', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.description
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='Carton box, 5-ply', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.quantity
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='3,000', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.unit
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='piece', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.unit_price_bdt
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='92.00', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.line_total_bdt
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='276,000.00', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.vat_included_in_price
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value=None, input_type=NoneType]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.source_quote
  Extra inputs are not permitted [type=extra_forbidden, input_value='E Carton box, 5-ply 3,000 piece 92.00 276,000.00', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/extra_forbidden
- **supplier-pack-05.pdf** [decision] DECISION_MISMATCH: pipeline NEEDS_REVIEW vs ground truth REJECT (ERROR: extraction_invalid_json)
- **supplier-pack-09.pdf** [processing] INVALID_JSON: ExtractionError: ValidationError: 7 validation errors for RawExtraction
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
- **supplier-pack-09.pdf** [decision] DECISION_MISMATCH: pipeline NEEDS_REVIEW vs ground truth NEEDS_REVIEW (ERROR: extraction_invalid_json)
- **supplier-pack-10.pdf** [processing] INVALID_JSON: ExtractionError: ValidationError: 8 validation errors for RawExtraction
line_items.0.item_code
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='E', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.description
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='Carton box, 5-ply', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.quantity
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='3,000', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.unit
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='piece', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.unit_price_bdt
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='94.00', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.line_total_bdt
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='282,000.00', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.vat_included_in_price
  Input should be a valid dictionary or instance of RawField [type=model_type, input_value='yes', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/model_type
line_items.0.source_quote
  Extra inputs are not permitted [type=extra_forbidden, input_value='E Carton box, 5-ply 3,000 piece 94.00 282,000.00', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/extra_forbidden
- **supplier-pack-10.pdf** [decision] DECISION_MISMATCH: pipeline NEEDS_REVIEW vs ground truth REJECT (ERROR: extraction_invalid_json)
