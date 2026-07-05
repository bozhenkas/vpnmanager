import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc = load_module("cluster_config_under_test", "subscription/cluster_config.py")


def base_inventory() -> dict:
    return {
        "schema": 1,
        "version": 7,
        "servers": {
            "ru": {"role": "primary", "status": "active", "domain": "ru.goida.fun",
                   "ip": "45.91.54.152", "ssh_host": "45.91.54.152", "ssh_port": 17904, "cf_managed": True},
            "fin": {"role": "exit", "status": "active", "domain": "fin.goida.fun",
                    "ip": "77.110.108.57", "ssh_port": 17904, "transports": ["grpc-reality:443"]},
            "swe": {"role": "exit", "status": "active", "domain": "swe.goida.fun",
                    "ip": "89.22.230.5", "ssh_port": 17904, "transports": ["xhttp-reality:443"]},
            "swe_old": {"role": "exit", "status": "disabled", "domain": "old.goida.fun",
                        "ip": "89.22.230.99"},
            "backup": {"role": "backup", "status": "backup", "ip": "45.91.53.93", "cf_managed": True},
            "ru4": {"role": "reserve", "status": "reserve", "ip": "194.117.80.94", "ssh_port": 22},
        },
        "cloudflare": {"primary_domain": "ru.goida.fun"},
        "legacy_blocklist": ["83.147.255.98"],
    }


class ParseAccessorsTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cc.parse_cluster(json.dumps(base_inventory()).encode())

    def test_version_and_primary_backup(self):
        self.assertEqual(self.cfg.version, 7)
        self.assertEqual(self.cfg.primary_cf_ip(), "45.91.54.152")
        self.assertEqual(self.cfg.backup_cf_ip(), "45.91.53.93")
        self.assertEqual(self.cfg.primary_domain(), "ru.goida.fun")

    def test_active_exits_exclude_disabled(self):
        self.assertEqual(self.cfg.active_exit_ips(), {"77.110.108.57", "89.22.230.5"})

    def test_server_ip_set_is_primary_plus_active_exits(self):
        self.assertEqual(
            self.cfg.server_ip_set(),
            {"45.91.54.152", "77.110.108.57", "89.22.230.5"},
        )

    def test_ssh_node(self):
        self.assertEqual(self.cfg.ssh_node("ru"), ("45.91.54.152", 17904))
        self.assertEqual(self.cfg.ssh_node("ru4"), ("194.117.80.94", 22))

    def test_probe_endpoints_match_legacy_shape(self):
        eps = self.cfg.probe_endpoints()
        labels = [e["label"] for e in eps]
        self.assertEqual(labels[:3], ["domain", "primary_ip", "backup_ip"])
        by_label = {e["label"]: e for e in eps}
        self.assertEqual(by_label["domain"]["url"], "https://ru.goida.fun/")
        self.assertEqual(by_label["primary_ip"]["url"], "https://45.91.54.152/")
        self.assertEqual(by_label["primary_ip"]["status_key"], "primary_ip_status")
        # exit-домены присутствуют (в т.ч. disabled — это research-проба, не статус ноды)
        self.assertIn("fin.goida.fun", labels)
        self.assertIn("swe.goida.fun", labels)


class BlocklistTest(unittest.TestCase):
    def test_config_level_legacy_rejected(self):
        inv = base_inventory()
        inv["servers"]["ru"]["ip"] = "83.147.255.98"
        with self.assertRaises(cc.ClusterConfigError):
            cc.parse_cluster(json.dumps(inv).encode())

    def test_hardcoded_baseline_blocklist_even_if_config_empty(self):
        inv = base_inventory()
        inv["legacy_blocklist"] = []
        inv["servers"]["ru4"]["ssh_host"] = "83.147.255.98"
        with self.assertRaises(cc.ClusterConfigError):
            cc.parse_cluster(json.dumps(inv).encode())

    def test_extra_blocklist_param(self):
        with self.assertRaises(cc.ClusterConfigError):
            cc.parse_cluster(json.dumps(base_inventory()).encode(),
                             extra_blocklist=["77.110.108.57"])


class SignatureTest(unittest.TestCase):
    def test_sign_verify_roundtrip(self):
        inv = base_inventory()
        inv["sig"] = cc.sign_payload(inv, "s3cret")
        self.assertTrue(cc.verify_payload(inv, "s3cret"))
        cfg = cc.parse_cluster(json.dumps(inv).encode(), hmac_secret="s3cret")
        self.assertEqual(cfg.version, 7)

    def test_tampered_payload_rejected(self):
        inv = base_inventory()
        inv["sig"] = cc.sign_payload(inv, "s3cret")
        inv["servers"]["ru"]["ip"] = "45.91.54.200"  # подмена после подписи
        with self.assertRaises(cc.ClusterConfigError):
            cc.parse_cluster(json.dumps(inv).encode(), hmac_secret="s3cret")

    def test_missing_sig_rejected_when_secret_required(self):
        with self.assertRaises(cc.ClusterConfigError):
            cc.parse_cluster(json.dumps(base_inventory()).encode(), hmac_secret="s3cret")


class SchemaValidationTest(unittest.TestCase):
    def test_no_servers(self):
        with self.assertRaises(cc.ClusterConfigError):
            cc.parse_cluster(b'{"servers": {}}')

    def test_server_missing_ip(self):
        with self.assertRaises(cc.ClusterConfigError):
            cc.parse_cluster(b'{"servers": {"x": {"role": "exit"}}}')

    def test_invalid_json(self):
        with self.assertRaises(cc.ClusterConfigError):
            cc.parse_cluster(b"not json{")


class LoadCacheFallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cache = self.d / "cache" / "cluster.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_plain_load_writes_cache(self):
        plain = self.d / "cluster.json"
        plain.write_text(json.dumps(base_inventory()))
        cfg = cc.load_cluster(plain_path=plain, cache_path=self.cache)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.primary_cf_ip(), "45.91.54.152")
        self.assertTrue(self.cache.exists())

    def test_falls_back_to_cache_on_corrupt_live(self):
        # сначала наполняем кэш валидным
        plain = self.d / "cluster.json"
        plain.write_text(json.dumps(base_inventory()))
        cc.load_cluster(plain_path=plain, cache_path=self.cache)
        # теперь live битый → должен прийти кэш
        plain.write_text("garbage{")
        calls = []
        cfg = cc.load_cluster(plain_path=plain, cache_path=self.cache,
                              on_fallback=lambda m: calls.append(m))
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.primary_cf_ip(), "45.91.54.152")
        self.assertEqual(len(calls), 1)

    def test_returns_none_when_nothing_available(self):
        calls = []
        cfg = cc.load_cluster(plain_path=self.d / "missing.json",
                              cache_path=self.d / "no-cache.json",
                              on_fallback=lambda m: calls.append(m))
        self.assertIsNone(cfg)
        self.assertEqual(len(calls), 1)

    def test_corrupt_live_and_no_cache_returns_none(self):
        plain = self.d / "cluster.json"
        plain.write_text("garbage{")
        cfg = cc.load_cluster(plain_path=plain, cache_path=self.d / "no-cache.json")
        self.assertIsNone(cfg)


class CanonicalInventoryTest(unittest.TestCase):
    """боевой .claude/cluster/cluster.json (если присутствует) валиден и без legacy."""

    def test_repo_inventory_parses(self):
        path = ROOT / ".claude" / "cluster" / "cluster.json"
        if not path.exists():
            self.skipTest("no canonical cluster.json in this checkout")
        cfg = cc.parse_cluster(path.read_bytes())
        self.assertTrue(cfg.primary_cf_ip())
        self.assertIn("83.147.255.98", cfg.legacy_blocklist)


if __name__ == "__main__":
    unittest.main()
