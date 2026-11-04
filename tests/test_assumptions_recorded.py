"""Review fixes: every assumption that shapes a result is carried by that result (PROJECT_GUIDE: `[A]` must be
labelled in system output), AMB-15 survives an UNKNOWN VAT check, and an UNKNOWN arithmetic result that shows a
printed value also shows its quote. Typed inputs only (tests/builders.py)."""

from src.arithmetic import run_arithmetic_checks
from src.models import AMB_VAT_SUBTOTAL, Evidence, FieldRecord, FieldState, VerificationStatus
from src.rule_engine import collect_assumptions, evaluate_all_rules
from src.verifier import AMB15_METHOD
from tests.builders import line, make_doc, make_pr, unknown
from tests.test_rule_engine import get

PR = make_pr()


def results_for(doc):
    checks = run_arithmetic_checks(doc)
    return checks, evaluate_all_rules(doc, PR, checks)


def layout_basis():
    ev = Evidence(source_quote="Subtotal 1.00 VAT 15% 0.15 Total 1.15", page_number=1,
                  normalization_method=AMB15_METHOD, verification_status=VerificationStatus.VERIFIED)
    return FieldRecord[str](state=FieldState.FOUND, value="subtotal", evidence=ev)


# ---- finding 1: the assumption register is carried into the results -----------------------------------------


def test_each_rule_records_the_assumptions_that_shaped_its_result():
    _, rules = results_for(make_doc())
    ids = lambda rule, item=None: set(get(rules, rule, item).assumptions_applied)  # noqa: E731
    assert ids("R-05") == {"AMB-01"} and ids("NR-04") == {"AMB-01"}
    assert ids("R-01", "line-1") == {"AMB-12", "AMB-02", "AMB-06"} == ids("NR-01", "line-1")
    assert ids("NR-02", "line-1") == {"AMB-06"}
    assert ids("NR-03") == {"AMB-03", "AMB-13"}
    assert ids("R-03") == {"AMB-04"} and ids("NR-08") == {"AMB-14", "AMB-04"}
    assert ids("NR-06") == {"AMB-09"}


def test_the_pack_level_union_is_exactly_these_ids():
    _, rules = results_for(make_doc())
    assert collect_assumptions(rules) == ["AMB-01", "AMB-02", "AMB-03", "AMB-04", "AMB-06", "AMB-09", "AMB-12",
                                          "AMB-13", "AMB-14"]  # no AMB-05 (advance known), no AMB-15 (basis by words)


def test_a_boundary_assumption_is_not_claimed_when_the_rule_could_not_evaluate():
    _, rules = results_for(make_doc(delivery_days=unknown("int"), payment=make_doc().payment.__class__()))
    assert get(rules, "R-05").assumptions_applied == [] and get(rules, "NR-04").assumptions_applied == []
    assert get(rules, "NR-03").assumptions_applied == []


def test_arithmetic_results_record_amb09_only_when_something_was_calculated():
    checks, _ = results_for(make_doc())
    assert all(c.assumptions_applied == ["AMB-09"] for c in checks)
    unknown_checks, _ = results_for(make_doc(lines=[line(qty=None)]))
    subtotal = next(c for c in unknown_checks if c.check_name == "independent_subtotal")
    assert subtotal.calculated is None and subtotal.assumptions_applied == []


# ---- finding 2: AMB-15 survives an UNKNOWN VAT / grand-total check -----------------------------------------


def test_amb15_is_kept_on_unknown_vat_and_grand_checks():
    checks, rules = results_for(make_doc(vat_price_basis=layout_basis(), vat_rate_pct=unknown("dec")))
    vat = next(c for c in checks if c.check_name == "vat")
    grand = next(c for c in checks if c.check_name == "grand")
    assert vat.status == grand.status == "UNKNOWN"
    assert vat.assumptions_applied == [AMB_VAT_SUBTOTAL] == grand.assumptions_applied
    assert AMB_VAT_SUBTOTAL in get(rules, "NR-06").assumptions_applied
    assert AMB_VAT_SUBTOTAL in collect_assumptions(rules)


def test_amb15_is_kept_when_the_independent_subtotal_is_unavailable():
    checks, _ = results_for(make_doc(lines=[line(qty=None)], vat_price_basis=layout_basis()))
    vat = next(c for c in checks if c.check_name == "vat")
    assert vat.status == "UNKNOWN" and AMB_VAT_SUBTOTAL in vat.assumptions_applied


def test_the_words_route_never_reports_amb15_even_when_unknown():
    checks, _ = results_for(make_doc(vat_rate_pct=unknown("dec")))  # builder basis is verified 'subtotal' by words
    assert all(AMB_VAT_SUBTOTAL not in c.assumptions_applied for c in checks)


def test_an_unusable_basis_reports_no_amb15():
    checks, _ = results_for(make_doc(vat_price_basis=unknown("str")))
    assert all(AMB_VAT_SUBTOTAL not in c.assumptions_applied for c in checks)


# ---- finding 3: UNKNOWN results that show a printed value also show its quote --------------------------------


def quotes(check):
    return [r.source_quote for r in check.evidence_refs]


def test_unknown_subtotal_result_attaches_the_printed_subtotal_quote():
    checks, _ = results_for(make_doc(lines=[line(qty=None)]))
    sub = next(c for c in checks if c.check_name == "independent_subtotal")
    assert sub.status == "UNKNOWN" and sub.printed is not None
    assert any(q.startswith("Subtotal ") for q in quotes(sub))


def test_unknown_vat_result_attaches_the_printed_vat_quote():
    checks, _ = results_for(make_doc(vat_price_basis=unknown("str")))
    vat = next(c for c in checks if c.check_name == "vat")
    assert vat.status == "UNKNOWN" and vat.printed is not None
    assert any(q.startswith("VAT 15% ") for q in quotes(vat))


def test_unknown_grand_result_attaches_the_printed_total_quote():
    checks, _ = results_for(make_doc(vat_price_basis=unknown("str")))
    grand = next(c for c in checks if c.check_name == "grand")
    assert grand.status == "UNKNOWN" and grand.printed is not None
    assert any(q.startswith("Total ") for q in quotes(grand))


def test_an_unusable_printed_value_adds_no_reference_and_no_printed_figure():
    checks, _ = results_for(make_doc(vat_price_basis=unknown("str"), vat_amount_bdt=unknown("dec")))
    vat = next(c for c in checks if c.check_name == "vat")
    assert vat.printed is None and not any(q.startswith("VAT 15% ") for q in quotes(vat))
