import importlib
import json
import os
import sys
from pathlib import Path


def _load_module(tmp_path: Path):
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "links.json").write_text(json.dumps({
        "fin": {
            "default_stage": "xhttp-reality",
            "fallback_stage": "hy2-fallback",
            "signature_target": "fin",
            "probe": {
                "xhttp-reality": "77.110.108.57:443",
                "hy2-fallback": "127.0.0.1:17455",
            },
        },
        "fra": {
            "default_stage": "epilepsy",
            "fallback_stage": "hy2-fallback",
            "signature_target": "fra",
            "probe": {
                "epilepsy": "127.0.0.1:17452",
                "hy2-fallback": "127.0.0.1:17454",
            },
        },
    }))
    (conf / "signature-targets.json").write_text(json.dumps({
        "fin": {"domain": "fin.goida.fun", "ip": "77.110.108.57", "link": "fin"},
        "fra": {"domain": "fra.goida.fun", "ip": "95.163.152.210", "link": "fra"},
        "ru-backup": {"ip": "45.91.53.93", "ladon": False},
    }))
    (conf / "token").write_text("switch-token\n")
    os.environ.update({
        "ANALYZER_CONF_DIR": str(conf),
        "ANALYZER_LINKS": str(conf / "links.json"),
        "ANALYZER_SIGNATURE_TARGETS": str(conf / "signature-targets.json"),
        "ANALYZER_SWITCH_TOKEN_FILE": str(conf / "token"),
        "ANALYZER_STATE": str(tmp_path / "state.json"),
        "ANALYZER_CONFIRM_ATTEMPTS": "3",
        "ANALYZER_CONFIRM_DELAY": "0",
        "ANALYZER_CONFIRM_JITTER": "0",
        "ANALYZER_APPLY": "0",
    })
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "intermesh"))
    mod = importlib.import_module("analyzer")
    return importlib.reload(mod)


def _blocked_matrix(m, target="fin", code="tcp_timeout"):
    return {
        target: {
            "ru": m.LadonResult("ru", target, f"{target}.goida.fun", False, True, code),
            "home": m.LadonResult("home", target, f"{target}.goida.fun", True, False, "ok"),
        }
    }


def _ok_matrix(m, target="fin"):
    return {
        target: {
            "ru": m.LadonResult("ru", target, f"{target}.goida.fun", True, False, "ok"),
            "home": m.LadonResult("home", target, f"{target}.goida.fun", True, False, "ok"),
        }
    }


def test_ladon_parser_ok(tmp_path):
    m = _load_module(tmp_path)
    result = m.parse_ladon_json(
        '{"Domain":"fin.goida.fun","TCPOK":true,"TLSOK":true,"HTTPOK":true,"LatencyMS":42}',
        "ru",
        "fin",
        "fin.goida.fun",
    )

    assert result.ok is True
    assert result.blocked is False
    assert result.code == "ok"
    assert result.latency_ms == 42


def test_ladon_parser_normalizes_block_codes(tmp_path):
    m = _load_module(tmp_path)
    for code in ("tcp_timeout", "tls_handshake_timeout", "tls_reset", "tls_garbage", "tls13_block", "http_cutoff", "http_451"):
        result = m.parse_ladon_json(json.dumps({"FailureCode": code, "FailureReason": "blocked"}), "home", "fin", "fin.goida.fun")
        assert result.ok is False
        assert result.blocked is True
        assert result.code == code


def test_ladon_parser_malformed_json(tmp_path):
    m = _load_module(tmp_path)
    result = m.parse_ladon_json("not-json", "ru", "fin", "fin.goida.fun")

    assert result.ok is False
    assert result.blocked is False
    assert result.code == "LADON_PARSE_ERROR"


def test_home_ladon_ssh_failure_is_not_signature(tmp_path, monkeypatch):
    m = _load_module(tmp_path)

    def fail(_cmd, _timeout):
        raise OSError("ssh unavailable")

    monkeypatch.setattr(m, "_run_command", fail)
    result = m.run_ladon_home("fin", "fin.goida.fun")

    assert result.ok is False
    assert result.blocked is False
    assert result.code == "SSH_ERROR"


def test_decide_switches_to_hy2_only_with_ladon_signature(tmp_path, monkeypatch):
    m = _load_module(tmp_path)
    calls = []

    def fake_probe(link, cfg, stage):
        calls.append((link, stage))
        return m.ProbeResult(stage == "hy2-fallback", "OK" if stage == "hy2-fallback" else "TIMEOUT")

    monkeypatch.setattr(m, "probe_stage", fake_probe)
    decision = m.decide_link("fin", m.load_links()["fin"], "xhttp-reality", _blocked_matrix(m))

    assert decision.action == "fallback"
    assert decision.desired_stage == "hy2-fallback"
    assert decision.desired_mode == "active"
    assert decision.signature_bad is True
    assert "ru:tcp_timeout" in decision.signature_codes
    assert ("fin", "xhttp-reality") in calls
    assert ("fin", "hy2-fallback") in calls


def test_decide_holds_when_primary_fails_without_ladon_signature(tmp_path, monkeypatch):
    m = _load_module(tmp_path)

    def fake_probe(link, cfg, stage):
        return m.ProbeResult(stage == "hy2-fallback", "OK" if stage == "hy2-fallback" else "TIMEOUT")

    monkeypatch.setattr(m, "probe_stage", fake_probe)
    decision = m.decide_link("fin", m.load_links()["fin"], "xhttp-reality", _ok_matrix(m))

    assert decision.action == "noop"
    assert decision.desired_stage == "xhttp-reality"
    assert decision.signature_bad is False
    assert "no Ladon blocking signature" in decision.decision_reason


def test_decide_does_not_switch_on_weak_fallback_probe(tmp_path, monkeypatch):
    m = _load_module(tmp_path)

    def fake_probe(link, cfg, stage):
        return m.ProbeResult(stage == "hy2-fallback", "OK" if stage == "hy2-fallback" else "TIMEOUT", weak=True)

    monkeypatch.setattr(m, "probe_stage", fake_probe)
    decision = m.decide_link("fin", m.load_links()["fin"], "xhttp-reality", _blocked_matrix(m))

    assert decision.action == "noop"
    assert decision.desired_stage == "xhttp-reality"
    assert "weak" in decision.decision_reason


def test_decide_restores_primary_when_it_recovers(tmp_path, monkeypatch):
    m = _load_module(tmp_path)

    def fake_probe(link, cfg, stage):
        return m.ProbeResult(stage == "xhttp-reality", "OK" if stage == "xhttp-reality" else "TIMEOUT")

    monkeypatch.setattr(m, "probe_stage", fake_probe)
    decision = m.decide_link("fin", m.load_links()["fin"], "hy2-fallback", _blocked_matrix(m))

    assert decision.action == "restore-primary"
    assert decision.desired_stage == "xhttp-reality"


def test_decide_disables_link_when_both_stages_fail(tmp_path, monkeypatch):
    m = _load_module(tmp_path)
    monkeypatch.setattr(m, "probe_stage", lambda link, cfg, stage: m.ProbeResult(False, "TIMEOUT"))

    decision = m.decide_link("fra", m.load_links()["fra"], "epilepsy", _blocked_matrix(m, "fra", "tls13_block"))

    assert decision.action == "disabled"
    assert decision.desired_stage == "hy2-fallback"
    assert decision.desired_mode == "disabled"
    assert decision.signature_bad is True


def test_write_state_records_matrix_smokes_and_reasons(tmp_path):
    m = _load_module(tmp_path)
    decisions = [
        m.LinkDecision(
            "fin",
            "xhttp-reality",
            "hy2-fallback",
            "disabled",
            False,
            False,
            True,
            ["ru:tcp_timeout"],
            "disabled",
            "primary and fallback smokes failed after confirmation; Ladon signature also bad",
        ),
    ]
    smokes = {"fin": {"xhttp-reality": m.ProbeResult(False, "TIMEOUT"), "hy2-fallback": m.ProbeResult(False, "TIMEOUT")}}

    m.write_state(decisions, all_foreign_down=True, vantage_results=_blocked_matrix(m), transport_smokes=smokes)
    data = json.loads(Path(os.environ["ANALYZER_STATE"]).read_text())

    assert data["all_foreign_down"] is True
    assert data["vantage_results"]["fin"]["ru"]["blocked"] is True
    assert data["transport_smokes"]["fin"]["xhttp-reality"]["verdict"] == "TIMEOUT"
    assert data["decisions"][0]["decision_reason"].startswith("primary and fallback")
