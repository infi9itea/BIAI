"""Typed builders shared by the M1 tests. Inputs are built from the real typed models; no test passes raw
strings to arithmetic.py or rule_engine.py. Totals are derived from the lines so a 'good' document is
internally consistent unless a test deliberately breaks it."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from src.models import (
    AbsenceEvidence,
    Evidence,
    ExtractedDocument,
    FieldRecord,
    FieldState,
    LineItem,
    LineItemField,
    PaymentTerms,
    PenaltyClause,
    PRItem,
    PurchaseRequest,
    StrictDecimal,
    StrictInt,
    VerificationStatus,
)

VERIFIED = VerificationStatus.VERIFIED
UNVERIFIED = VerificationStatus.UNVERIFIED
D = Decimal


def ev(quote: str = "quoted text", page: int = 1, status: VerificationStatus = VERIFIED) -> Evidence:
    return Evidence(source_quote=quote, page_number=page, verification_status=status)


def absence(field: str = "tin", complete: bool = True) -> AbsenceEvidence:
    return AbsenceEvidence(
        field_searched=field, search_terms=["TIN", "Tax Identification"], pages_scanned=[1],
        extraction_complete=complete, contextual_excerpt="Supplier registration details available on request.",
        determination_basis="searched all pages for the field and its synonyms",
    )


_TYPES = {"dec": StrictDecimal, "int": StrictInt, "bool": bool, "str": str}


def fld(kind: str, value: Any, quote: str | None = None, status: VerificationStatus = VERIFIED, line: bool = False):
    """A FOUND field (default VERIFIED). kind: dec | int | bool | str."""
    cls = (LineItemField if line else FieldRecord)[_TYPES[kind]]
    return cls(state=FieldState.FOUND, value=value, evidence=ev(quote or f"{kind}:{value}", status=status))


def unknown(kind: str, line: bool = False):
    return (LineItemField if line else FieldRecord)[_TYPES[kind]]()


def absent(kind: str, field: str, line: bool = False):
    return (LineItemField if line else FieldRecord)[_TYPES[kind]](
        state=FieldState.ABSENT, absence_evidence=absence(field)
    )


def money(x: Any) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def line(
    code: str | None = "A", qty: Any = "12000", price: Any = "472.00", unit: str | None = "metre",
    total: Any = None, vat_included: Any = False, code_match: str = "YES", unit_match: str = "YES", **overrides: Any,
) -> LineItem:
    """A line item. Each argument may be a plain value (wrapped as FOUND/VERIFIED), None (UNKNOWN) or a ready field."""

    def wrap(kind: str, value: Any, label: str):
        if value is None:
            return unknown(kind, line=True)
        if hasattr(value, "state"):
            return value
        return fld(kind, D(value) if kind == "dec" else value, f"line {label}: {value}", line=True)

    if total is None and qty is not None and price is not None and not hasattr(qty, "state") and not hasattr(price, "state"):
        total = money(D(qty) * D(price))
    fields = dict(
        item_code=wrap("str", code, "code"), description=fld("str", "item description", "item description", line=True),
        quantity=wrap("dec", qty, "qty"), unit=wrap("str", unit, "unit"),
        unit_price_bdt=wrap("dec", price, "price"), line_total_bdt=wrap("dec", total, "total"),
        vat_included_in_price=wrap("bool", vat_included, "vat included"),
        item_code_match=code_match, description_match="YES", unit_match=unit_match,
    )
    fields.update(overrides)
    return LineItem(**fields)


def good_penalty(rate: Any = "1", period: str | None = "week", basis: str | None = "order value", **kw: Any) -> PenaltyClause:
    return PenaltyClause(
        state=FieldState.FOUND, original_wording="Late delivery penalty clause", rate_pct=None if rate is None else D(rate),
        calculation_basis=basis, period_unit=period, full_weeks_only=True,
        evidence=ev("Late delivery penalty: 1% of order value for each full week of delay."), **kw,
    )


def good_payment(min_days: Any = 45, max_days: Any = None, anchor: str | None = "delivery", discretionary: Any = False,
                 state: FieldState = FieldState.FOUND, **kw: Any) -> PaymentTerms:
    return PaymentTerms(
        state=state, original_wording="Payment within 45 days of delivery.",
        min_days=min_days, max_days=min_days if max_days is None else max_days, anchor=anchor,
        discretionary=discretionary, evidence=ev("Balance payable within 45 days after delivery."), **kw,
    )


def make_doc(lines: list[LineItem] | None = None, **overrides: Any) -> ExtractedDocument:
    """A fully consistent, approvable single-item document (Padma-like). Override any top-level field."""
    lines = [line()] if lines is None else lines
    subtotal = sum((money(D(str(l.line_total_bdt.value))) for l in lines if l.line_total_bdt.value is not None), D("0"))
    vat = money(subtotal * D("0.15"))
    fields: dict[str, Any] = dict(
        supplier_name=fld("str", "Test Supplier Ltd", "Test Supplier Ltd"),
        tin=fld("str", "101458013391", "Supplier TIN: 101458013391"),
        bin=fld("str", "000170141-0101", "BIN: 000170141-0101"),
        line_items=lines,
        subtotal_bdt=fld("dec", subtotal, f"Subtotal {subtotal}"),
        vat_rate_pct=fld("dec", D("15"), "VAT 15%"),
        vat_amount_bdt=fld("dec", vat, f"VAT 15% {vat}"),
        grand_total_bdt=fld("dec", subtotal + vat, f"Total {subtotal + vat}"),
        vat_shown_separately=fld("bool", True, "VAT 15% shown as its own line"),
        vat_price_basis=fld("str", "subtotal", "VAT 15% under Subtotal"),
        delivery_original_wording=fld("str", "Delivery within 24 days of PO", "Delivery within 24 days of PO"),
        delivery_days=fld("int", 24, "Delivery within 24 days of PO"),
        payment=good_payment(),
        advance_payment_pct=fld("dec", D("10"), "10% advance payable with the purchase order"),
        advance_original_wording="10% advance payable with the purchase order",
        penalty=good_penalty(),
        validity_original_wording=fld("str", "valid for 45 days", "valid for 45 days"),
        validity_days=fld("int", 45, "This quotation is valid for 45 days"),
    )
    fields.update(overrides)
    return ExtractedDocument(**fields)


def make_pr() -> PurchaseRequest:
    rows = [
        ("A", "Cotton twill fabric, 280 gsm", "metre", "12000", "480.00"),
        ("B", "Metal zipper, 18 cm", "piece", "20000", "38.00"),
        ("C", "Polyester sewing thread, cone", "cone", "800", "210.00"),
        ("D", "Poly bag, 30 x 40 cm", "piece", "50000", "6.50"),
        ("E", "Carton box, 5-ply", "piece", "3000", "95.00"),
    ]
    return PurchaseRequest(
        pr_number="PR-2026-114",
        items={c: PRItem(item_code=c, description=d, unit=u, quantity_requested=D(q), max_unit_price_bdt=D(p))
               for c, d, u, q, p in rows},
    )
