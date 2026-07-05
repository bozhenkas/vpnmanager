import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import unittest
from pathlib import Path


os.environ.setdefault("SUB_UPDATER_LOG_PATH", "/tmp/sub-updater-test.log")

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sub_updater", ROOT / "sub-updater" / "updater.py")
sub_updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sub_updater)


def hydra_server(name, host):
    return {
        "name": name,
        "raw_name": name,
        "uuid": f"00000000-0000-0000-0000-{host.replace('.', ''):0<12}"[:36],
        "host": host,
        "port": 443,
        "flow": "",
        "sni": "example.com",
        "pbk": "",
        "sid": "",
        "fp": "chrome",
    }


def make_conn(template):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO settings(key, value) VALUES ('xrayTemplateConfig', ?)", (json.dumps(template),))
    conn.execute("CREATE TABLE inbounds(id INTEGER PRIMARY KEY, enable INTEGER, remark TEXT, port INTEGER)")
    conn.execute("CREATE TABLE client_server_prefs(user_id INTEGER, server_key TEXT, enabled INTEGER)")
    return conn


def base_template():
    return {
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "home-mac-exit", "protocol": "freedom"},
            {"tag": "blocked", "protocol": "blackhole"},
            {"tag": "proxy-fi", "protocol": "freedom"},
            {"tag": "proxy-se", "protocol": "freedom"},
            {"tag": "dns-out", "protocol": "dns"},
            {"tag": "smart-pro-out", "protocol": "socks"},
            {"tag": "hydra-proxy-usa", "protocol": "vless"},
            {"tag": "hydra-proxy-pol", "protocol": "vless"},
            {"tag": "hydra-proxy-tur", "protocol": "vless"},
        ],
        "routing": {
            "balancers": [{"tag": "balancer-smart", "selector": ["proxy-fi", "proxy-se"], "fallbackTag": "proxy-fi"}],
            "rules": [
                {
                    "type": "field",
                    "ruleTag": "manual-direct",
                    "inboundTag": ["inbound-10003", "inbound-10005"],
                    "domain": ["domain:manual.example"],
                    "outboundTag": "direct",
                },
                {
                    "type": "field",
                    "ruleTag": "manual-home",
                    "inboundTag": ["inbound-10003", "inbound-10005"],
                    "domain": ["domain:lk.sut.ru"],
                    "outboundTag": "home-mac-exit",
                },
                {"type": "field", "inboundTag": ["inbound-10011"], "outboundTag": "hydra-proxy-usa"},
                {"type": "field", "inboundTag": ["inbound-10012"], "outboundTag": "hydra-proxy-pol"},
                {"type": "field", "inboundTag": ["inbound-10013"], "outboundTag": "hydra-proxy-tur"},
            ],
        },
    }


class SubUpdaterTest(unittest.TestCase):
    def test_template_backups_keep_last_ten_and_restore(self):
        conn = make_conn({"outbounds": [], "routing": {"rules": []}})
        for i in range(12):
            current = conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()[0]
            new_template = json.dumps({"version": i})
            sub_updater.update_template_config(conn, new_template, f"test-{i}", old_content=current)
        backups = conn.execute("SELECT id, reason FROM template_backups ORDER BY id").fetchall()
        self.assertEqual(len(backups), 10)
        self.assertEqual(backups[0][1], "test-2")

        first_kept_id = backups[0][0]
        self.assertTrue(sub_updater.restore_template(first_kept_id, conn=conn))
        restored = json.loads(conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()[0])
        self.assertEqual(restored, {"version": 1})

    def test_dry_run_prints_diff_and_does_not_write_db(self):
        conn = make_conn(base_template())
        for port, cc in [(10011, "usa"), (10012, "pol"), (10013, "tur")]:
            conn.execute("INSERT INTO inbounds(id, enable, remark, port) VALUES (?, 1, ?, ?)", (port, cc, port))
        before = conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()[0]

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            changed = sub_updater.manage_hydra_ru(
                [hydra_server("США", "1.1.1.1"), hydra_server("Польша", "2.2.2.2")],
                conn,
                dry_run=True,
            )

        self.assertTrue(changed)
        self.assertEqual(conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()[0], before)
        self.assertFalse(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='template_backups'"
        ).fetchone())
        self.assertIn("--- xrayTemplateConfig.before", out.getvalue())
        self.assertIn("DRY RUN:", out.getvalue())

    def test_ru_hydra_cleanup_stale_country_preserves_manual_rules(self):
        conn = make_conn(base_template())
        for port, cc in [(10011, "usa"), (10012, "pol"), (10013, "tur")]:
            conn.execute("INSERT INTO inbounds(id, enable, remark, port) VALUES (?, 1, ?, ?)", (port, cc, port))
        conn.executemany(
            "INSERT INTO client_server_prefs(user_id, server_key, enabled) VALUES (?, ?, ?)",
            [(1, "hydra:usa", 1), (1, "hydra:pol", 1), (1, "hydra:tur", 1)],
        )

        changed = sub_updater.manage_hydra_ru(
            [hydra_server("США", "1.1.1.1"), hydra_server("Польша", "2.2.2.2")],
            conn,
        )

        self.assertTrue(changed)
        cfg = json.loads(conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()[0])
        out_tags = {o["tag"] for o in cfg["outbounds"]}
        self.assertIn("hydra-proxy-usa", out_tags)
        self.assertIn("hydra-proxy-pol", out_tags)
        self.assertNotIn("hydra-proxy-tur", out_tags)

        rules = cfg["routing"]["rules"]
        self.assertFalse(any("inbound-10013" in r.get("inboundTag", []) for r in rules))
        self.assertTrue(any(r.get("ruleTag") == "manual-direct" and "domain:manual.example" in r.get("domain", []) for r in rules))
        self.assertTrue(any(
            r.get("ruleTag") == "manual-home"
            and r.get("outboundTag") == "home-mac-exit"
            and "domain:lk.sut.ru" in r.get("domain", [])
            for r in rules
        ))
        prefs = conn.execute("SELECT server_key FROM client_server_prefs ORDER BY server_key").fetchall()
        self.assertEqual([row[0] for row in prefs], ["hydra:pol", "hydra:usa"])
        self.assertEqual(conn.execute("SELECT enable FROM inbounds WHERE port=10013").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM template_backups").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
