"""Reproductions for three verification gaps found in review (written BEFORE any fix; they must fail first).

A quote that contains the right words and numbers is not enough. It must support the field on its own clause:

* VAT basis: "Subtotal" and "VAT" both appearing does not mean VAT applies to the whole subtotal.
* Delivery: a period in a different clause (after a semicolon) is not the delivery time.
* Payment: a period in a different clause (after a semicolon) is not the payment period, and its wording
  must not set the payment anchor.
Synthetic text only.
"""

from src.extractor import normalize_extraction
from src.models import FieldState, VerificationStatus, is_usable
from src.parsing import canonical_vat_basis
from src.verifier import verify_evidence
from tests.m3_helpers import NULL, SYNTH_TEXT, f, make_pdf_result, make_pr, raw_with

PR = make_pr()


def verify(text: str, **overrides):
    return verify_evidence(normalize_extraction(raw_with(**overrides), PR), make_pdf_result([text]))


def replace(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new)


# ---- 1. VAT basis ------------------------------------------------------------------------------------------

PARTIAL_VAT_TEXT = replace(SYNTH_TEXT, "Subtotal 5,664,000.00\nVAT 15% 849,600.00",
                           "Subtotal BDT 100,000\nVAT on selected items 15% BDT 5,000")
PARTIAL_VAT_QUOTE = "Subtotal BDT 100,000 VAT on selected items 15% BDT 5,000"


def test_vat_on_selected_items_does_not_verify_as_vat_on_the_subtotal():
    doc = verify(PARTIAL_VAT_TEXT, vat_price_basis=f("subtotal", PARTIAL_VAT_QUOTE))
    basis = doc.vat_price_basis
    assert not is_usable(basis), "VAT limited to selected items was verified as basis 'subtotal'"
    assert basis.value is None


def test_a_limited_vat_scope_is_not_normalized_to_subtotal():
    for wording in ("VAT on selected items of the subtotal", "VAT on part of the subtotal",
                    "VAT applies only to items A and B, not the whole subtotal"):
        assert canonical_vat_basis(wording) != "subtotal", wording


def test_control_the_ordinary_subtotal_then_vat_layout_still_verifies():
    quote = "Subtotal 5,664,000.00 VAT 15% 849,600.00"
    doc = verify(SYNTH_TEXT, vat_price_basis=f("subtotal", quote))
    assert is_usable(doc.vat_price_basis) and doc.vat_price_basis.value == "subtotal"


# ---- 2. Delivery crosses a clause boundary -----------------------------------------------------------------

DELIVERY_OLD = "Delivery within 24 days of receiving the purchase order."
SEMI_DELIVERY = "Delivery schedule will be confirmed; technical documents must be submitted within 24 days of PO."


def test_a_document_deadline_after_a_semicolon_is_not_the_delivery_time():
    text = replace(SYNTH_TEXT, DELIVERY_OLD, SEMI_DELIVERY)
    doc = verify(text, delivery_period=f("24 days", SEMI_DELIVERY))
    assert not is_usable(doc.delivery_days), "a document-submission deadline was verified as delivery time"
    assert doc.delivery_days.value is None
    assert not is_usable(doc.delivery_original_wording)


def test_control_a_real_delivery_clause_next_to_other_clauses_still_verifies():
    both = "Delivery within 24 days of PO; technical documents must be submitted within 10 days of PO."
    text = replace(SYNTH_TEXT, DELIVERY_OLD, both)
    doc = verify(text, delivery_period=f("24 days", "Delivery within 24 days of PO"))
    assert is_usable(doc.delivery_days) and doc.delivery_days.value == 24


# ---- 3. Payment crosses a clause boundary ------------------------------------------------------------------

PAYMENT_OLD = "Balance payable within 45 days after delivery."
SEMI_PAYMENT = "Payment terms will be confirmed separately; replacement goods will be provided within 30 days after delivery."


def test_a_replacement_deadline_after_a_semicolon_is_not_the_payment_period():
    text = replace(SYNTH_TEXT, PAYMENT_OLD, SEMI_PAYMENT)
    doc = verify(text, payment_terms_wording=f(SEMI_PAYMENT, SEMI_PAYMENT), payment_period=f("30 days", SEMI_PAYMENT))
    pay = doc.payment
    assert pay.min_days is None and pay.max_days is None, "the replacement deadline was verified as a payment period"
    assert not is_usable(pay) or pay.min_days is None
    assert pay.anchor != "delivery", "the anchor was taken from the replacement clause"


def test_control_a_real_payment_clause_next_to_other_clauses_still_verifies():
    both = "Balance payable within 45 days after delivery; replacement goods within 30 days after delivery."
    text = replace(SYNTH_TEXT, PAYMENT_OLD, both)
    quote = "Balance payable within 45 days after delivery"
    doc = verify(text, payment_terms_wording=f(quote, quote), payment_period=f("45 days", quote))
    assert is_usable(doc.payment) and (doc.payment.min_days, doc.payment.max_days) == (45, 45)
    assert doc.payment.anchor == "delivery"


# ---- positive coverage and relationship-not-words (review) ---------------------------------------------------

import pytest  # noqa: E402

from src.verifier import payment_anchor  # noqa: E402

TOTALS_OLD = "Subtotal 5,664,000.00\nVAT 15% 849,600.00"


def basis_doc(totals: str, quote: str):
    return verify(replace(SYNTH_TEXT, TOTALS_OLD, totals), vat_price_basis=f("subtotal", quote))


def test_explicit_vat_on_subtotal_wording_verifies():
    doc = basis_doc("Subtotal 5,664,000.00\nVAT @ 15% on the subtotal 849,600.00", "VAT @ 15% on the subtotal 849,600.00")
    assert is_usable(doc.vat_price_basis) and doc.vat_price_basis.value == "subtotal"


def test_explicit_vat_calculated_on_subtotal_in_a_note_verifies():
    quote = "VAT is calculated on the subtotal."
    doc = basis_doc(TOTALS_OLD + "\n" + quote, quote)
    assert is_usable(doc.vat_price_basis)


@pytest.mark.parametrize("totals,quote", [
    ("Subtotal 5,664,000.00\nVAT 15% applies only to items A and B 849,600.00",
     "Subtotal 5,664,000.00 VAT 15% applies only to items A and B 849,600.00"),
    ("Subtotal 5,664,000.00\nVAT 15% on selected items 849,600.00",
     "Subtotal 5,664,000.00 VAT 15% on selected items 849,600.00"),
    ("Subtotal 5,664,000.00\nVAT on the subtotal of selected items 849,600.00",
     "Subtotal 5,664,000.00 VAT on the subtotal of selected items 849,600.00"),
    ("Subtotal 5,664,000.00\nVAT 15% on part of the subtotal 849,600.00",
     "Subtotal 5,664,000.00 VAT 15% on part of the subtotal 849,600.00"),
])
def test_limited_vat_scope_never_verifies_as_subtotal_in_any_word_order(totals, quote):
    assert not is_usable(basis_doc(totals, quote).vat_price_basis)


def test_a_subtotal_row_and_a_vat_row_with_something_between_them_is_not_a_relationship():
    totals = "Subtotal 5,664,000.00\nShipping 500.00\nVAT 15% 849,600.00"
    quote = "Subtotal 5,664,000.00 Shipping 500.00 VAT 15% 849,600.00"
    assert not is_usable(basis_doc(totals, quote).vat_price_basis)


def test_the_words_vat_and_subtotal_in_unrelated_places_do_not_establish_the_basis():
    quote = "VAT 15% 849,600.00 Total 6,513,600.00 Subtotal 5,664,000.00"  # the VAT row comes BEFORE the subtotal
    totals = "VAT 15% 849,600.00\nTotal 6,513,600.00\nSubtotal 5,664,000.00"
    assert not is_usable(basis_doc(totals, quote).vat_price_basis)


def test_a_vat_row_with_the_value_not_subtotal_keeps_its_own_wording_or_is_unknown():
    doc = verify(PARTIAL_VAT_TEXT, vat_price_basis=f("selected items", "VAT on selected items 15% BDT 5,000"))
    assert doc.vat_price_basis.value in (None, "selected items")  # never 'subtotal'


@pytest.mark.parametrize("wording,expected", [
    ("on the Sub-total", "subtotal"),
    ("VAT calculated on the subtotal", "subtotal"),
    ("select items", "select items"),
    ("VAT on selected items of the subtotal", "vat on selected items of the subtotal"),
    ("only part of the subtotal", "only part of the subtotal"),
])
def test_vat_basis_normalization(wording, expected):
    assert canonical_vat_basis(wording) == expected


def test_a_genuine_payment_clause_after_an_unrelated_clause_is_still_identified():
    quote = "Payment terms will be confirmed separately; balance payable within 45 days after delivery."
    text = replace(SYNTH_TEXT, "Balance payable within 45 days after delivery.", quote)
    doc = verify(text, payment_terms_wording=f(quote, quote), payment_period=f("45 days", quote))
    assert is_usable(doc.payment) and (doc.payment.min_days, doc.payment.max_days) == (45, 45)
    assert doc.payment.anchor == "delivery"


def test_a_genuine_payment_clause_before_an_unrelated_clause_is_still_identified():
    quote = "Balance payable within 45 days after delivery; replacement goods will be provided within 30 days after delivery."
    text = replace(SYNTH_TEXT, "Balance payable within 45 days after delivery.", quote)
    doc = verify(text, payment_terms_wording=f(quote, quote), payment_period=f("45 days", quote))
    assert is_usable(doc.payment) and (doc.payment.min_days, doc.payment.max_days) == (45, 45)
    assert doc.payment.anchor == "delivery"


@pytest.mark.parametrize("quote,anchor", [
    ("Payment terms will be confirmed separately; replacement goods will be provided within 30 days after delivery.", None),
    ("Balance payable within 45 days after delivery; replacement goods within 30 days after acceptance.", "delivery"),
    ("Replacement within 30 days after delivery; payment within 45 days of invoice.", "invoice"),
])
def test_the_payment_anchor_comes_only_from_the_payment_clause(quote, anchor):
    assert payment_anchor(quote) == anchor


def test_a_payment_wording_without_a_period_does_not_borrow_an_anchor_from_another_clause():
    quote = "Payment terms will be confirmed separately; replacement goods will be provided within 30 days after delivery."
    text = replace(SYNTH_TEXT, "Balance payable within 45 days after delivery.", quote)
    doc = verify(text, payment_terms_wording=f(quote, quote), payment_period=NULL)
    assert doc.payment.anchor != "delivery" and doc.payment.min_days is None


@pytest.mark.parametrize("sentence,days", [
    ("Delivery within 24 days of PO.", 24),
    ("Goods will be delivered at your factory within 21 days from the date of PO.", 21),
    ("Delivery within 4 weeks of PO; documents within 10 days of PO.", 28),
])
def test_normal_delivery_clauses_still_verify_after_semicolon_splitting(sentence, days):
    text = replace(SYNTH_TEXT, DELIVERY_OLD, sentence)
    value = "4 weeks" if days == 28 else f"{days} days"
    quote = sentence.split(";")[0]
    doc = verify(text, delivery_period=f(value, quote))
    assert is_usable(doc.delivery_days) and doc.delivery_days.value == days
