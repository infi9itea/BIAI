"""Workflow state for the LangGraph pipeline (Milestone 4).

One dict flows through the seven fixed nodes. Each node reads what earlier nodes wrote and adds its own
output. ``error_code`` / ``error_detail`` are set only when a known failure (unreadable PDF, failed
extraction) diverts the run straight to ``format_output``.
"""

from typing import Any, TypedDict

from src.models import (
    ArithmeticCheckResult,
    Decision,
    ExtractedDocument,
    PackResult,
    PurchaseRequest,
    RuleResult,
)


class WorkflowState(TypedDict, total=False):
    # inputs
    pdf_path: str
    pr: PurchaseRequest
    client: Any  # optional injected LLM client (offline tests); None means the real Gemini client

    # stage outputs
    pdf_result: dict[str, Any]
    extracted: ExtractedDocument
    arithmetic_checks: list[ArithmeticCheckResult]
    rule_results: list[RuleResult]
    decision: Decision
    reason: str

    # known-failure channel
    error_code: str
    error_detail: str

    # final output
    result: PackResult
