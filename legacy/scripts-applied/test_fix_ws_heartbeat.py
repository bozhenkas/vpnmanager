import json
import os
import sqlite3
import tempfile
import unittest

from scripts.fix_ws_heartbeat import fix_ws_heartbeat


class FixWsHeartbeatTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE inbounds (
                id INTEGER PRIMARY KEY,
                remark TEXT,
                enable INTEGER,
                stream_settings TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO inbounds (id, remark, enable, stream_settings) VALUES (?, ?, ?, ?)",
            [
                (1, "ws missing", 1, json.dumps({"network": "ws", "wsSettings": {"path": "/a"}})),
                (2, "ws wrong", 1, json.dumps({"network": "ws", "heartbeatPeriod": 10})),
                (3, "ws ok", 1, json.dumps({"network": "ws", "heartbeatPeriod": 30})),
                (4, "tcp", 1, json.dumps({"network": "tcp"})),
                (5, "disabled ws", 0, json.dumps({"network": "ws"})),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_dry_run_does_not_write(self):
        changed = fix_ws_heartbeat(self.db_path, dry_run=True)
        self.assertEqual(changed, [(1, "ws missing"), (2, "ws wrong")])

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT stream_settings FROM inbounds WHERE id=1").fetchone()
        conn.close()
        self.assertNotIn("heartbeatPeriod", json.loads(row[0]))

    def test_apply_and_idempotent_second_run(self):
        changed = fix_ws_heartbeat(self.db_path)
        self.assertEqual(changed, [(1, "ws missing"), (2, "ws wrong")])

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT id, stream_settings FROM inbounds ORDER BY id").fetchall()
        conn.close()
        by_id = {row[0]: json.loads(row[1]) for row in rows}
        self.assertEqual(by_id[1]["heartbeatPeriod"], 30)
        self.assertEqual(by_id[2]["heartbeatPeriod"], 30)
        self.assertEqual(by_id[3]["heartbeatPeriod"], 30)
        self.assertNotIn("heartbeatPeriod", by_id[4])
        self.assertNotIn("heartbeatPeriod", by_id[5])

        self.assertEqual(fix_ws_heartbeat(self.db_path), [])


if __name__ == "__main__":
    unittest.main()
