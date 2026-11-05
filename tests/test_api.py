"""Tests for the Vercel web layer (api/check.py). Offline: the WSGI app is called directly, no server, no network.
Live-path tests only reach checks that return before any Gemini call."""

import io
import json

import pytest

from api import check

DECISIONS = {
    "01": "NEEDS_REVIEW", "02": "APPROVE", "03": "REJECT", "04": "NEEDS_REVIEW", "05": "REJECT",
    "06": "REJECT", "07": "NEEDS_REVIEW", "08": "REJECT", "09": "NEEDS_REVIEW", "10": "REJECT",
}


def call(method="GET", path="/api/check", query="", body=b"", headers=None):
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
    for k, v in (headers or {}).items():
        environ["HTTP_" + k.upper().replace("-", "_")] = v
    out = {}

    def start(status, hdrs):
        out["status"], out["headers"] = int(status.split()[0]), dict(hdrs)

    raw = b"".join(check.app(environ, start))
    return out["status"], out["headers"], raw


def jcall(*a, **k):
    status, _, raw = call(*a, **k)
    return status, json.loads(raw)


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ACCESS_CODE", raising=False)


def test_root_serves_the_page():
    status, headers, raw = call(path="/")
    assert status == 200 and headers["Content-Type"].startswith("text/html") and b"Supplier Quote Checker" in raw


def test_unknown_path_is_404():
    assert jcall(path="/nope")[0] == 404


def test_lists_ten_samples_and_live_flags():
    status, data = jcall()
    assert status == 200 and len(data["samples"]) == 10
    assert data["live"] is False and data["needs_code"] is False


@pytest.mark.parametrize("n", sorted(DECISIONS))
def test_sample_replay_gives_expected_decision(n):
    status, data = jcall(query=f"sample=supplier-pack-{n}")
    assert status == 200 and data["mode"] == "replay"
    assert data["result"]["decision"] == DECISIONS[n]


def test_unknown_sample_is_404():
    assert jcall(query="sample=supplier-pack-99")[0] == 404


def test_summary_matches_answer_key():
    status, data = jcall(query="summary=1")
    assert status == 200 and len(data["rows"]) == 10
    assert all(r["decision_match"] and r["rules_match"] for r in data["rows"])


def test_live_sample_without_key_is_503():
    assert jcall(query="sample=supplier-pack-01&live=1")[0] == 503


def test_post_without_key_is_503():
    assert jcall("POST", query="name=a.pdf", body=b"%PDF-1.4")[0] == 503


def test_post_requires_access_code_when_set(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("ACCESS_CODE", "secret")
    assert jcall("POST", body=b"%PDF-1.4")[0] == 401
    assert jcall("POST", body=b"%PDF-1.4", headers={"X-Access-Code": "wrong"})[0] == 401
    assert jcall(query="sample=supplier-pack-01&live=1")[0] == 401


def test_post_rejects_non_pdf_and_oversize(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert jcall("POST", body=b"hello")[0] == 400
    assert jcall("POST", body=b"")[0] == 413
    assert jcall("POST", body=b"%PDF" + b"0" * check.MAX_PDF_BYTES)[0] == 413


def test_wrong_method_is_405():
    assert jcall("DELETE")[0] == 405


def test_sample_pdf_is_served_inline():
    status, headers, raw = call(query="pdf=supplier-pack-05")
    assert status == 200 and headers["Content-Type"] == "application/pdf" and raw.startswith(b"%PDF")


def test_pdf_only_for_known_samples():
    assert jcall(query="pdf=../README")[0] == 404


def test_summary_has_supplier_total_and_rule_list():
    _, data = jcall(query="summary=1")
    assert all(r["supplier"] and r["total"] for r in data["rows"])
    ids = [r["id"] for r in data["rules"]]
    assert "R-02" in ids and "NR-08" in ids and len(ids) == len(set(ids))
