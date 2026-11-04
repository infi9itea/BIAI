"""M6 tests: the evaluation harness itself. Offline: no API call, no key. Replays the saved fixtures and deliberately
damaged copies of them to prove the scorer can FAIL, keeps live and fixture runs apart, and checks that the
field-level answer key is traceable to the PDFs."""

import json
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

import main
from evaluation import evaluate, scoring
from src.extractor import MAX_ATTEMPTS
from src.parsing import normalize_text, periods_in_text
from src.pdf_reader import extract_text

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "assessment-data"
FIXTURES = ROOT / "fixtures" / "mock_llm_responses"
PR = main.load_purchase_request(DATA / "purchase-request.csv")
DECISION_GT = scoring.load_decision_gt()
FIELD_GT = scoring.load_field_gt()


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(evaluate.extractor, "RETRY_DELAY_SECONDS", 0)


def fixture_json(n: int) -> dict:
    return json.loads((FIXTURES / f"supplier-pack-{n:02d}.json").read_text(encoding="utf-8"))


def run_with(n: int, payload: dict | None = None, client=None):
    """Score pack n with a replayed (optionally damaged) model response."""
    name = f"supplier-pack-{n:02d}.pdf"
    client = client or evaluate.ReplayClient([{"ok": True, "response_text": json.dumps(payload or fixture_json(n))}])
    run, _ = evaluate.run_pack(DATA / name, PR, client)
    return scoring.score_pack(run, DECISION_GT[name], FIELD_GT[name], PR)


def categories(score: dict) -> set[str]:
    return {f["category"] for f in score["failures"]}


def row(score: dict, scope: str, field: str) -> dict:
    return next(r for r in score["fields"] if r["scope"] == scope and r["field"] == field)


# ---------------------------------------------------------------------------------------------
# The answer key
# ---------------------------------------------------------------------------------------------


def test_field_ground_truth_covers_the_same_packs_as_the_decision_ground_truth():
    assert set(FIELD_GT) == set(DECISION_GT) and len(FIELD_GT) == 10


@pytest.mark.parametrize("pack", sorted(FIELD_GT))
def test_every_field_ground_truth_value_is_traceable_to_the_pdf_text(pack):
    """A typo in the key would silently mis-score the model, so every non-null value must appear in the PDF."""
    text = normalize_text(extract_text(str(DATA / pack))["full_text"])
    fields, line = FIELD_GT[pack]["fields"], FIELD_GT[pack]["line_items"][0]

    def money(value):
        d = Decimal(value)
        return {f"{d:,.2f}", f"{d:,.0f}", str(d)}

    for name in ("supplier_name", "tin", "bin"):
        assert fields[name] is None or fields[name] in text, name
    for name in ("subtotal_bdt", "vat_amount_bdt", "grand_total_bdt"):
        assert fields[name] is None or any(m in text for m in money(fields[name])), name
    for name in ("vat_rate_pct", "advance_payment_pct", "penalty_rate_pct"):
        assert fields[name] is None or fields[name] in text, name
    for name in ("item_code", "description", "unit"):
        assert line[name] in text, name
    for name in ("quantity", "unit_price_bdt", "line_total_bdt"):
        assert any(m in text for m in money(line[name])), name
    periods = periods_in_text(text)
    for days in (fields["delivery_days"], fields["validity_days"]):
        assert (days, days) in periods
    assert (fields["payment_min_days"], fields["payment_max_days"]) in periods
    assert fields["penalty_present"] == ("Delay." in text)  # the T&C heading of the late-delivery clause


def test_arithmetic_ground_truth_flags_only_pack_09_as_wrong():
    wrong = {p for p, e in FIELD_GT.items() if any(a["expected_status"] == "FAIL" for a in e["arithmetic"])}
    assert wrong == {"supplier-pack-09.pdf"}
    assert {a["check_name"] for a in FIELD_GT["supplier-pack-10.pdf"]["arithmetic"]} == {"line_recalc"}


# ---------------------------------------------------------------------------------------------
# The scorer can pass and can fail
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("n", range(1, 11))
def test_unmodified_fixture_scores_clean(n):
    score = run_with(n)
    assert score["failures"] == [] and score["decision_agrees"] and score["rules_exact"]
    assert all(r["model_outcome"] in scoring.RIGHT for r in score["fields"])
    assert all(a["status_match"] and a["value_match"] for a in score["arithmetic"])


def test_wrong_value_is_scored_wrong_and_a_wrong_quantity_changes_nothing_it_should_not():
    payload = fixture_json(2)
    for key in ("quantity",):
        payload["line_items"][0][key]["value"] = "21,000"  # pack 02 really says 20,000
    score = run_with(2, payload)
    assert row(score, "line-1", "quantity")["model_outcome"] == scoring.WRONG
    assert "WRONG_VALUE" in categories(score)


def test_missed_value_and_spurious_value_are_distinguished():
    payload = fixture_json(2)
    payload["tin"] = {"value": None, "source_quote": None, "page_number": None}
    payload["vat_price_basis"] = {"value": "VAT on the subtotal", "source_quote": "VAT 15% 108,000.00", "page_number": 1}
    score = run_with(2, payload)
    assert row(score, "doc", "tin")["model_outcome"] == scoring.MISSED
    assert row(score, "doc", "vat_price_basis")["model_outcome"] == scoring.SPURIOUS
    assert {"MISSED_VALUE", "SPURIOUS_VALUE"} <= categories(score)


def test_invented_penalty_clause_on_a_pack_with_none_is_spurious():
    payload = fixture_json(6)  # pack 06 has no late-delivery penalty clause
    payload["penalty_wording"] = {"value": "Late delivery penalty of 1% per week.", "source_quote": "Late delivery penalty of 1% per week.", "page_number": 1}
    score = run_with(6, payload)
    assert row(score, "doc", "penalty_present")["model_outcome"] == scoring.SPURIOUS


def test_fabricated_quote_is_caught_independently_of_the_verifier():
    payload = fixture_json(2)
    payload["delivery_period"]["source_quote"] = "Delivery will be made within 21 days from the date of PO."  # not in the PDF
    score = run_with(2, payload)
    delivery = row(score, "doc", "delivery_days")
    assert delivery["model_outcome"] == scoring.CORRECT  # the VALUE is right ...
    assert delivery["evidence"]["quote_on_claimed_page"] is False  # ... the evidence is not
    assert "QUOTE_NOT_VERBATIM_ON_PAGE" in categories(score)
    assert delivery["pipeline_outcome"] != scoring.CORRECT  # and the real verifier agreed


def test_quote_on_the_wrong_page_is_reported_as_such():
    payload = fixture_json(2)
    payload["delivery_period"]["page_number"] = 2
    assert "QUOTE_WRONG_PAGE" in categories(run_with(2, payload))


def test_quote_that_is_verbatim_but_does_not_support_the_value_is_caught():
    payload = fixture_json(2)
    payload["validity_period"]["source_quote"] = payload["delivery_period"]["source_quote"]  # real text, wrong clause
    score = run_with(2, payload)
    assert row(score, "doc", "validity_days")["evidence"]["supports_expected"] is False
    assert "QUOTE_DOES_NOT_SUPPORT_VALUE" in categories(score)


def test_right_decision_with_a_wrong_intermediate_is_flagged_not_celebrated():
    payload = fixture_json(2)  # APPROVE; the supplier name does not feed any rule
    payload["supplier_name"] = {"value": "Wrong Trading Co", "source_quote": "Wrong Trading Co", "page_number": 1}
    score = run_with(2, payload)
    assert score["decision_agrees"] is True
    assert "RIGHT_DECISION_WRONG_INTERMEDIATE" in categories(score)
    agg = scoring.aggregate([score])
    assert agg["decision"]["agreement"]["n"] == 1
    assert agg["decision"]["agreeing_with_no_other_error"]["n"] == 0
    assert agg["decision"]["right_decision_wrong_intermediate"] == ["supplier-pack-02.pdf"]
    assert agg["extraction_model_view"]["fields_correct"]["n"] < agg["extraction_model_view"]["fields_correct"]["of"]


def test_wrong_extraction_that_flips_the_decision_is_a_mismatch_with_an_upstream_cause():
    payload = fixture_json(2)
    payload["advance_payment_pct"]["value"] = "30"  # R-02 would reject
    payload["advance_payment_pct"]["source_quote"] = "No advance payment required."
    score = run_with(2, payload)
    assert score["decision_agrees"] is False
    assert {"WRONG_VALUE", "DECISION_MISMATCH"} <= categories(score)


def test_arithmetic_error_in_the_pdf_is_expected_to_fail_and_pack_09_does_so():
    score = run_with(9)
    assert [a["expected_status"] for a in score["arithmetic"]] == ["FAIL"] * 4
    assert all(a["status_match"] and a["value_match"] for a in score["arithmetic"])
    assert "NR-06" in score["triggered_rules"]


def test_arithmetic_miss_caused_by_a_wrong_extraction_is_marked_inherited():
    payload = fixture_json(1)
    line_total = payload["line_items"][0]["line_total_bdt"]
    line_total["value"] = "5,000,000.00"
    line_total["source_quote"] = payload["line_items"][0]["quantity"]["source_quote"]  # real row, wrong amount
    score = run_with(1, payload)
    inherited = [a for a in score["arithmetic"] if not (a["status_match"] and a["value_match"])]
    assert inherited and all(a["inherited_from_extraction"] for a in inherited)
    assert "ARITHMETIC_INHERITED_FROM_EXTRACTION" in categories(score)


# ---------------------------------------------------------------------------------------------
# A failed call is a failure, never an agreement
# ---------------------------------------------------------------------------------------------


class _AlwaysFails:
    def __init__(self):
        self.attempts = []
        self.models = self

    def generate_content(self, **kwargs):
        self.attempts.append({"ok": False})
        raise ConnectionError("API unreachable")


def test_api_failure_on_a_needs_review_pack_does_not_count_as_agreement():
    """The pipeline turns a failed call into NEEDS_REVIEW. Pack 01's ground truth is also NEEDS_REVIEW, so a naive
    comparison would call this 'correct'."""
    score = run_with(1, client=_AlwaysFails())
    assert score["processing_status"] == "ERROR: extraction_failed"
    assert score["decision"] == "NEEDS_REVIEW" == score["expected_decision"]
    assert score["decision_agrees"] is False
    assert "API_FAILURE" in categories(score)
    assert all(r["model_outcome"] in (scoring.NOT_EXTRACTED, scoring.CORRECT_NULL) for r in score["fields"])
    assert not any(a["status_match"] for a in score["arithmetic"])
    agg = scoring.aggregate([score])
    assert agg["decision"]["agreement"]["n"] == 0 and agg["decision"]["processing_failures_counted_incorrect"] == 1
    assert agg["extraction_model_view"]["fields_correct"]["n"] < agg["extraction_model_view"]["fields_correct"]["of"]


def test_invalid_json_is_categorised_and_the_failed_calls_are_recorded():
    client = evaluate.ReplayClient([{"ok": True, "response_text": "not json"}] * MAX_ATTEMPTS)
    run, attempts = evaluate.run_pack(DATA / "supplier-pack-01.pdf", PR, client)
    score = scoring.score_pack(run, DECISION_GT["supplier-pack-01.pdf"], FIELD_GT["supplier-pack-01.pdf"], PR)
    assert "INVALID_JSON" in categories(score) and score["decision_agrees"] is False


# ---------------------------------------------------------------------------------------------
# Live / rescore / fixtures are kept apart
# ---------------------------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text):
        self.text = text
        self.model_version = "gemini-fake-001"
        self.response_id = "resp-1"
        self.usage_metadata = type("U", (), {"prompt_token_count": 10, "candidates_token_count": 5,
                                             "thoughts_token_count": 0, "total_token_count": 15})()


class _FakeGemini:
    """Stands in for the real SDK client: serves each pack's saved JSON, keyed by the document text it is sent."""

    def __init__(self, fail_for: str | None = None):
        self.models = self
        self.fail_for = fail_for

    def generate_content(self, *, model, config, contents):
        quote_no = re.search(r"QT-2026-(\d{3})", contents).group(1)
        n = int(quote_no) - 900
        if self.fail_for == f"supplier-pack-{n:02d}.pdf":
            raise TimeoutError("simulated outage")
        return _FakeResponse((FIXTURES / f"supplier-pack-{n:02d}.json").read_text(encoding="utf-8"))


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "RUNS_DIR", tmp_path / "runs")
    return tmp_path / "runs"


def test_live_mode_without_a_key_stops_and_never_falls_back_to_fixtures(runs_dir, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(evaluate, "cmd_fixtures", lambda *a, **k: pytest.fail("live mode must not use fixtures"))
    monkeypatch.setattr(evaluate.extractor, "_get_client", lambda: pytest.fail("no client without a key"))
    assert evaluate.main(["live"]) == 2
    assert "will not fall back to fixtures" in capsys.readouterr().err
    assert not runs_dir.exists()


def test_live_run_records_model_version_prompt_hash_and_raw_responses_and_rescore_reproduces_it(runs_dir, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(evaluate.extractor, "_get_client", lambda: _FakeGemini(fail_for="supplier-pack-04.pdf"))
    assert evaluate.main(["live", "--model", "gemini-test-model"]) == 0
    (run_dir,) = list(runs_dir.iterdir())
    assert run_dir.name.startswith("live-")
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "live" and not meta["partial_run"]
    assert meta["model"] == {"requested": "gemini-test-model", "reported_by_api": ["gemini-fake-001"], "note": None}
    assert meta["generation_config"]["temperature"] == 0 and len(meta["generation_config"]["system_instruction_sha256"]) == 64
    assert len(meta["input_sha256"]) >= 14 and "test-key-not-real" not in json.dumps(meta)
    assert (run_dir / "prompt" / "system_instruction.txt").read_text(encoding="utf-8") == evaluate.extractor.SYSTEM_INSTRUCTION
    raw = json.loads((run_dir / "raw" / "supplier-pack-01.json").read_text(encoding="utf-8"))
    assert raw["attempts"][0]["response_text"] == (FIXTURES / "supplier-pack-01.json").read_text(encoding="utf-8")
    scores = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
    by_pack = {p["pack"]: p for p in scores["packs"]}
    # pack 04's outage is a failure, recorded as such, and the other nine are scored from their real responses
    assert by_pack["supplier-pack-04.pdf"]["processing_status"] == "ERROR: extraction_failed"
    assert by_pack["supplier-pack-04.pdf"]["decision_agrees"] is False  # its ground truth is NEEDS_REVIEW too
    assert scores["aggregate"]["decision"]["agreement"] == {"n": 9, "of": 10, "pct": 90.0}
    assert len(meta["calls"]["supplier-pack-04.pdf"]) == MAX_ATTEMPTS
    assert "FIXTURE" not in (run_dir / "report.md").read_text(encoding="utf-8")

    assert evaluate.main(["rescore", str(run_dir)]) == 0  # offline: the fake client is gone
    rescored = json.loads((run_dir / "rescored" / "scores.json").read_text(encoding="utf-8"))
    assert rescored["aggregate"] == scores["aggregate"] and rescored["packs"] == scores["packs"]


def test_partial_live_run_is_labelled_partial(runs_dir, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(evaluate.extractor, "_get_client", lambda: _FakeGemini())
    assert evaluate.main(["live", "--packs", "02"]) == 0
    (run_dir,) = list(runs_dir.iterdir())
    assert json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))["partial_run"] is True
    assert "PARTIAL RUN" in (run_dir / "report.md").read_text(encoding="utf-8")


def test_fixture_regression_is_labelled_and_saved_apart_from_live_runs(runs_dir):
    assert evaluate.main(["fixtures"]) == 0
    (run_dir,) = list(runs_dir.iterdir())
    assert run_dir.name.startswith("fixtures-")
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "fixture_regression" and meta["model"]["requested"] is None
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "NOT A LIVE LLM MEASURE" in report
    assert not (run_dir / "raw").exists()  # fixtures are not re-saved as if they were live responses


def test_rescore_refuses_a_fixture_run(runs_dir, capsys):
    evaluate.main(["fixtures"])
    (run_dir,) = list(runs_dir.iterdir())
    assert evaluate.main(["rescore", str(run_dir)]) == 2


# ---------------------------------------------------------------------------------------------
# M6 review corrections
# ---------------------------------------------------------------------------------------------


def run_obj(n: int, payload: dict | None = None):
    """Like run_with, but also returns the PackRun and the ground truths so a test can re-score a modified result."""
    name = f"supplier-pack-{n:02d}.pdf"
    client = evaluate.ReplayClient([{"ok": True, "response_text": json.dumps(payload or fixture_json(n))}])
    run, _ = evaluate.run_pack(DATA / name, PR, client)
    return run, name


# --- Review item 1 and 5: RESCORE is its own mode, names the original run, and validates the original's inputs --------


def _live_run(runs_dir, monkeypatch, fail_for=None) -> Path:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(evaluate.extractor, "_get_client", lambda: _FakeGemini(fail_for=fail_for))
    assert evaluate.main(["live", "--model", "gemini-test-model"]) == 0
    (run_dir,) = list(runs_dir.iterdir())
    return run_dir


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_rescore_is_labelled_rescore_not_live_and_identifies_the_original_run(runs_dir, monkeypatch):
    live = _live_run(runs_dir, monkeypatch)
    assert evaluate.main(["rescore", str(live)]) == 0
    out = live / "rescored"
    meta, report = _read(out / "metadata.json"), (out / "report.md").read_text(encoding="utf-8")
    assert meta["mode"] == "rescore" != "live"
    assert "No Gemini API calls were made during this run." in report
    assert "RESCORE" in report and "LIVE RUN" not in report
    assert live.name in report and meta["rescore"]["original_run_id"] == live.name
    assert meta["rescore"]["reproduction"] == "EXACT" and meta["rescore"]["differences"] == []
    # the live run's own report is untouched and still says LIVE
    assert "**LIVE RUN**" in (live / "report.md").read_text(encoding="utf-8")


def test_rescore_with_a_changed_pdf_is_refused_and_writes_nothing(runs_dir, monkeypatch, capsys):
    live = _live_run(runs_dir, monkeypatch)
    meta = _read(live / "metadata.json")
    meta["input_sha256"]["supplier-pack-03.pdf"] = "0" * 64  # as if the PDF had changed since the live run
    (live / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    assert evaluate.main(["rescore", str(live)]) == 2
    err = capsys.readouterr().err
    assert "refusing to rescore" in err and "supplier-pack-03.pdf" in err
    assert not (live / "rescored").exists()


@pytest.mark.parametrize("file", ["ground_truth.json", "field_ground_truth.json", "purchase-request.csv", "company-rules.md"])
def test_rescore_with_changed_ground_truth_or_request_data_is_refused(runs_dir, monkeypatch, capsys, file):
    live = _live_run(runs_dir, monkeypatch)
    meta = _read(live / "metadata.json")
    meta["input_sha256"][file] = "f" * 64
    (live / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    assert evaluate.main(["rescore", str(live)]) == 2
    assert file in capsys.readouterr().err and not (live / "rescored").exists()


def test_rescore_with_a_changed_pipeline_source_is_refused(runs_dir, monkeypatch, capsys):
    live = _live_run(runs_dir, monkeypatch)
    meta = _read(live / "metadata.json")
    meta["source_sha256"]["pipeline"]["src/rule_engine.py"] = "a" * 64
    (live / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    assert evaluate.main(["rescore", str(live)]) == 2
    assert "src/rule_engine.py" in capsys.readouterr().err


def test_rescore_of_a_run_that_did_not_record_its_provenance_is_refused(runs_dir, monkeypatch, capsys):
    live = _live_run(runs_dir, monkeypatch)
    meta = _read(live / "metadata.json")
    del meta["source_sha256"]
    (live / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    assert evaluate.main(["rescore", str(live)]) == 2
    assert "not recorded" in capsys.readouterr().err


def test_rescore_with_a_changed_prompt_is_refused_unless_allowed_and_then_keeps_the_original_prompt(
        runs_dir, monkeypatch, capsys):
    live = _live_run(runs_dir, monkeypatch)
    original = _read(live / "metadata.json")
    original_prompt = (live / "prompt" / "system_instruction.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(evaluate.extractor, "SYSTEM_INSTRUCTION", original_prompt + "\nAn instruction added later.")

    assert evaluate.main(["rescore", str(live)]) == 2
    assert "system prompt sha256" in capsys.readouterr().err and not (live / "rescored").exists()

    assert evaluate.main(["rescore", str(live), "--allow-changed-inputs"]) == 0
    out = live / "rescored"
    meta, report = _read(out / "metadata.json"), (out / "report.md").read_text(encoding="utf-8")
    info = meta["rescore"]
    assert info["reproduction"] == "CHANGED_INPUTS_NEW_EVALUATION"
    assert any(d["item"] == "system prompt sha256" for d in info["differences"])
    assert "NOT EXACT" in report and "NEW EVALUATION" in report and "RESCORE" in report
    # the prompt recorded for this rescore is the one that produced the responses, not today's
    orig_hash = original["generation_config"]["system_instruction_sha256"]
    assert meta["generation_config"]["system_instruction_sha256"] == orig_hash == info["original_system_instruction_sha256"]
    assert meta["prompt"]["system_instruction_sha256"] == original["prompt"]["system_instruction_sha256"]
    assert info["current_system_instruction_sha256"] != orig_hash
    assert (out / "prompt" / "system_instruction.txt").read_text(encoding="utf-8") == original_prompt
    assert info["call_drift"]  # the request now built no longer matches the recorded one, and it says so


def test_rescore_replay_notices_when_the_request_differs_from_the_recorded_one():
    client = evaluate.ReplayClient([{"ok": True, "response_text": "{}", "contents_sha256": evaluate._sha("old"),
                                     "system_instruction_sha256": evaluate._sha("prompt")}])
    client.generate_content(model="m", config={"system_instruction": "prompt"}, contents="new")
    assert [d["what"] for d in client.drift] == ["contents_sha256"]


# --- Review item 2: fabricated extra line items --------------------------------------------------------------------


def test_extra_fabricated_line_item_is_detected_even_though_every_expected_field_is_correct():
    payload = fixture_json(2)
    fake = deepcopy(payload["line_items"][0])
    fake["item_code"]["value"], fake["description"]["value"] = "Z", "Fabricated gold-plated zipper"
    payload["line_items"].append(fake)
    score = run_with(2, payload)
    # every field the key knows about is still right: the old scorer would have reported a perfect extraction
    expected_rows = [r for r in score["fields"] if not r.get("spurious_line")]
    assert all(r["model_outcome"] in scoring.RIGHT for r in expected_rows)
    assert score["line_counts"] == {"expected": 1, "model": 2, "spurious": 1, "missing": 0}
    assert "SPURIOUS_LINE" in categories(score) and "SPURIOUS_VALUE" not in categories(score)
    assert sum(f["category"] == "SPURIOUS_LINE" for f in score["failures"]) == 1
    assert row(score, "line-2", "quantity")["model_outcome"] == scoring.SPURIOUS
    agg = scoring.aggregate([score])
    assert agg["line_items"]["spurious"] == 1 and agg["line_items"]["packs_with_spurious_lines"] == ["supplier-pack-02.pdf"]
    view = agg["extraction_model_view"]
    assert view["fields_correct"]["n"] < view["fields_correct"]["of"] and view["value_precision"]["pct"] < 100
    assert agg["decision"]["agreeing_with_no_other_error"]["n"] == 0  # not a clean pack even if the decision agrees


def test_a_missing_line_item_is_reported_as_not_extracted_not_spurious():
    payload = fixture_json(2)
    payload["line_items"] = []
    score = run_with(2, payload)
    assert score["line_counts"]["spurious"] == 0 and "SPURIOUS_LINE" not in categories(score)
    assert row(score, "line-1", "quantity")["model_outcome"] == scoring.NOT_EXTRACTED


# --- Review item 3: arithmetic inheritance is per check ------------------------------------------------------------


def test_arithmetic_dependencies_are_per_check():
    deps = scoring.arithmetic_dependencies
    assert deps("line_recalc", "line-1", ["line-1"]) == {("line-1", "quantity"), ("line-1", "unit_price_bdt"),
                                                         ("line-1", "line_total_bdt")}
    assert deps("independent_subtotal", None, ["line-1"]) == {("line-1", "quantity"), ("line-1", "unit_price_bdt"),
                                                              ("doc", "subtotal_bdt")}
    assert ("doc", "vat_rate_pct") in deps("vat", None, ["line-1"]) and ("doc", "vat_amount_bdt") in deps("vat", None, ["line-1"])
    assert ("doc", "grand_total_bdt") in deps("grand", None, ["line-1"])
    assert ("doc", "grand_total_bdt") not in deps("vat", None, ["line-1"]) | deps("independent_subtotal", None, ["line-1"])
    assert ("line-1", "line_total_bdt") not in deps("grand", None, ["line-1"])  # printed line totals are not used


def test_a_wrong_grand_total_does_not_mark_unrelated_arithmetic_checks_as_inherited():
    run, name = run_obj(1)
    rows = scoring.score_extraction(run, FIELD_GT[name], PR)
    for r in rows:  # damage only the grand total, as the pipeline saw it
        if (r["scope"], r["field"]) == ("doc", "grand_total_bdt"):
            r["pipeline_outcome"] = scoring.WRONG
    arithmetic = {a["check"]: a for a in scoring.score_arithmetic(run, FIELD_GT[name], rows)}
    assert arithmetic["grand"]["inherited_from_extraction"] is True
    assert arithmetic["grand"]["wrong_inputs"] == ["doc.grand_total_bdt"]
    for unrelated in ("line_recalc:line-1", "independent_subtotal", "vat"):
        assert arithmetic[unrelated]["inherited_from_extraction"] is False, unrelated
        assert arithmetic[unrelated]["wrong_inputs"] == []


def test_a_wrong_vat_amount_taints_the_vat_check_only():
    run, name = run_obj(1)
    rows = scoring.score_extraction(run, FIELD_GT[name], PR)
    for r in rows:
        if (r["scope"], r["field"]) == ("doc", "vat_amount_bdt"):
            r["pipeline_outcome"] = scoring.MISSED
    inherited = {a["check"] for a in scoring.score_arithmetic(run, FIELD_GT[name], rows) if a["inherited_from_extraction"]}
    assert inherited == {"vat"}  # the grand total is built from the rate, not the printed VAT amount


def test_a_wrong_line_quantity_taints_every_check_built_from_it():
    run, name = run_obj(1)
    rows = scoring.score_extraction(run, FIELD_GT[name], PR)
    for r in rows:
        if (r["scope"], r["field"]) == ("line-1", "quantity"):
            r["pipeline_outcome"] = scoring.WRONG
    inherited = {a["check"] for a in scoring.score_arithmetic(run, FIELD_GT[name], rows) if a["inherited_from_extraction"]}
    assert inherited == {"line_recalc:line-1", "independent_subtotal", "vat", "grand"}


def test_end_to_end_wrong_grand_total_is_inherited_by_grand_only_and_not_by_the_line_check():
    payload = fixture_json(1)
    payload["grand_total_bdt"]["value"] = "6,513,601.00"  # not what the quote says
    score = run_with(1, payload)
    flagged = {a["check"] for a in score["arithmetic"] if a["inherited_from_extraction"]}
    assert flagged == {"grand"}
    line = next(a for a in score["arithmetic"] if a["check"] == "line_recalc:line-1")
    assert line["status_match"] and line["value_match"] and not line["inherited_from_extraction"]


# --- Review item 4: the verifier is judged against the model's CLAIMED value ---------------------------------------


def test_quote_supporting_the_expected_value_but_not_the_claimed_one_is_told_apart():
    spec = next(s for s in scoring.LINE_SPECS if s.name == "quantity")
    raw = scoring.RawField(value="21,000", source_quote="B Metal zipper, 18 cm Quantity 20,000 piece", page_number=1)
    pages = {1: "B Metal zipper, 18 cm Quantity 20,000 piece 36.00 720,000.00"}
    ev = scoring.evidence_check(spec, raw, pages, "20000", None, claimed=Decimal("21000"))
    assert ev["quote_on_claimed_page"] is True
    assert ev["supports_expected"] is True  # the quote does show the right number ...
    assert ev["supports_claimed"] is False  # ... and not the number the model returned
    assert ev["claimed_value"] == "21000"


def test_verifier_false_accept_is_measured_against_the_claim_not_the_ground_truth():
    """expected 20,000; model claims 21,000; the quote reads 20,000. The real verifier would refuse, so the verifier's
    verdict is set to VERIFIED here to prove that the scorer would catch a verifier that accepted it."""
    payload = fixture_json(2)
    payload["line_items"][0]["quantity"]["value"] = "21,000"  # pack 02 says 20,000, and so does the quote
    score = run_with(2, payload)
    ev = row(score, "line-1", "quantity")["evidence"]
    assert ev["supports_expected"] is True and ev["supports_claimed"] is False
    assert scoring.aggregate([score])["evidence"]["verifier_false_accepts"] == 0  # the real verifier did refuse

    bad = deepcopy(score)
    row(bad, "line-1", "quantity")["evidence"]["verifier_status"] = "VERIFIED"
    bad["failures"] = scoring.categorize(bad)
    evidence = scoring.aggregate([bad])["evidence"]
    assert evidence["verifier_false_accepts"] == 1  # the old supports_expected test would have said 0
    assert evidence["verified_but_claim_wrong"] == 1
    assert {"VERIFIER_FALSE_ACCEPT", "QUOTE_DOES_NOT_SUPPORT_CLAIMED_VALUE"} <= categories(bad)
    assert any("21000" in f["detail"] for f in bad["failures"] if f["category"] == "VERIFIER_FALSE_ACCEPT")


def test_verifier_false_reject_requires_a_correct_claim_that_its_quote_supports():
    score = run_with(2)
    bad = deepcopy(score)
    row(bad, "doc", "delivery_days")["evidence"]["verifier_status"] = "REJECTED"
    assert scoring.aggregate([bad])["evidence"]["verifier_false_rejects"] == 1
    wrong = run_with(2, {**fixture_json(2), "line_items": [
        {**fixture_json(2)["line_items"][0], "quantity": {**fixture_json(2)["line_items"][0]["quantity"], "value": "21,000"}}]})
    assert scoring.aggregate([wrong])["evidence"]["verifier_false_rejects"] == 0  # refusing a wrong claim is right


def test_unmodified_fixtures_have_no_verifier_errors_and_every_claim_is_supported():
    scores = [run_with(n) for n in range(1, 11)]
    evidence = scoring.aggregate(scores)["evidence"]
    assert evidence["verifier_false_accepts"] == 0 == evidence["verifier_false_rejects"]
    assert evidence["verified_but_claim_wrong"] == 0
    claim = evidence["supports_claimed_value"]
    assert claim["of"] > 0 and claim["n"] == claim["of"]


# --- Review item 6: printed values ---------------------------------------------------------------------------------


def test_printed_value_used_by_the_pipeline_is_compared_with_the_key():
    run, name = run_obj(1)
    clean = scoring.score_arithmetic(run, FIELD_GT[name], scoring.score_extraction(run, FIELD_GT[name], PR))
    assert all(a["printed_match"] and Decimal(a["printed"]) == Decimal(a["expected_printed"]) for a in clean)

    # the status and recalculated value stay right while the printed value the pipeline compared against is wrong
    tampered = [c.model_copy(update={"printed": Decimal("1.00")}) if c.check_name == "vat" else c
                for c in run.result.arithmetic_checks]
    run.result = run.result.model_copy(update={"arithmetic_checks": tampered})
    rows = {a["check"]: a for a in scoring.score_arithmetic(run, FIELD_GT[name], scoring.score_extraction(run, FIELD_GT[name], PR))}
    assert rows["vat"]["status_match"] and rows["vat"]["value_match"] and rows["vat"]["printed_match"] is False
    assert rows["grand"]["printed_match"] is True


def test_wrong_printed_value_is_a_reported_arithmetic_discrepancy():
    run, name = run_obj(1)
    tampered = [c.model_copy(update={"printed": Decimal("1.00")}) if c.check_name == "vat" else c
                for c in run.result.arithmetic_checks]
    run.result = run.result.model_copy(update={"arithmetic_checks": tampered})
    score = scoring.score_pack(run, DECISION_GT[name], FIELD_GT[name], PR)
    assert any(f["level"] == "arithmetic" and "printed 1.00" in f["detail"] for f in score["failures"])
    assert scoring.aggregate([score])["arithmetic"]["printed_agrees"]["n"] == 3  # of 4


def test_pack_09_printed_values_are_the_wrong_ones_in_the_pdf_and_match_the_key():
    score = run_with(9)
    assert all(a["printed_match"] for a in score["arithmetic"])
    grand = next(a for a in score["arithmetic"] if a["check"] == "grand")
    assert Decimal(grand["printed"]) == Decimal(grand["expected_printed"]) != Decimal(grand["expected_calculated"])


# --- Review item 9: rule attribution keeps the line -----------------------------------------------------------------


def test_rule_attribution_keeps_the_line_item_each_rule_fired_on():
    run, name = run_obj(10)  # R-01 and NR-01 are per-item rules; the rest are document-level
    score = scoring.score_pack(run, DECISION_GT[name], FIELD_GT[name], PR)
    assert score["expected_rule_items"] == ["NR-01@line-1", "NR-05", "NR-06", "R-01@line-1", "R-05"]
    assert score["triggered_rule_items"] == score["expected_rule_items"] and score["rule_items_exact"] is True

    moved = [r.model_copy(update={"item_id": "line-2"}) if r.item_id == "line-1" else r for r in run.result.rule_results]
    run.result = run.result.model_copy(update={"rule_results": moved})
    wrong = scoring.score_pack(run, DECISION_GT[name], FIELD_GT[name], PR)
    assert wrong["rules_exact"] is True  # the deduplicated rule IDs cannot see the problem ...
    assert wrong["rule_items_exact"] is False  # ... the item-level comparison can
    assert "R-01@line-2" in wrong["rule_items_extra"] and "R-01@line-1" in wrong["rule_items_missing"]
    assert "RULE_ATTRIBUTED_TO_WRONG_ITEM" in categories(wrong)
    assert scoring.aggregate([wrong])["rules"]["packs_with_exact_rule_items"] == {"n": 0, "of": 1, "pct": 0.0}


def test_expected_rule_items_cannot_pin_a_per_item_rule_when_the_key_has_several_lines():
    assert scoring.expected_rule_items(["R-01", "R-04"], ["line-1", "line-2"]) is None
    assert scoring.expected_rule_items(["R-04"], ["line-1", "line-2"]) == ["R-04"]
    assert scoring.expected_rule_items(["NR-02", "R-03"], ["line-1"]) == ["NR-02@line-1", "R-03"]


@pytest.mark.parametrize("n", range(1, 11))
def test_every_unmodified_fixture_has_exact_rule_items(n):
    assert run_with(n)["rule_items_exact"] is True


# --- Review item 8: one model, named consistently -------------------------------------------------------------------


def test_the_example_configuration_and_the_source_default_name_the_same_model():
    env = dict(line.split("=", 1) for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
               if "=" in line and not line.startswith("#"))
    source_default = re.search(r'GEMINI_MODEL\s*=\s*os\.getenv\("GEMINI_MODEL",\s*"([^"]+)"\)',
                               (ROOT / "src" / "extractor.py").read_text(encoding="utf-8")).group(1)
    assert env["GEMINI_MODEL"] == source_default == "gemini-3.5-flash-lite"


# --- Review item 7: the answer key is checked against the PDFs field by field ---------------------------------------

_CLAUSE = re.compile(r"(?ms)^\d+\.\s+([A-Za-z]+)\.\s(.*?)(?=^\d+\.\s+[A-Za-z]+\.\s|\Z)")


def _clauses(text: str) -> dict[str, str]:
    return {m.group(1): " ".join(m.group(2).split()) for m in _CLAUSE.finditer(text)}


def _vat_row(text: str):
    return re.search(r"(?m)^VAT\s+(\d+(?:\.\d+)?)%\s+([\d,]+\.\d\d)\s*$", text)


@pytest.mark.parametrize("pack", sorted(FIELD_GT))
def test_every_field_of_the_answer_key_is_traceable_to_its_pdf_clause_or_row(pack):
    """Complements the test above, which only checks that values occur somewhere in the text. Here every field,
    including the Boolean and clause-interpretation ones, is derived again from the specific clause or row."""
    text = extract_text(str(DATA / pack))["full_text"]
    norm = normalize_text(text)
    fields, lines = FIELD_GT[pack]["fields"], FIELD_GT[pack]["line_items"]
    clauses = _clauses(text)
    assert len(lines) == 1 and set(clauses) >= {"Payment", "Delivery", "Validity"}, clauses

    # penalty clause: presence, rate, basis, period unit and the full-weeks flag
    penalty = clauses.get("Delay")
    assert fields["penalty_present"] is (penalty is not None)
    if penalty is None:
        assert not re.search(r"(?i)penalt|late delivery", text)  # no clause anywhere, not just no 'Delay' heading
        for name in ("penalty_rate_pct", "penalty_calculation_basis", "penalty_period_unit", "penalty_full_weeks_only"):
            assert fields[name] is None, name
    else:
        assert fields["penalty_rate_pct"] == re.search(r"(\d+(?:\.\d+)?)%", penalty).group(1)
        assert fields["penalty_calculation_basis"] == ("order value" if "order value" in penalty.lower() else None)
        assert fields["penalty_period_unit"] == ("week" if re.search(r"\bweeks?\b", penalty.lower()) else None)
        assert fields["penalty_full_weeks_only"] == (True if "full week" in penalty.lower() else None)

    # payment clause: advance and balance period
    payment = clauses["Payment"]
    advance = "0" if re.search(r"(?i)no advance", payment) else re.search(r"(\d+(?:\.\d+)?)%\s+advance", payment).group(1)
    assert fields["advance_payment_pct"] == advance
    window = re.search(r"(?i)within\s+(\d+)(?:\s+to\s+(\d+))?\s+days", payment)
    assert (fields["payment_min_days"], fields["payment_max_days"]) == (int(window.group(1)), int(window.group(2) or window.group(1)))

    # delivery and validity are read from their own clauses
    for name, clause in (("delivery_days", clauses["Delivery"]), ("validity_days", clauses["Validity"])):
        assert (fields[name], fields[name]) in periods_in_text(clause), name

    # VAT: a printed VAT row (rate and amount) or none at all; 'VAT included' in a price heading
    vat = _vat_row(text)
    assert fields["vat_shown_separately"] is (vat is not None)
    assert fields["vat_rate_pct"] == (vat.group(1) if vat else None)
    assert fields["vat_amount_bdt"] == (str(Decimal(vat.group(2).replace(",", ""))) if vat else None)
    assert lines[0]["vat_included_in_price"] == (True if re.search(r"(?i)VAT included", text) else None)
    assert fields["vat_price_basis"] is None  # derived from the totals layout; never stated in a PDF

    # identifiers: present exactly when the PDF carries the label
    for label, name in (("TIN", "tin"), ("BIN", "bin")):
        if fields[name] is None:
            assert not re.search(rf"\b{label}\b", text), name
        else:
            assert re.search(rf"{label}[^\n]*{re.escape(fields[name])}", text), name
    assert normalize_text(fields["supplier_name"]) in norm


@pytest.mark.parametrize("pack", sorted(FIELD_GT))
def test_arithmetic_block_of_the_answer_key_is_consistent_with_its_own_line_and_total_values(pack):
    entry = FIELD_GT[pack]
    fields, line = entry["fields"], entry["line_items"][0]
    checks = {a["check_name"]: a for a in entry["arithmetic"]}
    quantity, price = Decimal(line["quantity"]), Decimal(line["unit_price_bdt"])
    calc_line = (quantity * price).quantize(Decimal("0.01"))
    assert Decimal(checks["line_recalc"]["expected_calculated"]) == calc_line
    assert checks["line_recalc"]["expected_printed"] == line["line_total_bdt"]
    printed = {"independent_subtotal": "subtotal_bdt", "vat": "vat_amount_bdt", "grand": "grand_total_bdt"}
    for name, field in printed.items():
        if name in checks:
            assert checks[name]["expected_printed"] == fields[field], name
    if "vat" in checks:
        vat = (calc_line * Decimal(fields["vat_rate_pct"]) / 100).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
        assert Decimal(checks["independent_subtotal"]["expected_calculated"]) == calc_line
        assert Decimal(checks["vat"]["expected_calculated"]) == vat
        assert Decimal(checks["grand"]["expected_calculated"]) == calc_line + vat
    for a in entry["arithmetic"]:
        diff = abs(Decimal(a["expected_calculated"]) - Decimal(a["expected_printed"]))
        assert a["expected_status"] == ("PASS" if diff <= Decimal("0.01") else "FAIL"), a["check_name"]
