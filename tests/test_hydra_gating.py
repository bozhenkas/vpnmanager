import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "bot"))
from subscription import SubscriptionEngine  # noqa: F401


def load_vpn_bot():
    path = Path(__file__).resolve().parents[1] / "bot" / "vpn-bot.py"
    spec = importlib.util.spec_from_file_location("vpn_bot_hydra_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HydraGatingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_vpn_bot()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(Path(self.tmp.name) / "bot.db")
        self.db_patch = patch.object(self.bot, "BOT_DB", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE users (
                name TEXT PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                hydra_enabled INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "INSERT INTO users (name, token, created_at, hydra_enabled) VALUES ('alice', 'tok', 'now', 0)"
        )
        conn.execute(
            "INSERT INTO users (name, token, created_at, hydra_enabled) VALUES ('bob', 'tok2', 'now', 1)"
        )
        conn.execute("CREATE TABLE bot_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.commit()
        conn.close()

    def test_user_hydra_enabled_reads_bot_db(self):
        self.assertFalse(self.bot.user_hydra_enabled("alice"))
        self.assertTrue(self.bot.user_hydra_enabled("bob"))

    def test_subscription_links_skip_hydra_without_flag(self):
        with patch.object(self.bot, "remnawave_user_hydra_squads", return_value=["HYDRA_DE_REMNA"]):
            links = self.bot.remnawave_subscription_links("alice", "uuid-test")
        self.assertNotIn("/hydra-de", links)

    def test_subscription_links_include_hydra_with_flag(self):
        with patch.object(self.bot, "remnawave_user_hydra_squads", return_value=["HYDRA_DE_REMNA"]):
            links = self.bot.remnawave_subscription_links("bob", "uuid-test")
        self.assertIn("hydra-de", links)

    def test_foreign_down_runtime_flag_grants_hydra_to_all_users(self):
        self.bot.foreign_exits_down_set(True)
        with patch.object(self.bot, "remnawave_user_hydra_squads", return_value=["HYDRA_DE_REMNA"]):
            links = self.bot.remnawave_subscription_links("alice", "uuid-test")
        self.assertIn("hydra-de", links)
        self.assertIn("НА РЕМОНТЕ", links)

    def test_subscription_links_include_reserve_and_canonical_remarks(self):
        with patch.object(self.bot, "remnawave_user_hydra_squads", return_value=[]):
            links = self.bot.remnawave_subscription_links("bob", "uuid-test")
        self.assertIn("reserve.goida.fun", links)
        self.assertIn("Оптимальный", links)
        self.assertNotIn("smart-bob", links)

    def test_custom_sub_legacy_poison_detected(self):
        poison = "vless://x@ru.goida.fun#smart-bob 🇸🇨\n#profile-title: goida remnawave ws :)"
        self.assertTrue(self.bot.custom_sub_is_legacy_poisoned(poison, "bob"))
        good = self.bot.remnawave_subscription_links("bob", "uuid-test")
        self.assertFalse(self.bot.custom_sub_is_legacy_poisoned(good, "bob"))


if __name__ == "__main__":
    unittest.main()
