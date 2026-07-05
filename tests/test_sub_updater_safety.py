import importlib.util
import contextlib
import io
import json
import os
import sqlite3
import unittest
from pathlib import Path


def load_updater():
    os.environ.setdefault("SUB_UPDATER_LOG_PATH", "/private/tmp/sub-updater-test.log")
    path = Path(__file__).resolve().parents[1] / "sub-updater" / "updater.py"
    spec = importlib.util.spec_from_file_location("sub_updater_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SubUpdaterSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.updater = load_updater()

    def test_template_backup_limit_and_restore(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO settings (key, value) VALUES ('xrayTemplateConfig', ?)", ("initial",))

        for i in range(12):
            changed = self.updater.update_template_config(conn, f"template-{i}", "test")
            self.assertTrue(changed)
            conn.commit()

        backups = conn.execute("SELECT id, content FROM template_backups ORDER BY id").fetchall()
        self.assertEqual(len(backups), 10)
        self.assertEqual(backups[0][1], "template-1")
        self.assertEqual(backups[-1][1], "template-10")

        restored = self.updater.restore_template(backups[0][0], conn=conn)
        self.assertTrue(restored)
        current = conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()[0]
        self.assertEqual(current, "template-1")

    def test_update_template_dry_run_does_not_write_or_backup(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO settings (key, value) VALUES ('xrayTemplateConfig', ?)", ("before",))

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            changed = self.updater.update_template_config(conn, "after", "test", dry_run=True)
        self.assertTrue(changed)
        current = conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()[0]
        self.assertEqual(current, "before")
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='template_backups'"
        ).fetchone()
        self.assertIsNone(table_exists)

    def test_ru_cleanup_removes_stale_hydra_country_and_keeps_manual_rules(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            """
            CREATE TABLE inbounds (
                id INTEGER PRIMARY KEY,
                port INTEGER NOT NULL,
                enable INTEGER NOT NULL,
                remark TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE TABLE client_server_prefs (username TEXT, server_key TEXT, enabled INTEGER)")
        for idx, slot in enumerate(self.updater.RU_COUNTRY_SLOTS.values(), start=1):
            conn.execute(
                "INSERT INTO inbounds (id, port, enable, remark) VALUES (?, ?, 1, ?)",
                (idx, slot["port"], slot["remark"]),
            )
        conn.executemany(
            "INSERT INTO client_server_prefs (username, server_key, enabled) VALUES ('u', ?, 0)",
            [("hydra:usa",), ("hydra:pol",), ("hydra:tur",)],
        )

        manual_rule = {
            "type": "field",
            "ruleTag": "manual-direct",
            "inboundTag": ["inbound-10003", "inbound-10005"],
            "domain": ["domain:manual.example"],
            "outboundTag": "direct",
        }
        manual_static_rule = {
            "type": "field",
            "ruleTag": "manual-home",
            "inboundTag": ["inbound-10003", "inbound-10005"],
            "domain": ["domain:lk.sut.ru"],
            "outboundTag": "home-mac-exit",
        }
        before = {
            "outbounds": [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "hydra-proxy-usa", "protocol": "vless"},
                {"tag": "hydra-proxy-pol", "protocol": "vless"},
                {"tag": "hydra-proxy-tur", "protocol": "vless"},
            ],
            "routing": {
                "balancers": [{"tag": "balancer-smart", "selector": ["proxy-fi", "proxy-se"], "fallbackTag": "proxy-fi"}],
                "rules": [
                    manual_rule,
                    manual_static_rule,
                    {"type": "field", "inboundTag": ["inbound-10011"], "outboundTag": "hydra-proxy-usa"},
                    {"type": "field", "inboundTag": ["inbound-10012"], "outboundTag": "hydra-proxy-pol"},
                    {"type": "field", "inboundTag": ["inbound-10013"], "outboundTag": "hydra-proxy-tur"},
                ],
            },
        }
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('xrayTemplateConfig', ?)",
            (json.dumps(before),),
        )

        changed = self.updater.manage_hydra_ru(
            [hydra_server("США"), hydra_server("Польша")],
            conn,
        )
        self.assertTrue(changed)
        conn.commit()

        after_raw = conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()[0]
        after = json.loads(after_raw)
        outbound_tags = {item["tag"] for item in after["outbounds"]}
        self.assertIn("hydra-proxy-usa", outbound_tags)
        self.assertIn("hydra-proxy-pol", outbound_tags)
        self.assertNotIn("hydra-proxy-tur", outbound_tags)

        rules = after["routing"]["rules"]
        self.assertFalse(any(rule.get("outboundTag") == "hydra-proxy-tur" for rule in rules))
        self.assertFalse(any("inbound-10013" in rule.get("inboundTag", []) for rule in rules))
        self.assertTrue(any(rule.get("domain") == ["domain:manual.example"] for rule in rules))
        self.assertTrue(any(
            rule.get("ruleTag") == "manual-home"
            and rule.get("outboundTag") == "home-mac-exit"
            and rule.get("domain") == ["domain:lk.sut.ru"]
            for rule in rules
        ))

        prefs = conn.execute("SELECT server_key FROM client_server_prefs ORDER BY server_key").fetchall()
        self.assertEqual([row[0] for row in prefs], ["hydra:pol", "hydra:usa"])

        backup = conn.execute("SELECT reason, content FROM template_backups").fetchone()
        self.assertEqual(backup[0], "manage_hydra_ru")
        self.assertEqual(json.loads(backup[1]), before)


def hydra_server(name):
    return {
        "name": name,
        "raw_name": name,
        "host": "example.com",
        "port": 443,
        "uuid": "11111111-1111-1111-1111-111111111111",
        "flow": "",
        "pbk": "",
        "sni": "example.com",
        "fp": "chrome",
        "sid": "",
    }


if __name__ == "__main__":
    unittest.main()
