import importlib.util
import unittest
from pathlib import Path


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

    def test_browser_like_ios_user_agent(self):
        meta = self.bot.parse_device_metadata(
            "ip:1.2.3.4",
            "1.2.3.4",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 26_5 like Mac OS X)",
        )
        self.assertEqual(meta["platform"], "iOS")
        self.assertEqual(meta["platform_version"], "26.5")
        self.assertEqual(meta["device_name"], "iPhone")


if __name__ == "__main__":
    unittest.main()
