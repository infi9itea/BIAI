"""Milestone 5 tests: CLI behaviour and the report, with ``run_pipeline`` mocked. Synthetic data only; no live
API call and no real supplier pack."""

import re
from decimal import Decimal
from pathlib import Path

import pytest

import main
from src import arithmetic, reporter, rule_engine
from src.models import Decision, PackResult
from src.workflow import build_error_result
from tests.builders import absent, line, make_doc
from tests.builders import make_pr as builders_pr

PR = builders_pr()

PR_CSV = (
    "\ufeffPR number,Item,Description,Unit,Quantity requested,Maximum unit price (BDT)\n"
    'PR-TEST,A,"Cotton twill fabric, 280 gsm",metre,12000,480.00\n'
    'PR-TEST,B,"Metal zipper, 18 cm",piece,20000,38.00\n'
)


def ok_result(name: str, doc=None) -> PackResult:
    """A genuine OK PackResult built from the real arithmetic and rule engine on synthetic data."""
    doc = doc or make_doc()
    checks = arithmetic.run_arithmetic_checks(doc)
    results = rule_engine.evaluate_all_rules(doc, PR, checks)
    decision, reason = rule_engine.aggregate_decision(results)
    return PackResult(
        pack_filename=name, extracted=doc, arithmetic_checks=checks, rule_results=results,
        decision=decision, reason=reason, assumptions_applied=rule_engine.collect_assumptions(results),
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "purchase-request.csv").write_bytes(PR_CSV.encode("utf-8"))
    for n in (1, 2, 3, 10):
        (folder / f"supplier-pack-{n:02d}.pdf").write_bytes(b"%PDF-not-read-because-pipeline-is-mocked")
    (folder / "notes.pdf").write_bytes(b"ignored")
    return folder


# ---------------------------------------------------------------------------------------------
# Purchase request loader
# ---------------------------------------------------------------------------------------------


def test_pr_loader_handles_bom_and_gives_decimals(data_dir):
    pr = main.load_purchase_request(data_dir / "purchase-request.csv")
    assert pr.pr_number == "PR-TEST"
    assert set(pr.items) == {"A", "B"}
    item = pr.items["A"]
    assert item.quantity_requested == Decimal("12000") and isinstance(item.quantity_requested, Decimal)
    assert item.max_unit_price_bdt == Decimal("480.00") and isinstance(item.max_unit_price_bdt, Decimal)
    assert item.description == "Cotton twill fabric, 280 gsm"  # comma inside quotes survives


def test_pr_loader_rejects_bad_number(tmp_path):
    bad = tmp_path / "pr.csv"
    bad.write_text("PR number,Item,Description,Unit,Quantity requested,Maximum unit price (BDT)\n"
                   "PR-1,A,x,metre,twelve,480.00\n", encoding="utf-8")
    with pytest.raises(main.InputError, match="not a number"):
        main.load_purchase_request(bad)


def test_pr_loader_rejects_missing_column_and_missing_file(tmp_path):
    bad = tmp_path / "pr.csv"
    bad.write_text("PR number,Item\nPR-1,A\n", encoding="utf-8")
    with pytest.raises(main.InputError, match="missing column"):
        main.load_purchase_request(bad)
    with pytest.raises(main.InputError, match="cannot read"):
        main.load_purchase_request(tmp_path / "nope.csv")


HEADER = "PR number,Item,Description,Unit,Quantity requested,Maximum unit price (BDT)\n"


@pytest.mark.parametrize(
    "row, expected",
    [
        ("PR-1,A,x,metre,NaN,480.00", "finite"),
        ("PR-1,A,x,metre,12000,Infinity", "finite"),
        ("PR-1,A,x,metre,-Infinity,480.00", "finite"),
        ("PR-1,A,x,metre,sNaN,480.00", "finite"),
        ("PR-1,,x,metre,12000,480.00", "Item is empty"),
        ("PR-1,   ,x,metre,12000,480.00", "Item is empty"),
        (",A,x,metre,12000,480.00", "PR number is empty"),
        ("PR-1,A,,metre,12000,480.00", "Description is empty"),
        ("PR-1,A,x,,12000,480.00", "Unit is empty"),
        ("PR-1,A,x,metre,,480.00", "Quantity requested is empty"),
        ("PR-1,A,x,metre,12000", "Maximum unit price (BDT) is empty or missing"),
        ("PR-1", "Item is empty or missing"),
    ],
)
def test_pr_loader_turns_every_bad_value_into_input_error(tmp_path, row, expected):
    bad = tmp_path / "pr.csv"
    bad.write_text(HEADER + row + "\n", encoding="utf-8")
    with pytest.raises(main.InputError, match=re.escape(expected)):
        main.load_purchase_request(bad)


@pytest.mark.parametrize("surplus", [",oops", ",,", ",12000"])
def test_pr_loader_and_cli_reject_surplus_csv_fields(data_dir, tmp_path, monkeypatch, capsys, surplus):
    """A row with more fields than the header (e.g. an unquoted comma in a description) must not load silently."""
    (data_dir / "purchase-request.csv").write_text(
        HEADER + "PR-1,A,x,metre,1,1\n" + f"PR-1,B,y,metre,2,2{surplus}\n", encoding="utf-8")
    with pytest.raises(main.InputError, match=r"row 3: .*extra value"):
        main.load_purchase_request(data_dir / "purchase-request.csv")
    monkeypatch.setattr(main, "run_pipeline", lambda *a, **k: pytest.fail("pipeline must not run"))
    assert main.main(["--data-dir", str(data_dir), "--output", str(tmp_path / "x.txt")]) == 2
    assert "row 3" in capsys.readouterr().err


def test_pr_loader_rejects_duplicate_item_and_mixed_pr_numbers(tmp_path):
    dup = tmp_path / "dup.csv"
    dup.write_text(HEADER + "PR-1,A,x,metre,1,1\nPR-1,A,y,metre,2,2\n", encoding="utf-8")
    with pytest.raises(main.InputError, match="appears twice"):
        main.load_purchase_request(dup)
    mixed = tmp_path / "mixed.csv"
    mixed.write_text(HEADER + "PR-1,A,x,metre,1,1\nPR-2,B,y,metre,2,2\n", encoding="utf-8")
    with pytest.raises(main.InputError, match="several PR numbers"):
        main.load_purchase_request(mixed)


def test_pr_loader_rejects_non_utf8_file(tmp_path):
    bad = tmp_path / "pr.csv"
    bad.write_bytes(HEADER.encode() + b"PR-1,A,caf\xe9,metre,1,1\n")
    with pytest.raises(main.InputError, match="UTF-8"):
        main.load_purchase_request(bad)


def test_cli_exits_2_with_message_on_nan_purchase_request(data_dir, tmp_path, monkeypatch, capsys):
    (data_dir / "purchase-request.csv").write_text(HEADER + "PR-1,A,x,metre,NaN,480.00\n", encoding="utf-8")
    monkeypatch.setattr(main, "run_pipeline", lambda *a, **k: pytest.fail("pipeline must not run"))
    assert main.main(["--data-dir", str(data_dir), "--output", str(tmp_path / "x.txt")]) == 2
    err = capsys.readouterr().err
    assert "error:" in err and "row 2" in err and "Quantity requested" in err
    assert "Traceback" not in err


# ---------------------------------------------------------------------------------------------
# Pack discovery
# ---------------------------------------------------------------------------------------------


def test_find_packs_sorted_numerically_and_ignores_other_pdfs(data_dir):
    names = [p.name for p in main.find_packs(data_dir)]
    assert names == ["supplier-pack-01.pdf", "supplier-pack-02.pdf", "supplier-pack-03.pdf", "supplier-pack-10.pdf"]


def test_numeric_sort_puts_9_before_10(tmp_path):
    for n in (10, 9, 2):
        (tmp_path / f"supplier-pack-{n}.pdf").write_bytes(b"x")
    assert [p.name for p in main.find_packs(tmp_path)] == [
        "supplier-pack-2.pdf", "supplier-pack-9.pdf", "supplier-pack-10.pdf"]


def test_find_single_pack_and_unknown_pack(data_dir):
    assert [p.name for p in main.find_packs(data_dir, "supplier-pack-02.pdf")] == ["supplier-pack-02.pdf"]
    with pytest.raises(main.InputError, match="pack not found"):
        main.find_packs(data_dir, "supplier-pack-99.pdf")


@pytest.mark.parametrize("name", ["notes.pdf", "supplier-pack-.pdf", "supplier-pack-1.txt", "my-supplier-pack-01.pdf",
                                  "supplier-pack-01.pdf.bak", "../notes.pdf"])
def test_pack_option_rejects_names_that_are_not_supplier_packs(data_dir, name):
    with pytest.raises(main.InputError, match="supplier-pack-NN.pdf"):
        main.find_packs(data_dir, name)


def test_pack_option_rejects_an_existing_non_pack_pdf_even_by_full_path(data_dir):
    assert (data_dir / "notes.pdf").is_file()  # it exists, but is not a supplier pack
    with pytest.raises(main.InputError, match="supplier-pack-NN.pdf"):
        main.find_packs(data_dir, str(data_dir / "notes.pdf"))


def test_cli_rejects_non_pack_name_with_exit_2(data_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main, "run_pipeline", lambda *a, **k: pytest.fail("pipeline must not run"))
    assert main.main(["--data-dir", str(data_dir), "--pack", "notes.pdf", "--output", str(tmp_path / "x.txt")]) == 2
    assert "supplier-pack-NN.pdf" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------
# Unexpected exceptions never drop a pack
# ---------------------------------------------------------------------------------------------


def fake_pipeline(boom_for: str | None = None):
    def run(pdf_path, pr, client=None):
        name = Path(pdf_path).name
        if name == boom_for:
            raise RuntimeError("boom")
        return ok_result(name)

    return run


def test_unexpected_exception_becomes_needs_review_and_others_continue(data_dir, monkeypatch):
    monkeypatch.setattr(main, "run_pipeline", fake_pipeline("supplier-pack-02.pdf"))
    results = main.process_all(main.find_packs(data_dir), PR)

    assert [r.pack_filename for r in results] == [
        "supplier-pack-01.pdf", "supplier-pack-02.pdf", "supplier-pack-03.pdf", "supplier-pack-10.pdf"]
    failed = results[1]
    assert failed.decision == Decision.NEEDS_REVIEW
    assert failed.processing_status == "ERROR: unexpected_exception"
    assert "RuntimeError" in failed.processing_error_detail and "boom" in failed.processing_error_detail
    assert failed.rule_results == [] and failed.extracted is None and failed.arithmetic_checks == []
    for other in (results[0], results[2], results[3]):
        assert other.processing_status == "OK" and other.rule_results


def test_traceback_is_logged_not_put_in_the_result(data_dir, monkeypatch, caplog):
    monkeypatch.setattr(main, "run_pipeline", fake_pipeline("supplier-pack-01.pdf"))
    with caplog.at_level("ERROR", logger="supplier_checker"):
        result = main.process_pack(data_dir / "supplier-pack-01.pdf", PR)
    assert "Traceback" in caplog.text and "boom" in caplog.text
    assert "Traceback" not in result.reason and "Traceback" not in result.processing_error_detail


# ---------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------


def mixed_results() -> list[PackResult]:
    approve = ok_result("supplier-pack-01.pdf")
    reject = ok_result("supplier-pack-02.pdf", make_doc([line(price="600.00")]))  # price far over the PR maximum
    review = ok_result("supplier-pack-03.pdf", make_doc(delivery_days=absent("int", "delivery_days")))
    failed = build_error_result("supplier-pack-04.pdf", "unexpected_exception", "RuntimeError: boom")
    return [approve, reject, review, failed]


def test_report_has_a_section_for_every_pack_including_the_failed_one():
    results = mixed_results()
    report = reporter.generate_report(results)
    for result in results:
        assert f"Supplier Pack: {result.pack_filename}" in report
    assert report.count("FINAL PROCUREMENT DECISION:") == len(results)


def test_summary_counts_and_processing_error_listing():
    results = mixed_results()
    counts = {d: sum(1 for r in results if r.decision == d) for d in Decision}
    summary = reporter.format_summary(results)
    assert f"APPROVE      : {counts[Decision.APPROVE]}" in summary
    assert f"REJECT       : {counts[Decision.REJECT]}" in summary
    assert f"NEEDS_REVIEW : {counts[Decision.NEEDS_REVIEW]}" in summary
    assert "Total        : 4" in summary
    assert "Processing errors (included in NEEDS_REVIEW above): 1" in summary
    assert "supplier-pack-04.pdf: ERROR: unexpected_exception" in summary


def test_error_pack_shows_error_info_and_no_evidence():
    report = reporter.format_pack(build_error_result("supplier-pack-04.pdf", "pdf_unreadable", "PDFReadError: bad"))
    assert "Processing Status: ERROR: pdf_unreadable" in report
    assert "Processing error detail: PDFReadError: bad" in report
    assert "No evidence is fabricated" in report
    assert "EXTRACTED FIELDS" not in report and "RULE CHECK RESULTS" not in report
    assert "FINAL PROCUREMENT DECISION: NEEDS_REVIEW" in report


def test_ok_pack_shows_evidence_arithmetic_and_every_rule():
    result = ok_result("supplier-pack-01.pdf")
    report = reporter.format_pack(result)
    assert "EXTRACTED FIELDS" in report and "ARITHMETIC CHECKS" in report and "RULE CHECK RESULTS" in report
    assert '"Supplier TIN: 101458013391"' in report or "101458013391" in report
    assert "calculated" in report and "printed" in report
    for rule in result.rule_results:
        assert rule.rule_id in report
    assert f"FINAL PROCUREMENT DECISION: {result.decision.value}" in report


def test_absence_evidence_is_shown_under_the_rule_that_rests_on_it():
    doc = make_doc(tin=absent("str", "tin"))
    result = ok_result("supplier-pack-05.pdf", doc)
    r04 = next(r for r in result.rule_results if r.rule_id == "R-04")
    assert r04.absence_evidence, "precondition: R-04 carries absence evidence"
    report = reporter.format_pack(result)
    assert "absence:" in report and "Not found" in report


def test_formatting_failure_falls_back_to_minimal_block_and_keeps_the_pack(monkeypatch):
    results = mixed_results()
    original = reporter.format_pack

    def flaky(result):
        if result.pack_filename == "supplier-pack-02.pdf":
            raise ValueError("cannot format this one")
        return original(result)

    monkeypatch.setattr(reporter, "format_pack", flaky)
    report = reporter.generate_report(results)
    assert "Supplier Pack: supplier-pack-02.pdf" in report
    assert "detailed formatting of this pack failed (ValueError)" in report
    assert f"FINAL PROCUREMENT DECISION: {results[1].decision.value}" in report
    assert report.count("FINAL PROCUREMENT DECISION:") == len(results)  # nothing dropped
    assert "Supplier Pack: supplier-pack-01.pdf" in report  # the others are formatted normally
    assert "SUMMARY" in report


# ---------------------------------------------------------------------------------------------
# main() end to end (pipeline mocked)
# ---------------------------------------------------------------------------------------------


def test_main_writes_report_and_prints_summary(data_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main, "run_pipeline", fake_pipeline("supplier-pack-03.pdf"))
    out = tmp_path / "nested" / "report.txt"
    code = main.main(["--data-dir", str(data_dir), "--output", str(out)])
    assert code == 0
    text = out.read_text(encoding="utf-8")
    for n in ("01", "02", "03", "10"):
        assert f"Supplier Pack: supplier-pack-{n}.pdf" in text
    assert "ERROR: unexpected_exception" in text
    shown = capsys.readouterr().out
    assert "SUMMARY — All 4 Supplier Packs" in shown and "Total        : 4" in shown
    assert str(out) in shown


def test_main_single_pack(data_dir, tmp_path, monkeypatch):
    seen = []

    def run(pdf_path, pr, client=None):
        seen.append(Path(pdf_path).name)
        return ok_result(Path(pdf_path).name)

    monkeypatch.setattr(main, "run_pipeline", run)
    out = tmp_path / "one.txt"
    assert main.main(["--data-dir", str(data_dir), "--pack", "supplier-pack-02.pdf", "--output", str(out)]) == 0
    assert seen == ["supplier-pack-02.pdf"]
    text = out.read_text(encoding="utf-8")
    assert "Supplier Pack: supplier-pack-02.pdf" in text and "supplier-pack-01.pdf" not in text
    assert "Total        : 1" in text


def test_main_input_errors_exit_2_without_running_anything(data_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main, "run_pipeline", lambda *a, **k: pytest.fail("pipeline must not run"))
    assert main.main(["--data-dir", str(data_dir), "--pack", "supplier-pack-99.pdf",
                      "--output", str(tmp_path / "x.txt")]) == 2
    assert main.main(["--data-dir", str(tmp_path / "empty")]) == 2
    assert "error:" in capsys.readouterr().err
    assert not (tmp_path / "x.txt").exists()
