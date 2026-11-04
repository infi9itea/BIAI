# Evaluation run `live-20261104T124853Z`

**LIVE RUN** - real Gemini API calls; a failed call is counted as a failure, never replaced by a fixture.

- Model requested: `gemini-3.5-flash-lite`; reported by API: `['gemini-3.5-flash-lite']`
- Commit: `63db0d0fd836dc2012cb852bc63ac7ea7a3bf424` (dirty: False); ground truth modified in working tree: False
- Started 2026-11-04T12:48:53.195340+00:00, finished 2026-11-04T12:50:06.844741+00:00; SDK google-genai 2.34.0

## Headline: four separate measures

| Measure | Result |
|---|---|
| Extraction - model output, field values correct | 239/260 (91.9%) |
| Extraction - after evidence verification, field values correct | 230/251 (91.6%) |
| Evidence - quotes verbatim on the cited page | 192/192 (100.0%) |
| Evidence - quote supports the correct value | 192/192 (100.0%) |
| Evidence - quote supports the value the model claimed | 192/192 (100.0%) |
| Arithmetic - check status agrees with expected | 36/37 (97.3%) |
| Arithmetic - printed value read by the pipeline matches the PDF | 36/37 (97.3%) |
| Arithmetic - status agrees, excluding checks inheriting an extraction error in their own inputs | 36/37 (97.3%) |
| Rules - exact rule set matches ground truth | 9/10 (90.0%) |
| Rules - exact rule set including the line each per-item rule fired on | 9/10 (90.0%) |
| Line items - fabricated (beyond the answer key) / returned | 0 / 9 |
| **Decision agreement** (processing failures count as wrong) | 9/10 (90.0%) |
| Decision agreement with no other error in the pack | 9/9 (100.0%) |

Decision agreement is not extraction accuracy: a correct decision can rest on a wrong intermediate value. Right decision, wrong intermediate: none.

## Extraction detail (model view)

- Value recall (fields that have a value in the PDF): 201/222 (90.5%)
- Value precision (values returned that are right): 201/201 (100.0%)
- Outcomes: {'CORRECT': 201, 'CORRECT_NULL': 38, 'NOT_EXTRACTED': 21}
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
| subtotal_bdt | 9 | 1 | 0 | 0 | 0 | 0 |
| supplier_name | 9 | 0 | 0 | 0 | 0 | 1 |
| tin | 8 | 1 | 0 | 0 | 0 | 1 |
| unit | 9 | 0 | 0 | 0 | 0 | 1 |
| unit_price_bdt | 9 | 0 | 0 | 0 | 0 | 1 |
| validity_days | 9 | 0 | 0 | 0 | 0 | 1 |
| vat_amount_bdt | 9 | 1 | 0 | 0 | 0 | 0 |
| vat_included_in_price | 0 | 9 | 0 | 0 | 0 | 1 |
| vat_price_basis | 0 | 10 | 0 | 0 | 0 | 0 |
| vat_rate_pct | 9 | 1 | 0 | 0 | 0 | 0 |
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
| supplier-pack-09.pdf | NEEDS_REVIEW | NEEDS_REVIEW | yes | NR-03, NR-06 | NR-03, NR-06 | OK |
| supplier-pack-10.pdf | REJECT | NEEDS_REVIEW | NO | NR-01, NR-05, NR-06, R-01, R-05 | - | ERROR: extraction_invalid_json |

Confusion (rows = expected, columns = pipeline; processing failures excluded): `{'APPROVE': {'APPROVE': 1, 'NEEDS_REVIEW': 0, 'REJECT': 0}, 'NEEDS_REVIEW': {'APPROVE': 0, 'NEEDS_REVIEW': 4, 'REJECT': 0}, 'REJECT': {'APPROVE': 0, 'NEEDS_REVIEW': 0, 'REJECT': 4}}`

## Failure analysis

Counts: `{'INVALID_JSON': 1, 'DECISION_MISMATCH': 1}`

- **supplier-pack-10.pdf** [processing] INVALID_JSON: ExtractionError: ValidationError: 9 validation errors for RawExtraction
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
line_items.0.page_number
  Extra inputs are not permitted [type=extra_forbidden, input_value=1, input_type=int]
    For further information visit https://errors.pydantic.dev/2.14/v/extra_forbidden
- **supplier-pack-10.pdf** [decision] DECISION_MISMATCH: pipeline NEEDS_REVIEW vs ground truth REJECT (ERROR: extraction_invalid_json)
