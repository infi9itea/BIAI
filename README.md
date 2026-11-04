# Supplier Document Checker

An AI-assisted checker for Aamar Garments Ltd (a fictional company). It reads each supplier pack (a PDF with a quotation and its terms), compares it with the purchase request, applies the company rules in `assessment-data/company-rules.md`, and gives one decision per pack: **APPROVE**, **REJECT** or **NEEDS_REVIEW**, with the exact quoted text behind each result.

The design keeps the language model away from the decision. Gemini only *extracts* candidate values and quotes. Plain Python then verifies each quote against the PDF text, recalculates the arithmetic with `Decimal`, applies the rules and makes the decision. When something cannot be established, the pack goes to NEEDS_REVIEW; it is never guessed.

## Prerequisites

- **Python 3.13** (the pinned dependencies were tested with 3.13.18).
- A free **Gemini API key** from Google AI Studio (<https://aistudio.google.com/apikey>). It is needed only for live runs. The offline tests need no key and no network.

## Setup

```bash
git clone <your-repo-url> BIAI-main
cd BIAI-main
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # Windows: copy .env.example .env
```

Edit `.env`:

```
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-3.5-flash-lite
```

`GEMINI_MODEL` is the model used for extraction. The default in `src/extractor.py`, `.env.example` and the documents is `gemini-3.5-flash-lite`, and a test keeps the first two equal. Never commit `.env` (it is git-ignored).

## Run

Process all ten supplied packs (live Gemini calls):

```bash
python main.py
```

Process one pack:

```bash
python main.py --pack supplier-pack-01.pdf
```

Other options: `--output outputs/my_report.txt` (default `outputs/report.txt`) and `--data-dir <folder>` (default `assessment-data`). A summary is printed and the full report is written to the output file. Every pack appears in the report; a pack that cannot be processed is reported as NEEDS_REVIEW with an error code, never dropped and never approved by default.

## Tests

Offline tests (no API key, no network; they need the supplied PDFs in `assessment-data/`):

```bash
python -m pytest tests/ -v -m "not integration"
```

All tests, including the one marked `integration`, which makes a live Gemini call and needs `GEMINI_API_KEY`:

```bash
python -m pytest tests/ -v
```

At the time of writing the offline suite is 1025 tests. It includes:

- `tests/test_replay.py`: replays the ten saved model responses in `fixtures/mock_llm_responses/` through the real pipeline and checks each decision and rule set against `evaluation/ground_truth.json`.
- `tests/test_injection.py`: shows that a decision key in model output is rejected and that planted text cannot change rule results. This is "the model cannot influence rule outputs", not a claim of prompt-injection immunity.
- `tests/test_edge_cases.py`: field states and boundary values (for example advance exactly 20%, price exactly 3% over, payment 30 to 90 days).

## Live evaluation

```bash
python evaluation/evaluate.py live --model gemini-3.5-flash-lite
python evaluation/evaluate.py rescore evaluation/runs/live-<id>     # offline re-score of a saved run
python evaluation/evaluate.py fixtures                              # code regression only, not a model measure
```

Each live run writes a folder under `evaluation/runs/` holding the raw model responses, the prompt, the run metadata and the scores. The results, their limits and the failure analysis are in `evaluation/eval_report.md`. A short summary is in `ONE_PAGE_NOTE.md`.

## How it works

A fixed, sequential LangGraph workflow of seven nodes, with no agent loop:

```
read_pdf -> extract_fields -> verify_evidence -> check_arithmetic
         -> evaluate_rules -> aggregate_decision -> format_output
```

| Stage | What it does | Uses the model? |
|---|---|---|
| `read_pdf` | Per-page text with pdfplumber | no |
| `extract_fields` | Gemini returns `{value, source_quote, page_number}` for each field, under a response schema; Pydantic validates it (`extra="forbid"`); values are then normalised in code | **yes** |
| `verify_evidence` | Each quote must appear verbatim on the cited page and support the value, or the field becomes UNKNOWN | no |
| `check_arithmetic` | Recalculates line totals, subtotal, VAT and grand total with `Decimal` | no |
| `evaluate_rules` | Applies every rule (R-01 to R-05, NR-01 to NR-08), keeping all results | no |
| `aggregate_decision` | Any definite Reject failure rejects; otherwise any failure or uncertainty means NEEDS_REVIEW; otherwise APPROVE | no |
| `format_output` | Builds the evidence-backed result | no |

A PDF that cannot be read, or an extraction that fails after one retry, skips straight to `format_output` as a NEEDS_REVIEW processing error.

## Folder structure

```
main.py                 CLI entry point
src/                    models, pdf_reader, extractor, verifier, arithmetic, rule_engine, workflow, reporter
assessment-data/        the ten supplier PDFs, purchase-request.csv, company-rules.md
fixtures/mock_llm_responses/   saved model responses for the offline replay (committed)
evaluation/             evaluate.py, scoring.py, answer keys, eval_report.md, runs/ (live run evidence)
tests/                  offline test suite
docs/                   SPEC.md, IMPLEMENTATION_PLAN.md, PROJECT_GUIDE.md
```

## Known limitations

- **Small, synthetic and in-sample.** Ten one-page, single-line-item packs. The extraction prompt was developed against these same packs, so results show the pipeline works on this set, not how it generalises. One pack is ten percentage points.
- **Extraction is not fully reliable.** In four baseline live runs without a response schema, 7 of 40 pack-runs ended in a processing failure (the model returned line items in the wrong shape). With the response schema, one run processed all ten packs, but that is a single small sample and the API still returned transient 503 errors that the retry recovered. Failed packs fail closed to NEEDS_REVIEW.
- **One known extraction miss.** In the post-schema run, the model missed pack 08's delivery time. The final decision was still correct because a different rule rejected the pack, but two intermediate results were wrong.
- **Field answer key is unreviewed by an independent person.** The per-field key used for the extraction scores was drafted by an AI assistant from the PDFs. The decision key was made manually, before the pipeline existed in the repository, but git cannot show how the labels were produced.
- **Evidence-support check is heuristic.** It uses number, period and keyword matching, not a human reading of each clause.
- **Documented assumptions.** Where the rules are ambiguous the code records an assumption ID (AMB-xx, see `docs/SPEC.md`) in the result and does not convert units, for example a per-day penalty is never converted to per-week.
- **Not a production system.** No authentication, queueing, concurrency control or data protection beyond the project's own requirements.
