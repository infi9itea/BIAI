"""Quality gate for a live extraction before it is saved as an offline fixture (M3 task 8).

A fixture is only worth keeping if every value the model CLAIMED survived normalization and evidence verification.
This module compares what the model supplied (``RawExtraction``) with what the verifier established
(``ExtractedDocument``) and lists the claims that did not survive.

It deliberately does NOT require every field to be FOUND. A field the model left null is legitimately UNKNOWN or
ABSENT (a pack with no TIN/BIN, no penalty clause, or a penalty clause that does not say what the rate applies to).
That is a correct representation, not a failure, and must never be 'fixed' by fabricating a value.

It reads no rule, makes no decision and is not a source of ground truth.
"""

from src.extractor import _DOC_CLAIMS, _LINE_RAW, _PENALTY_RAW, _supplied
from src.models import ExtractedDocument, FieldState, RawExtraction, is_usable

# For each penalty component the model supplied, the typed attribute that must still be set after verification.
_PENALTY_COMPONENTS = {
    "penalty_rate_pct": "rate_pct",
    "penalty_calculation_basis": "calculation_basis",
    "penalty_period": "period_unit",
    "penalty_full_weeks_only": "full_weeks_only",
}


def claim_failures(raw: RawExtraction, doc: ExtractedDocument) -> list[str]:
    """Names of fields the model supplied a value for that are NOT usable (VERIFIED) in ``doc``.

    Empty list = every claim was established. ``doc`` must be the verified document built from ``raw``."""
    failures: list[str] = []

    for raw_name, targets in _DOC_CLAIMS.items():
        if not _supplied(getattr(raw, raw_name)):
            continue
        for target in targets:
            if target != "payment" and not is_usable(getattr(doc, target)):
                failures.append(f"{target} ({_state(getattr(doc, target))})")

    if _supplied(raw.payment_terms_wording) or _supplied(raw.payment_period):
        if not is_usable(doc.payment):
            failures.append(f"payment ({_state(doc.payment)})")
        elif _supplied(raw.payment_period) and doc.payment.min_days is None:
            failures.append("payment.period (supplied but not converted to days)")

    if any(_supplied(getattr(raw, name)) for name in _PENALTY_RAW):
        if not is_usable(doc.penalty):
            failures.append(f"penalty ({_state(doc.penalty)})")
        else:  # the clause verified; each component the model supplied must also have been confirmed by its quote
            for raw_name, attribute in _PENALTY_COMPONENTS.items():
                if _supplied(getattr(raw, raw_name)) and getattr(doc.penalty, attribute) is None:
                    failures.append(f"penalty.{attribute} (supplied but not confirmed by the clause quote)")

    if len(doc.line_items) != len(raw.line_items):
        failures.append(f"line_items (model gave {len(raw.line_items)}, document has {len(doc.line_items)})")
    for number, (raw_line, line) in enumerate(zip(raw.line_items, doc.line_items), start=1):
        for name in _LINE_RAW:
            if _supplied(getattr(raw_line, name)) and not is_usable(getattr(line, name)):
                failures.append(f"line-{number}.{name} ({_state(getattr(line, name))})")
    return failures


def _state(field) -> str:
    status = field.evidence.verification_status.value if getattr(field, "evidence", None) else "no evidence"
    return f"{field.state.value}, {status}"


def unclaimed_fields(raw: RawExtraction, doc: ExtractedDocument) -> list[str]:
    """Informational: fields not established because the model gave nothing for them (null). Not failures."""
    names: list[str] = []
    for raw_name, targets in _DOC_CLAIMS.items():
        if not _supplied(getattr(raw, raw_name)):
            names.extend(f"{t} ({getattr(doc, t).state.value})" for t in targets if t != "payment")
    if not (_supplied(raw.payment_terms_wording) or _supplied(raw.payment_period)):
        names.append(f"payment ({doc.payment.state.value})")
    if not any(_supplied(getattr(raw, n)) for n in _PENALTY_RAW):
        names.append(f"penalty ({doc.penalty.state.value})")
    elif doc.penalty.state == FieldState.FOUND:
        names.extend(f"penalty.{a} (not stated)" for r, a in _PENALTY_COMPONENTS.items() if not _supplied(getattr(raw, r)))
    return names
