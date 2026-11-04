"""Deterministic text and value parsers shared by extractor.py (normalization) and verifier.py (evidence checks).

Pure functions only: no LLM, no I/O, no rule logic, no float. Anything that cannot be parsed unambiguously
returns None, and the caller turns None into an UNKNOWN field (SPEC §3.1 rule 2).
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

# --------------------------------------------------------------------------------------
# Text normalisation (used for quote verification, SPEC §7.1 rule 1)
# --------------------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Unicode NFKC + whitespace collapsed. Case is kept. Nothing else is rewritten: in particular no space is
    ever inserted, so a quote must reproduce joined text (e.g. ``...included)Amount``) exactly."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


# --------------------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------------------

# Western grouping (5,664,000), Bangladeshi/Indian grouping (56,64,000) or no grouping; optional decimals.
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+)(?:\.\d+)?"
NUMBER_PATTERN = _NUMBER
_NUMBER_FULL = re.compile(rf"^{_NUMBER}$")
_CURRENCY_TOKENS = re.compile(r"(?i)(?:\bBDT\b|\bTk\.?(?=\s|\d|$)|\bTaka\b|৳|/-)")


def to_decimal(text: str | None) -> Decimal | None:
    """'5,664,000.00' -> Decimal; a currency token is removed. Not a plain non-negative number -> None."""
    if text is None:
        return None
    cleaned = _CURRENCY_TOKENS.sub("", unicodedata.normalize("NFKC", text)).strip()
    if not _NUMBER_FULL.match(cleaned):
        return None
    try:
        return Decimal(cleaned.replace(",", ""))
    except InvalidOperation:
        return None


def to_pct_points(text: str | None) -> Decimal | None:
    """'15%' and '15' -> Decimal('15'); '0.5' -> Decimal('0.5'). Never rescaled (SPEC §3.1 rule 4)."""
    if text is None:
        return None
    cleaned = re.sub(r"(?i)\s*(?:%|per\s*cent|percent)\s*$", "", unicodedata.normalize("NFKC", text).strip())
    return to_decimal(cleaned)


# --------------------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------------------

_UNIT_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7}
_PERIOD_FULL = re.compile(r"^(\d+)\s*(days?|weeks?)$", re.IGNORECASE)
_RANGE_FULL = re.compile(r"^(\d+)\s*(?:to|-|–|—)\s*(\d+)\s*(days?|weeks?)$", re.IGNORECASE)
# Inside running text (verifier): "45 days", "30 to 90 days", "4 weeks".
PERIOD_IN_TEXT = re.compile(r"(\d+)(?:\s*(?:to|-|–|—)\s*(\d+))?\s*(days?|weeks?)\b", re.IGNORECASE)


def parse_period_days(text: str | None) -> tuple[int, str] | None:
    """'24 days' -> (24, 'explicit days'); '4 weeks' -> (28, 'weeks × 7'). Anything else -> None."""
    if text is None:
        return None
    match = _PERIOD_FULL.match(unicodedata.normalize("NFKC", text).strip())
    if not match:
        return None
    unit = match.group(2).lower()
    days = int(match.group(1)) * _UNIT_DAYS[unit]
    return days, ("weeks × 7" if unit.startswith("week") else "explicit days")


def parse_payment_range(text: str | None) -> tuple[int, int, str] | None:
    """'30 to 90 days' / '30-90 days' -> (30, 90); '30 days' -> (30, 30); 'n to m' with n > m -> None."""
    if text is None:
        return None
    cleaned = unicodedata.normalize("NFKC", text).strip()
    match = _RANGE_FULL.match(cleaned)
    if match:
        factor = _UNIT_DAYS[match.group(3).lower()]
        low, high = int(match.group(1)) * factor, int(match.group(2)) * factor
        if low > high:
            return None
        return low, high, "payment range" + (" (weeks × 7)" if factor == 7 else "")
    single = parse_period_days(cleaned)
    if single:
        return single[0], single[0], single[1]
    return None


def periods_in_text(text: str) -> set[tuple[int, int]]:
    """Every distinct (min_days, max_days) period written in running text. Empty set if there is none."""
    found: set[tuple[int, int]] = set()
    for match in PERIOD_IN_TEXT.finditer(unicodedata.normalize("NFKC", text)):
        factor = _UNIT_DAYS[match.group(3).lower()]
        low = int(match.group(1)) * factor
        high = int(match.group(2)) * factor if match.group(2) else low
        if low <= high:
            found.add((low, high))
    return found


# --------------------------------------------------------------------------------------
# Yes / no, penalty period, bases
# --------------------------------------------------------------------------------------

_YES = {"yes", "true", "y"}
_NO = {"no", "false", "n"}


def parse_yes_no(text: str | None) -> bool | None:
    if text is None:
        return None
    word = text.strip().strip(".").lower()
    if word in _YES:
        return True
    if word in _NO:
        return False
    return None


def penalty_period_unit(text: str | None) -> str | None:
    """Wording -> 'week' | 'day' | 'other' | None. Never converts between units (AMB-14).

    Both 'week' and 'day' mentioned, or no recognisable period at all -> None (UNKNOWN)."""
    if text is None:
        return None
    lowered = text.lower()
    has_week = bool(re.search(r"\bweek", lowered))
    has_day = bool(re.search(r"\bday", lowered))
    has_other = bool(re.search(r"\b(?:month|year|fortnight|hour)", lowered))
    kinds = [has_week, has_day, has_other]
    if sum(kinds) != 1:
        return None
    return "week" if has_week else "day" if has_day else "other"


def canonical_penalty_basis(text: str | None) -> str | None:
    """'the total order value' -> 'order value' (the value rule_engine compares). Other wording is kept
    lower-cased and trimmed, which the rule engine then treats as a basis it cannot compare."""
    if text is None:
        return None
    lowered = normalize_text(text).lower()
    if not lowered:
        return None
    return "order value" if "order value" in lowered else lowered


# Words that LIMIT what the VAT applies to ("VAT on selected items", "VAT applies only to items A and B"). Wording
# that has any of them is never the whole subtotal. Deliberately conservative: UNKNOWN is better than a wrong
# VAT calculation (arithmetic.py computes VAT on the whole independent subtotal).
VAT_SCOPE_LIMIT = re.compile(
    r"(?i)\b(?:selected?|certain|some|part(?:ial(?:ly)?)?|portion|specific|only|except\w*|exempt\w*|excluding|excl\.?|items?)\b"
)


def canonical_vat_basis(text: str | None) -> str | None:
    """'on the sub-total' -> 'subtotal' (the value arithmetic.py requires), but only when the wording does not
    limit the scope: 'VAT on selected items of the subtotal' keeps its own wording (lower-cased) and is therefore
    not 'subtotal'. Other wording is kept lower-cased."""
    if text is None:
        return None
    lowered = normalize_text(text).lower()
    if not lowered:
        return None
    if VAT_SCOPE_LIMIT.search(lowered):
        return lowered
    return "subtotal" if re.search(r"sub-?total", lowered) else lowered


# --------------------------------------------------------------------------------------
# Units (SPEC §10 alias table)
# --------------------------------------------------------------------------------------

_UNIT_GROUPS = (
    frozenset({"metre", "metres", "meter", "meters", "m", "মিটার"}),
    frozenset({"piece", "pieces", "pcs", "pc", "পিস"}),
    frozenset({"cone", "cones"}),
)


def unit_key(unit: str) -> str:
    """Canonical comparison key: alias group representative, else the lower-cased unit itself."""
    cleaned = normalize_text(unit).lower().rstrip(".")
    for group in _UNIT_GROUPS:
        if cleaned in group:
            return min(group)  # any fixed member of the group
    return cleaned


# --------------------------------------------------------------------------------------
# Sentence helper for clause-scoped checks
# --------------------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<!\d)\.(?!\d)|;")


def sentences(text: str) -> list[str]:
    """Split on full stops that are not decimal points, and on semicolons. Pass whitespace-collapsed text
    (``normalize_text``): the PDF wraps sentences across lines, so line breaks must not split them."""
    return [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]


# --------------------------------------------------------------------------------------
# Payment wording
# --------------------------------------------------------------------------------------

_DISCRETIONARY = re.compile(
    r"(?i)\b(?:as\s+agreed|to\s+be\s+agreed|to\s+be\s+negotiated|negotiable|mutually\s+agreed|"
    r"by\s+agreement|subject\s+to\s+agreement|as\s+per\s+agreement|to\s+be\s+decided|tbd|"
    r"at\s+the\s+discretion|at\s+our\s+discretion)\b"
)


def discretionary_from_text(text: str | None) -> bool | None:
    """True if the wording is open-ended ('as agreed', 'to be negotiated', ...). False only when the wording
    states a numeric period and contains no such phrase. Otherwise None (UNKNOWN): 'not discretionary' is a
    positive claim and is never assumed (models.PaymentTerms)."""
    if not text or not text.strip():
        return None
    if _DISCRETIONARY.search(text):
        return True
    if periods_in_text(text):
        return False
    return None
