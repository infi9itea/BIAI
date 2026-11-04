"""Vercel serverless endpoint.

GET  /api/check                      -> list of sample packs
GET  /api/check?sample=supplier-pack-03  -> replay saved Gemini extraction (no API key, no network)
POST /api/check?name=quote.pdf       -> body = raw PDF bytes; live Gemini extraction (needs GEMINI_API_KEY)
"""
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler
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


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        name = (q.get("sample") or [None])[0]
        if not name:
            return self._send(200, {"samples": _samples(), "live": bool(os.getenv("GEMINI_API_KEY"))})
        if name not in _samples():
            return self._send(404, {"error": "unknown sample"})
        try:
            client = _ReplayClient((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
            result = run_pipeline(DATA / f"{name}.pdf", PR, client=client)
            self._send(200, {"mode": "replay", "result": json.loads(result.model_dump_json())})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self):
        if not os.getenv("GEMINI_API_KEY"):
            return self._send(503, {"error": "Live mode is off: GEMINI_API_KEY is not set on the server."})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_PDF_BYTES:
            return self._send(413, {"error": "Send one PDF up to 4 MB."})
        data = self.rfile.read(length)
        if not data.startswith(b"%PDF"):
            return self._send(400, {"error": "That is not a PDF file."})
        name = (parse_qs(urlparse(self.path).query).get("name") or ["upload.pdf"])[0][:80]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "upload.pdf"
            path.write_bytes(data)
            try:
                result = run_pipeline(path, PR)
            except Exception as exc:  # noqa: BLE001
                result = build_error_result(name, "unexpected_error", f"{type(exc).__name__}")
        out = json.loads(result.model_dump_json())
        out["pack_filename"] = name
        self._send(200, {"mode": "live", "result": out})
