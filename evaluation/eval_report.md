# M6 Evaluation Report

**Status: harness complete and tested; live evaluation NOT YET RUN.** No live accuracy figure exists yet, and none is
stated anywhere in this file. The build environment had no `GEMINI_API_KEY` and its network policy blocks the Gemini
endpoint, so no real call could be made. Nothing below substitutes fixtures for that run.

## 1. What is measured

Four measures are scored and reported separately. A correct final decision can rest on a wrong intermediate value, so
decision agreement is never reported as extraction accuracy.

| Measure | Question | Compared with |
|---|---|---|
| Extraction | Did the model return the right value for each field (19 document fields + 7 per line item, 26 per pack, 260 in all)? Right "not stated" answers count as correct; a value returned where the PDF has none is `SPURIOUS`. | `field_ground_truth.json` |
| Evidence | Is each quote verbatim on the page it cites, does it support the correct value, and did the verifier agree? Checked independently of the verifier, so verifier false accepts and rejects are counted. | the PDF page text |
| Arithmetic | Does each arithmetic check give the expected status and recalculated value? Checks that inherit an extraction error are reported both with and without. | `field_ground_truth.json` |
| Decision | Which rules were raised (FAIL or UNCERTAIN) and what was decided? | `ground_truth.json` (Checkpoint G) |

Extraction is scored in two views: the **model view** (what the model said, normalized but unverified) and the
**pipeline view** (what survived evidence verification and reached the rules). `vat_price_basis` is scored in the model
view only, because the verifier derives it from layout and the model is told to return null.

Rules for the run:

* A pack whose processing fails (API error, invalid JSON, unreadable PDF) is a failure in every measure. In particular a
  NEEDS_REVIEW produced by a processing error is **not** an agreement with a NEEDS_REVIEW ground truth (four packs have
  that expected decision).
* `live` never falls back to fixtures. Without a key it exits with code 2.
* Fixture runs go to a separately named folder and carry a "NOT A LIVE LLM MEASURE" banner.
* Ground-truth files are only read.

## 2. Running it

```
python evaluation/evaluate.py live --model gemini-3.5-flash-lite       # real API calls, all ten packs; always name the model
python evaluation/evaluate.py rescore evaluation/runs/live-<id>   # offline RESCORE of a saved live run (no API call)
python evaluation/evaluate.py fixtures                  # code regression only
```

Each run writes `evaluation/runs/<mode>-<UTC timestamp>/`: `metadata.json`, `raw/` (every model response verbatim, with
the API-reported model version, token usage and latency), `prompt/system_instruction.txt`, `results/`, `scores.json`
and `report.md`. Metadata records the requested model and the version the API reports, temperature and response format,
the SHA-256 of the system instruction and of each pack's full prompt, retry settings, package versions, the git commit
(and whether the tree was dirty), the last commit of each ground-truth file, and SHA-256 hashes of every input. The
API key is never written.

**Model.** M6 evaluates `gemini-3.5-flash-lite`: the default in `src/extractor.py`, the value in `.env.example` (a test keeps
the two equal) and the `--model` of the command above. Results from another model are a different experiment and are
never combined with these. The report records both the requested model and the version the API reports. `gemini-3.5-flash-lite` was chosen deliberately by the project owner; this differs from the review's recommendation of `gemini-3.8-flash`, and no result in this report says anything about `gemini-3.8-flash`.

**Rescore is not a live run.** It replays the raw responses of a saved live run through the pipeline and scorer, is
labelled `RESCORE` (never `LIVE RUN`), and names the original run, model and prompt hash. Before replaying it compares
the original run's SHA-256 of every input file (PDFs, purchase request, company rules, both answer keys), of the system
prompt and of the pipeline source (`main.py`, `src/*.py`) with what is on disk. If anything differs, or the original did
not record it, the rescore refuses (exit 2). `--allow-changed-inputs` overrides that, and the output is then labelled a
NEW EVALUATION of the recorded responses against changed inputs/code, listing each difference. The scorer itself is
allowed to differ: re-scoring with a corrected scorer is the point of a rescore. The original prompt is archived in the
rescore folder, and the current prompt hash is recorded beside it.

**Run artifacts.** A `live-*` folder (raw responses, prompt, metadata, scores, report) is the evidence for every
reported number and is committed. `fixtures-*` and `*/rescored/` are regenerable and ignored by `.gitignore`.

Checkpoint G ordering was checked against the real git history (see section 3). Result: the decision key was committed
after the ten M3 live-response fixtures, so git alone cannot show that it predates all model output on these packs.

## 3. Ground truth and its provenance

* **`ground_truth.json` (decisions).** Established manually at Checkpoint G, from the PDFs and `company-rules.md`, without an
  official answer key. Git history (`git log --follow`): first committed 2026-11-04T06:11:55Z (`efcf6a6`), revised in four
  later commits, last 2026-11-04T07:58:10Z (`2d61058`); not edited in M6. **Ordering caveat:** the ten M3 live Gemini
  response fixtures for these same packs were committed earlier, at 05:17:27Z (`a2fe74c`). So the key does *not* predate
  all model output on the real packs by commit order. That it was made from the PDFs and not from model output rests on the
  author's statement (and on the commit message of `d4a5f98`), not on git. Treat the in-sample caveat accordingly. If an error is found
  later, correct it in a separate commit and record the change and reason here; never adjust it to match output.
* **`field_ground_truth.json` (fields and arithmetic), added in M6.** The decision ground truth holds rules and prose, not
  per-field values, so a field-level key was needed. It was drafted on 2026-11-04 by an AI assistant (Claude) from the PDF
  text, committed in the same commit as the M6 harness (`7ddc6c6`, 2026-11-04T15:36:07+06:00), not on its own; it was committed
  before any live run (the first live run started 2026-11-04T12:22Z). It has **not yet been reviewed by the
  project owner**. Disclosure: before drafting it the assistant had viewed the first 3,500 characters of the pack-01
  fixture while inspecting the data format; every pack-01 value was re-read from the PDF text. A test confirms each
  non-null value appears in its PDF. Review it against the PDFs before quoting results; any correction goes in its own
  commit, recorded here.

## 4. Fixture regression (checks the code, not the model)

`python evaluation/evaluate.py fixtures` on the committed M3 fixtures: decision 10/10, rule sets 10/10, 260/260 fields in
the model view, 37/37 arithmetic checks. This only shows that the scorer, the answer key, the fixtures and the pipeline
agree with one another. The fixtures were captured in M3 with a model and version that were not recorded, and the prompt
was iterated against these same ten packs, so this is not an accuracy estimate. The harness tests damage the fixtures on
purpose (wrong value, missing value, invented value, fabricated quote, wrong page, non-supporting quote, API failure,
invalid JSON) to show the scorer reports each of them.

## 5. Live results

Everything in 5.1 and 5.2 is copied from `evaluation/runs/live-*/scores.json` and `metadata.json`; none of it is
restated from memory. All four runs are kept as the **baseline**. They are reported together, not as a best run.

### 5.1 Baseline: four runs, `gemini-3.5-flash-lite`, no response schema

All four runs used the same model, the same system prompt (SHA-256 `c8998d68...`), temperature 0, JSON mime type, and
one retry. Runs 2 and 3 are marked dirty only because `src/extractor.py` differed from run 1 by a single line, the
default model name (`gemini-3.8-flash` to `gemini-3.5-flash-lite`). That line cannot change a run that passes `--model`.
By the recorded source hashes, runs 2 and 3 ran identical code (no file differs) and still scored 9/10 and 7/10 on
decisions. Run 4 differs from run 2 only in `evaluation/evaluate.py` (text edits to the model name in help strings); no
pipeline file differs between runs 2, 3 and 4.

| | Run 1 | Run 2 | Run 3 | Run 4 |
|---|---|---|---|---|
| Run id | `20261104T122226Z` | `20261104T122945Z` | `20261104T123446Z` | `20261104T124853Z` |
| Commit / tree | `7ddc6c6` clean | `7ddc6c6` dirty | `7ddc6c6` dirty | `63db0d0` clean |
| Model reported by API | gemini-3.5-flash-lite | gemini-3.5-flash-lite | gemini-3.5-flash-lite | gemini-3.5-flash-lite |
| Packs that failed extraction | 03, 09 | 09 | 05, 09, 10 | 10 |
| Decision agreement (failures counted wrong) | 8/10 | 9/10 | 7/10 | 9/10 |
| Extraction fields correct, model view | 214/260 | 237/260 | 193/260 | 239/260 |
| Extraction fields correct, after verification | 206/252 | 228/251 | 186/253 | 230/251 |
| Value precision (values returned that were correct) | 176/176 | 199/199 | 155/155 | 201/201 |
| Quotes verbatim on the claimed page | 168/168 | 190/190 | 148/148 | 192/192 |
| Quotes supporting the correct value | 168/168 | 190/190 | 148/148 | 192/192 |
| Quotes supporting the value the model claimed | 168/168 | 190/190 | 148/148 | 192/192 |
| Verifier false accepts / false rejects | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Fabricated (spurious) line items | 0 | 0 | 0 | 0 |
| Arithmetic status agrees | 29/37 | 33/37 | 28/37 | 36/37 |
| Exact rule set | 8/10 | 9/10 | 7/10 | 9/10 |
| Right decision, wrong intermediate | 0 | 0 | 0 | 0 |

Arithmetic and rule counts include the packs that failed extraction, which score as wrong; evidence counts cover only
the packs that parsed. This is why the denominators of the evidence rows change from run to run.

**Processing reliability.** 7 of 40 pack-runs (17.5%) ended in a processing failure after the retry (observed
reliability 82.5%). Per run: 2, 1, 3 and 1 of 10. Pack 09 failed in runs 1 to 3 and processed in run 4 (on the retry);
pack 10 failed in runs 3 and 4; packs 03 and 05 failed once each. Which packs fail changes from run to run at
temperature 0, so the failure set of a single run is not a property of a pack.

**Conditional quality.** On every pack that parsed, in every run: every value returned was correct (value precision
100% in all four runs), every quote was verbatim and supported the claimed value, there were no verifier false accepts
or rejects, and no fabricated line items. All arithmetic checks, rule sets and decisions agreed with the key for those
packs. These statements are conditional on the pack having parsed, and they are in-sample (section 6).

**Decision accuracy versus processing reliability.** A failed pack is fail-closed to NEEDS_REVIEW, which is a safe
outcome but not a classification. The scorer counts every failed pack as a decision mismatch, even pack 09, where the
key itself expects NEEDS_REVIEW, because the pipeline never examined the document. That is an end-to-end mismatch caused
by a processing failure, not evidence that the rule engine judged any pack wrongly. In every run the only decision
mismatches are the packs that failed extraction. Run 4: 9/10, the mismatch being pack 10
(expected REJECT, returned NEEDS_REVIEW after the failed extraction).

**Pack 09** (printed line total 322,000 against 50,000 x 6.40 = 320,000) is the deliberate arithmetic trap. It first
processed in run 4 and was fully right: NEEDS_REVIEW, exactly the expected rules NR-03 and NR-06, with the mismatch
reported as 320,000.00 calculated against 322,000.00 printed.

### 5.2 Failure analysis: all seven failed pack-runs

Each failure was checked from its saved `raw/` responses and its `results/` error, not inferred from another pack.

| Run | Pack | Attempts | What the response was | Validation errors at |
|---|---|---|---|---|
| 1 | 03 | 2 | valid JSON, line item is a flat object of strings | `line_items.*` only |
| 1 | 09 | 2 | same | `line_items.*` only |
| 2 | 09 | 2 | same | `line_items.*` only |
| 3 | 05 | 2 | same | `line_items.*` only |
| 3 | 09 | 2 | same | `line_items.*` only |
| 3 | 10 | 2 | same | `line_items.*` only |
| 4 | 10 | 2 | same | `line_items.*` only |

* **All 14 attempts** (7 failures x 2) returned valid JSON. Re-validating each against `RawExtraction` gave errors under
  `line_items` only, with none outside it. The model
  wrote each line item as one object of plain strings with a single `source_quote` for the row, instead of one
  `{value, source_quote, page_number}` object per field. No top-level field failed validation in any failed attempt.
* **The error label is a misnomer.** The pipeline reports these as `extraction_invalid_json` (and the scorer as
  `INVALID_JSON`), but they are schema-conformance failures of well-formed JSON. The code was left unchanged; read it as
  "response did not match the required structure".
* **The retry helps sometimes.** 10 of the 33 successful pack-runs needed their second call. On the 7 failed pack-runs
  the second attempt had the same flat shape (the two attempts for pack 09 in run 3 were byte-identical). So the retry
  recovers some failures and cannot be relied on to recover them all.
* **Probable contributing cause (a hypothesis, not established).** The system prompt says every field is a
  `{value, source_quote, page_number}` object, but its line-item paragraph says the `source_quote` is "the ENTIRE table
  row", which can be read as one quote per row. The model's flat output matches that reading. The prompt was
  deliberately left unchanged (5.3).
* **No answer-key problem and no rule-engine problem** was found in any run: the mismatches are the failed packs.
* **Line-item pairing.** Packs are matched to both answer keys by filename. Line items within a pack are paired by
  position (line n of the response with line n of the key). For all 33 well-shaped responses in the four runs the
  returned `item_code` sequence equals the key's, so positional pairing did not affect any result; it would score a
  reordered response as wrong, never as right.

### 5.3 After the response schema (pending, reported separately)

**Change.** Every request now carries a response schema (`src/extractor.py`, `RESPONSE_SCHEMA`) built from
`RawExtraction`, requiring `{value, source_quote, page_number}` for every field including each line-item field. The
Pydantic validation, the single retry, the fail-closed NEEDS_REVIEW result, the system prompt (hash pinned by a test)
and the model are unchanged, so the run differs from the baseline in this one request setting. The evaluator records
the schema hash in `metadata.json` (`generation_config.response_schema_sha256`) and per call, and replay flags a
changed schema as drift.

**Procedure.** Commit; confirm a clean tree; run `python evaluation/evaluate.py live --model gemini-3.5-flash-lite`;
keep the folder as its own run. Do not merge it with the baseline runs and do not drop any baseline run. Compare
processing reliability (failed pack-runs of 10) and decision agreement against the baseline spread of 1 to 3 failed
packs and 7/10 to 9/10 decisions. One post-fix run is a small sample; repeat it if the comparison matters. Investigate
any failure from its `raw/` files before drawing a conclusion.

**Results.** *None yet. No post-fix run has been made.*

## 6. Limitations

* **Not held out.** The extraction prompt was developed against these same ten packs in M3, so live results are
  in-sample for the prompt. They show the pipeline works on this assessment set, not how it generalises.
* **Small and synthetic.** Ten one-page, single-line-item packs. Percentages carry wide uncertainty; one pack is 10 points.
* **One model, four baseline runs.** Temperature 0 is not a determinism guarantee: runs 2 and 3 used identical code
  and differ by 17 points of field accuracy and 2 of 10 decisions, purely through which packs failed. Report the spread,
  not the best run. Four runs of one model say nothing about other models.
* **Single-annotator ground truth.** The field key is AI-drafted and unreviewed (section 3). The decision key is a single
  person's manual reading, and pack 08 is rated MEDIUM confidence.
* **Evidence "supports" check is heuristic.** It uses number, period and keyword matching, and is not defined for a few
  boolean fields. It checks the quote, not whether the model understood the clause.
* **Arithmetic and rules depend on extraction.** An arithmetic check is flagged as inherited only when one of its own
  inputs (see `arithmetic_dependencies` in `scoring.py`) was extracted wrongly, but one wrong number can still cascade
  into several rule differences. `vat_price_basis`, an input of the VAT and grand checks, is verifier-derived and not
  scored, so it cannot be named as a cause.
* **Rule line attribution is partly inferred.** `ground_truth.json` lists rule IDs without lines. The scorer pins a
  per-item rule (R-01, NR-01, NR-02) to `line-1` only because every pack has exactly one line.
* **Derived fields.** `vat_price_basis` and the payment anchor come from layout and wording code, not the model, so they
  are not part of the extraction score.
