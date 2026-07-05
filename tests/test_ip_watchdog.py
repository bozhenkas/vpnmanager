import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class WatchdogLogTest(unittest.TestCase):
    def setUp(self):
        self.watchdog = load_module("watchdog_under_test", "ip-watchdog/watchdog.py")
        self.tmp = tempfile.TemporaryDirectory()
        self.watchdog.LOG_DB = Path(self.tmp.name) / "watchdog.sqlite"

    def tearDown(self):
        self.tmp.cleanup()
        self.watchdog.socket.create_connection = self.watchdog._orig_create_connection

    def test_sqlite_log_keeps_last_1000_rows_and_reports_ok_percent(self):
        for i in range(1005):
            verdict = "OK" if i % 2 == 0 else "TIMEOUT"
            self.watchdog.log_probe_result("primary", verdict, 10, "test")
        self.watchdog.log_probe_result("backup", "OK", 20, "test")

        with self.watchdog._log_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM watchdog_log").fetchone()[0]
            primary = conn.execute(
                "SELECT COUNT(*) FROM watchdog_log WHERE target='primary'"
            ).fetchone()[0]

        self.assertEqual(total, 1000)
        self.assertEqual(primary, 999)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.watchdog.print_report()

        text = out.getvalue()
        self.assertIn("primary: total=999", text)
        self.assertIn("backup: total=1 ok=100.0%", text)
        self.assertIn("OK=", text)
        self.assertIn("TIMEOUT=", text)


class YoutubeMediaProbeTest(unittest.TestCase):
    def setUp(self):
        self.watchdog = load_module("watchdog_youtube_under_test", "ip-watchdog/watchdog.py")
        self.watchdog.YOUTUBE_MEDIA_URL = "https://video.example.test/videoplayback?id=1"

    def tearDown(self):
        self.watchdog.socket.create_connection = self.watchdog._orig_create_connection

    def test_youtube_media_ok_on_206_video_body(self):
        sock = FakeSocket([b"HTTP/1.1 206 Partial Content\r\nContent-Type: video/mp4\r\n\r\n1234"])
        with patch.object(self.watchdog.socket, "create_connection", return_value=sock), \
                patch.object(self.watchdog.ssl, "create_default_context", return_value=FakeContext()):
            result = self.watchdog.check_youtube_media()

        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "OK")
        self.assertEqual(result["status"], 206)
        self.assertIn("Range: bytes=0-131071", sock.sent.decode())

    def test_youtube_media_content_mismatch_on_non_video_body(self):
        sock = FakeSocket([b"HTTP/1.1 206 Partial Content\r\nContent-Type: text/html\r\n\r\nblocked"])
        with patch.object(self.watchdog.socket, "create_connection", return_value=sock), \
                patch.object(self.watchdog.ssl, "create_default_context", return_value=FakeContext()):
            result = self.watchdog.check_youtube_media()

        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "CONTENT_MISMATCH")


class RknTspuStatusTest(unittest.TestCase):
    def setUp(self):
        self.rkn = load_module("rkn_checker_under_test", "ip-watchdog/rkn-checker.py")

    def test_blocked_alert_requires_primary_and_domain_bad(self):
        timeout_only = self.rkn.TspuStatus(
            domain_status="OK",
            primary_ip_status="TIMEOUT",
            backup_ip_status="OK",
            media_quality="OK",
            checked_at=datetime.fromtimestamp(1, tz=timezone.utc),
        )
        self.assertFalse(timeout_only.blocked_alert())
        self.assertFalse(self.rkn.should_send_blocked_alert({}, timeout_only))

        blocked = self.rkn.TspuStatus(
            domain_status="TLS_BLOCK",
            primary_ip_status="TLS_BLOCK",
            backup_ip_status="OK",
            media_quality="OK",
            checked_at=datetime.fromtimestamp(2, tz=timezone.utc),
        )
        self.assertTrue(blocked.blocked_alert())
        self.assertTrue(self.rkn.should_send_blocked_alert({}, blocked))
        self.assertEqual(len(self.rkn.tspu_alert_text(blocked).splitlines()), 4)

    def test_tls_block_with_tcp_open_is_not_ok_for_primary(self):
        full_probe_fixture = {
            "verdict": "TLS_BLOCK",
            "ok": False,
            "tcp_ok": True,
            "tls_ok": False,
            "tcp_ms": 12,
            "tls_ms": 0,
            "ms": 12,
            "detail": "",
        }
        ep = {"label": "primary_ip", "url": "https://1.2.3.4/"}
        with patch.object(self.rkn, "full_probe", return_value=full_probe_fixture):
            parsed = self.rkn.probe_endpoint(ep, timeout=5.0)
        self.assertFalse(parsed["ok"])
        self.assertTrue(parsed["tcp_open_tls_blocked"])
        self.assertEqual(parsed["raw_verdict"], "TLS_BLOCK")


class WatchdogFailoverGuardTest(unittest.TestCase):
    def setUp(self):
        self.watchdog = load_module("watchdog_guard_under_test", "ip-watchdog/watchdog.py")
        self.tmp = tempfile.TemporaryDirectory()
        self.watchdog.STATE_FILE = Path(self.tmp.name) / "state"
        self.watchdog.MANUAL_OVERRIDE_FILE = Path(self.tmp.name) / "state.manual"
        self.watchdog.PRIMARY_IP = "45.91.54.152"
        self.watchdog.BACKUP_IP = "45.91.53.93"
        self.watchdog.CF_TOKEN = "token"
        self.watchdog.CF_ZONE_ID = "zone"

    def health(self, *, domain_ok=True, domain_blocked=False, primary_ok=True, primary_blocked=False, backup_ok=True):
        return {
            "ru_domain": {
                "name": "ru.goida.fun",
                "flag": "🇷🇺",
                "ok": domain_ok,
                "blocked": domain_blocked,
                "verdict": "OK" if domain_ok else "tcp_timeout",
                "detail": "",
                "notify": True,
            },
            "ru_primary_ip": {
                "name": "RU primary IP",
                "flag": "🇷🇺",
                "ok": primary_ok,
                "blocked": primary_blocked,
                "verdict": "OK" if primary_ok else "TIMEOUT",
                "detail": "45.91.54.152",
                "notify": True,
            },
            "ru_backup_ip": {
                "name": "RU backup IP",
                "flag": "🇷🇺",
                "ok": backup_ok,
                "blocked": not backup_ok,
                "verdict": "OK" if backup_ok else "TIMEOUT",
                "detail": "45.91.53.93",
                "notify": True,
            },
        }

    def tearDown(self):
        self.tmp.cleanup()
        self.watchdog.socket.create_connection = self.watchdog._orig_create_connection

    def test_stale_state_is_migrated_without_manual_override(self):
        self.watchdog.STATE_FILE.write_text("83.147.255.98")
        with patch.object(self.watchdog, "read_current_ip_safe", return_value="45.91.54.152"), \
                patch.object(self.watchdog, "service_profiles", return_value={}), \
                patch.object(self.watchdog, "build_health_snapshot", return_value=self.health()), \
                patch.object(self.watchdog, "notify_health_changes"), \
                patch.object(self.watchdog, "dns_switch") as dns_switch, \
                patch.object(self.watchdog, "tg_alert"):
            self.watchdog.main()

        self.assertEqual(self.watchdog.STATE_FILE.read_text(), "45.91.54.152")
        self.assertFalse(self.watchdog.MANUAL_OVERRIDE_FILE.exists())
        dns_switch.assert_not_called()

    def test_backup_https_failure_blocks_dns_switch_even_if_hy2_ok(self):
        self.watchdog.STATE_FILE.write_text("45.91.54.152")
        profiles = {"backup_hy2_8443_udp": {"ok": True}, "reserve_reality_443": {"ok": True}}
        with patch.object(self.watchdog, "read_current_ip_safe", return_value="45.91.54.152"), \
                patch.object(self.watchdog, "service_profiles", return_value=profiles), \
                patch.object(self.watchdog, "build_health_snapshot", return_value=self.health(domain_ok=False, domain_blocked=True, primary_ok=False, primary_blocked=True)), \
                patch.object(self.watchdog, "notify_health_changes"), \
                patch.object(self.watchdog, "probe_with_retry", return_value=False), \
                patch.object(self.watchdog, "dns_switch") as dns_switch, \
                patch.object(self.watchdog, "tg_alert") as tg_alert:
            with self.assertRaises(SystemExit):
                self.watchdog.main()

        dns_switch.assert_not_called()
        self.assertTrue(tg_alert.called)

    def test_ladon_parser_blocks_signature_codes(self):
        parsed = self.watchdog.parse_ladon_probe('{"FailureCode":"tls_handshake_timeout","FailureReason":"dpi"}')

        self.assertFalse(parsed["ok"])
        self.assertTrue(parsed["blocked"])
        self.assertEqual(parsed["verdict"], "tls_handshake_timeout")

    def test_ladon_parser_handles_malformed_json(self):
        parsed = self.watchdog.parse_ladon_probe("not-json")

        self.assertFalse(parsed["ok"])
        self.assertFalse(parsed["blocked"])
        self.assertEqual(parsed["verdict"], "LADON_PARSE_ERROR")

    def test_primary_blocked_switches_to_backup_when_backup_is_safe(self):
        self.watchdog.STATE_FILE.write_text("45.91.54.152")
        profiles = {"backup_hy2_8443_udp": {"ok": True}, "reserve_reality_443": {"ok": True}}
        with patch.object(self.watchdog, "read_current_ip_safe", return_value="45.91.54.152"), \
                patch.object(self.watchdog, "service_profiles", return_value=profiles), \
                patch.object(self.watchdog, "build_health_snapshot", return_value=self.health(domain_ok=False, domain_blocked=True, primary_ok=False, primary_blocked=True)), \
                patch.object(self.watchdog, "notify_health_changes"), \
                patch.object(self.watchdog, "probe_with_retry", return_value=True), \
                patch.object(self.watchdog, "dns_switch") as dns_switch, \
                patch.object(self.watchdog, "tg_alert"):
            self.watchdog.main()

        dns_switch.assert_called_once_with("45.91.53.93")
        self.assertEqual(self.watchdog.STATE_FILE.read_text(), "45.91.53.93")

    def test_no_switch_when_primary_bad_without_ladon_signature(self):
        self.watchdog.STATE_FILE.write_text("45.91.54.152")
        profiles = {"backup_hy2_8443_udp": {"ok": True}, "reserve_reality_443": {"ok": True}}
        with patch.object(self.watchdog, "read_current_ip_safe", return_value="45.91.54.152"), \
                patch.object(self.watchdog, "service_profiles", return_value=profiles), \
                patch.object(self.watchdog, "build_health_snapshot", return_value=self.health(domain_ok=False, domain_blocked=False, primary_ok=True, primary_blocked=False)), \
                patch.object(self.watchdog, "notify_health_changes"), \
                patch.object(self.watchdog, "probe_with_retry") as backup_probe, \
                patch.object(self.watchdog, "dns_switch") as dns_switch, \
                patch.object(self.watchdog, "tg_alert"):
            with self.assertRaises(SystemExit):
                self.watchdog.main()

        backup_probe.assert_not_called()
        dns_switch.assert_not_called()

    def test_primary_recovered_switches_back_to_primary(self):
        self.watchdog.STATE_FILE.write_text("45.91.53.93")
        with patch.object(self.watchdog, "read_current_ip_safe", return_value="45.91.53.93"), \
                patch.object(self.watchdog, "service_profiles", return_value={}), \
                patch.object(self.watchdog, "build_health_snapshot", return_value=self.health()), \
                patch.object(self.watchdog, "notify_health_changes"), \
                patch.object(self.watchdog, "dns_switch") as dns_switch, \
                patch.object(self.watchdog, "tg_alert"):
            self.watchdog.main()

        dns_switch.assert_called_once_with("45.91.54.152")
        self.assertEqual(self.watchdog.STATE_FILE.read_text(), "45.91.54.152")

    def test_manual_override_still_blocks_actions(self):
        self.watchdog.STATE_FILE.write_text("45.91.54.152")
        self.watchdog.MANUAL_OVERRIDE_FILE.write_text("1.2.3.4")
        with patch.object(self.watchdog, "read_current_ip_safe", return_value="1.2.3.4"), \
                patch.object(self.watchdog, "service_profiles", return_value={}), \
                patch.object(self.watchdog, "build_health_snapshot", return_value=self.health()), \
                patch.object(self.watchdog, "notify_health_changes"), \
                patch.object(self.watchdog, "primary_status") as primary_status, \
                patch.object(self.watchdog, "dns_switch") as dns_switch:
            self.watchdog.main()

        primary_status.assert_not_called()
        dns_switch.assert_not_called()


class FakeSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = b""

    def settimeout(self, _timeout):
        pass

    def sendall(self, data):
        self.sent += data

    def recv(self, _size):
        if self.chunks:
            return self.chunks.pop(0)
        return b""

    def close(self):
        pass


class FakeContext:
    def wrap_socket(self, sock, server_hostname=None):
        return FakeTls(sock)


class FakeTls:
    def __init__(self, sock):
        self.sock = sock

    def __enter__(self):
        return self.sock

    def __exit__(self, exc_type, exc, tb):
        return False


if __name__ == "__main__":
    unittest.main()
