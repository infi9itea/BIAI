"""Data models for the Supplier Document Checker (Milestone 0).

Two clearly separated layers (SPEC §3.1, PROJECT_GUIDE §5):

* RAW layer        -> RawField / RawLineItem / RawExtraction: strings exactly as the LLM returned them.
* NORMALIZED layer -> everything else: typed values (Decimal / int / bool / str).
                      arithmetic.py and rule_engine.py only ever read this layer.

Conventions enforced here:
* No float anywhere (SPEC AC-06). Numeric types reject float input outright, because Pydantic would
  otherwise silently coerce 479.5 into a Decimal in lax mode.
* `_pct` fields hold PERCENTAGE POINTS as Decimal (Decimal("15") == 15%). Ratios (price excess,
  quantity difference) are fractions and live in rule_engine.py, not here (SPEC §3.1).
* FOUND / ABSENT / UNKNOWN invariants are checked by model validators (SPEC §6, §7.2).
* A normalized value is only a *candidate* (FOUND + UNVERIFIED) until verifier.py marks it VERIFIED.
  Consumers must read fields through is_usable() (SPEC §3.1 rule 5).
"""

import re
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# --------------------------------------------------------------------------------------
# Strict numeric types (no float, no bool, no NaN/Infinity)
# --------------------------------------------------------------------------------------


_INTEGER_STRING = re.compile(r"^[+-]?[0-9]+$")


def _strict_decimal_input(value: Any) -> Any:
    """Accept only str, int or Decimal. float and bool are rejected before Pydantic's lax coercion
    can accept them [A] PROJECT_GUIDE §5; any other type (list, None-like objects, ...) is rejected too."""
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(
            f"{type(value).__name__} is not allowed for a Decimal field; pass a str, int or Decimal"
        )
    return value


def _strict_int_input(value: Any) -> Any:
    """Accept only an int or an integer-looking str ("45"). Rejects bool, float and Decimal
    (Pydantic would otherwise coerce Decimal("45") or "45.0" into an int) and non-integer strings."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(
            f"{type(value).__name__} is not allowed for an int field; pass an int or an integer string"
        )
    if isinstance(value, str) and not _INTEGER_STRING.match(value.strip()):
        raise ValueError("string must look like an integer, e.g. '45'")
    return value


def _require_finite(value: Decimal) -> Decimal:
    """NaN / Infinity can never be a money value, quantity or percentage."""
    if not value.is_finite():
        raise ValueError("Decimal must be finite")
    return value


# Money, quantities and percentage points (SPEC §3.1). Accepts str, int, Decimal only.
StrictDecimal = Annotated[
    Decimal, BeforeValidator(_strict_decimal_input), AfterValidator(_require_finite)
]
# Day counts and page numbers. Accepts int or an integer-looking str only.
StrictInt = Annotated[int, BeforeValidator(_strict_int_input)]

T = TypeVar("T")

_FORBID = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------------------


class FieldState(str, Enum):
    FOUND = "FOUND"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    CONFLICT = "CONFLICT"


class RuleStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RuleSeverity(str, Enum):
    REJECT = "REJECT"
    REVIEW = "REVIEW"


class Decision(str, Enum):
    """Always exactly one of three values. ERROR is deliberately NOT a member (SPEC NFR-09)."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    NEEDS_REVIEW = "NEEDS_REVIEW"


# --------------------------------------------------------------------------------------
# RAW layer: what the LLM returned. Field values are str | None; page_number is int | None.
# --------------------------------------------------------------------------------------


class RawField(BaseModel):
    model_config = _FORBID

    value: str | None = None  # exactly as returned (numbers as strings, periods as written)
    source_quote: str | None = None
    page_number: StrictInt | None = None

    @model_validator(mode="after")
    def _page_is_positive(self) -> "RawField":
        # Same rule as Evidence / EvidenceRef so page numbering is consistent across both layers.
        # Fail-closed: an impossible page number makes the whole extraction invalid (-> retry, then
        # ExtractionError -> NEEDS_REVIEW) rather than silently trusting a bad citation.
        if self.page_number is not None and self.page_number < 1:
            raise ValueError("page_number must be positive (pages are numbered from 1)")
        return self


def _raw() -> RawField:
    return RawField()


class RawLineItem(BaseModel):
    model_config = _FORBID

    item_code: RawField = Field(default_factory=_raw)
    description: RawField = Field(default_factory=_raw)
    quantity: RawField = Field(default_factory=_raw)
    unit: RawField = Field(default_factory=_raw)
    unit_price_bdt: RawField = Field(default_factory=_raw)
    line_total_bdt: RawField = Field(default_factory=_raw)
    vat_included_in_price: RawField = Field(default_factory=_raw)


class RawExtraction(BaseModel):
    """Exactly the JSON the model returns; what the offline fixtures store (SPEC §3.1).

    extra="forbid": a `decision` (or any unknown) key in model output fails parsing, so the LLM
    has no channel through which to supply a decision (SPEC §12.2 item 7).
    Periods (delivery, validity, payment, penalty period) are strings as written, e.g. "4 weeks".
    Percentages are strings as written, e.g. "15" or "15%". Missing keys default to an empty RawField.
    """

    model_config = _FORBID

    supplier_name: RawField = Field(default_factory=_raw)
    tin: RawField = Field(default_factory=_raw)
    bin: RawField = Field(default_factory=_raw)
    line_items: list[RawLineItem] = Field(default_factory=list)
    subtotal_bdt: RawField = Field(default_factory=_raw)
    vat_rate_pct: RawField = Field(default_factory=_raw)
    vat_amount_bdt: RawField = Field(default_factory=_raw)
    grand_total_bdt: RawField = Field(default_factory=_raw)
    vat_shown_separately: RawField = Field(default_factory=_raw)
    vat_price_basis: RawField = Field(default_factory=_raw)
    delivery_period: RawField = Field(default_factory=_raw)
    payment_terms_wording: RawField = Field(default_factory=_raw)
    payment_period: RawField = Field(default_factory=_raw)  # e.g. "30 to 90 days"
    advance_payment_pct: RawField = Field(default_factory=_raw)
    penalty_wording: RawField = Field(default_factory=_raw)
    penalty_rate_pct: RawField = Field(default_factory=_raw)
    penalty_calculation_basis: RawField = Field(default_factory=_raw)
    penalty_period: RawField = Field(default_factory=_raw)  # e.g. "per week"
    penalty_full_weeks_only: RawField = Field(default_factory=_raw)
    validity_period: RawField = Field(default_factory=_raw)


# --------------------------------------------------------------------------------------
# NORMALIZED layer: evidence records
# --------------------------------------------------------------------------------------


# [A] AMB-15 (SPEC §13): the assumption ID recorded when a VAT basis of 'subtotal' was established by the document's
# totals-block layout rather than by words. Shared by the verifier (which applies it), the arithmetic checks and the
# rule engine (which report it), so the three can never drift apart.
AMB_VAT_SUBTOTAL = "AMB-15"


class Evidence(BaseModel):
    """Evidence for a FOUND field. raw_value / normalized_value are audit-only (SPEC §7.1)."""

    model_config = _FORBID

    raw_value: str | None = None  # exactly what the LLM returned
    normalized_value: str | None = None  # canonical string of the typed value
    source_quote: str  # verbatim; non-empty for FOUND (enforced in the field validators)
    page_number: StrictInt | None = None
    normalization_method: str | None = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED

    @model_validator(mode="after")
    def _is_a_real_quote(self) -> "Evidence":
        # Every Evidence object holds an actual quote, including evidence kept for audit after a
        # field is downgraded. (A value with no quote has no Evidence at all: the field is UNKNOWN.)
        if not self.source_quote.strip():
            raise ValueError("source_quote must be non-empty")
        if self.page_number is not None and self.page_number < 1:
            raise ValueError("page_number must be positive (pages are numbered from 1)")
        return self


class AbsenceEvidence(BaseModel):
    """Replaces Evidence when a field is ABSENT. Never a fabricated quote (SPEC §7.2).

    The record must describe an actual documented search, not just a flag: a named field, real search
    terms, at least one scanned page and a stated basis. (extraction_complete = True is enforced where
    the record establishes ABSENT: FieldRecord / PenaltyClause / RuleResult.)
    """

    model_config = _FORBID

    field_searched: str  # e.g. "tin" or "line-2.unit_price_bdt"
    search_terms: list[str]
    pages_scanned: list[StrictInt]
    extraction_complete: bool  # True only if full-document extraction succeeded
    contextual_excerpt: str | None = None  # real nearby text, NOT a fabricated quote
    determination_basis: str  # plain-English explanation

    @model_validator(mode="after")
    def _describes_a_real_search(self) -> "AbsenceEvidence":
        if not self.field_searched.strip():
            raise ValueError("field_searched must be non-empty")
        if not self.search_terms or any(not term.strip() for term in self.search_terms):
            raise ValueError("search_terms needs at least one term, and no blank terms")
        if not self.pages_scanned or any(page < 1 for page in self.pages_scanned):
            raise ValueError("pages_scanned needs at least one page, and page numbers must be positive")
        if not self.determination_basis.strip():
            raise ValueError("determination_basis must be non-empty")
        # SPEC §7.2: the excerpt is optional ("if any"), but when given it must be real text, not blank.
        if self.contextual_excerpt is not None and not self.contextual_excerpt.strip():
            raise ValueError("contextual_excerpt must be None or non-blank text")
        return self


class EvidenceRef(BaseModel):
    """One verified quote attached to a rule or arithmetic result (SPEC §7.3).

    Structural checks only: that the quote really appears in the PDF is the verifier's job.
    """

    model_config = _FORBID

    label: str  # e.g. "printed subtotal", "line-1 quantity"
    source_quote: str
    page_number: StrictInt | None = None

    @model_validator(mode="after")
    def _not_structurally_empty(self) -> "EvidenceRef":
        if not self.label.strip():
            raise ValueError("label must be non-empty")
        if not self.source_quote.strip():
            raise ValueError("source_quote must be non-empty")
        if self.page_number is not None and self.page_number < 1:
            raise ValueError("page_number must be positive (pages are numbered from 1)")
        return self


# --------------------------------------------------------------------------------------
# NORMALIZED layer: three-state fields
# --------------------------------------------------------------------------------------


def _check_three_state(
    state: FieldState,
    value: Any,
    evidence: Evidence | None,
    absence_evidence: AbsenceEvidence | None,
) -> None:
    """Invariants shared by FieldRecord and LineItemField (SPEC §6, §7.2)."""
    if state == FieldState.FOUND:
        if value is None:
            raise ValueError("FOUND requires a typed value")
        if evidence is None or not evidence.source_quote.strip():
            raise ValueError("FOUND requires Evidence with a non-empty source_quote")
        if evidence.verification_status == VerificationStatus.CONFLICT:
            raise ValueError("a CONFLICT field must be UNKNOWN, not FOUND")
        if absence_evidence is not None:
            raise ValueError("FOUND must not carry AbsenceEvidence")
    elif state == FieldState.ABSENT:
        if value is not None:
            raise ValueError("ABSENT requires value to be None")
        if absence_evidence is None:
            raise ValueError("ABSENT requires an AbsenceEvidence record (never a fabricated quote)")
        if not absence_evidence.extraction_complete:
            # SPEC §7.2: extraction_complete = False means the field cannot be ABSENT.
            raise ValueError("ABSENT requires AbsenceEvidence.extraction_complete = True")
        if evidence is not None:
            raise ValueError("ABSENT carries AbsenceEvidence, not Evidence")
    else:  # UNKNOWN
        if value is not None:
            raise ValueError("UNKNOWN requires value to be None (typed value is cleared)")
        if absence_evidence is not None:
            raise ValueError("UNKNOWN must not carry AbsenceEvidence")
        # A downgraded field keeps its Evidence for audit, but it can never be VERIFIED.
        if evidence is not None and evidence.verification_status == VerificationStatus.VERIFIED:
            raise ValueError("UNKNOWN must not carry VERIFIED evidence")


def _reject_float_value(value: Any) -> Any:
    """Generic value guard: float is never a legal typed value for any T (bool/int/str/Decimal)."""
    if isinstance(value, float):
        raise ValueError("float is not allowed; pass a str, int or Decimal")
    return value


class FieldRecord(BaseModel, Generic[T]):
    """A document-level field that can be FOUND, ABSENT or UNKNOWN. Default is UNKNOWN."""

    model_config = _FORBID

    state: FieldState = FieldState.UNKNOWN
    value: T | None = None
    evidence: Evidence | None = None
    absence_evidence: AbsenceEvidence | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _reject_float_value(value)

    @model_validator(mode="after")
    def _check_state(self) -> "FieldRecord[T]":
        if isinstance(self.value, Decimal) and not self.value.is_finite():
            raise ValueError("Decimal must be finite")
        _check_three_state(self.state, self.value, self.evidence, self.absence_evidence)
        return self


class LineItemField(FieldRecord[T], Generic[T]):
    """One sub-field of a LineItem. Same shape as FieldRecord, and able to carry its own
    AbsenceEvidence when ABSENT (SPEC §8.2)."""


def is_usable(field: Any) -> bool:
    """True only for FOUND + VERIFIED evidence (SPEC §3.1 rule 5).

    arithmetic.py and rule_engine.py must read every field through this and treat anything else
    (a FOUND candidate that is still UNVERIFIED, ABSENT, UNKNOWN) as UNKNOWN.
    Works for FieldRecord, LineItemField, PaymentTerms and PenaltyClause (all expose state/evidence).

    CAUTION for composite records: for PaymentTerms and PenaltyClause, is_usable() means the CLAUSE
    WORDING is verified. It does NOT mean every component is known: min_days, max_days, anchor,
    discretionary, rate_pct or period_unit can still be None (UNKNOWN). The rule engine must check
    each component it needs (see PaymentTerms.discretionary_usable()) before using it.
    """
    evidence = getattr(field, "evidence", None)
    return (
        getattr(field, "state", None) == FieldState.FOUND
        and evidence is not None
        and evidence.verification_status == VerificationStatus.VERIFIED
    )


# --------------------------------------------------------------------------------------
# NORMALIZED layer: line items, payment, penalty, document
# --------------------------------------------------------------------------------------

MatchResult = Literal["YES", "NO", "UNKNOWN"]


class LineItem(BaseModel):
    model_config = _FORBID

    item_code: LineItemField[str] = Field(default_factory=LineItemField[str])
    description: LineItemField[str] = Field(default_factory=LineItemField[str])
    quantity: LineItemField[StrictDecimal] = Field(default_factory=LineItemField[StrictDecimal])
    unit: LineItemField[str] = Field(default_factory=LineItemField[str])
    unit_price_bdt: LineItemField[StrictDecimal] = Field(default_factory=LineItemField[StrictDecimal])
    line_total_bdt: LineItemField[StrictDecimal] = Field(default_factory=LineItemField[StrictDecimal])  # as printed
    vat_included_in_price: LineItemField[bool] = Field(default_factory=LineItemField[bool])
    # Set deterministically in normalization; reset to "UNKNOWN" by the verifier on downgrade (SPEC §8.2).
    item_code_match: MatchResult = "UNKNOWN"
    description_match: MatchResult = "UNKNOWN"
    unit_match: MatchResult = "UNKNOWN"


PaymentAnchor = Literal["delivery", "acceptance", "invoice", "purchase_order", "other"]


class PaymentTerms(BaseModel):
    """Payment terms kept as a range plus anchor (PROJECT_GUIDE §14, SPEC §8.6).

    `discretionary` is bool | None: None means UNKNOWN. It is NEVER defaulted to False, because
    "not discretionary" is a positive claim that must come from verified wording. Read it through
    discretionary_usable(). ABSENT is not offered here (no AbsenceEvidence field): a missing
    payment clause is UNKNOWN, which makes NR-03 UNCERTAIN -> NEEDS_REVIEW.
    """

    model_config = _FORBID

    state: FieldState = FieldState.UNKNOWN
    original_wording: str | None = None  # kept for audit in every state; never discarded
    min_days: StrictInt | None = None
    max_days: StrictInt | None = None
    anchor: PaymentAnchor | None = None  # None = UNKNOWN (SPEC §8.6, AMB-13)
    discretionary: bool | None = None  # None = UNKNOWN
    evidence: Evidence | None = None

    @model_validator(mode="after")
    def _check(self) -> "PaymentTerms":
        if self.state == FieldState.ABSENT:
            raise ValueError("PaymentTerms cannot be ABSENT (no AbsenceEvidence); use UNKNOWN")
        if self.state == FieldState.FOUND:
            if self.evidence is None or not self.evidence.source_quote.strip():
                raise ValueError("FOUND payment terms require Evidence with a non-empty source_quote")
            if self.evidence.verification_status == VerificationStatus.CONFLICT:
                raise ValueError("a CONFLICT field must be UNKNOWN, not FOUND")
            if not (self.original_wording or "").strip():
                raise ValueError("FOUND payment terms require original_wording")
            if (self.min_days is None) != (self.max_days is None):
                raise ValueError("min_days and max_days must both be set or both be None")
            if self.min_days is not None and (self.min_days < 0 or self.min_days > self.max_days):
                raise ValueError("require 0 <= min_days <= max_days")
        else:  # UNKNOWN: typed values are cleared; wording and Evidence may stay for audit
            if any(v is not None for v in (self.min_days, self.max_days, self.anchor, self.discretionary)):
                raise ValueError("UNKNOWN payment terms must have min/max/anchor/discretionary = None")
            if self.evidence is not None and self.evidence.verification_status == VerificationStatus.VERIFIED:
                raise ValueError("UNKNOWN must not carry VERIFIED evidence")
        return self

    def discretionary_usable(self) -> bool:
        """True only when the wording is verified AND discretionary was actually determined.

        Otherwise NR-03 must not assume 'not discretionary'; it is UNCERTAIN unless a definite
        violation is established elsewhere (SPEC §5.2 NR-03 precedence).
        """
        return is_usable(self) and self.discretionary is not None


PenaltyPeriodUnit = Literal["week", "day", "other"]


class PenaltyClause(BaseModel):
    model_config = _FORBID

    state: FieldState = FieldState.UNKNOWN
    original_wording: str | None = None
    rate_pct: StrictDecimal | None = None  # AS STATED, percentage points (SPEC §3.1, §8.8)
    calculation_basis: str | None = None
    period_unit: PenaltyPeriodUnit | None = None  # None = UNKNOWN. [A] AMB-14: rate is per-week only if "week"
    full_weeks_only: bool | None = None
    evidence: Evidence | None = None
    absence_evidence: AbsenceEvidence | None = None

    @model_validator(mode="after")
    def _check(self) -> "PenaltyClause":
        details = (self.rate_pct, self.calculation_basis, self.period_unit, self.full_weeks_only)
        if self.state == FieldState.FOUND:
            if self.evidence is None or not self.evidence.source_quote.strip():
                raise ValueError("FOUND penalty clause requires Evidence with a non-empty source_quote")
            if self.evidence.verification_status == VerificationStatus.CONFLICT:
                raise ValueError("a CONFLICT field must be UNKNOWN, not FOUND")
            if not (self.original_wording or "").strip():
                raise ValueError("FOUND penalty clause requires original_wording")
            if self.absence_evidence is not None:
                raise ValueError("FOUND must not carry AbsenceEvidence")
            if self.rate_pct is not None and not self.rate_pct.is_finite():
                raise ValueError("rate_pct must be finite")
            if self.rate_pct is not None and self.rate_pct < 0:
                raise ValueError("rate_pct must not be negative")
            return self
        # ABSENT and UNKNOWN: no clause details may be present
        if any(v is not None for v in details):
            raise ValueError("a non-FOUND penalty clause must not carry rate/basis/period details")
        if self.state == FieldState.ABSENT:
            if self.absence_evidence is None or not self.absence_evidence.extraction_complete:
                raise ValueError("ABSENT penalty clause requires AbsenceEvidence with extraction_complete = True")
            if self.evidence is not None:
                raise ValueError("ABSENT carries AbsenceEvidence, not Evidence")
            if self.original_wording is not None:
                raise ValueError("ABSENT penalty clause must not carry original_wording")
        else:  # UNKNOWN (original_wording may stay for audit)
            if self.absence_evidence is not None:
                raise ValueError("UNKNOWN must not carry AbsenceEvidence")
            if self.evidence is not None and self.evidence.verification_status == VerificationStatus.VERIFIED:
                raise ValueError("UNKNOWN must not carry VERIFIED evidence")
        return self


class ExtractedDocument(BaseModel):
    """Top-level normalized extraction result. Typed values only; no raw strings, no float.

    Every field defaults to UNKNOWN, so ExtractedDocument() is the 'nothing known' document.
    """

    model_config = _FORBID

    supplier_name: FieldRecord[str] = Field(default_factory=FieldRecord[str])
    tin: FieldRecord[str] = Field(default_factory=FieldRecord[str])
    bin: FieldRecord[str] = Field(default_factory=FieldRecord[str])
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal_bdt: FieldRecord[StrictDecimal] = Field(default_factory=FieldRecord[StrictDecimal])
    vat_rate_pct: FieldRecord[StrictDecimal] = Field(default_factory=FieldRecord[StrictDecimal])  # percentage points
    vat_amount_bdt: FieldRecord[StrictDecimal] = Field(default_factory=FieldRecord[StrictDecimal])
    grand_total_bdt: FieldRecord[StrictDecimal] = Field(default_factory=FieldRecord[StrictDecimal])
    vat_shown_separately: FieldRecord[bool] = Field(default_factory=FieldRecord[bool])
    vat_price_basis: FieldRecord[str] = Field(default_factory=FieldRecord[str])
    delivery_original_wording: FieldRecord[str] = Field(default_factory=FieldRecord[str])
    delivery_days: FieldRecord[StrictInt] = Field(default_factory=FieldRecord[StrictInt])
    payment: PaymentTerms = Field(default_factory=PaymentTerms)
    advance_payment_pct: FieldRecord[StrictDecimal] = Field(default_factory=FieldRecord[StrictDecimal])  # pct points
    advance_original_wording: str | None = None
    penalty: PenaltyClause = Field(default_factory=PenaltyClause)
    validity_original_wording: FieldRecord[str] = Field(default_factory=FieldRecord[str])
    validity_days: FieldRecord[StrictInt] = Field(default_factory=FieldRecord[StrictInt])
    # Claim provenance (SPEC §3.1, §6). Names of fields for which the model SUPPLIED a value but normalization could
    # not keep any evidence (no source quote, so no Evidence record can exist). Such a field is UNKNOWN, and the
    # verifier must never turn it into ABSENT: "the model claimed it" and "the model never mentioned it" are
    # different facts. Names: a document field ("tin", "penalty", "payment", "delivery_days", ...) or
    # "line-<n>.<field>". Set only by normalize_extraction(); the verifier reads it and keeps it for audit.
    claimed_without_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _advance_never_absent(self) -> "ExtractedDocument":
        # [A] AMB-05 / SPEC §8.7: advance payment is FOUND or UNKNOWN only. An explicit "no advance"
        # is FOUND with Decimal("0"); an unstated advance is UNKNOWN, never ABSENT and never 0.
        if self.advance_payment_pct.state == FieldState.ABSENT:
            raise ValueError("advance_payment_pct must be FOUND or UNKNOWN, never ABSENT (AMB-05)")
        return self

    @model_validator(mode="after")
    def _periods_must_be_nonnegative(self) -> "ExtractedDocument":
        # StrictInt stays a general integer type; the business constraint lives here. Mirrors
        # PaymentTerms, which already forbids negative day counts.
        for name in ("delivery_days", "validity_days"):
            field = getattr(self, name)
            if field.state == FieldState.FOUND and field.value < 0:
                raise ValueError(f"{name} must not be negative")
        return self


# --------------------------------------------------------------------------------------
# Arithmetic, rules and pack results
# --------------------------------------------------------------------------------------

_LINE_ID_PATTERN = r"^line-[1-9][0-9]*$"


class ArithmeticCheckResult(BaseModel):
    """Produced by arithmetic.py; consumed (never recomputed) by rule_engine.py (SPEC §9)."""

    model_config = _FORBID

    check_name: str  # "line_recalc", "independent_subtotal", "vat", "grand"
    item_id: str | None = Field(default=None, pattern=_LINE_ID_PATTERN)  # None for subtotal/vat/grand
    calculated: StrictDecimal | None = None
    printed: StrictDecimal | None = None
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    detail: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    # Assumption IDs the calculation RESTED ON (e.g. ["AMB-15"] when the VAT basis came from the totals layout).
    # NR-06 carries them into its own assumptions_applied; they are never used to compute anything.
    assumptions_applied: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "ArithmeticCheckResult":
        if not self.detail.strip():
            raise ValueError("detail must be non-empty")
        if self.status in ("PASS", "FAIL"):
            # SPEC §9: FAIL (and PASS) only when every input and the printed value are known.
            if self.calculated is None or self.printed is None:
                raise ValueError("PASS/FAIL requires both calculated and printed values")
            if not self.evidence_refs:
                raise ValueError("PASS/FAIL requires evidence_refs for the printed value and inputs")
        return self


class RuleResult(BaseModel):
    """One rule outcome with full traceability (SPEC §5.6, §7.3)."""

    model_config = _FORBID

    rule_id: str
    description: str
    status: RuleStatus
    severity: RuleSeverity
    item_id: str | None = Field(default=None, pattern=_LINE_ID_PATTERN)  # "line-<n>" for per-item rules
    detail: str
    triggering_value: str | None = None  # display string only
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    absence_evidence: list[AbsenceEvidence] = Field(default_factory=list)
    assumptions_applied: list[str] = Field(default_factory=list)  # e.g. ["AMB-12"]
    not_applicable_reason: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "RuleResult":
        if not self.detail.strip():
            raise ValueError("detail must be non-empty")
        if self.status == RuleStatus.NOT_APPLICABLE and not (self.not_applicable_reason or "").strip():
            raise ValueError("NOT_APPLICABLE requires not_applicable_reason (SPEC §5.5)")
        # SPEC §7.3 rule 2: never rest a result on AbsenceEvidence from an incomplete extraction.
        if any(not a.extraction_complete for a in self.absence_evidence):
            raise ValueError("absence_evidence must have extraction_complete = True")
        # SPEC §7.3 rule 1: an untraceable FAIL is never emitted (the rule engine downgrades to UNCERTAIN).
        if self.status == RuleStatus.FAIL and not self.evidence_refs and not self.absence_evidence:
            raise ValueError("FAIL requires at least one EvidenceRef or AbsenceEvidence")
        return self


_ERROR_PREFIX = "ERROR: "


class PackResult(BaseModel):
    model_config = _FORBID

    pack_filename: str
    processing_status: str = "OK"  # "OK" or "ERROR: <code>"
    processing_error_detail: str | None = None
    extracted: ExtractedDocument | None = None
    arithmetic_checks: list[ArithmeticCheckResult] = Field(default_factory=list)
    rule_results: list[RuleResult] = Field(default_factory=list)
    decision: Decision
    reason: str
    assumptions_applied: list[str] = Field(default_factory=list)  # de-duplicated union over rule results

    @model_validator(mode="after")
    def _check(self) -> "PackResult":
        if not self.reason.strip():
            raise ValueError("reason must be non-empty")
        if self.processing_status == "OK":
            # An OK pack has no error detail: the two must never contradict each other.
            if self.processing_error_detail is not None:
                raise ValueError('processing_status "OK" must not carry a processing_error_detail')
            return self
        if not self.processing_status.startswith(_ERROR_PREFIX):
            raise ValueError('processing_status must be "OK" or "ERROR: <code>"')
        if not self.processing_status[len(_ERROR_PREFIX):].strip():
            raise ValueError('"ERROR: " must be followed by an actual error code')
        if not (self.processing_error_detail or "").strip():
            raise ValueError("a processing error requires a non-empty processing_error_detail")
        # SPEC NFR-09: a technical failure is always NEEDS_REVIEW, never a fourth decision.
        if self.decision != Decision.NEEDS_REVIEW:
            raise ValueError("a processing error always yields NEEDS_REVIEW")
        # SPEC §11 / M4 build_error_result: the pipeline did not complete, so there are no
        # extracted data, arithmetic results, rule results or assumptions to show. (Dropping the
        # spec's "or a partial document" option keeps every error path identical.)
        if (self.extracted is not None or self.arithmetic_checks or self.rule_results
                or self.assumptions_applied):
            raise ValueError("a processing-error result must not carry extracted data, "
                             "arithmetic checks, rule results or assumptions")
        return self


# --------------------------------------------------------------------------------------
# Purchase request
# --------------------------------------------------------------------------------------


class PRItem(BaseModel):
    model_config = _FORBID

    item_code: str
    description: str
    unit: str
    quantity_requested: StrictDecimal
    max_unit_price_bdt: StrictDecimal


class PurchaseRequest(BaseModel):
    model_config = _FORBID

    pr_number: str
    items: dict[str, PRItem]  # keyed by item code, e.g. "A"

    @model_validator(mode="after")
    def _keys_match_codes(self) -> "PurchaseRequest":
        for key, item in self.items.items():
            if key != item.item_code:
                raise ValueError(f"items key {key!r} does not match item_code {item.item_code!r}")
        return self
