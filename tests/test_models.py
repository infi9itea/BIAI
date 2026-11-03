"""Tests for src/models.py (Milestone 0).

Covers: field-state invariants, LineItemField carrying AbsenceEvidence, strict no-float typing,
is_usable (candidate vs verified), PaymentTerms.discretionary never defaulting to False,
RuleResult / ArithmeticCheckResult / PackResult traceability rules, and the raw layer.
"""

import inspect
import typing
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from src import models
from src.models import (
    AbsenceEvidence,
    ArithmeticCheckResult,
    Decision,
    Evidence,
    EvidenceRef,
    ExtractedDocument,
    FieldRecord,
    FieldState,
    LineItem,
    LineItemField,
    PackResult,
    PaymentTerms,
    PenaltyClause,
    PRItem,
    PurchaseRequest,
    RawExtraction,
    RawField,
    RuleResult,
    RuleSeverity,
    RuleStatus,
    StrictDecimal,
    StrictInt,
    VerificationStatus,
    is_usable,
)

# ---------- helpers ----------


def ev(status=VerificationStatus.VERIFIED, quote="Subtotal 5,664,000.00", **kw) -> Evidence:
    return Evidence(source_quote=quote, verification_status=status, page_number=1, **kw)


def absence(complete=True, field="tin") -> AbsenceEvidence:
    return AbsenceEvidence(
        field_searched=field,
        search_terms=["TIN"],
        pages_scanned=[1],
        extraction_complete=complete,
        contextual_excerpt="Supplier registration details available on request.",
        determination_basis="searched all pages",
    )


def found(value, status=VerificationStatus.VERIFIED):
    # Use the same strict types the production models use (Decimal -> StrictDecimal), so tests
    # exercise the real validation contract rather than Pydantic's lax defaults.
    field_type = StrictDecimal if isinstance(value, Decimal) else type(value)
    return FieldRecord[field_type](state=FieldState.FOUND, value=value, evidence=ev(status))


def ref(label="printed subtotal") -> EvidenceRef:
    return EvidenceRef(label=label, source_quote="Subtotal 5,664,000.00", page_number=1)


# ---------- enums ----------


def test_decision_has_exactly_three_values_and_no_error():
    assert {d.value for d in Decision} == {"APPROVE", "REJECT", "NEEDS_REVIEW"}


def test_rule_status_values():
    assert {s.value for s in RuleStatus} == {"PASS", "FAIL", "UNCERTAIN", "NOT_APPLICABLE"}


# ---------- field-state invariants (SPEC §6) ----------


def test_default_field_is_unknown():
    f = FieldRecord[str]()
    assert f.state == FieldState.UNKNOWN and f.value is None


def test_found_requires_value_evidence_and_quote():
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.FOUND, value=None, evidence=ev())
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.FOUND, value="x")  # no evidence
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.FOUND, value="x", evidence=ev(quote="   "))
    assert FieldRecord[str](state=FieldState.FOUND, value="x", evidence=ev()).value == "x"


def test_found_cannot_be_conflict_or_carry_absence():
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.FOUND, value="x", evidence=ev(VerificationStatus.CONFLICT))
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.FOUND, value="x", evidence=ev(), absence_evidence=absence())


def test_absent_requires_absence_evidence_and_no_value():
    assert FieldRecord[str](state=FieldState.ABSENT, absence_evidence=absence()).value is None
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.ABSENT)  # null alone never establishes ABSENT
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.ABSENT, value="x", absence_evidence=absence())
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.ABSENT, absence_evidence=absence(), evidence=ev())


def test_absent_requires_complete_extraction():
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.ABSENT, absence_evidence=absence(complete=False))


def test_unknown_must_have_cleared_value_and_no_absence():
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.UNKNOWN, value="x")
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.UNKNOWN, absence_evidence=absence())


def test_unknown_may_keep_unverified_or_conflict_evidence_for_audit_but_not_verified():
    for status in (VerificationStatus.UNVERIFIED, VerificationStatus.CONFLICT):
        f = FieldRecord[str](state=FieldState.UNKNOWN, evidence=ev(status, raw_value="479.50"))
        assert f.evidence.raw_value == "479.50"
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.UNKNOWN, evidence=ev(VerificationStatus.VERIFIED))


# ---------- LineItemField carries its own AbsenceEvidence ----------


def test_line_item_field_absent_accepts_and_requires_absence_evidence():
    ok = LineItemField[str](state=FieldState.ABSENT, absence_evidence=absence(field="line-2.unit"))
    assert ok.absence_evidence.field_searched == "line-2.unit"
    with pytest.raises(ValidationError):
        LineItemField[str](state=FieldState.ABSENT)


def test_line_item_has_per_field_records_and_unknown_matches_by_default():
    item = LineItem()
    assert item.quantity.state == FieldState.UNKNOWN
    assert (item.item_code_match, item.description_match, item.unit_match) == ("UNKNOWN",) * 3
    with pytest.raises(ValidationError):
        LineItem(unit_match="MAYBE")


# ---------- strict typing: no float ----------


@pytest.mark.parametrize("bad", [479.5, 15.0, 0.1])
def test_decimal_field_rejects_float(bad):
    with pytest.raises(ValidationError):
        FieldRecord[StrictDecimal](state=FieldState.FOUND, value=bad, evidence=ev())
    with pytest.raises(ValidationError):
        LineItemField[StrictDecimal](state=FieldState.FOUND, value=bad, evidence=ev())


@pytest.mark.parametrize("bad", [True, False, float("nan"), Decimal("NaN"), Decimal("Infinity"), "NaN"])
def test_decimal_field_rejects_bool_and_non_finite(bad):
    with pytest.raises(ValidationError):
        FieldRecord[StrictDecimal](state=FieldState.FOUND, value=bad, evidence=ev())


def test_decimal_field_accepts_str_int_decimal_exactly():
    s = FieldRecord[StrictDecimal](state=FieldState.FOUND, value="479.50", evidence=ev())
    assert s.value == Decimal("479.50") and isinstance(s.value, Decimal)
    assert str(s.value) == "479.50"  # trailing zero preserved, no float round trip
    assert FieldRecord[StrictDecimal](state=FieldState.FOUND, value=15, evidence=ev()).value == Decimal("15")
    assert FieldRecord[StrictDecimal](state=FieldState.FOUND, value=Decimal("0.5"), evidence=ev()).value == Decimal("0.5")


def test_int_field_rejects_float_and_bool():
    # StrictInt is the type ExtractedDocument uses for day counts (delivery_days, validity_days).
    for bad in (45.0, 45.5, True, False):
        with pytest.raises(ValidationError):
            FieldRecord[StrictInt](state=FieldState.FOUND, value=bad, evidence=ev())
        with pytest.raises(ValidationError):
            ExtractedDocument(delivery_days={"state": "FOUND", "value": bad, "evidence": ev().model_dump()})
    assert FieldRecord[StrictInt](state=FieldState.FOUND, value=45, evidence=ev()).value == 45
    assert FieldRecord[StrictInt](state=FieldState.FOUND, value="45", evidence=ev()).value == 45


def test_document_level_decimal_fields_reject_float():
    with pytest.raises(ValidationError):
        ExtractedDocument(vat_rate_pct={"state": "FOUND", "value": 15.0, "evidence": ev().model_dump()})
    with pytest.raises(ValidationError):
        ExtractedDocument(delivery_days={"state": "FOUND", "value": 24.0, "evidence": ev().model_dump()})


def test_decimal_typed_attributes_reject_float():
    with pytest.raises(ValidationError):
        PRItem(item_code="A", description="d", unit="metre", quantity_requested=12000.0, max_unit_price_bdt="480.00")
    with pytest.raises(ValidationError):
        PenaltyClause(state=FieldState.FOUND, original_wording="w", evidence=ev(), rate_pct=0.5)
    with pytest.raises(ValidationError):
        ArithmeticCheckResult(check_name="vat", status="UNKNOWN", detail="d", calculated=1.5)
    with pytest.raises(ValidationError):
        EvidenceRef(label="x", source_quote="q", page_number=1.0)


def test_no_float_annotation_anywhere_in_models_module():
    """AC-06: walk every model's field annotations; none may mention float."""

    def mentions_float(tp) -> bool:
        if tp is float:
            return True
        return any(mentions_float(a) for a in typing.get_args(tp))

    for _, cls in inspect.getmembers(models, inspect.isclass):
        if issubclass(cls, BaseModel) and cls.__module__ == models.__name__:
            for name, info in cls.model_fields.items():
                assert not mentions_float(info.annotation), f"{cls.__name__}.{name} uses float"


def test_percentage_points_are_stored_as_written():
    vat = FieldRecord[StrictDecimal](state=FieldState.FOUND, value="15", evidence=ev())
    assert vat.value == Decimal("15") and vat.value != Decimal("0.15")


# ---------- is_usable: candidate vs verified (SPEC §3.1 rule 5) ----------


def test_is_usable_only_for_found_and_verified():
    assert is_usable(found("x")) is True
    assert is_usable(found("x", VerificationStatus.UNVERIFIED)) is False  # candidate
    assert is_usable(FieldRecord[str]()) is False
    assert is_usable(FieldRecord[str](state=FieldState.ABSENT, absence_evidence=absence())) is False
    assert is_usable(LineItemField[str](state=FieldState.FOUND, value="A", evidence=ev())) is True
    assert is_usable(LineItemField[str](state=FieldState.FOUND, value="A", evidence=ev(VerificationStatus.UNVERIFIED))) is False


def test_is_usable_works_on_payment_and_penalty():
    pay = PaymentTerms(
        state=FieldState.FOUND, original_wording="within 30 days of delivery",
        min_days=30, max_days=30, anchor="delivery", discretionary=False, evidence=ev(),
    )
    assert is_usable(pay) is True
    cand = pay.model_copy(update={"evidence": ev(VerificationStatus.UNVERIFIED)})
    assert is_usable(cand) is False
    assert is_usable(PenaltyClause()) is False


# ---------- PaymentTerms: discretionary is never assumed False ----------


def test_payment_discretionary_defaults_to_unknown_not_false():
    assert PaymentTerms().discretionary is None
    assert PaymentTerms().discretionary_usable() is False


def test_payment_discretionary_usable_requires_verified_wording_and_a_determined_value():
    base = dict(
        state=FieldState.FOUND, original_wording="Payment within 30 days of delivery",
        min_days=30, max_days=30, anchor="delivery",
    )
    unknown_flag = PaymentTerms(**base, discretionary=None, evidence=ev())
    assert unknown_flag.discretionary_usable() is False  # verified wording, but flag not determined
    unverified = PaymentTerms(**base, discretionary=False, evidence=ev(VerificationStatus.UNVERIFIED))
    assert unverified.discretionary_usable() is False  # False is not trusted until verified
    verified = PaymentTerms(**base, discretionary=False, evidence=ev())
    assert verified.discretionary_usable() is True


def test_unknown_payment_cannot_carry_typed_values():
    for kw in ({"min_days": 30}, {"max_days": 60}, {"anchor": "delivery"}, {"discretionary": False}):
        with pytest.raises(ValidationError):
            PaymentTerms(state=FieldState.UNKNOWN, **kw)


def test_payment_range_is_kept_not_collapsed():
    p = PaymentTerms(
        state=FieldState.FOUND, original_wording="Payment within 30 to 90 days as agreed by both parties",
        min_days=30, max_days=90, anchor="other", discretionary=True, evidence=ev(),
    )
    assert (p.min_days, p.max_days) == (30, 90)


def test_payment_validation_errors():
    common = dict(state=FieldState.FOUND, original_wording="w", evidence=ev())
    with pytest.raises(ValidationError):
        PaymentTerms(**common, min_days=30)  # only one bound
    with pytest.raises(ValidationError):
        PaymentTerms(**common, min_days=60, max_days=30)  # min > max
    with pytest.raises(ValidationError):
        PaymentTerms(**common, min_days=30, max_days=30, anchor="after_lunch")  # unknown anchor
    with pytest.raises(ValidationError):
        PaymentTerms(state=FieldState.FOUND, original_wording="w")  # no evidence
    with pytest.raises(ValidationError):
        PaymentTerms(state=FieldState.ABSENT)  # unsupported: use UNKNOWN


# ---------- PenaltyClause ----------


def test_penalty_found_keeps_rate_as_stated_with_period():
    p = PenaltyClause(
        state=FieldState.FOUND, original_wording="Late delivery penalty of 0.6% of order value per week.",
        rate_pct="0.6", calculation_basis="order value", period_unit="week", evidence=ev(),
    )
    assert p.rate_pct == Decimal("0.6") and p.period_unit == "week"


def test_penalty_absent_requires_complete_absence_evidence_and_no_details():
    ok = PenaltyClause(state=FieldState.ABSENT, absence_evidence=absence(field="penalty_clause"))
    assert ok.state == FieldState.ABSENT
    with pytest.raises(ValidationError):
        PenaltyClause(state=FieldState.ABSENT)
    with pytest.raises(ValidationError):
        PenaltyClause(state=FieldState.ABSENT, absence_evidence=absence(complete=False))
    with pytest.raises(ValidationError):
        PenaltyClause(state=FieldState.ABSENT, absence_evidence=absence(), rate_pct="0.5")


def test_penalty_absent_must_not_carry_original_wording_but_unknown_may():
    with pytest.raises(ValidationError):
        PenaltyClause(
            state=FieldState.ABSENT,
            original_wording="Late delivery penalty of 0.5%",
            absence_evidence=absence(field="penalty_clause"),
        )
    # UNKNOWN keeps wording for audit
    assert PenaltyClause(state=FieldState.UNKNOWN, original_wording="unclear penalty text").original_wording


def test_penalty_unknown_has_no_details_and_period_must_be_known_literal():
    with pytest.raises(ValidationError):
        PenaltyClause(state=FieldState.UNKNOWN, rate_pct="0.5")
    with pytest.raises(ValidationError):
        PenaltyClause(state=FieldState.FOUND, original_wording="w", evidence=ev(), period_unit="fortnight")


# ---------- ExtractedDocument ----------


def test_empty_document_is_all_unknown():
    doc = ExtractedDocument()
    assert doc.line_items == []
    for name in ("supplier_name", "tin", "bin", "subtotal_bdt", "vat_rate_pct", "vat_shown_separately",
                 "delivery_days", "advance_payment_pct", "validity_days"):
        assert getattr(doc, name).state == FieldState.UNKNOWN
    assert doc.payment.state == doc.penalty.state == FieldState.UNKNOWN


def test_advance_payment_can_be_found_zero_or_unknown_but_never_absent():
    zero = FieldRecord[StrictDecimal](state=FieldState.FOUND, value="0", evidence=ev(quote="No advance payment required."))
    assert ExtractedDocument(advance_payment_pct=zero).advance_payment_pct.value == Decimal("0")
    with pytest.raises(ValidationError):
        ExtractedDocument(advance_payment_pct=FieldRecord[StrictDecimal](state=FieldState.ABSENT, absence_evidence=absence()))


def test_tin_and_vat_flag_may_be_absent_with_evidence():
    doc = ExtractedDocument(
        tin=FieldRecord[str](state=FieldState.ABSENT, absence_evidence=absence()),
        vat_shown_separately=FieldRecord[bool](state=FieldState.ABSENT, absence_evidence=absence(field="vat_shown_separately")),
    )
    assert doc.tin.absence_evidence.field_searched == "tin"


def test_extra_keys_are_forbidden_on_normalized_models():
    with pytest.raises(ValidationError):
        ExtractedDocument(decision="APPROVE")


# ---------- RuleResult traceability (SPEC §5.6, §7.3) ----------


def rr(**kw) -> RuleResult:
    base = dict(rule_id="R-05", description="Delivery > 45 days", status=RuleStatus.PASS,
                severity=RuleSeverity.REJECT, detail="24 days <= 45")
    base.update(kw)
    return RuleResult(**base)


def test_rule_result_accepts_multiple_evidence_refs():
    r = rr(rule_id="NR-06", status=RuleStatus.FAIL, severity=RuleSeverity.REVIEW,
           evidence_refs=[ref("line-1 printed total"), ref("printed grand total")])
    assert len(r.evidence_refs) == 2


def test_fail_without_evidence_or_absence_is_rejected():
    with pytest.raises(ValidationError):
        rr(status=RuleStatus.FAIL)


def test_fail_accepts_absence_evidence_alone():
    r = rr(rule_id="R-04", status=RuleStatus.FAIL, absence_evidence=[absence(field="tin"), absence(field="bin")])
    assert len(r.absence_evidence) == 2


def test_rule_result_never_rests_on_incomplete_absence():
    with pytest.raises(ValidationError):
        rr(status=RuleStatus.UNCERTAIN, absence_evidence=[absence(complete=False)])


def test_not_applicable_requires_reason_and_detail_must_be_non_empty():
    with pytest.raises(ValidationError):
        rr(rule_id="NR-08", status=RuleStatus.NOT_APPLICABLE)
    ok = rr(rule_id="NR-08", status=RuleStatus.NOT_APPLICABLE, not_applicable_reason="penalty clause absent; see R-03")
    assert ok.not_applicable_reason
    with pytest.raises(ValidationError):
        rr(detail="  ")


def test_item_id_format():
    assert rr(item_id="line-1").item_id == "line-1"
    for bad in ("line-0", "line-", "item-1", "A"):
        with pytest.raises(ValidationError):
            rr(item_id=bad)


# ---------- ArithmeticCheckResult ----------


def test_arithmetic_result_pass_fail_need_values_and_evidence():
    ok = ArithmeticCheckResult(check_name="independent_subtotal", status="PASS", calculated="5664000.00",
                               printed="5664000.00", detail="match", evidence_refs=[ref(), ref("line-1 quantity")])
    assert ok.calculated == Decimal("5664000.00") and len(ok.evidence_refs) == 2
    with pytest.raises(ValidationError):
        ArithmeticCheckResult(check_name="vat", status="FAIL", calculated="1", detail="d", evidence_refs=[ref()])
    with pytest.raises(ValidationError):
        ArithmeticCheckResult(check_name="vat", status="PASS", calculated="1", printed="1", detail="d")  # no refs


def test_arithmetic_unknown_needs_no_values():
    r = ArithmeticCheckResult(check_name="independent_subtotal", status="UNKNOWN",
                              detail="line-2 quantity is UNKNOWN")
    assert r.calculated is None and r.item_id is None
    assert ArithmeticCheckResult(check_name="line_recalc", item_id="line-1", status="UNKNOWN", detail="d").item_id == "line-1"


# ---------- PackResult ----------


def test_pack_result_ok():
    p = PackResult(pack_filename="supplier-pack-01.pdf", decision=Decision.NEEDS_REVIEW, reason="UNCERTAIN: NR-03")
    assert p.processing_status == "OK" and p.rule_results == []


def test_pack_result_error_requires_detail_and_needs_review():
    base = dict(pack_filename="supplier-pack-03.pdf", reason="Processing error (pdf_unreadable)")
    with pytest.raises(ValidationError):
        PackResult(**base, processing_status="ERROR: pdf_unreadable", decision=Decision.NEEDS_REVIEW)
    with pytest.raises(ValidationError):
        PackResult(**base, processing_status="ERROR: pdf_unreadable", processing_error_detail="x", decision=Decision.APPROVE)
    with pytest.raises(ValidationError):
        PackResult(**base, processing_status="broken", processing_error_detail="x", decision=Decision.NEEDS_REVIEW)
    ok = PackResult(**base, processing_status="ERROR: pdf_unreadable", processing_error_detail="PDFReadError: boom",
                    decision=Decision.NEEDS_REVIEW)
    assert ok.extracted is None and ok.rule_results == []


def test_decision_cannot_be_error():
    with pytest.raises(ValidationError):
        PackResult(pack_filename="x.pdf", decision="ERROR", reason="r")


# ---------- PurchaseRequest ----------


def test_purchase_request_keys_must_match_codes_and_decimals_are_exact():
    item = PRItem(item_code="A", description="Cotton twill fabric, 280 gsm", unit="metre",
                  quantity_requested="12000", max_unit_price_bdt="480.00")
    pr = PurchaseRequest(pr_number="PR-2026-114", items={"A": item})
    assert pr.items["A"].max_unit_price_bdt == Decimal("480.00")
    with pytest.raises(ValidationError):
        PurchaseRequest(pr_number="PR-2026-114", items={"B": item})


# ---------- raw layer ----------


def test_raw_extraction_defaults_to_empty_fields():
    raw = RawExtraction()
    assert raw.supplier_name.value is None and raw.line_items == []


def test_raw_extraction_forbids_a_decision_key():
    with pytest.raises(ValidationError):
        RawExtraction.model_validate({"decision": "APPROVE"})
    with pytest.raises(ValidationError):
        RawExtraction.model_validate_json('{"supplier_name": {"value": "x"}, "decision": "APPROVE"}')
    with pytest.raises(ValidationError):
        RawField.model_validate({"value": "x", "decision": "APPROVE"})


def test_raw_values_are_strings_and_numbers_are_not_silently_coerced():
    ok = RawField.model_validate_json('{"value": "479.50", "source_quote": "q", "page_number": 1}')
    assert ok.value == "479.50" and ok.page_number == 1
    with pytest.raises(ValidationError):
        RawField.model_validate_json('{"value": 479.5}')  # model must return strings, not JSON numbers
    with pytest.raises(ValidationError):
        RawField.model_validate_json('{"value": "1", "page_number": 1.0}')


def test_raw_extraction_parses_line_items_as_strings():
    raw = RawExtraction.model_validate_json(
        '{"line_items": [{"item_code": {"value": "A", "source_quote": "A Cotton", "page_number": 1},'
        ' "unit_price_bdt": {"value": "472.00", "source_quote": "472.00", "page_number": 1}}]}'
    )
    assert raw.line_items[0].unit_price_bdt.value == "472.00"
    assert raw.line_items[0].quantity.value is None


def test_normalized_document_round_trips_through_json_without_float():
    doc = ExtractedDocument(vat_rate_pct=found(Decimal("15")), subtotal_bdt=found(Decimal("5664000.00")))
    again = ExtractedDocument.model_validate_json(doc.model_dump_json())
    assert again.subtotal_bdt.value == Decimal("5664000.00")
    assert str(again.subtotal_bdt.value) == "5664000.00"


# ---------- M0 review: AbsenceEvidence must describe a real search ----------


def absence_kwargs(**overrides):
    base = dict(
        field_searched="tin", search_terms=["TIN"], pages_scanned=[1], extraction_complete=True,
        contextual_excerpt="Supplier registration details available on request.",
        determination_basis="searched every page for TIN synonyms",
    )
    base.update(overrides)
    return base


def test_empty_absence_evidence_is_rejected():
    with pytest.raises(ValidationError):
        AbsenceEvidence(field_searched="", search_terms=[], pages_scanned=[], extraction_complete=True,
                        contextual_excerpt=None, determination_basis="")


@pytest.mark.parametrize(
    "bad",
    [
        {"field_searched": "  "},
        {"search_terms": []},
        {"search_terms": ["TIN", "  "]},
        {"pages_scanned": []},
        {"pages_scanned": [0]},
        {"pages_scanned": [1, -2]},
        {"determination_basis": ""},
        {"determination_basis": "   "},
        {"contextual_excerpt": "   "},  # None is allowed (SPEC §7.2 "if any"); blank text is not
    ],
)
def test_absence_evidence_rejects_meaningless_content(bad):
    with pytest.raises(ValidationError):
        AbsenceEvidence(**absence_kwargs(**bad))


def test_absence_evidence_accepts_a_real_search_with_or_without_excerpt():
    assert AbsenceEvidence(**absence_kwargs()).pages_scanned == [1]
    assert AbsenceEvidence(**absence_kwargs(contextual_excerpt=None)).contextual_excerpt is None


def test_empty_absence_evidence_cannot_establish_absent_or_support_a_fail():
    with pytest.raises(ValidationError):
        AbsenceEvidence(**absence_kwargs(search_terms=[], pages_scanned=[], determination_basis=""))
    # so no field or rule result can be built on it
    with pytest.raises(ValidationError):
        FieldRecord[str](state=FieldState.ABSENT, absence_evidence={
            "field_searched": "", "search_terms": [], "pages_scanned": [],
            "extraction_complete": True, "determination_basis": ""})


# ---------- M0 review: EvidenceRef / Evidence content ----------


@pytest.mark.parametrize(
    "kw",
    [
        {"label": "", "source_quote": "q"},
        {"label": "   ", "source_quote": "q"},
        {"label": "printed subtotal", "source_quote": ""},
        {"label": "printed subtotal", "source_quote": "   "},
        {"label": "printed subtotal", "source_quote": "q", "page_number": 0},
        {"label": "printed subtotal", "source_quote": "q", "page_number": -1},
    ],
)
def test_evidence_ref_rejects_structurally_empty_references(kw):
    with pytest.raises(ValidationError):
        EvidenceRef(**kw)


def test_empty_evidence_ref_cannot_satisfy_traceability():
    with pytest.raises(ValidationError):
        EvidenceRef(label="", source_quote="   ", page_number=1)
    # the only way to build a FAIL is with a real reference
    assert rr(status=RuleStatus.FAIL, evidence_refs=[ref()]).status == RuleStatus.FAIL
    with pytest.raises(ValidationError):
        ArithmeticCheckResult(check_name="vat", status="FAIL", calculated="1", printed="2", detail="d",
                              evidence_refs=[{"label": "", "source_quote": " ", "page_number": 1}])


def test_evidence_page_number_must_be_positive():
    with pytest.raises(ValidationError):
        Evidence(source_quote="q", page_number=0)
    assert Evidence(source_quote="q", page_number=2).page_number == 2
    assert Evidence(source_quote="q").page_number is None


# ---------- M0 review: numeric input contracts match their comments ----------


@pytest.mark.parametrize("bad", [Decimal("45"), "45.0", "45.5", "forty-five", "", "4 5", [45], None, 45.0, True])
def test_strict_int_rejects_everything_but_int_and_integer_strings(bad):
    with pytest.raises(ValidationError):
        FieldRecord[StrictInt](state=FieldState.FOUND, value=bad, evidence=ev())
    if bad is not None:  # page_number is Optional, so None is legal there (and only there)
        with pytest.raises(ValidationError):
            EvidenceRef(label="x", source_quote="q", page_number=bad)


@pytest.mark.parametrize("good, expected", [(45, 45), ("45", 45), (" 45 ", 45), ("-3", -3), ("+7", 7)])
def test_strict_int_accepts_int_and_integer_strings(good, expected):
    assert FieldRecord[StrictInt](state=FieldState.FOUND, value=good, evidence=ev()).value == expected


@pytest.mark.parametrize("bad", [[1], {"a": 1}, (1,), b"1", 1.5, True])
def test_strict_decimal_rejects_other_types(bad):
    with pytest.raises(ValidationError):
        PRItem(item_code="A", description="d", unit="metre", quantity_requested=bad, max_unit_price_bdt="1")


def test_strict_decimal_still_accepts_str_int_decimal():
    for good in ("12000", 12000, Decimal("12000")):
        item = PRItem(item_code="A", description="d", unit="metre", quantity_requested=good, max_unit_price_bdt="480.00")
        assert item.quantity_requested == Decimal("12000")


# ---------- M0 review: is_usable on composites only vouches for the clause wording ----------


def test_is_usable_on_composites_does_not_imply_every_component_is_known():
    """Documents the caution in is_usable(): the rule engine must check each component it needs."""
    pay = PaymentTerms(state=FieldState.FOUND, original_wording="Payment as agreed", evidence=ev())
    assert is_usable(pay) is True  # wording verified ...
    assert (pay.min_days, pay.max_days, pay.anchor, pay.discretionary) == (None,) * 4  # ... components unknown
    assert pay.discretionary_usable() is False
    clause = PenaltyClause(state=FieldState.FOUND, original_wording="A penalty applies for late delivery.", evidence=ev())
    assert is_usable(clause) is True
    assert clause.rate_pct is None and clause.period_unit is None


# ---------- M0 review round 2 ----------


@pytest.mark.parametrize("page", [0, -1, "0", "-2"])
def test_raw_field_rejects_non_positive_page_numbers(page):
    with pytest.raises(ValidationError):
        RawField(value="480.00", source_quote="Unit price 480.00", page_number=page)
    with pytest.raises(ValidationError):
        RawExtraction.model_validate({"supplier_name": {"value": "x", "page_number": page}})


def test_raw_field_accepts_positive_or_missing_page():
    assert RawField(value="480.00", source_quote="q", page_number=1).page_number == 1
    assert RawField(value="480.00").page_number is None
    assert RawField(value="480.00", page_number="3").page_number == 3


@pytest.mark.parametrize("name", ["delivery_days", "validity_days"])
def test_document_periods_must_not_be_negative(name):
    neg = FieldRecord[StrictInt](state=FieldState.FOUND, value="-3", evidence=ev())  # StrictInt itself allows it
    assert neg.value == -3
    with pytest.raises(ValidationError):
        ExtractedDocument(**{name: neg})
    for ok in (0, 24, 45):
        good = FieldRecord[StrictInt](state=FieldState.FOUND, value=ok, evidence=ev())
        assert getattr(ExtractedDocument(**{name: good}), name).value == ok


def test_unknown_period_is_still_fine():
    assert ExtractedDocument().delivery_days.value is None


def test_pack_result_ok_must_not_carry_error_detail():
    with pytest.raises(ValidationError):
        PackResult(pack_filename="x.pdf", decision=Decision.APPROVE, reason="r", processing_error_detail="stale")
    with pytest.raises(ValidationError):
        PackResult(pack_filename="x.pdf", decision=Decision.NEEDS_REVIEW, reason="r", processing_error_detail="")


@pytest.mark.parametrize("status", ["ERROR: ", "ERROR:   ", "ERROR:pdf_unreadable", "error: pdf_unreadable", "FAILED"])
def test_pack_result_error_status_needs_a_real_code(status):
    with pytest.raises(ValidationError):
        PackResult(pack_filename="x.pdf", processing_status=status, processing_error_detail="boom",
                   decision=Decision.NEEDS_REVIEW, reason="r")


def test_pack_result_error_cannot_carry_pipeline_output():
    base = dict(pack_filename="x.pdf", processing_status="ERROR: unexpected_exception",
                processing_error_detail="RuntimeError: boom", decision=Decision.NEEDS_REVIEW, reason="r")
    assert PackResult(**base).extracted is None
    with pytest.raises(ValidationError):
        PackResult(**base, extracted=ExtractedDocument())
    with pytest.raises(ValidationError):
        PackResult(**base, rule_results=[rr()])
    with pytest.raises(ValidationError):
        PackResult(**base, arithmetic_checks=[ArithmeticCheckResult(check_name="vat", status="UNKNOWN", detail="d")])
    with pytest.raises(ValidationError):
        PackResult(**base, assumptions_applied=["AMB-12"])


def test_ok_pack_result_may_carry_pipeline_output():
    p = PackResult(pack_filename="x.pdf", decision=Decision.NEEDS_REVIEW, reason="r",
                   extracted=ExtractedDocument(), rule_results=[rr()], assumptions_applied=["AMB-12"])
    assert len(p.rule_results) == 1


@pytest.mark.parametrize("quote", ["", "   ", "\n\t"])
def test_evidence_requires_a_real_quote_even_for_audit_records(quote):
    with pytest.raises(ValidationError):
        Evidence(source_quote=quote)
    with pytest.raises(ValidationError):  # a downgraded UNKNOWN field cannot keep a blank-quote Evidence
        FieldRecord[str](state=FieldState.UNKNOWN, evidence={"source_quote": quote, "verification_status": "UNVERIFIED"})
    assert Evidence(source_quote="Unit price 480.00").source_quote


# ---------- production-type checks: nesting, JSON exactness ----------


def test_document_and_line_item_decimal_fields_are_the_strict_type():
    """FieldRecord[StrictDecimal] must work nested in LineItem / ExtractedDocument, with the same strictness."""
    item = LineItem(
        quantity=LineItemField[StrictDecimal](state=FieldState.FOUND, value="12,000".replace(",", ""), evidence=ev()),
        unit_price_bdt=LineItemField[StrictDecimal](state=FieldState.FOUND, value="472.00", evidence=ev()),
    )
    assert item.unit_price_bdt.value == Decimal("472.00") and str(item.unit_price_bdt.value) == "472.00"
    for bad in (True, 472.0, Decimal("NaN")):
        with pytest.raises(ValidationError):
            LineItem(unit_price_bdt={"state": "FOUND", "value": bad, "evidence": ev().model_dump()})
        with pytest.raises(ValidationError):
            ExtractedDocument(subtotal_bdt={"state": "FOUND", "value": bad, "evidence": ev().model_dump()})
        with pytest.raises(ValidationError):
            ExtractedDocument(
                line_items=[{"unit_price_bdt": {"state": "FOUND", "value": bad, "evidence": ev().model_dump()}}])


def test_json_round_trip_keeps_exact_decimals_in_nested_models():
    doc = ExtractedDocument(
        line_items=[LineItem(unit_price_bdt=LineItemField[StrictDecimal](
            state=FieldState.FOUND, value="6.50", evidence=ev()))],
        advance_payment_pct=found(Decimal("0")),
        subtotal_bdt=found(Decimal("5664000.00")),
    )
    again = ExtractedDocument.model_validate_json(doc.model_dump_json())
    assert again == doc
    assert str(again.line_items[0].unit_price_bdt.value) == "6.50"
    assert str(again.subtotal_bdt.value) == "5664000.00"
    assert again.advance_payment_pct.value == Decimal("0")
