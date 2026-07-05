import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_manual_routes():
    spec = importlib.util.spec_from_file_location("manual_routes", ROOT / "bot" / "manual_routes.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RemnawaveManualRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_manual_routes()

    def rules_by_tag(self, rules):
        return {rule.get("ruleTag"): rule for rule in rules if rule.get("ruleTag")}

    def test_reserve_fin_only_not_in_smart_balancer(self):
        rules = self.mod._build_remna_rules({"home": [], "direct": [], "foreign": []})
        tagged = self.rules_by_tag(rules)

        safety = tagged["direct-goida-cluster-ip"]
        self.assertEqual(safety["outboundTag"], "DIRECT")
        self.assertIn("RU_WS_SMART", safety["inboundTag"])
        self.assertIn("RU_REALITY_GRPC_RESERVE", safety["inboundTag"])
        self.assertIn("45.91.54.152/32", safety["ip"])
        self.assertIn("83.147.255.0/24", safety["ip"])
        self.assertIn("194.117.80.94/32", safety["ip"])

        domains = tagged["direct-goida-cluster-domain"]
        self.assertEqual(domains["outboundTag"], "DIRECT")
        self.assertIn("RU_REALITY_GRPC_RESERVE", domains["inboundTag"])
        self.assertIn("domain:ru.goida.fun", domains["domain"])
        self.assertIn("domain:reserve.goida.fun", domains["domain"])

        reserve_catch = tagged["reserve-fin-catch-all"]
        self.assertEqual(reserve_catch["outboundTag"], "REMNA_FI")
        self.assertEqual(reserve_catch["inboundTag"], ["RU_REALITY_GRPC_RESERVE"])

        smart_catch = tagged["foreign-smart-catch-all"]
        self.assertEqual(smart_catch["balancerTag"], "BALANCER_FOREIGN_SMART")
        self.assertNotIn("RU_REALITY_GRPC_RESERVE", smart_catch["inboundTag"])
        self.assertEqual(smart_catch["inboundTag"], ["RU_WS_SMART"])

    def test_home_rules_use_current_remna_home_outbound(self):
        rules = self.mod._build_remna_rules({"home": ["sberbank.ru", "78.107.88.21"], "direct": [], "foreign": []})
        tagged = self.rules_by_tag(rules)

        self.assertEqual(tagged["manual-home"]["outboundTag"], "REMNA_HOME")
        self.assertEqual(tagged["manual-home-ip"]["outboundTag"], "REMNA_HOME")
        self.assertIn("RU_WS_FIN", tagged["manual-home"]["inboundTag"])
        self.assertIn("RU_WS_DIRECT", tagged["manual-home"]["inboundTag"])
        self.assertNotIn("RU_REALITY_GRPC_RESERVE", tagged["manual-home"]["inboundTag"])


if __name__ == "__main__":
    unittest.main()
