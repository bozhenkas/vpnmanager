"""тесты goida-intermesh-switch (actuator переключения ступеней).

проверяем чистую логику без прода: SWITCH_APPLY=0 => без systemctl.
"""
import importlib
import json
import os
import sys
from pathlib import Path


def _load_module(tmp_path: Path):
    """импортируем switch.py с конфигом в tmp и отключённым apply."""
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "links.json").write_text(json.dumps({
        "_comment": "test config",
        "fin": {
            "listen": 18001,
            "inbound": "in-fin",
            "outbounds": {
                "xhttp-reality": "out-fin-xhttp-reality",
                "hy2-fallback": "out-fin-hy2-fallback",
            },
            "probe": {
                "xhttp-reality": "77.110.108.57:443",
                "hy2-fallback": "127.0.0.1:17455",
            },
        },
        "swe": {
            "listen": 18002,
            "inbound": "in-swe",
            "outbounds": {
                "xhttp-reality": "out-swe-xhttp-reality",
                "hy2-fallback": "out-swe-hy2-fallback",
            },
        },
        "fra": {
            "listen": 18003,
            "inbound": "in-fra",
            "default_stage": "epilepsy",
            "outbounds": {
                "epilepsy": "out-fra-epilepsy",
                "hy2-fallback": "out-fra-hy2-fallback",
            },
            "probe": {
                "epilepsy": "127.0.0.1:17452",
                "hy2-fallback": "127.0.0.1:17454",
            },
        },
    }))
    (conf / "token").write_text("test-token\n")
    os.environ.update({
        "SWITCH_CONF_DIR": str(conf),
        "SWITCH_LINKS": str(conf / "links.json"),
        "SWITCH_STATE": str(conf / "state.json"),
        "SWITCH_TOKEN_FILE": str(conf / "token"),
        "SWITCH_APPLY": "0",
        "SWITCH_BACKEND": "xray",
        "SWITCH_XRAY_ROUTING_FILE": str(conf / "routing.generated.json"),
        "SWITCH_PROBE_TIMEOUT": "0.2",
    })
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "intermesh"))
    mod = importlib.import_module("switch")
    return importlib.reload(mod)


def test_links_and_token(tmp_path):
    m = _load_module(tmp_path)
    links = m.load_links()
    assert set(links) == {"fin", "swe", "fra"}
    assert m._read_token() == "test-token"


def test_stage_aliases_are_normalized(tmp_path):
    m = _load_module(tmp_path)
    assert m.canonical_stage("primary") == "xhttp-reality"
    assert m.canonical_stage("epilepsy") == "epilepsy"
    assert m.canonical_stage("postgres") == "epilepsy"
    assert m.canonical_stage("fallback") == "hy2-fallback"
    assert m.canonical_stage("hysteria2-salamander") == "hy2-fallback"


def test_render_xray_routing_per_link(tmp_path):
    m = _load_module(tmp_path)
    links = m.load_links()
    states = m.normalize_state({
        "links": {
            "fin": {"mode": "active", "stage": "hy2-fallback"},
            "swe": {"mode": "active", "stage": "xhttp-reality"},
            "fra": {"mode": "active", "stage": "epilepsy"},
        },
    }, links)

    routing = m.render_xray_routing(links, states)
    assert routing["routing"]["rules"] == [
        {"type": "field", "inboundTag": ["in-fin"], "outboundTag": "out-fin-hy2-fallback"},
        {"type": "field", "inboundTag": ["in-fra"], "outboundTag": "out-fra-epilepsy"},
        {"type": "field", "inboundTag": ["in-swe"], "outboundTag": "out-swe-xhttp-reality"},
    ]


def test_disabled_routes_to_block(tmp_path):
    m = _load_module(tmp_path)
    links = m.load_links()
    states = m.normalize_state({
        "links": {
            "fin": {"mode": "disabled", "stage": "hy2-fallback"},
            "fra": {"mode": "disabled", "stage": "epilepsy"},
            "swe": {"mode": "active", "stage": "xhttp-reality"},
        },
    }, links)

    routing = m.render_xray_routing(links, states)
    assert routing["routing"]["rules"][0]["outboundTag"] == "block"
    assert routing["routing"]["rules"][1]["outboundTag"] == "block"
    assert routing["routing"]["rules"][2]["outboundTag"] == "out-swe-xhttp-reality"


def test_desired_from_body_supports_down_and_repair(tmp_path):
    m = _load_module(tmp_path)
    links = m.load_links()
    current = {"mode": "active", "stage": "xhttp-reality", "updated_at": 0}

    down = m.desired_from_body({"stage": "down"}, current, links["fin"])
    assert down["mode"] == "disabled"
    assert down["stage"] == "xhttp-reality"

    repair = m.desired_from_body({"mode": "repair", "stage": "hy2"}, current, links["fin"])
    assert repair["mode"] == "repair"
    assert repair["stage"] == "hy2-fallback"


def test_apply_switch_noop_updates_state_for_xray(tmp_path):
    m = _load_module(tmp_path)
    res = m.apply_desired("fin", {
        "mode": "active",
        "stage": "hy2-fallback",
        "updated_at": 123,
    })
    assert res["backend"] == "xray"
    assert res["outbound"] == "out-fin-hy2-fallback"
    assert res["routing_file"].endswith("routing.generated.json")
    assert m.load_state()["links"]["fin"]["stage"] == "hy2-fallback"


def test_fra_can_switch_between_epilepsy_and_hy2(tmp_path):
    m = _load_module(tmp_path)
    res = m.apply_desired("fra", {
        "mode": "active",
        "stage": "epilepsy",
        "updated_at": 123,
    })
    assert res["outbound"] == "out-fra-epilepsy"

    res = m.apply_desired("fra", {
        "mode": "active",
        "stage": "hy2-fallback",
        "updated_at": 124,
    })
    assert res["outbound"] == "out-fra-hy2-fallback"
    assert m.load_state()["links"]["fra"]["stage"] == "hy2-fallback"


def test_remnawave_backend_rewrites_only_target(tmp_path):
    m = _load_module(tmp_path)
    profile = {
        "outbounds": [
            {
                "tag": "REMNA_FI",
                "protocol": "vless",
                "settings": {
                    "vnext": [{
                        "address": "77.110.108.57",
                        "port": 443,
                        "users": [{"id": "relay", "encryption": "none"}],
                    }],
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "reality",
                    "realitySettings": {"serverName": "www.suomi.fi"},
                },
            },
        ],
    }

    changed = m.rewrite_remnawave_profile(
        profile,
        "fin",
        m.load_links()["fin"],
        {"mode": "active", "stage": "hy2-fallback", "updated_at": 1},
    )

    outbound = profile["outbounds"][0]
    assert changed == {"outbound": "REMNA_FI", "target": "127.0.0.1:17455"}
    assert outbound["settings"]["vnext"][0]["address"] == "127.0.0.1"
    assert outbound["settings"]["vnext"][0]["port"] == 17455
    assert outbound["streamSettings"]["network"] == "xhttp"
    assert outbound["streamSettings"]["realitySettings"]["serverName"] == "www.suomi.fi"


def test_remnawave_backend_supports_fra_epilepsy(tmp_path):
    m = _load_module(tmp_path)
    profile = {
        "outbounds": [{
            "tag": "REMNA_FRA",
            "settings": {"vnext": [{"address": "127.0.0.1", "port": 17454}]},
            "streamSettings": {"network": "tcp", "security": "reality"},
        }],
    }

    changed = m.rewrite_remnawave_profile(
        profile,
        "fra",
        m.load_links()["fra"],
        {"mode": "active", "stage": "epilepsy", "updated_at": 1},
    )

    assert changed == {"outbound": "REMNA_FRA", "target": "127.0.0.1:17452"}
    assert profile["outbounds"][0]["settings"]["vnext"][0]["port"] == 17452


def test_old_flat_state_still_roundtrips(tmp_path):
    m = _load_module(tmp_path)
    m.save_state({"fin": "fallback", "_fin_ts": 123})
    links = m.load_links()
    st = m.normalize_state(m.load_state(), links)
    assert st["fin"]["stage"] == "hy2-fallback"
    assert m.state_of("fin") == "hy2-fallback"
    assert m.state_of("swe") == "xhttp-reality"
    assert m.state_of("fra") == "epilepsy"
