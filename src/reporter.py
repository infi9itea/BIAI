"""Report generator (Milestone 5, SPEC §4.2 and §4.3).

Turns a list of ``PackResult`` into readable text. It only *displays* what the pipeline decided: no rule is
evaluated and no value is computed here, apart from counting decisions for the summary.

Two safety properties:
  * No pack can disappear. Each pack section is formatted independently; if formatting one raises, that pack
    falls back to a minimal block (filename, status, decision, reason) and the report carries on.
  * No evidence is invented. A processing-error pack shows its status code and detail and says plainly that no
    document evidence is available.
"""

from typing import Any

from src.models import (
    AbsenceEvidence,
    ArithmeticCheckResult,
    Decision,
    EvidenceRef,
    ExtractedDocument,
    FieldRecord,
    FieldState,
    LineItem,
    PackResult,
    RuleResult,
    RuleSeverity,
    RuleStatus,
)

HEAVY = "═" * 59
SUMMARY_RULE = "═" * 34
LIGHT = "─" * 29


# ---------------------------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------------------------


def _num(value: Any) -> str:
    """Decimals with thousands separators (never via float); everything else as text."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if hasattr(value, "is_finite"):  # Decimal
        return f"{value:,}"
    return str(value)


def _page(page: int | None) -> str:
    return f"page {page}" if page is not None else "page ?"


def _evidence_line(evidence: Any) -> str:
    text = f'"{evidence.source_quote}" ({_page(evidence.page_number)})'
    status = getattr(evidence.verification_status, "value", str(evidence.verification_status))
    if status != "VERIFIED":
        text += f"  [{status}]"
    if evidence.normalization_method:
        text += f'  Normalised by: "{evidence.normalization_method}"'
    return text


def _absence_line(absence: AbsenceEvidence) -> str:
    pages = ", ".join(str(p) for p in absence.pages_scanned) or "none"
    terms = ", ".join(f'"{t}"' for t in absence.search_terms) or "none"
    scope = "complete extraction" if absence.extraction_complete else "INCOMPLETE extraction"
    parts = [f"{absence.field_searched}: pages scanned: {pages} ({scope}). Search terms: {terms}. Not found."]
    if absence.contextual_excerpt:
        parts.append(f'Nearby text: "{absence.contextual_excerpt}"')
    parts.append(f"Basis: {absence.determination_basis}")
    return " ".join(parts)


def _field(label: str, record: FieldRecord[Any], indent: str = "  ", pad: int = 15) -> list[str]:
    state = record.state.value
    value = _num(record.value) if record.state == FieldState.FOUND else "—"
    lines = [f"{indent}{label:<{pad}}: {value}  [{state}]"]
    if record.evidence is not None:
        lines.append(f"{indent}  Evidence    : {_evidence_line(record.evidence)}")
    if record.absence_evidence is not None:
        lines.append(f"{indent}  Absence     : {_absence_line(record.absence_evidence)}")
    return lines


def _line_item(index: int, item: LineItem) -> list[str]:
    code = item.item_code.value if item.item_code.state == FieldState.FOUND else "?"
    out = [f"    line-{index} (item code {code}):"]
    for label, record, match in (
        ("Item Code", item.item_code, f"Code match: {item.item_code_match}"),
        ("Description", item.description, f"Description match: {item.description_match}"),
        ("Quantity", item.quantity, None),
        ("Unit", item.unit, f"Unit match: {item.unit_match}"),
        ("Unit Price", item.unit_price_bdt, None),
        ("Line Total", item.line_total_bdt, None),
        ("VAT in price", item.vat_included_in_price, None),
    ):
        value = _num(record.value) if record.state == FieldState.FOUND else "—"
        suffix = f"  {match}" if match else ""
        out.append(f"      {label:<12}: {value}  [{record.state.value}]{suffix}")
        if record.evidence is not None:
            out.append(f"        Evidence  : {_evidence_line(record.evidence)}")
        if record.absence_evidence is not None:
            out.append(f"        Absence   : {_absence_line(record.absence_evidence)}")
    return out


def _payment(doc: ExtractedDocument) -> list[str]:
    pay = doc.payment
    wording = f'"{pay.original_wording}"' if pay.original_wording else "—"
    out = [f"  Payment Terms : {wording}  [{pay.state.value}]"]
    anchor = pay.anchor if pay.anchor is not None else "UNKNOWN"
    disc = {True: "YES", False: "NO", None: "UNKNOWN"}[pay.discretionary]
    out.append(
        f"    Min days: {_num(pay.min_days)}  Max days: {_num(pay.max_days)}  "
        f"Anchor: {anchor}  Discretionary: {disc}"
    )
    if pay.evidence is not None:
        out.append(f"    Evidence: {_evidence_line(pay.evidence)}")
    return out


def _penalty(doc: ExtractedDocument) -> list[str]:
    pen = doc.penalty
    out = [f"  Penalty Clause : [{pen.state.value}]"]
    if pen.original_wording:
        out.append(f'    Original wording: "{pen.original_wording}"')
    full = {True: "YES", False: "NO", None: "UNKNOWN"}[pen.full_weeks_only]
    rate = f"{_num(pen.rate_pct)}%" if pen.rate_pct is not None else "UNKNOWN"
    out.append(
        f"    Rate: {rate}  Basis: {pen.calculation_basis or 'UNKNOWN'}  "
        f"Period: {pen.period_unit or 'UNKNOWN'}  Full weeks only: {full}"
    )
    if pen.evidence is not None:
        out.append(f"    Evidence: {_evidence_line(pen.evidence)}")
    if pen.absence_evidence is not None:
        out.append(f"    Absence : {_absence_line(pen.absence_evidence)}")
    return out


def _extracted_fields(doc: ExtractedDocument) -> list[str]:
    out = ["EXTRACTED FIELDS"]
    out += _field("Supplier Name", doc.supplier_name)
    out += _field("TIN", doc.tin)
    out += _field("BIN", doc.bin)
    out += ["", "  Line Items:"]
    if doc.line_items:
        for index, item in enumerate(doc.line_items, start=1):
            out += _line_item(index, item)
    else:
        out.append("    (no line items extracted)")
    out.append("")
    out += _field("Delivery (as written)", doc.delivery_original_wording, pad=21)
    out += _field("Delivery days", doc.delivery_days)
    out += _payment(doc)
    out += _field("Advance Payment", doc.advance_payment_pct)
    if doc.advance_original_wording:
        out.append(f'    Original wording: "{doc.advance_original_wording}"')
    out += _field("VAT rate %", doc.vat_rate_pct)
    out += _field("VAT amount", doc.vat_amount_bdt)
    out += _field("VAT separate", doc.vat_shown_separately)
    out += _field("VAT basis", doc.vat_price_basis)
    out += _field("Subtotal (printed)", doc.subtotal_bdt, pad=18)
    out += _field("Grand total (printed)", doc.grand_total_bdt, pad=21)
    out += _field("Validity (as written)", doc.validity_original_wording, pad=21)
    out += _field("Validity days", doc.validity_days)
    out += _penalty(doc)
    if doc.claimed_without_evidence:
        out.append(f"  Claimed by the model without evidence (ignored): {', '.join(doc.claimed_without_evidence)}")
    return out


def _arithmetic(checks: list[ArithmeticCheckResult]) -> list[str]:
    out = ["", "ARITHMETIC CHECKS (independently recalculated)"]
    if not checks:
        out.append("  (none)")
    for check in checks:
        where = f" [{check.item_id}]" if check.item_id else ""
        out.append(
            f"  {check.check_name}{where}: calculated {_num(check.calculated)} | "
            f"printed {_num(check.printed)} | {check.status}"
        )
        out.append(f"    {check.detail}")
        for ref in check.evidence_refs:
            out.append(f'    evidence: {ref.label}: "{ref.source_quote}" ({_page(ref.page_number)})')
        if check.assumptions_applied:
            out.append(f"    [A] {', '.join(check.assumptions_applied)} applied.")
    return out


def _refs(refs: list[EvidenceRef]) -> list[str]:
    return [f'      evidence: {r.label}: "{r.source_quote}" ({_page(r.page_number)})' for r in refs]


def _rule(result: RuleResult) -> list[str]:
    where = f" [{result.item_id}]" if result.item_id else ""
    head = f"  {result.rule_id:<5}{where} {result.description}  {result.status.value}  severity: {result.severity.value}"
    if result.triggering_value:
        head += f"  triggering: {result.triggering_value}"
    if result.assumptions_applied:
        head += f"  [A] {', '.join(result.assumptions_applied)} applied"
    out = [head, f"      {result.detail}"]
    if result.not_applicable_reason:
        out.append(f"      not applicable: {result.not_applicable_reason}")
    out += _refs(result.evidence_refs)
    out += [f"      absence: {_absence_line(a)}" for a in result.absence_evidence]
    return out


def _rules(results: list[RuleResult]) -> list[str]:
    out = ["", "RULE CHECK RESULTS (all rules evaluated; per-item rules show one line per quoted item)"]
    if not results:
        out.append("  (none)")
    for result in results:
        out += _rule(result)
    return out


def _aggregation(results: list[RuleResult]) -> list[str]:
    rejects = [r for r in results if r.severity == RuleSeverity.REJECT and r.status == RuleStatus.FAIL]
    others = [
        r for r in results
        if r not in rejects and r.status in (RuleStatus.FAIL, RuleStatus.UNCERTAIN)
    ]

    def ids(items: list[RuleResult]) -> str:
        return ", ".join(f"{r.rule_id}{'/' + r.item_id if r.item_id else ''}" for r in items)

    return [
        "",
        "DECISION AGGREGATION",
        f"  Any definite REJECT FAIL? {'YES (' + ids(rejects) + ')' if rejects else 'NO'}",
        f"  Any other FAIL or UNCERTAIN? {'YES (' + ids(others) + ')' if others else 'NO'}",
    ]


def _decision_block(result: PackResult) -> list[str]:
    reason = result.reason.strip().splitlines() or [""]
    out = ["", f"FINAL PROCUREMENT DECISION: {result.decision.value}", f"Reason: {reason[0]}"]
    out += [f"  {line}" for line in reason[1:]]
    if result.assumptions_applied:
        out.append(f"Assumptions applied this pack: {', '.join(result.assumptions_applied)}")
    return out


def _header(result: PackResult) -> list[str]:
    out = [HEAVY, f"Supplier Pack: {result.pack_filename}", f"Processing Status: {result.processing_status}"]
    if result.processing_status != "OK":
        out.append(f"Processing error detail: {result.processing_error_detail}")
    out.append(HEAVY)
    return out


def format_pack(result: PackResult) -> str:
    """One pack's full section (SPEC §4.2)."""
    lines = _header(result)
    if result.processing_status != "OK":
        lines += [
            "No extracted fields, rule results or document evidence are available",
            "(processing error). No evidence is fabricated.",
        ]
        lines += _decision_block(result)
        return "\n".join(lines)
    if result.extracted is not None:
        lines += _extracted_fields(result.extracted)
    lines += _arithmetic(result.arithmetic_checks)
    lines += _rules(result.rule_results)
    lines += _aggregation(result.rule_results)
    lines += _decision_block(result)
    return "\n".join(lines)


def format_pack_fallback(result: Any, error: Exception) -> str:
    """Minimal block used when detailed formatting of a pack failed. Reads attributes defensively so it
    cannot fail itself."""
    decision = getattr(getattr(result, "decision", None), "value", getattr(result, "decision", "UNKNOWN"))
    return "\n".join([
        HEAVY,
        f"Supplier Pack: {getattr(result, 'pack_filename', '<unknown>')}",
        f"Processing Status: {getattr(result, 'processing_status', '<unknown>')}",
        HEAVY,
        f"NOTE: detailed formatting of this pack failed ({type(error).__name__}); showing a minimal block.",
        f"FINAL PROCUREMENT DECISION: {decision}",
        f"Reason: {getattr(result, 'reason', '<unavailable>')}",
    ])


# ---------------------------------------------------------------------------------------------
# Summary (SPEC §4.3)
# ---------------------------------------------------------------------------------------------


def format_summary(results: list[PackResult]) -> str:
    count = {d: sum(1 for r in results if r.decision == d) for d in Decision}
    errors = [r for r in results if r.processing_status != "OK"]
    n = len(results)
    lines = [
        SUMMARY_RULE,
        f"SUMMARY — All {n} Supplier Packs",
        SUMMARY_RULE,
        f"  APPROVE      : {count[Decision.APPROVE]}",
        f"  REJECT       : {count[Decision.REJECT]}",
        f"  NEEDS_REVIEW : {count[Decision.NEEDS_REVIEW]}",
        f"  {LIGHT}",
        f"  Total        : {n}",
        "",
        f"Processing errors (included in NEEDS_REVIEW above): {len(errors)}",
    ]
    lines += [f"  {r.pack_filename}: {r.processing_status}" for r in errors]
    lines.append(SUMMARY_RULE)
    return "\n".join(lines)


def generate_report(results: list[PackResult]) -> str:
    """Full report: one independent section per pack, then the summary."""
    sections: list[str] = []
    for result in results:
        try:
            sections.append(format_pack(result))
        except Exception as exc:  # a formatting bug must never drop a pack from the report
            sections.append(format_pack_fallback(result, exc))
    return "\n\n".join(sections + [format_summary(results)]) + "\n"
