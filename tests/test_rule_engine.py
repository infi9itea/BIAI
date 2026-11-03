"""Tests for src/rule_engine.py (Milestone 1; SPEC §5, §5.6, §7.3, §8.4, §10, §13).

Organised by rule. Inputs are typed models built in tests/builders.py; no test passes raw strings to the
engine. These tests prove the deterministic engine on valid typed inputs. They say nothing about extraction
accuracy or results on the supplied packs.
"""

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from src import rule_engine
from src.arithmetic import run_arithmetic_checks
from src.models import (
    ArithmeticCheckResult,
    Decision,
    EvidenceRef,
    FieldRecord,
    FieldState,
    PaymentTerms,
    PenaltyClause,
    RuleSeverity,
    RuleStatus,
)
from src.rule_engine import (
    GLOBAL_RULES,
    PER_ITEM_RULES,
    RULE_TEXT,
    aggregate_decision,
    build_rule_result,
    collect_assumptions,
    evaluate_all_rules,
)
from tests.builders import (
    D, UNVERIFIED, absence, absent, fld, good_payment, good_penalty, line, make_doc, make_pr, ev, unknown,
)

PASS, FAIL, UNCERTAIN, NA = RuleStatus.PASS, RuleStatus.FAIL, RuleStatus.UNCERTAIN, RuleStatus.NOT_APPLICABLE
PR = make_pr()
ALL_IDS = [*PER_ITEM_RULES, *GLOBAL_RULES]


def run(doc, pr=PR):
    return evaluate_all_rules(doc, pr, run_arithmetic_checks(doc))


def get(results, rule_id, item_id=None):
    matches = [r for r in results if r.rule_id == rule_id and r.item_id == item_id]
    assert len(matches) == 1, f"expected exactly one {rule_id} {item_id}, got {len(matches)}"
    return matches[0]


def st(results, rule_id, item_id=None):
    return get(results, rule_id, item_id).status


def decide(doc):
    return aggregate_decision(run(doc))[0]


def priced(price, qty="12000", **line_kw):
    return make_doc([line(price=price, qty=qty, **line_kw)])


# =============================================================================================
# Step 2: the common result builder (M1 decision 2) and defensive evidence handling (decision 1)
# =============================================================================================


def test_builder_keeps_a_fail_supported_by_a_verified_evidence_ref():
    r = build_rule_result("R-05", FAIL, "d", evidence_refs=[EvidenceRef(label="x", source_quote="q", page_number=1)])
    assert r.status == FAIL


def test_builder_downgrades_a_fail_with_no_evidence_instead_of_raising():
    r = build_rule_result("R-05", FAIL, "delivery too long")
    assert r.status == UNCERTAIN and "downgraded from FAIL" in r.detail


def test_builder_keeps_a_fail_supported_by_complete_absence_evidence():
    r = build_rule_result("R-04", FAIL, "TIN missing", absence_evidence=[absence("tin")])
    assert r.status == FAIL and len(r.absence_evidence) == 1


def test_builder_never_attaches_incomplete_absence_evidence_and_downgrades_the_fail():
    r = build_rule_result("R-04", FAIL, "TIN missing", absence_evidence=[absence("tin", complete=False)])
    assert r.status == UNCERTAIN and r.absence_evidence == []


def test_builder_mixed_absence_keeps_only_the_complete_record():
    r = build_rule_result("R-04", FAIL, "x", absence_evidence=[absence("tin", complete=False), absence("bin")])
    assert r.status == FAIL and [a.field_searched for a in r.absence_evidence] == ["bin"]


def test_builder_drops_incomplete_absence_on_a_non_fail_result_too():
    r = build_rule_result("R-04", UNCERTAIN, "x", absence_evidence=[absence("tin", complete=False)])
    assert r.status == UNCERTAIN and r.absence_evidence == []


def test_builder_not_applicable_carries_its_reason():
    r = build_rule_result("NR-08", NA, "d", not_applicable_reason="no clause")
    assert r.status == NA and r.not_applicable_reason == "no clause"


def test_builder_severity_comes_from_the_rule_id_and_description_from_the_rule_text():
    assert build_rule_result("R-02", UNCERTAIN, "d").severity == RuleSeverity.REJECT
    assert build_rule_result("NR-02", UNCERTAIN, "d").severity == RuleSeverity.REVIEW
    assert build_rule_result("R-03", UNCERTAIN, "d").description == RULE_TEXT["R-03"]


def test_builder_deduplicates_refs_and_assumptions():
    ref = EvidenceRef(label="x", source_quote="q", page_number=1)
    r = build_rule_result("R-05", PASS, "d", evidence_refs=[ref, ref], assumptions=["AMB-12", "AMB-12"])
    assert len(r.evidence_refs) == 1 and r.assumptions_applied == ["AMB-12"]


def test_rule_text_covers_all_thirteen_rules():
    assert set(RULE_TEXT) == set(ALL_IDS) and len(ALL_IDS) == 13


def _construct_absent(cls, inc):
    """Bypass validation (test-only): an ABSENT record whose search was incomplete. M0 rightly forbids it."""
    return cls.model_construct(state=FieldState.ABSENT, value=None, evidence=None, absence_evidence=inc)


def _no_incomplete_absence_anywhere(results):
    return all(a.extraction_complete for r in results for a in r.absence_evidence)


def test_defensive_incomplete_absent_tin_never_causes_a_definite_fail():
    inc = absence("tin", complete=False)
    doc = make_doc().model_copy(update={"tin": _construct_absent(FieldRecord[str], inc)})
    results = run(doc)
    r04 = get(results, "R-04")
    assert r04.status == UNCERTAIN and r04.absence_evidence == []
    assert _no_incomplete_absence_anywhere(results) and aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_defensive_only_the_complete_absence_record_supports_an_r04_fail():
    inc = absence("tin", complete=False)
    doc = make_doc(bin=absent("str", "bin")).model_copy(update={"tin": _construct_absent(FieldRecord[str], inc)})
    r04 = get(run(doc), "R-04")
    assert r04.status == FAIL and [a.field_searched for a in r04.absence_evidence] == ["bin"]


def test_defensive_incomplete_absent_penalty_clause_is_uncertain_not_fail_and_not_not_applicable():
    inc = absence("penalty_clause", complete=False)
    doc = make_doc().model_copy(update={"penalty": PenaltyClause.model_construct(state=FieldState.ABSENT, absence_evidence=inc)})
    results = run(doc)
    assert st(results, "R-03") == UNCERTAIN and st(results, "NR-08") == UNCERTAIN
    assert _no_incomplete_absence_anywhere(results) and aggregate_decision(results)[0] != Decision.REJECT


def test_defensive_incomplete_absent_vat_shown_flag_is_uncertain_and_never_attached():
    inc = absence("vat_shown_separately", complete=False)
    doc = make_doc().model_copy(update={"vat_shown_separately": _construct_absent(FieldRecord[bool], inc)})
    results = run(doc)
    assert get(results, "NR-05").status == UNCERTAIN and _no_incomplete_absence_anywhere(results)


def test_defensive_incomplete_absence_does_not_hide_an_independent_definite_fail():
    inc = absence("vat_shown_separately", complete=False)
    doc = make_doc(vat_rate_pct=fld("dec", D("10"), "VAT 10%")).model_copy(
        update={"vat_shown_separately": _construct_absent(FieldRecord[bool], inc)})
    nr05 = get(run(doc), "NR-05")
    assert nr05.status == FAIL and nr05.absence_evidence == [] and nr05.evidence_refs  # FAIL rests on the rate quote


# =============================================================================================
# Cardinality, order, empty list (SPEC §5.6, §10)
# =============================================================================================


@pytest.mark.parametrize("k", [1, 2, 3])
def test_k_items_yield_3k_plus_10_results(k):
    assert len(run(make_doc([line() for _ in range(k)]))) == 3 * k + 10


def test_deterministic_order_per_item_rules_by_rule_then_line_then_globals_in_id_order():
    results = run(make_doc([line(), line()]))
    assert [(r.rule_id, r.item_id) for r in results] == [
        ("R-01", "line-1"), ("R-01", "line-2"), ("NR-01", "line-1"), ("NR-01", "line-2"),
        ("NR-02", "line-1"), ("NR-02", "line-2"),
        *[(rid, None) for rid in GLOBAL_RULES],
    ]
    assert list(GLOBAL_RULES) == ["R-02", "R-03", "R-04", "R-05", "NR-03", "NR-04", "NR-05", "NR-06", "NR-07", "NR-08"]


def test_two_items_have_distinct_item_ids_and_non_empty_details_naming_the_code():
    results = run(make_doc([line(), line(code="B", qty="20000", price="38.00", unit="piece")]))
    for rid in PER_ITEM_RULES:
        assert {r.item_id for r in results if r.rule_id == rid} == {"line-1", "line-2"}
    assert all(r.detail.strip() for r in results)
    assert "item A" in get(results, "R-01", "line-1").detail and "item B" in get(results, "R-01", "line-2").detail


def test_two_lines_with_the_same_item_code_still_get_distinct_item_ids():
    results = run(make_doc([line(), line()]))
    assert {r.item_id for r in results if r.rule_id == "R-01"} == {"line-1", "line-2"}


def test_unknown_item_code_detail_says_code_unknown():
    results = run(make_doc([line(code=None, code_match="UNKNOWN")]))
    assert "code unknown" in get(results, "R-01", "line-1").detail


def test_empty_item_list_gives_one_uncertain_none_result_per_per_item_rule_never_approve():
    results = run(make_doc([]))
    assert len(results) == 13
    for rid in PER_ITEM_RULES:
        r = get(results, rid, None)
        assert r.status == UNCERTAIN and "no line items could be extracted" in r.detail
    decision, _ = aggregate_decision(results)
    assert decision == Decision.NEEDS_REVIEW


def test_all_rules_are_always_evaluated_even_after_a_definite_reject():
    results = run(make_doc(delivery_days=fld("int", 50, "Delivery within 50 days"), advance_payment_pct=fld("dec", D("40"), "40% advance")))
    assert st(results, "R-05") == FAIL and st(results, "R-02") == FAIL
    assert {r.rule_id for r in results} == set(ALL_IDS)


def test_good_document_is_approved_with_thirteen_passing_results():
    results = run(make_doc())
    assert len(results) == 13 and all(r.status == PASS for r in results)
    assert aggregate_decision(results)[0] == Decision.APPROVE


# =============================================================================================
# R-01 / NR-01: unit price (ratio, per item, VAT basis precondition)
# =============================================================================================


@pytest.mark.parametrize("price,r01,nr01,decision", [
    ("480.00", PASS, PASS, Decision.APPROVE),            # exactly at PR maximum: no rule fires (AMB-02)
    ("472.00", PASS, PASS, Decision.APPROVE),            # below maximum
    ("480.01", PASS, FAIL, Decision.NEEDS_REVIEW),       # any excess is at least review
    ("494.40", PASS, FAIL, Decision.NEEDS_REVIEW),       # exactly 3% above: R-01 needs strictly more
    ("494.4048", FAIL, PASS, Decision.REJECT),           # 3.001% above
    ("494.88", FAIL, PASS, Decision.REJECT),             # 3.1%: ratio constant 0.03 vs 0.031 handled as a fraction
    ("600.00", FAIL, PASS, Decision.REJECT),
])
def test_price_boundaries(price, r01, nr01, decision):
    doc = priced(price)
    results = run(doc)
    assert st(results, "R-01", "line-1") == r01 and st(results, "NR-01", "line-1") == nr01
    assert aggregate_decision(results)[0] == decision


def test_price_fail_carries_the_verified_unit_price_quote_and_amb12():
    r = get(run(priced("494.4048")), "R-01", "line-1")
    assert r.evidence_refs and any(e.label == "line-1 unit price" for e in r.evidence_refs)
    assert "AMB-12" in r.assumptions_applied and "PR maximum" in r.detail


def test_price_decision_uses_cross_multiplication_not_a_rounded_ratio():
    # A price that is 3.00001% above the maximum would round to 0.0300 as a ratio but must still be > 3%.
    price = D("480") * D("1.0300001")
    assert st(run(priced(str(price))), "R-01", "line-1") == FAIL


@pytest.mark.parametrize("price", ["400.00", "480.00", "500.00"])
def test_vat_inclusive_price_is_never_compared(price):
    results = run(priced(price, vat_included=True))
    for rid in ("R-01", "NR-01"):
        r = get(results, rid, "line-1")
        assert r.status == UNCERTAIN
        assert price in r.detail and "480" in r.detail and "PR maximum VAT basis not stated" in r.detail
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_unknown_vat_inclusion_with_vat_shown_separately_is_compared_and_records_amb12():
    results = run(priced("494.4048", vat_included=None))
    r = get(results, "R-01", "line-1")
    assert r.status == FAIL and "AMB-12" in r.assumptions_applied
    assert "AMB-12" in collect_assumptions(results)


@pytest.mark.parametrize("shown", [fld("bool", False, "Total (all taxes included)"), unknown("bool"), absent("bool", "vat_shown_separately")])
def test_unknown_vat_inclusion_without_separate_vat_is_uncertain(shown):
    results = run(priced("494.4048", vat_included=None).model_copy(update={"vat_shown_separately": shown}))
    assert st(results, "R-01", "line-1") == UNCERTAIN and st(results, "NR-01", "line-1") == UNCERTAIN


def test_explicitly_vat_exclusive_price_is_compared_even_if_vat_is_not_shown_separately():
    doc = priced("494.4048", vat_included=False).model_copy(update={"vat_shown_separately": unknown("bool")})
    assert st(run(doc), "R-01", "line-1") == FAIL


@pytest.mark.parametrize("kwargs", [
    dict(unit_match="NO", unit="roll"), dict(unit_match="UNKNOWN"), dict(code_match="NO"), dict(code_match="UNKNOWN"),
    dict(code=None, code_match="UNKNOWN"), dict(code="Z", code_match="YES"),  # verified code that is not in the PR
])
def test_unmatched_item_or_unit_makes_all_per_item_rules_uncertain_never_not_applicable(kwargs):
    results = run(make_doc([line(**kwargs)]))
    for rid in PER_ITEM_RULES:
        assert st(results, rid, "line-1") == UNCERTAIN
    assert not any(r.status == NA for r in results if r.rule_id in PER_ITEM_RULES)
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


@pytest.mark.parametrize("bad_unit", [
    None,                                                                        # UNKNOWN
    fld("str", "metre", "metre", status=UNVERIFIED, line=True),                  # FOUND but UNVERIFIED
    absent("str", "line-1.unit", line=True),                                     # ABSENT
], ids=["unknown", "unverified", "absent"])
def test_unusable_unit_is_uncertain_even_when_unit_match_claims_yes(bad_unit):
    # unit_match is deliberately left as "YES": the engine must not rely on that flag being consistent.
    results = run(make_doc([line(unit=bad_unit, unit_match="YES")]))
    for rid in PER_ITEM_RULES:
        r = get(results, rid, "line-1")
        assert r.status == UNCERTAIN and "unit" in r.detail and "not usable" in r.detail
    decision, _ = aggregate_decision(results)
    assert decision == Decision.NEEDS_REVIEW  # never APPROVE


def test_usable_unit_with_unit_match_yes_still_compares_normally():
    assert [st(run(make_doc([line(unit="metre", unit_match="YES")])), rid, "line-1") for rid in PER_ITEM_RULES] == [PASS] * 3


@pytest.mark.parametrize("bad_code", [
    None,                                                                         # UNKNOWN
    fld("str", "A", "A", status=UNVERIFIED, line=True),                           # FOUND but UNVERIFIED
    absent("str", "line-1.item_code", line=True),                                 # ABSENT
], ids=["unknown", "unverified", "absent"])
def test_unusable_item_code_is_uncertain_even_when_item_code_match_claims_yes(bad_code):
    # Same class of gap as the unit: a derived match flag must never outrank the field it was derived from.
    results = run(make_doc([line(code=bad_code, code_match="YES")]))
    for rid in PER_ITEM_RULES:
        assert st(results, rid, "line-1") == UNCERTAIN
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_unit_mismatch_detail_names_both_units():
    r = get(run(make_doc([line(unit="roll", unit_match="NO")])), "R-01", "line-1")
    assert "roll" in r.detail and "metre" in r.detail


def test_item_code_lookup_is_case_insensitive():
    assert st(run(make_doc([line(code="a")])), "R-01", "line-1") == PASS


def test_description_match_is_informational_only():
    assert st(run(make_doc([line(description_match="NO")])), "R-01", "line-1") == PASS


def test_unusable_unit_price_makes_price_rules_uncertain_but_quantity_still_evaluated():
    for bad in (unknown("dec", line=True), absent("dec", "line-1.unit_price_bdt", line=True),
                fld("dec", D("1"), "unverified", status=UNVERIFIED, line=True)):
        results = run(make_doc([line(price=bad, total="5664000.00")]))
        assert st(results, "R-01", "line-1") == UNCERTAIN and st(results, "NR-01", "line-1") == UNCERTAIN
        assert st(results, "NR-02", "line-1") == PASS


def test_two_items_one_over_reject_threshold_one_at_max_rejects_and_names_line_1():
    doc = make_doc([line(price="494.4048"), line(code="B", qty="20000", price="38.00", unit="piece")])
    results = run(doc)
    assert st(results, "R-01", "line-1") == FAIL and st(results, "R-01", "line-2") == PASS
    decision, reason = aggregate_decision(results)
    assert decision == Decision.REJECT and "line-1" in reason and "line-2" not in reason


def test_two_items_line_2_unit_mismatch_leaves_line_1_unaffected_and_needs_review():
    doc = make_doc([line(), line(code="B", qty="20000", price="38.00", unit="roll", unit_match="NO")])
    results = run(doc)
    assert [st(results, rid, "line-1") for rid in PER_ITEM_RULES] == [PASS, PASS, PASS]
    assert [st(results, rid, "line-2") for rid in PER_ITEM_RULES] == [UNCERTAIN] * 3
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


# =============================================================================================
# NR-02: quantity (ratio, strictly more than 5%)
# =============================================================================================


@pytest.mark.parametrize("qty,expected", [
    ("12000", PASS), ("12600", PASS), ("11400", PASS), ("12601", FAIL), ("11399", FAIL), ("6000", FAIL),
])
def test_quantity_boundaries(qty, expected):
    assert st(run(priced("472.00", qty=qty)), "NR-02", "line-1") == expected


def test_quantity_fail_is_needs_review_and_carries_quantity_evidence():
    results = run(priced("472.00", qty="6000"))
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW
    assert get(results, "NR-02", "line-1").evidence_refs[0].label == "line-1 quantity"


def test_unusable_quantity_is_uncertain():
    for bad in (unknown("dec", line=True), absent("dec", "line-1.quantity", line=True),
                fld("dec", D("1"), "unverified", status=UNVERIFIED, line=True)):
        results = run(make_doc([line(qty=bad, total="5664000.00")]))
        assert st(results, "NR-02", "line-1") == UNCERTAIN and st(results, "R-01", "line-1") == PASS


# =============================================================================================
# R-02: advance payment (percentage points)
# =============================================================================================


@pytest.mark.parametrize("pct,expected", [("20", PASS), ("20.1", FAIL), ("21", FAIL), ("0", PASS), ("10", PASS), ("40", FAIL)])
def test_advance_boundaries(pct, expected):
    results = run(make_doc(advance_payment_pct=fld("dec", D(pct), f"{pct}% advance")))
    assert st(results, "R-02") == expected
    if expected == FAIL:
        assert aggregate_decision(results)[0] == Decision.REJECT and get(results, "R-02").evidence_refs


def test_advance_unknown_is_uncertain_with_amb05_and_needs_review():
    results = run(make_doc(advance_payment_pct=unknown("dec")))
    r = get(results, "R-02")
    assert r.status == UNCERTAIN and "AMB-05" in r.assumptions_applied
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_advance_unverified_is_treated_as_unknown():
    doc = make_doc(advance_payment_pct=fld("dec", D("90"), "90% advance", status=UNVERIFIED))
    assert st(run(doc), "R-02") == UNCERTAIN


# =============================================================================================
# R-03: penalty clause present / absent, and NR-08 not applicable
# =============================================================================================


def test_penalty_clause_present_passes_r03():
    assert st(run(make_doc()), "R-03") == PASS


def test_absent_penalty_clause_fails_r03_with_absence_evidence_and_nr08_not_applicable():
    clause = PenaltyClause(state=FieldState.ABSENT, absence_evidence=absence("penalty_clause"))
    results = run(make_doc(penalty=clause))
    r03, nr08 = get(results, "R-03"), get(results, "NR-08")
    assert r03.status == FAIL and r03.absence_evidence == [clause.absence_evidence]
    assert nr08.status == NA and "R-03" in nr08.not_applicable_reason
    assert aggregate_decision(results)[0] == Decision.REJECT


def test_unknown_or_unverified_penalty_clause_is_uncertain_for_r03_and_nr08():
    for clause in (PenaltyClause(), good_penalty().model_copy(update={"evidence": ev("x", status=UNVERIFIED)})):
        results = run(make_doc(penalty=clause))
        assert st(results, "R-03") == UNCERTAIN and st(results, "NR-08") == UNCERTAIN
        assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


# =============================================================================================
# R-04: TIN / BIN (M1 decision 9 table)
# =============================================================================================

TIN_BIN = {
    "found": lambda n: fld("str", f"{n}-1", f"{n}: {n}-1"),
    "unknown": lambda n: unknown("str"),
    "absent": lambda n: absent("str", n.lower()),
}


@pytest.mark.parametrize("tin,bin_,expected,n_absence", [
    ("absent", "unknown", FAIL, 1), ("unknown", "absent", FAIL, 1), ("absent", "absent", FAIL, 2),
    ("found", "absent", FAIL, 1), ("absent", "found", FAIL, 1),
    ("found", "unknown", UNCERTAIN, 0), ("unknown", "unknown", UNCERTAIN, 0), ("unknown", "found", UNCERTAIN, 0),
    ("found", "found", PASS, 0),
])
def test_r04_table(tin, bin_, expected, n_absence):
    r04 = get(run(make_doc(tin=TIN_BIN[tin]("TIN"), bin=TIN_BIN[bin_]("BIN"))), "R-04")
    assert r04.status == expected and len(r04.absence_evidence) == n_absence


def test_r04_fail_attaches_only_the_absence_records_that_establish_it():
    r04 = get(run(make_doc(tin=absent("str", "tin"), bin=fld("str", "x", "BIN: x"))), "R-04")
    assert [a.field_searched for a in r04.absence_evidence] == ["tin"] and r04.evidence_refs == []


def test_r04_fail_is_reject():
    assert decide(make_doc(tin=absent("str", "tin"))) == Decision.REJECT


def test_r04_unverified_tin_is_uncertain():
    doc = make_doc(tin=fld("str", "123", "TIN 123", status=UNVERIFIED))
    assert st(run(doc), "R-04") == UNCERTAIN


# =============================================================================================
# R-05 / NR-04: delivery days
# =============================================================================================


@pytest.mark.parametrize("days,r05,nr04,decision", [
    (24, PASS, PASS, Decision.APPROVE), (30, PASS, PASS, Decision.APPROVE), (31, PASS, FAIL, Decision.NEEDS_REVIEW),
    (45, PASS, FAIL, Decision.NEEDS_REVIEW), (46, FAIL, PASS, Decision.REJECT), (60, FAIL, PASS, Decision.REJECT),
])
def test_delivery_boundaries(days, r05, nr04, decision):
    results = run(make_doc(delivery_days=fld("int", days, f"Delivery within {days} days")))
    assert st(results, "R-05") == r05 and st(results, "NR-04") == nr04
    assert aggregate_decision(results)[0] == decision


def test_delivery_unknown_or_unverified_is_uncertain_for_both_rules():
    for bad in (unknown("int"), fld("int", 90, "Delivery within 90 days", status=UNVERIFIED)):
        results = run(make_doc(delivery_days=bad))
        assert st(results, "R-05") == UNCERTAIN and st(results, "NR-04") == UNCERTAIN
        assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW  # an uncertain Reject is never a REJECT


def test_four_weeks_normalized_to_28_days_is_just_a_typed_28():
    assert st(run(make_doc(delivery_days=fld("int", 28, "Delivery within 4 weeks of PO"))), "R-05") == PASS


# =============================================================================================
# NR-03: payment terms (SPEC §5.2 order; M1 decision 8)
# =============================================================================================


@pytest.mark.parametrize("lo,hi,expected", [
    (30, 30, PASS), (60, 60, PASS), (45, 45, PASS), (30, 60, PASS), (29, 29, FAIL), (61, 61, FAIL),
    (30, 90, FAIL), (0, 30, FAIL),
])
def test_payment_window_is_inclusive_on_both_bounds(lo, hi, expected):
    results = run(make_doc(payment=good_payment(lo, hi)))
    assert st(results, "NR-03") == expected
    assert aggregate_decision(results)[0] == (Decision.APPROVE if expected == PASS else Decision.NEEDS_REVIEW)


@pytest.mark.parametrize("anchor", ["acceptance", "invoice", "purchase_order", "other", None])
def test_any_anchor_other_than_delivery_is_uncertain_amb13(anchor):
    r = get(run(make_doc(payment=good_payment(45, anchor=anchor))), "NR-03")
    assert r.status == UNCERTAIN and "AMB-13" in r.assumptions_applied


def test_combined_delivery_and_acceptance_anchor_is_uncertain():
    pay = good_payment(45, anchor="other")
    assert decide(make_doc(payment=pay)) == Decision.NEEDS_REVIEW and st(run(make_doc(payment=pay)), "NR-03") == UNCERTAIN


def test_delivery_anchor_30_to_60_is_evaluated_normally():
    assert st(run(make_doc(payment=good_payment(30, 60, anchor="delivery"))), "NR-03") == PASS


def test_discretionary_payment_fails_with_the_verified_quote_even_with_max_90():
    r = get(run(make_doc(payment=good_payment(30, 90, discretionary=True))), "NR-03")
    assert r.status == FAIL and r.evidence_refs[0].source_quote == "Balance payable within 45 days after delivery."


def test_discretionary_true_fails_even_when_the_anchor_is_other_step1_precedes_anchor():
    assert st(run(make_doc(payment=good_payment(45, anchor="other", discretionary=True))), "NR-03") == FAIL


def test_discretionary_unknown_is_never_assumed_false_so_it_cannot_pass():
    results = run(make_doc(payment=good_payment(45, discretionary=None)))
    r = get(results, "NR-03")
    assert r.status == UNCERTAIN and "not assumed to be false" in r.detail
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


@pytest.mark.parametrize("lo,hi,expected", [
    (29, 29, FAIL), (61, 61, FAIL), (30, 90, FAIL), (0, 30, FAIL),   # known bounds outside 30..60: definite violation
    (45, 45, UNCERTAIN), (30, 30, UNCERTAIN), (60, 60, UNCERTAIN), (30, 60, UNCERTAIN),  # acceptable bounds: cannot PASS
])
def test_unknown_discretion_with_known_bounds_fails_only_on_a_definite_violation(lo, hi, expected):
    results = run(make_doc(payment=good_payment(lo, hi, discretionary=None)))
    r = get(results, "NR-03")
    assert r.status == expected
    if expected == FAIL:
        assert r.evidence_refs[0].source_quote == "Balance payable within 45 days after delivery."  # verified quote
    else:
        assert "not assumed to be false" in r.detail
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_unknown_discretion_with_non_delivery_anchor_or_unknown_bounds_stays_uncertain():
    for pay in (good_payment(29, 29, anchor="other", discretionary=None),   # anchor concern precedes the bounds
                good_payment(29, 29, anchor=None, discretionary=None),
                good_payment(None, None, discretionary=None)):               # bounds not established
        assert st(run(make_doc(payment=pay)), "NR-03") == UNCERTAIN


def test_known_acceptable_bounds_pass_only_when_discretion_is_known_false():
    assert st(run(make_doc(payment=good_payment(45, discretionary=False))), "NR-03") == PASS
    assert st(run(make_doc(payment=good_payment(45, discretionary=None))), "NR-03") == UNCERTAIN
    assert st(run(make_doc(payment=good_payment(45, discretionary=True))), "NR-03") == FAIL


def test_discretionary_false_continues_to_anchor_and_bounds():
    assert st(run(make_doc(payment=good_payment(29, discretionary=False))), "NR-03") == FAIL


def test_unknown_unverified_or_missing_bounds_payment_is_uncertain():
    cases = [
        PaymentTerms(),  # UNKNOWN clause
        good_payment(45).model_copy(update={"evidence": ev("x", status=UNVERIFIED)}),  # FOUND but unverified
        good_payment(None, None),  # verified clause, day bounds not established
    ]
    for pay in cases:
        assert st(run(make_doc(payment=pay)), "NR-03") == UNCERTAIN


# =============================================================================================
# NR-05: VAT presentation only (never recalculates)
# =============================================================================================


def test_vat_shown_separately_at_exactly_15_passes():
    assert st(run(make_doc()), "NR-05") == PASS


def test_printed_rate_10_consistent_arithmetic_is_nr05_fail_but_nr06_pass_needs_review():
    doc = make_doc(
        vat_rate_pct=fld("dec", D("10"), "VAT 10%"), vat_amount_bdt=fld("dec", D("566400.00"), "VAT 10% 566,400.00"),
        grand_total_bdt=fld("dec", D("6230400.00"), "Total 6,230,400.00"),
    )
    results = run(doc)
    assert st(results, "NR-05") == FAIL and st(results, "NR-06") == PASS
    assert get(results, "NR-05").evidence_refs and aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_vat_included_wording_is_found_false_and_fails_with_evidence_not_absence():
    doc = make_doc(vat_shown_separately=fld("bool", False, "Total (all taxes included)"))
    r = get(run(doc), "NR-05")
    assert r.status == FAIL and r.absence_evidence == []
    assert any("all taxes included" in e.source_quote for e in r.evidence_refs)


def test_vat_shown_flag_absent_with_complete_search_fails_with_absence_evidence():
    r = get(run(make_doc(vat_shown_separately=absent("bool", "vat_shown_separately"))), "NR-05")
    assert r.status == FAIL and len(r.absence_evidence) == 1 and r.evidence_refs == []


def test_vat_rate_absent_with_complete_search_fails_with_absence_evidence():
    r = get(run(make_doc(vat_rate_pct=absent("dec", "vat_rate_pct"))), "NR-05")
    assert r.status == FAIL and r.absence_evidence[0].field_searched == "vat_rate_pct"


def test_vat_shown_true_with_unknown_rate_is_uncertain():
    results = run(make_doc(vat_rate_pct=unknown("dec")))
    assert st(results, "NR-05") == UNCERTAIN and aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_vat_both_unknown_is_uncertain_and_unverified_values_are_unknown():
    assert st(run(make_doc(vat_shown_separately=unknown("bool"), vat_rate_pct=unknown("dec"))), "NR-05") == UNCERTAIN
    doc = make_doc(vat_rate_pct=fld("dec", D("10"), "VAT 10%", status=UNVERIFIED))
    assert st(run(doc), "NR-05") == UNCERTAIN


def test_vat_not_shown_separately_fails_even_when_rate_is_unknown():
    doc = make_doc(vat_shown_separately=fld("bool", False, "VAT included"), vat_rate_pct=unknown("dec"))
    assert st(run(doc), "NR-05") == FAIL


def test_nr05_never_recalculates_so_wrong_arithmetic_does_not_change_it():
    results = run(make_doc(grand_total_bdt=fld("dec", D("1.00"), "Total 1.00")))
    assert st(results, "NR-05") == PASS and st(results, "NR-06") == FAIL


# =============================================================================================
# NR-06: consumes ArithmeticCheckResult objects, never recomputes
# =============================================================================================


def chk(name, status, item_id=None, label=None):
    refs = [EvidenceRef(label=label or f"{name} ref", source_quote=f"quote for {name}", page_number=1)]
    kw = dict(calculated=D("1"), printed=D("2")) if status != "UNKNOWN" else {}
    return ArithmeticCheckResult(check_name=name, item_id=item_id, status=status, detail=f"{name} {status}",
                                 evidence_refs=refs if status != "UNKNOWN" else [], **kw)


def test_nr06_single_fail_keeps_that_checks_refs():
    r = rule_engine._nr06([chk("line_recalc", "FAIL", "line-1"), chk("vat", "PASS")])
    assert r.status == FAIL and [e.label for e in r.evidence_refs] == ["line_recalc ref"]


def test_nr06_two_fails_keep_the_refs_of_both():
    r = rule_engine._nr06([chk("line_recalc", "FAIL", "line-1"), chk("vat", "PASS"), chk("grand", "FAIL")])
    assert r.status == FAIL and {e.label for e in r.evidence_refs} == {"line_recalc ref", "grand ref"}
    assert "grand" in r.detail and "line_recalc[line-1]" in r.detail


def test_nr06_unknown_without_fail_is_uncertain_and_fail_beats_unknown():
    assert rule_engine._nr06([chk("vat", "UNKNOWN"), chk("grand", "PASS")]).status == UNCERTAIN
    assert rule_engine._nr06([chk("vat", "UNKNOWN"), chk("grand", "FAIL")]).status == FAIL


def test_nr06_all_pass_passes_and_empty_list_is_uncertain_never_pass():
    assert rule_engine._nr06([chk("vat", "PASS"), chk("grand", "PASS")]).status == PASS
    empty = evaluate_all_rules(make_doc(), PR, [])
    assert get(empty, "NR-06").status == UNCERTAIN
    assert aggregate_decision(empty)[0] == Decision.NEEDS_REVIEW


def test_nr06_trusts_the_supplied_checks_and_never_recomputes():
    wrong_doc = make_doc(grand_total_bdt=fld("dec", D("1.00"), "Total 1.00"))
    forged_pass = [chk("grand", "PASS")]
    assert get(evaluate_all_rules(wrong_doc, PR, forged_pass), "NR-06").status == PASS


def test_nr06_end_to_end_line_total_error_is_review():
    results = run(make_doc([line(total="5664001.00")]))
    assert st(results, "NR-06") == FAIL and aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_nr06_end_to_end_unknown_arithmetic_is_uncertain():
    assert st(run(make_doc(vat_price_basis=unknown("str"))), "NR-06") == UNCERTAIN


# =============================================================================================
# NR-07: quotation validity
# =============================================================================================


@pytest.mark.parametrize("days,expected", [(30, PASS), (29, FAIL), (45, PASS), (7, FAIL)])
def test_validity_boundaries(days, expected):
    assert st(run(make_doc(validity_days=fld("int", days, f"valid for {days} days"))), "NR-07") == expected


def test_validity_unknown_or_unverified_is_uncertain():
    for bad in (unknown("int"), fld("int", 5, "valid for 5 days", status=UNVERIFIED)):
        assert st(run(make_doc(validity_days=bad)), "NR-07") == UNCERTAIN


# =============================================================================================
# NR-08: penalty rate (per week only, canonical basis)
# =============================================================================================


@pytest.mark.parametrize("rate,expected", [("0.5", PASS), ("0.4", FAIL), ("0.499", FAIL), ("1", PASS), ("0.75", PASS)])
def test_penalty_rate_boundaries_per_week(rate, expected):
    results = run(make_doc(penalty=good_penalty(rate)))
    assert st(results, "NR-08") == expected and st(results, "R-03") == PASS
    if expected == FAIL:
        assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_penalty_rate_unknown_is_uncertain():
    assert st(run(make_doc(penalty=good_penalty(None))), "NR-08") == UNCERTAIN


@pytest.mark.parametrize("period", ["day", "other", None])
@pytest.mark.parametrize("rate", ["0.1", "2"])
def test_non_week_or_unknown_period_is_uncertain_no_conversion_amb14(period, rate):
    results = run(make_doc(penalty=good_penalty(rate, period=period)))
    r = get(results, "NR-08")
    assert r.status == UNCERTAIN and "AMB-14" in r.assumptions_applied
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


@pytest.mark.parametrize("basis", [None, "contract value", "", "unit price"])
def test_unclear_or_non_canonical_basis_is_uncertain_even_for_a_clearly_low_rate(basis):
    r = get(run(make_doc(penalty=good_penalty("0.1", basis=basis))), "NR-08")
    assert r.status == UNCERTAIN and "calculation basis" in r.detail


def test_canonical_basis_match_ignores_case_and_surrounding_space():
    assert st(run(make_doc(penalty=good_penalty("0.4", basis=" Order Value "))), "NR-08") == FAIL


def test_full_weeks_only_is_not_required_by_nr08():
    clause = good_penalty("0.5").model_copy(update={"full_weeks_only": None})
    assert st(run(make_doc(penalty=clause)), "NR-08") == PASS


# =============================================================================================
# Unit conventions (SPEC §3.1): _pct fields are percentage points; ratios are fractions
# =============================================================================================


def test_named_constants_use_the_documented_conventions():
    assert rule_engine.PRICE_REJECT_EXCESS_RATIO == Decimal("0.03")  # fraction
    assert rule_engine.QTY_REVIEW_THRESHOLD_RATIO == Decimal("0.05")  # fraction
    assert rule_engine.ADVANCE_REJECT_THRESHOLD_PCT == Decimal("20")  # percentage points
    assert rule_engine.PENALTY_MIN_RATE_PCT == Decimal("0.5")  # percentage points per week
    assert rule_engine.VAT_REQUIRED_RATE_PCT == Decimal("15")  # percentage points


def test_pct_fields_are_percentage_points_not_fractions():
    # 0.2 points of advance is tiny (PASS); mistaking it for the fraction 0.2 would be a 20%-style value.
    assert st(run(make_doc(advance_payment_pct=fld("dec", D("0.2"), "0.2% advance"))), "R-02") == PASS
    # A penalty of 0.005 points per week is far below 0.5 points (FAIL); as a fraction it would equal 0.5%.
    assert st(run(make_doc(penalty=good_penalty("0.005"))), "NR-08") == FAIL
    # A VAT rate of 0.15 is not 15 percentage points.
    assert st(run(make_doc(vat_rate_pct=fld("dec", D("0.15"), "VAT 0.15%"))), "NR-05") == FAIL


def test_ratios_are_fractions_a_three_percent_excess_is_0_03_not_3():
    assert st(run(priced("494.40")), "R-01", "line-1") == PASS  # excess 0.03 exactly
    assert st(run(priced("494.88")), "R-01", "line-1") == FAIL  # excess 0.031
    assert st(run(priced("1440.00")), "R-01", "line-1") == FAIL  # excess 2.0 (200%); a points-style 3 would pass


# =============================================================================================
# Reject + Review together; precedence at aggregation
# =============================================================================================


def test_reject_fail_and_review_fail_both_present_and_reject_wins():
    doc = make_doc([line(price="494.4048")], delivery_days=fld("int", 50, "Delivery within 50 days"), validity_days=fld("int", 10, "valid for 10 days"))
    results = run(doc)
    assert st(results, "R-05") == FAIL and st(results, "NR-07") == FAIL
    decision, reason = aggregate_decision(results)
    assert decision == Decision.REJECT and "R-05" in reason and "R-01" in reason and "NR-07" not in reason


def test_reject_rule_with_unknown_field_is_needs_review_never_reject():
    results = run(make_doc(delivery_days=unknown("int"), tin=unknown("str")))
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_definite_reject_stands_despite_unrelated_uncertainty():
    results = run(make_doc(advance_payment_pct=fld("dec", D("40"), "40% advance"), validity_days=unknown("int")))
    assert st(results, "NR-07") == UNCERTAIN and aggregate_decision(results)[0] == Decision.REJECT


# =============================================================================================
# Step 4: aggregation and coverage validation (M1 decision 10)
# =============================================================================================


def full_set(k=1, overrides=None):
    """A complete, all-PASS result set built through the builder; overrides map (rule_id, item_id) -> status."""
    overrides = overrides or {}
    out = []
    ref = EvidenceRef(label="q", source_quote="quoted", page_number=1)
    ids = [f"line-{n}" for n in range(1, k + 1)] if k else [None]
    for rid in PER_ITEM_RULES:
        for item_id in ids:
            status = overrides.get((rid, item_id), UNCERTAIN if item_id is None else PASS)
            out.append(build_rule_result(rid, status, f"{rid} detail", item_id=item_id, evidence_refs=[ref]))
    for rid in GLOBAL_RULES:
        out.append(build_rule_result(rid, overrides.get((rid, None), PASS), f"{rid} detail", evidence_refs=[ref]))
    return out


def test_all_pass_is_approve():
    assert aggregate_decision(full_set(2))[0] == Decision.APPROVE


def test_not_applicable_does_not_block_approve_when_everything_else_passes():
    results = [r for r in full_set(1) if r.rule_id != "NR-08"]
    results.append(build_rule_result("NR-08", NA, "no clause", not_applicable_reason="clause absent"))
    assert aggregate_decision(results)[0] == Decision.APPROVE


def test_empty_result_list_is_needs_review_never_approve():
    decision, reason = aggregate_decision([])
    assert decision == Decision.NEEDS_REVIEW and reason.strip()


def test_definite_reject_wins_over_review_fail_and_uncertain():
    results = full_set(1, {("R-02", None): FAIL, ("NR-07", None): FAIL, ("NR-05", None): UNCERTAIN})
    assert aggregate_decision(results)[0] == Decision.REJECT


def test_uncertain_reject_rule_is_needs_review_not_reject():
    assert aggregate_decision(full_set(1, {("R-05", None): UNCERTAIN}))[0] == Decision.NEEDS_REVIEW


def test_review_fail_is_needs_review():
    assert aggregate_decision(full_set(1, {("NR-04", None): FAIL}))[0] == Decision.NEEDS_REVIEW


def test_reject_reason_lists_each_definite_reject_fail_with_item_id_and_evidence():
    results = full_set(2, {("R-01", "line-2"): FAIL, ("R-05", None): FAIL})
    decision, reason = aggregate_decision(results)
    assert decision == Decision.REJECT
    assert "R-01 [line-2] FAIL" in reason and "R-05 FAIL" in reason and '"quoted"' in reason and "R-01 [line-1]" not in reason


def test_reject_reason_shows_absence_evidence_for_absent_fields():
    clause = PenaltyClause(state=FieldState.ABSENT, absence_evidence=absence("penalty_clause"))
    decision, reason = aggregate_decision(run(make_doc(penalty=clause)))
    assert decision == Decision.REJECT and "absence of penalty_clause" in reason


def test_needs_review_reason_lists_every_fail_and_uncertain_result():
    results = full_set(1, {("NR-04", None): FAIL, ("NR-05", None): UNCERTAIN, ("R-05", None): UNCERTAIN})
    _, reason = aggregate_decision(results)
    assert "NR-04 FAIL" in reason and "NR-05 UNCERTAIN" in reason and "R-05 UNCERTAIN" in reason


@pytest.mark.parametrize("missing", GLOBAL_RULES)
def test_a_missing_global_rule_blocks_approve(missing):
    results = [r for r in full_set(1) if r.rule_id != missing]
    decision, reason = aggregate_decision(results)
    assert decision == Decision.NEEDS_REVIEW and missing in reason


@pytest.mark.parametrize("missing", PER_ITEM_RULES)
def test_a_missing_per_item_rule_blocks_approve(missing):
    assert aggregate_decision([r for r in full_set(1) if r.rule_id != missing])[0] == Decision.NEEDS_REVIEW


def test_duplicate_global_result_blocks_approve():
    results = full_set(1)
    results.append(next(r for r in results if r.rule_id == "NR-07"))
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_global_rule_carrying_an_item_id_blocks_approve():
    results = [r for r in full_set(1) if r.rule_id != "NR-07"]
    results.append(build_rule_result("NR-07", PASS, "d", item_id="line-1", evidence_refs=[EvidenceRef(label="q", source_quote="q")]))
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_per_item_rules_covering_different_line_sets_block_approve():
    results = [r for r in full_set(2) if not (r.rule_id == "NR-02" and r.item_id == "line-2")]
    decision, reason = aggregate_decision(results)
    assert decision == Decision.NEEDS_REVIEW and "same set of line items" in reason


def test_duplicate_per_item_results_block_approve():
    results = full_set(1)
    results.append(next(r for r in results if r.rule_id == "R-01"))
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_item_id_none_per_item_result_that_is_not_uncertain_blocks_approve():
    results = [r for r in full_set(0) if r.rule_id != "R-01"]
    results.append(build_rule_result("R-01", PASS, "d", evidence_refs=[EvidenceRef(label="q", source_quote="q")]))
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_mixing_none_and_line_results_for_a_per_item_rule_blocks_approve():
    results = full_set(1)
    results.append(build_rule_result("R-01", UNCERTAIN, "no line items could be extracted"))
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_empty_item_document_shape_is_valid_coverage_and_needs_review_via_uncertain():
    decision, reason = aggregate_decision(full_set(0))
    assert decision == Decision.NEEDS_REVIEW and "coverage is incomplete" not in reason


def test_definite_reject_still_wins_when_other_results_are_missing():
    results = [r for r in full_set(1, {("R-05", None): FAIL}) if r.rule_id != "NR-08"]
    assert aggregate_decision(results)[0] == Decision.REJECT


def test_incomplete_coverage_is_reported_in_the_reason():
    _, reason = aggregate_decision([r for r in full_set(1) if r.rule_id != "NR-08"])
    assert "fail-closed" in reason and "NR-08 has no result" in reason


def test_collect_assumptions_is_the_deduplicated_sorted_union():
    results = [
        build_rule_result("R-01", UNCERTAIN, "d", item_id="line-1", assumptions=["AMB-12", "AMB-05"]),
        build_rule_result("NR-01", UNCERTAIN, "d", item_id="line-1", assumptions=["AMB-12"]),
        build_rule_result("R-02", UNCERTAIN, "d"),
    ]
    assert collect_assumptions(results) == ["AMB-05", "AMB-12"]
    assert collect_assumptions([]) == []


def test_end_to_end_decision_reasons_are_never_empty():
    for doc in (make_doc(), make_doc([]), make_doc(tin=absent("str", "tin")), priced("494.40")):
        assert aggregate_decision(run(doc))[1].strip()


# =============================================================================================
# Static guarantees (Definition of Done): no float, no string-to-number conversion, no recomputation
# =============================================================================================

SRC = Path(__file__).resolve().parents[1] / "src"


def float_hits(tree):
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "float":
            hits.append(f"name 'float' at line {node.lineno}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            hits.append(f"float literal {node.value!r} at line {node.lineno}")
    return hits


def test_ast_detector_catches_float_names_annotations_calls_and_literals_but_not_text():
    assert len(float_hits(ast.parse("x: float = 1.5\ny = float('2')"))) == 3
    assert float_hits(ast.parse('"""a float in a docstring"""\n# float comment\nx = "float"')) == []


@pytest.mark.parametrize("module", ["arithmetic.py", "rule_engine.py"])
def test_no_float_in_module(module):
    assert float_hits(ast.parse((SRC / module).read_text())) == []


@pytest.mark.parametrize("module", ["arithmetic.py", "rule_engine.py"])
def test_modules_never_convert_strings_into_numbers(module):
    tree = ast.parse((SRC / module).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"int", "float", "eval"}, f"{node.func.id}() at line {node.lineno}"
            if node.func.id == "Decimal":
                assert all(isinstance(a, ast.Constant) for a in node.args), f"Decimal(non-literal) at line {node.lineno}"


def test_rule_engine_does_not_import_or_call_the_arithmetic_module():
    tree = ast.parse((SRC / "rule_engine.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [getattr(node, "module", "") or ""] + [a.name for a in node.names]
            assert not any("arithmetic" in n and "ArithmeticCheckResult" not in n for n in names), names
        if isinstance(node, ast.Name):
            assert node.id != "run_arithmetic_checks"


def test_arithmetic_module_has_no_rule_or_decision_logic():
    tree = ast.parse((SRC / "arithmetic.py").read_text())
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not imported & {"Decision", "RuleResult", "RuleStatus", "RuleSeverity"}
    assert "rule_engine" not in (SRC / "arithmetic.py").read_text()
