"""PDF ingestion and text extraction (Milestone 2).

Does one thing: turn a supplier PDF into per-page text. It does NOT interpret the text, call an LLM or
normalize anything (that is M3).

Safety contract (SPEC §7.2 / §7.3): absence of a field may only be concluded from a *complete* extraction.
So this module never hands back a partial result. A PDF that cannot be opened, a page that fails to
extract, or a page that visibly holds content we could not read as text raises ``PDFReadError``. The
pipeline turns that into a processing error (NEEDS_REVIEW), never into "the field is absent".

What counts as "could not read the page" (all raise ``PDFReadError`` naming the page):
  * the page extraction itself raised;
  * the page has no extractable text but does contain an image (typically a scanned page);
  * the page text contains font-mapping garbage (``(cid:N)``) or the Unicode replacement character.
A page that is genuinely blank (no text, no images) is complete and is recorded as ``""``.

Known limit: a page that mixes real text with a *scanned image of more text* extracts without error and
looks complete. Detecting that needs OCR, which is out of scope here.
"""

import os
import re
from typing import Any

import pdfplumber

PAGE_DELIMITER_TEMPLATE = "\n--- PAGE {n} ---\n"

_CID_PATTERN = re.compile(r"\(cid:\d+\)")
_REPLACEMENT_CHAR = "�"


class PDFReadError(Exception):
    """The PDF could not be read completely. Callers must treat this as a processing error."""


def _unreadable_reason(text: str, image_count: int) -> str | None:
    """Why this page's extraction cannot be trusted, or None if it can."""
    if _CID_PATTERN.search(text):
        return "text contains unmapped font glyphs '(cid:N)' (garbled extraction)"
    if _REPLACEMENT_CHAR in text:
        return "text contains the Unicode replacement character (garbled extraction)"
    if not text.strip() and image_count > 0:
        return f"no extractable text but the page contains {image_count} image(s) (possibly a scanned page)"
    return None


def extract_text(pdf_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Return ``{"full_text": str, "pages": [{"page_number": int, "text": str}], "page_count": int}``.

    Pages are numbered from 1 in document order. ``full_text`` is each page's text preceded by
    ``"\\n--- PAGE N ---\\n"``. Blank pages are kept as ``""``. Raises ``PDFReadError`` if the file is missing
    or unopenable, has no pages, any page cannot be read (see module docstring), or every page is empty.
    """
    path = os.fspath(pdf_path)
    if not os.path.exists(path):
        raise PDFReadError(f"PDF file not found: {path}")
    if not os.path.isfile(path):
        raise PDFReadError(f"PDF path is not a file: {path}")

    try:
        pdf = pdfplumber.open(path)
    except Exception as exc:  # pdfminer/pdfplumber raise many unrelated types for bad files
        raise PDFReadError(f"cannot open PDF {path}: {type(exc).__name__}: {exc}") from exc

    pages: list[dict[str, Any]] = []
    try:
        try:
            page_objects = list(pdf.pages)
        except Exception as exc:
            raise PDFReadError(f"cannot read page list of {path}: {type(exc).__name__}: {exc}") from exc
        if not page_objects:
            raise PDFReadError(f"PDF has no pages: {path}")

        for number, page in enumerate(page_objects, start=1):
            try:
                text = page.extract_text() or ""
                image_count = len(page.images)
            except Exception as exc:
                raise PDFReadError(
                    f"cannot extract text from page {number} of {path}: {type(exc).__name__}: {exc}"
                ) from exc
            reason = _unreadable_reason(text, image_count)
            if reason is not None:
                raise PDFReadError(f"page {number} of {path} not fully readable: {reason}")
            pages.append({"page_number": number, "text": text})
    finally:
        pdf.close()

    if all(not p["text"].strip() for p in pages):
        raise PDFReadError(f"no text could be extracted from any of the {len(pages)} page(s) of {path}")

    full_text = "".join(PAGE_DELIMITER_TEMPLATE.format(n=p["page_number"]) + p["text"] for p in pages)
    return {"full_text": full_text, "pages": pages, "page_count": len(pages)}
