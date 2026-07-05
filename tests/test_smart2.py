import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "bot"))
from subscription import SubscriptionEngine  # noqa: F401
from subscription.xhttp import XHTTP_REALITY_EXTRA_MINIMAL


def load_vpn_bot():
    path = root / "bot" / "vpn-bot.py"
    spec = importlib.util.spec_from_file_location("vpn_bot_smart2_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_client_bot():
    path = root / "client-bot" / "client-bot.py"
    spec = importlib.util.spec_from_file_location("client_bot_smart2_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_routing_spec():
    path = root / "scripts" / "remna_routing_spec.py"
    name = "remna_routing_spec_smart2_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Smart2VpnBotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_vpn_bot()

    def setUp(self):
        self.env_patch = patch.dict(
            "os.environ",
            {
                "SMART2_REALITY_PBK": "test-public-key-base64",
                "SMART2_REALITY_SID": "a1b2c3d4e5f67890",
                "SMART2_REALITY_SNI": "ok.ru",
                "SMART2_PORT": "7443",
            },
            clear=False,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.bot.SMART2_REALITY_PBK = "test-public-key-base64"
        self.bot.SMART2_REALITY_SID = "a1b2c3d4e5f67890"
        self.bot.SMART2_REALITY_SNI = "ok.ru"
        self.bot.SMART2_PORT = 7443

    def test_smart2_link_uses_backup_ip_and_xhttp_reality(self):
        link = self.bot.remnawave_smart2_link("uuid-test")
        self.assertIn("@45.91.53.93:7443/", link)
        self.assertIn("type=xhttp", link)
        self.assertIn("security=reality", link)
        self.assertIn("path=%2Fsmart2", link)
        self.assertIn("mode=stream-one", link)
        self.assertIn("host=ok.ru", link)
        self.assertNotIn("type=grpc", link)
        self.assertNotIn("flow=", link)
        self.assertIn("Оптимальный Нео", urllib.parse.unquote(link.split("#", 1)[1]))

    def test_smart2_link_skipped_without_reality_keys(self):
        self.bot.SMART2_REALITY_PBK = ""
        self.assertEqual(self.bot.remnawave_smart2_link("uuid-test"), "")

    def test_subscription_links_include_smart2_after_smart(self):
        self.bot.SMART2_IN_SUBSCRIPTION = True
        self.bot.SMART_LITE_OBFS_PASSWORD = ""
        with patch.object(self.bot, "user_hydra_enabled", return_value=False):
            with patch.object(self.bot, "remnawave_user_hydra_squads", return_value=[]):
                links = self.bot.remnawave_subscription_links("alice", "uuid-test").splitlines()
        self.assertIn("path=%2Fsmart", links[0])
        self.assertTrue(any("45.91.53.93:7443" in line for line in links))
        self.assertTrue(any("reserve.goida.fun" in line for line in links))

    def test_emergency_mode_keeps_stable_order(self):
        self.bot.SMART2_IN_SUBSCRIPTION = False
        self.bot.SMART_LITE_OBFS_PASSWORD = "test-obfs"
        with patch.object(self.bot, "emergency_ingress_get", return_value=True), \
                patch.object(self.bot, "user_hydra_enabled", return_value=False), \
                patch.object(self.bot, "remnawave_user_hydra_squads", return_value=[]):
            links = self.bot.remnawave_subscription_links("alice", "uuid-test").splitlines()
        self.assertIn("path=%2Fsmart", links[0])
        self.assertIn("hysteria2://", links[1])
        self.assertIn("Оптимальный Лайт", urllib.parse.unquote(links[1]))
        self.assertIn("reserve.goida.fun", links[2])

    def test_custom_sub_server_key_from_smart2_path(self):
        line = (
            "vless://uuid@45.91.53.93:7443/?type=xhttp&security=reality"
            "&path=%2Fsmart2#Оптимальный Нео 🇸🇨"
        )
        self.assertEqual(self.bot.custom_sub_server_key(line), "smart2")

    def test_custom_sub_server_key_from_smart2_remark(self):
        line = "vless://uuid@45.91.53.93:7443/?type=xhttp#Оптимальный Нео 🇸🇨"
        self.assertEqual(self.bot.custom_sub_server_key(line), "smart2")

    def test_filter_custom_sub_respects_smart2_toggle(self):
        line = self.bot.remnawave_smart2_link("uuid-test")
        custom = f"vless://x@ru.goida.fun#smart\n{line}"
        filtered = self.bot.filter_custom_sub_links(custom, {"smart2"})
        self.assertNotIn("45.91.53.93:7443", filtered)
        self.assertIn("ru.goida.fun", filtered)


class Smart2SubscriptionEngineTest(unittest.TestCase):
    def test_json_outbound_parses_xhttp_reality(self):
        link = (
            "vless://11111111-1111-1111-1111-111111111111@45.91.53.93:7443/"
            "?type=xhttp&security=reality&encryption=none&pbk=test&sid=abcd"
            "&sni=ok.ru&host=ok.ru&path=%2Fsmart2&mode=stream-one#Оптимальный Нео 🇸🇨"
        )
        engine = SubscriptionEngine(domain="ru.goida.fun", inbounds={}, server_ips=set())
        outbound = engine._json_outbound(link)
        self.assertIsNotNone(outbound)
        stream = outbound["streamSettings"]
        self.assertEqual(stream["network"], "xhttp")
        self.assertEqual(stream["security"], "reality")
        xh = stream["xhttpSettings"]
        self.assertEqual(xh["path"], "/smart2")
        self.assertEqual(xh["mode"], "stream-one")
        self.assertEqual(xh["host"], "ok.ru")
        self.assertEqual(xh["extra"], XHTTP_REALITY_EXTRA_MINIMAL)
        self.assertNotIn("xmux", xh["extra"])
        self.assertNotIn("flow", outbound["settings"]["vnext"][0]["users"][0])

    def test_json_profile_from_custom_sub_smart2_link(self):
        link = (
            "vless://11111111-1111-1111-1111-111111111111@45.91.53.93:7443/"
            "?type=xhttp&security=reality&pbk=test&sid=abcd&sni=ok.ru&host=ok.ru"
            "&path=%2Fsmart2&mode=stream-one#Оптимальный Нео 🇸🇨"
        )
        engine = SubscriptionEngine(domain="ru.goida.fun", inbounds={}, server_ips=set())
        profiles = engine.json_profiles(link)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["remarks"], "Оптимальный Нео 🇸🇨")
        self.assertEqual(profiles[0]["outbounds"][0]["streamSettings"]["network"], "xhttp")


class Smart2RoutingSpecTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec_mod = load_routing_spec()

    def test_goida_smart2_in_smart_balancer_catch_all(self):
        rules = self.spec_mod.build_rules({"home": [], "direct": [], "foreign": []})
        tagged = {rule.get("ruleTag"): rule for rule in rules if rule.get("ruleTag")}
        smart_catch = tagged["foreign-smart-catch-all"]
        self.assertIn("GOIDA_SMART2", smart_catch["inboundTag"])
        zapret = tagged["direct-zapret-services-domain"]
        self.assertIn("GOIDA_SMART2", zapret["inboundTag"])


class Smart2ClientBotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_client_bot()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(Path(self.tmp.name) / "bot.db")
        self.db_patch = patch.object(self.bot, "BOT_DB", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.bot.init_db()
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE users (name TEXT PRIMARY KEY, token TEXT, tg_id INTEGER)")
        conn.commit()
        conn.close()

    def test_catalog_includes_smart2_for_smart_ru_squad(self):
        with patch.object(
            self.bot,
            "remnawave_server_catalog",
            return_value=[
                self.bot.REMNAWAVE_SERVER_CATALOG["smart"],
                self.bot.REMNAWAVE_SERVER_CATALOG["smart2"],
                self.bot.REMNAWAVE_SERVER_CATALOG["reserve"],
                self.bot.REMNAWAVE_SERVER_CATALOG["fi"],
                self.bot.REMNAWAVE_SERVER_CATALOG["se"],
                self.bot.REMNAWAVE_SERVER_CATALOG["zapret"],
            ],
        ):
            conn = sqlite3.connect(self.db_path)
            catalog = self.bot.server_catalog(conn, "alice")
            conn.close()
        keys = [item["key"] for item in catalog]
        self.assertIn("smart2", keys)
        smart2 = next(item for item in catalog if item["key"] == "smart2")
        self.assertEqual(smart2["label"], "Оптимальный 2 🇸🇨")


if __name__ == "__main__":
    unittest.main()
