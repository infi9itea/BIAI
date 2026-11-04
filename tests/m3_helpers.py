"""Shared helpers for the M3 tests (extractor, verifier). Everything here is synthetic test data: a made-up
quotation, a made-up model response, and a fake Gemini client. No test in this milestone calls the real API."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from src.models import PRItem, PurchaseRequest, RawExtraction
from src.pdf_reader import PAGE_DELIMITER_TEMPLATE


def make_pr() -> PurchaseRequest:
    rows = [
        ("A", "Cotton twill fabric, 280 gsm", "metre", "12000", "480.00"),
        ("B", "Metal zipper, 18 cm", "piece", "20000", "38.00"),
        ("C", "Polyester sewing thread, cone", "cone", "800", "210.00"),
        ("D", "Poly bag, 30 x 40 cm", "piece", "50000", "6.50"),
        ("E", "Carton box, 5-ply", "piece", "3000", "95.00"),
    ]
    items = {c: PRItem(item_code=c, description=d, unit=u, quantity_requested=Decimal(q), max_unit_price_bdt=Decimal(p))
             for c, d, u, q, p in rows}
    return PurchaseRequest(pr_number="PR-TEST", items=items)


def make_pdf_result(page_texts: list[str]) -> dict[str, Any]:
    pages = [{"page_number": i, "text": t} for i, t in enumerate(page_texts, start=1)]
    full = "".join(PAGE_DELIMITER_TEMPLATE.format(n=p["page_number"]) + p["text"] for p in pages)
    return {"full_text": full, "pages": pages, "page_count": len(pages)}


ROW = "A Cotton twill fabric, 280 gsm 12,000 metre 472.00 5,664,000.00"

SYNTH_TEXT = f"""Acme Mills Ltd
QUOTATION
To Buyer Co (fictional) Reference PR-TEST
Item Description Qty Unit Unit price (BDT) Amount (BDT)
{ROW}
Subtotal 5,664,000.00
VAT 15% 849,600.00
Total 6,513,600.00
Supplier TIN: 101458013391 BIN: 000170141-0101
TERMS AND CONDITIONS
1. Payment. 10% advance payable with the purchase order. Balance payable within 45 days after delivery.
2. Delivery. Delivery within 24 days of receiving the purchase order.
3. Delay. Late delivery penalty: 1% of order value for each full week of delay.
4. Validity. This quotation is valid for 45 days from the date above.
5. Quality. Goods are supplied as per the buyer's approved sample."""


def f(value: str | None, quote: str | None, page: int | None = 1) -> dict[str, Any]:
    return {"value": value, "source_quote": quote, "page_number": page}


NULL = {"value": None, "source_quote": None, "page_number": None}

PENALTY_QUOTE = "Late delivery penalty: 1% of order value for each full week of delay."
PAYMENT_QUOTE = "Balance payable within 45 days after delivery."

# What a well-behaved model would return for SYNTH_TEXT.
GOOD_RAW: dict[str, Any] = {
    "supplier_name": f("Acme Mills Ltd", "Acme Mills Ltd"),
    "tin": f("101458013391", "Supplier TIN: 101458013391"),
    "bin": f("000170141-0101", "BIN: 000170141-0101"),
    "line_items": [{
        "item_code": f("A", ROW), "description": f("Cotton twill fabric, 280 gsm", ROW),
        "quantity": f("12,000", ROW), "unit": f("metre", ROW), "unit_price_bdt": f("472.00", ROW),
        "line_total_bdt": f("5,664,000.00", ROW),
        "vat_included_in_price": NULL,
    }],
    "subtotal_bdt": f("5,664,000.00", "Subtotal 5,664,000.00"),
    "vat_rate_pct": f("15", "VAT 15% 849,600.00"),
    "vat_amount_bdt": f("849,600.00", "VAT 15% 849,600.00"),
    "grand_total_bdt": f("6,513,600.00", "Total 6,513,600.00"),
    "vat_shown_separately": f("yes", "VAT 15% 849,600.00"),
    "vat_price_basis": f("subtotal", "Subtotal 5,664,000.00 VAT 15% 849,600.00"),
    "delivery_period": f("24 days", "Delivery within 24 days of receiving the purchase order."),
    "payment_terms_wording": f(PAYMENT_QUOTE, PAYMENT_QUOTE),
    "payment_period": f("45 days", PAYMENT_QUOTE),
    "advance_payment_pct": f("10", "10% advance payable with the purchase order."),
    "penalty_wording": f(PENALTY_QUOTE, PENALTY_QUOTE),
    "penalty_rate_pct": f("1", PENALTY_QUOTE),
    "penalty_calculation_basis": f("order value", PENALTY_QUOTE),
    "penalty_period": f("each full week of delay", PENALTY_QUOTE),
    "penalty_full_weeks_only": f("yes", PENALTY_QUOTE),
    "validity_period": f("45 days", "This quotation is valid for 45 days from the date above."),
}


def raw_with(**overrides: Any) -> RawExtraction:
    """GOOD_RAW with some fields replaced. Pass a dict for a field, or NULL to blank it out."""
    data = deepcopy(GOOD_RAW)
    data.update(overrides)
    return RawExtraction.model_validate(data)


def with_line(**overrides: Any) -> dict[str, Any]:
    """A copy of the good line item with some sub-fields replaced (for raw_with(line_items=[...]))."""
    line = deepcopy(GOOD_RAW["line_items"][0])
    line.update(overrides)
    return line


class FakeClient:
    """Stands in for google.genai.Client. Each queued response is a JSON string (returned as .text) or an
    Exception (raised). Records every call's keyword arguments."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.models = self

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(text=response)
