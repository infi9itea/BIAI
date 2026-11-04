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

import main as cli  # noqa: E402  (loads the purchase request)
from src.workflow import build_error_result, run_pipeline  # noqa: E402

DATA = ROOT / "assessment-data"
FIXTURES = ROOT / "fixtures" / "mock_llm_responses"
MAX_PDF_BYTES = 4 * 1024 * 1024  # Vercel request bodies are capped at ~4.5 MB
PR = cli.load_purchase_request(DATA / cli.PR_FILE)


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
    method, path = environ["REQUEST_METHOD"], environ.get("PATH_INFO", "/")
    q = parse_qs(environ.get("QUERY_STRING", ""))
    if method == "GET" and path in ("/", "/index.html"):
        body = PAGE.read_bytes()
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body)))])
        return [body]
    if path != "/api/check":
        return _json(start_response, 404, {"error": "not found"})

    if method == "GET":
        name = (q.get("sample") or [None])[0]
        if not name:
            return _json(start_response, 200, {"samples": _samples(), "live": bool(os.getenv("GEMINI_API_KEY"))})
        if name not in _samples():
            return _json(start_response, 404, {"error": "unknown sample"})
        try:
            client = _ReplayClient((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
            result = run_pipeline(DATA / f"{name}.pdf", PR, client=client)
            return _json(start_response, 200, {"mode": "replay", "result": json.loads(result.model_dump_json())})
        except Exception as exc:  # noqa: BLE001
            return _json(start_response, 500, {"error": f"{type(exc).__name__}: {exc}"})

    if method == "POST":
        if not os.getenv("GEMINI_API_KEY"):
            return _json(start_response, 503, {"error": "Live mode is off: GEMINI_API_KEY is not set on the server."})
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
                result = run_pipeline(pdf, PR)
            except Exception as exc:  # noqa: BLE001
                result = build_error_result(name, "unexpected_error", type(exc).__name__)
        out = json.loads(result.model_dump_json())
        out["pack_filename"] = name
        return _json(start_response, 200, {"mode": "live", "result": out})

    return _json(start_response, 405, {"error": "method not allowed"})
