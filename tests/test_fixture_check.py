"""The fixture quality gate (review fix): verify BEFORE saving; a claimed value that fails is a failure; a field the
model honestly left null is not. Synthetic text and a fake client only; no API, no real pack, no ground truth."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.extractor import normalize_extraction
from src.fixture_check import claim_failures, unclaimed_fields
from src.models import RawExtraction
from src.verifier import verify_evidence
from tests.m3_helpers import GOOD_RAW, NULL, SYNTH_TEXT, FakeClient, f, make_pdf_result, make_pr, raw_with, with_line

PR = make_pr()
PDF = make_pdf_result([SYNTH_TEXT])


def _script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "extract_fixtures.py"
    spec = importlib.util.spec_from_file_location("extract_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _script()


def failures(raw: RawExtraction, text: str = SYNTH_TEXT) -> list[str]:
    doc = verify_evidence(normalize_extraction(raw, PR), make_pdf_result([text]))
    return claim_failures(raw, doc)


# ---- claim_failures ---------------------------------------------------------------------------


def test_a_fully_verified_extraction_has_no_claim_failures():
    assert failures(raw_with()) == []


def test_a_claimed_value_with_a_made_up_quote_is_a_failure():
    out = failures(raw_with(tin=f("101458013391", "Supplier TIN: 999999999999")))
    assert any(x.startswith("tin") for x in out)


def test_a_claimed_value_with_no_quote_is_a_failure():
    assert any(x.startswith("bin") for x in failures(raw_with(bin=f("000170141-0101", None))))


def test_a_wrong_clause_quote_is_a_failure():
    quote = "Goods are supplied as per the buyer's approved sample."
    assert any(x.startswith("delivery_days") for x in failures(raw_with(delivery_period=f("24 days", quote))))


def test_a_claimed_payment_period_that_does_not_convert_is_a_failure():
    out = failures(raw_with(payment_period=f("Net 30", "Balance payable within 45 days after delivery.")))
    assert any(x.startswith("payment.period") for x in out)


def test_a_penalty_component_the_clause_does_not_confirm_is_a_failure():
    out = failures(raw_with(penalty_rate_pct=f("2", "Late delivery penalty: 1% of order value for each full week of delay.")))
    assert any(x.startswith("penalty.rate_pct") for x in out)


def test_a_line_item_field_that_fails_is_a_failure_and_vat_included_is_checked():
    bad = with_line(quantity=f("13,000", "A Cotton twill fabric, 280 gsm 13,000 metre 472.00 5,664,000.00"),
                    vat_included_in_price=f("yes", "Unit price (BDT, VAT included)"))
    out = failures(raw_with(line_items=[bad]))
    assert any(x.startswith("line-1.quantity") for x in out)
    assert any(x.startswith("line-1.vat_included_in_price") for x in out)


def test_fields_the_model_left_null_are_not_failures():
    """Missing TIN/BIN, no penalty clause and an unstated penalty basis are legitimate results (e.g. a pack that
    really lacks them). They must stay UNKNOWN/ABSENT and must not be reported as failures."""
    text = SYNTH_TEXT.replace("Supplier TIN: 101458013391 BIN: 000170141-0101", "Supplier registered locally")
    raw = raw_with(tin=NULL, bin=NULL, penalty_calculation_basis=NULL)
    assert failures(raw, text) == []
    nothing_penalty = text.replace("3. Delay. Late delivery penalty: 1% of order value for each full week of delay.\n", "")
    raw = raw_with(tin=NULL, bin=NULL, penalty_wording=NULL, penalty_rate_pct=NULL, penalty_calculation_basis=NULL,
                   penalty_period=NULL, penalty_full_weeks_only=NULL)
    assert failures(raw, nothing_penalty) == []


def test_unclaimed_fields_are_listed_for_information_only():
    text = SYNTH_TEXT.replace("Supplier TIN: 101458013391 BIN: 000170141-0101", "Supplier registered locally")
    raw = raw_with(tin=NULL, bin=NULL)
    doc = verify_evidence(normalize_extraction(raw, PR), make_pdf_result([text]))
    listed = unclaimed_fields(raw, doc)
    assert "tin (ABSENT)" in listed and "bin (ABSENT)" in listed


# ---- process_pack: verify before saving --------------------------------------------------------


def good_json() -> str:
    return json.dumps(GOOD_RAW)


def bad_json() -> str:
    data = deepcopy(GOOD_RAW)
    data["tin"] = f("101458013391", "Supplier TIN: 999999999999")
    return json.dumps(data)


def run(tmp_path, response: str, force=False, existing: str | None = None):
    target = tmp_path / "supplier-pack-01.json"
    if existing is not None:
        target.write_text(existing, encoding="utf-8")
    ok = SCRIPT.process_pack("supplier-pack-01", PDF, PR, target, force=force, client=FakeClient([response]))
    return ok, target


def test_a_good_extraction_is_saved(tmp_path, capsys):
    ok, target = run(tmp_path, good_json())
    assert ok and target.exists()
    assert RawExtraction.model_validate_json(target.read_text(encoding="utf-8"))


def test_an_extraction_with_a_failed_claim_is_not_saved_and_is_a_failure(tmp_path, capsys):
    ok, target = run(tmp_path, bad_json())
    assert not ok and not target.exists()
    err = capsys.readouterr().err
    assert "NOT SAVED" in err and "tin" in err


def test_a_response_that_breaks_hygiene_is_not_saved(tmp_path):
    data = deepcopy(GOOD_RAW)
    data["supplier_name"] = f("Acme Mills Ltd", "Acme Mills Ltd --- PAGE 1 ---")
    ok, target = run(tmp_path, json.dumps(data))
    assert not ok and not target.exists()


def test_an_existing_bad_fixture_is_caught_on_the_next_run_without_force(tmp_path, capsys):
    ok, target = run(tmp_path, good_json(), existing=bad_json())  # the client is never called: no --force
    assert not ok
    assert "FIXTURE IS BAD" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == bad_json()  # untouched, never silently rewritten


def test_an_existing_good_fixture_is_kept_and_passes(tmp_path):
    ok, target = run(tmp_path, bad_json(), existing=raw_with().model_dump_json())
    assert ok


def test_force_re_extracts_and_replaces_a_bad_fixture(tmp_path):
    ok, target = run(tmp_path, good_json(), force=True, existing=bad_json())
    assert ok and "999999999999" not in target.read_text(encoding="utf-8")


def test_an_extraction_error_is_a_failure_and_writes_nothing(tmp_path):
    from src.extractor import ExtractionError  # noqa: F401

    target = tmp_path / "supplier-pack-01.json"
    ok = SCRIPT.process_pack("supplier-pack-01", PDF, PR, target, client=FakeClient([RuntimeError("down")] * 2))
    assert not ok and not target.exists()


# ---- a refused forced refresh must be explicit (review) ----------------------------------------------------

MESSAGE_PARTS = ("REFRESH FAILED", "previous fixture NOT replaced", "retained unchanged", "STALE")


def assert_explicit_refusal(err: str) -> None:
    for part in MESSAGE_PARTS:
        assert part in err, (part, err)


def test_forced_refresh_that_fails_verification_keeps_the_old_file_and_says_so(tmp_path, capsys):
    old = good_json()
    ok, target = run(tmp_path, bad_json(), force=True, existing=old)
    assert not ok
    assert target.read_bytes() == old.encode("utf-8")  # byte-for-byte unchanged
    assert_explicit_refusal(capsys.readouterr().err)


def test_forced_refresh_that_fails_the_api_keeps_the_old_file_and_says_so(tmp_path, capsys):
    target = tmp_path / "supplier-pack-01.json"
    old = good_json().encode("utf-8")
    target.write_bytes(old)
    ok = SCRIPT.process_pack("supplier-pack-01", PDF, PR, target, force=True, client=FakeClient([RuntimeError("down")] * 2))
    assert not ok and target.read_bytes() == old
    assert_explicit_refusal(capsys.readouterr().err)


def test_forced_refresh_that_breaks_hygiene_keeps_the_old_file_and_says_so(tmp_path, capsys):
    data = deepcopy(GOOD_RAW)
    data["supplier_name"] = f("Acme Mills Ltd", "Acme Mills Ltd --- PAGE 1 ---")
    old = good_json()
    ok, target = run(tmp_path, json.dumps(data), force=True, existing=old)
    assert not ok and target.read_bytes() == old.encode("utf-8")
    assert_explicit_refusal(capsys.readouterr().err)


def test_a_first_time_failure_has_no_stale_notice_because_there_is_no_old_file(tmp_path, capsys):
    ok, target = run(tmp_path, bad_json())
    err = capsys.readouterr().err
    assert not ok and not target.exists() and "STALE" not in err


def test_a_successful_forced_refresh_prints_no_refusal(tmp_path, capsys):
    ok, target = run(tmp_path, good_json(), force=True, existing=bad_json())
    assert ok and "REFRESH FAILED" not in capsys.readouterr().err


def test_main_exits_nonzero_and_keeps_the_old_file_when_a_forced_refresh_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(SCRIPT, "FIXTURE_DIR", tmp_path)
    target = tmp_path / "supplier-pack-01.json"
    old = good_json().encode("utf-8")
    target.write_bytes(old)
    # Real pack-01 PDF; the synthetic response cannot verify against it, so the refresh must be refused.
    code = SCRIPT.main(["--packs", "01", "--force"], client=FakeClient([good_json()]))
    assert code == 1
    assert target.read_bytes() == old
    assert_explicit_refusal(capsys.readouterr().err)
