import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def load_client_bot():
    path = Path(__file__).resolve().parents[1] / "client-bot" / "client-bot.py"
    spec = importlib.util.spec_from_file_location("client_bot_invites_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InvitesTestBase(unittest.TestCase):
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

        self.username_patch = patch.object(self.bot, "BOT_USERNAME", "goida_client_bot")
        self.username_patch.start()
        self.addCleanup(self.username_patch.stop)
        self.get_username_patch = patch.object(self.bot, "get_bot_username", return_value="goida_client_bot")
        self.get_username_patch.start()
        self.addCleanup(self.get_username_patch.stop)

        # avoid touching remnawave/postgres in unit tests
        self.remna_patch = patch.object(self.bot, "remnawave_user", return_value=None)
        self.remna_patch.start()
        self.addCleanup(self.remna_patch.stop)

        self.bot.init_db()

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE users (name TEXT PRIMARY KEY, token TEXT, tg_id INTEGER)")
        conn.execute(
            """
            CREATE TABLE user_devices (
                token TEXT, device_id TEXT, first_seen TEXT, last_seen TEXT,
                client_ip TEXT, app_name TEXT, app_version TEXT,
                platform TEXT, platform_version TEXT, device_name TEXT
            )
            """
        )
        conn.commit()
        conn.close()
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def capture_send(self, tg_id, text, markup=None):
        self.sent.append((tg_id, text, markup))

    def add_user(self, username, token, tg_id=None):
        self.conn.execute("INSERT INTO users (name, token, tg_id) VALUES (?, ?, ?)", (username, token, tg_id))
        self.conn.execute(
            "INSERT INTO client_profiles (username, device_limit, paid_until, updated_at) VALUES (?, ?, ?, ?)",
            (username, 2, "", "now"),
        )
        self.conn.commit()

    def mark_paid(self, username, paid_until="2026-12-31"):
        self.conn.execute("UPDATE client_profiles SET paid_until=? WHERE username=?", (paid_until, username))
        self.conn.commit()

    def link_tg(self, tg_id, username, tg_username=""):
        self.conn.execute(
            "INSERT OR REPLACE INTO client_tg_links (tg_id, username, tg_username, created_at) VALUES (?, ?, ?, ?)",
            (tg_id, username, tg_username, "now"),
        )
        self.conn.commit()


class CanInviteTest(InvitesTestBase):
    def test_can_invite_true_when_paid_until_set(self):
        self.add_user("alice", "token-alice")
        self.mark_paid("alice")
        self.assertTrue(self.bot.can_invite("alice"))

    def test_can_invite_false_when_never_paid(self):
        self.add_user("bob", "token-bob")
        self.assertFalse(self.bot.can_invite("bob"))

    def test_can_invite_false_for_unknown_user(self):
        self.assertFalse(self.bot.can_invite("ghost"))

    def test_can_invite_true_for_permanent_free_access(self):
        self.add_user("carol", "token-carol")
        self.conn.execute("UPDATE client_profiles SET free_access=1 WHERE username=?", ("carol",))
        self.conn.commit()
        self.assertTrue(self.bot.can_invite("carol"))

    def test_can_invite_true_after_paid_until_expired_and_never_renewed(self):
        self.add_user("dave", "token-dave")
        self.mark_paid("dave", paid_until="2020-01-01")
        self.assertTrue(self.bot.can_invite("dave"))


class DeepLinkDispatchTest(InvitesTestBase):
    def test_ref_prefix_dispatches_to_ref_handler(self):
        with patch.object(self.bot, "handle_ref_deeplink") as ref_handler, \
                patch.object(self.bot, "handle_link_deeplink") as link_handler:
            update = {"message": {"chat": {"id": 1}, "from": {"id": 1, "username": "u"}, "text": "/start ref_abc123"}}
            self.bot.handle_update(update)
        ref_handler.assert_called_once_with(1, 1, "u", "abc123")
        link_handler.assert_not_called()

    def test_link_prefix_dispatches_to_link_handler(self):
        with patch.object(self.bot, "handle_ref_deeplink") as ref_handler, \
                patch.object(self.bot, "handle_link_deeplink") as link_handler:
            update = {"message": {"chat": {"id": 1}, "from": {"id": 1, "username": "u"}, "text": "/start link_xyz789"}}
            self.bot.handle_update(update)
        link_handler.assert_called_once_with(1, 1, "u", "xyz789")
        ref_handler.assert_not_called()

    def test_legacy_bare_token_dispatches_to_resolve_invite_token(self):
        self.add_user("carol", "token-carol")
        token = self.bot.get_or_create_invite_token("carol")
        with patch.object(self.bot, "handle_ref_deeplink") as ref_handler, \
                patch.object(self.bot, "handle_link_deeplink") as link_handler, \
                patch.object(self.bot, "link_user") as link_user:
            update = {"message": {"chat": {"id": 2}, "from": {"id": 2, "username": "d"}, "text": f"/start {token}"}}
            self.bot.handle_update(update)
        ref_handler.assert_not_called()
        link_handler.assert_not_called()
        link_user.assert_called_once_with(2, "carol", "d")


class RefDeepLinkTest(InvitesTestBase):
    def setUp(self):
        super().setUp()
        self.add_user("alice", "token-alice")
        self.mark_paid("alice")
        code = self.bot.get_or_create_ref_code("alice")
        self.code = code

    def test_new_user_creates_trial_via_internal_call(self):
        with patch.object(self.bot, "call_vpnbot_internal") as call_internal:
            call_internal.return_value = {"ok": True, "token": "tok", "subUrl": "https://ru.goida.fun/subscribe/tok"}
            self.bot.handle_ref_deeplink(100, 100, "friend_a", self.code)

        create_call = call_internal.call_args_list[0]
        self.assertEqual(create_call.args[0], "/internal/invites/create-user")
        self.assertEqual(create_call.args[1]["deviceLimit"], self.bot.DEFAULT_DEVICES)
        self.assertEqual(create_call.args[1]["expireDays"], 7)

        rows = self.conn.execute("SELECT * FROM client_invites WHERE inviter_username='alice'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "trial")
        self.assertEqual(rows[0]["activated_tg_id"], 100)

        linked = self.conn.execute("SELECT username FROM client_tg_links WHERE tg_id=?", (100,)).fetchone()
        self.assertIsNotNone(linked)

        self.assertTrue(any("ссылка подписки" in text for _, text, _ in self.sent))

    def test_internal_call_failure_does_not_create_invite_row(self):
        with patch.object(self.bot, "call_vpnbot_internal", return_value=None):
            self.bot.handle_ref_deeplink(101, 101, "friend_b", self.code)

        rows = self.conn.execute("SELECT * FROM client_invites").fetchall()
        self.assertEqual(len(rows), 0)

    def test_unknown_ref_code_is_invalid(self):
        with patch.object(self.bot, "call_vpnbot_internal") as call_internal:
            self.bot.handle_ref_deeplink(102, 102, "friend_c", "not-a-real-code")
        call_internal.assert_not_called()
        self.assertTrue(any("недействительна" in text for _, text, _ in self.sent))


class DedupTest(InvitesTestBase):
    def setUp(self):
        super().setUp()
        self.add_user("alice", "token-alice")
        self.mark_paid("alice")
        self.add_user("dave", "token-dave")
        self.mark_paid("dave")
        self.code_alice = self.bot.get_or_create_ref_code("alice")
        self.code_dave = self.bot.get_or_create_ref_code("dave")

    def _create_via_ref(self, code, tg_id, tg_username):
        with patch.object(self.bot, "call_vpnbot_internal") as call_internal:
            call_internal.return_value = {"ok": True, "token": "tok", "subUrl": "https://x/tok"}
            self.bot.handle_ref_deeplink(tg_id, tg_id, tg_username, code)

    def test_own_link_message(self):
        # alice opens her own ref link -- she must already be linked to herself
        self.link_tg(500, "alice", "alice_tg")
        self.sent.clear()
        with patch.object(self.bot, "call_vpnbot_internal") as call_internal:
            self.bot.handle_ref_deeplink(500, 500, "alice_tg", self.code_alice)
        call_internal.assert_not_called()
        self.assertTrue(any("это твоя ссылка" in text for _, text, _ in self.sent))
        rows = self.conn.execute("SELECT * FROM client_invites").fetchall()
        self.assertEqual(len(rows), 0)

    def test_second_inviter_names_first_inviter(self):
        self._create_via_ref(self.code_alice, 600, "friend_x")
        self.sent.clear()
        with patch.object(self.bot, "call_vpnbot_internal") as call_internal:
            self.bot.handle_ref_deeplink(600, 600, "friend_x", self.code_dave)
        call_internal.assert_not_called()
        self.assertTrue(any("уже был приглашён alice" in text for _, text, _ in self.sent))
        rows = self.conn.execute("SELECT * FROM client_invites").fetchall()
        self.assertEqual(len(rows), 1)  # no new invite row created

    def test_organic_existing_user_gets_generic_message(self):
        self.add_user("erin", "token-erin")
        self.link_tg(700, "erin", "erin_tg")
        self.sent.clear()
        with patch.object(self.bot, "call_vpnbot_internal") as call_internal:
            self.bot.handle_ref_deeplink(700, 700, "erin_tg", self.code_alice)
        call_internal.assert_not_called()
        self.assertTrue(any("уже привязан" in text for _, text, _ in self.sent))
        rows = self.conn.execute("SELECT * FROM client_invites").fetchall()
        self.assertEqual(len(rows), 0)


class PersonalInviteRaceTest(InvitesTestBase):
    def setUp(self):
        super().setUp()
        self.add_user("alice", "token-alice")
        self.mark_paid("alice")

    def _insert_personal_invite(self, token="tok123"):
        cur = self.conn.execute(
            """
            INSERT INTO client_invites (type, inviter_username, token, created_at, activated_username, status)
            VALUES ('personal', ?, ?, ?, ?, 'created')
            """,
            ("alice", token, self.bot.now_iso(), "friend-user"),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def test_first_activation_wins_second_gets_rowcount_zero(self):
        self.add_user("friend-user", "token-friend")
        invite_id = self._insert_personal_invite()

        with patch.object(self.bot, "call_vpnbot_internal", return_value={"ok": True}):
            self.bot.handle_link_deeplink(1, 1001, "first_friend", "tok123")

        row = self.conn.execute("SELECT * FROM client_invites WHERE id=?", (invite_id,)).fetchone()
        self.assertEqual(row["status"], "trial")
        self.assertEqual(row["activated_tg_id"], 1001)

        self.sent.clear()
        with patch.object(self.bot, "call_vpnbot_internal") as call_internal:
            self.bot.handle_link_deeplink(2, 1002, "second_friend", "tok123")

        call_internal.assert_not_called()
        row_after = self.conn.execute("SELECT * FROM client_invites WHERE id=?", (invite_id,)).fetchone()
        self.assertEqual(row_after["activated_tg_id"], 1001)  # unchanged, second lost race
        self.assertTrue(any("активировано кем-то другим" in text for _, text, _ in self.sent))

    def test_unknown_token_is_invalid(self):
        with patch.object(self.bot, "call_vpnbot_internal") as call_internal:
            self.bot.handle_link_deeplink(3, 1003, "someone", "no-such-token")
        call_internal.assert_not_called()
        self.assertTrue(any("недействительна" in text for _, text, _ in self.sent))

    def test_revoked_invite_rejects_activation(self):
        invite_id = self._insert_personal_invite(token="tok-revoked")
        self.conn.execute("UPDATE client_invites SET status='revoked' WHERE id=?", (invite_id,))
        self.conn.commit()
        with patch.object(self.bot, "call_vpnbot_internal") as call_internal:
            self.bot.handle_link_deeplink(4, 1004, "x", "tok-revoked")
        call_internal.assert_not_called()
        self.assertTrue(any("больше не активно" in text for _, text, _ in self.sent))


class PaymentLockTest(InvitesTestBase):
    def test_invite_state_no_invite_row(self):
        self.add_user("alice", "token-alice")
        self.mark_paid("alice")
        state = self.bot.invite_state_for_username("alice")
        self.assertIsNone(state["status"])
        self.assertFalse(state["paymentLocked"])
        self.assertTrue(state["eligible"])

    def test_invite_state_locked_until_approved_or_paid(self):
        self.add_user("friend1", "token-friend1")
        self.conn.execute(
            """
            INSERT INTO client_invites (type, inviter_username, token, created_at, activated_username, status)
            VALUES ('ref', 'alice', 'code1', ?, 'friend1', 'trial')
            """,
            (self.bot.now_iso(),),
        )
        self.conn.commit()
        state = self.bot.invite_state_for_username("friend1")
        self.assertEqual(state["status"], "trial")
        self.assertTrue(state["paymentLocked"])

        self.conn.execute("UPDATE client_invites SET status='approved' WHERE activated_username='friend1'")
        self.conn.commit()
        state2 = self.bot.invite_state_for_username("friend1")
        self.assertFalse(state2["paymentLocked"])

    def test_plan_request_handler_returns_403_when_locked(self):
        self.add_user("friend1", "token-friend1")
        self.link_tg(2001, "friend1", "friend1_tg")
        self.conn.execute(
            """
            INSERT INTO client_invites (type, inviter_username, token, created_at, activated_username, status)
            VALUES ('ref', 'alice', 'code1', ?, 'friend1', 'trial')
            """,
            (self.bot.now_iso(),),
        )
        self.conn.commit()

        init_data = "fake-init-data"
        with patch.object(self.bot, "validate_init_data", return_value={"id": 2001}):
            handler = FakeApiHandler({"X-Telegram-Init-Data": init_data}, self.bot)
            self.bot.ApiHandler.handle_plan_request(handler)

        self.assertEqual(handler.status, 403)
        payload = json.loads(handler.body)
        self.assertEqual(payload["error"], "payment_locked")


class ApiInvitesGetTest(InvitesTestBase):
    def test_not_eligible_returns_teaser_shape(self):
        self.add_user("bob", "token-bob")
        payload = self.bot.build_invites_payload("bob")
        self.assertFalse(payload["eligible"])
        self.assertIsNone(payload["refCode"])
        self.assertEqual(payload["invited"], [])
        self.assertEqual(payload["personalPending"], [])
        self.assertEqual(payload["earnedDays"], 0)

    def test_eligible_shape_and_ref_code_persists(self):
        self.add_user("alice", "token-alice")
        self.mark_paid("alice")
        payload1 = self.bot.build_invites_payload("alice")
        self.assertTrue(payload1["eligible"])
        self.assertIsNotNone(payload1["refCode"])
        self.assertIn("ref_", payload1["refLink"])
        self.assertIn(payload1["refCode"], payload1["refLink"])

        payload2 = self.bot.build_invites_payload("alice")
        self.assertEqual(payload1["refCode"], payload2["refCode"])

    def test_invited_list_reflects_status_text(self):
        self.add_user("alice", "token-alice")
        self.mark_paid("alice")
        self.add_user("friendx", "token-friendx")
        self.conn.execute(
            """
            INSERT INTO client_invites (type, inviter_username, token, created_at, activated_username, status)
            VALUES ('ref', 'alice', 'code9', ?, 'friendx', 'paid')
            """,
            (self.bot.now_iso(),),
        )
        self.conn.commit()
        payload = self.bot.build_invites_payload("alice")
        self.assertEqual(len(payload["invited"]), 1)
        self.assertEqual(payload["invited"][0]["statusText"], "оплатил ✓")


class RevokeInviteTest(InvitesTestBase):
    def setUp(self):
        super().setUp()
        self.add_user("alice", "token-alice")
        self.mark_paid("alice")
        self.add_user("bob", "token-bob")
        self.mark_paid("bob")

    def _insert_personal(self, inviter, status="created", token="tokr"):
        cur = self.conn.execute(
            """
            INSERT INTO client_invites (type, inviter_username, token, created_at, activated_username, status)
            VALUES ('personal', ?, ?, ?, 'friend-r', ?)
            """,
            (inviter, token, self.bot.now_iso(), status),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def test_cannot_revoke_someone_elses_invite(self):
        invite_id = self._insert_personal("bob")
        ok = self.bot.revoke_personal_invite("alice", invite_id)
        self.assertFalse(ok)
        row = self.conn.execute("SELECT status FROM client_invites WHERE id=?", (invite_id,)).fetchone()
        self.assertEqual(row["status"], "created")

    def test_cannot_revoke_already_active_invite(self):
        invite_id = self._insert_personal("alice", status="trial")
        ok = self.bot.revoke_personal_invite("alice", invite_id)
        self.assertFalse(ok)

    def test_revoke_own_pending_invite_succeeds(self):
        invite_id = self._insert_personal("alice", status="created")
        with patch.object(self.bot, "call_vpnbot_internal") as call_internal:
            call_internal.return_value = {"ok": True}
            ok = self.bot.revoke_personal_invite("alice", invite_id)
        self.assertTrue(ok)
        row = self.conn.execute("SELECT status FROM client_invites WHERE id=?", (invite_id,)).fetchone()
        self.assertEqual(row["status"], "revoked")
        call_internal.assert_called_once()
        self.assertEqual(call_internal.call_args.args[0], "/internal/invites/set-remna")
        self.assertTrue(call_internal.call_args.args[1]["expireNow"])


class FakeApiHandler:
    def __init__(self, headers, bot_module):
        self.headers = headers
        self.status = None
        self.response_headers = {}
        self.body = b""
        self.wfile = self
        self._bot = bot_module

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        pass

    def write(self, body):
        self.body += body

    def read_json(self):
        return {}

    def send_json(self, status, payload):
        self._bot.ApiHandler.send_json(self, status, payload)


if __name__ == "__main__":
    unittest.main()
