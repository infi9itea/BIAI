"""Evidence verification (Milestone 3).

``verify_evidence`` is the only code allowed to set ``VerificationStatus.VERIFIED`` (SPEC §3.1 rule 5). The model
supplied candidate values and quotes; nothing here trusts them. A candidate (FOUND + UNVERIFIED) is promoted only
after ALL of these checks pass; otherwise it becomes UNKNOWN with its typed value cleared (the Evidence, including
``raw_value``, stays for audit):

1. the quote appears in the PDF text (Unicode-normalised, whitespace-collapsed; nothing is rewritten, no space is
   ever inserted, so a quote must reproduce joined text exactly);
2. field-specific parsing: the value parsed FROM THE QUOTE (using the field's label and layout, not just any digits)
   equals the typed value;
3. no conflict: the quote does not support two different values, and a document-wide scan finds one value only.
   The same value stated twice is not a conflict.

Absence (``ABSENT``) is decided here, never by the model: a null answer from the model is not evidence. A field the
model did not claim at all is ABSENT only if an independent search of every page finds none of the field's search
terms, and an ``AbsenceEvidence`` record documents that search. If any term is found, or the model claimed a value
that failed verification, the field stays UNKNOWN (when in doubt, UNKNOWN).

AMB-15 (VAT basis from the totals layout, SPEC §13): a model is asked for the wording that states what VAT applies
to, never to infer it from row order. When the field did not verify from words, the verifier itself looks for the
plain totals block (contiguous Subtotal row, VAT row, Total row, nothing else on them) in the PDF's own lines and, only
if that block is unique and nothing in the document limits or contradicts it, sets ``vat_price_basis = "subtotal"``
with the block's three real rows as the evidence quote and ``AMB-15`` in ``normalization_method``.

Known limit (inherited from M2): a page that mixes extractable text with a scanned image of more text looks
complete. ABSENT conclusions therefore only cover the extractable text. OCR is out of scope.
"""

import re
from dataclasses import dataclass
from typing import Any, Callable

from src.models import (
    AMB_VAT_SUBTOTAL,
    AbsenceEvidence,
    Evidence,
    ExtractedDocument,
    FieldState,
    LineItem,
    PaymentTerms,
    PenaltyClause,
    VerificationStatus,
)
from src.parsing import (
    NUMBER_PATTERN,
    PERIOD_IN_TEXT,
    VAT_SCOPE_LIMIT,
    discretionary_from_text,
    normalize_text,
    penalty_period_unit,
    periods_in_text,
    sentences,
    to_decimal,
    to_pct_points,
    unit_key,
)

BUYER_NAME_FRAGMENT = "aamar garments"  # the buyer is never the supplier

_VERIFIED = VerificationStatus.VERIFIED
_UNVERIFIED = VerificationStatus.UNVERIFIED
_CONFLICT = VerificationStatus.CONFLICT

# --------------------------------------------------------------------------------------
# The document as the verifier sees it
# --------------------------------------------------------------------------------------

_PAGE_MARKER = re.compile(r"\n?--- PAGE (\d+) ---\n?")


@dataclass
class _Doc:
    pages: list[tuple[int, str]]  # (page number, whitespace-collapsed text)
    text: str  # all pages joined by one space (no page markers): used for document-wide scans
    lines: list[str]  # the PDF's own text lines (line breaks kept): only used to quote real context
    page_lines: list[tuple[int, list[str]]]  # (page number, that page's non-empty lines): row adjacency (AMB-15)

    @classmethod
    def from_pdf_result(cls, pdf_result: dict[str, Any]) -> "_Doc":
        raw_pages = pdf_result.get("pages")
        if raw_pages:
            sources = [(int(p["page_number"]), p["text"]) for p in raw_pages]
        else:  # fall back to splitting full_text on the M2 page markers
            parts = _PAGE_MARKER.split(pdf_result["full_text"])
            sources = [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
        pages = [(number, normalize_text(text)) for number, text in sources]
        lines = [line.strip() for _, text in sources for line in text.splitlines() if line.strip()]
        page_lines = [(number, [line.strip() for line in text.splitlines() if line.strip()]) for number, text in sources]
        return cls(pages=pages, text=" ".join(text for _, text in pages), lines=lines, page_lines=page_lines)

    def locate(self, quote: str) -> list[int]:
        """Pages whose text contains the quote. A quote that holds a page marker is never found."""
        needle = normalize_text(quote)
        if not needle or "--- PAGE" in needle:
            return []
        return [number for number, text in self.pages if needle in text]


# --------------------------------------------------------------------------------------
# Field-specific parsers. Each returns the set of values the QUOTE supports for that field.
# --------------------------------------------------------------------------------------

_AMT = rf"({NUMBER_PATTERN})(?!\d|[,.]\d|\s*%)"  # a full amount, never the front of a longer number or a %
_CUR = r"(?:BDT|Tk\.?|৳)?"
_ID = r"((?=[A-Za-z0-9/-]*\d)[A-Za-z0-9][A-Za-z0-9/-]*)"  # an identifier that contains a digit

_TIN_LABEL = r"(?:\bTIN\b|(?i:tax(?:payer)?\s+identification(?:\s+number)?))"
_BIN_LABEL = r"(?:\bBIN\b|(?i:business\s+identification(?:\s+number)?))"


def _labelled_ids(label: str, text: str) -> set[str]:
    return {m.group(1).upper() for m in re.finditer(label + r"[\s:#.–-]*" + _ID, text)}


_SUBTOTAL = re.compile(rf"(?i)\bsub-?total\b\s*(?:\([^)]*\))?\s*{_CUR}\s*:?\s*{_AMT}")
_VAT_AMOUNT = re.compile(
    rf"(?i)\b(?:VAT|value\s+added\s+tax)\b\s*(?:\([^)]*\)|@)?\s*(?:{NUMBER_PATTERN}\s*%)?\s*{_CUR}\s*:?\s*{_AMT}"
)
_GRAND_TOTAL = re.compile(rf"(?i)(?<![\w-])(?<!line )(?<!item )(?<!row )(?:grand\s+)?total\b\s*(?:\([^)]*\))?\s*{_CUR}\s*:?\s*{_AMT}")
_VAT_RATE = re.compile(
    rf"(?i)\b(?:VAT|value\s+added\s+tax)\b\s*(?:@|at|of|rate)?\s*:?\s*({NUMBER_PATTERN})\s*%|({NUMBER_PATTERN})\s*%\s*(?:VAT|value\s+added\s+tax)\b"
)
_VAT_ROW = re.compile(r"(?i)\b(?:VAT|value\s+added\s+tax)\b[^A-Za-z]{0,20}\d")
_VAT_INCLUDED = re.compile(
    r"(?i)\b(?:VAT|taxes|tax)\s+(?:is\s+|are\s+)?(?:included|inclusive)\b|inclusive\s+of\s+(?:VAT|all\s+taxes?)|"
    r"\b(?:including|incl\.?)\s+(?:VAT|all\s+taxes?)|\ball\s+taxes\s+included\b"
)
_VAT_EXCLUDED = re.compile(
    r"(?i)\b(?:excluding|excl\.?|exclusive\s+of)\s+VAT\b|\bVAT\s+(?:excluded|extra|not\s+included)\b|\bplus\s+VAT\b"
)
_ADVANCE_PCT = (
    re.compile(rf"(?i)({NUMBER_PATTERN})\s*%\s*(?:as\s+)?advance\b"),
    re.compile(rf"(?i)\badvance(?:\s+payment)?\s*(?:of|:|-|–|is)?\s*({NUMBER_PATTERN})\s*%"),
)
_NO_ADVANCE = re.compile(r"(?i)\b(?:no|nil|zero|without)\s+advance\b")
_PO_ANCHOR = re.compile(
    r"(?i)\bPO\b|\bP\.O\.|\bpurchase\s+order\b|\bwork\s+order\b|\b(?:of|from|after)\s+(?:the\s+)?(?:date\s+of\s+)?order\b"
)
# Each is a CLAUSE pattern: the field's own wording, then its period. A number elsewhere (a warranty, a document
# deadline) never matches, so a quote cannot verify a field just by containing the right digits.
# From the delivery word to the end of ITS clause: a full stop or a semicolon ends it, so a period or a PO anchor
# in the next clause (a document deadline, a warranty) is never borrowed.
_DELIVERY_CLAUSE = re.compile(r"(?i)\bdeliver\w*\b[^.;]*")
_CLAUSE_PERIOD = re.compile(r"(?i)\b(?:within|in|after)\s+(\d+)\s*(days?|weeks?)\b")
_VALIDITY_QUOTATION = re.compile(  # "This quotation is valid for 45 days"
    r"(?i)\b(?:quotation|quote|offer|prices?)\b[^.]{0,40}?\bvalid\w*\b[^.\d]{0,40}(\d+)\s*(days?|weeks?)\b"
)
_VALIDITY_LABEL = re.compile(r"(?i)\bvalidity\b[^.\d]{0,40}(\d+)\s*(days?|weeks?)\b")  # "Validity period: 45 days"
_PAYMENT_WORDS = re.compile(r"(?i)\b(?:payment|balance|payable)\b")
_PAYMENT_DOC = re.compile(
    r"(?i)\b(?:payment|balance|payable)\b[^.;]{0,80}?\b(?:within|in|after)\s+(\d+)(?:\s*(?:to|-|–)\s*(\d+))?\s*(days?|weeks?)\b"
)


def _decimals(pattern: re.Pattern[str], text: str, group: int = 1) -> set:
    values = set()
    for match in pattern.finditer(text):
        raw = match.group(group)
        number = to_decimal(raw) if raw else None
        if number is not None:
            values.add(number)
    return values


def _vat_rates(text: str) -> set:
    values = set()
    for match in _VAT_RATE.finditer(text):
        number = to_pct_points(match.group(1) or match.group(2))
        if number is not None:
            values.add(number)
    return values


def _advance_values(text: str) -> set:
    values: set = set()
    for pattern in _ADVANCE_PCT:
        values |= _decimals(pattern, text)
    if _NO_ADVANCE.search(text):
        values.add(to_decimal("0"))
    return values


def _days_from_doc(pattern: re.Pattern[str], text: str) -> set[int]:
    factor = lambda unit: 7 if unit.lower().startswith("week") else 1  # noqa: E731
    return {int(m.group(1)) * factor(m.group(2)) for m in pattern.finditer(text)}


def _payment_bounds(text: str) -> set[tuple[int, int]]:
    """(min, max) days of every PAYMENT clause in the text (payment/balance/payable wording + period)."""
    found = set()
    for m in _PAYMENT_DOC.finditer(text):
        factor = 7 if m.group(3).lower().startswith("week") else 1
        low = int(m.group(1)) * factor
        high = int(m.group(2)) * factor if m.group(2) else low
        if low <= high:
            found.add((low, high))
    return found


def _parse_row(quote: str) -> dict[str, Any] | None:
    """A table row 'code description... qty unit unit-price amount' -> its parts, else None.

    The last four tokens are qty, unit, unit price, amount; the first token is the item code; the rest is the
    description. Anything that does not fit (a header, a fragment, a multi-word unit) is not a verifiable row."""
    tokens = normalize_text(quote).split(" ")
    if len(tokens) < 6:
        return None
    qty, unit, price, amount = (to_decimal(tokens[-4]), tokens[-3], to_decimal(tokens[-2]), to_decimal(tokens[-1]))
    if qty is None or price is None or amount is None or re.search(r"\d", unit):
        return None
    return {"code": tokens[0], "description": " ".join(tokens[1:-4]), "quantity": qty, "unit": unit,
            "unit_price": price, "amount": amount}


def _key_text(value: str) -> str:
    return normalize_text(value).casefold()


@dataclass(frozen=True)
class _Spec:
    key: Callable[[Any], Any]  # typed value -> comparable key
    supports: Callable[[Any, str], set]  # (typed value, quote) -> keys the quote supports
    doc_values: Callable[[str], set] | None = None  # document-wide scan: all distinct keys found


def _contains(value: str, quote: str) -> set:
    return {_key_text(value)} if _key_text(value) in _key_text(quote) else set()


def _supplier_supports(value: str, quote: str) -> set:
    if BUYER_NAME_FRAGMENT in _key_text(value):
        return set()  # the buyer's own name on the quotation is not the supplier
    return _contains(value, quote)


def _delivery_supports(_value: Any, quote: str) -> set:
    """Days the quote promises as DELIVERY time counted from the PO (AMB-07). It needs delivery wording, the period
    and a PO anchor in the same clause; a PO date in some other clause (e.g. 'submit documents within 24 days of
    PO') supports nothing."""
    return _delivery_days(quote, require_po=True)


def _delivery_days(text: str, require_po: bool) -> set[int]:
    """Every period stated inside a delivery clause (so two promises in one sentence are two values)."""
    found: set[int] = set()
    for clause in _DELIVERY_CLAUSE.finditer(text):
        if require_po and not _PO_ANCHOR.search(clause.group(0)):
            continue
        found |= {int(m.group(1)) * (7 if m.group(2).lower().startswith("week") else 1)
                  for m in _CLAUSE_PERIOD.finditer(clause.group(0))}
    return found


def _validity_days(text: str) -> set[int]:
    """Day counts of a QUOTATION-validity clause. A warranty or any other 'valid'/'period' sentence is ignored."""
    found: set[int] = set()
    for pattern in (_VALIDITY_QUOTATION, _VALIDITY_LABEL):
        for m in pattern.finditer(text):
            found.add(int(m.group(1)) * (7 if m.group(2).lower().startswith("week") else 1))
    return found


def _wording_supports(clause_supports: Callable[[Any, str], set]) -> Callable[[str, str], set]:
    """An 'original wording' text field verifies only from the same clause that supports its period."""
    return lambda value, quote: _contains(value, quote) if clause_supports(None, quote) else set()


_VAT_CLAUSE = re.compile(r"(?i)\bVAT\b(?:[^;.]|\.(?=\d)){0,80}")  # a VAT mention and what follows it, to the clause end
_VAT_ON_SUBTOTAL = re.compile(  # words that STATE the relationship: "VAT @ 15% on the subtotal"
    r"(?i)\bVAT\b[^;.]{0,60}?\b(?:on|of|calculated\s+on|charged\s+on|applied\s+to|applies\s+to|applicable\s+(?:to|on))"
    r"\s+(?:the\s+)?(?:entire\s+|whole\s+|total\s+)?sub-?total\b"
)


def _vat_on_subtotal(quote: str) -> bool:
    """True only if the quote's WORDS establish that VAT is calculated on the whole subtotal ('VAT @ 15% on the
    subtotal'). Layout is never read here: a Subtotal row followed by a VAT row is handled, with its own assumption
    record, by ``_vat_basis_from_layout`` (AMB-15). Finding the words 'subtotal' and 'VAT' is not enough, and any
    wording that limits the scope ('selected items', 'only', 'part of') means it is not the subtotal."""
    text = normalize_text(quote)
    if any(VAT_SCOPE_LIMIT.search(clause.group(0)) for clause in _VAT_CLAUSE.finditer(text)):
        return False
    return bool(_VAT_ON_SUBTOTAL.search(text))


def _vat_basis_supports(value: str, quote: str) -> set:
    if _key_text(value) == "subtotal":
        return {"subtotal"} if _vat_on_subtotal(quote) else set()
    return _contains(value, quote)


# ---- AMB-15: the plain totals block ---------------------------------------------------------------------------
# Each row regex matches a WHOLE PDF line and nothing else on it. A row with any extra wording ("VAT on selected
# items 15% ...", "Subtotal (selected items) ...", "Total (all taxes included) ...") is not a plain row.
_BDT = r"(?:\(\s*(?:BDT|Tk\.?|৳)\s*\))?"
_PLAIN_SUBTOTAL = re.compile(rf"(?i)^sub-?total\s*{_BDT}\s*{_CUR}\s*:?\s*{_AMT}$")
_PLAIN_VAT = re.compile(
    rf"(?i)^(?:VAT|value\s+added\s+tax)\s*(?:\(\s*{NUMBER_PATTERN}\s*%\s*\)|@?\s*{NUMBER_PATTERN}\s*%)?\s*{_CUR}\s*:?\s*{_AMT}$"
)
_PLAIN_TOTAL = re.compile(rf"(?i)^(?:grand\s+)?total\s*{_BDT}\s*{_CUR}\s*:?\s*{_AMT}$")
_VAT_MENTION = re.compile(r"(?i)\bVAT\b|\bvalue[\s-]+added\s+tax\b")
_SENTENCE_END = re.compile(r"(?<!\d)\.(?!\d)|;")

AMB15_METHOD = (
    f"{AMB_VAT_SUBTOTAL}: plain totals block (Subtotal row, VAT row, Total row, contiguous) "
    "read as VAT on the whole subtotal; established by layout, not by wording"
)


def _vat_sentence(rows: list[str], index: int) -> str:
    """The sentence around the VAT mention on rows[index]. A sentence wrapped onto the next PDF line is followed."""
    combined = rows[index] + (" " + rows[index + 1] if index + 1 < len(rows) else "")
    mention = _VAT_MENTION.search(rows[index])
    assert mention is not None
    start, end = 0, len(combined)
    for boundary in _SENTENCE_END.finditer(combined):
        if boundary.end() <= mention.start():
            start = boundary.end()
        elif boundary.start() >= mention.end():
            end = boundary.start()
            break
    return combined[start:end]


def _totals_block_basis(doc: _Doc) -> tuple[int, str] | None:
    """(page, quote) of the document's one plain totals block, or None. None means 'AMB-15 does not apply'
    (the basis stays UNKNOWN), never 'the basis is something else'. All of these must hold:

    * three CONTIGUOUS PDF lines: a plain Subtotal row, then a plain VAT row, then a plain Total row;
    * exactly one such block (the same block printed twice is the same value; two different blocks mean more than
      one possible tax base);
    * no other VAT row anywhere in the document;
    * no VAT sentence anywhere that limits what VAT applies to ('selected items', 'only', 'part of', 'except'):
      explicit scope-limiting wording always overrides the layout;
    * nothing in the document says prices already include VAT."""
    blocks: dict[str, int] = {}  # block text -> page
    vat_rows: set[tuple[int, int]] = set()  # (page, line index) of every VAT row that belongs to a block
    rows_by_page: list[tuple[int, list[str]]] = []
    for page, lines in doc.page_lines:
        rows = [normalize_text(line) for line in lines]
        rows_by_page.append((page, rows))
        for i in range(len(rows) - 2):
            if _PLAIN_SUBTOTAL.match(rows[i]) and _PLAIN_VAT.match(rows[i + 1]) and _PLAIN_TOTAL.match(rows[i + 2]):
                blocks.setdefault(" ".join(rows[i:i + 3]), page)
                vat_rows.add((page, i + 1))
    if len(blocks) != 1 or _VAT_INCLUDED.search(doc.text):
        return None
    for page, rows in rows_by_page:
        for i, row in enumerate(rows):
            if (page, i) in vat_rows or not _VAT_MENTION.search(row):
                continue
            if _VAT_ROW.search(row) or VAT_SCOPE_LIMIT.search(_vat_sentence(rows, i)):
                return None
    ((quote, page),) = blocks.items()
    return page, quote


def _vat_basis_from_layout(field: Any, doc: _Doc, claims: frozenset[str]) -> Any:
    """AMB-15. Replace an UNKNOWN ``vat_price_basis`` by 'subtotal', with the real totals-block rows as VERIFIED
    evidence and AMB-15 in its normalization_method, when ``_totals_block_basis`` finds the plain block.

    Never overrides: a basis already verified from words (explicit 'subtotal' or any scope-limiting wording); a claim
    the model made without a quote (we cannot tell what it said); or a failed claim of some other wording than
    'subtotal' (the model saw wording we could not verify: that is for a human)."""
    if field.state == FieldState.FOUND or "vat_price_basis" in claims:
        return field
    claimed = field.evidence
    if claimed is not None and (claimed.normalized_value != "subtotal" or claimed.verification_status == _CONFLICT):
        return field
    found = _totals_block_basis(doc)
    if found is None:
        return field
    page, quote = found
    evidence = Evidence(
        raw_value=claimed.raw_value if claimed is not None else None,  # what the model returned, kept for audit
        normalized_value="subtotal",
        source_quote=quote,
        page_number=page,
        normalization_method=AMB15_METHOD,
        verification_status=_VERIFIED,
    )
    return type(field)(state=FieldState.FOUND, value="subtotal", evidence=evidence)


def _vat_shown_supports(_value: Any, quote: str) -> set:
    found = set()
    if _VAT_INCLUDED.search(quote):
        found.add(False)
    if _VAT_ROW.search(quote) and not _VAT_INCLUDED.search(quote):
        found.add(True)
    return found


def _vat_included_supports(_value: Any, quote: str) -> set:
    found = set()
    if _VAT_INCLUDED.search(quote):
        found.add(True)
    if _VAT_EXCLUDED.search(quote):
        found.add(False)
    return found


def _row_spec(part: str, key: Callable[[Any], Any]) -> _Spec:
    def supports(_value: Any, quote: str) -> set:
        row = _parse_row(quote)
        return {key(row[part])} if row else set()

    return _Spec(key=key, supports=supports)


_ID_KEY = lambda v: str(v).strip().upper()  # noqa: E731
_IDENTITY = lambda v: v  # noqa: E731

DOC_SPECS: dict[str, _Spec] = {
    "supplier_name": _Spec(_key_text, _supplier_supports),
    "tin": _Spec(_ID_KEY, lambda _v, q: _labelled_ids(_TIN_LABEL, q), lambda t: _labelled_ids(_TIN_LABEL, t)),
    "bin": _Spec(_ID_KEY, lambda _v, q: _labelled_ids(_BIN_LABEL, q), lambda t: _labelled_ids(_BIN_LABEL, t)),
    "subtotal_bdt": _Spec(_IDENTITY, lambda _v, q: _decimals(_SUBTOTAL, q), lambda t: _decimals(_SUBTOTAL, t)),
    "vat_rate_pct": _Spec(_IDENTITY, lambda _v, q: _vat_rates(q), _vat_rates),
    "vat_amount_bdt": _Spec(_IDENTITY, lambda _v, q: _decimals(_VAT_AMOUNT, q), lambda t: _decimals(_VAT_AMOUNT, t)),
    "grand_total_bdt": _Spec(_IDENTITY, lambda _v, q: _decimals(_GRAND_TOTAL, q), lambda t: _decimals(_GRAND_TOTAL, t)),
    "vat_shown_separately": _Spec(_IDENTITY, _vat_shown_supports),
    "vat_price_basis": _Spec(_key_text, _vat_basis_supports),
    "delivery_original_wording": _Spec(_key_text, _wording_supports(_delivery_supports)),
    "delivery_days": _Spec(_IDENTITY, _delivery_supports, lambda t: _delivery_days(t, require_po=False)),
    "advance_payment_pct": _Spec(_IDENTITY, lambda _v, q: _advance_values(q), _advance_values),
    "validity_original_wording": _Spec(_key_text, _wording_supports(lambda _v, q: _validity_days(q))),
    "validity_days": _Spec(_IDENTITY, lambda _v, q: _validity_days(q), _validity_days),
}

LINE_SPECS: dict[str, _Spec] = {
    "item_code": _row_spec("code", _ID_KEY),
    "description": _row_spec("description", _key_text),
    "quantity": _row_spec("quantity", _IDENTITY),
    "unit": _row_spec("unit", unit_key),
    "unit_price_bdt": _row_spec("unit_price", _IDENTITY),
    "line_total_bdt": _row_spec("amount", _IDENTITY),
    "vat_included_in_price": _Spec(_IDENTITY, _vat_included_supports),
}

# --------------------------------------------------------------------------------------
# Verifying one field
# --------------------------------------------------------------------------------------

_OK, _UNSUPPORTED, _CONFLICTING = "ok", "unsupported", "conflict"


def _verdict(spec: _Spec, value: Any, quote: str, doc: _Doc) -> str:
    wanted = spec.key(value)
    supported = spec.supports(value, quote)
    if wanted not in supported:
        return _UNSUPPORTED
    if len(supported) > 1:
        return _CONFLICTING  # the quote itself supports two different values
    if spec.doc_values is not None:
        document = spec.doc_values(doc.text)
        if len(document) > 1:
            return _CONFLICTING  # the document states two different values for this field
        if document and wanted not in document:
            return _UNSUPPORTED  # the one value the document states is not the candidate
    return _OK


def _resolve_page(evidence: Evidence, pages: list[int]) -> int:
    """The model's cited page is kept if the quote is really on it; otherwise the page where it was found."""
    return evidence.page_number if evidence.page_number in pages else pages[0]


def _checked(field: Any, spec: _Spec, doc: _Doc) -> Any:
    """Promote a candidate to VERIFIED, or downgrade it to UNKNOWN (value cleared, Evidence kept)."""
    if field.state != FieldState.FOUND or field.evidence is None:
        return field
    evidence = field.evidence
    pages = doc.locate(evidence.source_quote)
    status = _UNVERIFIED
    if pages:
        verdict = _verdict(spec, field.value, evidence.source_quote, doc)
        if verdict == _OK:
            verified = evidence.model_copy(update={"verification_status": _VERIFIED, "page_number": _resolve_page(evidence, pages)})
            return type(field)(state=FieldState.FOUND, value=field.value, evidence=verified)
        if verdict == _CONFLICTING:
            status = _CONFLICT
    return type(field)(state=FieldState.UNKNOWN, evidence=evidence.model_copy(update={"verification_status": status}))


# --------------------------------------------------------------------------------------
# ABSENT: an independent search, documented by AbsenceEvidence
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Search:
    terms: list[str]  # shown to the human reviewer
    pattern: re.Pattern[str]  # ANY match means the field may be present, so it is not ABSENT
    context: list[str]  # words used to pick a real nearby line as the contextual excerpt


_I = re.IGNORECASE
ABSENCE_SEARCHES: dict[str, _Search] = {
    "tin": _Search(["TIN", "Taxpayer Identification Number", "Tax Identification Number", "টিন"],
                   re.compile(r"\btin\b|tax(?:payer)?\s+identification|টিন", _I), ["registration", "supplier"]),
    "bin": _Search(["BIN", "Business Identification Number", "বিআইএন"],
                   re.compile(r"\bbin\b|business\s+identification|বিআইএন", _I), ["registration", "supplier"]),
    "penalty": _Search(["penalty", "late delivery", "delay", "liquidated damages", "damages", "জরিমানা"],
                       re.compile(r"penalt|liquidated|late\s+deliver|delay|damages|জরিমানা|পেনাল্টি", _I),
                       ["delivery", "terms and conditions"]),
    "vat_shown_separately": _Search(["VAT", "value added tax", "tax", "ভ্যাট"],
                                    re.compile(r"\bvat\b|value[\s-]added|\btax(?:es)?\b|ভ্যাট", _I), ["total", "subtotal"]),
    "vat_rate_pct": _Search(["VAT", "value added tax", "tax", "ভ্যাট"],
                            re.compile(r"\bvat\b|value[\s-]added|\btax(?:es)?\b|ভ্যাট", _I), ["total", "subtotal"]),
    # line-item columns: absent only if the column heading appears nowhere in the document
    "line.quantity": _Search(["Qty", "Quantity"], re.compile(r"\bqty\b|\bquantity\b", _I), ["description"]),
    "line.unit": _Search(["Unit", "UOM"], re.compile(r"\bunit\b(?!\s+price)|\buom\b", _I), ["description"]),
    "line.unit_price_bdt": _Search(["Unit price", "Rate", "Price"], re.compile(r"unit\s+price|\brate\b|\bprice\b", _I), ["description"]),
    "line.line_total_bdt": _Search(["Amount", "Line total", "Extended"], re.compile(r"\bamount\b|line\s+total|\bextended\b", _I), ["description"]),
}


def _context_line(doc: _Doc, words: list[str]) -> str | None:
    """A real line of PDF text near where the field would be expected (never a fabricated quote)."""
    for line in doc.lines:
        if any(w in line.lower() for w in words) and len(line) <= 200:
            return line
    return None


def _absence(name: str, label: str, doc: _Doc) -> AbsenceEvidence | None:
    """AbsenceEvidence if NONE of the field's search terms occurs on ANY page, else None (stay UNKNOWN)."""
    search = ABSENCE_SEARCHES[name]
    if not doc.pages or not doc.text.strip() or search.pattern.search(doc.text):
        return None
    return AbsenceEvidence(
        field_searched=label,
        search_terms=search.terms,
        pages_scanned=[number for number, _ in doc.pages],
        extraction_complete=True,  # the PDF was read completely (src/pdf_reader raises otherwise)
        contextual_excerpt=_context_line(doc, search.context),
        determination_basis=(
            f"Searched the text of every page for {', '.join(repr(t) for t in search.terms)}; none occurs, "
            "and the model did not claim a value for this field."
        ),
    )


def _claimed(field: Any) -> bool:
    """The model offered something for this field (even a value that later failed verification)."""
    return getattr(field, "evidence", None) is not None


def _maybe_absent(field: Any, name: str, label: str, doc: _Doc, claims: frozenset[str]) -> Any:
    """ABSENT only for a field the model never claimed: no kept Evidence and no entry in the document's
    ``claimed_without_evidence`` (a value supplied without a quote). A claim that failed is UNKNOWN."""
    if field.state != FieldState.UNKNOWN or _claimed(field) or label in claims:
        return field
    absence = _absence(name, label, doc)
    if absence is None:
        return field
    return type(field)(state=FieldState.ABSENT, absence_evidence=absence)


# --------------------------------------------------------------------------------------
# Composite records: payment terms and penalty clause
# --------------------------------------------------------------------------------------

_EVENTS = (
    ("delivery", re.compile(r"(?i)\bdeliver(?:y|ed|ies)\b")),
    ("acceptance", re.compile(r"(?i)\baccept\w*")),
    ("invoice", re.compile(r"(?i)\binvoic\w*|\bbill(?:ing)?\b")),
    ("purchase_order", re.compile(r"(?i)\bpurchase\s+order\b|\bPO\b|\bP\.O\.")),
    ("other", re.compile(r"(?i)\breceipt\b|\breceiv(?:ing|ed)\b|\binspection\b|\bcompletion\b")),
)


def _period_segment(quote: str) -> str:
    """The comma-clause that holds the payment period, so that an earlier clause such as '40% advance with the
    purchase order, balance 60% within 30 days of delivery' is judged on its balance clause only."""
    text = normalize_text(quote)
    paying = [s for s in sentences(text) if _PAYMENT_WORDS.search(s)]  # a clause about something else is ignored
    clauses = [c for s in paying for c in s.split(",") if PERIOD_IN_TEXT.search(c)]
    return " ".join(clauses) if clauses else " ".join(paying)


def payment_anchor(quote: str) -> str | None:
    """SPEC §5.2 AMB-13: the one event payment is counted from; more than one event -> 'other'; none -> None."""
    events = {name for name, pattern in _EVENTS if pattern.search(_period_segment(quote))}
    if not events:
        return None
    return next(iter(events)) if len(events) == 1 else "other"


def _unknown_payment(pay: PaymentTerms, status: VerificationStatus) -> PaymentTerms:
    return PaymentTerms(state=FieldState.UNKNOWN, original_wording=pay.original_wording,
                        evidence=pay.evidence.model_copy(update={"verification_status": status}))


def _verify_payment(pay: PaymentTerms, doc: _Doc) -> PaymentTerms:
    if pay.state != FieldState.FOUND or pay.evidence is None:
        return pay
    quote = pay.evidence.source_quote
    pages = doc.locate(quote)
    if not pages:
        return _unknown_payment(pay, _UNVERIFIED)
    if not _PAYMENT_WORDS.search(quote):
        return _unknown_payment(pay, _UNVERIFIED)  # not a payment clause at all (e.g. a warranty or delivery clause)
    if pay.min_days is not None:
        in_quote, in_document = _payment_bounds(quote), _payment_bounds(doc.text)
        if (pay.min_days, pay.max_days) not in in_quote:
            return _unknown_payment(pay, _UNVERIFIED)  # the period is not stated by a payment clause in this quote
        if len(in_quote) > 1 or len(in_document) > 1:
            return _unknown_payment(pay, _CONFLICT)
        if in_document and (pay.min_days, pay.max_days) not in in_document:
            return _unknown_payment(pay, _UNVERIFIED)
    verified = pay.evidence.model_copy(update={"verification_status": _VERIFIED, "page_number": _resolve_page(pay.evidence, pages)})
    # Anchor and discretionary are derived HERE, from the verified quote only (AMB-13; never assumed).
    return PaymentTerms(
        state=FieldState.FOUND, original_wording=pay.original_wording, min_days=pay.min_days, max_days=pay.max_days,
        anchor=payment_anchor(quote), discretionary=discretionary_from_text(quote), evidence=verified,
    )


_FULL_WEEKS = re.compile(r"(?i)\bfull\s+weeks?\b|\bcomplete(?:d)?\s+weeks?\b|\bwhole\s+weeks?\b")
_PART_WEEKS = re.compile(r"(?i)\bpart\s+thereof\b|\bpart\s+of\s+a\s+week\b|\bpro[\s-]?rata\b|\bany\s+part\b")
_PENALTY_SENTENCE = re.compile(r"(?i)penalt|late\s+deliver|delay")
_LATE = re.compile(r"(?i)\blate\s+deliver\w*|\bdelay\w*")
_CONSEQUENCE = re.compile(r"(?i)penalt|liquidated|damages|\bpay\b|deduct|compensat|\bfine\b")


def _is_penalty_clause(quote: str) -> bool:
    """A late-delivery PENALTY clause names the lateness and a financial consequence. A sentence that only mentions
    late delivery ('late delivery will be reported') is not one."""
    return bool(_LATE.search(quote) and _CONSEQUENCE.search(quote))


def _penalty_rates(text: str) -> set:
    return _decimals(re.compile(rf"({NUMBER_PATTERN})\s*%"), text)


def _unknown_penalty(pen: PenaltyClause, status: VerificationStatus) -> PenaltyClause:
    return PenaltyClause(state=FieldState.UNKNOWN, original_wording=pen.original_wording,
                         evidence=pen.evidence.model_copy(update={"verification_status": status}))


def _verify_penalty(pen: PenaltyClause, doc: _Doc) -> PenaltyClause:
    if pen.state != FieldState.FOUND or pen.evidence is None:
        return pen
    quote = pen.evidence.source_quote
    pages = doc.locate(quote)
    if not pages:
        return _unknown_penalty(pen, _UNVERIFIED)
    if not _is_penalty_clause(quote):
        return _unknown_penalty(pen, _UNVERIFIED)
    document_rates: set = set()
    for sentence in sentences(doc.text):
        if _PENALTY_SENTENCE.search(sentence):
            document_rates |= _penalty_rates(sentence)
    if len(document_rates) > 1:
        return _unknown_penalty(pen, _CONFLICT)  # the document states two different penalty rates
    # Each component must be supported by the verified clause quote; an unsupported one is cleared (UNKNOWN)
    # and the rule engine then treats it as unestablished.
    quote_rates = _penalty_rates(quote)
    rate = pen.rate_pct if pen.rate_pct is not None and quote_rates == {pen.rate_pct} else None
    basis = pen.calculation_basis
    if basis is not None:
        wanted = r"order\s+value" if basis == "order value" else re.escape(basis)
        basis = basis if re.search(wanted, quote, re.IGNORECASE) else None
    unit = pen.period_unit if pen.period_unit is not None and penalty_period_unit(quote) == pen.period_unit else None
    weeks = pen.full_weeks_only
    if weeks is True and not _FULL_WEEKS.search(quote):
        weeks = None
    if weeks is False and not _PART_WEEKS.search(quote):
        weeks = None
    verified = pen.evidence.model_copy(update={"verification_status": _VERIFIED, "page_number": _resolve_page(pen.evidence, pages)})
    return PenaltyClause(
        state=FieldState.FOUND, original_wording=pen.original_wording, rate_pct=rate, calculation_basis=basis,
        period_unit=unit, full_weeks_only=weeks, evidence=verified,
    )


# --------------------------------------------------------------------------------------
# Line items
# --------------------------------------------------------------------------------------


def _verify_line(item: LineItem, position: int, doc: _Doc, claims: frozenset[str]) -> LineItem:
    fields = {name: _checked(getattr(item, name), spec, doc) for name, spec in LINE_SPECS.items()}
    for name in ("quantity", "unit", "unit_price_bdt", "line_total_bdt"):
        fields[name] = _maybe_absent(fields[name], f"line.{name}", f"line-{position}.{name}", doc, claims)
    code_ok = fields["item_code"].state == FieldState.FOUND
    # A match flag is only as good as the field(s) it was derived from (SPEC §8.2).
    matches = {
        "item_code_match": item.item_code_match if code_ok else "UNKNOWN",
        "description_match": item.description_match if code_ok and fields["description"].state == FieldState.FOUND else "UNKNOWN",
        "unit_match": item.unit_match if code_ok and fields["unit"].state == FieldState.FOUND else "UNKNOWN",
    }
    return LineItem(**fields, **matches)


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def verify_evidence(extracted: ExtractedDocument, pdf_result: dict[str, Any]) -> ExtractedDocument:
    """Verify every FOUND candidate against the PDF text; return a new document (the input is not changed).

    Candidates that pass become VERIFIED. Candidates that fail become UNKNOWN (typed value cleared, Evidence kept).
    Unclaimed fields may become ABSENT, with AbsenceEvidence, only as described in the module docstring."""
    doc = _Doc.from_pdf_result(pdf_result)
    claims = frozenset(extracted.claimed_without_evidence)
    updates: dict[str, Any] = {name: _checked(getattr(extracted, name), spec, doc) for name, spec in DOC_SPECS.items()}
    for name in ("tin", "bin", "vat_shown_separately", "vat_rate_pct"):
        updates[name] = _maybe_absent(updates[name], name, name, doc, claims)
    updates["vat_price_basis"] = _vat_basis_from_layout(updates["vat_price_basis"], doc, claims)  # AMB-15

    payment = _verify_payment(extracted.payment, doc)
    penalty = _verify_penalty(extracted.penalty, doc)
    if penalty.state == FieldState.UNKNOWN and penalty.evidence is None and "penalty" not in claims:
        absence = _absence("penalty", "penalty_clause", doc)
        if absence is not None:
            penalty = PenaltyClause(state=FieldState.ABSENT, absence_evidence=absence)
    updates["payment"] = payment
    updates["penalty"] = penalty
    updates["line_items"] = [_verify_line(item, n, doc, claims) for n, item in enumerate(extracted.line_items, start=1)]

    result = extracted.model_copy(update=updates)
    return ExtractedDocument.model_validate(result.model_dump())  # re-run every model invariant on the result
