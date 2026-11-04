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
python evaluation/evaluate.py live --model gemini-3.8-flash       # real API calls, all ten packs; always name the model
python evaluation/evaluate.py rescore evaluation/runs/live-<id>   # offline RESCORE of a saved live run (no API call)
python evaluation/evaluate.py fixtures                  # code regression only
```

Each run writes `evaluation/runs/<mode>-<UTC timestamp>/`: `metadata.json`, `raw/` (every model response verbatim, with
the API-reported model version, token usage and latency), `prompt/system_instruction.txt`, `results/`, `scores.json`
and `report.md`. Metadata records the requested model and the version the API reports, temperature and response format,
the SHA-256 of the system instruction and of each pack's full prompt, retry settings, package versions, the git commit
(and whether the tree was dirty), the last commit of each ground-truth file, and SHA-256 hashes of every input. The
API key is never written.

**Model.** M6 evaluates `gemini-3.8-flash`: the default in `src/extractor.py`, the value in `.env.example` (a test keeps
the two equal) and the `--model` of the command above. Results from another model are a different experiment and are
never combined with these. The report records both the requested model and the version the API reports.

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

Before the live run, check Checkpoint G ordering in the real repository, which this archive cannot show (it has no
`.git`): `git log --follow evaluation/ground_truth.json` must predate the first real-pack pipeline run.

## 3. Ground truth and its provenance

* **`ground_truth.json` (decisions).** Established manually at Checkpoint G, from the PDFs and `company-rules.md`, before
  the pipeline was first run on the real packs, without an official answer key. Not edited in M6. If an error is found
  later, correct it in a separate commit and record the change and reason here; never adjust it to match output.
* **`field_ground_truth.json` (fields and arithmetic), added in M6.** The decision ground truth holds rules and prose, not
  per-field values, so a field-level key was needed. It was drafted on 2026-11-04 by an AI assistant (Claude) from the PDF
  text, committed on its own **before** the harness and before any live run. It has **not yet been reviewed by the
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

*Pending. Fill from `evaluation/runs/live-<id>/report.md` after the run; copy the numbers, do not restate them from memory.*

| Measure | Result |
|---|---|
| Model requested / reported by API | |
| Extraction, model view | |
| Extraction, after verification | |
| Evidence verbatim / supports correct value / supports claimed value | |
| Verifier false accepts / false rejects | |
| Fabricated line items | |
| Arithmetic status agrees / printed value agrees | |
| Exact rule set / including line attribution | |
| Decision agreement (N/10, failures counted wrong) | |
| Right decision, wrong intermediate | |

### Failure analysis

*Pending.* The run's `report.md` lists every discrepancy by pack under these categories; the root cause for each
mismatch belongs here once it has been investigated: `API_FAILURE`, `INVALID_JSON`, `PDF_READ_FAILURE`, `WRONG_VALUE`,
`MISSED_VALUE`, `SPURIOUS_VALUE`, `SPURIOUS_LINE`, `QUOTE_NOT_VERBATIM_ON_PAGE`, `QUOTE_WRONG_PAGE`,
`QUOTE_DOES_NOT_SUPPORT_VALUE`, `QUOTE_DOES_NOT_SUPPORT_CLAIMED_VALUE`, `VERIFIER_FALSE_ACCEPT`,
`VERIFIER_REJECTED_CORRECT_VALUE`, `VERIFIED_WRONG_VALUE`, `ABSENCE_NOT_ESTABLISHED`, `ARITHMETIC_INHERITED_FROM_EXTRACTION`,
`ARITHMETIC_MISMATCH`, `RULES_DIFFER_AFTER_UPSTREAM_ERROR`, `RULES_DIFFER_NO_UPSTREAM_ERROR`,
`RULE_ATTRIBUTED_TO_WRONG_ITEM`, `DECISION_MISMATCH`,
`RIGHT_DECISION_WRONG_INTERMEDIATE`. The categories are assigned mechanically; a person must confirm each root cause.
`VERIFIED_WRONG_VALUE` (a wrong value that passed verification) and `RULES_DIFFER_NO_UPSTREAM_ERROR` (a rule-engine
or ground-truth problem) deserve attention first.

## 6. Limitations

* **Not held out.** The extraction prompt was developed against these same ten packs in M3, so live results are
  in-sample for the prompt. They show the pipeline works on this assessment set, not how it generalises.
* **Small and synthetic.** Ten one-page, single-line-item packs. Percentages carry wide uncertainty; one pack is 10 points.
* **One model, one run.** Temperature 0 is not a determinism guarantee for the API. A single run does not show run-to-run
  variation; repeat it before drawing conclusions about small differences.
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
