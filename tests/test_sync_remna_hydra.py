"""tests for scripts/sync_remna_hydra.py — JSON-source parsing, transport fidelity, WL."""

import base64
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sync_remna_hydra", ROOT / "scripts" / "sync_remna_hydra.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)

FIXTURE = ROOT / "tests" / "fixtures" / "hydra_subscription_sample.txt"


def load_fixture_profiles() -> list[dict]:
    """fixture = comment lines (#...) + several JSON objects concatenated."""
    text = FIXTURE.read_text()
    # strip comment lines
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    decoder = json.JSONDecoder()
    profiles: list[dict] = []
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        obj, end = decoder.raw_decode(text, idx)
        profiles.append(obj)
        idx = end
    return profiles


SUB_UID = "11111111-2222-3333-4444-555555555555"


class ClassifyProfilesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = load_fixture_profiles()
        cls.grouped, cls.wl, cls.uid = sync.classify_profiles(cls.profiles, probe=False)

    def test_fixture_parsed(self):
        self.assertGreaterEqual(len(self.profiles), 11)
        self.assertTrue(self.uid)  # sub uid extracted

    def test_countries(self):
        self.assertEqual(set(self.grouped), {"nl", "de", "pol", "tur"})
        self.assertEqual(len(self.grouped["nl"]), 3)  # tcp, tcp:8443, xhttp
        self.assertEqual(len(self.grouped["de"]), 1)

    def test_nl_transports(self):
        nets = sorted(
            s["outbound"].get("streamSettings", {}).get("network") for s in self.grouped["nl"]
        )
        self.assertEqual(nets, ["tcp", "tcp", "xhttp"])

    def test_wl_count(self):
        self.assertEqual(len(self.wl), 5)


class MakeOutboundTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grouped, cls.wl, _ = sync.classify_profiles(load_fixture_profiles(), probe=False)

    def test_xhttp_outbound_fidelity(self):
        # find the NL xhttp server
        xhttp = [
            s for s in self.grouped["nl"]
            if s["outbound"].get("streamSettings", {}).get("network") == "xhttp"
        ][0]
        ob = sync.make_outbound("nl", 2, xhttp, SUB_UID)
        ss = ob["streamSettings"]
        self.assertEqual(ss["network"], "xhttp")
        self.assertEqual(ss["security"], "reality")
        xh = ss["xhttpSettings"]
        self.assertEqual(xh.get("mode"), "stream-one")
        self.assertEqual(xh["extra"]["mode"], "stream-one")
        self.assertIn("xmux", xh["extra"])
        # uid rewritten, flow preserved (empty for this node)
        self.assertEqual(ob["settings"]["vnext"][0]["users"][0]["id"], SUB_UID)
        self.assertEqual(ob["tag"], "HYDRA_NL_3")

    def test_tcp_reality_outbound(self):
        tcp = [
            s for s in self.grouped["nl"]
            if s["outbound"].get("streamSettings", {}).get("network") == "tcp"
        ][0]
        ob = sync.make_outbound("nl", 0, tcp, SUB_UID)
        self.assertEqual(ob["streamSettings"]["security"], "reality")
        self.assertEqual(ob["settings"]["vnext"][0]["users"][0]["id"], SUB_UID)
        self.assertIn("realitySettings", ob["streamSettings"])


class UpdateProfileConfigTest(unittest.TestCase):
    def setUp(self):
        self.grouped, self.wl, _ = sync.classify_profiles(load_fixture_profiles(), probe=False)
        self.base_cfg = {
            "inbounds": [{"tag": "GOIDA_SMART"}],
            "outbounds": [{"tag": "DIRECT"}],
            "routing": {"rules": [], "balancers": []},
            "burstObservatory": {"subjectSelector": ["REMNA_FI"]},
        }

    def test_balancer_leastping_and_observatory(self):
        cfg = sync.update_profile_config(self.grouped, self.base_cfg, SUB_UID)
        nl_bal = [b for b in cfg["routing"]["balancers"] if b["tag"] == "BALANCER_HYDRA_NL"]
        self.assertEqual(len(nl_bal), 1)
        self.assertEqual(nl_bal[0]["strategy"], {"type": "leastPing"})
        self.assertEqual(len(nl_bal[0]["selector"]), 3)
        # observatory present, covers balanced NL tags, leaves existing burstObservatory intact
        self.assertIn("observatory", cfg)
        self.assertTrue(all(t.startswith("HYDRA_NL") for t in cfg["observatory"]["subjectSelector"]))
        self.assertEqual(cfg["burstObservatory"]["subjectSelector"], ["REMNA_FI"])

    def test_single_backend_no_balancer(self):
        cfg = sync.update_profile_config(self.grouped, self.base_cfg, SUB_UID)
        # DE has 1 backend → direct outboundTag, no balancer
        de_bal = [b for b in cfg["routing"]["balancers"] if b["tag"] == "BALANCER_HYDRA_DE"]
        self.assertEqual(de_bal, [])
        de_rule = [
            r for r in cfg["routing"]["rules"]
            if r.get("inboundTag") == ["GOIDA_HYDRA_DE"]
        ]
        self.assertEqual(de_rule[0]["outboundTag"], "HYDRA_DE")


class WhitelistTest(unittest.TestCase):
    def setUp(self):
        _, self.wl, _ = sync.classify_profiles(load_fixture_profiles(), probe=False)
        # force all backends alive for deterministic test
        self._orig = sync.tcp_alive
        sync.tcp_alive = lambda *a, **k: True

    def tearDown(self):
        sync.tcp_alive = self._orig

    def test_wl_lines_are_json_marker(self):
        lines = sync.build_wl_lines(self.wl, SUB_UID)
        self.assertEqual(len(lines), 5)
        for ln in lines:
            self.assertTrue(ln.startswith(sync.WL_JSON_PREFIX))

    def test_wl_uid_rewritten_and_backends(self):
        lines = sync.build_wl_lines(self.wl, SUB_UID)
        payload = lines[0][len(sync.WL_JSON_PREFIX):]
        prof = json.loads(base64.urlsafe_b64decode(payload + "==").decode())
        targets = [
            (vn["address"], vn["port"])
            for o in prof["outbounds"] if o.get("protocol") == "vless"
            for vn in o["settings"]["vnext"]
        ]
        self.assertGreaterEqual(len(targets), 2)  # WL#1 has 2 backends
        ids = {
            u["id"]
            for o in prof["outbounds"] if o.get("protocol") == "vless"
            for vn in o["settings"]["vnext"]
            for u in vn["users"]
        }
        self.assertEqual(ids, {SUB_UID})
        self.assertNotIn("log", prof)  # macOS log path stripped

    def test_wl_remarks_sequential(self):
        lines = sync.build_wl_lines(self.wl, SUB_UID)
        for i, ln in enumerate(lines, 1):
            payload = ln[len(sync.WL_JSON_PREFIX):]
            prof = json.loads(base64.urlsafe_b64decode(payload + "==").decode())
            self.assertEqual(prof["remarks"], f"Whitelist {i}\U0001f1f7\U0001f1fa")

    def test_wl_dead_skipped(self):
        sync.tcp_alive = lambda *a, **k: False
        lines = sync.build_wl_lines(self.wl, SUB_UID)
        self.assertEqual(lines, [])


if __name__ == "__main__":
    unittest.main()
