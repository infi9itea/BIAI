"""Arithmetic checks (Milestone 1; SPEC §9, AMB-09).

Pure Python, ``Decimal`` only, no LLM, no rule logic, no decisions. Reads only the typed, verified
``ExtractedDocument``: every field goes through ``is_usable`` and anything that is not usable (UNKNOWN,
ABSENT, or a FOUND candidate that is still UNVERIFIED) is treated as UNKNOWN (SPEC §3.1 rule 5).

Rounding policy (AMB-09, M1 review decision 3):
  * each recalculated line amount is rounded to 2 dp with ROUND_HALF_UP;
  * the independent subtotal is the sum of those rounded line amounts, never of the printed line totals;
  * the expected VAT is rounded to 2 dp with ROUND_HALF_UP;
  * the expected grand total is independent subtotal + rounded expected VAT;
  * every comparison is ``abs(calculated - printed) <= ARITHMETIC_TOLERANCE_BDT``; printed values are
    never modified.

Evidence propagation (M1 review): a check's ``evidence_refs`` hold the verified quote of the printed value
AND of every FOUND input the calculation used, so NR-06 can show a complete audit trail.
"""

from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
from typing import Any

from src.models import AMB_VAT_SUBTOTAL, ArithmeticCheckResult, EvidenceRef, ExtractedDocument, is_usable

AMB_ARITHMETIC_ROUNDING = "AMB-09"  # [A] rounding (ROUND_HALF_UP, 2 dp) and tolerance; recorded on every computed result

ARITHMETIC_TOLERANCE_BDT = Decimal("0.01")  # [A] AMB-09: ±0.01 BDT, inclusive (0.01 off passes, 0.02 off fails)
VAT_BASIS_SUBTOTAL = "subtotal"  # [A] canonical vat_price_basis that permits the VAT check (M1 review decision 4)

_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")
# Own context so results never depend on the caller's global Decimal context.
_CTX = Context(prec=60, rounding=ROUND_HALF_UP)


def _known(field: Any) -> Any:
    """The typed value of a usable field, else None (None means UNKNOWN). Never returns an unverified value."""
    if is_usable(field) and field.value is not None:
        return field.value
    return None


def _ref(label: str, field: Any) -> EvidenceRef:
    """Verified quote of a usable field. Call only for fields that passed ``_known``."""
    evidence = field.evidence
    return EvidenceRef(label=label, source_quote=evidence.source_quote, page_number=evidence.page_number)


def _with_printed(refs: list[EvidenceRef], label: str, field: Any) -> list[EvidenceRef]:
    """refs plus the printed field's own quote, whenever that field is usable. A result that shows a printed value
    must show where it came from, even when the calculation itself is UNKNOWN."""
    return [*refs, _ref(label, field)] if _known(field) is not None else list(refs)


def _q2(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP, context=_CTX)


def _line_amount(quantity: Decimal, unit_price: Decimal) -> Decimal:
    return _q2(_CTX.multiply(quantity, unit_price))


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _result(
    check_name: str,
    item_id: str | None,
    status: str,
    detail: str,
    calculated: Decimal | None = None,
    printed: Decimal | None = None,
    refs: list[EvidenceRef] | None = None,
    assumptions: list[str] | None = None,
) -> ArithmeticCheckResult:
    return ArithmeticCheckResult(
        check_name=check_name,
        item_id=item_id,
        calculated=calculated,
        printed=printed,
        status=status,  # type: ignore[arg-type]
        detail=detail,
        evidence_refs=list(refs or []),
        assumptions_applied=_unique([*(assumptions or []), *([AMB_ARITHMETIC_ROUNDING] if calculated is not None else [])]),
    )


def _compare(
    check_name: str,
    item_id: str | None,
    label: str,
    calculated: Decimal,
    printed_field: Any,
    printed_label: str,
    input_refs: list[EvidenceRef],
    assumptions: list[str] | None = None,
) -> ArithmeticCheckResult:
    """PASS/FAIL only when the printed value is usable too; otherwise UNKNOWN (SPEC §9)."""
    printed = _known(printed_field)
    if printed is None:
        return _result(
            check_name, item_id, "UNKNOWN",
            f"{label}: {printed_label} is not usable (unknown, absent or unverified); calculated {calculated}",
            calculated=calculated, refs=input_refs, assumptions=assumptions,
        )
    refs = [*input_refs, _ref(printed_label, printed_field)]
    difference = _CTX.abs(_CTX.subtract(calculated, printed))
    status = "PASS" if difference <= ARITHMETIC_TOLERANCE_BDT else "FAIL"
    detail = (
        f"{label}: calculated {calculated} vs printed {printed} "
        f"(difference {difference}, tolerance {ARITHMETIC_TOLERANCE_BDT})"
    )
    return _result(check_name, item_id, status, detail, calculated=calculated, printed=printed, refs=refs,
                   assumptions=assumptions)


# ---------------------------------------------------------------------------------------------
# Check 1: per-line recalculation
# ---------------------------------------------------------------------------------------------


def _check_line(position: int, item: Any) -> ArithmeticCheckResult:
    item_id = f"line-{position}"
    inputs = (("quantity", item.quantity), ("unit_price_bdt", item.unit_price_bdt))
    refs = [_ref(f"{item_id} {name}", field) for name, field in inputs if _known(field) is not None]
    missing = [name for name, field in (*inputs, ("line_total_bdt", item.line_total_bdt)) if _known(field) is None]

    quantity, unit_price = _known(item.quantity), _known(item.unit_price_bdt)
    calculated = None
    if quantity is not None and unit_price is not None:
        try:
            calculated = _line_amount(quantity, unit_price)
        except InvalidOperation:
            return _result("line_recalc", item_id, "UNKNOWN", f"{item_id}: value out of range, cannot recalculate", refs=refs)

    if missing:
        printed = _known(item.line_total_bdt)
        if printed is not None:
            refs = [*refs, _ref(f"{item_id} printed line total", item.line_total_bdt)]
        return _result(
            "line_recalc", item_id, "UNKNOWN",
            f"{item_id}: cannot check; not usable: {', '.join(missing)}",
            calculated=calculated, printed=printed, refs=refs,
        )
    return _compare("line_recalc", item_id, f"{item_id} quantity × unit price", calculated,
                    item.line_total_bdt, f"{item_id} printed line total", refs)


# ---------------------------------------------------------------------------------------------
# Check 2: independent subtotal (no partial sums)
# ---------------------------------------------------------------------------------------------


def _independent_subtotal(doc: ExtractedDocument) -> tuple[Decimal | None, list[EvidenceRef], list[str]]:
    """(subtotal or None, refs of every usable quantity/price, problems). Never a partial sum."""
    if not doc.line_items:
        return None, [], ["no line items could be extracted"]
    total = Decimal("0")
    refs: list[EvidenceRef] = []
    problems: list[str] = []
    for position, item in enumerate(doc.line_items, start=1):
        item_id = f"line-{position}"
        quantity, unit_price = _known(item.quantity), _known(item.unit_price_bdt)
        for name, field, value in (("quantity", item.quantity, quantity), ("unit_price_bdt", item.unit_price_bdt, unit_price)):
            if value is None:
                problems.append(f"{item_id} {name}")
            else:
                refs.append(_ref(f"{item_id} {name}", field))
        if quantity is not None and unit_price is not None and not problems:
            try:
                total = _CTX.add(total, _line_amount(quantity, unit_price))
            except InvalidOperation:
                problems.append(f"{item_id} value out of range")
    if problems:
        return None, refs, problems
    return total, refs, []


# ---------------------------------------------------------------------------------------------
# Checks 3 and 4: VAT and grand total (use the PRINTED rate, never a hard-coded 15)
# ---------------------------------------------------------------------------------------------


def _basis_assumptions(doc: ExtractedDocument) -> list[str]:
    """['AMB-15'] when the usable VAT basis was established by the totals-block layout and not by words (the verifier
    records this in the evidence's normalization_method). The VAT and grand-total checks rest on it, so they say so."""
    basis = doc.vat_price_basis
    if _known(basis) is None:
        return []
    method = basis.evidence.normalization_method or ""
    return [AMB_VAT_SUBTOTAL] if method.startswith(AMB_VAT_SUBTOTAL) else []


def _expected_vat(
    doc: ExtractedDocument, subtotal: Decimal | None, subtotal_problems: list[str], subtotal_refs: list[EvidenceRef]
) -> tuple[Decimal | None, list[EvidenceRef], list[str]]:
    problems: list[str] = []
    refs = list(subtotal_refs)
    if subtotal is None:
        problems.append("independent subtotal unavailable (" + "; ".join(subtotal_problems) + ")")
    rate = _known(doc.vat_rate_pct)
    if rate is None:
        problems.append("printed VAT rate is not usable")
    else:
        refs.append(_ref("printed VAT rate", doc.vat_rate_pct))
    basis = _known(doc.vat_price_basis)
    if basis is None:
        problems.append("VAT price basis is not usable")
    else:
        refs.append(_ref("VAT price basis", doc.vat_price_basis))
        if basis.strip().lower() != VAT_BASIS_SUBTOTAL:
            problems.append(f"VAT price basis is {basis!r}, not the canonical '{VAT_BASIS_SUBTOTAL}'")
    if problems:
        return None, refs, problems
    try:
        expected = _q2(_CTX.multiply(subtotal, _CTX.divide(rate, _HUNDRED)))
    except InvalidOperation:
        return None, refs, ["value out of range"]
    return expected, refs, []


def run_arithmetic_checks(doc: ExtractedDocument) -> list[ArithmeticCheckResult]:
    """SPEC §9. Order: one line check per line, then subtotal, VAT, grand total."""
    results = [_check_line(position, item) for position, item in enumerate(doc.line_items, start=1)]

    subtotal, subtotal_refs, subtotal_problems = _independent_subtotal(doc)
    if subtotal is None:
        printed_subtotal = _known(doc.subtotal_bdt)
        results.append(_result(
            "independent_subtotal", None, "UNKNOWN",
            "independent subtotal cannot be calculated: " + "; ".join(subtotal_problems),
            printed=printed_subtotal, refs=_with_printed(subtotal_refs, "printed subtotal", doc.subtotal_bdt),
        ))
    else:
        results.append(_compare("independent_subtotal", None, "independent subtotal (sum of recalculated lines)",
                                subtotal, doc.subtotal_bdt, "printed subtotal", subtotal_refs))

    expected_vat, vat_refs, vat_problems = _expected_vat(doc, subtotal, subtotal_problems, subtotal_refs)
    assumptions = _basis_assumptions(doc)  # decided BEFORE the early return: an UNKNOWN result still used the basis
    if expected_vat is None:
        results.append(_result(
            "vat", None, "UNKNOWN", "VAT check cannot be calculated: " + "; ".join(vat_problems),
            printed=_known(doc.vat_amount_bdt), refs=_with_printed(vat_refs, "printed VAT amount", doc.vat_amount_bdt),
            assumptions=assumptions,
        ))
        results.append(_result(
            "grand", None, "UNKNOWN", "grand total cannot be calculated: expected VAT unavailable",
            printed=_known(doc.grand_total_bdt), refs=_with_printed(vat_refs, "printed grand total", doc.grand_total_bdt),
            assumptions=assumptions,
        ))
        return results

    results.append(_compare("vat", None, "VAT (independent subtotal × printed rate)", expected_vat,
                            doc.vat_amount_bdt, "printed VAT amount", vat_refs, assumptions))
    expected_grand = _CTX.add(subtotal, expected_vat)
    results.append(_compare("grand", None, "grand total (independent subtotal + expected VAT)", expected_grand,
                            doc.grand_total_bdt, "printed grand total", vat_refs, assumptions))
    return results
