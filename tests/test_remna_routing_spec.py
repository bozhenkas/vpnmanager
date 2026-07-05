import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(path, name):
    import sys
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module  # must precede exec_module for Python 3.14 dataclass forward-ref resolution
    spec.loader.exec_module(module)
    return module


class RemnaRoutingSpecTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec_mod = load_module("scripts/remna_routing_spec.py", "remna_routing_spec")

    def rules_by_tag(self, rules):
        return {rule.get("ruleTag"): rule for rule in rules if rule.get("ruleTag")}

    def test_default_p15_reserve_fin_only(self):
        spec = self.spec_mod.RoutingSpec(direct_zapret_outbound="DIRECT_ZAPRET")
        rules = self.spec_mod.build_rules(
            {"home": ["sberbank.ru", "10.10.0.0/16"], "direct": [], "foreign": ["www.happ.su"]},
            spec,
        )
        tagged = self.rules_by_tag(rules)

        home = tagged["manual-home"]
        self.assertEqual(home["outboundTag"], "REMNA_HOME")
        self.assertIn("RU_WS_SMART", home["inboundTag"])
        self.assertNotIn("RU_REALITY_GRPC_RESERVE", home["inboundTag"])
        self.assertIn("RU_WS_FIN", home["inboundTag"])
        self.assertIn("RU_WS_DIRECT", home["inboundTag"])

        telegram = tagged["proxy-telegram-domain-foreign-smart"]
        self.assertEqual(telegram["balancerTag"], "BALANCER_FOREIGN_SMART")
        self.assertIn("geosite:telegram", telegram["domain"])
        self.assertNotIn("RU_REALITY_GRPC_RESERVE", telegram["inboundTag"])

        zapret = tagged["direct-zapret-services-domain"]
        self.assertEqual(zapret["outboundTag"], "DIRECT_ZAPRET")
        self.assertNotIn("RU_REALITY_GRPC_RESERVE", zapret["inboundTag"])
        self.assertIn("geosite:youtube", zapret["domain"])
        self.assertIn("geosite:discord", zapret["domain"])
        self.assertNotIn("geosite:telegram", zapret["domain"])

        direct_zapret_catch = tagged["direct-catch-all"]
        self.assertEqual(direct_zapret_catch["outboundTag"], "DIRECT_ZAPRET")
        self.assertEqual(direct_zapret_catch["inboundTag"], ["RU_WS_DIRECT"])

        reserve_catch = tagged["reserve-fin-catch-all"]
        self.assertEqual(reserve_catch["outboundTag"], "REMNA_FI")
        self.assertEqual(reserve_catch["inboundTag"], ["RU_REALITY_GRPC_RESERVE"])

        smart_catch = tagged["foreign-smart-catch-all"]
        self.assertEqual(smart_catch["balancerTag"], "BALANCER_FOREIGN_SMART")
        self.assertNotIn("RU_REALITY_GRPC_RESERVE", smart_catch["inboundTag"])
        self.assertEqual(smart_catch["inboundTag"], ["RU_WS_SMART", "GOIDA_SMART2"])

    def test_legacy_smart_includes_reserve(self):
        spec = self.spec_mod.RoutingSpec(reserve_fin_only=False)
        rules = self.spec_mod.build_rules({"home": [], "direct": [], "foreign": []}, spec)
        tagged = self.rules_by_tag(rules)
        smart_catch = tagged["foreign-smart-catch-all"]
        self.assertIn("RU_REALITY_GRPC_RESERVE", smart_catch["inboundTag"])
        self.assertNotIn("reserve-fin-catch-all", tagged)

    def test_ru_safety_rules_precede_catchalls(self):
        rules = self.spec_mod.build_rules({"home": [], "direct": [], "foreign": []})
        tagged = self.rules_by_tag(rules)
        safety_ip_idx = rules.index(tagged["direct-goida-cluster-ip"])
        direct_catch_idx = rules.index(tagged["direct-catch-all"])
        reserve_catch_idx = rules.index(tagged["reserve-fin-catch-all"])
        smart_catch_idx = rules.index(tagged["foreign-smart-catch-all"])

        self.assertLess(safety_ip_idx, direct_catch_idx)
        self.assertLess(safety_ip_idx, reserve_catch_idx)
        self.assertLess(reserve_catch_idx, smart_catch_idx)
        self.assertIn("45.91.54.152/32", tagged["direct-goida-cluster-ip"]["ip"])
        self.assertIn("83.147.255.0/24", tagged["direct-goida-cluster-ip"]["ip"])
        self.assertIn("RU_REALITY_GRPC_RESERVE", tagged["direct-goida-cluster-ip"]["inboundTag"])
        self.assertIn("domain:reserve.goida.fun", tagged["direct-goida-cluster-domain"]["domain"])


class RemnaRoutingApplyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.apply_mod = load_module("scripts/apply_remna_routing_spec.py", "apply_remna_routing_spec")

    def test_preserves_hydra_rules(self):
        profile = {
            "routing": {
                "balancers": [
                    {"tag": "BALANCER_FOREIGN_SMART", "selector": ["REMNA_FI"], "fallbackTag": "REMNA_SWE"},
                    {"tag": "BALANCER_HYDRA_DE", "selector": ["HYDRA_DE"], "fallbackTag": "HYDRA_DE"},
                ],
                "rules": [
                    {"type": "field", "ruleTag": "old", "inboundTag": ["RU_WS_SMART"], "balancerTag": "BALANCER_FOREIGN_SMART"},
                    {"type": "field", "inboundTag": ["RU_WS_HYDRA_DE"], "balancerTag": "BALANCER_HYDRA_DE"},
                ]
            }
        }
        updated = self.apply_mod.build_updated_profile(
            profile,
            {"home": [], "direct": [], "foreign": []},
            self.apply_mod.RoutingSpec(),
        )
        self.assertEqual(updated["routing"]["rules"][-1]["balancerTag"], "BALANCER_HYDRA_DE")
        self.assertTrue(any(rule.get("ruleTag") == "foreign-smart-catch-all" for rule in updated["routing"]["rules"]))
        self.assertTrue(any(rule.get("ruleTag") == "reserve-fin-catch-all" for rule in updated["routing"]["rules"]))
        self.assertEqual(updated["routing"]["balancers"][0]["selector"], ["REMNA_FI", "REMNA_FRA"])
        self.assertEqual(updated["routing"]["balancers"][0]["fallbackTag"], "REMNA_SWE")
        self.assertEqual(updated["routing"]["balancers"][1]["tag"], "BALANCER_HYDRA_DE")


if __name__ == "__main__":
    unittest.main()
