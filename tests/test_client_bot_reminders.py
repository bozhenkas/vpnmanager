import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


def load_client_bot():
    path = Path(__file__).resolve().parents[1] / "client-bot" / "client-bot.py"
    spec = importlib.util.spec_from_file_location("client_bot_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClientBotReminderTest(unittest.TestCase):
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
        self.sent = []
        self.send_patch = patch.object(self.bot, "send_message", side_effect=self.capture_send)
        self.send_patch.start()
        self.addCleanup(self.send_patch.stop)
        self.bot.init_db()

        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE users (name TEXT PRIMARY KEY, token TEXT, tg_id INTEGER)")
        conn.execute(
            """
            CREATE TABLE user_devices (
                token TEXT,
                device_id TEXT,
                first_seen TEXT,
                last_seen TEXT,
                client_ip TEXT,
                app_name TEXT,
                app_version TEXT,
                platform TEXT,
                platform_version TEXT,
                device_name TEXT
            )
            """
        )
        conn.execute("INSERT INTO users (name, token, tg_id) VALUES ('alice', 'token-alice', 1001)")
        conn.execute(
            "INSERT INTO client_profiles (username, device_limit, paid_until, updated_at) VALUES (?, ?, ?, ?)",
            ("alice", 2, "2026-05-20", "now"),
        )
        conn.execute(
            "INSERT INTO client_tg_links (tg_id, username, tg_username, created_at) VALUES (?, ?, ?, ?)",
            (1001, "alice", "alice_tg", "now"),
        )
        conn.commit()
        conn.close()

    def capture_send(self, tg_id, text, markup=None):
        self.sent.append((tg_id, text, markup))

    def test_auto_reminder_is_sent_day_before_user_paid_until(self):
        with patch.object(self.bot, "today_msk", return_value=date(2026, 5, 19)):
            count = self.bot.send_due_reminders(force=False)

        self.assertEqual(count, 1)
        self.assertEqual(self.sent[0][0], 1001)
        self.assertIn("напоминание об оплате", self.sent[0][1])

        with patch.object(self.bot, "today_msk", return_value=date(2026, 5, 19)):
            count_again = self.bot.send_due_reminders(force=False)

        self.assertEqual(count_again, 0)
        self.assertEqual(len(self.sent), 1)

    def test_disabled_payment_reminder_is_not_sent(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE client_profiles SET payment_reminders_enabled=0 WHERE username='alice'")
        conn.commit()
        conn.close()

        with patch.object(self.bot, "today_msk", return_value=date(2026, 5, 19)):
            count = self.bot.send_due_reminders(force=False)

        self.assertEqual(count, 0)
        self.assertEqual(self.sent, [])

    def test_paid_until_text_uses_russian_genitive_month(self):
        with patch.object(self.bot, "today_msk", return_value=date(2026, 5, 19)):
            profile = self.bot.build_profile("alice")
        self.assertEqual(profile["paidUntilText"], "20 мая")

        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE client_profiles SET paid_until='2027-06-14' WHERE username='alice'")
        conn.commit()
        conn.close()

        with patch.object(self.bot, "today_msk", return_value=date(2026, 5, 19)):
            profile = self.bot.build_profile("alice")
        self.assertEqual(profile["paidUntilText"], "14 июня 2027")


if __name__ == "__main__":
    unittest.main()
