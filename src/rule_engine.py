"""Deterministic rule engine (Milestone 1; SPEC §5, §5.6, §7.3, §8.4, §10, §13).

Consumes a typed, verified ``ExtractedDocument``, a ``PurchaseRequest`` and the ``ArithmeticCheckResult``
list from ``arithmetic.py``. No LLM, no string parsing, no arithmetic recalculation (NR-06 only reads the
check results). Every field is read through ``is_usable``: a FOUND field that is not VERIFIED is UNKNOWN.

All rules are always evaluated; only ``aggregate_decision`` gives REJECT priority (SPEC §5, §5.4).
"""

from collections import defaultdict
from decimal import Context, Decimal
from typing import Any, Iterable

from src.models import (
    AbsenceEvidence,
    ArithmeticCheckResult,
    Decision,
    EvidenceRef,
    ExtractedDocument,
    FieldState,
    LineItem,
    PRItem,
    PurchaseRequest,
    RuleResult,
    RuleSeverity,
    RuleStatus,
    is_usable,
)

# ---------------------------------------------------------------------------------------------
# Named constants (each cites rule ID and SPEC / AMB-ID)
# ---------------------------------------------------------------------------------------------

# Price rules (R-01, NR-01) — excess RATIO (a fraction, not percentage points)  [A] AMB-02, SPEC §3.1
PRICE_REJECT_EXCESS_RATIO = Decimal("0.03")  # > 3% over max → REJECT (R-01)
PRICE_REVIEW_EXCESS_RATIO = Decimal("0")  # any excess over max → at least REVIEW (NR-01)

# Advance payment (R-02) — percentage points  [E] R-02, SPEC §3.1
ADVANCE_REJECT_THRESHOLD_PCT = Decimal("20")  # > 20 percentage points → REJECT

# Delivery (R-05, NR-04) — days  [A] AMB-01
DELIVERY_REJECT_DAYS = 45  # strictly > 45 → REJECT (R-05)
DELIVERY_REVIEW_MIN_DAYS = 31  # 31..45 inclusive → REVIEW (NR-04)

# Payment terms (NR-03) — days, inclusive  [A] AMB-03; anchor [A] AMB-13
PAYMENT_MIN_ACCEPTABLE_DAYS = 30
PAYMENT_MAX_ACCEPTABLE_DAYS = 60
PAYMENT_REQUIRED_ANCHOR = "delivery"  # only this anchor is evaluated against the window (AMB-13)

# Penalty rate (NR-08) — percentage points PER WEEK  [E] NR-08, [A] AMB-14, SPEC §3.1
PENALTY_MIN_RATE_PCT = Decimal("0.5")  # < 0.5 percentage points/week → REVIEW
PENALTY_COMPARABLE_PERIOD = "week"  # any other / UNKNOWN period → UNCERTAIN (AMB-14)
PENALTY_REQUIRED_BASIS = "order value"  # canonical verified basis required before comparing (M1 decision 5)

# Validity (NR-07) — days  [E] NR-07
VALIDITY_MIN_DAYS = 30

# Quantity (NR-02) — ratio (a fraction)  [E] NR-02
QTY_REVIEW_THRESHOLD_RATIO = Decimal("0.05")  # > 5% → REVIEW

# VAT presentation (NR-05) — percentage points  [E] NR-05, SPEC §3.1
VAT_REQUIRED_RATE_PCT = Decimal("15")  # VAT must be shown separately at exactly 15 percentage points

# Assumption IDs recorded in RuleResult.assumptions_applied (SPEC §7.3, §13)
AMB_ADVANCE_UNKNOWN = "AMB-05"
AMB_VAT_BASIS = "AMB-12"
AMB_PAYMENT_ANCHOR = "AMB-13"
AMB_PENALTY_PERIOD = "AMB-14"

# Company rule wording, verbatim from company-rules.md (used as RuleResult.description)
RULE_TEXT: dict[str, str] = {
    "R-01": "Unit price is more than 3% above the maximum unit price in the purchase request.",
    "R-02": "Advance payment is more than 20%.",
    "R-03": "The contract has no late-delivery penalty clause.",
    "R-04": "The supplier's TIN or BIN is missing.",
    "R-05": "Delivery is promised later than 45 days after the purchase order.",
    "NR-01": "Unit price is above the maximum, but by no more than 3%.",
    "NR-02": "Quantity differs from the quantity requested by more than 5%.",
    "NR-03": "Payment terms are unclear, or outside 30 to 60 days after delivery.",
    "NR-04": "Delivery is promised in 31 to 45 days.",
    "NR-05": "VAT is not shown separately at 15%.",
    "NR-06": "The numbers do not add up (for example line total or subtotal is wrong).",
    "NR-07": "The quotation is valid for less than 30 days.",
    "NR-08": "The late-delivery penalty must be at least 0.5% of order value per week of delay.",
}
PER_ITEM_RULES = ("R-01", "NR-01", "NR-02")  # SPEC §5.6: one result per quoted line item
GLOBAL_RULES = ("R-02", "R-03", "R-04", "R-05", "NR-03", "NR-04", "NR-05", "NR-06", "NR-07", "NR-08")

_CTX = Context(prec=60)  # own context: comparisons never depend on the caller's global Decimal context


def _severity(rule_id: str) -> RuleSeverity:
    return RuleSeverity.REJECT if rule_id.startswith("R-") else RuleSeverity.REVIEW


# ---------------------------------------------------------------------------------------------
# Step 2: the common RuleResult builder (SPEC §7.3)
# ---------------------------------------------------------------------------------------------


def _unique(items: Iterable[Any]) -> list[Any]:
    seen: list[Any] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def build_rule_result(
    rule_id: str,
    status: RuleStatus,
    detail: str,
    *,
    item_id: str | None = None,
    triggering_value: str | None = None,
    evidence_refs: Iterable[EvidenceRef] = (),
    absence_evidence: Iterable[AbsenceEvidence] = (),
    assumptions: Iterable[str] = (),
    not_applicable_reason: str | None = None,
) -> RuleResult:
    """The only place a RuleResult is constructed (M0 rejects an unsupported FAIL, so downgrade BEFORE).

    * An AbsenceEvidence with extraction_complete = False is never attached as supporting evidence.
    * A proposed FAIL with neither a verified EvidenceRef nor a complete AbsenceEvidence becomes UNCERTAIN
      (SPEC §7.3 rule 1): an untraceable FAIL is never emitted.
    """
    refs = _unique(evidence_refs)
    all_absences = list(absence_evidence)
    complete_absences = [a for a in all_absences if a.extraction_complete]
    if len(complete_absences) != len(all_absences):
        detail = f"{detail} [incomplete absence record not used as evidence]"
    if status == RuleStatus.FAIL and not refs and not complete_absences:
        status = RuleStatus.UNCERTAIN
        detail = f"{detail} [downgraded from FAIL: no verified evidence or complete absence evidence]"
    return RuleResult(
        rule_id=rule_id,
        description=RULE_TEXT[rule_id],
        status=status,
        severity=_severity(rule_id),
        item_id=item_id,
        detail=detail,
        triggering_value=triggering_value,
        evidence_refs=refs,
        absence_evidence=complete_absences,
        assumptions_applied=_unique(assumptions),
        not_applicable_reason=not_applicable_reason,
    )


# ---------------------------------------------------------------------------------------------
# Typed readers (everything goes through is_usable)
# ---------------------------------------------------------------------------------------------


def _known(field: Any) -> Any:
    """Typed value of a usable field, else None. A FOUND-but-unverified value is None (UNKNOWN)."""
    if is_usable(field) and field.value is not None:
        return field.value
    return None


def _ref(label: str, holder: Any) -> EvidenceRef:
    """Verified quote of a usable field or clause. Call only after is_usable / _known succeeded."""
    evidence = holder.evidence
    return EvidenceRef(label=label, source_quote=evidence.source_quote, page_number=evidence.page_number)


def _absent_complete(field: Any) -> bool:
    """ABSENT with a complete documented search. Anything else (incl. an inconsistent record) is not ABSENT."""
    absence = getattr(field, "absence_evidence", None)
    return (
        getattr(field, "state", None) == FieldState.ABSENT
        and absence is not None
        and absence.extraction_complete
        and getattr(field, "evidence", None) is None
    )


def _sub(a: Decimal, b: Decimal) -> Decimal:
    return _CTX.subtract(a, b)


def _mul(a: Decimal, b: Decimal) -> Decimal:
    return _CTX.multiply(a, b)


def _ratio_text(numerator: Decimal, denominator: Decimal) -> str:
    """Human-readable ratio for ``detail`` ONLY. It never decides PASS or FAIL (M1 decision 6)."""
    if denominator == 0:
        return "n/a"
    try:
        return str(_CTX.divide(numerator, denominator).quantize(Decimal("0.0001"), context=_CTX))
    except ArithmeticError:
        return "n/a"


# ---------------------------------------------------------------------------------------------
# Per-item rules R-01, NR-01, NR-02 (SPEC §5.6, §8.4, §10)
# ---------------------------------------------------------------------------------------------


def _item_label(position: int, item: LineItem) -> str:
    code = _known(item.item_code)
    return f"line-{position} (item {code})" if code else f"line-{position} (code unknown)"


def _resolve_pr_item(item: LineItem, pr_index: dict[str, PRItem]) -> tuple[PRItem | None, str | None]:
    """(PR item, None) when the item can be compared; otherwise (maybe PR item, reason). Never NOT_APPLICABLE."""
    code = _known(item.item_code)
    if code is None:
        return None, "item code is not usable (unknown, absent or unverified)"
    if item.item_code_match != "YES":
        return None, f"item code '{code}' is not matched to the purchase request (item_code_match={item.item_code_match})"
    pr_item = pr_index.get(code.strip().upper())
    if pr_item is None:
        return None, f"item code '{code}' is not in the purchase request"
    # The unit itself must be usable. unit_match is derived data and is NOT trusted on its own: a missing,
    # absent or unverified unit can never be compared, whatever the flag says (SPEC §10 item 3).
    unit = _known(item.unit)
    if unit is None:
        return pr_item, f"unit is not usable (unknown, absent or unverified); PR unit is '{pr_item.unit}'"
    if item.unit_match != "YES":
        return pr_item, f"unit '{unit}' does not match PR unit '{pr_item.unit}' (unit_match={item.unit_match})"
    return pr_item, None


def _price_basis(doc: ExtractedDocument, item: LineItem) -> tuple[bool, str, list[EvidenceRef]]:
    """SPEC §8.4 / AMB-12: (comparable, note, refs). Never strips or adds VAT to force a comparison."""
    included = _known(item.vat_included_in_price)
    if included is False:
        return True, "unit price is explicitly VAT-exclusive", [_ref("VAT included in price", item.vat_included_in_price)]
    if included is True:
        return False, "supplier price is VAT-inclusive; PR maximum VAT basis not stated", [
            _ref("VAT included in price", item.vat_included_in_price)
        ]
    shown = _known(doc.vat_shown_separately)
    if shown is True:
        return True, "VAT is a separate line, so the unit price is taken as VAT-exclusive", [
            _ref("VAT shown separately", doc.vat_shown_separately)
        ]
    return False, "VAT basis of the supplier price is not established; PR maximum VAT basis not stated", []


def _per_item_results(doc: ExtractedDocument, pr: PurchaseRequest) -> list[RuleResult]:
    if not doc.line_items:
        return [
            build_rule_result(rule_id, RuleStatus.UNCERTAIN, "no line items could be extracted")
            for rule_id in PER_ITEM_RULES
        ]
    pr_index = {code.strip().upper(): item for code, item in pr.items.items()}
    r01: list[RuleResult] = []
    nr01: list[RuleResult] = []
    nr02: list[RuleResult] = []
    for position, item in enumerate(doc.line_items, start=1):
        item_id = f"line-{position}"
        label = _item_label(position, item)
        pr_item, problem = _resolve_pr_item(item, pr_index)
        a, b = _price_results(doc, item, item_id, label, pr_item, problem)
        r01.append(a)
        nr01.append(b)
        nr02.append(_quantity_result(item, item_id, label, pr_item, problem))
    return [*r01, *nr01, *nr02]


def _price_results(
    doc: ExtractedDocument, item: LineItem, item_id: str, label: str, pr_item: PRItem | None, problem: str | None
) -> tuple[RuleResult, RuleResult]:
    price = _known(item.unit_price_bdt)
    if problem is not None or pr_item is None:
        return _pair(RuleStatus.UNCERTAIN, f"{label}: {problem}", item_id)
    if price is None:
        return _pair(RuleStatus.UNCERTAIN, f"{label}: unit price is not usable (unknown, absent or unverified)", item_id)

    maximum = pr_item.max_unit_price_bdt
    comparable, note, basis_refs = _price_basis(doc, item)
    price_ref = _ref(f"{item_id} unit price", item.unit_price_bdt)
    if not comparable:
        detail = f"{label}: supplier price {price} vs PR maximum {maximum}; not compared — {note}"
        return _pair(RuleStatus.UNCERTAIN, detail, item_id, triggering_value=f"{price} BDT",
                     evidence_refs=[price_ref, *basis_refs])

    excess = _sub(price, maximum)  # decisions use cross-multiplication, never a rounded ratio
    detail = (
        f"{label}: supplier price {price} vs PR maximum {maximum}; "
        f"excess ratio {_ratio_text(excess, maximum)} (a fraction); {note}"
    )
    refs = [price_ref, *basis_refs]
    r01_status = RuleStatus.FAIL if excess > _mul(PRICE_REJECT_EXCESS_RATIO, maximum) else RuleStatus.PASS
    nr01_status = (
        RuleStatus.FAIL
        if excess > _mul(PRICE_REVIEW_EXCESS_RATIO, maximum) and excess <= _mul(PRICE_REJECT_EXCESS_RATIO, maximum)
        else RuleStatus.PASS
    )
    kw: dict[str, Any] = dict(triggering_value=f"{price} BDT", evidence_refs=refs, assumptions=[AMB_VAT_BASIS])
    return (
        build_rule_result("R-01", r01_status, detail, item_id=item_id, **kw),
        build_rule_result("NR-01", nr01_status, detail, item_id=item_id, **kw),
    )


def _pair(status: RuleStatus, detail: str, item_id: str, **kw: Any) -> tuple[RuleResult, RuleResult]:
    """R-01 and NR-01 with the same status/detail (used for UNCERTAIN outcomes)."""
    return (
        build_rule_result("R-01", status, detail, item_id=item_id, **kw),
        build_rule_result("NR-01", status, detail, item_id=item_id, **kw),
    )


def _quantity_result(item: LineItem, item_id: str, label: str, pr_item: PRItem | None, problem: str | None) -> RuleResult:
    if problem is not None or pr_item is None:
        return build_rule_result("NR-02", RuleStatus.UNCERTAIN, f"{label}: {problem}", item_id=item_id)
    quantity = _known(item.quantity)
    if quantity is None:
        return build_rule_result(
            "NR-02", RuleStatus.UNCERTAIN, f"{label}: quantity is not usable (unknown, absent or unverified)", item_id=item_id
        )
    requested = pr_item.quantity_requested
    difference = _CTX.abs(_sub(quantity, requested))
    status = RuleStatus.FAIL if difference > _mul(QTY_REVIEW_THRESHOLD_RATIO, requested) else RuleStatus.PASS
    detail = (
        f"{label}: quoted quantity {quantity} vs requested {requested}; "
        f"difference ratio {_ratio_text(difference, requested)} (a fraction)"
    )
    return build_rule_result(
        "NR-02", status, detail, item_id=item_id, triggering_value=str(quantity),
        evidence_refs=[_ref(f"{item_id} quantity", item.quantity)],
    )


# ---------------------------------------------------------------------------------------------
# Global rules
# ---------------------------------------------------------------------------------------------


def _not_established(name: str) -> str:
    return f"{name} is not established (unknown, absent or unverified)"


def _r02(doc: ExtractedDocument) -> RuleResult:
    advance = _known(doc.advance_payment_pct)
    if advance is None:
        return build_rule_result("R-02", RuleStatus.UNCERTAIN, _not_established("advance payment"),
                                 assumptions=[AMB_ADVANCE_UNKNOWN])
    status = RuleStatus.FAIL if advance > ADVANCE_REJECT_THRESHOLD_PCT else RuleStatus.PASS
    return build_rule_result(
        "R-02", status, f"advance payment {advance} percentage points vs limit {ADVANCE_REJECT_THRESHOLD_PCT}",
        triggering_value=f"{advance}%", evidence_refs=[_ref("advance payment", doc.advance_payment_pct)],
    )


def _r03(doc: ExtractedDocument) -> RuleResult:
    penalty = doc.penalty
    if _absent_complete(penalty):
        return build_rule_result("R-03", RuleStatus.FAIL, "no late-delivery penalty clause found in the document",
                                 absence_evidence=[penalty.absence_evidence])
    if is_usable(penalty):
        return build_rule_result("R-03", RuleStatus.PASS, "a late-delivery penalty clause is present",
                                 evidence_refs=[_ref("penalty clause", penalty)])
    return build_rule_result("R-03", RuleStatus.UNCERTAIN, _not_established("the late-delivery penalty clause"))


def _r04(doc: ExtractedDocument) -> RuleResult:
    fields = (("TIN", doc.tin), ("BIN", doc.bin))
    absent = [(name, f) for name, f in fields if _absent_complete(f)]
    if absent:
        names = " and ".join(name for name, _ in absent)
        return build_rule_result("R-04", RuleStatus.FAIL, f"{names} missing from the document",
                                 absence_evidence=[f.absence_evidence for _, f in absent])
    if all(_known(f) is not None for _, f in fields):
        return build_rule_result("R-04", RuleStatus.PASS, "TIN and BIN are both present",
                                 evidence_refs=[_ref(name, f) for name, f in fields])
    unknown = " and ".join(name for name, f in fields if _known(f) is None)
    return build_rule_result("R-04", RuleStatus.UNCERTAIN, f"{unknown} not established (unknown or unverified)")


def _r05(doc: ExtractedDocument) -> RuleResult:
    days = _known(doc.delivery_days)
    if days is None:
        return build_rule_result("R-05", RuleStatus.UNCERTAIN, _not_established("delivery time in days"))
    status = RuleStatus.FAIL if days > DELIVERY_REJECT_DAYS else RuleStatus.PASS
    return build_rule_result(
        "R-05", status, f"delivery in {days} days vs limit {DELIVERY_REJECT_DAYS} (strictly greater rejects)",
        triggering_value=f"{days} days", evidence_refs=[_ref("delivery days", doc.delivery_days)],
    )


def _nr03(doc: ExtractedDocument) -> RuleResult:
    """SPEC §5.2 NR-03 order: (1) discretionary True -> FAIL; (2) non-'delivery' anchor or unknown bounds ->
    UNCERTAIN; (3) known bounds outside 30..60 -> FAIL (a definite violation, whatever the discretionary flag);
    (4) acceptable bounds -> PASS only if discretionary is known False, else UNCERTAIN (never assumed False)."""
    pay = doc.payment
    if not is_usable(pay):
        return build_rule_result("NR-03", RuleStatus.UNCERTAIN, _not_established("payment terms"))
    clause_ref = _ref("payment terms", pay)
    # (1) discretionary wording → FAIL, before any anchor check
    if pay.discretionary is True:
        return build_rule_result("NR-03", RuleStatus.FAIL,
                                 "payment terms are open-ended / discretionary, so they are unclear",
                                 triggering_value=pay.original_wording, evidence_refs=[clause_ref])
    # (2) anchor and bounds must be known; only 'delivery' is evaluated (AMB-13)
    if pay.anchor != PAYMENT_REQUIRED_ANCHOR:
        shown = pay.anchor if pay.anchor is not None else "unknown"
        return build_rule_result(
            "NR-03", RuleStatus.UNCERTAIN,
            f"payment is counted from '{shown}', not from delivery; the 30 to 60 day window is not evaluated",
            evidence_refs=[clause_ref], assumptions=[AMB_PAYMENT_ANCHOR],
        )
    if pay.min_days is None or pay.max_days is None:
        return build_rule_result("NR-03", RuleStatus.UNCERTAIN, "payment day bounds are not established",
                                 evidence_refs=[clause_ref])
    # (3) known bounds outside the window (both inclusive, AMB-03) are a definite violation, whatever the
    #     discretionary flag says: an undetermined flag cannot make a known violation uncertain.
    outside = pay.min_days < PAYMENT_MIN_ACCEPTABLE_DAYS or pay.max_days > PAYMENT_MAX_ACCEPTABLE_DAYS
    detail = (
        f"payment {pay.min_days} to {pay.max_days} days after delivery vs acceptable "
        f"{PAYMENT_MIN_ACCEPTABLE_DAYS} to {PAYMENT_MAX_ACCEPTABLE_DAYS} days (inclusive)"
    )
    triggering = f"{pay.min_days} to {pay.max_days} days"
    if outside:
        return build_rule_result("NR-03", RuleStatus.FAIL, detail, triggering_value=triggering,
                                 evidence_refs=[clause_ref], assumptions=[AMB_PAYMENT_ANCHOR])
    # (4) acceptable bounds: PASS only if the clause is KNOWN not to be discretionary. Unknown is never assumed False.
    if pay.discretionary is None:
        return build_rule_result(
            "NR-03", RuleStatus.UNCERTAIN,
            f"{detail}; but whether the payment terms are discretionary was not determined; not assumed to be false",
            triggering_value=triggering, evidence_refs=[clause_ref], assumptions=[AMB_PAYMENT_ANCHOR],
        )
    return build_rule_result("NR-03", RuleStatus.PASS, detail, triggering_value=triggering,
                             evidence_refs=[clause_ref], assumptions=[AMB_PAYMENT_ANCHOR])


def _nr04(doc: ExtractedDocument) -> RuleResult:
    days = _known(doc.delivery_days)
    if days is None:
        return build_rule_result("NR-04", RuleStatus.UNCERTAIN, _not_established("delivery time in days"))
    in_band = DELIVERY_REVIEW_MIN_DAYS <= days <= DELIVERY_REJECT_DAYS
    return build_rule_result(
        "NR-04", RuleStatus.FAIL if in_band else RuleStatus.PASS,
        f"delivery in {days} days vs review band {DELIVERY_REVIEW_MIN_DAYS} to {DELIVERY_REJECT_DAYS} days (inclusive)",
        triggering_value=f"{days} days", evidence_refs=[_ref("delivery days", doc.delivery_days)],
    )


def _nr05(doc: ExtractedDocument) -> RuleResult:
    """Presentation only (SPEC §5.2): never recalculates anything."""
    shown, rate = doc.vat_shown_separately, doc.vat_rate_pct
    shown_value, rate_value = _known(shown), _known(rate)
    reasons: list[str] = []
    refs: list[EvidenceRef] = []
    absences: list[AbsenceEvidence] = []
    if shown_value is False:
        reasons.append("VAT is not shown separately")
        refs.append(_ref("VAT shown separately", shown))
    elif _absent_complete(shown):
        reasons.append("the document says nothing about VAT being shown separately")
        absences.append(shown.absence_evidence)
    if rate_value is not None and rate_value != VAT_REQUIRED_RATE_PCT:
        reasons.append(f"printed VAT rate is {rate_value}, not {VAT_REQUIRED_RATE_PCT}")
        refs.append(_ref("printed VAT rate", rate))
    elif _absent_complete(rate):
        reasons.append("the document prints no VAT rate")
        absences.append(rate.absence_evidence)
    if reasons:
        return build_rule_result("NR-05", RuleStatus.FAIL, "; ".join(reasons),
                                 evidence_refs=refs, absence_evidence=absences)
    if shown_value is True and rate_value == VAT_REQUIRED_RATE_PCT:
        return build_rule_result("NR-05", RuleStatus.PASS, f"VAT shown separately at {VAT_REQUIRED_RATE_PCT}%",
                                 triggering_value=f"{rate_value}%",
                                 evidence_refs=[_ref("VAT shown separately", shown), _ref("printed VAT rate", rate)])
    return build_rule_result("NR-05", RuleStatus.UNCERTAIN,
                             "VAT presentation not established (shown-separately flag or printed rate unknown)")


def _nr06(checks: list[ArithmeticCheckResult]) -> RuleResult:
    """Derived only from the arithmetic results passed in; never recomputes (SPEC §5.2)."""
    if not checks:
        return build_rule_result("NR-06", RuleStatus.UNCERTAIN, "no arithmetic checks were provided; never PASS on an empty list")

    def name(check: ArithmeticCheckResult) -> str:
        return f"{check.check_name}[{check.item_id}]" if check.item_id else check.check_name

    failed = [c for c in checks if c.status == "FAIL"]
    if failed:
        return build_rule_result(
            "NR-06", RuleStatus.FAIL, "arithmetic mismatch: " + " | ".join(f"{name(c)}: {c.detail}" for c in failed),
            triggering_value=", ".join(name(c) for c in failed),
            evidence_refs=[ref for c in failed for ref in c.evidence_refs],  # EVERY failed check's refs
        )
    unknown = [c for c in checks if c.status == "UNKNOWN"]
    if unknown:
        return build_rule_result("NR-06", RuleStatus.UNCERTAIN,
                                 "arithmetic could not be fully verified: " + " | ".join(f"{name(c)}: {c.detail}" for c in unknown))
    return build_rule_result("NR-06", RuleStatus.PASS, f"all {len(checks)} arithmetic checks agree with the printed amounts",
                             evidence_refs=[ref for c in checks for ref in c.evidence_refs])


def _nr07(doc: ExtractedDocument) -> RuleResult:
    days = _known(doc.validity_days)
    if days is None:
        return build_rule_result("NR-07", RuleStatus.UNCERTAIN, _not_established("quotation validity in days"))
    return build_rule_result(
        "NR-07", RuleStatus.FAIL if days < VALIDITY_MIN_DAYS else RuleStatus.PASS,
        f"quotation valid for {days} days vs minimum {VALIDITY_MIN_DAYS} days",
        triggering_value=f"{days} days", evidence_refs=[_ref("quotation validity", doc.validity_days)],
    )


def _nr08(doc: ExtractedDocument) -> RuleResult:
    penalty = doc.penalty
    if _absent_complete(penalty):
        return build_rule_result(
            "NR-08", RuleStatus.NOT_APPLICABLE, "no penalty clause to evaluate",
            not_applicable_reason="The penalty clause is absent; there is no rate to evaluate. R-03 reports the absence.",
        )
    if not is_usable(penalty):
        return build_rule_result("NR-08", RuleStatus.UNCERTAIN, _not_established("the late-delivery penalty clause"))
    clause_ref = _ref("penalty clause", penalty)
    problems: list[str] = []
    assumptions: list[str] = []
    if penalty.period_unit != PENALTY_COMPARABLE_PERIOD:
        shown = penalty.period_unit if penalty.period_unit is not None else "unknown"
        problems.append(f"penalty period is '{shown}', not per week; no period conversion is implemented")
        assumptions.append(AMB_PENALTY_PERIOD)
    if penalty.calculation_basis is None or penalty.calculation_basis.strip().lower() != PENALTY_REQUIRED_BASIS:
        shown = penalty.calculation_basis if penalty.calculation_basis is not None else "unknown"
        problems.append(f"penalty calculation basis is '{shown}', not '{PENALTY_REQUIRED_BASIS}'")
    if penalty.rate_pct is None:
        problems.append("penalty rate is not established")
    if problems:
        return build_rule_result("NR-08", RuleStatus.UNCERTAIN, "; ".join(problems),
                                 evidence_refs=[clause_ref], assumptions=assumptions)
    status = RuleStatus.FAIL if penalty.rate_pct < PENALTY_MIN_RATE_PCT else RuleStatus.PASS
    return build_rule_result(
        "NR-08", status,
        f"penalty {penalty.rate_pct} percentage points per week of {PENALTY_REQUIRED_BASIS} vs minimum {PENALTY_MIN_RATE_PCT}",
        triggering_value=f"{penalty.rate_pct}% per week", evidence_refs=[clause_ref], assumptions=[AMB_PENALTY_PERIOD],
    )


# ---------------------------------------------------------------------------------------------
# Step 3 entry point
# ---------------------------------------------------------------------------------------------


def evaluate_all_rules(
    doc: ExtractedDocument, pr: PurchaseRequest, arithmetic_checks: list[ArithmeticCheckResult]
) -> list[RuleResult]:
    """Evaluate EVERY rule (never stops early). Deterministic order (M1 decision 11): per-item R-01 by line,
    per-item NR-01 by line, per-item NR-02 by line, then the global rules in rule-ID order.
    k >= 1 line items yield 3k + 10 results; k = 0 yields 3 + 10 (one UNCERTAIN item_id=None per per-item rule)."""
    return [
        *_per_item_results(doc, pr),
        _r02(doc), _r03(doc), _r04(doc), _r05(doc),
        _nr03(doc), _nr04(doc), _nr05(doc), _nr06(arithmetic_checks), _nr07(doc), _nr08(doc),
    ]


# ---------------------------------------------------------------------------------------------
# Step 4: aggregation (SPEC §5.4) with fail-closed coverage validation (M1 decision 10)
# ---------------------------------------------------------------------------------------------


def _coverage_problems(results: list[RuleResult]) -> list[str]:
    by_rule: dict[str, list[RuleResult]] = defaultdict(list)
    for result in results:
        by_rule[result.rule_id].append(result)
    problems: list[str] = []
    for rule_id in GLOBAL_RULES:
        found = by_rule.get(rule_id, [])
        if not found:
            problems.append(f"{rule_id} has no result")
        elif len(found) > 1:
            problems.append(f"{rule_id} appears {len(found)} times (expected exactly once)")
        elif found[0].item_id is not None:
            problems.append(f"{rule_id} is a global rule but carries item_id {found[0].item_id}")
    id_sets: dict[str, frozenset[str | None]] = {}
    for rule_id in PER_ITEM_RULES:
        found = by_rule.get(rule_id, [])
        if not found:
            problems.append(f"{rule_id} has no result")
            continue
        ids = [r.item_id for r in found]
        if len(ids) != len(set(ids)):
            problems.append(f"{rule_id} has duplicate item_id values")
        if None in ids and len(ids) > 1:
            problems.append(f"{rule_id} mixes an item_id=None result with per-line results")
        if None in ids and any(r.item_id is None and r.status != RuleStatus.UNCERTAIN for r in found):
            problems.append(f"{rule_id} has an item_id=None result that is not UNCERTAIN (only valid for an empty item list)")
        id_sets[rule_id] = frozenset(ids)
    if len(id_sets) == len(PER_ITEM_RULES) and len(set(id_sets.values())) > 1:
        problems.append("the per-item rules (R-01, NR-01, NR-02) do not cover the same set of line items")
    return problems


def _evidence_text(result: RuleResult) -> str:
    parts = [f'"{ref.source_quote}" (page {ref.page_number if ref.page_number is not None else "?"})'
             for ref in result.evidence_refs]
    parts += [f"absence of {a.field_searched}: {a.determination_basis}" for a in result.absence_evidence]
    return "; ".join(parts) if parts else "no evidence attached"


def _describe(result: RuleResult) -> str:
    where = f" [{result.item_id}]" if result.item_id else ""
    return f"- {result.rule_id}{where} {result.status.value}: {result.detail} | evidence: {_evidence_text(result)}"


def aggregate_decision(rule_results: list[RuleResult]) -> tuple[Decision, str]:
    """SPEC §5.4 over every result. A definite Reject FAIL always wins; otherwise incomplete rule coverage,
    any FAIL or any UNCERTAIN means NEEDS_REVIEW; APPROVE only when full coverage and nothing failed."""
    rejects = [r for r in rule_results if r.severity == RuleSeverity.REJECT and r.status == RuleStatus.FAIL]
    if rejects:
        return Decision.REJECT, "REJECT: definite Reject rule failure(s):\n" + "\n".join(_describe(r) for r in rejects)

    problems = _coverage_problems(rule_results)
    flagged = [r for r in rule_results if r.status in (RuleStatus.FAIL, RuleStatus.UNCERTAIN)]
    if problems or flagged:
        lines: list[str] = []
        if problems:
            lines.append("rule coverage is incomplete (fail-closed): " + "; ".join(problems))
        lines += [_describe(r) for r in flagged]
        return Decision.NEEDS_REVIEW, "NEEDS_REVIEW: a person must check:\n" + "\n".join(lines)

    return Decision.APPROVE, f"APPROVE: all {len(rule_results)} rule results passed (or were not applicable); none failed or were uncertain."


def collect_assumptions(rule_results: list[RuleResult]) -> list[str]:
    """De-duplicated, sorted union of assumptions_applied (pack-level list; used by format_output in M4)."""
    return sorted({a for r in rule_results for a in r.assumptions_applied})
