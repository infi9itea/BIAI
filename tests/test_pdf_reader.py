"""M2 tests: PDF text extraction (IMPLEMENTATION_PLAN.md M2).

Offline: no API key, no network. Tests that need a supplied PDF FAIL (never skip) with a message naming the
missing file. Error-path tests use tiny PDFs built in tests/pdf_builders.py, so they need no supplied files.
"""

from pathlib import Path

import pdfplumber
import pytest

from src import pdf_reader
from src.pdf_reader import PDFReadError, _unreadable_reason, extract_text
from tests.pdf_builders import IMAGE_ONLY, build_pdf

ASSESSMENT_DIR = Path(__file__).resolve().parent.parent / "assessment-data"
PACK_NUMBERS = list(range(1, 11))
PACK_IDS = [f"pack-{n:02d}" for n in PACK_NUMBERS]


def require_pack(number: int, directory: Path | None = None) -> Path:
    """Path of supplier-pack-NN.pdf; FAILS (does not skip) with the file name if it is absent."""
    path = (directory if directory is not None else ASSESSMENT_DIR) / f"supplier-pack-{number:02d}.pdf"
    if not path.is_file():
        pytest.fail(f"required input file is missing: {path} (supplier-pack-{number:02d}.pdf must be in assessment-data/)")
    return path


def write_pdf(tmp_path: Path, pages: list, name: str = "synthetic.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(build_pdf(pages))
    return path


# ---------------------------------------------------------------------------------------------------------
# The ten supplied packs: one independently reported test each.
# ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("number", PACK_NUMBERS, ids=PACK_IDS)
def test_supplied_pack_extracts(number):
    path = require_pack(number)
    result = extract_text(str(path))  # any exception fails this test only

    assert result["full_text"].strip(), f"{path.name}: empty full_text"
    assert result["page_count"] >= 1
    assert len(result["pages"]) == result["page_count"]
    # Page numbers are preserved: 1..N in order, each page's text and delimiter present in full_text.
    assert [p["page_number"] for p in result["pages"]] == list(range(1, result["page_count"] + 1))
    for page in result["pages"]:
        assert f"--- PAGE {page['page_number']} ---" in result["full_text"]
        assert page["text"] in result["full_text"]
    # Smoke check that the text is the real document (every pack is a quotation), not just non-empty.
    assert "QUOTATION" in result["full_text"], f"{path.name}: expected quotation text, got {result['full_text'][:80]!r}"


def test_missing_supplied_pack_is_a_test_failure_not_a_skip(tmp_path):
    # The guard used by every pack test must raise a failure that names the file.
    with pytest.raises(pytest.fail.Exception, match=r"supplier-pack-03\.pdf"):
        require_pack(3, directory=tmp_path)


def test_all_ten_packs_present():
    missing = [f"supplier-pack-{n:02d}.pdf" for n in PACK_NUMBERS if not (ASSESSMENT_DIR / f"supplier-pack-{n:02d}.pdf").is_file()]
    assert not missing, f"missing from {ASSESSMENT_DIR}: {', '.join(missing)}"


# ---------------------------------------------------------------------------------------------------------
# Return shape and page handling (synthetic PDFs).
# ---------------------------------------------------------------------------------------------------------

def test_multi_page_preserves_page_numbers_and_text(tmp_path):
    path = write_pdf(tmp_path, ["Alpha page", "Beta page", "Gamma page"])
    result = extract_text(path)
    assert result["page_count"] == 3
    assert result["pages"] == [
        {"page_number": 1, "text": "Alpha page"},
        {"page_number": 2, "text": "Beta page"},
        {"page_number": 3, "text": "Gamma page"},
    ]
    assert "--- PAGE 1 ---" in result["full_text"]
    assert result["full_text"] == "\n--- PAGE 1 ---\nAlpha page\n--- PAGE 2 ---\nBeta page\n--- PAGE 3 ---\nGamma page"


def test_result_has_exactly_the_planned_keys(tmp_path):
    result = extract_text(write_pdf(tmp_path, ["x"]))
    assert set(result) == {"full_text", "pages", "page_count"}
    assert all(set(p) == {"page_number", "text"} for p in result["pages"])


def test_blank_page_is_recorded_as_empty_string_and_numbering_is_kept(tmp_path):
    result = extract_text(write_pdf(tmp_path, ["First", "", "Third"]))
    assert result["page_count"] == 3
    assert [p["page_number"] for p in result["pages"]] == [1, 2, 3]
    assert result["pages"][1]["text"] == ""
    assert result["pages"][2]["text"] == "Third"
    assert "--- PAGE 2 ---" in result["full_text"]


def test_accepts_str_and_pathlike(tmp_path):
    path = write_pdf(tmp_path, ["same"])
    assert extract_text(str(path)) == extract_text(path)


# ---------------------------------------------------------------------------------------------------------
# Unreadable / partially extracted PDFs are errors, never silently-incomplete results.
# ---------------------------------------------------------------------------------------------------------

def test_nonexistent_path_raises_with_useful_message(tmp_path):
    missing = tmp_path / "nope.pdf"
    with pytest.raises(PDFReadError, match="not found") as info:
        extract_text(str(missing))
    assert str(missing) in str(info.value)


def test_directory_path_raises(tmp_path):
    with pytest.raises(PDFReadError, match="not a file"):
        extract_text(str(tmp_path))


def test_empty_file_raises(tmp_path):
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    with pytest.raises(PDFReadError, match="cannot open"):
        extract_text(path)


def test_non_pdf_content_raises(tmp_path):
    path = tmp_path / "fake.pdf"
    path.write_text("this is definitely not a PDF")
    with pytest.raises(PDFReadError):
        extract_text(path)


def test_all_pages_blank_raises(tmp_path):
    with pytest.raises(PDFReadError, match="no text could be extracted"):
        extract_text(write_pdf(tmp_path, ["", ""]))


def test_scanned_image_only_pdf_raises_not_empty_result(tmp_path):
    with pytest.raises(PDFReadError, match="page 1 .*image"):
        extract_text(write_pdf(tmp_path, [IMAGE_ONLY]))


def test_one_unreadable_page_among_good_pages_fails_whole_document(tmp_path):
    # Partial extraction: page 2 is a scan. We must NOT return pages 1 and 3 as if they were the document.
    path = write_pdf(tmp_path, ["Good page one", IMAGE_ONLY, "Good page three"])
    with pytest.raises(PDFReadError, match="page 2"):
        extract_text(path)


def test_page_level_exception_fails_whole_document_naming_the_page(tmp_path, monkeypatch):
    path = write_pdf(tmp_path, ["one", "two", "three"])
    original = pdfplumber.page.Page.extract_text

    def flaky(self, *args, **kwargs):
        if self.page_number == 2:
            raise RuntimeError("boom")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pdfplumber.page.Page, "extract_text", flaky)
    with pytest.raises(PDFReadError, match=r"page 2.*boom"):
        extract_text(path)


def test_truncated_pdf_never_returns_a_silent_partial_result(tmp_path):
    data = build_pdf(["Page one text", "Page two text", "Page three text"])
    path = tmp_path / "truncated.pdf"
    path.write_bytes(data[: len(data) // 2])
    try:
        result = extract_text(path)
    except PDFReadError:
        return  # correct: reported as a processing error
    # If the parser could recover, it must have recovered every page.
    assert result["page_count"] == 3, f"silent partial extraction: {result['page_count']} of 3 pages"


@pytest.mark.parametrize("text", ["Total (cid:12)(cid:45) 5,000", "Price ��"])
def test_garbled_text_is_unreadable(text):
    assert _unreadable_reason(text, 0) is not None


@pytest.mark.parametrize("text,images", [("Normal text", 0), ("Normal text with logo", 2), ("", 0), ("   \n", 0)])
def test_clean_or_blank_page_is_readable(text, images):
    assert _unreadable_reason(text, images) is None


def test_whitespace_only_page_with_image_is_unreadable():
    assert _unreadable_reason("  \n", 1) is not None


def test_garbled_page_text_raises_from_extract_text(tmp_path, monkeypatch):
    path = write_pdf(tmp_path, ["fine", "also fine"])
    monkeypatch.setattr(pdfplumber.page.Page, "extract_text", lambda self, *a, **k: "(cid:7)(cid:9)")
    with pytest.raises(PDFReadError, match="page 1 .*garbled"):
        extract_text(path)


def test_file_handle_is_closed_after_error(tmp_path, monkeypatch):
    closed = []
    real_open = pdfplumber.open

    def tracking_open(*args, **kwargs):
        pdf = real_open(*args, **kwargs)
        original_close = pdf.close
        pdf.close = lambda: (closed.append(True), original_close())[1]
        return pdf

    monkeypatch.setattr(pdf_reader.pdfplumber, "open", tracking_open)
    with pytest.raises(PDFReadError):
        extract_text(write_pdf(tmp_path, [IMAGE_ONLY]))
    assert closed, "PDF was not closed after a read error"
