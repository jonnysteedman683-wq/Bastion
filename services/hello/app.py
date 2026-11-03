"""hello: the smallest Bastion service. It exists to prove Quartermaster end to end.

    GET /        -> plain text greeting
    GET /health  -> {"status": "ok", "service": "hello", "version": "<git sha>"}

Standard library only. Logs one JSON line per request to stderr, which Cloud Logging
parses into structured entries. Shuts down cleanly on SIGTERM, as Cloud Run sends it
before stopping an instance.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE = "hello"
HEALTH_PATH = "/health"


def make_handler(version: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "bastion-hello"
        sys_version = ""

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == HEALTH_PATH:
                self._send(200, "application/json", {"status": "ok", "service": SERVICE, "version": version})
            elif path == "/":
                self._send(200, "text/plain; charset=utf-8", "Bastion stands.\n")
            else:
                self._send(404, "application/json", {"error": "not found"})

        def _send(self, status: int, content_type: str, payload: object) -> None:
            body = (payload if isinstance(payload, str) else json.dumps(payload)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            entry = {"severity": "INFO", "service": SERVICE, "message": format % args}
            sys.stderr.write(json.dumps(entry) + "\n")

    return Handler


def build_server(port: int, version: str, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(version))
    server.daemon_threads = True
    return server


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    version = os.environ.get("BASTION_VERSION", "dev")
    server = build_server(port, version)

    def _stop(_signum: int, _frame: object) -> None:
        # shutdown() blocks until serve_forever() returns, so it must run on another thread.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    sys.stderr.write(json.dumps({"severity": "INFO", "service": SERVICE, "message": f"listening on {port}"}) + "\n")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
