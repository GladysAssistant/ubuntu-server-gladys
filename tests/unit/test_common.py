from __future__ import annotations

import json
import os
import subprocess
import stat
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[2] / "payload" / "usr" / "lib" / "gladys-installer"
sys.path.insert(0, str(RUNTIME))

import common  # noqa: E402


class StateTests(unittest.TestCase):
    def test_atomic_state_write_persists_valid_schema_without_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"

            common.write_state(path, common.Phase.PULL_GLADYS, 60, "Downloading Gladys Assistant…")

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                {"schema", "phase", "progress", "message", "updated_at"}, set(data)
            )
            self.assertEqual(data["phase"], "PULL_GLADYS")
            self.assertEqual(data["progress"], 60)
            self.assertTrue(data["updated_at"].endswith("Z"))
            self.assertEqual(list(Path(directory).glob(".state.json.*")), [])
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_state_rejects_unknown_phase_and_invalid_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with self.assertRaises(ValueError):
                common.write_state(path, "UNKNOWN", 10, "bad")
            with self.assertRaises(ValueError):
                common.write_state(path, common.Phase.READY, 101, "bad")

    def test_completion_marker_is_atomic_json_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "completed"

            common.write_completed_marker(path, "1.0.0", "sha256:abc", "repo@sha256:def")

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["installer_version"], "1.0.0")
            self.assertEqual(data["image_id"], "sha256:abc")
            self.assertEqual(data["image_digest"], "repo@sha256:def")
            self.assertTrue(common.is_completed(path))


class RetryAndCommandTests(unittest.TestCase):
    def test_backoff_caps_at_sixty_seconds(self) -> None:
        delays = common.backoff_delays()
        self.assertEqual([next(delays) for _ in range(9)], [2, 4, 8, 15, 30, 60, 60, 60, 60])

    def test_failed_command_raises_with_captured_result(self) -> None:
        with self.assertRaises(common.CommandError) as caught:
            common.run_command(
                [sys.executable, "-c", "import sys; print('safe'); sys.exit(7)"]
            )

        self.assertEqual(caught.exception.result.returncode, 7)
        self.assertEqual(caught.exception.result.stdout.strip(), "safe")
        self.assertNotIn(dict(os.environ).__repr__(), str(caught.exception))

    def test_docker_compose_command_is_an_argv_list(self) -> None:
        self.assertEqual(
            common.compose_command("pull", "gladys"),
            ["docker", "compose", "-f", "/opt/gladys/compose.yaml", "pull", "gladys"],
        )

    def test_timed_out_command_is_terminated_promptly(self) -> None:
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            common.run_command(
                [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05
            )
        self.assertLess(time.monotonic() - started, 3)

    @unittest.skipUnless(os.name == "posix", "process-group semantics are Linux-specific")
    def test_timeout_kills_descendant_that_retains_output_pipes(self) -> None:
        child = (
            "import signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(30)"
        )
        parent = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
            "time.sleep(30)"
        )
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            common.run_command([sys.executable, "-c", parent], timeout=0.1)
        self.assertLess(time.monotonic() - started, 5)


class NetworkTests(unittest.TestCase):
    def test_network_check_requires_dns_and_verified_tls(self) -> None:
        calls: list[tuple[str, int]] = []

        def resolver(host: str, port: int, **_kwargs: object) -> list[tuple]:
            calls.append((host, port))
            return [(0, 0, 0, "", ("91.189.91.83", port))]

        def tls_probe(host: str, port: int, timeout: float) -> None:
            self.assertEqual((host, port), ("archive.ubuntu.com", 443))
            self.assertGreater(timeout, 0)

        self.assertTrue(common.internet_available(resolver=resolver, tls_probe=tls_probe))
        self.assertEqual(calls, [("archive.ubuntu.com", 443)])

    def test_network_check_returns_false_when_dns_or_tls_fails(self) -> None:
        def broken(*_args: object, **_kwargs: object) -> list[tuple]:
            raise OSError("offline")

        self.assertFalse(common.internet_available(resolver=broken))

        def resolver(*_args: object, **_kwargs: object) -> list[tuple]:
            return [(0, 0, 0, "", ("127.0.0.1", 443))]

        self.assertFalse(common.internet_available(resolver=resolver, tls_probe=broken))

    def test_ip_parser_returns_sorted_non_loopback_global_ipv4(self) -> None:
        ip_json = json.dumps(
            [
                {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "scope": "host"}]},
                {"ifname": "enp1s0", "addr_info": [{"family": "inet", "local": "192.168.1.42", "scope": "global"}]},
                {"ifname": "docker0", "addr_info": [{"family": "inet", "local": "172.17.0.1", "scope": "global"}]},
            ]
        )

        self.assertEqual(common.parse_global_ipv4(ip_json), ["192.168.1.42"])


class _ReadyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(302)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


class HttpReadinessTests(unittest.TestCase):
    def test_http_status_below_400_is_ready(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ReadyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            self.assertTrue(common.http_ready(url, timeout=1))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_connection_failure_is_not_ready(self) -> None:
        self.assertFalse(common.http_ready("http://127.0.0.1:1/", timeout=0.1))


if __name__ == "__main__":
    unittest.main()
