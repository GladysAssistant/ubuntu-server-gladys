#!/usr/bin/env python3
"""Minimal, exact-route first-boot status server."""

from __future__ import annotations

import argparse
import base64
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from common import Phase
import i18n


DEFAULT_STATE = {
    "schema": 1,
    "phase": Phase.PREFLIGHT.value,
    "progress": 2,
    "message": "Preparing your system…",
    "updated_at": "",
}
ALLOWED_STATE_KEYS = frozenset(DEFAULT_STATE)


def safe_state(path: Path) -> dict[str, object]:
    try:
        candidate = json.loads(path.read_text(encoding="utf-8"))
        phase = Phase(candidate["phase"]).value
        progress = candidate["progress"]
        if candidate.get("schema") != 1 or not isinstance(progress, int) or not 0 <= progress <= 100:
            raise ValueError("invalid state schema")
        message = candidate["message"]
        updated_at = candidate["updated_at"]
        if not isinstance(message, str) or not isinstance(updated_at, str):
            raise ValueError("invalid state field type")
        return {
            "schema": 1,
            "phase": phase,
            "progress": progress,
            "message": message[:512],
            "updated_at": updated_at[:64],
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return dict(DEFAULT_STATE)


def _script_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    )


def render_index(
    web_root: Path, *, locale_path: Path = i18n.DEFAULT_LOCALE_PATH
) -> bytes:
    language = i18n.read_installed_language(locale_path)
    translations = i18n.catalog(language)
    index = (web_root / "index.html").read_text(encoding="utf-8")
    style = (web_root / "style.css").read_text(encoding="utf-8")
    script = (web_root / "app.js").read_text(encoding="utf-8")
    bootstrap = {
        "language": language,
        "direction": i18n.direction(language),
        "ui": translations["ui"],
        "phases": translations["phases"],
    }
    replacements = {
        "__LANG__": language.replace("_", "-"),
        "__DIR__": i18n.direction(language),
        "__PREPARING_TITLE__": i18n.text(language, "preparing_title"),
        "__KEEP_CONNECTED__": i18n.text(language, "keep_connected"),
        "__PROGRESS_LABEL__": i18n.text(language, "progress"),
        "__LIVE__": i18n.text(language, "live"),
        "__STEP_SYSTEM__": i18n.text(language, "step_system"),
        "__STEP_NETWORK__": i18n.text(language, "step_network"),
        "__STEP_PACKAGES__": i18n.text(language, "step_packages"),
        "__STEP_DOCKER__": i18n.text(language, "step_docker"),
        "__STEP_DOWNLOAD__": i18n.text(language, "step_download"),
        "__STEP_SERVICES__": i18n.text(language, "step_services"),
    }
    rendered = index.replace("/*__STYLE__*/", style).replace(
        "/*__I18N__*/", f"window.GLADYS_SETUP = Object.freeze({_script_json(bootstrap)});"
    )
    rendered = rendered.replace("/*__APP__*/", script)
    if "__GLADYS_ICON__" in rendered:
        encoded_icon = "".join(
            (web_root / "favicon-96x96.b64").read_text(encoding="ascii").split()
        )
        base64.b64decode(encoded_icon, validate=True)
        rendered = rendered.replace(
            "__GLADYS_ICON__", f"data:image/png;base64,{encoded_icon}"
        )
    for marker, value in replacements.items():
        rendered = rendered.replace(marker, value)
    return rendered.encode("utf-8")


def handler_factory(state_path: Path, web_root: Path) -> type[BaseHTTPRequestHandler]:
    index_body = render_index(web_root)

    class StatusHandler(BaseHTTPRequestHandler):
        server_version = "GladysSetup/1"
        sys_version = ""

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Gladys-Setup", "active")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "img-src data:; connect-src 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self._send(HTTPStatus.OK, index_body, "text/html; charset=utf-8")
                return
            if self.path == "/status.json":
                body = json.dumps(safe_state(state_path), ensure_ascii=False).encode("utf-8") + b"\n"
                self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")
                return
            if self.path == "/healthz":
                self._send(HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8")
                return
            self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")

        def _method_not_allowed(self) -> None:
            self._send(
                HTTPStatus.METHOD_NOT_ALLOWED,
                b"method not allowed\n",
                "text/plain; charset=utf-8",
            )

        do_HEAD = _method_not_allowed
        do_POST = _method_not_allowed
        do_PUT = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_OPTIONS = _method_not_allowed

        def log_message(self, format_string: str, *args: object) -> None:
            print(f"status-server {self.address_string()} {format_string % args}")

    return StatusHandler


class StatusHttpServer(HTTPServer):
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, *args: object, request_timeout: float = 5.0, **kwargs: object) -> None:
        if request_timeout <= 0:
            raise ValueError("request timeout must be positive")
        self.request_timeout = request_timeout
        super().__init__(*args, **kwargs)

    def get_request(self) -> tuple[object, object]:
        connection, address = super().get_request()
        connection.settimeout(self.request_timeout)
        return connection, address


def make_server(
    host: str,
    port: int,
    state_path: Path,
    web_root: Path,
    *,
    request_timeout: float = 5.0,
) -> StatusHttpServer:
    return StatusHttpServer(
        (host, port),
        handler_factory(state_path, web_root),
        request_timeout=request_timeout,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument(
        "--state", type=Path, default=Path("/var/lib/gladys-installer/state.json")
    )
    parser.add_argument(
        "--web-root", type=Path, default=Path("/usr/share/gladys-installer/web")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with make_server(args.host, args.port, args.state, args.web_root) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
