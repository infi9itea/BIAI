"""Milestone 7: field-state and boundary scenarios through the real arithmetic checks, rule engine and aggregator.

Every input is a typed document built with ``tests/builders.py``; nothing here calls a model or reads a PDF (except the
one verifier test, which uses a synthetic page). Expected statuses come from ``assessment-data/company-rules.md`` and
the project SPEC, not from observed output."""

from decimal import Decimal as D

import pytest

from src import verifier
from src.arithmetic import run_arithmetic_checks
from src.models import (
    Decision,
    ExtractedDocument,
    FieldState,
    PackResult,
    RuleStatus,
)
from src.parsing import unit_key
from src.rule_engine import aggregate_decision, evaluate_all_rules
from tests.builders import (
    UNVERIFIED,
    absence,
    absent,
    fld,
    good_payment,
    good_penalty,
    line,
    make_doc,
    make_pr,
    money,
    unknown,
)
from tests.m3_helpers import SYNTH_TEXT, make_pdf_result

PR = make_pr()


def run(doc: ExtractedDocument):
    checks = run_arithmetic_checks(doc)
    results = evaluate_all_rules(doc, PR, checks)
    decision, reason = aggregate_decision(results)
    return decision, results, checks


def status_of(results, rule_id: str, item_id: str | None = None) -> RuleStatus:
    matches = [r for r in results if r.rule_id == rule_id and (item_id is None or r.item_id == item_id)]
    assert matches, f"no {rule_id} result"
    assert len(matches) == 1, f"{len(matches)} results for {rule_id}; pass item_id"
    return matches[0].status


def get(results, rule_id: str, item_id: str | None = None):
    return next(r for r in results if r.rule_id == rule_id and (item_id is None or r.item_id == item_id))


def test_baseline_document_is_approved():
    decision, results, _ = run(make_doc())
    assert decision == Decision.APPROVE
    assert not [r for r in results if r.status in (RuleStatus.FAIL, RuleStatus.UNCERTAIN)]


# ---------------------------------------------------------------------------------------------
# Whole-document states
# ---------------------------------------------------------------------------------------------


def test_all_fields_unknown_is_needs_review():
    decision, results, _ = run(ExtractedDocument())
    assert decision == Decision.NEEDS_REVIEW
    assert not [r for r in results if r.status == RuleStatus.PASS]  # nothing can pass on no information
    assert not [r for r in results if r.severity.value == "REJECT" and r.status == RuleStatus.FAIL]


def test_empty_item_list_is_needs_review_with_one_uncertain_result_per_per_item_rule():
    decision, results, checks = run(make_doc(lines=[]))
    assert decision == Decision.NEEDS_REVIEW
    for rule_id in ("R-01", "NR-01", "NR-02"):
        rows = [r for r in results if r.rule_id == rule_id]
        assert len(rows) == 1 and rows[0].status == RuleStatus.UNCERTAIN and rows[0].item_id is None
    assert len(results) == 3 + 10


def test_empty_arithmetic_check_list_makes_nr06_uncertain_never_pass():
    doc = make_doc()
    results = evaluate_all_rules(doc, PR, [])
    assert status_of(results, "NR-06") == RuleStatus.UNCERTAIN
    assert aggregate_decision(results)[0] == Decision.NEEDS_REVIEW


def test_empty_item_list_leaves_the_subtotal_check_unknown():
    doc = make_doc(lines=[])
    subtotal = [c for c in run_arithmetic_checks(doc) if c.check_name == "independent_subtotal"]
    assert subtotal and all(c.status == "UNKNOWN" for c in subtotal)


# ---------------------------------------------------------------------------------------------
# Payment terms (NR-03)
# ---------------------------------------------------------------------------------------------


def test_payment_range_30_to_90_days_fails_nr03():
    _, results, _ = run(make_doc(payment=good_payment(min_days=30, max_days=90)))
    assert status_of(results, "NR-03") == RuleStatus.FAIL


def test_payment_range_30_to_90_days_as_agreed_is_discretionary_and_fails_nr03():
    _, results, _ = run(make_doc(payment=good_payment(min_days=30, max_days=90, discretionary=True)))
    assert status_of(results, "NR-03") == RuleStatus.FAIL


def test_discretionary_payment_with_acceptable_bounds_still_fails_nr03():
    _, results, _ = run(make_doc(payment=good_payment(min_days=45, discretionary=True)))
    assert status_of(results, "NR-03") == RuleStatus.FAIL


def test_payment_after_delivery_and_acceptance_is_uncertain_not_fail():
    _, results, _ = run(make_doc(payment=good_payment(min_days=45, anchor="other")))
    assert status_of(results, "NR-03") == RuleStatus.UNCERTAIN


@pytest.mark.parametrize("anchor", ["other", "invoice", None])
def test_payment_anchor_other_than_delivery_is_uncertain_and_needs_review(anchor):
    decision, results, _ = run(make_doc(payment=good_payment(min_days=45, anchor=anchor)))
    assert status_of(results, "NR-03") == RuleStatus.UNCERTAIN
    assert decision == Decision.NEEDS_REVIEW


@pytest.mark.parametrize("days,expected", [(30, RuleStatus.PASS), (60, RuleStatus.PASS), (29, RuleStatus.FAIL), (61, RuleStatus.FAIL)])
def test_payment_window_bounds_are_inclusive(days, expected):
    _, results, _ = run(make_doc(payment=good_payment(min_days=days)))
    assert status_of(results, "NR-03") == expected


# ---------------------------------------------------------------------------------------------
# VAT presentation (NR-05) and arithmetic (NR-06)
# ---------------------------------------------------------------------------------------------


def test_vat_absent_with_complete_search_fails_nr05_with_absence_evidence():
    doc = make_doc(vat_shown_separately=absent("bool", "vat_shown_separately"))
    _, results, _ = run(doc)
    nr05 = get(results, "NR-05")
    assert nr05.status == RuleStatus.FAIL
    assert nr05.absence_evidence and nr05.absence_evidence[0].field_searched == "vat_shown_separately"


def test_all_taxes_included_fails_nr05_through_evidence_refs():
    doc = make_doc(vat_shown_separately=fld("bool", False, "All taxes included in the prices"))
    _, results, _ = run(doc)
    nr05 = get(results, "NR-05")
    assert nr05.status == RuleStatus.FAIL
    assert nr05.evidence_refs and any("All taxes included" in (ref.source_quote or str(ref)) for ref in nr05.evidence_refs)


def test_printed_ten_percent_vat_correctly_calculated_fails_nr05_passes_nr06_needs_review():
    subtotal = money(D("12000") * D("472.00"))
    vat = money(subtotal * D("0.10"))
    doc = make_doc(
        vat_rate_pct=fld("dec", D("10"), "VAT 10%"),
        vat_amount_bdt=fld("dec", vat, f"VAT 10% {vat}"),
        grand_total_bdt=fld("dec", subtotal + vat, f"Total {subtotal + vat}"),
    )
    decision, results, _ = run(doc)
    assert status_of(results, "NR-05") == RuleStatus.FAIL
    assert status_of(results, "NR-06") == RuleStatus.PASS
    assert decision == Decision.NEEDS_REVIEW


def test_wrong_line_total_fails_nr06_and_needs_review():
    doc = make_doc(lines=[line(total="5664001.00")])
    decision, results, _ = run(doc)
    assert status_of(results, "NR-06") == RuleStatus.FAIL
    assert decision == Decision.NEEDS_REVIEW


# ---------------------------------------------------------------------------------------------
# Field states
# ---------------------------------------------------------------------------------------------


def test_found_but_unverified_field_is_treated_as_unknown_by_the_rules():
    doc = make_doc(advance_payment_pct=fld("dec", D("50"), "50% advance", status=UNVERIFIED))
    _, results, _ = run(doc)
    assert status_of(results, "R-02") == RuleStatus.UNCERTAIN  # not FAIL: an unverified 50 must not reject


def test_unverified_tin_does_not_trigger_r04_fail():
    doc = make_doc(tin=fld("str", "101458013391", "TIN 101458013391", status=UNVERIFIED))
    _, results, _ = run(doc)
    assert status_of(results, "R-04") == RuleStatus.UNCERTAIN


def test_advance_explicit_zero_passes_r02():
    doc = make_doc(advance_payment_pct=fld("dec", D("0"), "No advance payment is required"))
    _, results, _ = run(doc)
    assert status_of(results, "R-02") == RuleStatus.PASS


def test_advance_unknown_is_uncertain_and_needs_review():
    decision, results, _ = run(make_doc(advance_payment_pct=unknown("dec")))
    assert status_of(results, "R-02") == RuleStatus.UNCERTAIN
    assert decision == Decision.NEEDS_REVIEW


@pytest.mark.parametrize("pct,expected", [("20", RuleStatus.PASS), ("20.01", RuleStatus.FAIL), ("19.99", RuleStatus.PASS)])
def test_advance_threshold_is_strictly_greater_than_twenty(pct, expected):
    _, results, _ = run(make_doc(advance_payment_pct=fld("dec", D(pct), f"{pct}% advance")))
    assert status_of(results, "R-02") == expected


def test_absent_tin_and_bin_with_absence_evidence_fail_r04_and_reject():
    doc = make_doc(tin=absent("str", "tin"), bin=absent("str", "bin"))
    decision, results, _ = run(doc)
    r04 = get(results, "R-04")
    assert r04.status == RuleStatus.FAIL and len(r04.absence_evidence) == 2
    assert decision == Decision.REJECT


def test_absent_without_a_complete_search_cannot_even_be_built():
    """The model layer refuses ABSENT unless the search was complete, so an incomplete search can never reach R-04."""
    with pytest.raises(ValueError):
        type(unknown("str"))(state=FieldState.ABSENT, absence_evidence=absence("tin", complete=False))


def test_absence_evidence_reaches_the_final_pack_result():
    from src import rule_engine

    doc = make_doc(
        tin=absent("str", "tin"),
        penalty=type(good_penalty())(state=FieldState.ABSENT, absence_evidence=absence("penalty")),
    )
    decision, results, checks = run(doc)
    result = PackResult(pack_filename="x.pdf", extracted=doc, arithmetic_checks=checks, rule_results=results,
                        decision=decision, reason="reason",
                        assumptions_applied=rule_engine.collect_assumptions(results))
    assert get(result.rule_results, "R-04").absence_evidence
    assert get(result.rule_results, "R-03").absence_evidence
    assert result.decision == Decision.REJECT


# ---------------------------------------------------------------------------------------------
# Penalty clause (R-03, NR-08)
# ---------------------------------------------------------------------------------------------


def test_no_penalty_clause_fails_r03_and_nr08_is_not_applicable():
    doc = make_doc(penalty=type(good_penalty())(state=FieldState.ABSENT, absence_evidence=absence("penalty")))
    decision, results, _ = run(doc)
    assert status_of(results, "R-03") == RuleStatus.FAIL
    nr08 = get(results, "NR-08")
    assert nr08.status == RuleStatus.NOT_APPLICABLE and nr08.not_applicable_reason
    assert decision == Decision.REJECT


def test_penalty_rate_below_half_percent_per_week_fails_nr08():
    decision, results, _ = run(make_doc(penalty=good_penalty(rate="0.4")))
    assert status_of(results, "NR-08") == RuleStatus.FAIL
    assert decision == Decision.NEEDS_REVIEW


@pytest.mark.parametrize("rate,expected", [("0.5", RuleStatus.PASS), ("0.49", RuleStatus.FAIL), ("2", RuleStatus.PASS)])
def test_penalty_minimum_is_inclusive(rate, expected):
    _, results, _ = run(make_doc(penalty=good_penalty(rate=rate)))
    assert status_of(results, "NR-08") == expected


def test_penalty_rate_unknown_is_uncertain_and_needs_review():
    decision, results, _ = run(make_doc(penalty=good_penalty(rate=None)))
    assert status_of(results, "NR-08") == RuleStatus.UNCERTAIN
    assert decision == Decision.NEEDS_REVIEW


@pytest.mark.parametrize("period", ["day", "other", None])
def test_penalty_not_stated_per_week_is_uncertain_with_no_conversion(period):
    decision, results, _ = run(make_doc(penalty=good_penalty(rate="0.1", period=period)))
    nr08 = get(results, "NR-08")
    assert nr08.status == RuleStatus.UNCERTAIN  # a per-day 0.1% is never converted to a weekly 0.7% or compared
    assert decision == Decision.NEEDS_REVIEW


# ---------------------------------------------------------------------------------------------
# Delivery and validity boundaries
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "days,r05,nr04",
    [(30, RuleStatus.PASS, RuleStatus.PASS), (31, RuleStatus.PASS, RuleStatus.FAIL),
     (45, RuleStatus.PASS, RuleStatus.FAIL), (46, RuleStatus.FAIL, RuleStatus.PASS)],
)
def test_delivery_boundaries(days, r05, nr04):
    _, results, _ = run(make_doc(delivery_days=fld("int", days, f"Delivery within {days} days")))
    assert status_of(results, "R-05") == r05
    assert status_of(results, "NR-04") == nr04


@pytest.mark.parametrize("days,expected", [(30, RuleStatus.PASS), (29, RuleStatus.FAIL)])
def test_validity_minimum_is_thirty_days(days, expected):
    _, results, _ = run(make_doc(validity_days=fld("int", days, f"valid for {days} days")))
    assert status_of(results, "NR-07") == expected


# ---------------------------------------------------------------------------------------------
# Price, VAT-inclusive prices and per-item matching
# ---------------------------------------------------------------------------------------------


def test_vat_included_price_is_not_compared():
    _, results, _ = run(make_doc(lines=[line(price="500.00", vat_included=True)]))
    assert status_of(results, "R-01", "line-1") == RuleStatus.UNCERTAIN
    assert status_of(results, "NR-01", "line-1") == RuleStatus.UNCERTAIN


def test_vat_inclusion_unknown_with_vat_shown_separately_is_compared_and_records_amb12():
    _, results, _ = run(make_doc(lines=[line(price="500.00", vat_included=None)]))
    r01 = get(results, "R-01", "line-1")
    assert r01.status == RuleStatus.FAIL  # 500 vs 480 is 4.17% over
    assert "AMB-12" in r01.assumptions_applied


def test_vat_inclusion_unknown_without_separate_vat_is_uncertain():
    doc = make_doc(lines=[line(price="500.00", vat_included=None)], vat_shown_separately=fld("bool", False, "VAT included"))
    _, results, _ = run(doc)
    assert status_of(results, "R-01", "line-1") == RuleStatus.UNCERTAIN


@pytest.mark.parametrize(
    "price,r01,nr01",
    [("480.00", RuleStatus.PASS, RuleStatus.PASS),
     ("494.40", RuleStatus.PASS, RuleStatus.FAIL),   # exactly 3% above: review, not reject
     ("494.41", RuleStatus.FAIL, RuleStatus.PASS),   # just over 3%: reject
     ("480.01", RuleStatus.PASS, RuleStatus.FAIL)],
)
def test_price_excess_boundaries(price, r01, nr01):
    _, results, _ = run(make_doc(lines=[line(price=price)]))
    assert status_of(results, "R-01", "line-1") == r01
    assert status_of(results, "NR-01", "line-1") == nr01


def test_quantity_difference_boundary():
    _, ok, _ = run(make_doc(lines=[line(qty="12600")]))        # exactly 5% over 12000
    _, over, _ = run(make_doc(lines=[line(qty="12601")]))
    assert status_of(ok, "NR-02", "line-1") == RuleStatus.PASS
    assert status_of(over, "NR-02", "line-1") == RuleStatus.FAIL


def test_unknown_item_code_makes_that_items_rules_uncertain():
    _, results, _ = run(make_doc(lines=[line(code="Z", code_match="NO")]))
    for rule_id in ("R-01", "NR-01", "NR-02"):
        assert status_of(results, rule_id, "line-1") == RuleStatus.UNCERTAIN


def test_mismatched_unit_makes_that_items_rules_uncertain():
    _, results, _ = run(make_doc(lines=[line(unit="kg", unit_match="NO")]))
    for rule_id in ("R-01", "NR-01", "NR-02"):
        assert status_of(results, rule_id, "line-1") == RuleStatus.UNCERTAIN


def test_multi_item_document_keeps_distinct_item_ids_through_aggregation():
    lines = [line(), line(code="B", qty="20000", price="38.00", unit="piece")]
    decision, results, checks = run(make_doc(lines=lines))
    for rule_id in ("R-01", "NR-01", "NR-02"):
        ids = [r.item_id for r in results if r.rule_id == rule_id]
        assert ids == ["line-1", "line-2"]
    assert len(results) == 3 * 2 + 10
    pack = PackResult(pack_filename="x.pdf", rule_results=results, arithmetic_checks=checks, decision=decision, reason="r")
    assert {r.item_id for r in pack.rule_results if r.item_id} == {"line-1", "line-2"}
    assert decision == Decision.APPROVE


def test_one_failing_item_in_a_multi_item_document_rejects_the_pack_and_names_the_line():
    lines = [line(), line(code="B", qty="20000", price="45.00", unit="piece")]  # B max is 38.00
    decision, results, _ = run(make_doc(lines=lines))
    assert status_of(results, "R-01", "line-1") == RuleStatus.PASS
    assert status_of(results, "R-01", "line-2") == RuleStatus.FAIL
    assert decision == Decision.REJECT


@pytest.mark.parametrize("extracted", ["m", "M", "metres", "Metre"])
def test_metre_aliases_share_one_comparison_key(extracted):
    assert unit_key(extracted) == unit_key("metre")


def test_unit_alias_m_matches_metre_in_the_extractor_matching_step():
    from src.extractor import _match_item

    item = line(code="A", unit="m", unit_match="UNKNOWN", code_match="UNKNOWN")
    assert _match_item(item, PR).unit_match == "YES"


# ---------------------------------------------------------------------------------------------
# Verification failure and rule coverage
# ---------------------------------------------------------------------------------------------


def test_evidence_verification_failure_turns_the_field_unknown():
    doc = make_doc(advance_payment_pct=fld("dec", D("10"), "this sentence is not in the document"))
    verified = verifier.verify_evidence(doc, make_pdf_result([SYNTH_TEXT]))
    assert verified.advance_payment_pct.state == FieldState.UNKNOWN
    assert verified.advance_payment_pct.value is None
    _, results, _ = run(verified)
    assert status_of(results, "R-02") == RuleStatus.UNCERTAIN


def test_review_rules_are_still_evaluated_when_a_reject_rule_fails():
    doc = make_doc(
        advance_payment_pct=fld("dec", D("30"), "30% advance"),   # R-02 reject
        validity_days=fld("int", 10, "valid for 10 days"),        # NR-07 review
    )
    decision, results, _ = run(doc)
    assert decision == Decision.REJECT
    assert status_of(results, "R-02") == RuleStatus.FAIL
    assert status_of(results, "NR-07") == RuleStatus.FAIL  # evaluated and reported, not skipped
    assert len(results) == 3 + 10
