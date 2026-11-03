"""Debug helper (M2 task 5): print the text extracted from one or more PDFs, to check extraction quality
by eye. Not part of the pipeline.

    python scripts/dump_pdf_text.py assessment-data/supplier-pack-01.pdf
    python scripts/dump_pdf_text.py --save assessment-data/supplier-pack-*.pdf   # -> extracted_text/<name>.txt

Exit status is 1 if any PDF raised PDFReadError (each failure is reported on stderr; the rest still run).
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.pdf_reader import PDFReadError, extract_text  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdfs", nargs="+", help="PDF file(s) to dump")
    parser.add_argument("--save", action="store_true", help="write extracted_text/<pdf stem>.txt instead of printing")
    args = parser.parse_args(argv)

    out_dir = REPO_ROOT / "extracted_text"
    failures = 0
    for name in args.pdfs:
        try:
            result = extract_text(name)
        except PDFReadError as exc:
            failures += 1
            print(f"FAILED {name}: {exc}", file=sys.stderr)
            continue
        if args.save:
            out_dir.mkdir(exist_ok=True)
            target = out_dir / f"{Path(name).stem}.txt"
            target.write_text(result["full_text"], encoding="utf-8")
            print(f"{name}: {result['page_count']} page(s) -> {target.relative_to(REPO_ROOT)}")
        else:
            print(f"=== {name} ({result['page_count']} page(s)) ===")
            print(result["full_text"])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
