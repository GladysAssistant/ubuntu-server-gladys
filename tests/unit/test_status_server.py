from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[2] / "payload" / "usr" / "lib" / "gladys-installer"
sys.path.insert(0, str(RUNTIME))

import status_server  # noqa: E402


WEB_ROOT = Path(__file__).resolve().parents[2] / "payload" / "usr" / "share" / "gladys-installer" / "web"


class StatusServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.state = root / "state.json"
        self.web = root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text(
            "<html><style>/*__STYLE__*/</style><script>/*__APP__*/</script></html>",
            encoding="utf-8",
        )
        (self.web / "style.css").write_text("body { color: white; }", encoding="utf-8")
        (self.web / "app.js").write_text("window.gladysSetup = true;", encoding="utf-8")
        self.server = status_server.make_server("127.0.0.1", 0, self.state, self.web)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def get(self, path: str) -> tuple[int, bytes, dict[str, str]]:
        with urllib.request.urlopen(self.base + path, timeout=2) as response:
            return response.status, response.read(), dict(response.headers.items())

    def test_root_embeds_only_known_local_assets(self) -> None:
        status, body, headers = self.get("/")
        text = body.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertIn("body { color: white; }", text)
        self.assertIn("window.gladysSetup = true;", text)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Gladys-Setup"], "active")

    def test_real_page_uses_the_installed_language_and_embeds_no_remote_assets(self) -> None:
        locale_path = Path(self.temporary.name) / "locale"
        locale_path.write_text('LANG="fr_FR.UTF-8"\n', encoding="utf-8")

        body = status_server.render_index(WEB_ROOT, locale_path=locale_path).decode("utf-8")

        self.assertIn('<html lang="fr" dir="ltr">', body)
        self.assertIn("Préparation de votre maison connectée", body)
        self.assertIn("Téléchargement de Gladys Assistant", body)
        self.assertIn("gladysassistant.local", body)
        self.assertNotIn("https://", body)
        self.assertNotIn("http://", body)

    def test_real_page_marks_right_to_left_languages(self) -> None:
        locale_path = Path(self.temporary.name) / "locale"
        locale_path.write_text("LANG=ar_EG.UTF-8\n", encoding="utf-8")

        body = status_server.render_index(WEB_ROOT, locale_path=locale_path).decode("utf-8")

        self.assertIn('<html lang="ar" dir="rtl">', body)

    def test_real_page_embeds_the_supplied_gladys_icon_without_asset_requests(self) -> None:
        body = status_server.render_index(WEB_ROOT).decode("utf-8")

        encoded_icons = re.findall(
            r'data:image/png;base64,([A-Za-z0-9+/=]+)', body
        )
        self.assertGreaterEqual(len(encoded_icons), 2)
        decoded = base64.b64decode(encoded_icons[0], validate=True)
        self.assertEqual(decoded[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(
            hashlib.sha256(decoded).hexdigest(),
            "823fd9c1c5dace8653406c5380c7d268f7e22790661e431ec209278c64f8d4a4",
        )
        self.assertNotIn("__GLADYS_ICON__", body)

    def test_root_csp_allows_only_embedded_images(self) -> None:
        _, _, headers = self.get("/")

        policy = headers["Content-Security-Policy"]
        self.assertIn("img-src data:", policy)
        self.assertNotIn("img-src http", policy)

    def test_real_page_uses_the_current_gladys_glass_theme(self) -> None:
        body = status_server.render_index(WEB_ROOT).decode("utf-8")

        self.assertIn('class="setup-page"', body)
        self.assertIn('class="brand-header"', body)
        self.assertIn('class="brand-logo"', body)
        self.assertIn('class="installer-card"', body)
        self.assertIn('class="setup-navigation"', body)
        self.assertIn("--gl-card-bg: rgba(255, 255, 255, .52)", body)
        self.assertIn("backdrop-filter: blur(28px) saturate(1.6)", body)
        self.assertIn("radial-gradient(76% 78% at 12% -10%", body)
        self.assertIn("rgba(126, 166, 233, .55)", body)
        self.assertIn("rgba(244, 190, 148, .5)", body)
        self.assertIn("rgba(187, 160, 228, .42)", body)
        self.assertIn("rgba(150, 212, 195, .4)", body)
        self.assertNotIn("color-scheme: dark", body)
        self.assertLess(
            body.index('class="setup-navigation"'),
            body.index('class="installer-card"'),
        )
        step_positions = [
            body.index(f'data-step="{step}"')
            for step in ("system", "network", "packages", "docker", "download", "services")
        ]
        self.assertEqual(step_positions, sorted(step_positions))
        for number, position in enumerate(step_positions, start=1):
            step_markup = body[position : position + 260]
            self.assertRegex(
                step_markup,
                rf'class="step-status" aria-hidden="true">{number}</span>',
            )

    def test_mobile_navigation_centers_the_active_stage(self) -> None:
        body = status_server.render_index(WEB_ROOT).decode("utf-8")

        self.assertIn('activeStep.scrollIntoView({', body)
        self.assertIn('inline: "center"', body)
        self.assertIn('block: "nearest"', body)

    def test_mobile_retains_the_approved_logo_size(self) -> None:
        stylesheet = (WEB_ROOT / "style.css").read_text(encoding="utf-8")
        mobile_rules = stylesheet.split("@media (max-width: 30rem) {", 1)[1].split(
            "@media (prefers-reduced-motion: reduce)", 1
        )[0]

        self.assertNotIn(".brand-logo", mobile_rules)

    def test_step_labels_meet_normal_text_contrast(self) -> None:
        stylesheet = (WEB_ROOT / "style.css").read_text(encoding="utf-8")

        def declared_color(name: str) -> str:
            match = re.search(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})", stylesheet)
            self.assertIsNotNone(match)
            return match.group(1)

        def luminance(color: str) -> float:
            components = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [
                value / 12.92
                if value <= 0.04045
                else ((value + 0.055) / 1.055) ** 2.4
                for value in components
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        def composite(foreground: str, background: str, alpha: float) -> str:
            foreground_rgb = [int(foreground[index : index + 2], 16) for index in (1, 3, 5)]
            background_rgb = [int(background[index : index + 2], 16) for index in (1, 3, 5)]
            blended = [
                round(foreground_value * alpha + background_value * (1 - alpha))
                for foreground_value, background_value in zip(foreground_rgb, background_rgb)
            ]
            return "#" + "".join(f"{value:02x}" for value in blended)

        def contrast(first: str, second: str) -> float:
            lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
            return (lighter + 0.05) / (darker + 0.05)

        for variable in ("--gl-muted", "--gl-active"):
            self.assertGreaterEqual(contrast(declared_color(variable), "#ffffff"), 4.5)

        strongest_blue_halo = composite("#7ea6e9", "#eef3fb", 0.55)
        glass_surface = composite("#ffffff", strongest_blue_halo, 0.52)
        self.assertGreaterEqual(
            contrast(declared_color("--gl-accent-text"), glass_surface),
            4.5,
        )

        self.assertIn("color: var(--gl-muted);", stylesheet)
        self.assertIn("background: var(--gl-active);", stylesheet)
        self.assertIn("color: var(--gl-accent-text);", stylesheet)

    def test_fatal_state_hides_progress_that_can_no_longer_be_truthful(self) -> None:
        body = status_server.render_index(WEB_ROOT).decode("utf-8")

        self.assertIn('body[data-phase="FATAL"] .progress-block', body)
        self.assertIn('body[data-phase="FATAL"] .steps', body)

    def test_status_returns_only_sanitized_schema_fields(self) -> None:
        self.state.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "phase": "PULL_GLADYS",
                    "progress": 60,
                    "message": "Downloading…",
                    "updated_at": "2026-08-23T12:00:00Z",
                    "secret": "must-not-leak",
                }
            ),
            encoding="utf-8",
        )

        _, body, _ = self.get("/status.json")
        response = json.loads(body)

        self.assertEqual(response["phase"], "PULL_GLADYS")
        self.assertNotIn("secret", response)

    def test_malformed_state_returns_safe_preflight_state(self) -> None:
        self.state.write_text("not-json", encoding="utf-8")

        _, body, _ = self.get("/status.json")
        response = json.loads(body)

        self.assertEqual(response["phase"], "PREFLIGHT")
        self.assertNotIn("Traceback", response["message"])

    def test_healthz_is_local_static_response(self) -> None:
        status, body, _ = self.get("/healthz")
        self.assertEqual((status, body), (200, b"ok\n"))

    def test_unexpected_and_traversal_paths_are_404(self) -> None:
        for path in ("/etc/passwd", "/../etc/passwd", "/%2e%2e/etc/passwd", "/?file=/etc/passwd"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code, 404)

    def test_incomplete_request_is_bounded_without_spawning_threads(self) -> None:
        self.assertFalse(
            issubclass(status_server.StatusHttpServer, status_server.ThreadingHTTPServer)
        )
        server = status_server.make_server(
            "127.0.0.1", 0, self.state, self.web, request_timeout=0.1
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        slow = socket.create_connection(("127.0.0.1", server.server_port), timeout=1)
        try:
            slow.sendall(b"GET /")
            started = time.monotonic()
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/healthz", timeout=1
            ) as response:
                self.assertEqual(response.status, 200)
            self.assertLess(time.monotonic() - started, 0.8)
        finally:
            slow.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
