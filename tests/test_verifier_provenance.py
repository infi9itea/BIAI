"""M3 review fixes: claim provenance and wrong-clause verification. Synthetic text and model output only.

Two rules these tests pin down (SPEC §3.1, §6):

1. A model CLAIM that cannot be verified is UNKNOWN. Only a field the model never claimed, and that an independent
   document search cannot find, may be ABSENT. A value with no quote is still a claim.
2. A quote that appears in the PDF is not enough. It must support the SPECIFIC field: a payment value needs a
   payment clause, a delivery value a delivery clause counted from the PO, a validity value a quotation-validity
   clause, a penalty a late-delivery penalty clause. A different clause that happens to hold the same number must
   not verify.
"""

import pytest

from src.extractor import normalize_extraction
from src.models import FieldState, VerificationStatus, is_usable
from src.verifier import verify_evidence
from tests.m3_helpers import NULL, ROW, SYNTH_TEXT, f, make_pdf_result, make_pr, raw_with, with_line

PR = make_pr()


def verify(text: str = SYNTH_TEXT, **overrides):
    raw = raw_with(**overrides)
    # Deliberately the public two-argument form: the claim must survive normalization inside the document itself.
    return verify_evidence(normalize_extraction(raw, PR), make_pdf_result([text]))


def replace(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new)


def assert_unknown_claim(field):
    assert field.state == FieldState.UNKNOWN, field.state
    assert field.absence_evidence is None
    assert not is_usable(field)


# ---------------------------------------------------------------------------------------------
# 1. An unquoted claim is never ABSENT
# ---------------------------------------------------------------------------------------------

NO_IDS = replace(SYNTH_TEXT, "Supplier TIN: 101458013391 BIN: 000170141-0101", "Supplier registered locally")
NO_PENALTY = replace(SYNTH_TEXT, "3. Delay. Late delivery penalty: 1% of order value for each full week of delay.\n", "")
NO_QTY_HEADING = replace(SYNTH_TEXT, "Item Description Qty Unit", "Item Description Count Unit")


def test_control_a_null_tin_and_bin_are_absent_when_the_document_has_none():
    doc = verify(NO_IDS, tin=NULL, bin=NULL)
    assert doc.tin.state == FieldState.ABSENT and doc.bin.state == FieldState.ABSENT


def test_tin_value_without_a_quote_is_unknown_not_absent():
    doc = verify(NO_IDS, tin=f("101458013391", None), bin=NULL)
    assert_unknown_claim(doc.tin)
    assert doc.bin.state == FieldState.ABSENT  # the unclaimed neighbour is still decided by the search


def test_blank_quote_is_also_a_claim():
    doc = verify(NO_IDS, bin=f("000170141-0101", "   "), tin=NULL)
    assert_unknown_claim(doc.bin)


def test_a_claim_without_a_quote_never_verifies_even_when_the_value_is_in_the_pdf():
    doc = verify(tin=f("101458013391", None))  # the TIN really is in the PDF; still no quote, so not usable
    assert_unknown_claim(doc.tin)


def test_penalty_wording_without_a_quote_is_unknown_not_absent():
    doc = verify(NO_PENALTY, penalty_wording=f("1% per week", None), penalty_rate_pct=NULL, penalty_period=NULL,
                 penalty_calculation_basis=NULL, penalty_full_weeks_only=NULL)
    assert_unknown_claim(doc.penalty)


@pytest.mark.parametrize("component", ["penalty_rate_pct", "penalty_calculation_basis", "penalty_period",
                                       "penalty_full_weeks_only"])
def test_any_penalty_component_claimed_without_the_clause_wording_is_unknown_not_absent(component):
    values = {"penalty_rate_pct": "1", "penalty_calculation_basis": "order value",
              "penalty_period": "per week", "penalty_full_weeks_only": "yes"}
    overrides = {k: NULL for k in values}
    overrides[component] = f(values[component], "1% of order value per week")
    doc = verify(NO_PENALTY, penalty_wording=NULL, **overrides)
    assert_unknown_claim(doc.penalty)


def test_control_null_penalty_is_absent_when_no_penalty_words_exist():
    doc = verify(NO_PENALTY, penalty_wording=NULL, penalty_rate_pct=NULL, penalty_calculation_basis=NULL,
                 penalty_period=NULL, penalty_full_weeks_only=NULL)
    assert doc.penalty.state == FieldState.ABSENT and doc.penalty.absence_evidence is not None


def test_vat_fields_claimed_without_a_quote_are_unknown_not_absent():
    no_vat = replace(replace(SYNTH_TEXT, "VAT 15% 849,600.00\n", ""), "Total 6,513,600.00", "Total 5,664,000.00")
    doc = verify(no_vat, vat_shown_separately=f("yes", None), vat_rate_pct=f("15", None),
                 vat_amount_bdt=NULL, vat_price_basis=NULL, grand_total_bdt=NULL)
    assert_unknown_claim(doc.vat_shown_separately)
    assert_unknown_claim(doc.vat_rate_pct)


def test_line_item_sub_field_claimed_without_a_quote_is_unknown_not_absent():
    doc = verify(NO_QTY_HEADING, line_items=[with_line(quantity=f("12,000", None))])
    assert_unknown_claim(doc.line_items[0].quantity)


def test_control_null_line_quantity_is_absent_when_the_column_does_not_exist():
    doc = verify(NO_QTY_HEADING, line_items=[with_line(quantity=NULL)])
    assert doc.line_items[0].quantity.state == FieldState.ABSENT


def test_second_line_claim_is_tracked_by_its_own_position():
    two = replace(NO_QTY_HEADING, ROW, ROW + "\n" + ROW.replace("A Cotton", "B Cotton", 1))
    lines = [with_line(), with_line(quantity=f("12,000", None))]
    doc = verify(two, line_items=lines)
    assert doc.line_items[1].quantity.state == FieldState.UNKNOWN
    assert doc.line_items[1].quantity.absence_evidence is None


def test_normalization_records_which_fields_were_claimed_without_a_quote():
    doc = normalize_extraction(raw_with(tin=f("101458013391", None), penalty_wording=NULL, penalty_rate_pct=f("1", None),
                                        line_items=[with_line(unit=f("metre", None))]), PR)
    assert {"tin", "penalty", "line-1.unit"} <= set(doc.claimed_without_evidence)


def test_a_fully_quoted_extraction_claims_nothing_without_a_quote():
    assert normalize_extraction(raw_with(), PR).claimed_without_evidence == []


# ---------------------------------------------------------------------------------------------
# 2. A quote must support THIS field, not just contain the number
# ---------------------------------------------------------------------------------------------

WARRANTY_30 = "Defective goods will be replaced within 30 days after delivery."


def test_payment_value_cannot_be_verified_from_a_warranty_clause():
    text = replace(SYNTH_TEXT, "Balance payable within 45 days after delivery.", "Payment is due after 90 days.")
    text += "\n6. Warranty. " + WARRANTY_30
    doc = verify(text, payment_terms_wording=f(WARRANTY_30, WARRANTY_30), payment_period=f("30 days", WARRANTY_30))
    assert doc.payment.state == FieldState.UNKNOWN
    assert doc.payment.min_days is None and doc.payment.max_days is None and doc.payment.anchor is None
    assert not is_usable(doc.payment)
    assert doc.payment.evidence.verification_status == VerificationStatus.UNVERIFIED


def test_payment_clause_still_verifies_with_the_real_layouts():
    for sentence, bounds in (("Balance payable within 45 days after delivery.", (45, 45)),
                             ("Full payment within 30 days of delivery.", (30, 30)),
                             ("Payment within 30 to 90 days as agreed by both parties.", (30, 90))):
        text = replace(SYNTH_TEXT, "Balance payable within 45 days after delivery.", sentence)
        period = f"{bounds[0]} to {bounds[1]} days" if bounds[0] != bounds[1] else f"{bounds[0]} days"
        doc = verify(text, payment_terms_wording=f(sentence, sentence), payment_period=f(period, sentence))
        assert is_usable(doc.payment), sentence
        assert (doc.payment.min_days, doc.payment.max_days) == bounds


def test_delivery_value_cannot_be_verified_from_a_document_submission_clause():
    text = replace(SYNTH_TEXT, "Delivery within 24 days of receiving the purchase order.",
                   "Delivery promised after 60 days from PO.")
    text += "\n6. Documents. Submit documents within 24 days of PO."
    quote = "Submit documents within 24 days of PO."
    doc = verify(text, delivery_period=f("24 days", quote))
    assert_downgraded_unknown(doc.delivery_days)
    assert_downgraded_unknown(doc.delivery_original_wording)


def test_delivery_clause_with_po_anchor_still_verifies():
    for sentence, days in (("Goods will be delivered at your factory within 21 days from the date of PO.", 21),
                           ("Delivery within 4 weeks of PO.", 28),
                           ("Delivery within 24 days of receiving the purchase order.", 24)):
        text = replace(SYNTH_TEXT, "Delivery within 24 days of receiving the purchase order.", sentence)
        value = "4 weeks" if days == 28 else f"{days} days"
        doc = verify(text, delivery_period=f(value, sentence))
        assert is_usable(doc.delivery_days) and doc.delivery_days.value == days, sentence


def test_delivery_wording_without_a_po_anchor_does_not_verify():
    text = replace(SYNTH_TEXT, "Delivery within 24 days of receiving the purchase order.",
                   "Delivery within 24 days of the date above.")
    doc = verify(text, delivery_period=f("24 days", "Delivery within 24 days of the date above."))
    assert_downgraded_unknown(doc.delivery_days)


def test_validity_value_cannot_be_verified_from_a_warranty_clause():
    text = replace(SYNTH_TEXT, "valid for 45 days", "valid for 20 days")
    text += "\n6. Warranty. The warranty period is 24 days."
    quote = "The warranty period is 24 days."
    doc = verify(text, validity_period=f("24 days", quote))
    assert_downgraded_unknown(doc.validity_days)
    assert_downgraded_unknown(doc.validity_original_wording)


def test_validity_clause_still_verifies_and_a_warranty_clause_does_not_conflict_with_it():
    text = SYNTH_TEXT + "\n6. Warranty. The warranty period is 24 days."
    doc = verify(text)
    assert is_usable(doc.validity_days) and doc.validity_days.value == 45


def test_a_generic_late_delivery_sentence_is_not_a_penalty_clause():
    sentence = "Late delivery will be reported to the buyer immediately."
    text = replace(SYNTH_TEXT, "Late delivery penalty: 1% of order value for each full week of delay.", sentence)
    doc = verify(text, penalty_wording=f(sentence, sentence), penalty_rate_pct=NULL, penalty_calculation_basis=NULL,
                 penalty_period=NULL, penalty_full_weeks_only=NULL)
    assert doc.penalty.state == FieldState.UNKNOWN and not is_usable(doc.penalty)
    assert doc.penalty.absence_evidence is None  # words about late delivery exist, so never ABSENT either


@pytest.mark.parametrize("sentence", [
    "Late delivery penalty: 1% of order value for each full week of delay.",
    "For late delivery the supplier shall pay 0.75% of the order value per week of delay.",
    "Delay in delivery will attract a penalty of 0.5% of order value per week.",
    "Late delivery penalty of 0.5% per week of delay.",
])
def test_real_penalty_wordings_still_verify(sentence):
    text = replace(SYNTH_TEXT, "Late delivery penalty: 1% of order value for each full week of delay.", sentence)
    doc = verify(text, penalty_wording=f(sentence, sentence), penalty_rate_pct=NULL, penalty_calculation_basis=NULL,
                 penalty_period=NULL, penalty_full_weeks_only=NULL)
    assert is_usable(doc.penalty), sentence


def assert_downgraded_unknown(field):
    assert field.state == FieldState.UNKNOWN and field.value is None and not is_usable(field)
    assert field.evidence is not None and field.evidence.verification_status != VerificationStatus.VERIFIED
