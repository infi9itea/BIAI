"""Milestone 4 tests: the sequential LangGraph workflow. Synthetic data and mocks only; no live API call and
no real supplier pack."""

import json

import pytest

from src import extractor, pdf_reader, verifier, workflow
from src.extractor import ExtractionError
from src.models import Decision, PackResult
from src.pdf_reader import PDFReadError
from src.workflow import NODE_NAMES, build_error_result, build_graph, run_pipeline
from tests.builders import make_doc
from tests.builders import make_pr as builders_pr
from tests.m3_helpers import GOOD_RAW, SYNTH_TEXT, FakeClient, make_pdf_result
from tests.m3_helpers import make_pr as m3_pr

PDF_PATH = "synthetic/pack.pdf"


@pytest.fixture(autouse=True)
def fresh_graph(monkeypatch):
    """run_pipeline caches its compiled graph. Reset it before and after every test so a graph compiled around
    a test's patched nodes can never be reused by another test."""
    monkeypatch.setattr(workflow, "_GRAPH", None)
    yield
    workflow._GRAPH = None


@pytest.fixture
def mocked(monkeypatch):
    """Readable PDF, canned extraction, pass-through verifier."""
    doc = make_doc()
    monkeypatch.setattr(pdf_reader, "extract_text", lambda path: make_pdf_result([SYNTH_TEXT]))
    monkeypatch.setattr(extractor, "extract_fields", lambda pdf_result, pr, client=None: doc)
    monkeypatch.setattr(verifier, "verify_evidence", lambda extracted, pdf_result: extracted)
    return doc


def test_full_graph_produces_pack_result(mocked):
    result = run_pipeline(PDF_PATH, builders_pr())
    assert isinstance(result, PackResult)
    assert result.pack_filename == "pack.pdf"
    assert result.processing_status == "OK"
    assert result.processing_error_detail is None
    assert result.extracted is mocked
    assert result.arithmetic_checks and result.rule_results
    assert result.decision in set(Decision)
    assert result.reason.strip()


def test_all_seven_nodes_present_in_graph():
    nodes = set(build_graph().get_graph().nodes)
    assert set(NODE_NAMES) <= nodes
    assert len(NODE_NAMES) == 7


def test_nodes_run_in_fixed_order(mocked, monkeypatch):
    order = []
    for name in NODE_NAMES:  # all seven, including format_output
        original = getattr(workflow, name)

        def wrapper(state, _name=name, _orig=original):
            order.append(_name)
            return _orig(state)

        monkeypatch.setattr(workflow, name, wrapper)
    run_pipeline(PDF_PATH, builders_pr())
    assert order == list(NODE_NAMES)


def test_error_path_skips_middle_nodes(monkeypatch):
    """A known failure visits only read_pdf and format_output."""
    order = []
    for name in NODE_NAMES:
        original = getattr(workflow, name)

        def wrapper(state, _name=name, _orig=original):
            order.append(_name)
            return _orig(state)

        monkeypatch.setattr(workflow, name, wrapper)
    monkeypatch.setattr(pdf_reader, "extract_text", lambda p: (_ for _ in ()).throw(PDFReadError("cannot open PDF")))
    run_pipeline(PDF_PATH, builders_pr())
    assert order == ["read_pdf", "format_output"]


def test_cached_graph_is_not_left_behind_by_patched_nodes(mocked, monkeypatch):
    monkeypatch.setattr(workflow, "read_pdf", lambda state: {"pdf_result": make_pdf_result([SYNTH_TEXT])})
    run_pipeline(PDF_PATH, builders_pr())
    assert workflow._GRAPH is not None  # compiled around the patched node
    monkeypatch.undo()
    workflow._GRAPH = None  # what the autouse fixture guarantees between tests


@pytest.mark.parametrize(
    "message, code",
    [
        ("cannot open PDF x.pdf: PDFSyntaxError: bad", "pdf_unreadable"),
        ("page 2 of x.pdf not fully readable: scanned", "pdf_unreadable"),
        ("no text could be extracted from any of the 3 page(s) of x.pdf", "pdf_no_text"),
    ],
)
def test_pdf_read_error_becomes_needs_review(monkeypatch, message, code):
    def boom(path):
        raise PDFReadError(message)

    monkeypatch.setattr(pdf_reader, "extract_text", boom)
    extract_called = []
    monkeypatch.setattr(extractor, "extract_fields", lambda *a, **k: extract_called.append(1))
    result = run_pipeline(PDF_PATH, builders_pr())
    assert result.decision == Decision.NEEDS_REVIEW
    assert result.processing_status == f"ERROR: {code}"
    assert message in result.processing_error_detail
    assert not extract_called  # the run went straight to format_output


@pytest.mark.parametrize("code", ["extraction_failed", "extraction_invalid_json"])
def test_extraction_error_becomes_needs_review(monkeypatch, code):
    def boom(pdf_result, pr, client=None):
        raise ExtractionError(code, "model unavailable")

    monkeypatch.setattr(pdf_reader, "extract_text", lambda path: make_pdf_result([SYNTH_TEXT]))
    monkeypatch.setattr(extractor, "extract_fields", boom)
    verify_called = []
    monkeypatch.setattr(verifier, "verify_evidence", lambda *a: verify_called.append(1))
    result = run_pipeline(PDF_PATH, builders_pr())
    assert result.decision == Decision.NEEDS_REVIEW
    assert result.processing_status == f"ERROR: {code}"
    assert "model unavailable" in result.processing_error_detail
    assert not verify_called


def test_unexpected_exception_is_not_swallowed(monkeypatch):
    def boom(path):
        raise RuntimeError("something else broke")

    monkeypatch.setattr(pdf_reader, "extract_text", boom)
    with pytest.raises(RuntimeError, match="something else broke"):
        run_pipeline(PDF_PATH, builders_pr())


def test_build_error_result_shape():
    result = build_error_result("supplier-pack-03.pdf", "pdf_unreadable", "PDFReadError: broken")
    assert result.decision == Decision.NEEDS_REVIEW
    assert result.processing_status == "ERROR: pdf_unreadable"
    assert result.processing_error_detail == "PDFReadError: broken"
    assert "pdf_unreadable" in result.reason and "human" in result.reason
    assert result.extracted is None
    assert result.arithmetic_checks == [] and result.rule_results == []
    assert result.assumptions_applied == []


def test_assumptions_are_union_of_rule_result_assumptions(mocked):
    result = run_pipeline(PDF_PATH, builders_pr())
    expected = sorted({a for r in result.rule_results for a in r.assumptions_applied})
    assert result.assumptions_applied == expected
    assert len(result.assumptions_applied) == len(set(result.assumptions_applied))


def test_real_stages_end_to_end_with_fake_llm_client(monkeypatch):
    """Real normalization, verification, arithmetic, rules and aggregation; only the LLM client is fake."""
    monkeypatch.setattr(pdf_reader, "extract_text", lambda path: make_pdf_result([SYNTH_TEXT]))
    client = FakeClient([json.dumps(GOOD_RAW)])
    result = run_pipeline(PDF_PATH, m3_pr(), client=client)
    assert result.processing_status == "OK"
    assert result.extracted.supplier_name.value == "Acme Mills Ltd"
    assert result.rule_results
    assert len(client.calls) == 1


def test_runs_are_independent(monkeypatch, mocked):
    """A failure on one pack must not leak into the next (the batch loop in M5 relies on this)."""
    good = run_pipeline(PDF_PATH, builders_pr())
    monkeypatch.setattr(pdf_reader, "extract_text", lambda p: (_ for _ in ()).throw(PDFReadError("cannot open PDF")))
    bad = run_pipeline(PDF_PATH, builders_pr())
    monkeypatch.setattr(pdf_reader, "extract_text", lambda path: make_pdf_result([SYNTH_TEXT]))
    again = run_pipeline(PDF_PATH, builders_pr())
    assert bad.processing_status.startswith("ERROR")
    assert good.processing_status == again.processing_status == "OK"
    assert good.decision == again.decision
