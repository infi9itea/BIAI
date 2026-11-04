"""Milestone 7: offline replay of the ten saved live-model responses through the real pipeline.

For each pack the saved response in ``fixtures/mock_llm_responses/`` is injected in place of the Gemini call (through
the ``client`` argument of ``run_pipeline``, so the extractor's own retry, parsing and validation run). PDF reading,
normalization, evidence verification, arithmetic, rules and aggregation all run for real against the supplied PDFs.
No API key, no network.

This checks that the code and the committed fixtures agree with the manual decision key. It is a regression test, not
an accuracy measure: the fixtures were captured in M3 and the prompt was developed on these same packs (see
``evaluation/eval_report.md`` section 4)."""

import json
from pathlib import Path

import pytest

import main
from evaluation import scoring
from src.extractor import FIXTURE_MAX_QUOTE_CHARS
from src.models import Decision, RawExtraction
from src.workflow import run_pipeline
from tests.m3_helpers import FakeClient

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "assessment-data"
FIXTURES = ROOT / "fixtures" / "mock_llm_responses"
PACKS = [f"supplier-pack-{n:02d}" for n in range(1, 11)]
PR = main.load_purchase_request(DATA / "purchase-request.csv")
DECISION_GT = scoring.load_decision_gt()  # {"supplier-pack-01.pdf": {"pack": ..., "expected_decision": ..., "rule_ids": [...]}, ...}


def _fixture_path(pack: str) -> Path:
    path = FIXTURES / f"{pack}.json"
    if not path.is_file():
        pytest.fail(f"missing offline fixture for {pack}: expected {path}")
    return path


def _expected_decision(pack: str) -> str:
    entry = DECISION_GT.get(f"{pack}.pdf")
    if entry is None:
        pytest.fail(f"no decision ground truth for {pack}")
    return entry["expected_decision"]


def test_all_ten_fixtures_exist():
    missing = [p for p in PACKS if not (FIXTURES / f"{p}.json").is_file()]
    assert not missing, f"missing fixtures: {missing}"


@pytest.mark.parametrize("pack", PACKS)
def test_replayed_fixture_gives_the_ground_truth_decision(pack):
    text = _fixture_path(pack).read_text(encoding="utf-8")
    client = FakeClient([text])
    result = run_pipeline(DATA / f"{pack}.pdf", PR, client=client)
    assert len(client.calls) == 1, "the fixture must be accepted on the first call, with no retry"
    assert result.processing_status == "OK"
    assert result.decision == Decision(_expected_decision(pack))


@pytest.mark.parametrize("pack", PACKS)
def test_replayed_fixture_raises_the_ground_truth_rules(pack):
    """The rules raised (FAIL or UNCERTAIN) are the ones the key lists. Where they differ, the decision test above
    may still pass; that is the 'right decision, wrong intermediate' case the evaluation report discusses."""
    text = _fixture_path(pack).read_text(encoding="utf-8")
    result = run_pipeline(DATA / f"{pack}.pdf", PR, client=FakeClient([text]))
    raised = {r.rule_id for r in result.rule_results if r.status.value in ("FAIL", "UNCERTAIN")}
    assert raised == set(DECISION_GT[f"{pack}.pdf"]["rule_ids"])


@pytest.mark.parametrize("pack", PACKS)
def test_replay_makes_no_live_call(pack, monkeypatch):
    """Whatever happens, the replay must not construct a real Gemini client."""
    from src import extractor

    def forbidden():
        raise AssertionError("the replay tried to create a live Gemini client")

    monkeypatch.setattr(extractor, "_get_client", forbidden)
    text = _fixture_path(pack).read_text(encoding="utf-8")
    run_pipeline(DATA / f"{pack}.pdf", PR, client=FakeClient([text]))


# ---------------------------------------------------------------------------------------------
# Fixture hygiene: a fixture is a model response with short quotes, never a copy of the PDF text
# ---------------------------------------------------------------------------------------------


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _quotes(node):
    if isinstance(node, dict):
        if "source_quote" in node and isinstance(node["source_quote"], str):
            yield node["source_quote"]
        for value in node.values():
            yield from _quotes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _quotes(value)


@pytest.mark.parametrize("pack", PACKS)
def test_fixture_hygiene(pack):
    data = json.loads(_fixture_path(pack).read_text(encoding="utf-8"))
    RawExtraction.model_validate(data)  # still the documented response shape
    for value in _strings(data):
        assert "--- PAGE" not in value, "a page marker means full PDF text was committed"
        assert "<DOCUMENT>" not in value and "</DOCUMENT>" not in value
    for quote in _quotes(data):
        assert len(quote) <= FIXTURE_MAX_QUOTE_CHARS, f"quote of {len(quote)} chars exceeds {FIXTURE_MAX_QUOTE_CHARS}"


def test_fixtures_are_not_git_ignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    active = [line.strip() for line in ignore if line.strip() and not line.startswith("#")]
    assert not any("mock_llm_responses" in line for line in active)
