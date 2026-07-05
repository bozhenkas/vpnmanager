import importlib.util
import json
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "bot"))

from subscription import SubscriptionEngine, happ_routing_neo_line
from subscription.ru_routing import HAPP_ROUTING_NEO_PROFILE


def load_vpn_bot():
    path = root / "bot" / "vpn-bot.py"
    spec = importlib.util.spec_from_file_location("vpn_bot_smart_lite_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SmartLiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_vpn_bot()

    def setUp(self):
        self.env_patch = patch.dict(
            "os.environ",
            {
                "SMART_LITE_OBFS_PASSWORD": "test-obfs-secret",
                "SMART_LITE_HOST": "45.91.53.93",
                "SMART_LITE_PORT": "8443",
                "SMART_LITE_SNI": "ru.goida.fun",
                "SMART2_IN_SUBSCRIPTION": "0",
                "SMART2_REALITY_PBK": "test-public-key",
                "SMART2_REALITY_SID": "a1b2c3d4",
            },
            clear=False,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.bot.SMART_LITE_OBFS_PASSWORD = "test-obfs-secret"
        self.bot.SMART_LITE_HOST = "45.91.53.93"
        self.bot.SMART_LITE_PORT = 8443
        self.bot.SMART_LITE_SNI = "ru.goida.fun"
        self.bot.SMART2_IN_SUBSCRIPTION = False

    def test_smart_lite_link_hysteria2_salamander(self):
        link = self.bot.remnawave_smart_lite_link("uuid-test")
        self.assertTrue(link.startswith("hysteria2://uuid-test@45.91.53.93:8443/"))
        self.assertIn("obfs=salamander", link)
        self.assertIn("obfs-password=test-obfs-secret", link)
        self.assertIn("sni=ru.goida.fun", link)
        self.assertIn("Оптимальный Лайт", urllib.parse.unquote(link.split("#", 1)[1]))

    def test_subscription_excludes_neo_and_lite_while_broken(self):
        with patch.object(self.bot, "user_hydra_enabled", return_value=False):
            with patch.object(self.bot, "remnawave_user_hydra_squads", return_value=[]):
                links = self.bot.remnawave_subscription_links("alice", "uuid-test").splitlines()
        self.assertIn("path=%2Fsmart", links[0])
        self.assertFalse(any("Оптимальный Лайт" in urllib.parse.unquote(line) for line in links))
        self.assertFalse(any("hysteria2://" in line for line in links))
        self.assertFalse(any("45.91.53.93:7443" in line for line in links))

    def test_neo_routing_profile_name(self):
        self.assertEqual(HAPP_ROUTING_NEO_PROFILE["Name"], "goida neo")
        self.assertTrue(happ_routing_neo_line().startswith("happ://routing/onadd/"))

    def test_custom_sub_server_key_smart_lite(self):
        line = self.bot.remnawave_smart_lite_link("uuid-test")
        self.assertEqual(self.bot.custom_sub_server_key(line), "smart-lite")

    def test_json_profile_includes_hysteria2_lite(self):
        link = self.bot.remnawave_smart_lite_link("uuid-test")
        plain = f"vless://uuid@ru.goida.fun:443/?type=ws&security=tls&path=%2Fsmart#smart\n{link}"
        body = SubscriptionEngine(domain="ru.goida.fun", inbounds={}).generate_json_profile(plain)
        data = json.loads(body)
        profiles = data if isinstance(data, list) else [data]
        hy = [p for p in profiles if p.get("remarks", "").startswith("Оптимальный Лайт")]
        self.assertEqual(len(hy), 1)
        outbound = hy[0]["outbounds"][0]
        self.assertEqual(outbound["protocol"], "hysteria")
        self.assertEqual(outbound["settings"]["address"], "45.91.53.93")
        self.assertEqual(outbound["streamSettings"]["finalmask"]["udp"][0]["type"], "salamander")

    def test_native_json_filter_removes_broken_lite(self):
        body = json.dumps([
            {"remarks": "Оптимальный 🇸🇨"},
            {"remarks": "Оптимальный Лайт 🇸🇨"},
            {"remarks": "Финляндия 🇫🇮"},
        ], ensure_ascii=False)

        filtered = self.bot.strip_broken_smart_lite_from_subscription(body)
        remarks = [item["remarks"] for item in json.loads(filtered)]

        self.assertEqual(remarks, ["Оптимальный 🇸🇨", "Финляндия 🇫🇮"])

    def test_native_json_filter_keeps_lite_when_allowed(self):
        body = json.dumps([
            {"remarks": "Оптимальный 🇸🇨"},
            {"remarks": "Оптимальный Лайт 🇸🇨"},
            {"remarks": "Финляндия 🇫🇮"},
        ], ensure_ascii=False)

        kept = self.bot.strip_broken_smart_lite_from_subscription(body, allow_smart_lite=True)
        remarks = [item["remarks"] for item in json.loads(kept)]

        self.assertEqual(remarks, ["Оптимальный 🇸🇨", "Оптимальный Лайт 🇸🇨", "Финляндия 🇫🇮"])


if __name__ == "__main__":
    unittest.main()
