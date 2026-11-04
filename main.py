"""Command-line entry point (Milestone 5).

    python main.py                                  # all packs in assessment-data/
    python main.py --pack supplier-pack-01.pdf      # one pack
    python main.py --output outputs/my_report.txt   # choose the report file

Every pack ends up in the report. A known failure comes back from ``run_pipeline`` as a NEEDS_REVIEW result;
an unexpected exception is caught *here* (the one place that does) and turned into the same kind of result,
with the traceback sent to the log, never into the decision. One bad pack never stops the others.
"""

import argparse
import csv
import logging
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import ValidationError

from src.models import PackResult, PRItem, PurchaseRequest
from src.reporter import format_summary, generate_report
from src.workflow import build_error_result, run_pipeline

DATA_DIR = Path("assessment-data")
PR_FILE = "purchase-request.csv"
DEFAULT_OUTPUT = Path("outputs") / "report.txt"
PACK_PATTERN = re.compile(r"^supplier-pack-(\d+)\.pdf$")

log = logging.getLogger("supplier_checker")


class InputError(Exception):
    """A problem with the inputs (purchase request, pack name), not with a document. Exits with a message."""


# ---------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------

_PR_COLUMNS = {
    "pr": "PR number",
    "item": "Item",
    "description": "Description",
    "unit": "Unit",
    "qty": "Quantity requested",
    "price": "Maximum unit price (BDT)",
}


def _cell(row: dict[str, str | None], key: str, row_number: int) -> str:
    """One CSV cell as stripped text. A missing cell (short row) or a blank one is an input error."""
    column = _PR_COLUMNS[key]
    value = (row.get(column) or "").strip()
    if not value:
        raise InputError(f"{PR_FILE} row {row_number}: {column} is empty or missing")
    return value


def _decimal(text: str, column: str, row_number: int) -> Decimal:
    try:
        value = Decimal(text)  # from the string, never via float
    except InvalidOperation as exc:
        raise InputError(f"{PR_FILE} row {row_number}: {column} {text!r} is not a number") from exc
    if not value.is_finite():
        raise InputError(f"{PR_FILE} row {row_number}: {column} {text!r} must be a finite number")
    return value


def load_purchase_request(path: str | os.PathLike[str]) -> PurchaseRequest:
    """Read the purchase request CSV. ``utf-8-sig`` strips the byte-order mark on the first header cell.
    Every problem with the file's contents is raised as ``InputError`` with the row and field named."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = [c for c in _PR_COLUMNS.values() if c not in (reader.fieldnames or [])]
            if missing:
                raise InputError(f"{path}: missing column(s) {missing}; found {reader.fieldnames}")
            rows = list(reader)
    except OSError as exc:
        raise InputError(f"cannot read purchase request {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise InputError(f"{path} is not valid UTF-8 text: {exc}") from exc
    if not rows:
        raise InputError(f"{path} has no rows")

    numbers = {_cell(row, "pr", n) for n, row in enumerate(rows, start=2)}  # row 1 is the header
    if len(numbers) != 1:
        raise InputError(f"{path} mixes several PR numbers: {sorted(numbers)}")
    items: dict[str, PRItem] = {}
    for number, row in enumerate(rows, start=2):
        code = _cell(row, "item", number)
        if code in items:
            raise InputError(f"{PR_FILE} row {number}: item {code!r} appears twice")
        try:
            items[code] = PRItem(
                item_code=code,
                description=_cell(row, "description", number),
                unit=_cell(row, "unit", number),
                quantity_requested=_decimal(_cell(row, "qty", number), _PR_COLUMNS["qty"], number),
                max_unit_price_bdt=_decimal(_cell(row, "price", number), _PR_COLUMNS["price"], number),
            )
        except ValidationError as exc:  # anything the model itself rejects
            raise InputError(f"{PR_FILE} row {number}: invalid purchase-request values: {_first_problem(exc)}") from exc
    try:
        return PurchaseRequest(pr_number=numbers.pop(), items=items)
    except ValidationError as exc:
        raise InputError(f"{PR_FILE}: invalid purchase request: {_first_problem(exc)}") from exc


def _first_problem(exc: ValidationError) -> str:
    error = exc.errors()[0]
    field = ".".join(str(part) for part in error["loc"]) or "value"
    return f"{field}: {error['msg']}"


def find_packs(data_dir: str | os.PathLike[str], only: str | None = None) -> list[Path]:
    """All ``supplier-pack-N.pdf`` files sorted numerically, or just the one named by ``only``."""
    directory = Path(data_dir)
    if only is not None:
        candidate = Path(only)
        if not PACK_PATTERN.match(candidate.name):
            raise InputError(f"--pack must be named like supplier-pack-NN.pdf, got {candidate.name!r}")
        path = candidate if candidate.is_file() else directory / candidate.name
        if not path.is_file():
            raise InputError(f"pack not found: {only} (looked in {directory})")
        return [path]
    packs = [p for p in directory.glob("supplier-pack-*.pdf") if PACK_PATTERN.match(p.name)]
    if not packs:
        raise InputError(f"no supplier-pack-*.pdf files found in {directory}")
    return sorted(packs, key=lambda p: int(PACK_PATTERN.match(p.name).group(1)))


# ---------------------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------------------


def process_pack(pdf_path: str | os.PathLike[str], pr: PurchaseRequest) -> PackResult:
    """Run one pack. Never raises: an unexpected exception becomes a NEEDS_REVIEW result."""
    try:
        return run_pipeline(os.fspath(pdf_path), pr)
    except Exception as exc:
        log.exception("unexpected exception while processing %s", pdf_path)  # traceback to the log only
        return build_error_result(
            Path(pdf_path).name, "unexpected_exception", f"{type(exc).__name__}: {exc}"
        )


def process_all(pdf_paths: list[Path], pr: PurchaseRequest) -> list[PackResult]:
    """One result per pack, in order, whatever happens."""
    results = []
    for path in pdf_paths:
        log.info("processing %s", path.name)
        results.append(process_pack(path, pr))
    return results


def write_report(report: str, output: str | os.PathLike[str]) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check supplier quotes and contracts against a purchase request.")
    parser.add_argument("--pack", metavar="supplier-pack-NN.pdf", help="process only this pack")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"report file (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help=f"folder with the PDFs and PR (default: {DATA_DIR})")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr)

    try:
        pr = load_purchase_request(Path(args.data_dir) / PR_FILE)
        packs = find_packs(args.data_dir, args.pack)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not os.getenv("GEMINI_API_KEY"):
        log.warning("GEMINI_API_KEY is not set: every pack will be reported as ERROR: extraction_failed "
                    "(copy .env.example to .env and add your key)")

    results = process_all(packs, pr)
    path = write_report(generate_report(results), args.output)
    print(format_summary(results))
    print(f"\nFull report written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
