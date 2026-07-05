import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "update_lekanta_routing",
    os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "scripts",
        "update_lekanta_routing.py",
    ),
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def payload(version="test-r1"):
    return {
        "_version": version,
        "xray_routing": {
            "domainMatcher": "hybrid",
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"},
                {"type": "field", "network": "tcp,udp", "outboundTag": "proxy"},
            ],
        },
        "stats": {},
    }


class RoutingUpdaterTest(unittest.TestCase):
    def test_validate_rejects_missing_proxy_fallback(self):
        data = payload()
        data["xray_routing"]["rules"][-1]["outboundTag"] = "direct"
        with self.assertRaises(ValueError):
            MODULE.validate(data)

    def test_main_preserves_previous_file_on_fetch_error(self):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "routing.json")
            previous = payload("previous")
            with open(output, "w", encoding="utf-8") as stream:
                json.dump(previous, stream)
            with (
                mock.patch.object(MODULE, "OUTPUT", output),
                mock.patch.object(MODULE, "fetch", side_effect=OSError("offline")),
            ):
                self.assertEqual(MODULE.main(), 0)
            with open(output, encoding="utf-8") as stream:
                self.assertEqual(json.load(stream), previous)

    def test_main_atomically_installs_valid_update(self):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "routing.json")
            with open(output, "w", encoding="utf-8") as stream:
                json.dump(payload("old"), stream)
            updated = payload("new")
            with (
                mock.patch.object(MODULE, "OUTPUT", output),
                mock.patch.object(MODULE, "fetch", return_value=updated),
            ):
                self.assertEqual(MODULE.main(), 0)
            with open(output, encoding="utf-8") as stream:
                self.assertEqual(json.load(stream), updated)


if __name__ == "__main__":
    unittest.main()
