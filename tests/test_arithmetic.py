"""Tests for src/arithmetic.py (Milestone 1, SPEC §9). All inputs are typed models (tests/builders.py)."""

from decimal import Decimal

import pytest

from src.arithmetic import ARITHMETIC_TOLERANCE_BDT, run_arithmetic_checks
from tests.builders import D, UNVERIFIED, absent, fld, line, make_doc, unknown


def by_name(results, name, item_id=None):
    matches = [r for r in results if r.check_name == name and r.item_id == item_id]
    assert len(matches) == 1, f"expected one {name} {item_id}, got {len(matches)}"
    return matches[0]


def statuses(results):
    return {(r.check_name, r.item_id): r.status for r in results}


def labels(result):
    return [ref.label for ref in result.evidence_refs]


# ---------- happy path ----------


def test_tolerance_constant():
    assert ARITHMETIC_TOLERANCE_BDT == Decimal("0.01")


def test_correct_arithmetic_all_pass():
    results = run_arithmetic_checks(make_doc())
    assert statuses(results) == {
        ("line_recalc", "line-1"): "PASS", ("independent_subtotal", None): "PASS",
        ("vat", None): "PASS", ("grand", None): "PASS",
    }


def test_pass_and_fail_results_carry_decimals_and_evidence():
    doc = make_doc(grand_total_bdt=fld("dec", D("1"), "Total 1"))
    for r in run_arithmetic_checks(doc):
        if r.status in ("PASS", "FAIL"):
            assert isinstance(r.calculated, Decimal) and isinstance(r.printed, Decimal)
            assert r.evidence_refs, f"{r.check_name} has no evidence_refs"


# ---------- FAIL cases ----------


def test_line_total_mismatch_fails_with_item_id_and_refs():
    doc = make_doc([line(total="5664001.00")])
    r = by_name(run_arithmetic_checks(doc), "line_recalc", "line-1")
    assert r.status == "FAIL" and r.item_id == "line-1"
    assert r.calculated == D("5664000.00") and r.printed == D("5664001.00")
    assert set(labels(r)) == {"line-1 quantity", "line-1 unit_price_bdt", "line-1 printed line total"}


def test_subtotal_mismatch_fails():
    doc = make_doc(subtotal_bdt=fld("dec", D("5664100.00"), "Subtotal 5,664,100.00"))
    assert by_name(run_arithmetic_checks(doc), "independent_subtotal").status == "FAIL"


def test_vat_amount_mismatch_fails():
    doc = make_doc(vat_amount_bdt=fld("dec", D("849000.00"), "VAT 15% 849,000.00"))
    assert by_name(run_arithmetic_checks(doc), "vat").status == "FAIL"


def test_grand_total_mismatch_fails():
    doc = make_doc(grand_total_bdt=fld("dec", D("6513000.00"), "Total 6,513,000.00"))
    r = run_arithmetic_checks(doc)
    assert by_name(r, "grand").status == "FAIL" and by_name(r, "vat").status == "PASS"


def test_subtotal_uses_recalculated_amounts_not_printed_line_totals_ac15():
    # Printed line totals 1100 + 500 = 1600 = printed subtotal, which would hide the error if summed.
    # Recalculated: 10 x 100 + 5 x 100 = 1500, so the independent subtotal must FAIL against 1600.
    lines = [line(qty="10", price="100", total="1100.00"), line(qty="5", price="100", total="500.00")]
    doc = make_doc(lines, subtotal_bdt=fld("dec", D("1600.00"), "Subtotal 1,600.00"))
    r = run_arithmetic_checks(doc)
    sub = by_name(r, "independent_subtotal")
    assert sub.status == "FAIL" and sub.calculated == D("1500.00") and sub.printed == D("1600.00")
    assert by_name(r, "line_recalc", "line-1").status == "FAIL"
    assert by_name(r, "line_recalc", "line-2").status == "PASS"


# ---------- VAT uses the PRINTED rate ----------


def test_printed_rate_10_with_exact_10_percent_vat_passes_vat_and_grand():
    doc = make_doc(
        vat_rate_pct=fld("dec", D("10"), "VAT 10%"),
        vat_amount_bdt=fld("dec", D("566400.00"), "VAT 10% 566,400.00"),
        grand_total_bdt=fld("dec", D("6230400.00"), "Total 6,230,400.00"),
    )
    r = run_arithmetic_checks(doc)
    assert by_name(r, "vat").status == "PASS" and by_name(r, "grand").status == "PASS"


def test_printed_rate_15_but_amount_computed_at_10_fails_vat():
    doc = make_doc(vat_amount_bdt=fld("dec", D("566400.00"), "VAT 15% 566,400.00"))
    assert by_name(run_arithmetic_checks(doc), "vat").status == "FAIL"


# ---------- UNKNOWN handling ----------


def test_unknown_printed_rate_makes_vat_and_grand_unknown():
    r = run_arithmetic_checks(make_doc(vat_rate_pct=unknown("dec")))
    assert by_name(r, "vat").status == "UNKNOWN" and by_name(r, "grand").status == "UNKNOWN"
    assert by_name(r, "independent_subtotal").status == "PASS"


def test_unknown_vat_basis_is_unknown_not_fail():
    r = run_arithmetic_checks(make_doc(vat_price_basis=unknown("str")))
    assert by_name(r, "vat").status == "UNKNOWN" and by_name(r, "grand").status == "UNKNOWN"


@pytest.mark.parametrize("basis", ["select items", "unclear", "line items"])
def test_only_canonical_subtotal_basis_permits_the_vat_check(basis):
    r = run_arithmetic_checks(make_doc(vat_price_basis=fld("str", basis, f"VAT applies to {basis}")))
    vat = by_name(r, "vat")
    assert vat.status == "UNKNOWN" and basis in vat.detail


def test_canonical_basis_is_case_insensitive_text_match():
    r = run_arithmetic_checks(make_doc(vat_price_basis=fld("str", " Subtotal ", "VAT on Subtotal")))
    assert by_name(r, "vat").status == "PASS"


@pytest.mark.parametrize("field,check", [
    ("subtotal_bdt", "independent_subtotal"), ("vat_amount_bdt", "vat"), ("grand_total_bdt", "grand"),
])
def test_unverified_printed_value_is_unknown_not_fail(field, check):
    doc = make_doc(**{field: fld("dec", D("1.00"), "wrong but unverified", status=UNVERIFIED)})
    assert by_name(run_arithmetic_checks(doc), check).status == "UNKNOWN"


def test_unknown_printed_subtotal_does_not_block_vat_and_grand():
    r = run_arithmetic_checks(make_doc(subtotal_bdt=unknown("dec")))
    assert by_name(r, "independent_subtotal").status == "UNKNOWN"
    assert by_name(r, "vat").status == "PASS" and by_name(r, "grand").status == "PASS"


def test_absent_printed_value_is_unknown():
    r = run_arithmetic_checks(make_doc(vat_amount_bdt=absent("dec", "vat_amount_bdt")))
    assert by_name(r, "vat").status == "UNKNOWN"


@pytest.mark.parametrize("bad", [unknown("dec", line=True), fld("dec", D("1"), "unverified", status=UNVERIFIED, line=True)])
@pytest.mark.parametrize("which", ["qty", "price"])
def test_one_unusable_input_makes_subtotal_unknown_not_fail_even_if_partial_sum_differs(bad, which):
    good = line(qty="10", price="100", total="1000.00")
    broken = line(**{which: bad}, total="500.00", **({"price": "100"} if which == "qty" else {"qty": "5"}))
    # Printed subtotal 1500 differs from the partial sum 1000: a partial comparison would wrongly FAIL.
    doc = make_doc([good, broken], subtotal_bdt=fld("dec", D("1500.00"), "Subtotal 1,500.00"))
    r = run_arithmetic_checks(doc)
    sub = by_name(r, "independent_subtotal")
    assert sub.status == "UNKNOWN" and "line-2" in sub.detail
    assert by_name(r, "vat").status == "UNKNOWN" and by_name(r, "grand").status == "UNKNOWN"
    assert by_name(r, "line_recalc", "line-1").status == "PASS"  # other lines still checked
    assert by_name(r, "line_recalc", "line-2").status == "UNKNOWN"


def test_empty_line_item_list_makes_subtotal_vat_grand_unknown_never_zero():
    r = run_arithmetic_checks(make_doc([], subtotal_bdt=fld("dec", D("0.00"), "Subtotal 0.00")))
    assert statuses(r) == {("independent_subtotal", None): "UNKNOWN", ("vat", None): "UNKNOWN", ("grand", None): "UNKNOWN"}
    assert by_name(r, "independent_subtotal").calculated is None


def test_line_with_missing_printed_total_is_unknown_with_item_id_and_field_named():
    doc = make_doc([line(), line(total=unknown("dec", line=True))])
    r = by_name(run_arithmetic_checks(doc), "line_recalc", "line-2")
    assert r.status == "UNKNOWN" and r.item_id == "line-2" and "line_total_bdt" in r.detail


def test_line_with_missing_quantity_names_the_field_and_is_not_omitted():
    doc = make_doc([line(qty=unknown("dec", line=True), total="5664000.00")])
    r = by_name(run_arithmetic_checks(doc), "line_recalc", "line-1")
    assert r.status == "UNKNOWN" and "quantity" in r.detail


def test_unverified_input_is_treated_as_unknown_never_fail():
    doc = make_doc([line(qty=fld("dec", D("1"), "unverified qty", status=UNVERIFIED, line=True), total="5664000.00")])
    assert by_name(run_arithmetic_checks(doc), "line_recalc", "line-1").status == "UNKNOWN"


def test_every_line_gets_a_result_one_per_line():
    r = run_arithmetic_checks(make_doc([line(), line(), line()]))
    assert [x.item_id for x in r if x.check_name == "line_recalc"] == ["line-1", "line-2", "line-3"]


def test_extreme_value_does_not_crash_it_is_unknown():
    doc = make_doc([line(qty="1E+999", price="10", total="1.00")])
    r = run_arithmetic_checks(doc)
    assert by_name(r, "line_recalc", "line-1").status == "UNKNOWN"
    assert by_name(r, "independent_subtotal").status == "UNKNOWN"


# ---------- tolerance and rounding policy (AMB-09, review decision 3) ----------


@pytest.mark.parametrize("printed,expected", [
    ("5664000.01", "PASS"), ("5663999.99", "PASS"), ("5664000.02", "FAIL"), ("5663999.98", "FAIL"),
    ("5664000.00", "PASS"),
])
def test_tolerance_boundary_is_inclusive(printed, expected):
    doc = make_doc([line(total=printed)])
    assert by_name(run_arithmetic_checks(doc), "line_recalc", "line-1").status == expected


def test_half_cent_line_amount_rounds_half_up_not_bankers():
    # 1 x 1.005 = 1.005 -> ROUND_HALF_UP = 1.01 (round-half-even would give 1.00).
    r = by_name(run_arithmetic_checks(make_doc([line(qty="1", price="1.005", total="1.01")])), "line_recalc", "line-1")
    assert r.calculated == D("1.01") and r.status == "PASS"


def test_rounding_is_enforced_not_just_tolerance():
    # If 1.005 were left unrounded, a printed 1.02 would be 0.015 off (FAIL). Rounded to 1.01 it is 0.01 off (PASS).
    assert by_name(run_arithmetic_checks(make_doc([line(qty="1", price="1.005", total="1.02")])), "line_recalc", "line-1").status == "PASS"
    assert by_name(run_arithmetic_checks(make_doc([line(qty="1", price="1.005", total="1.03")])), "line_recalc", "line-1").status == "FAIL"


def test_vat_is_rounded_half_up_and_grand_uses_rounded_vat():
    # subtotal 10.10 x 5% = 0.505 -> 0.51 (half-even would give 0.50); grand = 10.10 + 0.51 = 10.61.
    doc = make_doc(
        [line(qty="1", price="10.10", total="10.10")],
        vat_rate_pct=fld("dec", D("5"), "VAT 5%"), vat_amount_bdt=fld("dec", D("0.51"), "VAT 5% 0.51"),
        grand_total_bdt=fld("dec", D("10.61"), "Total 10.61"),
    )
    r = run_arithmetic_checks(doc)
    assert by_name(r, "vat").calculated == D("0.51") and by_name(r, "vat").status == "PASS"
    assert by_name(r, "grand").calculated == D("10.61") and by_name(r, "grand").status == "PASS"


def test_subtotal_sums_rounded_line_amounts():
    # two lines of 1 x 1.005: each rounds to 1.01, so the independent subtotal is 2.02 (not 2.01 from an unrounded 2.010).
    lines = [line(qty="1", price="1.005", total="1.01"), line(qty="1", price="1.005", total="1.01")]
    r = run_arithmetic_checks(make_doc(lines, subtotal_bdt=fld("dec", D("2.02"), "Subtotal 2.02")))
    assert by_name(r, "independent_subtotal").calculated == D("2.02")


def test_printed_values_are_never_modified():
    doc = make_doc(grand_total_bdt=fld("dec", D("6513600.50"), "Total 6,513,600.50"))
    before = doc.model_dump()
    results = run_arithmetic_checks(doc)
    assert doc.model_dump() == before
    assert by_name(results, "grand").printed == D("6513600.50")


# ---------- evidence propagation (NR-06 audit trail) ----------


def test_subtotal_evidence_keeps_every_line_input_and_the_printed_subtotal():
    r = by_name(run_arithmetic_checks(make_doc([line(), line(code="B", qty="100", price="10.00", unit="piece")])),
                "independent_subtotal")
    assert set(labels(r)) == {"line-1 quantity", "line-1 unit_price_bdt", "line-2 quantity", "line-2 unit_price_bdt",
                              "printed subtotal"}


def test_vat_evidence_keeps_subtotal_inputs_rate_basis_and_printed_vat():
    r = by_name(run_arithmetic_checks(make_doc()), "vat")
    assert set(labels(r)) == {"line-1 quantity", "line-1 unit_price_bdt", "printed VAT rate", "VAT price basis",
                              "printed VAT amount"}


def test_grand_evidence_keeps_subtotal_inputs_rate_basis_and_printed_grand():
    r = by_name(run_arithmetic_checks(make_doc()), "grand")
    assert set(labels(r)) == {"line-1 quantity", "line-1 unit_price_bdt", "printed VAT rate", "VAT price basis",
                              "printed grand total"}


def test_evidence_quotes_come_from_the_verified_evidence():
    r = by_name(run_arithmetic_checks(make_doc()), "vat")
    rate_ref = next(x for x in r.evidence_refs if x.label == "printed VAT rate")
    assert rate_ref.source_quote == "VAT 15%" and rate_ref.page_number == 1
