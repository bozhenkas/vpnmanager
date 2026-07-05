import importlib.util
import unittest
from pathlib import Path


def load_inspector():
    path = Path(__file__).resolve().parents[1] / "scripts" / "hwid_inspector.py"
    spec = importlib.util.spec_from_file_location("hwid_inspector_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HwidInspectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inspector = load_inspector()

    def test_happ_version_is_not_hwid(self):
        self.assertEqual(self.inspector.expected_hwid("Happ/4.10.2/ios"), "")
        self.assertEqual(
            self.inspector.expected_hwid("Happ/4.10.2/ios/device-123"),
            "device-123",
        )

    def test_reports_mismatch_shared_and_over_limit(self):
        rows = [
            {
                "username": "a", "hwid": "device-a", "device_model": "",
                "user_agent": "Happ/4.10.2/ios/device-b",
            },
            {
                "username": "a", "hwid": "shared-1", "device_model": "",
                "user_agent": "hwid=shared-1",
            },
            {
                "username": "b", "hwid": "shared-1", "device_model": "",
                "user_agent": "hwid=shared-1",
            },
        ]
        report = self.inspector.inspect(rows, {"a": 1, "b": 2})

        self.assertTrue(any(i["kind"] == "ua_hwid_mismatch" for i in report["issues"]))
        self.assertEqual(report["shared_hwids"][0]["users"], ["a", "b"])
        users = {item["username"]: item for item in report["users"]}
        self.assertTrue(users["a"]["over_limit"])
        self.assertFalse(users["b"]["over_limit"])

    def test_reports_version_and_synthetic_hwid(self):
        rows = [{
            "username": "a", "hwid": "4.10.2", "device_model": "diag-smoke",
            "user_agent": "Happ/4.10.2/ios",
        }]
        kinds = {item["kind"] for item in self.inspector.inspect(rows, {})["issues"]}
        self.assertIn("version_hwid", kinds)
        self.assertIn("synthetic_hwid", kinds)
        self.assertIn("happ_ua_without_hwid", kinds)


if __name__ == "__main__":
    unittest.main()
