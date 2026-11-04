"""Live extraction -> offline replay fixtures (M3 task 8). Needs GEMINI_API_KEY in .env (never committed).

    python scripts/extract_fixtures.py --packs 01          # Definition-of-Done check on one pack
    python scripts/extract_fixtures.py                     # all ten packs
    python scripts/extract_fixtures.py --force             # re-extract even if a fixture exists (never hand-edit)

For each pack it reads the PDF (src/pdf_reader), calls Gemini (src/extractor.call_llm), checks fixture hygiene, and
saves the RawExtraction JSON to fixtures/mock_llm_responses/supplier-pack-NN.json.

A fixture holds ONLY raw value strings, short evidence quotes and page numbers: never the prompt, page text, PDF or
API key. A response that breaks that is NOT saved; fix the prompt and extract again. Fixtures are never edited by hand.

Quality gate: each response is normalized and verified BEFORE it is saved. Every value the model claimed must verify
(src/fixture_check.py); a claimed value that fails is a failure (exit code 1) and the fixture is not written. Fields the
model left null (no TIN/BIN, no penalty clause, an unstated penalty basis) are legitimate and are only listed. An
existing fixture is re-checked on every run. The script prints no extracted values and runs no rules, no arithmetic and
no pipeline.
Do not use this output (or the fixtures) to create or check evaluation/ground_truth.json: ground truth comes from reading the PDFs (AMB-11).
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.extractor import FIXTURE_MAX_QUOTE_CHARS, ExtractionError, call_llm, normalize_extraction  # noqa: E402
from src.fixture_check import claim_failures, unclaimed_fields  # noqa: E402
from src.models import RawExtraction  # noqa: E402
from src.pdf_reader import PDFReadError, extract_text  # noqa: E402
from src.verifier import verify_evidence  # noqa: E402

DATA_DIR = REPO_ROOT / "assessment-data"
FIXTURE_DIR = REPO_ROOT / "fixtures" / "mock_llm_responses"
FORBIDDEN_MARKERS = ("--- PAGE", "<DOCUMENT>", "</DOCUMENT>")


def load_pr():
    """The purchase request from purchase-request.csv."""
    import csv
    from decimal import Decimal

    from src.models import PRItem, PurchaseRequest

    with open(DATA_DIR / "purchase-request.csv", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    items = {
        r["Item"]: PRItem(item_code=r["Item"], description=r["Description"], unit=r["Unit"],
                          quantity_requested=Decimal(r["Quantity requested"]),
                          max_unit_price_bdt=Decimal(r["Maximum unit price (BDT)"]))
        for r in rows
    }
    return PurchaseRequest(pr_number=rows[0]["PR number"], items=items)


def strings_in(node):
    """Every string in a RawExtraction dump (values and quotes)."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from strings_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings_in(value)


def hygiene_problems(raw: RawExtraction) -> list[str]:
    problems = []
    for text in strings_in(raw.model_dump()):
        if any(marker in text for marker in FORBIDDEN_MARKERS):
            problems.append(f"contains a forbidden marker: {text[:60]!r}")
        if len(text) > FIXTURE_MAX_QUOTE_CHARS:
            problems.append(f"string longer than {FIXTURE_MAX_QUOTE_CHARS} chars: {text[:60]!r}...")
    return problems


def quality_problems(raw: RawExtraction, pdf: dict, pr) -> tuple[list[str], list[str]]:
    """(problems, unclaimed). Problems = fixture hygiene + every claimed value that did not verify. Unclaimed =
    fields the model left null (informational only: a missing TIN or penalty clause is a legitimate result)."""
    doc = verify_evidence(normalize_extraction(raw, pr), pdf)
    return hygiene_problems(raw) + [f"claim failed verification: {c}" for c in claim_failures(raw, doc)], unclaimed_fields(raw, doc)


def _stale_notice(name: str, target: Path, refreshing: bool) -> None:
    """A failed forced refresh keeps the old file (it is recovery material) but must never look like a success."""
    if refreshing:
        print(f"{name}: REFRESH FAILED \u2014 previous fixture NOT replaced. The existing file {target.name} is retained "
              "unchanged and is STALE relative to this attempted refresh; do not treat it as the result of this run.",
              file=sys.stderr)


def process_pack(name: str, pdf: dict, pr, target: Path, force: bool = False, client=None) -> bool:
    """One pack. True = a good fixture exists at ``target``; False = it does not (and nothing bad was written).

    Order: extract -> hygiene + verification -> ONLY THEN write. A fixture that fails is never saved. An existing
    fixture is re-checked on every run, so a bad one cannot hide behind 'fixture exists, skipped'."""
    refreshing = force and target.exists()  # a forced refresh replaces a fixture that already exists
    if target.exists() and not force:
        raw = RawExtraction.model_validate_json(target.read_text(encoding="utf-8"))
        origin = "existing fixture re-checked"
    else:
        try:
            raw = call_llm(pdf, pr, client=client)
        except ExtractionError as exc:
            print(f"{name}: FAILED: {exc}", file=sys.stderr)
            _stale_notice(name, target, refreshing)
            return False
        origin = "extracted"
    problems, unclaimed = quality_problems(raw, pdf, pr)
    if problems:
        verb = "NOT SAVED" if origin == "extracted" else "FIXTURE IS BAD (re-extract with --force after fixing the prompt)"
        print(f"{name}: {verb}. Never hand-edit a fixture.", file=sys.stderr)
        for problem in problems:
            print(f"    {problem}", file=sys.stderr)
        _stale_notice(name, target, refreshing)
        return False
    if origin == "extracted":
        target.write_text(raw.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"{name}: {origin}; every claimed value verified. Not claimed by the model: {', '.join(unclaimed) or 'none'}")
    return True


def main(argv: list[str] | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--packs", nargs="*", help="pack numbers such as 01 05 (default: all ten)")
    parser.add_argument("--force", action="store_true", help="re-extract even when a fixture already exists")
    args = parser.parse_args(argv)

    numbers = [f"{int(p):02d}" for p in args.packs] if args.packs else [f"{n:02d}" for n in range(1, 11)]
    pr = load_pr()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    for number in numbers:
        name = f"supplier-pack-{number}"
        try:
            pdf = extract_text(DATA_DIR / f"{name}.pdf")
        except PDFReadError as exc:
            failures += 1
            print(f"{name}: FAILED: {exc}", file=sys.stderr)
            continue
        if not process_pack(name, pdf, pr, FIXTURE_DIR / f"{name}.json", args.force, client):
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
