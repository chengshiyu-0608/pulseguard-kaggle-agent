from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agent_engine import RetentionAgent


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
ARTIFACT_DIR = ROOT / "artifacts"
AGENT = RetentionAgent(ARTIFACT_DIR)
OVERVIEW = json.loads((ARTIFACT_DIR / "overview.json").read_text(encoding="utf-8"))
EVALUATION = json.loads((ARTIFACT_DIR / "evaluation.json").read_text(encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    server_version = "PulseGuard/1.0"

    def send_json(self, payload: dict | list, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/summary":
            self.send_json({"overview": OVERVIEW, "evaluation": EVALUATION})
            return
        if parsed.path == "/api/users":
            query = parse_qs(parsed.query)
            tier = query.get("tier", ["全部"])[0]
            keyword = query.get("q", [""])[0]
            limit = min(int(query.get("limit", ["50"])[0]), 200)
            users = AGENT.list_users(tier, keyword)
            self.send_json({"total": len(users), "users": users[:limit]})
            return
        if parsed.path.startswith("/api/diagnose/"):
            user_id = parsed.path.rsplit("/", 1)[-1]
            try:
                self.send_json(AGENT.diagnose(user_id))
            except KeyError as error:
                self.send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/chat":
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            self.send_json(
                AGENT.diagnose(payload.get("user_id", ""), payload.get("question", ""))
            )
        except (KeyError, ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (WEB_DIR / relative).resolve()
        if WEB_DIR.resolve() not in candidate.parents and candidate != WEB_DIR.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.exists() or not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        mime, _ = mimetypes.guess_type(candidate.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PulseGuard product prototype.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8765")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"PulseGuard running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
