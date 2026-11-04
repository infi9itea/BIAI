"""The verifier against the REAL supplier PDFs, using hand-written STAND-IN model outputs.

What this is: a check that the verifier's quote matching, field parsing, conflict scan and absence search behave on
the real text layouts (wrapped lines, joined table headers, a repeated advance clause, a missing clause, missing IDs).
The stand-ins below are written by the developer to look like what a model returns. They are test inputs only:

* they are NOT the committed offline fixtures (``fixtures/mock_llm_responses/`` must come from live extraction);
* they are never used to make or check decisions, and no rule is evaluated here (Checkpoint G: ground truth comes
  from reading the PDFs, not from any pipeline or extraction output).
"""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from src.extractor import normalize_extraction
from src.models import FieldState, RawExtraction, is_usable
from src.pdf_reader import extract_text
from src.verifier import verify_evidence
from tests.m3_helpers import NULL, f, make_pr

DATA = Path(__file__).resolve().parent.parent / "assessment-data"
PR = make_pr()
pytestmark = pytest.mark.skipif(not (DATA / "supplier-pack-01.pdf").exists(), reason="assessment-data not present")

_BLANK_LINE = {k: NULL for k in ("item_code", "description", "quantity", "unit", "unit_price_bdt", "line_total_bdt", "vat_included_in_price")}


def row_fields(row: str, vat_included=NULL) -> dict:
    code, *rest = row.split(" ")
    qty, unit, price, amount = rest[-4:]
    return {
        "item_code": f(code, row), "description": f(" ".join(rest[:-4]), row), "quantity": f(qty, row),
        "unit": f(unit, row), "unit_price_bdt": f(price, row), "line_total_bdt": f(amount, row),
        "vat_included_in_price": vat_included,
    }


def standin(**fields) -> dict:
    base = {name: deepcopy(NULL) for name in RawExtraction.model_fields if name != "line_items"}
    base["line_items"] = []
    base.update(fields)
    return base


def run(pack: str, raw: dict):
    pdf = extract_text(DATA / f"supplier-pack-{pack}.pdf")
    return verify_evidence(normalize_extraction(RawExtraction.model_validate(raw), PR), pdf), pdf


def common(row: str, subtotal: str, vat: str, total: str, name: str, tin: str, bin_: str) -> dict:
    return standin(
        supplier_name=f(name, name),
        tin=f(tin, f"Supplier TIN: {tin}"), bin=f(bin_, f"BIN: {bin_}"),
        line_items=[row_fields(row)],
        subtotal_bdt=f(subtotal, f"Subtotal {subtotal}"),
        vat_rate_pct=f("15", f"VAT 15% {vat}"), vat_amount_bdt=f(vat, f"VAT 15% {vat}"),
        grand_total_bdt=f(total, f"Total {total}"),
        vat_shown_separately=f("yes", f"VAT 15% {vat}"),
        vat_price_basis=f("subtotal", f"Subtotal {subtotal} VAT 15% {vat}"),
    )


def pack01_raw() -> dict:
    raw = common("A Cotton twill fabric, 280 gsm 12,000 metre 472.00 5,664,000.00", "5,664,000.00", "849,600.00",
                 "6,513,600.00", "Padma Textile Mills Ltd", "101458013391", "000170141-0101")
    pay = "Balance payable within 45 days after delivery and acceptance of goods."  # wraps across two PDF lines
    pen = "Late delivery penalty: 1% of order value for each full week of delay."
    raw.update(
        payment_terms_wording=f(pay, pay), payment_period=f("45 days", pay),
        advance_payment_pct=f("10", "10% advance payable with the purchase order."),
        delivery_period=f("24 days", "Delivery within 24 days of receiving the purchase order."),
        penalty_wording=f(pen, pen), penalty_rate_pct=f("1", pen), penalty_calculation_basis=f("order value", pen),
        penalty_period=f("each full week of delay", pen), penalty_full_weeks_only=f("yes", pen),
        validity_period=f("45 days", "This quotation is valid for 45 days from the date above."),
    )
    return raw


def test_pack_01_every_field_verifies_across_a_wrapped_line():
    doc, _ = run("01", pack01_raw())
    for name in ("supplier_name", "tin", "bin", "subtotal_bdt", "vat_rate_pct", "vat_amount_bdt", "grand_total_bdt",
                 "vat_shown_separately", "vat_price_basis", "delivery_days", "advance_payment_pct", "validity_days"):
        assert is_usable(getattr(doc, name)), name
    line = doc.line_items[0]
    assert all(is_usable(getattr(line, n)) for n in ("item_code", "description", "quantity", "unit", "unit_price_bdt", "line_total_bdt"))
    assert (line.item_code_match, line.description_match, line.unit_match) == ("YES", "YES", "YES")
    assert doc.payment.anchor == "other"  # "after delivery and acceptance" names two events
    assert (doc.payment.min_days, doc.payment.max_days) == (45, 45) and doc.payment.discretionary is False
    assert doc.penalty.rate_pct == Decimal("1") and doc.penalty.period_unit == "week"
    assert doc.penalty.calculation_basis == "order value" and doc.penalty.full_weeks_only is True
    assert doc.delivery_days.value == 24 and doc.validity_days.value == 45


def test_pack_01_good_extraction_is_stable_when_the_page_number_is_wrong():
    raw = pack01_raw()
    raw["subtotal_bdt"]["page_number"] = 3
    doc, _ = run("01", raw)
    assert doc.subtotal_bdt.evidence.page_number == 1 and is_usable(doc.subtotal_bdt)


def test_pack_05_advance_clause_stated_twice_is_not_a_conflict():
    raw = pack01_raw()
    adv = "40% advance payable with the purchase order."
    pay = "40% advance with the purchase order, balance 60% within 30 days of delivery."
    raw.update(advance_payment_pct=f("40", adv), payment_terms_wording=f(pay, pay), payment_period=f("30 days", pay))
    doc, pdf = run("05", {**raw, "supplier_name": f("Meghna Carton Works", "Meghna Carton Works")})
    assert pdf["full_text"].count("40% advance") == 2
    assert is_usable(doc.advance_payment_pct) and doc.advance_payment_pct.value == Decimal("40")
    assert doc.payment.anchor == "delivery"  # the purchase order belongs to the advance clause, not the balance
    assert (doc.payment.min_days, doc.payment.max_days) == (30, 30)


def test_pack_05_whole_payment_paragraph_as_the_quote_still_gives_the_delivery_anchor():
    para = ("40% advance payable with the purchase order. 40% advance with the purchase order, balance 60% "
            "within 30 days of delivery.")
    raw = standin(payment_terms_wording=f(para, para), payment_period=f("30 days", para))
    doc, _ = run("05", raw)
    assert is_usable(doc.payment) and doc.payment.anchor == "delivery"


def test_pack_06_missing_penalty_clause_is_absent_with_documented_search():
    raw = common("A Cotton twill fabric, 280 gsm 12,000 metre 465.00 5,580,000.00", "5,580,000.00", "837,000.00",
                 "6,417,000.00", "Teesta Fabrics Ltd", "106458063391", "000670641-0101")
    doc, pdf = run("06", raw)
    pen = doc.penalty
    assert pen.state == FieldState.ABSENT and pen.evidence is None
    absence = pen.absence_evidence
    assert absence.extraction_complete and absence.pages_scanned == [1] and absence.search_terms
    assert absence.contextual_excerpt in pdf["full_text"]
    assert all(term.lower() not in pdf["full_text"].lower() for term in ("penalty", "delay", "late delivery"))


def test_pack_08_missing_tin_and_bin_are_absent():
    raw = common("C Polyester sewing thread, cone 800 cone 205.00 164,000.00", "164,000.00", "24,600.00", "188,600.00",
                 "Bhairab Thread and Co", "", "")
    raw["tin"], raw["bin"] = deepcopy(NULL), deepcopy(NULL)
    pen = "Late delivery penalty of 0.5% per week of delay."
    raw.update(penalty_wording=f(pen, pen), penalty_rate_pct=f("0.5", pen), penalty_calculation_basis=f("order value", pen),
               penalty_period=f("per week", pen))
    doc, pdf = run("08", raw)
    for name, field in (("tin", doc.tin), ("bin", doc.bin)):
        assert field.state == FieldState.ABSENT and field.absence_evidence.field_searched == name
        assert field.absence_evidence.contextual_excerpt == "Supplier registration details available on request."
    # the model claimed "order value", but this clause never says what the 0.5% applies to
    assert is_usable(doc.penalty) and doc.penalty.calculation_basis is None
    assert doc.penalty.rate_pct == Decimal("0.5") and doc.penalty.period_unit == "week"


def test_pack_09_discretionary_range_and_weeks():
    raw = common("D Poly bag, 30 x 40 cm 50,000 piece 6.40 322,000.00", "322,000.00", "48,300.00", "370,300.00",
                 "Dhaleshwari Poly Pack", "109458093391", "000970941-0101")
    pay = "Payment within 30 to 90 days as agreed by both parties."  # wraps across two PDF lines
    raw.update(payment_terms_wording=f(pay, pay), payment_period=f("30 to 90 days", pay),
               advance_payment_pct=f("5", "5% advance payable with the purchase order."),
               delivery_period=f("4 weeks", "Delivery within 4 weeks of PO."))
    doc, _ = run("09", raw)
    assert (doc.payment.min_days, doc.payment.max_days) == (30, 90)
    assert doc.payment.discretionary is True and doc.payment.anchor is None
    assert doc.delivery_days.value == 28 and is_usable(doc.delivery_days)
    line = doc.line_items[0]
    assert is_usable(line.description) and line.description.value == "Poly bag, 30 x 40 cm"
    assert is_usable(line.quantity) and line.quantity.value == Decimal("50000")


def pack10_raw(vat_quote: str) -> dict:
    row = "E Carton box, 5-ply 3,000 piece 94.00 282,000.00"
    return standin(
        supplier_name=f("Kushiyara Box Industries", "Kushiyara Box Industries"),
        line_items=[row_fields(row, vat_included=f("yes", vat_quote))],
        grand_total_bdt=f("282,000.00", "Total (all taxes included) 282,000.00"),
        vat_shown_separately=f("no", "Total (all taxes included) 282,000.00"),
    )


def test_pack_10_joined_header_verifies_only_when_quoted_exactly():
    pdf = extract_text(DATA / "supplier-pack-10.pdf")
    assert "Unit price (BDT, VAT included)Amount (BDT)" in pdf["full_text"]  # the cells really are joined
    exact = verify_evidence(normalize_extraction(RawExtraction.model_validate(pack10_raw("Unit price (BDT, VAT included)")), PR), pdf)
    assert exact.line_items[0].vat_included_in_price.value is True and is_usable(exact.line_items[0].vat_included_in_price)
    spaced = verify_evidence(normalize_extraction(
        RawExtraction.model_validate(pack10_raw("Unit price (BDT, VAT included) Amount (BDT)")), PR), pdf)
    field = spaced.line_items[0].vat_included_in_price
    assert field.state == FieldState.UNKNOWN and field.value is None  # not silently rewritten to match
    assert field.evidence.source_quote == "Unit price (BDT, VAT included) Amount (BDT)"


def test_pack_10_vat_presentation_fields():
    doc, _ = run("10", pack10_raw("Unit price (BDT, VAT included)"))
    assert is_usable(doc.vat_shown_separately) and doc.vat_shown_separately.value is False
    assert is_usable(doc.grand_total_bdt) and doc.grand_total_bdt.value == Decimal("282000.00")
    # the document talks about VAT/taxes, so no VAT rate or separate-VAT row is ABSENT-by-search
    assert doc.vat_rate_pct.state == FieldState.UNKNOWN and doc.subtotal_bdt.state == FieldState.UNKNOWN


def test_all_ten_pdfs_can_be_loaded_and_verified_with_an_empty_extraction():
    """A model that returns nothing must never yield a VERIFIED value from any real pack."""
    for n in range(1, 11):
        pdf = extract_text(DATA / f"supplier-pack-{n:02d}.pdf")
        doc = verify_evidence(normalize_extraction(RawExtraction(), PR), pdf)
        for name in ("supplier_name", "subtotal_bdt", "delivery_days", "advance_payment_pct", "validity_days"):
            assert not is_usable(getattr(doc, name)), (n, name)
        assert not is_usable(doc.payment) and not is_usable(doc.penalty) and doc.line_items == []


# ---------------------------------------------------------------------------------------------
# Clause-specific verification must still accept the REAL clauses (review fix: delivery / payment / validity /
# penalty now need their own wording, not just the right number). The quotes below are cut from each PDF's own text
# by sentence; the values are read from those quotes. This tests the verifier's plumbing only. It is not a
# reference for any decision and must never be used as ground truth.
# ---------------------------------------------------------------------------------------------

import re  # noqa: E402

from src.parsing import normalize_text, sentences  # noqa: E402


def _clauses(pack: str) -> dict:
    text = normalize_text(extract_text(DATA / f"supplier-pack-{pack}.pdf")["full_text"])
    found = {}
    for sentence in sentences(text):
        low = sentence.lower()
        if "within" in low and "days" in low or "weeks" in low and "within" in low:
            if ("delivered" in low or low.startswith("delivery") or "delivery within" in low) and re.search(r"\bPO\b|purchase order", sentence):
                found.setdefault("delivery", sentence)
            elif re.search(r"payment|balance", low):
                found.setdefault("payment", sentence)
        if "quotation is valid" in low:
            found.setdefault("validity", sentence)
        if ("penalty" in low or "late delivery" in low) and "%" in sentence:
            found.setdefault("penalty", sentence)
    return found


@pytest.mark.parametrize("pack", [f"{n:02d}" for n in range(1, 11)])
def test_real_clauses_verify_under_clause_specific_parsing(pack):
    clauses = _clauses(pack)
    period = lambda s: re.search(r"\d+ (?:days|weeks)", s).group(0)  # noqa: E731
    pay_period = lambda s: re.search(r"\d+(?: to \d+)? days", s).group(0)  # noqa: E731
    overrides = {
        "delivery_period": f(period(clauses["delivery"]), clauses["delivery"]),
        "validity_period": f(period(clauses["validity"]), clauses["validity"]),
        "payment_terms_wording": f(clauses["payment"], clauses["payment"]),
        "payment_period": f(pay_period(clauses["payment"]), clauses["payment"]),
    }
    if "penalty" in clauses:
        overrides["penalty_wording"] = f(clauses["penalty"], clauses["penalty"])
    doc, _ = run(pack, standin(**overrides))
    assert is_usable(doc.delivery_days), (pack, clauses["delivery"])
    assert is_usable(doc.delivery_original_wording), pack
    assert is_usable(doc.validity_days), (pack, clauses["validity"])
    assert is_usable(doc.validity_original_wording), pack
    assert is_usable(doc.payment), (pack, clauses["payment"])
    if "penalty" in clauses:
        assert is_usable(doc.penalty), (pack, clauses["penalty"])
    else:  # a pack with no penalty clause: the model says nothing, and the independent search decides
        assert doc.penalty.state == FieldState.ABSENT, pack
