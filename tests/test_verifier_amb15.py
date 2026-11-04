"""AMB-15 regression tests: when a VAT basis of 'subtotal' is established by the totals-block LAYOUT (SPEC §13).

The rule under test: a model is asked for the wording that states what VAT applies to, never to infer it from row
order. The verifier then reads the PDF's own lines. Only a unique, plain, contiguous Subtotal -> VAT -> Total block,
with nothing in the document limiting or contradicting it, yields ``vat_price_basis = "subtotal"``, and the result
records AMB-15 in the evidence's normalization_method, the VAT/grand arithmetic checks and NR-06.

Synthetic documents (``SYNTH_TEXT``) plus the ten real PDFs. The real-pack checks use an EMPTY model output, so the
outcome comes from the PDF alone and cannot be a model claim.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from src.arithmetic import run_arithmetic_checks
from src.extractor import SYSTEM_INSTRUCTION, build_prompt, normalize_extraction
from src.models import AMB_VAT_SUBTOTAL, FieldState, RawExtraction, RuleStatus, VerificationStatus, is_usable
from src.pdf_reader import extract_text
from src.rule_engine import collect_assumptions, evaluate_all_rules
from src.verifier import AMB15_METHOD, verify_evidence
from tests.m3_helpers import NULL, SYNTH_TEXT, f, make_pdf_result, make_pr, raw_with
from tests.test_verifier_realpacks import pack01_raw

PR = make_pr()
DATA = Path(__file__).resolve().parent.parent / "assessment-data"
BLOCK = "Subtotal 5,664,000.00\nVAT 15% 849,600.00\nTotal 6,513,600.00"
BLOCK_QUOTE = "Subtotal 5,664,000.00 VAT 15% 849,600.00 Total 6,513,600.00"
real_packs = pytest.mark.skipif(not (DATA / "supplier-pack-01.pdf").exists(), reason="assessment-data not present")


def with_totals(totals: str, extra: str = "") -> str:
    assert BLOCK in SYNTH_TEXT
    return SYNTH_TEXT.replace(BLOCK, totals) + extra


def basis(text: str, claim=NULL, claims_extra: dict | None = None):
    raw = raw_with(vat_price_basis=claim, **(claims_extra or {}))
    return verify_evidence(normalize_extraction(raw, PR), make_pdf_result([text])).vat_price_basis


def assert_unknown(field):
    assert field.state == FieldState.UNKNOWN and field.value is None and not is_usable(field)


# ---- the rule itself ----------------------------------------------------------------------------------------


def test_the_plain_totals_block_establishes_subtotal_even_when_the_model_says_nothing():
    field = basis(SYNTH_TEXT, claim=NULL)
    assert is_usable(field) and field.value == "subtotal"
    ev = field.evidence
    assert ev.verification_status == VerificationStatus.VERIFIED and ev.page_number == 1
    assert ev.normalized_value == "subtotal" and ev.raw_value is None


def test_the_evidence_is_the_three_real_rows_not_the_words_somewhere_in_the_document():
    ev = basis(SYNTH_TEXT).evidence
    assert ev.source_quote == BLOCK_QUOTE
    assert make_pdf_result([SYNTH_TEXT])["pages"][0]["text"].replace("\n", " ").count(ev.source_quote) == 1
    for row in ("Subtotal 5,664,000.00", "VAT 15% 849,600.00", "Total 6,513,600.00"):
        assert row in ev.source_quote


def test_amb15_is_recorded_in_the_evidence_normalization_method():
    ev = basis(SYNTH_TEXT).evidence
    assert ev.normalization_method == AMB15_METHOD and ev.normalization_method.startswith(AMB_VAT_SUBTOTAL)
    assert AMB_VAT_SUBTOTAL == "AMB-15"


def test_a_model_claim_of_subtotal_from_layout_is_replaced_by_the_verifiers_own_reading_and_keeps_raw_value():
    field = basis(SYNTH_TEXT, claim=f("subtotal", "Subtotal 5,664,000.00 VAT 15% 849,600.00"))
    assert is_usable(field) and field.evidence.normalization_method == AMB15_METHOD
    assert field.evidence.raw_value == "subtotal" and field.evidence.source_quote == BLOCK_QUOTE


def test_words_that_state_the_basis_verify_without_amb15():
    quote = "VAT is calculated on the subtotal."
    field = basis(with_totals(BLOCK, "\n" + quote), claim=f("on the subtotal", quote))
    assert is_usable(field) and field.value == "subtotal"
    assert AMB_VAT_SUBTOTAL not in (field.evidence.normalization_method or "")
    assert field.evidence.source_quote == quote


# ---- the layouts that must NOT establish 'subtotal' (the user's table) ----------------------------------------


@pytest.mark.parametrize("totals", [
    "Subtotal 5,664,000.00\nVAT on selected items 15% 849,600.00\nTotal 6,513,600.00",
    "Subtotal 5,664,000.00\nVAT 15% on selected items 849,600.00\nTotal 6,513,600.00",
    "Subtotal 5,664,000.00\nVAT 15% on part of the subtotal 849,600.00\nTotal 6,513,600.00",
    "Subtotal 5,664,000.00\nVAT 15% applies only to items A and B 849,600.00\nTotal 6,513,600.00",
    "Subtotal 5,664,000.00\nVAT 15% (except item B) 849,600.00\nTotal 6,513,600.00",
], ids=["selected-items", "selected-items-after-rate", "part-of-subtotal", "only-items", "except"])
def test_vat_limited_to_part_of_the_subtotal_is_not_subtotal(totals):
    assert_unknown(basis(with_totals(totals)))


def test_a_vat_row_without_a_preceding_subtotal_is_unknown():
    assert_unknown(basis(with_totals("VAT 15% 849,600.00\nTotal 6,513,600.00")))


def test_a_total_that_comes_before_the_vat_is_not_the_block():
    assert_unknown(basis(with_totals("Subtotal 5,664,000.00\nTotal 6,513,600.00\nVAT 15% 849,600.00")))


def test_subtotal_and_vat_rows_separated_by_unrelated_content_are_unknown():
    assert_unknown(basis(with_totals("Subtotal 5,664,000.00\nShipping 500.00\nVAT 15% 849,600.00\nTotal 6,513,600.00")))
    assert_unknown(basis(with_totals("Subtotal 5,664,000.00\nVAT 15% 849,600.00\nDiscount 100.00\nTotal 6,513,600.00")))


def test_a_subtotal_and_vat_with_no_total_row_is_not_the_block():
    assert_unknown(basis(with_totals("Subtotal 5,664,000.00\nVAT 15% 849,600.00\nSupplier notes follow")))


def test_a_total_row_that_says_taxes_are_included_is_not_the_block():
    assert_unknown(basis(with_totals("Subtotal 5,664,000.00\nVAT 15% 849,600.00\nTotal (all taxes included) 6,513,600.00")))


def test_multiple_possible_tax_bases_are_unknown_two_different_blocks():
    second = "Subtotal 100.00\nVAT 15% 15.00\nTotal 115.00"
    assert_unknown(basis(with_totals(BLOCK + "\nOptional extras\n" + second)))


def test_multiple_possible_tax_bases_are_unknown_a_second_vat_row():
    assert_unknown(basis(with_totals(BLOCK, "\nVAT 5% 25.00")))


def test_the_same_block_printed_twice_is_one_block_not_a_conflict():
    pdf = make_pdf_result([SYNTH_TEXT, "Copy of page 1\n" + BLOCK])
    field = verify_evidence(normalize_extraction(raw_with(vat_price_basis=NULL), PR), pdf).vat_price_basis
    assert is_usable(field) and field.value == "subtotal"


def test_a_document_that_says_prices_include_vat_is_unknown_even_with_a_block():
    assert_unknown(basis(with_totals(BLOCK, "\nAll prices are inclusive of VAT.")))


# ---- explicit scope-limiting wording overrides the structural assumption --------------------------------------


@pytest.mark.parametrize("note", [
    "VAT applies only to items A and B.",
    "VAT is charged on selected items.",
    "Only item C attracts VAT.",
    "VAT is not applicable to certain items.",
    "VAT is applicable to\nselected items.",  # the sentence wraps onto the next PDF line
])
def test_a_scope_limiting_statement_anywhere_in_the_document_overrides_the_plain_block(note):
    assert_unknown(basis(with_totals(BLOCK, "\n" + note)))


def test_a_vat_sentence_that_does_not_limit_scope_does_not_override():
    assert is_usable(basis(with_totals(BLOCK, "\nVAT will be charged as per prevailing law.")))


def test_a_scope_word_in_an_unrelated_sentence_does_not_override():
    note = "Only authorised signatories may sign. VAT will be charged as per prevailing law."
    assert is_usable(basis(with_totals(BLOCK, "\n" + note)))


# ---- the model's own output is never silently overridden ------------------------------------------------------


def test_a_verified_scope_limiting_claim_is_kept_and_never_replaced_by_subtotal():
    text = with_totals(BLOCK, "\nNote: VAT on selected items only.")
    field = basis(text, claim=f("VAT on selected items only", "Note: VAT on selected items only."))
    assert field.value != "subtotal"


def test_a_failed_claim_of_other_wording_is_not_overridden():
    field = basis(SYNTH_TEXT, claim=f("on selected items", "VAT on selected items"))  # quote is not in the document
    assert_unknown(field)
    assert field.evidence.normalized_value == "on selected items"


def test_a_claim_without_a_quote_is_not_overridden():
    assert_unknown(basis(SYNTH_TEXT, claim=f("subtotal", None)))


# ---- the real PDFs: no model involved --------------------------------------------------------------------------


@real_packs
@pytest.mark.parametrize("n", range(1, 10))
def test_real_packs_01_to_09_have_the_plain_block_and_establish_subtotal_without_any_model_claim(n):
    pdf = extract_text(DATA / f"supplier-pack-{n:02d}.pdf")
    field = verify_evidence(normalize_extraction(RawExtraction(), PR), pdf).vat_price_basis
    assert is_usable(field) and field.value == "subtotal"
    quote = field.evidence.source_quote
    assert quote.startswith("Subtotal ") and " VAT 15% " in quote and " Total " in quote
    assert quote in " ".join(pdf["full_text"].split())  # a real, verbatim excerpt of the PDF
    assert field.evidence.normalization_method == AMB15_METHOD


@real_packs
def test_real_pack_10_vat_inclusive_total_is_unknown():
    pdf = extract_text(DATA / "supplier-pack-10.pdf")
    assert_unknown(verify_evidence(normalize_extraction(RawExtraction(), PR), pdf).vat_price_basis)


# ---- prompt: ask for wording as written, never infer from row order --------------------------------------------


def test_the_prompt_asks_for_the_wording_as_written_and_forbids_inferring_from_row_order():
    pdf = {"full_text": "--- PAGE 1 ---\nx", "pages": [{"page_number": 1, "text": "x"}], "page_count": 1}
    system, _ = build_prompt(pdf, PR)
    assert system == SYSTEM_INSTRUCTION
    entry = system.split("vat_price_basis:")[1].split("delivery_period:")[0]
    assert "exactly as written" in entry and "Never infer it from the order or position of" in entry
    assert "return null" in entry
    assert 'Return "subtotal" ONLY' not in entry  # the old layout-inference instruction is gone


# ---- reporting: arithmetic and NR-06 carry AMB-15 -------------------------------------------------------------


def _pack01_doc(explicit: bool = False):
    raw = pack01_raw()
    quote = "VAT is calculated on the subtotal."
    raw["vat_price_basis"] = f("on the subtotal", quote) if explicit else NULL
    pdf = extract_text(DATA / "supplier-pack-01.pdf")
    if explicit:  # the real pack has no such sentence; add one to a copy of its text
        pdf = make_pdf_result([pdf["full_text"].split("--- PAGE 1 ---\n")[1] + "\n" + quote])
    return verify_evidence(normalize_extraction(RawExtraction.model_validate(raw), PR), pdf)


@real_packs
def test_m1_vat_formula_is_unchanged_and_the_vat_and_grand_checks_record_amb15():
    doc = _pack01_doc()
    checks = {c.check_name: c for c in run_arithmetic_checks(doc) if c.item_id is None}
    assert checks["vat"].status == "PASS" and checks["vat"].calculated == Decimal("849600.00")  # 5,664,000.00 x 15 / 100
    assert checks["grand"].status == "PASS" and checks["grand"].calculated == Decimal("6513600.00")
    # AMB-09 (rounding/tolerance) is recorded on every computed check; AMB-15 only on the two that use the basis
    assert set(checks["vat"].assumptions_applied) == {"AMB-09", AMB_VAT_SUBTOTAL} == set(checks["grand"].assumptions_applied)
    assert checks["independent_subtotal"].assumptions_applied == ["AMB-09"]
    vat_refs = [r.source_quote for r in checks["vat"].evidence_refs]
    assert BLOCK_QUOTE in vat_refs  # the audit trail shows the three real rows


@real_packs
def test_nr06_and_the_pack_level_assumptions_report_amb15():
    doc = _pack01_doc()
    results = evaluate_all_rules(doc, PR, run_arithmetic_checks(doc))
    nr06 = next(r for r in results if r.rule_id == "NR-06")
    assert nr06.status == RuleStatus.PASS and set(nr06.assumptions_applied) == {"AMB-09", AMB_VAT_SUBTOTAL}
    assert AMB_VAT_SUBTOTAL in collect_assumptions(results)


@real_packs
def test_amb15_is_not_reported_when_the_basis_was_stated_in_words():
    doc = _pack01_doc(explicit=True)
    assert is_usable(doc.vat_price_basis) and AMB_VAT_SUBTOTAL not in (doc.vat_price_basis.evidence.normalization_method or "")
    results = evaluate_all_rules(doc, PR, run_arithmetic_checks(doc))
    assert next(r for r in results if r.rule_id == "NR-06").assumptions_applied == ["AMB-09"]
    assert AMB_VAT_SUBTOTAL not in collect_assumptions(results)


def test_a_wrong_printed_vat_fails_nr06_and_the_failure_reports_amb15():
    text = with_totals("Subtotal 5,664,000.00\nVAT 15% 800,000.00\nTotal 6,513,600.00")
    raw = raw_with(vat_price_basis=NULL, vat_amount_bdt=f("800,000.00", "VAT 15% 800,000.00"),
                   vat_shown_separately=f("yes", "VAT 15% 800,000.00"), vat_rate_pct=f("15", "VAT 15% 800,000.00"))
    doc = verify_evidence(normalize_extraction(raw, PR), make_pdf_result([text]))
    assert is_usable(doc.vat_amount_bdt) and doc.vat_price_basis.evidence.normalization_method == AMB15_METHOD
    checks = run_arithmetic_checks(doc)
    vat = next(c for c in checks if c.check_name == "vat")
    assert vat.status == "FAIL" and vat.calculated == Decimal("849600.00") and vat.printed == Decimal("800000.00")
    results = evaluate_all_rules(doc, PR, checks)
    nr06 = next(r for r in results if r.rule_id == "NR-06")
    assert nr06.status == RuleStatus.FAIL and AMB_VAT_SUBTOTAL in nr06.assumptions_applied
    assert AMB_VAT_SUBTOTAL in collect_assumptions(results)


def test_no_basis_means_the_vat_check_is_unknown_not_fail_and_reports_no_assumption():
    doc = verify_evidence(normalize_extraction(raw_with(vat_price_basis=NULL), PR),
                          make_pdf_result([with_totals("Subtotal 5,664,000.00\nVAT 15% on selected items 849,600.00\nTotal 6,513,600.00")]))
    vat = next(c for c in run_arithmetic_checks(doc) if c.check_name == "vat")
    assert vat.status == "UNKNOWN" and vat.assumptions_applied == [] and "VAT price basis is not usable" in vat.detail
