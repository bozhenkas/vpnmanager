import base64
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from subscription import SubscriptionEngine


def load_bot():
    path = Path(__file__).resolve().parents[1] / "bot" / "vpn-bot.py"
    spec = importlib.util.spec_from_file_location("vpn_bot_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BotDeviceParsingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def test_happ_user_agent(self):
        meta = self.bot.parse_device_metadata(
            "hwid:iphone-15-pro",
            "1.2.3.4",
            "Happ/iOS/2.1.0/iphone-15-pro",
        )
        self.assertEqual(meta["app_name"], "Happ")
        self.assertEqual(meta["app_version"], "2.1.0")
        self.assertEqual(meta["platform"], "iOS")
        self.assertEqual(meta["device_name"], "iphone-15-pro")
        self.assertEqual(meta["client_ip"], "1.2.3.4")

    def test_happ_user_agent_without_model_does_not_use_hwid_as_name(self):
        meta = self.bot.parse_device_metadata(
            "hwid:2604271945694",
            "1.2.3.4",
            "Happ/4.8.3/macos catalyst/2604271945694",
        )
        self.assertEqual(meta["app_name"], "Happ")
        self.assertEqual(meta["app_version"], "4.8.3")
        self.assertEqual(meta["platform"], "macOS")
        self.assertEqual(meta["device_name"], "")

    def test_browser_like_ios_user_agent(self):
        meta = self.bot.parse_device_metadata(
            "ip:1.2.3.4",
            "1.2.3.4",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 26_5 like Mac OS X)",
        )
        self.assertEqual(meta["platform"], "iOS")
        self.assertEqual(meta["platform_version"], "26.5")
        self.assertEqual(meta["device_name"], "iPhone")

    def test_happ_manual_proxy_sites_reads_only_manual_tunnel_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "x-ui.db")
            cfg = {
                "routing": {
                    "rules": [
                        {"ruleTag": "manual-direct", "outboundTag": "direct", "domain": ["domain:direct.example"]},
                        {"ruleTag": "manual-home", "outboundTag": "home-mac-exit", "domain": ["domain:lk.sut.ru"]},
                        {"ruleTag": "manual-foreign", "balancerTag": "balancer-smart", "domain": ["domain:www.happ.su"]},
                        {"outboundTag": "home-mac-exit", "domain": ["domain:static.example"]},
                    ]
                }
            }
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO settings (key, value) VALUES ('xrayTemplateConfig', ?)", (json.dumps(cfg),))
            conn.commit()
            conn.close()

            with patch.object(self.bot, "XUI_DB", db_path):
                sites = self.bot.get_happ_manual_proxy_sites()

        self.assertEqual(sites, ["domain:lk.sut.ru", "domain:www.happ.su"])


class SubscriptionRouteClassificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def setUp(self):
        self.sent = []
        self.remembered_devices = []
        self.base_patches = [
            patch.object(self.bot, "get_user_by_token", return_value={
                "name": "test-sub",
                "token": "token-1",
                "created_at": "now",
                "custom_sub": "",
            }),
            patch.object(self.bot, "build_next_engine", return_value=route_engine()),
            patch.object(self.bot, "get_subscribe_next_description", return_value="goida :)"),
            patch.object(self.bot, "get_next_clients", return_value=(
                {"smart": {"id": "11111111-1111-1111-1111-111111111111", "enable": True}},
                {},
            )),
            patch.object(self.bot, "wl_get_status", return_value=False),
            patch.object(self.bot, "read_wl_links", return_value=[]),
            patch.object(self.bot, "get_known_devices", return_value=set()),
            patch.object(self.bot, "get_next_user_limit", return_value=0),
            patch.object(self.bot, "get_next_traffic", return_value={"up": 0, "down": 0, "total": 0, "expire": 0}),
            patch.object(self.bot, "read_subscription_stub_logo", return_value="<svg></svg>"),
            patch.object(self.bot, "send_text", side_effect=self.capture_send_text),
            patch.object(self.bot, "remember_device", side_effect=self.capture_remember_device),
            patch.object(self.bot, "log_sub_request"),
            patch.object(self.bot, "get_happ_manual_proxy_sites", return_value=[]),
            patch.object(self.bot, "native_sub_get", return_value=False),
            patch.object(self.bot, "get_invite_for_username", return_value=None),
        ]
        for item in self.base_patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.base_patches)])

    def capture_send_text(self, handler, status, body, headers):
        self.sent.append({"status": status, "body": body, "headers": headers})

    def capture_remember_device(self, token, device_id, client_ip, user_agent, **kwargs):
        self.remembered_devices.append((token, device_id, client_ip, user_agent))

    def latest_response(self):
        self.assertTrue(self.sent)
        return self.sent[-1]

    def test_happ_subscribe_gets_json_even_with_html_accept(self):
        handler = FakeHandler({
            "User-Agent": "Happ/iOS/2.1.0/iphone-15-pro",
            "Accept": "text/html,application/xhtml+xml",
            "X-Real-IP": "1.2.3.4",
        })
        self.bot.handle_subscribe_next(handler, "token-1", "plain", public_path="subscribe")

        response = self.latest_response()
        payload = json.loads(response["body"])
        profiles = payload if isinstance(payload, list) else [payload]
        self.assertGreaterEqual(len(profiles), 1)
        self.assertIn("routing", profiles[0])
        self.assertEqual(response["headers"]["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(response["headers"]["routing"], "happ://routing/off")
        self.assertNotIn("<!doctype html>", response["body"].lower())
        self.assertEqual(self.remembered_devices[0][1], "hwid:iphone-15-pro")

    def test_happ_json_public_paths_include_subscribe_and_subscribe_next(self):
        self.assertEqual(
            self.bot.HAPP_JSON_PUBLIC_PATHS,
            frozenset({"subscribe", "subscribe-next"}),
        )

    def test_happ_subscribe_next_gets_json_profiles(self):
        handler = FakeHandler({
            "User-Agent": "Happ/iOS/2.1.0/iphone-15-pro",
            "Accept": "*/*",
            "X-Real-IP": "1.2.3.4",
        })
        self.bot.handle_subscribe_next(handler, "token-1", "plain", public_path="subscribe-next")

        response = self.latest_response()
        payload = json.loads(response["body"])
        profiles = payload if isinstance(payload, list) else [payload]
        self.assertGreaterEqual(len(profiles), 1)
        self.assertIn("routing", profiles[0])
        self.assertEqual(response["headers"]["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(response["headers"]["routing"], "happ://routing/off")

    def test_curl_without_stable_hwid_gets_real_subscription_but_is_not_tracked(self):
        # curl UA имеет нет HWID → guard_ip_limit пропускает без tracking (не блокирует)
        handler = FakeHandler({
            "User-Agent": "curl/8.6.0",
            "Accept": "*/*",
            "X-Real-IP": "1.2.3.4",
        })
        self.bot.handle_subscribe_next(handler, "token-1", "plain", public_path="subscribe")

        response = self.latest_response()
        self.assertEqual(response["headers"]["Content-Type"], "text/plain; charset=utf-8")
        # реальная подписка — содержит vless:// узлы, а не заглушку
        decoded = decode_subscription_body(response["body"])
        self.assertNotIn("клиент не поддерживается", decoded)
        # curl не имеет стабильного HWID → устройство не запоминается
        self.assertEqual(self.remembered_devices, [])

    def test_browser_chrome_gets_html_stub(self):
        handler = FakeHandler({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Sec-Fetch-Dest": "document",
            "X-Real-IP": "1.2.3.4",
        })
        self.bot.handle_subscribe_next(handler, "token-1", "plain", public_path="subscribe")

        response = self.latest_response()
        self.assertEqual(response["headers"]["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("эту ссылку нельзя открыть", response["body"])
        self.assertNotIn("vless://", response["body"])

    def test_clash_route_gets_unsupported_yaml_before_browser_stub(self):
        handler = FakeHandler({
            "User-Agent": "Clash Verge/1.7.7",
            "Accept": "text/html,*/*",
            "X-Real-IP": "1.2.3.4",
        })
        self.bot.handle_subscribe_next(handler, "token-1", "clash", public_path="subscribe")

        response = self.latest_response()
        self.assertEqual(response["headers"]["Content-Type"], "text/yaml; charset=utf-8")
        self.assertIn("📱 Приложение не поддерживается!", response["body"])
        self.assertNotIn("smart-test-sub", response["body"])

    def test_subscribe_old_requires_explicit_per_user_flag(self):
        disabled = FakeHandler({"User-Agent": "v2rayN/6.45", "Accept": "*/*"}, path="/subscribe-old/token-1")
        with patch.object(self.bot, "legacy_sub_get", return_value=False), \
                patch.object(self.bot, "handle_subscribe_next") as handle_subscribe:
            self.bot.SubHandler.do_GET(disabled)

        self.assertEqual(disabled.status, 404)
        handle_subscribe.assert_not_called()

        enabled = FakeHandler({"User-Agent": "v2rayN/6.45", "Accept": "*/*"}, path="/subscribe-old/token-1")
        with patch.object(self.bot, "legacy_sub_get", return_value=True), \
                patch.object(self.bot, "handle_subscribe_next") as handle_subscribe:
            self.bot.SubHandler.do_GET(enabled)

        handle_subscribe.assert_called_once()
        _, args, kwargs = handle_subscribe.mock_calls[0]
        self.assertEqual(args[:3], (enabled, "token-1", "plain"))
        self.assertEqual(kwargs, {"public_path": "subscribe-old", "require_hwid": False})

    def test_normal_subscribe_never_switches_to_legacy_route_by_headers(self):
        handler = FakeHandler({"User-Agent": "v2rayN/6.45", "Accept": "*/*"}, path="/subscribe/token-1")
        with patch.object(self.bot, "legacy_sub_get", return_value=True), \
                patch.object(self.bot, "handle_subscribe_next") as handle_subscribe:
            self.bot.SubHandler.do_GET(handler)

        handle_subscribe.assert_called_once()
        _, args, kwargs = handle_subscribe.mock_calls[0]
        self.assertEqual(args[:3], (handler, "token-1", "plain"))
        self.assertEqual(kwargs, {"public_path": "subscribe"})


class FakeHandler:
    def __init__(self, headers=None, path="/subscribe/token-1", client_ip="1.2.3.4"):
        self.headers = headers or {}
        self.path = path
        self.client_address = (client_ip, 43210)
        self.status = None
        self.response_headers = {}
        self.body = b""
        self.wfile = self

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        pass

    def write(self, body):
        self.body += body


class FakeUpstreamResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"outbounds":[]}'


class RemnawaveRelaxedDeviceLimitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def test_native_subscription_does_not_evict_when_limits_are_relaxed(self):
        handler = FakeHandler({
            "User-Agent": "Happ/4.10.2/ios/third-device",
            "x-hwid": "third-device",
        })
        remna_user = {
            "shortUuid": "short-id",
            "username": "test-user",
            "deviceLimit": 2,
        }
        with patch.object(self.bot, "DEVICE_LIMITS_TEMP_DISABLED", True), \
                patch.object(self.bot, "remnawave_devices_by_username") as devices, \
                patch.object(self.bot, "remnawave_remember_device") as remember, \
                patch.object(self.bot.urllib.request, "urlopen", return_value=FakeUpstreamResponse()):
            self.bot.remnawave_native_subscription(
                handler,
                remna_user,
                "token",
                "plain",
                "happ",
                "1.2.3.4",
            )

        devices.assert_not_called()
        remember.assert_called_once()
        self.assertEqual(remember.call_args.args[:4], (
            "test-user",
            "hwid:third-device",
            "Happ/4.10.2/ios/third-device",
            "1.2.3.4",
        ))
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.body, b'{"outbounds":[]}')


class SubscriptionRequestLogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bot_db = str(Path(self.tmp.name) / "bot.db")
        self.bot_db_patch = patch.object(self.bot, "BOT_DB", self.bot_db)
        self.subs_dir_patch = patch.object(self.bot, "SUBS_DIR", str(Path(self.tmp.name) / "subscriptions"))
        self.bot_db_patch.start()
        self.subs_dir_patch.start()
        self.addCleanup(self.bot_db_patch.stop)
        self.addCleanup(self.subs_dir_patch.stop)
        self.bot.init_bot_db()

    def test_log_sub_request_hashes_sensitive_values_and_keeps_retention(self):
        for idx in range(5002):
            self.bot.log_sub_request(
                token=f"token-{idx}",
                client_type="happ",
                hwid_present=True,
                platform="iOS",
                app_version="2.1.0",
                client_ip=f"10.0.0.{idx}",
                response_type="real",
            )

        conn = sqlite3.connect(self.bot_db)
        rows = conn.execute(
            "SELECT token_hash, ip_hash, client_type, hwid_present, platform, app_version, response_type FROM sub_requests_log ORDER BY rowid"
        ).fetchall()
        conn.close()

        self.assertEqual(len(rows), 5000)
        first = rows[0]
        self.assertEqual(first[0], self.bot.sub_hash("token-2", first_bytes=8))
        self.assertEqual(first[1], self.bot.sub_hash("10.0.0.2"))
        self.assertEqual(first[2:], ("happ", 1, "iOS", "2.1.0", "real"))
        self.assertNotIn("token-2", first)
        self.assertNotIn("10.0.0.2", first)

    def test_get_sub_stats_uses_last_7_days(self):
        now = datetime(2026, 5, 18, tzinfo=timezone.utc)
        rows = [
            (now - timedelta(days=1), "tok-a", "happ", 1, "iOS", "2.1", "1.1.1.1", "real"),
            (now - timedelta(days=2), "tok-a", "browser", 0, "macOS", "5.0", "2.2.2.2", "html"),
            (now - timedelta(days=3), "tok-b", "legacy", 0, "Windows", "6.45", "3.3.3.3", "fake"),
            (now - timedelta(days=8), "tok-c", "clash", 0, "", "", "4.4.4.4", "fake"),
        ]
        conn = sqlite3.connect(self.bot_db)
        conn.executemany(
            """
            INSERT INTO sub_requests_log (
                ts, token_hash, client_type, hwid_present, platform,
                app_version, ip_hash, response_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    ts.isoformat(),
                    self.bot.sub_hash(token, first_bytes=8),
                    client_type,
                    hwid,
                    platform,
                    version,
                    self.bot.sub_hash(ip),
                    response_type,
                )
                for ts, token, client_type, hwid, platform, version, ip, response_type in rows
            ],
        )
        conn.commit()
        conn.close()

        stats = self.bot.get_sub_stats(days=7, now=now)

        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["unique_tokens"], 2)
        self.assertEqual(stats["responses"]["real"], 1)
        self.assertEqual(stats["responses"]["fake"], 1)
        self.assertEqual(stats["responses"]["html"], 1)
        self.assertEqual(stats["clients"], [("browser", 1), ("happ", 1), ("legacy", 1)])

    def test_classify_subscription_client(self):
        classify = self.bot.classify_subscription_client
        self.assertEqual(classify(user_agent="Happ/iOS/2.1/hwid", kind="plain", public_path="subscribe", browser_like=False), "happ")
        self.assertEqual(classify(user_agent="Mozilla/5.0", kind="plain", public_path="subscribe", browser_like=True), "browser")
        self.assertEqual(classify(user_agent="Clash Verge/1.7.7", kind="clash", public_path="subscribe", browser_like=False), "clash")
        self.assertEqual(classify(user_agent="v2rayN/6.45", kind="plain", public_path="subscribe", browser_like=False), "legacy")
        self.assertEqual(classify(user_agent="curl/8.6.0", kind="plain", public_path="subscribe", browser_like=False), "unknown")


def route_engine():
    return SubscriptionEngine(
        domain="ru.goida.fun",
        inbounds={
            "smart": {
                "id": 5,
                "path": "/smart",
                "prefix": "",
                "stream_settings": {
                    "network": "ws",
                    "security": "tls",
                    "wsSettings": {"path": "/smart", "headers": {"Host": "ru.goida.fun"}},
                    "tlsSettings": {"serverName": "ru.goida.fun"},
                },
            },
        },
        server_ips={"45.91.54.152"},
    )


def decode_subscription_body(body):
    return base64.b64decode(body).decode()


if __name__ == "__main__":
    unittest.main()
