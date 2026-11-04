"""Response-schema change (after the first four live runs). Offline: no API call, no key.

Background: in 4 live runs, 7 of 40 pack-runs failed because the model returned each line item as ONE flat object of
plain strings instead of one {value, source_quote, page_number} object per field. The responses were valid JSON that
failed RawExtraction validation. The fix asks the API for the right shape (a response schema). These tests pin what
must NOT change: validation, one retry, fail-closed NEEDS_REVIEW, and the system prompt (so a run with the schema
differs from the baseline runs in this one request setting)."""

import hashlib
import json
from pathlib import Path

import pytest

from evaluation import evaluate
from src import extractor
from src.extractor import ExtractionError, RESPONSE_SCHEMA, build_prompt, call_llm
from src.models import RawExtraction, RawField, RawLineItem
from tests.m3_helpers import GOOD_RAW, FakeClient, make_pdf_result, make_pr

PR = make_pr()
PDF = make_pdf_result(["Acme Mills Ltd\nQUOTATION"])
GOOD_JSON = json.dumps(GOOD_RAW)

# The system prompt of all four baseline live runs (metadata.json -> generation_config.system_instruction_sha256).
BASELINE_PROMPT_SHA256 = "c8998d6826a80c2fc52049918c800e5c4f2fcf207b4fa7fc5c907505f548a031"
RUNS = Path(__file__).resolve().parents[1] / "evaluation" / "runs"
FLAT_LINE_ITEM_RAW = RUNS / "live-20261104T124853Z" / "raw" / "supplier-pack-10.json"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(extractor.time, "sleep", lambda _seconds: None)


def _flat_line_item_response() -> str:
    """The failure as the model produced it: valid JSON, line item as one flat object of strings."""
    raw = json.loads(json.dumps(GOOD_RAW))
    raw["line_items"] = [{"item_code": "E", "description": "Carton box, 5-ply", "quantity": "3,000", "unit": "piece",
                          "unit_price_bdt": "94.00", "line_total_bdt": "282,000.00", "vat_included_in_price": "yes",
                          "source_quote": "E Carton box, 5-ply 3,000 piece 94.00 282,000.00"}]
    return json.dumps(raw)


# ---- the schema itself ------------------------------------------------------------------------------------------


def test_schema_follows_the_models_exactly():
    props = RESPONSE_SCHEMA["properties"]
    assert list(props) == list(RawExtraction.model_fields) == RESPONSE_SCHEMA["required"]
    line = props["line_items"]
    assert line["type"] == "ARRAY" and line["items"]["type"] == "OBJECT"
    assert list(line["items"]["properties"]) == list(RawLineItem.model_fields) == line["items"]["required"]
    assert set(RawField.model_fields) == set(extractor._RAW_FIELD_TYPES)  # a new RawField key must get a type here


def test_every_field_at_every_level_is_a_three_key_object_with_nullable_leaves():
    def check(field):
        assert field["type"] == "OBJECT"
        assert list(field["properties"]) == list(RawField.model_fields) == field["required"]
        assert all(leaf["nullable"] is True for leaf in field["properties"].values())
        assert {k: v["type"] for k, v in field["properties"].items()} == {
            "value": "STRING", "source_quote": "STRING", "page_number": "INTEGER"}

    for name, field in RESPONSE_SCHEMA["properties"].items():
        if name == "line_items":
            for line_field in field["items"]["properties"].values():
                check(line_field)  # the shape the model got wrong: line-item fields are objects too
        else:
            check(field)


def test_a_response_that_follows_the_schema_shape_parses():
    """The schema and RawExtraction agree: the saved well-shaped M3 fixture validates."""
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "mock_llm_responses" / "supplier-pack-09.json"
    RawExtraction.model_validate(json.loads(fixture.read_text(encoding="utf-8")))


def test_installed_sdk_accepts_the_schema_and_sends_it(monkeypatch):
    """Through the real SDK to a mock HTTP transport: the schema reaches the request body, nothing is called."""
    httpx = pytest.importorskip("httpx")
    genai = pytest.importorskip("google.genai")
    from google.genai import types

    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}], "role": "model"}}]})

    client = genai.Client(api_key="not-a-real-key", http_options=types.HttpOptions(
        httpx_client=httpx.Client(transport=httpx.MockTransport(handler))))
    extractor._generate(client, "system", "contents")
    config = seen["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json" and config["temperature"] == 0
    sent = config["responseSchema"]
    assert sent["type"] == "OBJECT" and list(sent["properties"]) == list(RawExtraction.model_fields)
    item = sent["properties"]["line_items"]["items"]["properties"]["item_code"]
    assert item["type"] == "OBJECT" and set(item["properties"]) == {"value", "source_quote", "page_number"}


# ---- nothing else about the request changed ------------------------------------------------------------------------


def test_every_call_carries_the_schema_and_the_baseline_prompt():
    client = FakeClient([GOOD_JSON])
    call_llm(PDF, PR, client=client)
    config = client.calls[0]["config"]
    assert config.response_schema == RESPONSE_SCHEMA
    assert config.response_mime_type == "application/json" and config.temperature == 0
    system_instruction = build_prompt(PDF, PR)[0]
    assert hashlib.sha256(system_instruction.encode("utf-8")).hexdigest() == BASELINE_PROMPT_SHA256, (
        "The system prompt differs from the one used in the four baseline runs. If that is deliberate, it is a second "
        "experimental change: update this hash and report the run as a different experiment.")


# ---- the failure is still fail-closed (regression for line_items returned as strings) ----------------------------------


def test_flat_line_items_are_never_repaired_they_are_retried_then_fail():
    client = FakeClient([_flat_line_item_response(), _flat_line_item_response()])
    with pytest.raises(ExtractionError) as info:
        call_llm(PDF, PR, client=client)
    assert info.value.code == "extraction_invalid_json"
    assert "line_items.0.item_code" in info.value.detail and "RawField" in info.value.detail
    assert len(client.calls) == 2  # one call and one retry, as before


def test_flat_line_items_then_a_well_shaped_retry_succeeds():
    client = FakeClient([_flat_line_item_response(), GOOD_JSON])
    assert call_llm(PDF, PR, client=client).supplier_name.value == "Acme Mills Ltd"
    assert len(client.calls) == 2


@pytest.mark.skipif(not FLAT_LINE_ITEM_RAW.exists(), reason="baseline run folder not present")
def test_the_real_recorded_failure_from_the_baseline_run_still_fails_closed():
    """The exact text the model returned for pack 10 in live-20261104T124853Z, both attempts."""
    attempts = json.loads(FLAT_LINE_ITEM_RAW.read_text(encoding="utf-8"))["attempts"]
    client = FakeClient([a["response_text"] for a in attempts])
    with pytest.raises(ExtractionError) as info:
        call_llm(PDF, PR, client=client)
    assert info.value.code == "extraction_invalid_json"


# ---- the evaluator records which schema was sent -----------------------------------------------------------------------


def test_recording_client_records_the_schema_hash_and_replay_detects_a_changed_schema():
    inner = FakeClient([GOOD_JSON])
    recorder = evaluate.RecordingClient(inner)
    call_llm(PDF, PR, client=recorder)
    record = recorder.attempts[0]
    expected = evaluate._schema_sha(RESPONSE_SCHEMA)
    assert record["response_schema_sha256"] == expected and expected is not None

    replay = evaluate.ReplayClient(recorder.attempts)
    call_llm(PDF, PR, client=replay)
    assert replay.drift == []  # same request: no drift

    changed = dict(record, response_schema_sha256="0" * 64)
    replay = evaluate.ReplayClient([changed])
    call_llm(PDF, PR, client=replay)
    assert [d["what"] for d in replay.drift] == ["response_schema_sha256"]


def test_records_made_before_the_schema_existed_are_not_flagged_as_drift():
    recorder = evaluate.RecordingClient(FakeClient([GOOD_JSON]))
    call_llm(PDF, PR, client=recorder)
    record = {k: v for k, v in recorder.attempts[0].items() if k != "response_schema_sha256"}
    replay = evaluate.ReplayClient([record])
    call_llm(PDF, PR, client=replay)
    assert replay.drift == []


def test_schema_hash_is_none_without_a_schema():
    assert evaluate._schema_sha(None) is None
