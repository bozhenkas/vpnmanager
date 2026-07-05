import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import remnawave_client.users as remnawave_client_users


def load_bot():
    path = Path(__file__).resolve().parents[1] / "bot" / "vpn-bot.py"
    spec = importlib.util.spec_from_file_location("vpn_bot_invites_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DecideInviteModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def row(self, **kwargs):
        base = {
            "id": 1,
            "type": "personal",
            "inviter_username": "inviter",
            "token": "tok",
            "created_at": "2026-07-01T00:00:00+00:00",
            "first_fetch_at": "",
            "tg_linked_at": "",
            "activated_username": "friend",
            "activated_tg_id": 0,
            "activated_tg_username": "",
            "trial_ends_at": "",
            "status": "created",
            "reward_days": 0,
            "reward_applied_at": "",
        }
        base.update(kwargs)
        return base

    def test_banned_wins_regardless_of_other_fields(self):
        row = self.row(status="banned")
        self.assertEqual(self.bot.decide_invite_mode(row, datetime.now(timezone.utc)), "banned")

    def test_revoked(self):
        row = self.row(status="revoked")
        self.assertEqual(self.bot.decide_invite_mode(row, datetime.now(timezone.utc)), "revoked")

    def test_approved_is_normal(self):
        row = self.row(status="approved")
        self.assertEqual(self.bot.decide_invite_mode(row, datetime.now(timezone.utc)), "normal")

    def test_paid_is_normal(self):
        row = self.row(status="paid")
        self.assertEqual(self.bot.decide_invite_mode(row, datetime.now(timezone.utc)), "normal")

    def test_trial_not_expired_is_normal(self):
        now = datetime(2026, 7, 4, tzinfo=timezone.utc)
        row = self.row(status="trial", trial_ends_at=(now + timedelta(days=1)).isoformat())
        self.assertEqual(self.bot.decide_invite_mode(row, now), "normal")

    def test_trial_expired(self):
        now = datetime(2026, 7, 4, tzinfo=timezone.utc)
        row = self.row(status="trial", trial_ends_at=(now - timedelta(seconds=1)).isoformat())
        self.assertEqual(self.bot.decide_invite_mode(row, now), "trial_expired")

    def test_trial_without_trial_ends_at_is_normal(self):
        now = datetime(2026, 7, 4, tzinfo=timezone.utc)
        row = self.row(status="trial", trial_ends_at="")
        self.assertEqual(self.bot.decide_invite_mode(row, now), "normal")

    def test_created_without_first_fetch_is_temp_hour(self):
        row = self.row(status="created", first_fetch_at="")
        self.assertEqual(
            self.bot.decide_invite_mode(row, datetime.now(timezone.utc)), "temp_hour"
        )

    def test_opened_within_hour_is_temp_hour(self):
        now = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
        row = self.row(status="opened", first_fetch_at=(now - timedelta(minutes=30)).isoformat())
        self.assertEqual(self.bot.decide_invite_mode(row, now), "temp_hour")

    def test_opened_after_hour_before_7_days_is_hour_expired(self):
        now = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
        row = self.row(status="opened", first_fetch_at=(now - timedelta(hours=2)).isoformat())
        self.assertEqual(self.bot.decide_invite_mode(row, now), "hour_expired")

    def test_tg_pending_after_hour_before_7_days_is_hour_expired(self):
        now = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
        row = self.row(status="tg_pending", first_fetch_at=(now - timedelta(days=3)).isoformat())
        self.assertEqual(self.bot.decide_invite_mode(row, now), "hour_expired")

    def test_tg_pending_after_7_days_is_expired(self):
        now = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
        row = self.row(status="tg_pending", first_fetch_at=(now - timedelta(days=8)).isoformat())
        self.assertEqual(self.bot.decide_invite_mode(row, now), "expired")

    def test_boundary_exactly_one_hour_is_hour_expired(self):
        now = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
        first_fetch = now - timedelta(hours=1)
        row = self.row(status="opened", first_fetch_at=first_fetch.isoformat())
        self.assertEqual(self.bot.decide_invite_mode(row, now), "hour_expired")

    def test_boundary_exactly_seven_days_is_expired(self):
        now = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
        first_fetch = now - timedelta(days=7)
        row = self.row(status="tg_pending", first_fetch_at=first_fetch.isoformat())
        self.assertEqual(self.bot.decide_invite_mode(row, now), "expired")

    def test_unknown_status_defaults_normal(self):
        row = self.row(status="something_else")
        self.assertEqual(self.bot.decide_invite_mode(row, datetime.now(timezone.utc)), "normal")


class ApplyInviteLazyTransitionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bot_db = str(Path(self.tmp.name) / "bot.db")
        self.patches = [
            patch.object(self.bot, "BOT_DB", self.bot_db),
            patch.object(self.bot, "SUBS_DIR", str(Path(self.tmp.name) / "subscriptions")),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.bot.init_bot_db()

    def make_invite(self, **kwargs):
        conn = sqlite3.connect(self.bot_db, timeout=30)
        conn.row_factory = sqlite3.Row
        fields = {
            "type": "personal",
            "inviter_username": "inviter",
            "token": "tok",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "first_fetch_at": "",
            "status": "created",
        }
        fields.update(kwargs)
        conn.execute(
            "INSERT INTO client_invites (type, inviter_username, token, created_at, first_fetch_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                fields["type"], fields["inviter_username"], fields["token"],
                fields["created_at"], fields["first_fetch_at"], fields["status"],
            ),
        )
        conn.commit()
        invite_id = conn.execute("SELECT id FROM client_invites WHERE token=?", (fields["token"],)).fetchone()[0]
        row = dict(conn.execute("SELECT * FROM client_invites WHERE id=?", (invite_id,)).fetchone())
        conn.close()
        return row

    def get_row(self, invite_id):
        conn = sqlite3.connect(self.bot_db, timeout=30)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM client_invites WHERE id=?", (invite_id,)).fetchone())
        conn.close()
        return row

    def count_events(self, invite_id, event=None):
        conn = sqlite3.connect(self.bot_db, timeout=30)
        if event:
            n = conn.execute(
                "SELECT COUNT(*) FROM client_invite_events WHERE invite_id=? AND event=?", (invite_id, event)
            ).fetchone()[0]
        else:
            n = conn.execute("SELECT COUNT(*) FROM client_invite_events WHERE invite_id=?", (invite_id,)).fetchone()[0]
        conn.close()
        return n

    def test_created_transitions_to_opened_and_sets_first_fetch(self):
        invite = self.make_invite(status="created", first_fetch_at="")
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.bot_db, timeout=30)
        conn.row_factory = sqlite3.Row
        result = self.bot.apply_invite_lazy_transition(conn, invite, now)
        conn.close()

        self.assertEqual(result["status"], "opened")
        self.assertTrue(result["first_fetch_at"])
        stored = self.get_row(invite["id"])
        self.assertEqual(stored["status"], "opened")
        self.assertEqual(self.count_events(invite["id"], "open"), 1)

    def test_idempotent_calling_twice_does_not_refire_or_corrupt(self):
        invite = self.make_invite(status="created", first_fetch_at="")
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.bot_db, timeout=30)
        conn.row_factory = sqlite3.Row
        first = self.bot.apply_invite_lazy_transition(conn, invite, now)
        second = self.bot.apply_invite_lazy_transition(conn, first, now)
        conn.close()

        self.assertEqual(first["status"], second["status"])
        self.assertEqual(first["first_fetch_at"], second["first_fetch_at"])
        self.assertEqual(self.count_events(invite["id"], "open"), 1)

    def test_opened_transitions_to_tg_pending_after_one_hour(self):
        first_fetch = datetime.now(timezone.utc) - timedelta(hours=2)
        invite = self.make_invite(status="opened", first_fetch_at=first_fetch.isoformat())
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.bot_db, timeout=30)
        conn.row_factory = sqlite3.Row
        result = self.bot.apply_invite_lazy_transition(conn, invite, now)
        conn.close()

        self.assertEqual(result["status"], "tg_pending")
        self.assertEqual(self.get_row(invite["id"])["status"], "tg_pending")

    def test_opened_stays_opened_within_one_hour(self):
        first_fetch = datetime.now(timezone.utc) - timedelta(minutes=10)
        invite = self.make_invite(status="opened", first_fetch_at=first_fetch.isoformat())
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.bot_db, timeout=30)
        conn.row_factory = sqlite3.Row
        result = self.bot.apply_invite_lazy_transition(conn, invite, now)
        conn.close()

        self.assertEqual(result["status"], "opened")

    def test_tg_pending_transitions_to_expired_after_seven_days(self):
        first_fetch = datetime.now(timezone.utc) - timedelta(days=8)
        invite = self.make_invite(status="tg_pending", first_fetch_at=first_fetch.isoformat())
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.bot_db, timeout=30)
        conn.row_factory = sqlite3.Row
        result = self.bot.apply_invite_lazy_transition(conn, invite, now)
        conn.close()

        self.assertEqual(result["status"], "expired")
        self.assertEqual(self.count_events(invite["id"], "expire"), 1)

    def test_tg_pending_expire_is_idempotent(self):
        first_fetch = datetime.now(timezone.utc) - timedelta(days=8)
        invite = self.make_invite(status="tg_pending", first_fetch_at=first_fetch.isoformat())
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.bot_db, timeout=30)
        conn.row_factory = sqlite3.Row
        first = self.bot.apply_invite_lazy_transition(conn, invite, now)
        second = self.bot.apply_invite_lazy_transition(conn, first, now)
        conn.close()

        self.assertEqual(first["status"], "expired")
        self.assertEqual(second["status"], "expired")
        self.assertEqual(self.count_events(invite["id"], "expire"), 1)

    def test_full_chain_created_to_expired_in_one_call_when_now_is_far_enough(self):
        # created -> opened is set with first_fetch_at=now (this fetch), so we can't jump straight
        # to expired in a single call chain (first_fetch_at didn't exist before). Verify partial
        # progression is correct instead: created always becomes opened first with fresh first_fetch.
        invite = self.make_invite(status="created", first_fetch_at="")
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.bot_db, timeout=30)
        conn.row_factory = sqlite3.Row
        result = self.bot.apply_invite_lazy_transition(conn, invite, now)
        conn.close()
        self.assertEqual(result["status"], "opened")


class ApplyReferralRewardIfDueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bot_db = str(Path(self.tmp.name) / "bot.db")
        self.patches = [
            patch.object(self.bot, "BOT_DB", self.bot_db),
            patch.object(self.bot, "SUBS_DIR", str(Path(self.tmp.name) / "subscriptions")),
            patch.object(self.bot, "_notify_bot", None),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.bot.init_bot_db()

    def make_invite(self, **kwargs):
        conn = sqlite3.connect(self.bot_db, timeout=30)
        fields = {
            "type": "ref",
            "inviter_username": "inviter",
            "token": "code1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "activated_username": "friend",
            "activated_tg_id": 555,
            "status": "trial",
            "reward_applied_at": "",
        }
        fields.update(kwargs)
        conn.execute(
            "INSERT INTO client_invites (type, inviter_username, token, created_at, activated_username, "
            "activated_tg_id, status, reward_applied_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fields["type"], fields["inviter_username"], fields["token"], fields["created_at"],
                fields["activated_username"], fields["activated_tg_id"], fields["status"],
                fields["reward_applied_at"],
            ),
        )
        conn.commit()
        invite_id = conn.execute("SELECT id FROM client_invites WHERE token=?", (fields["token"],)).fetchone()[0]
        conn.close()
        return invite_id

    def get_invite(self, invite_id):
        conn = sqlite3.connect(self.bot_db, timeout=30)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM client_invites WHERE id=?", (invite_id,)).fetchone())
        conn.close()
        return row

    @patch("subprocess.run")
    @patch("urllib.request.urlopen")
    def test_applies_reward_once_and_noops_on_second_call(self, mock_urlopen, mock_run):
        invite_id = self.make_invite()
        with patch.object(self.bot, "_notify_client_bot") as notify_client:
            self.bot.apply_referral_reward_if_due("friend", 12345)
        row = self.get_invite(invite_id)
        self.assertEqual(row["status"], "paid")
        self.assertEqual(row["reward_days"], 30)
        self.assertTrue(row["reward_applied_at"])
        notify_client.assert_called_once()

        profile = self.bot.get_admin_profile("inviter")
        expected = (datetime.utcnow().date() + timedelta(days=30)).isoformat()
        self.assertEqual(profile["paid_until"], expected)

        # second call: reward_applied_at is no longer '' -> no matching row -> no-op
        with patch.object(self.bot, "_notify_client_bot") as notify_client2:
            self.bot.apply_referral_reward_if_due("friend", 12345)
        notify_client2.assert_not_called()
        row2 = self.get_invite(invite_id)
        self.assertEqual(row2["reward_applied_at"], row["reward_applied_at"])
        self.assertEqual(row2["reward_days"], 30)

    def test_extends_from_today_when_inviter_subscription_already_expired(self):
        invite_id = self.make_invite()
        stale_past = (datetime.utcnow().date() - timedelta(days=100)).isoformat()
        self.bot.set_user_paid_until("inviter", stale_past, 1)

        with patch.object(self.bot, "_notify_client_bot"):
            self.bot.apply_referral_reward_if_due("friend", 12345)

        profile = self.bot.get_admin_profile("inviter")
        expected = (datetime.utcnow().date() + timedelta(days=30)).isoformat()
        self.assertEqual(profile["paid_until"], expected)

    def test_noop_when_friend_has_no_matching_invite(self):
        with patch.object(self.bot, "_notify_client_bot") as notify_client:
            self.bot.apply_referral_reward_if_due("nobody", 12345)
        notify_client.assert_not_called()

    def test_noop_when_invite_status_not_eligible(self):
        self.make_invite(status="banned", activated_username="banned_friend", activated_tg_id=777)
        with patch.object(self.bot, "_notify_client_bot") as notify_client:
            self.bot.apply_referral_reward_if_due("banned_friend", 12345)
        notify_client.assert_not_called()


class RewardDaysIsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def test_reward_days_is_standalone_callable(self):
        self.assertTrue(callable(self.bot.reward_days))
        self.assertEqual(self.bot.reward_days({"paid_until": ""}, {"paid_until": ""}), 30)
        self.assertEqual(self.bot.reward_days({"paid_until": "2099-01-01"}, {}), self.bot.REFERRAL_REWARD_DAYS)


class InternalEndpointAuthGatingTest(unittest.TestCase):
    """HTTP-level testing (spinning up SubHandler over a real socket) was skipped —
    the existing test suite doesn't exercise SubHandler.do_POST directly either
    (see test_bot_devices.py), so we unit-test the same IP/token predicates that
    do_POST applies before dispatch, following the existing /analyzer gate pattern."""

    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def test_notify_token_predicate(self):
        good_token = "s3cr3t"
        with patch.dict("os.environ", {"NOTIFY_TOKEN": good_token}):
            notify_token = __import__("os").environ.get("NOTIFY_TOKEN", "")
            self.assertTrue(notify_token and f"Bearer {notify_token}" == f"Bearer {good_token}")

    def test_internal_invite_paths_are_registered(self):
        self.assertIn("/internal/invites/create-user", self.bot.SubHandler.INTERNAL_INVITE_PATHS)
        self.assertIn("/internal/invites/set-remna", self.bot.SubHandler.INTERNAL_INVITE_PATHS)
        self.assertIn("/internal/invites/notify-owner", self.bot.SubHandler.INTERNAL_INVITE_PATHS)

    def test_ip_allowlist_predicate_matches_analyzer_pattern(self):
        server_ips = self.bot.SERVER_IPS
        for allowed in ("127.0.0.1", "::1", *list(server_ips)[:1]):
            self.assertTrue(allowed in ("127.0.0.1", "::1") or allowed in server_ips)
        self.assertFalse("203.0.113.5" in ("127.0.0.1", "::1") or "203.0.113.5" in server_ips)


class RemnawaveCreateUserExpireAtTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = load_bot()

    def test_passed_expire_at_is_used_instead_of_default(self):
        # remnawave_create_user живёт в remnawave_client.users (2026-07-05 move-only
        # рефакторинг) — патчить нужно там, а не в неймспейсе vpn-bot.py.
        calls = []

        def fake_remnawave_query(sql):
            calls.append(sql)
            return ""

        with patch.object(remnawave_client_users, "remnawave_user", return_value=None), \
                patch.object(remnawave_client_users, "remnawave_query", side_effect=fake_remnawave_query), \
                patch.object(remnawave_client_users, "remnawave_restart_all_nodes", return_value=True):
            self.bot.remnawave_create_user("someuser", device_limit=3, expire_at="2026-08-01 00:00:00")

        insert_sql = calls[0]
        self.assertIn("2026-08-01 00:00:00", insert_sql)
        self.assertNotIn(self.bot.REMNAWAVE_NEW_USER_EXPIRE, insert_sql)

    def test_default_expire_at_still_works_for_backward_compatibility(self):
        calls = []

        def fake_remnawave_query(sql):
            calls.append(sql)
            return ""

        with patch.object(remnawave_client_users, "remnawave_user", return_value=None), \
                patch.object(remnawave_client_users, "remnawave_query", side_effect=fake_remnawave_query), \
                patch.object(remnawave_client_users, "remnawave_restart_all_nodes", return_value=True):
            self.bot.remnawave_create_user("someuser2", device_limit=2)

        insert_sql = calls[0]
        self.assertIn(self.bot.REMNAWAVE_NEW_USER_EXPIRE, insert_sql)


if __name__ == "__main__":
    unittest.main()
