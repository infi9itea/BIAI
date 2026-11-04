# One-page note: Supplier Document Checker

## What was built
A command-line tool that reads each supplier quotation PDF, compares it with the purchase request, applies the company's Reject and Needs-a-person rules, and returns APPROVE, REJECT or NEEDS_REVIEW with the exact quoted text behind every rule result. Gemini (`gemini-3.5-flash-lite`) only extracts candidate values and quotes. Verification, `Decimal` arithmetic, the rules and the decision are plain Python, so the model never decides.

LangGraph was used for a fixed seven-node sequential pipeline. It makes each stage explicit and gives known failures (unreadable PDF, failed extraction) a defined path to a NEEDS_REVIEW result. There is no agent loop.

## How it was tested
- **Offline tests (1045, 20 of them for the web layer):** unit tests for every stage, edge and boundary cases, injection-structure tests, and a replay of ten saved model responses through the real pipeline. The replay matches all ten decisions and rule sets. It checks the code, not the model.
- **Live runs on all ten packs:** five runs of `gemini-3.5-flash-lite`, all kept with raw responses and metadata.

## Demo
A small Vercel app serves the page and an API. Sample packs replay the saved model responses through the real pipeline (no API key); uploading a PDF or ticking the live box makes a real Gemini call. A "Run all 10" table compares every pack with my answer key (10/10 decisions and rule sets). Replay is a regression check, not an accuracy measure.

## Results
No official answer key was supplied. The decision key was made by hand from the PDFs and was committed before the pipeline existed in the repository, though git cannot prove how the labels were produced. The per-field key was drafted by an AI assistant.

| | Baseline, 4 runs, no response schema | Run 5, with response schema |
|---|---|---|
| Packs failing to process | 7 of 40 | 0 of 10 |
| Decisions agreeing with key | 7 to 9 of 10 per run | 10 of 10 |

Run 5 also had 259/260 fields correct, 211/211 quotes verbatim and supported, and 37/37 arithmetic checks agreeing. Decision agreement is not extraction accuracy: pack 08 reached the right decision while missing its delivery time (two wrong intermediate rule results).

All seven baseline failures had the same cause: the model returned each line item as a flat object of strings instead of one `{value, source_quote, page_number}` object per field. A response schema fixed it in the first post-fix run.

## What does not work well
- Ten synthetic one-page packs, and the prompt was developed on them, so everything is in-sample. One run with the schema is too small to estimate reliability.
- The API returned transient 503 errors on three packs; the single retry recovered all three.
- The model can still miss a field (pack 08).
- The field answer key has not had an independent review, and the quote-support check is a heuristic.

## What I would improve with more time
- Repeat the live run several times and report the spread.
- Test on unseen packs, including multi-line-item and multi-page documents.
- Have a second person review the field key.
- Add a targeted second extraction pass for fields the model leaves empty when the text clearly states them.
- Try a larger model and compare it as a separate experiment.
