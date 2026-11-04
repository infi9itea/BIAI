"""Sequential LangGraph workflow (Milestone 4).

Seven fixed nodes, one after another. There is no agent and no free-running loop:

    read_pdf -> extract_fields -> verify_evidence -> check_arithmetic
             -> evaluate_rules -> aggregate_decision -> format_output

Two known failures divert straight to ``format_output`` and become a NEEDS_REVIEW processing-error result:
``PDFReadError`` after ``read_pdf`` and ``ExtractionError`` after ``extract_fields``. Any *other* exception is
deliberately not caught here; it propagates to ``main.py`` (M5), the single place that turns it into a result.

Stage functions are called through their modules (``pdf_reader.extract_text`` and so on) so tests and the
offline replay (M7) can substitute them.
"""

import os
from typing import Any

from langgraph.graph import END, START, StateGraph

from src import arithmetic, extractor, pdf_reader, rule_engine, verifier
from src.extractor import ExtractionError
from src.models import Decision, PackResult, PurchaseRequest
from src.pdf_reader import PDFReadError
from src.state import WorkflowState

NODE_NAMES = (
    "read_pdf",
    "extract_fields",
    "verify_evidence",
    "check_arithmetic",
    "evaluate_rules",
    "aggregate_decision",
    "format_output",
)

_NO_TEXT_MARKER = "no text could be extracted from any"


def build_error_result(pack_filename: str, status_code: str, detail: str) -> PackResult:
    """NEEDS_REVIEW result for a processing failure (SPEC §4.2 / §11). No evidence is ever fabricated."""
    return PackResult(
        pack_filename=pack_filename,
        processing_status=f"ERROR: {status_code}",
        processing_error_detail=detail,
        extracted=None,
        arithmetic_checks=[],
        rule_results=[],
        decision=Decision.NEEDS_REVIEW,
        reason=(
            f"Processing error ({status_code}) — no automated decision could be made; "
            "a human must inspect the original PDF."
        ),
        assumptions_applied=[],
    )


# ---------------------------------------------------------------------------------------------
# Nodes. Each returns only the keys it adds to the state.
# ---------------------------------------------------------------------------------------------


def read_pdf(state: WorkflowState) -> dict[str, Any]:
    try:
        return {"pdf_result": pdf_reader.extract_text(state["pdf_path"])}
    except PDFReadError as exc:
        code = "pdf_no_text" if _NO_TEXT_MARKER in str(exc) else "pdf_unreadable"
        return {"error_code": code, "error_detail": f"{type(exc).__name__}: {exc}"}


def extract_fields(state: WorkflowState) -> dict[str, Any]:
    try:
        doc = extractor.extract_fields(state["pdf_result"], state["pr"], client=state.get("client"))
    except ExtractionError as exc:
        return {"error_code": exc.code, "error_detail": f"{type(exc).__name__}: {exc.detail}"}
    return {"extracted": doc}


def verify_evidence(state: WorkflowState) -> dict[str, Any]:
    return {"extracted": verifier.verify_evidence(state["extracted"], state["pdf_result"])}


def check_arithmetic(state: WorkflowState) -> dict[str, Any]:
    return {"arithmetic_checks": arithmetic.run_arithmetic_checks(state["extracted"])}


def evaluate_rules(state: WorkflowState) -> dict[str, Any]:
    return {
        "rule_results": rule_engine.evaluate_all_rules(
            state["extracted"], state["pr"], state["arithmetic_checks"]
        )
    }


def aggregate_decision(state: WorkflowState) -> dict[str, Any]:
    decision, reason = rule_engine.aggregate_decision(state["rule_results"])
    return {"decision": decision, "reason": reason}


def format_output(state: WorkflowState) -> dict[str, Any]:
    pack_filename = os.path.basename(state["pdf_path"])
    if state.get("error_code"):
        return {
            "result": build_error_result(
                pack_filename, state["error_code"], state.get("error_detail") or state["error_code"]
            )
        }
    rule_results = state["rule_results"]
    return {
        "result": PackResult(
            pack_filename=pack_filename,
            processing_status="OK",
            extracted=state["extracted"],
            arithmetic_checks=state["arithmetic_checks"],
            rule_results=rule_results,
            decision=state["decision"],
            reason=state["reason"],
            assumptions_applied=rule_engine.collect_assumptions(rule_results),
        )
    }


# ---------------------------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------------------------


def _next_or_error(next_node: str):
    def route(state: WorkflowState) -> str:
        return "format_output" if state.get("error_code") else next_node

    return route


def build_graph():
    """Compile the fixed seven-node graph."""
    builder = StateGraph(WorkflowState)
    for name, fn in (
        ("read_pdf", read_pdf),
        ("extract_fields", extract_fields),
        ("verify_evidence", verify_evidence),
        ("check_arithmetic", check_arithmetic),
        ("evaluate_rules", evaluate_rules),
        ("aggregate_decision", aggregate_decision),
        ("format_output", format_output),
    ):
        builder.add_node(name, fn)

    builder.add_edge(START, "read_pdf")
    builder.add_conditional_edges(
        "read_pdf", _next_or_error("extract_fields"), ["extract_fields", "format_output"]
    )
    builder.add_conditional_edges(
        "extract_fields", _next_or_error("verify_evidence"), ["verify_evidence", "format_output"]
    )
    builder.add_edge("verify_evidence", "check_arithmetic")
    builder.add_edge("check_arithmetic", "evaluate_rules")
    builder.add_edge("evaluate_rules", "aggregate_decision")
    builder.add_edge("aggregate_decision", "format_output")
    builder.add_edge("format_output", END)
    return builder.compile()


_GRAPH = None


def run_pipeline(pdf_path: str, pr: PurchaseRequest, client: Any = None) -> PackResult:
    """Run one supplier pack through the workflow. Known failures return a NEEDS_REVIEW result; unexpected
    exceptions propagate (main.py, M5, handles them). ``client`` is an optional injected LLM client."""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    final = _GRAPH.invoke({"pdf_path": os.fspath(pdf_path), "pr": pr, "client": client})
    return final["result"]
