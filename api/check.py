"""Vercel Python app (WSGI). Serves the page at / and the API at /api/check.

GET  /api/check                      -> list of sample packs
GET  /api/check?sample=supplier-pack-03  -> replay saved Gemini extraction (no API key, no network)
POST /api/check?name=quote.pdf       -> body = raw PDF bytes; live Gemini extraction (needs GEMINI_API_KEY)
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import traceback  # noqa: E402

DATA = ROOT / "assessment-data"
FIXTURES = ROOT / "fixtures" / "mock_llm_responses"
MAX_PDF_BYTES = 4 * 1024 * 1024  # Vercel request bodies are capped at ~4.5 MB
_STATE: dict = {}


def _load():
    """Import the pipeline on first use so a failure comes back as readable JSON, not a bare 500."""
    if not _STATE:
        import main as cli
        from src.workflow import build_error_result, run_pipeline

        _STATE.update(pr=cli.load_purchase_request(DATA / cli.PR_FILE), run=run_pipeline, err=build_error_result)
    return _STATE


class _ReplayClient:
    """Stands in for google.genai.Client and returns one saved model response."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.models = self

    def generate_content(self, **_kwargs):
        return SimpleNamespace(text=self._text)


def _samples() -> list[str]:
    return sorted(p.stem for p in FIXTURES.glob("supplier-pack-*.json"))


PAGE = ROOT / "public" / "index.html"


def _json(start_response, status: int, payload: dict):
    body = json.dumps(payload).encode()
    start_response(f"{status} OK", [("Content-Type", "application/json"), ("Cache-Control", "no-store"),
                                    ("Content-Length", str(len(body)))])
    return [body]


def app(environ, start_response):
    try:
        return _app(environ, start_response)
    except Exception:  # noqa: BLE001
        return _json(start_response, 500, {"error": traceback.format_exc()[-1500:]})


def _debug():
    def ls(d):
        return sorted(x.name for x in d.iterdir()) if d.is_dir() else "MISSING"
    return {"python": sys.version, "root": str(ROOT), "root_files": ls(ROOT), "assessment_data": ls(DATA),
            "fixtures": ls(FIXTURES), "public": ls(ROOT / "public")}


def _summary() -> dict:
    """Replay all sample packs and compare with the manual answer key (evaluation/ground_truth.json)."""
    key = {e["pack"]: e for e in json.loads((ROOT / "evaluation" / "ground_truth.json").read_text(encoding="utf-8"))}
    st, rows = _load(), []
    for name in _samples():
        client = _ReplayClient((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        res = st["run"](DATA / f"{name}.pdf", st["pr"], client=client)
        raised = sorted(r.rule_id for r in res.rule_results if r.status.value in ("FAIL", "UNCERTAIN"))
        exp = key.get(f"{name}.pdf", {})
        doc = json.loads(res.model_dump_json()).get("extracted") or {}
        pick = lambda k: (doc.get(k) or {}).get("value")  # noqa: E731
        rows.append({"pack": name, "supplier": pick("supplier_name"), "total": pick("grand_total_bdt"),
                     "decision": res.decision.value, "expected": exp.get("expected_decision"),
                     "decision_match": res.decision.value == exp.get("expected_decision"), "raised": raised,
                     "expected_rules": sorted(exp.get("rule_ids", [])),
                     "rules_match": raised == sorted(exp.get("rule_ids", []))})
    rules = {}
    for r in res.rule_results:  # last pack's results carry every rule id, severity and description
        rules.setdefault(r.rule_id, {"id": r.rule_id, "severity": r.severity.value, "description": r.description})
    return {"rows": rows, "rules": sorted(rules.values(), key=lambda r: (r["severity"] != "REJECT", r["id"]))}


def _live_denied(environ):
    """None if a live Gemini call is allowed, else (status, message). ACCESS_CODE (optional) guards the API key."""
    if not os.getenv("GEMINI_API_KEY"):
        return 503, "Live mode is off: GEMINI_API_KEY is not set on the server."
    code = os.getenv("ACCESS_CODE")
    if code and environ.get("HTTP_X_ACCESS_CODE") != code:
        return 401, "Access code required for live runs."
    return None


def _app(environ, start_response):
    method, path = environ["REQUEST_METHOD"], environ.get("PATH_INFO", "/")
    q = parse_qs(environ.get("QUERY_STRING", ""))
    if method == "GET" and path in ("/", "/index.html"):
        body = PAGE.read_bytes()
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body)))])
        return [body]
    if path != "/api/check":
        return _json(start_response, 404, {"error": "not found"})

    if method == "GET" and q.get("debug"):
        info = _debug()
        try:
            _load()
            info["import"] = "ok"
        except Exception:  # noqa: BLE001
            info["import"] = traceback.format_exc()[-1500:]
        return _json(start_response, 200, info)

    if method == "GET":
        pdf = (q.get("pdf") or [None])[0]
        if pdf:
            if pdf not in _samples():
                return _json(start_response, 404, {"error": "unknown sample"})
            body = (DATA / f"{pdf}.pdf").read_bytes()
            start_response("200 OK", [("Content-Type", "application/pdf"), ("Content-Length", str(len(body))),
                                      ("Content-Disposition", f'inline; filename="{pdf}.pdf"')])
            return [body]
        if q.get("summary"):
            return _json(start_response, 200, _summary())
        name = (q.get("sample") or [None])[0]
        if not name:
            return _json(start_response, 200, {"samples": _samples(), "live": bool(os.getenv("GEMINI_API_KEY")),
                                               "needs_code": bool(os.getenv("ACCESS_CODE"))})
        if name not in _samples():
            return _json(start_response, 404, {"error": "unknown sample"})
        live = bool(q.get("live"))
        if live:
            denied = _live_denied(environ)
            if denied:
                return _json(start_response, denied[0], {"error": denied[1]})
        try:
            client = None if live else _ReplayClient((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
            result = _load()["run"](DATA / f"{name}.pdf", _load()["pr"], client=client)
            return _json(start_response, 200, {"mode": "live" if live else "replay",
                                               "result": json.loads(result.model_dump_json())})
        except Exception as exc:  # noqa: BLE001
            return _json(start_response, 500, {"error": f"{type(exc).__name__}: {exc}"})

    if method == "POST":
        denied = _live_denied(environ)
        if denied:
            return _json(start_response, denied[0], {"error": denied[1]})
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length <= 0 or length > MAX_PDF_BYTES:
            return _json(start_response, 413, {"error": "Send one PDF up to 4 MB."})
        data = environ["wsgi.input"].read(length)
        if not data.startswith(b"%PDF"):
            return _json(start_response, 400, {"error": "That is not a PDF file."})
        name = (q.get("name") or ["upload.pdf"])[0][:80]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "upload.pdf"
            pdf.write_bytes(data)
            try:
                result = _load()["run"](pdf, _load()["pr"])
            except Exception as exc:  # noqa: BLE001
                result = _load()["err"](name, "unexpected_error", type(exc).__name__)
        out = json.loads(result.model_dump_json())
        out["pack_filename"] = name
        return _json(start_response, 200, {"mode": "live", "result": out})

    return _json(start_response, 405, {"error": "method not allowed"})
