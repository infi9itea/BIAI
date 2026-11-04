"""Scoring for the M6 evaluation. Pure functions: no API call, no file writes, no pipeline run.

Four things are scored SEPARATELY, because a correct final decision can hide a wrong intermediate value:

1. EXTRACTION    each field the model returned, compared with ``field_ground_truth.json``.
                 Two views: the MODEL view (what the model said, normalized but not yet verified) and the PIPELINE
                 view (what survived evidence verification and so reached the rules).
2. EVIDENCE      each quote the model gave, checked independently of the verifier. Three questions are kept apart:
                 is the quote verbatim on the page it cites; does it support the value the MODEL CLAIMED
                 (``supports_claimed``); does it support the CORRECT value (``supports_expected``). Whether the claimed
                 value is itself correct is the extraction outcome. A verifier false accept is judged against the claim.
3. ARITHMETIC    each arithmetic check against the expected recalculation.
4. DECISION      rule IDs raised and the final decision against ``ground_truth.json`` (Checkpoint G).

A pack whose processing failed (API error, invalid JSON, unreadable PDF) is a failure in every column. In
particular a NEEDS_REVIEW produced by a processing error is never counted as agreeing with a NEEDS_REVIEW ground
truth. Nothing here substitutes fixtures for a missing live result.
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from src.extractor import _supplied, normalize_extraction
from src.models import (
    Decision,
    ExtractedDocument,
    FieldState,
    PackResult,
    PurchaseRequest,
    RawExtraction,
    RawField,
    RuleStatus,
    VerificationStatus,
    is_usable,
)
from src.parsing import NUMBER_PATTERN, normalize_text, periods_in_text, to_decimal

EVAL_DIR = Path(__file__).resolve().parent
DECISION_GT_PATH = EVAL_DIR / "ground_truth.json"
FIELD_GT_PATH = EVAL_DIR / "field_ground_truth.json"

# Outcomes for one field. CORRECT and CORRECT_NULL are the two kinds of right answer.
CORRECT, CORRECT_NULL, WRONG, MISSED, SPURIOUS, NOT_EXTRACTED = (
    "CORRECT", "CORRECT_NULL", "WRONG", "MISSED", "SPURIOUS", "NOT_EXTRACTED")
RIGHT = (CORRECT, CORRECT_NULL)

# Fields the verifier derives from layout rather than the model supplying them (see the prompt: the model must
# return null for vat_price_basis). They are scored in the model view only.
VERIFIER_DERIVED = {"vat_price_basis"}


# ---------------------------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------------------------


def load_decision_gt(path: Path = DECISION_GT_PATH) -> dict[str, dict]:
    return {e["pack"]: e for e in json.loads(path.read_text(encoding="utf-8"))}


def load_field_gt(path: Path = FIELD_GT_PATH) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))["packs"]


# ---------------------------------------------------------------------------------------------
# Field specs: how to read one ground-truth field from the raw, model-view and pipeline-view objects
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    name: str  # key in field_ground_truth.json
    kind: str  # decimal | int | str | bool
    raw: str  # RawExtraction / RawLineItem attribute that carries the quote
    model: Callable[[Any], Any]  # model-view doc/line -> observed value
    pipe: Callable[[Any], Any]  # pipeline-view doc/line -> (usable value, Evidence | None)
    casefold: bool = False
    evidence: bool = True  # False when another spec already covers the same raw field


def _fr(attr: str):
    """FieldRecord on the document or a line item."""
    return (lambda d: getattr(d, attr).value,
            lambda d: (getattr(d, attr).value if is_usable(getattr(d, attr)) else None, getattr(d, attr).evidence))


def _sub(owner: str, attr: str):
    """A component of the PaymentTerms / PenaltyClause: usable only when the clause wording is verified."""
    return (lambda d: getattr(getattr(d, owner), attr),
            lambda d: (getattr(getattr(d, owner), attr) if is_usable(getattr(d, owner)) else None,
                       getattr(d, owner).evidence))


def _penalty_present():
    def model(d):
        return True if d.penalty.state == FieldState.FOUND else None

    def pipe(d):
        usable = is_usable(d.penalty)
        value = True if usable else (False if d.penalty.state == FieldState.ABSENT else None)
        return value, d.penalty.evidence

    return model, pipe


def _spec(name, kind, raw, getters, **kw) -> Spec:
    return Spec(name, kind, raw, getters[0], getters[1], **kw)


DOC_SPECS: list[Spec] = [
    _spec("supplier_name", "str", "supplier_name", _fr("supplier_name")),
    _spec("tin", "str", "tin", _fr("tin")),
    _spec("bin", "str", "bin", _fr("bin")),
    _spec("subtotal_bdt", "decimal", "subtotal_bdt", _fr("subtotal_bdt")),
    _spec("vat_rate_pct", "decimal", "vat_rate_pct", _fr("vat_rate_pct")),
    _spec("vat_amount_bdt", "decimal", "vat_amount_bdt", _fr("vat_amount_bdt")),
    _spec("grand_total_bdt", "decimal", "grand_total_bdt", _fr("grand_total_bdt")),
    _spec("vat_shown_separately", "bool", "vat_shown_separately", _fr("vat_shown_separately")),
    _spec("vat_price_basis", "str", "vat_price_basis", _fr("vat_price_basis")),
    _spec("delivery_days", "int", "delivery_period", _fr("delivery_days")),
    _spec("payment_min_days", "int", "payment_period", _sub("payment", "min_days")),
    _spec("payment_max_days", "int", "payment_period", _sub("payment", "max_days"), evidence=False),
    _spec("advance_payment_pct", "decimal", "advance_payment_pct", _fr("advance_payment_pct")),
    _spec("penalty_present", "bool", "penalty_wording", _penalty_present()),
    _spec("penalty_rate_pct", "decimal", "penalty_rate_pct", _sub("penalty", "rate_pct")),
    _spec("penalty_calculation_basis", "str", "penalty_calculation_basis", _sub("penalty", "calculation_basis"),
          casefold=True),
    _spec("penalty_period_unit", "str", "penalty_period", _sub("penalty", "period_unit"), casefold=True),
    _spec("penalty_full_weeks_only", "bool", "penalty_full_weeks_only", _sub("penalty", "full_weeks_only")),
    _spec("validity_days", "int", "validity_period", _fr("validity_days")),
]

LINE_SPECS: list[Spec] = [
    _spec("item_code", "str", "item_code", _fr("item_code")),
    _spec("description", "str", "description", _fr("description")),
    _spec("quantity", "decimal", "quantity", _fr("quantity")),
    _spec("unit", "str", "unit", _fr("unit"), casefold=True),
    _spec("unit_price_bdt", "decimal", "unit_price_bdt", _fr("unit_price_bdt")),
    _spec("line_total_bdt", "decimal", "line_total_bdt", _fr("line_total_bdt")),
    _spec("vat_included_in_price", "bool", "vat_included_in_price", _fr("vat_included_in_price")),
]


# ---------------------------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------------------------


def _same(kind: str, expected: Any, observed: Any, casefold: bool = False) -> bool:
    try:
        if kind == "decimal":
            return Decimal(str(expected)) == Decimal(str(observed))
        if kind == "int":
            return int(expected) == int(observed)
        if kind == "bool":
            return bool(expected) == bool(observed)
        a, b = normalize_text(str(expected)), normalize_text(str(observed))
        return a.casefold() == b.casefold() if casefold else a == b
    except (InvalidOperation, ValueError):
        return False


def _outcome(kind: str, expected: Any, observed: Any, casefold: bool = False) -> str:
    if expected is None:
        return CORRECT_NULL if observed is None else SPURIOUS
    if observed is None:
        return MISSED
    return CORRECT if _same(kind, expected, observed, casefold) else WRONG


def _model_expected(spec: Spec, expected: Any) -> Any:
    """penalty_present: 'no clause' (False) is what a model that returns nothing is right about."""
    return None if (spec.name == "penalty_present" and expected is False) else expected


# ---------------------------------------------------------------------------------------------
# Evidence checks, independent of the verifier
# ---------------------------------------------------------------------------------------------


def _numbers(text: str) -> list[Decimal]:
    found = []
    for token in re.findall(NUMBER_PATTERN, text):
        value = to_decimal(token)
        if value is not None:
            found.append(value)
    return found


def quote_supports(spec: Spec, expected: Any, quote: str) -> bool | None:
    """Does the quote contain what the expected value is made from? None = not automatically checkable."""
    text = normalize_text(quote)
    lowered = text.casefold()
    name = spec.name
    if expected is None or name in ("vat_price_basis", "payment_max_days"):
        return None
    if name == "penalty_present":
        return bool(re.search(r"penalt|delay|late", lowered)) if expected else None
    if name == "vat_shown_separately":
        return ("vat" in lowered) if expected else bool(re.search(r"includ", lowered))
    if name == "vat_included_in_price":
        return bool(re.search(r"vat\s+includ", lowered)) if expected else bool(re.search(r"exclud", lowered))
    if name == "penalty_full_weeks_only":
        return ("full week" in lowered) if expected else None
    if name == "penalty_period_unit":
        return str(expected) in lowered
    if name == "advance_payment_pct" and Decimal(str(expected)) == 0:
        return bool(re.search(r"no\s+advance|advance.*(?:not required|nil)", lowered))
    if name in ("delivery_days", "validity_days"):
        return (int(expected), int(expected)) in periods_in_text(text)
    if name == "payment_min_days":
        return any(low == int(expected) for low, _ in periods_in_text(text))
    if spec.kind == "decimal":
        return Decimal(str(expected)) in _numbers(text)
    if name == "item_code":
        return bool(re.search(rf"(?:^|\s){re.escape(str(expected))}(?:\s|$)", text))
    return normalize_text(str(expected)).casefold() in lowered


def evidence_check(spec: Spec, raw_field: RawField, pages: dict[int, str], expected: Any, pipe_evidence: Any,
                   claimed: Any = None) -> dict:
    """Independent check of one claimed quote, plus the verifier's own verdict on the same field.

    ``claimed`` is the value the model actually returned (normalized). Three separate facts are reported:
    * ``quote_on_claimed_page``  the quote exists, verbatim, on the page the model cited;
    * ``supports_claimed``       the quote supports the model's CLAIMED value (None = not automatically checkable);
    * ``supports_expected``      the quote supports the GROUND-TRUTH value.
    Whether the claim is correct is the row's ``model_outcome``, not recorded here. A verifier is judged on
    ``supports_claimed``: with expected 20,000, claimed 21,000 and a quote reading 20,000, the quote supports the
    expected value but not the claim, so a verifier that accepts it has made a false accept."""
    quote = raw_field.source_quote or ""
    norm_quote = normalize_text(quote)
    page_text = normalize_text(pages.get(raw_field.page_number, "")) if raw_field.page_number else ""
    anywhere = bool(norm_quote) and any(norm_quote in normalize_text(t) for t in pages.values())
    return {
        "quote": quote,
        "page_number": raw_field.page_number,
        "quote_on_claimed_page": bool(norm_quote) and norm_quote in page_text,
        "quote_anywhere": anywhere,
        "claimed_value": None if claimed is None else str(claimed),
        "supports_claimed": quote_supports(spec, claimed, quote) if quote else False,
        "supports_expected": quote_supports(spec, expected, quote) if quote else False,
        "verifier_status": pipe_evidence.verification_status.value if pipe_evidence is not None else None,
    }


# ---------------------------------------------------------------------------------------------
# One pack
# ---------------------------------------------------------------------------------------------


@dataclass
class PackRun:
    """Everything the scorer needs about one pack's run. ``raw`` is None when no extraction was obtained."""

    pack: str
    result: PackResult
    raw: RawExtraction | None
    pdf_result: dict | None
    attempts: list[dict]  # one record per model call (empty for a pre-API failure)


def _field_row(scope: str, spec: Spec, expected: Any, raw_field: RawField | None, model_doc, pipe_doc,
               pages: dict[int, str]) -> dict:
    row: dict[str, Any] = {"scope": scope, "field": spec.name, "expected": expected}
    if raw_field is None or model_doc is None:  # the line/doc does not exist in this extraction
        row.update(model_value=None, model_outcome=NOT_EXTRACTED if expected is not None else CORRECT_NULL,
                   claimed=False, pipeline_value=None,
                   pipeline_outcome=NOT_EXTRACTED if expected is not None else CORRECT_NULL, evidence=None)
        return row
    observed = spec.model(model_doc)
    claimed = _supplied(raw_field)
    outcome = _outcome(spec.kind, _model_expected(spec, expected), observed, spec.casefold)
    row.update(model_value=None if observed is None else str(observed), claimed=claimed, model_outcome=outcome,
               model_expects_value=_model_expected(spec, expected) is not None)

    if spec.name in VERIFIER_DERIVED:
        row.update(pipeline_value=None, pipeline_outcome="SKIPPED", evidence=None)
        return row
    value, evidence = spec.pipe(pipe_doc)
    row["pipeline_value"] = None if value is None else str(value)
    row["pipeline_outcome"] = _outcome(spec.kind, expected, value, spec.casefold)
    row["evidence"] = (evidence_check(spec, raw_field, pages, expected, evidence, observed)
                       if (claimed and spec.evidence) else None)
    return row


def _failed_rows(gt_fields: dict, gt_lines: list[dict]) -> list[dict]:
    """Every ground-truth field of a pack that produced no extraction: a failure unless the right answer is null."""
    rows = []
    for scope, specs, values in [("doc", DOC_SPECS, gt_fields)] + [
            (f"line-{n}", LINE_SPECS, line) for n, line in enumerate(gt_lines, start=1)]:
        for spec in specs:
            expected = values.get(spec.name)
            outcome = CORRECT_NULL if expected is None else NOT_EXTRACTED
            rows.append({"scope": scope, "field": spec.name, "expected": expected, "model_value": None,
                         "model_outcome": outcome, "claimed": False, "pipeline_value": None,
                         "pipeline_outcome": outcome, "evidence": None})
    return rows


def _pages(pdf_result: dict | None) -> dict[int, str]:
    return {p["page_number"]: p["text"] for p in (pdf_result or {}).get("pages", [])}


def score_extraction(run: PackRun, gt: dict, pr: PurchaseRequest) -> list[dict]:
    gt_fields, gt_lines = gt["fields"], gt["line_items"]
    if run.raw is None or run.result.extracted is None:
        return _failed_rows(gt_fields, gt_lines)
    model_doc: ExtractedDocument = normalize_extraction(run.raw, pr)
    pipe_doc: ExtractedDocument = run.result.extracted
    pages = _pages(run.pdf_result)
    rows = [_field_row("doc", s, gt_fields.get(s.name), getattr(run.raw, s.raw), model_doc, pipe_doc, pages)
            for s in DOC_SPECS]
    for n, gt_line in enumerate(gt_lines, start=1):
        have = n <= len(run.raw.line_items) and n <= len(model_doc.line_items) and n <= len(pipe_doc.line_items)
        for s in LINE_SPECS:
            rows.append(_field_row(
                f"line-{n}", s, gt_line.get(s.name),
                getattr(run.raw.line_items[n - 1], s.raw) if have else None,
                model_doc.line_items[n - 1] if have else None, pipe_doc.line_items[n - 1] if have else None, pages))
    # Lines the model returned beyond the ground truth are fabricated quotation lines. Every value on such a line is
    # SPURIOUS (the key has nothing there), so fields_correct and value_precision fall, and categorize() reports one
    # SPURIOUS_LINE per extra line instead of one SPURIOUS_VALUE per field.
    for n in range(len(gt_lines) + 1, len(run.raw.line_items) + 1):
        in_model, in_pipe = n <= len(model_doc.line_items), n <= len(pipe_doc.line_items)
        for s in LINE_SPECS:
            row = _field_row(f"line-{n}", s, None, getattr(run.raw.line_items[n - 1], s.raw) if in_model else None,
                             model_doc.line_items[n - 1] if in_model else None,
                             pipe_doc.line_items[n - 1] if in_pipe else None, pages)
            row["spurious_line"] = True
            row["evidence"] = None  # there is no correct value to judge a quote against
            rows.append(row)
    return rows


def line_counts(run: PackRun, gt_lines: list[dict]) -> dict:
    """Expected vs returned line items. ``model`` is what the model returned (0 when nothing was obtained)."""
    returned = len(run.raw.line_items) if run.raw is not None else 0
    expected = len(gt_lines)
    return {"expected": expected, "model": returned, "spurious": max(0, returned - expected),
            "missing": max(0, expected - returned)}


# What each arithmetic check is computed from (src/arithmetic.py), as (scope, field) pairs of the field rows. A check
# is "inherited from extraction" only when one of ITS OWN inputs was extracted wrongly, so a wrong grand total does not
# taint the per-line recalculation. Notes on the dependency list:
#   * independent_subtotal is the sum of the recalculated lines (quantity x unit price) compared with the printed
#     subtotal; the printed line totals are not used.
#   * vat uses the recalculated subtotal and the printed RATE, compared with the printed VAT amount.
#   * grand uses the recalculated subtotal plus that expected VAT (so again the rate), compared with the printed total.
#     The printed VAT amount is not an input of the grand-total check.
#   * vat_price_basis is also needed by the VAT and grand checks, but it is derived by the verifier from layout and is
#     not scored in the pipeline view, so it cannot be named here.


def arithmetic_dependencies(check_name: str, item_id: str | None, line_ids: list[str]) -> set[tuple[str, str]]:
    quantities_and_prices = {(scope, name) for scope in line_ids for name in ("quantity", "unit_price_bdt")}
    if check_name == "line_recalc":
        return {(item_id, name) for name in ("quantity", "unit_price_bdt", "line_total_bdt")} if item_id else set()
    if check_name == "independent_subtotal":
        return quantities_and_prices | {("doc", "subtotal_bdt")}
    if check_name == "vat":
        return quantities_and_prices | {("doc", "vat_rate_pct"), ("doc", "vat_amount_bdt")}
    if check_name == "grand":
        return quantities_and_prices | {("doc", "vat_rate_pct"), ("doc", "grand_total_bdt")}
    return set()


def _check_label(check_name: str, item_id: str | None) -> str:
    return f"{check_name}" + (f":{item_id}" if item_id else "")


def score_arithmetic(run: PackRun, gt: dict, field_rows: list[dict]) -> list[dict]:
    """Each ground-truth arithmetic check against what the pipeline calculated: status, recalculated value and the
    PRINTED value the pipeline compared against. A check is 'inherited' when one of the extracted numbers it depends
    on was itself wrong (see ``arithmetic_dependencies``), so the arithmetic is not the first thing that failed."""
    wrong = {(r["scope"], r["field"]) for r in field_rows
             if r["pipeline_outcome"] not in RIGHT and r["pipeline_outcome"] != "SKIPPED"}
    line_ids = [f"line-{n}" for n in range(1, len(gt["line_items"]) + 1)]
    by_key = {(c.check_name, c.item_id): c for c in run.result.arithmetic_checks}
    rows = []
    for expected in gt["arithmetic"]:
        key = (expected["check_name"], expected["item_id"])
        got = by_key.get(key)
        bad_inputs = sorted(wrong & arithmetic_dependencies(key[0], key[1], line_ids))
        row = {"check": _check_label(*key), "expected_status": expected["expected_status"],
               "expected_calculated": expected["expected_calculated"], "expected_printed": expected["expected_printed"],
               "status": None, "calculated": None, "printed": None,
               "status_match": False, "value_match": False, "printed_match": False,
               "inherited_from_extraction": bool(bad_inputs),
               "wrong_inputs": [f"{scope}.{name}" for scope, name in bad_inputs]}
        if got is not None:
            row.update(status=got.status, calculated=None if got.calculated is None else str(got.calculated),
                       printed=None if got.printed is None else str(got.printed))
            row["status_match"] = got.status == expected["expected_status"]
            row["value_match"] = got.calculated is not None and got.calculated == Decimal(expected["expected_calculated"])
            row["printed_match"] = got.printed is not None and got.printed == Decimal(expected["expected_printed"])
        rows.append(row)
    return rows


# Rules the engine raises once per line item (3k results for k lines); every other rule is document-level.
PER_ITEM_RULES = frozenset({"R-01", "NR-01", "NR-02"})


def triggered_rules(result: PackResult) -> list[str]:
    return sorted({r.rule_id for r in result.rule_results if r.status in (RuleStatus.FAIL, RuleStatus.UNCERTAIN)})


def triggered_rule_items(result: PackResult) -> list[str]:
    """Rule results that fired, keeping WHICH line they fired on: 'R-01@line-1' for a per-item rule, 'R-04' otherwise.
    A rule raised on the wrong line (or a document-level rule given a line, or the reverse) is visible here even when
    the set of rule IDs is unchanged."""
    return sorted({f"{r.rule_id}@{r.item_id}" if r.item_id else r.rule_id for r in result.rule_results
                   if r.status in (RuleStatus.FAIL, RuleStatus.UNCERTAIN)})


def expected_rule_items(rule_ids: list[str], line_ids: list[str]) -> list[str] | None:
    """Expected rule results with line attribution, derived from the decision key plus the line structure of the
    field key. ground_truth.json records rule IDs only, so a per-item rule can be pinned to a line only when the pack
    has exactly one line (all ten do). With several lines it cannot, and None means 'not checkable'."""
    if len(line_ids) != 1 and any(r in PER_ITEM_RULES for r in rule_ids):
        return None
    return sorted(f"{r}@{line_ids[0]}" if r in PER_ITEM_RULES else r for r in rule_ids)


def score_pack(run: PackRun, decision_gt: dict, field_gt: dict, pr: PurchaseRequest) -> dict:
    result = run.result
    ok = result.processing_status == "OK"
    field_rows = score_extraction(run, field_gt, pr)
    arithmetic_rows = score_arithmetic(run, field_gt, field_rows) if ok else [
        {"check": _check_label(c["check_name"], c["item_id"]),
         "expected_status": c["expected_status"], "expected_calculated": c["expected_calculated"],
         "expected_printed": c["expected_printed"], "status": None, "calculated": None, "printed": None,
         "status_match": False, "value_match": False, "printed_match": False, "inherited_from_extraction": False,
         "wrong_inputs": []}
        for c in field_gt["arithmetic"]]
    expected_rules, got_rules = sorted(decision_gt["rule_ids"]), triggered_rules(result) if ok else []
    got_items = triggered_rule_items(result) if ok else []
    expected_items = expected_rule_items(expected_rules, [f"line-{n}" for n in range(1, len(field_gt["line_items"]) + 1)])
    score = {
        "pack": run.pack,
        "processing_status": result.processing_status,
        "processing_error_detail": result.processing_error_detail,
        "expected_decision": decision_gt["expected_decision"],
        "decision": result.decision.value,
        # A processing failure can never agree, even when it happens to say NEEDS_REVIEW.
        "decision_agrees": ok and result.decision.value == decision_gt["expected_decision"],
        "expected_rules": expected_rules,
        "triggered_rules": got_rules,
        "rules_exact": ok and got_rules == expected_rules,
        "rules_missing": sorted(set(expected_rules) - set(got_rules)),
        "rules_extra": sorted(set(got_rules) - set(expected_rules)),
        # the same comparison with the line each per-item rule fired on (None = not checkable for this key)
        "expected_rule_items": expected_items,
        "triggered_rule_items": got_items,
        "rule_items_exact": None if expected_items is None else (ok and got_items == expected_items),
        "rule_items_missing": None if expected_items is None else sorted(set(expected_items) - set(got_items)),
        "rule_items_extra": None if expected_items is None else sorted(set(got_items) - set(expected_items)),
        "line_counts": line_counts(run, field_gt["line_items"]),
        "gt_confidence": decision_gt["confidence"],
        "fields": field_rows,
        "arithmetic": arithmetic_rows,
        "model_calls": len(run.attempts),
    }
    score["failures"] = categorize(score)
    return score


# ---------------------------------------------------------------------------------------------
# Failure analysis
# ---------------------------------------------------------------------------------------------

_ERROR_CATEGORY = {
    "extraction_failed": "API_FAILURE", "extraction_invalid_json": "INVALID_JSON", "pdf_unreadable": "PDF_READ_FAILURE",
    "pdf_no_text": "PDF_READ_FAILURE", "unexpected_exception": "UNEXPECTED_EXCEPTION",
}


def independently_supported(ev: dict) -> bool:
    """The quote is on the cited page and does not contradict the model's CLAIMED value (None = cannot tell, which is
    not held against the verifier)."""
    return bool(ev["quote_on_claimed_page"]) and ev["supports_claimed"] is not False


def verifier_false_accept(ev: dict) -> bool:
    """The verifier marked the field VERIFIED although the quote does not support what the model claimed."""
    return ev["verifier_status"] == VerificationStatus.VERIFIED.value and not independently_supported(ev)


def categorize(score: dict) -> list[dict]:
    """Every discrepancy in one pack, labelled. Order follows the pipeline, so the earliest cause is listed first."""
    out: list[dict] = []

    def add(level: str, category: str, detail: str) -> None:
        out.append({"pack": score["pack"], "level": level, "category": category, "detail": detail})

    if score["processing_status"] != "OK":
        code = score["processing_status"].removeprefix("ERROR: ")
        add("processing", _ERROR_CATEGORY.get(code, "PROCESSING_ERROR"), score["processing_error_detail"] or code)
    else:
        counts = score["line_counts"]
        if counts["spurious"]:
            add("extraction", "SPURIOUS_LINE",
                f"model returned {counts['model']} line item(s), expected {counts['expected']}: "
                f"{counts['spurious']} fabricated line(s) "
                f"({', '.join(f'line-{n}' for n in range(counts['expected'] + 1, counts['model'] + 1))})")
        for r in score["fields"]:
            if r.get("spurious_line"):
                continue  # reported once, as SPURIOUS_LINE, above
            where = f"{r['scope']}.{r['field']}"
            if r["model_outcome"] == WRONG:
                add("extraction", "WRONG_VALUE", f"{where}: model {r['model_value']!r}, expected {r['expected']!r}")
            elif r["model_outcome"] == MISSED:
                why = "claimed but could not be normalized" if r["claimed"] else "model returned nothing"
                add("extraction", "MISSED_VALUE", f"{where}: expected {r['expected']!r}; {why}")
            elif r["model_outcome"] == SPURIOUS:
                add("extraction", "SPURIOUS_VALUE", f"{where}: model {r['model_value']!r}, expected null")
            ev = r.get("evidence")
            if ev:
                if not ev["quote_on_claimed_page"]:
                    add("evidence", "QUOTE_NOT_VERBATIM_ON_PAGE" if not ev["quote_anywhere"] else "QUOTE_WRONG_PAGE",
                        f"{where}: {ev['quote'][:80]!r} (page {ev['page_number']})")
                elif ev["supports_expected"] is False and r["model_outcome"] == CORRECT:
                    add("evidence", "QUOTE_DOES_NOT_SUPPORT_VALUE", f"{where}: {ev['quote'][:80]!r}")
                elif ev["supports_claimed"] is False and r["model_outcome"] != CORRECT:
                    add("evidence", "QUOTE_DOES_NOT_SUPPORT_CLAIMED_VALUE",
                        f"{where}: claimed {ev['claimed_value']!r}, quote {ev['quote'][:80]!r}")
                if verifier_false_accept(ev):
                    add("verification", "VERIFIER_FALSE_ACCEPT",
                        f"{where}: verified, but the quote does not support the claimed value {ev['claimed_value']!r}")
                if r["model_outcome"] == CORRECT and r["pipeline_outcome"] != CORRECT:
                    add("verification", "VERIFIER_REJECTED_CORRECT_VALUE", f"{where}: verifier status {ev['verifier_status']}")
                if r["model_outcome"] in (WRONG, SPURIOUS) and r["pipeline_outcome"] in (WRONG, SPURIOUS):
                    add("verification", "VERIFIED_WRONG_VALUE",
                        f"{where}: wrong value passed verification ({ev['verifier_status']})")
            if r["field"] in VERIFIER_DERIVED:
                continue
            if r["model_outcome"] in RIGHT and r["pipeline_outcome"] not in RIGHT and not ev:
                if r["expected"] is None:
                    add("verification", "PIPELINE_INVENTED_VALUE", f"{where}: pipeline holds {r['pipeline_value']!r}, expected null")
                else:  # e.g. the model rightly returned no penalty clause but absence was not established
                    add("verification", "ABSENCE_NOT_ESTABLISHED", f"{where}: expected {r['expected']!r}, pipeline {r['pipeline_outcome']}")
        for a in score["arithmetic"]:
            if not (a["status_match"] and a["value_match"] and a["printed_match"]):
                cause = "ARITHMETIC_INHERITED_FROM_EXTRACTION" if a["inherited_from_extraction"] else "ARITHMETIC_MISMATCH"
                add("arithmetic", cause,
                    f"{a['check']}: status {a['status']} (expected {a['expected_status']}), calculated "
                    f"{a['calculated']} (expected {a['expected_calculated']}), printed {a['printed']} "
                    f"(expected {a['expected_printed']})"
                    + (f"; wrong input(s): {', '.join(a['wrong_inputs'])}" if a["wrong_inputs"] else ""))
        if not score["rules_exact"]:
            upstream = any(f["level"] in ("extraction", "evidence", "verification", "arithmetic") for f in out)
            add("rules", "RULES_DIFFER_AFTER_UPSTREAM_ERROR" if upstream else "RULES_DIFFER_NO_UPSTREAM_ERROR",
                f"missing {score['rules_missing']}, extra {score['rules_extra']}")
        elif score["rule_items_exact"] is False:  # same rule IDs, but raised on a different line than expected
            add("rules", "RULE_ATTRIBUTED_TO_WRONG_ITEM",
                f"missing {score['rule_items_missing']}, extra {score['rule_items_extra']}")
    if not score["decision_agrees"]:
        add("decision", "DECISION_MISMATCH", f"pipeline {score['decision']} vs ground truth {score['expected_decision']}"
            + ("" if score["processing_status"] == "OK" else f" ({score['processing_status']})"))
    elif out:
        add("decision", "RIGHT_DECISION_WRONG_INTERMEDIATE",
            "decision agrees but an intermediate result above is wrong; the agreement is not evidence of correct extraction")
    return out


# ---------------------------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------------------------


def _ratio(num: int, den: int) -> dict:
    return {"n": num, "of": den, "pct": round(100 * num / den, 1) if den else None}


def aggregate(packs: list[dict]) -> dict:
    fields = [r for p in packs for r in p["fields"]]
    scored_pipe = [r for r in fields if r["pipeline_outcome"] != "SKIPPED"]
    m, pv = Counter(r["model_outcome"] for r in fields), Counter(r["pipeline_outcome"] for r in scored_pipe)

    def view(counter: Counter, rows: list[dict], key: str) -> dict:
        right = sum(counter[o] for o in RIGHT)
        value_rows = [r for r in rows if r.get("model_expects_value", r["expected"] is not None)
                      ] if key == "model_outcome" else [r for r in rows if r["expected"] is not None]
        return {
            "fields_correct": _ratio(right, len(rows)),
            "outcomes": dict(counter),
            # of the fields that do have a value in the PDF, how many were extracted correctly
            "value_recall": _ratio(sum(1 for r in value_rows if r[key] == CORRECT), len(value_rows)),
            # of the values returned, how many were right (a wrong or spurious value is the dangerous error)
            "value_precision": _ratio(counter[CORRECT], counter[CORRECT] + counter[WRONG] + counter[SPURIOUS]),
        }

    per_field: dict[str, Counter] = defaultdict(Counter)
    for r in fields:
        per_field[r["field"]][r["model_outcome"]] += 1

    evidence = [(r, r["evidence"]) for r in fields if r.get("evidence")]
    correct_ev = [(r, e) for r, e in evidence if r["model_outcome"] == CORRECT]
    checkable = [(r, e) for r, e in correct_ev if e["supports_expected"] is not None]
    claim_checkable = [(r, e) for r, e in evidence if e["supports_claimed"] is not None]
    verified = [(r, e) for r, e in evidence if e["verifier_status"] == VerificationStatus.VERIFIED.value]
    arithmetic = [a for p in packs for a in p["arithmetic"]]
    own_arith = [a for a in arithmetic if not a["inherited_from_extraction"]]

    rule_tp: Counter = Counter()
    rule_fp: Counter = Counter()
    rule_fn: Counter = Counter()
    for p in packs:
        for rid in set(p["expected_rules"]) & set(p["triggered_rules"]):
            rule_tp[rid] += 1
        for rid in p["rules_extra"]:
            rule_fp[rid] += 1
        for rid in p["rules_missing"]:
            rule_fn[rid] += 1

    labels = ["APPROVE", "NEEDS_REVIEW", "REJECT"]
    confusion = {e: {g: sum(1 for p in packs if p["expected_decision"] == e and p["decision"] == g
                            and p["processing_status"] == "OK") for g in labels} for e in labels}
    failures = [f for p in packs for f in p["failures"]]
    agree = [p for p in packs if p["decision_agrees"]]
    clean = [p for p in agree if not any(f["category"] != "RIGHT_DECISION_WRONG_INTERMEDIATE" for f in p["failures"])]
    return {
        "packs_scored": len(packs),
        "extraction_model_view": view(m, fields, "model_outcome"),
        "extraction_pipeline_view": view(pv, scored_pipe, "pipeline_outcome"),
        "extraction_per_field": {k: dict(v) for k, v in sorted(per_field.items())},
        "evidence": {
            "quotes_checked": len(evidence),
            "verbatim_on_claimed_page": _ratio(sum(1 for _, e in evidence if e["quote_on_claimed_page"]), len(evidence)),
            "supports_correct_value": _ratio(sum(1 for _, e in checkable if e["supports_expected"]), len(checkable)),
            # does the quote support what the model CLAIMED (right or wrong)? Independent of ground-truth correctness.
            "supports_claimed_value": _ratio(sum(1 for _, e in claim_checkable if e["supports_claimed"]),
                                             len(claim_checkable)),
            # Verifier errors are judged against the model's CLAIM, never against the ground-truth value.
            # false accept: VERIFIED although the quote is off the cited page or does not support the claimed value.
            # false reject: not VERIFIED although the claim is correct AND its quote is on the page and supports it.
            "verifier_false_accepts": sum(1 for _, e in verified if not independently_supported(e)),
            "verifier_false_rejects": sum(1 for r, e in correct_ev
                                          if independently_supported(e) and e["verifier_status"] != "VERIFIED"),
            # VERIFIED fields whose claimed value is wrong (a different question: the key says the claim is wrong,
            # whether or not the quote shows it). Reported separately so the two are never conflated.
            "verified_but_claim_wrong": sum(1 for r, _ in verified if r["model_outcome"] in (WRONG, SPURIOUS)),
            # VERIFIED fields whose claim-vs-quote support cannot be checked automatically
            "verified_claim_uncheckable": sum(1 for _, e in verified if e["supports_claimed"] is None),
        },
        "arithmetic": {
            "status_agrees": _ratio(sum(a["status_match"] for a in arithmetic), len(arithmetic)),
            "value_agrees": _ratio(sum(a["value_match"] for a in arithmetic), len(arithmetic)),
            "printed_agrees": _ratio(sum(a["printed_match"] for a in arithmetic), len(arithmetic)),
            "excluding_checks_inherited_from_extraction": {
                "status_agrees": _ratio(sum(a["status_match"] for a in own_arith), len(own_arith)),
                "value_agrees": _ratio(sum(a["value_match"] for a in own_arith), len(own_arith)),
                "printed_agrees": _ratio(sum(a["printed_match"] for a in own_arith), len(own_arith))},
        },
        "line_items": {"expected": sum(p["line_counts"]["expected"] for p in packs),
                       "returned_by_model": sum(p["line_counts"]["model"] for p in packs),
                       "spurious": sum(p["line_counts"]["spurious"] for p in packs),
                       "packs_with_spurious_lines": [p["pack"] for p in packs if p["line_counts"]["spurious"]]},
        "rules": {"packs_with_exact_rule_set": _ratio(sum(p["rules_exact"] for p in packs), len(packs)),
                  # same comparison with the line each per-item rule fired on; packs it cannot be checked for are skipped
                  "packs_with_exact_rule_items": _ratio(
                      sum(1 for p in packs if p["rule_items_exact"]),
                      sum(1 for p in packs if p["rule_items_exact"] is not None)),
                  "true_positive": dict(rule_tp), "false_positive": dict(rule_fp), "false_negative": dict(rule_fn)},
        "decision": {
            "agreement": _ratio(len(agree), len(packs)),
            "processing_failures_counted_incorrect": sum(1 for p in packs if p["processing_status"] != "OK"),
            "confusion_expected_by_pipeline": confusion,
            "agreeing_with_no_other_error": _ratio(len(clean), len(agree)),
            "right_decision_wrong_intermediate": [p["pack"] for p in agree if p not in clean],
        },
        "failure_counts": dict(Counter(f["category"] for f in failures)),
    }
