#!/usr/bin/env python3
"""
goida-intermesh-analyzer — Ladon-based решатель RU→foreign transport failover.

Ladon здесь используется только как CLI probe, не daemon. Решение о switch не
принимается по одному TCP-open: нужен Ladon signature verdict + transport smoke.
"""

from __future__ import annotations

import json
import logging
import os
import random
import shlex
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

BIND_SWITCH_URL = os.environ.get("ANALYZER_SWITCH_URL", "http://127.0.0.1:9099")
BOT_ACTION_URL = os.environ.get("ANALYZER_BOT_ACTION_URL", "http://127.0.0.1:9090/analyzer")
CONF_DIR = Path(os.environ.get("ANALYZER_CONF_DIR", "/etc/goida-intermesh"))
LINKS_FILE = Path(os.environ.get("ANALYZER_LINKS", str(CONF_DIR / "links.json")))
SIGNATURE_TARGETS_FILE = Path(os.environ.get("ANALYZER_SIGNATURE_TARGETS", str(CONF_DIR / "signature-targets.json")))
STATE_FILE = Path(os.environ.get("ANALYZER_STATE", "/var/lib/goida-intermesh-analyzer/state.json"))
SWITCH_TOKEN_FILE = Path(os.environ.get("ANALYZER_SWITCH_TOKEN_FILE", str(CONF_DIR / "token")))
BOT_TOKEN_FILE = Path(os.environ.get("ANALYZER_BOT_TOKEN_FILE", "/etc/rkn-checker/notify-token"))
SWITCH_TOKEN = os.environ.get("ANALYZER_SWITCH_TOKEN", "")
BOT_TOKEN = os.environ.get("NOTIFY_TOKEN", os.environ.get("ANALYZER_BOT_TOKEN", ""))

LADON_BIN = os.environ.get("ANALYZER_LADON_BIN", "/opt/ladon/ladon")
LADON_EXTRA_ARGS = shlex.split(os.environ.get(
    "ANALYZER_LADON_EXTRA_ARGS",
    "-db /opt/ladon/state/engine.db -config /etc/ladon/config.yaml",
))
HOME_LADON_SSH = os.environ.get("ANALYZER_HOME_SSH", "bozhenkas@78.107.88.21")
HOME_LADON_SSH_PORT = int(os.environ.get("ANALYZER_HOME_SSH_PORT", "1722"))
HOME_LADON_SSH_KEY = os.environ.get("ANALYZER_HOME_SSH_KEY", "/etc/goida-intermesh/ssh/home-ladon")
HOME_LADON_SUDO = os.environ.get("ANALYZER_HOME_LADON_SUDO", "1").lower() in ("1", "true", "yes", "on")

APPLY = os.environ.get("ANALYZER_APPLY", "0").lower() in ("1", "true", "yes", "on")
CONFIRM_ATTEMPTS = int(os.environ.get("ANALYZER_CONFIRM_ATTEMPTS", "3"))
CONFIRM_DELAY = float(os.environ.get("ANALYZER_CONFIRM_DELAY", "25"))
CONFIRM_JITTER = float(os.environ.get("ANALYZER_CONFIRM_JITTER", "5"))
PROBE_TIMEOUT = float(os.environ.get("ANALYZER_PROBE_TIMEOUT", "8"))
RESTORE_PRIMARY = os.environ.get("ANALYZER_RESTORE_PRIMARY", "1").lower() in ("1", "true", "yes", "on")
DISABLE_FAILED_LINKS = os.environ.get("ANALYZER_DISABLE_FAILED_LINKS", "1").lower() in ("1", "true", "yes", "on")

SIGNATURE_FAILURE_CODES = {
    "tcp_timeout",
    "tcp_reset",
    "tcp_refused",
    "tcp_unreachable",
    "tls_handshake_timeout",
    "tls_timeout",
    "tls_reset",
    "tls_garbage",
    "tls13_block",
    "http_cutoff",
    "http_timeout",
    "http_reset",
    "http_451",
}

log = logging.getLogger("intermesh-analyzer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class ProbeResult:
    ok: bool
    verdict: str
    detail: str = ""
    weak: bool = False


@dataclass
class LadonResult:
    vantage: str
    target: str
    domain: str
    ok: bool
    blocked: bool
    code: str
    reason: str = ""
    latency_ms: int = 0
    detail: str = ""


@dataclass
class LinkDecision:
    link: str
    current_stage: str
    desired_stage: str
    desired_mode: str
    primary_ok: bool
    fallback_ok: bool
    signature_bad: bool
    signature_codes: list[str]
    action: str
    decision_reason: str


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def switch_token() -> str:
    return SWITCH_TOKEN.strip() or _read_text_file(SWITCH_TOKEN_FILE)


def bot_token() -> str:
    return BOT_TOKEN.strip() or _read_text_file(BOT_TOKEN_FILE)


def load_links() -> dict[str, dict[str, Any]]:
    data = json.loads(LINKS_FILE.read_text())
    return {
        str(k): v for k, v in data.items()
        if isinstance(v, dict) and not str(k).startswith("_")
    }


def default_signature_targets() -> dict[str, dict[str, Any]]:
    return {
        "ru-primary": {"domain": "ru.goida.fun", "ip": "45.91.54.152", "observer_only": True},
        "ru-backup": {
            "domain": "",
            "ip": "45.91.53.93",
            "observer_only": True,
            "ladon": False,
            "reason": "Ladon CLI v1 cannot probe raw IP with ru.goida.fun SNI",
        },
        "reserve": {"domain": "reserve.goida.fun", "ip": "31.77.169.26", "observer_only": True},
        "fin": {"domain": "fin.goida.fun", "ip": "77.110.108.57", "link": "fin"},
        "swe": {"domain": "swe.goida.fun", "ip": "89.22.230.5", "link": "swe"},
        "fra": {"domain": "fra.goida.fun", "ip": "95.163.152.210", "link": "fra"},
        "home-observer": {
            "domain": "",
            "ip": "78.107.88.21",
            "observer_only": True,
            "ladon": False,
            "reason": "HOME is the remote vantage point, not a switch target",
        },
    }


def load_signature_targets() -> dict[str, dict[str, Any]]:
    if not SIGNATURE_TARGETS_FILE.exists():
        return default_signature_targets()
    data = json.loads(SIGNATURE_TARGETS_FILE.read_text())
    return {
        str(k): v for k, v in data.items()
        if isinstance(v, dict) and not str(k).startswith("_")
    }


def default_stage(linkcfg: dict[str, Any]) -> str:
    return str(linkcfg.get("default_stage") or "xhttp-reality")


def fallback_stage(linkcfg: dict[str, Any]) -> str:
    return str(linkcfg.get("fallback_stage") or "hy2-fallback")


def _split_hostport(value: str) -> tuple[str, int] | None:
    host, sep, port = str(value).rpartition(":")
    if not sep or not host:
        return None
    try:
        return host, int(port)
    except ValueError:
        return None


def tcp_probe(target: str) -> ProbeResult:
    hp = _split_hostport(target)
    if not hp:
        return ProbeResult(False, "NO_PROBE", f"bad target {target!r}", weak=True)
    try:
        with socket.create_connection(hp, timeout=PROBE_TIMEOUT):
            return ProbeResult(True, "TCP_OK", target, weak=True)
    except socket.timeout:
        return ProbeResult(False, "TIMEOUT", target, weak=True)
    except OSError as exc:
        return ProbeResult(False, "DOWN", f"{target}: {exc}", weak=True)


def command_probe(spec: dict[str, Any]) -> ProbeResult:
    cmd = spec.get("cmd")
    if not cmd:
        return ProbeResult(False, "NO_CMD")
    shell = isinstance(cmd, str)
    timeout = float(spec.get("timeout") or PROBE_TIMEOUT)
    try:
        proc = subprocess.run(
            cmd,
            shell=shell,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(False, "TIMEOUT", "command timeout")
    except OSError as exc:
        return ProbeResult(False, "ERROR", str(exc))

    output = (proc.stdout + "\n" + proc.stderr).strip()
    want = str(spec.get("contains") or "")
    if proc.returncode == 0 and (not want or want in output):
        return ProbeResult(True, "OK", output[:500])
    verdict = "BAD_OUTPUT" if proc.returncode == 0 else "EXIT_" + str(proc.returncode)
    return ProbeResult(False, verdict, output[:500])


def probe_stage(link: str, linkcfg: dict[str, Any], stage: str) -> ProbeResult:
    smoke = linkcfg.get("smoke")
    if isinstance(smoke, dict) and isinstance(smoke.get(stage), dict):
        res = command_probe(smoke[stage])
        log.info("%s/%s smoke -> %s", link, stage, res.verdict)
        return res

    probes = linkcfg.get("probe")
    target = probes.get(stage) if isinstance(probes, dict) else None
    res = tcp_probe(str(target or ""))
    log.info("%s/%s tcp -> %s weak=%s", link, stage, res.verdict, res.weak)
    return res


def confirmed_stage(link: str, linkcfg: dict[str, Any], stage: str) -> ProbeResult:
    last = ProbeResult(False, "NO_ATTEMPTS")
    for attempt in range(1, CONFIRM_ATTEMPTS + 1):
        last = probe_stage(link, linkcfg, stage)
        if last.ok:
            return last
        if attempt < CONFIRM_ATTEMPTS:
            delay = CONFIRM_DELAY + (random.uniform(-CONFIRM_JITTER, CONFIRM_JITTER) if CONFIRM_JITTER > 0 else 0)
            delay = max(0.0, delay)
            log.warning("%s/%s bad (%s), retry %d/%d after %.1fs", link, stage, last.verdict, attempt + 1, CONFIRM_ATTEMPTS, delay)
            time.sleep(delay)
    return last


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(stripped[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("ladon output is not a JSON object")
    return data


def _ladon_field(data: dict[str, Any], *names: str) -> Any:
    lower = {str(k).lower(): v for k, v in data.items()}
    for name in names:
        if name in data:
            return data[name]
        value = lower.get(name.lower())
        if value is not None:
            return value
    return None


def normalize_ladon_failure(code: str) -> str:
    value = str(code or "").strip().lower()
    aliases = {
        "timeout": "tcp_timeout",
        "tcp_connect_timeout": "tcp_timeout",
        "tls_block": "tls_handshake_timeout",
        "tls_handshake": "tls_handshake_timeout",
        "connection_reset": "tls_reset",
        "http_stub": "http_451",
    }
    return aliases.get(value, value)


def parse_ladon_json(text: str, vantage: str, target: str, domain: str) -> LadonResult:
    try:
        data = _extract_json_object(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return LadonResult(vantage, target, domain, False, False, "LADON_PARSE_ERROR", str(exc), detail=text[:500])

    raw_code = _ladon_field(data, "FailureCode", "failure_code", "code", "Verdict", "verdict") or ""
    code = normalize_ladon_failure(raw_code)
    reason = str(_ladon_field(data, "FailureReason", "failure_reason", "reason", "Detail", "detail") or "")
    latency = _ladon_field(data, "LatencyMS", "latency_ms", "latency")
    try:
        latency_ms = int(latency or 0)
    except (TypeError, ValueError):
        latency_ms = 0
    tcp_ok = bool(_ladon_field(data, "TCPOK", "tcp_ok"))
    tls_ok = bool(_ladon_field(data, "TLSOK", "tls_ok"))
    http_ok = bool(_ladon_field(data, "HTTPOK", "http_ok"))
    explicit_ok = _ladon_field(data, "OK", "ok")
    ok = bool(explicit_ok) if explicit_ok is not None else (not code and tcp_ok and tls_ok and http_ok)
    if str(raw_code).strip().upper() in {"OK", "PASS"}:
        ok = True
        code = ""
    blocked = bool(code in SIGNATURE_FAILURE_CODES)
    return LadonResult(vantage, target, domain, ok, blocked, code or ("ok" if ok else "unknown"), reason, latency_ms, json.dumps(data, ensure_ascii=False)[:500])


def skipped_ladon(vantage: str, target: str, targetcfg: dict[str, Any]) -> LadonResult:
    return LadonResult(
        vantage=vantage,
        target=target,
        domain=str(targetcfg.get("domain") or ""),
        ok=False,
        blocked=False,
        code="SKIPPED",
        reason=str(targetcfg.get("reason") or "no domain for Ladon probe"),
    )


def _run_command(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def run_ladon_local(target: str, domain: str) -> LadonResult:
    cmd = [LADON_BIN, *LADON_EXTRA_ARGS, "probe", domain]
    try:
        rc, out, err = _run_command(cmd, PROBE_TIMEOUT + 5)
    except subprocess.TimeoutExpired:
        return LadonResult("ru", target, domain, False, True, "tcp_timeout", "ladon local timeout")
    except OSError as exc:
        return LadonResult("ru", target, domain, False, False, "LADON_EXEC_ERROR", str(exc))
    if rc != 0:
        parsed = parse_ladon_json(out or err, "ru", target, domain)
        if parsed.code != "LADON_PARSE_ERROR":
            return parsed
        return LadonResult("ru", target, domain, False, False, "LADON_EXIT_" + str(rc), (err or out).strip()[:500])
    return parse_ladon_json(out, "ru", target, domain)


def run_ladon_home(target: str, domain: str) -> LadonResult:
    remote_parts = [LADON_BIN, *LADON_EXTRA_ARGS, "probe", domain]
    if HOME_LADON_SUDO:
        remote_parts = ["sudo", "-n", *remote_parts]
    remote_cmd = " ".join(shlex.quote(part) for part in remote_parts)
    cmd = [
        "ssh",
        "-i",
        HOME_LADON_SSH_KEY,
        "-p",
        str(HOME_LADON_SSH_PORT),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=accept-new",
        HOME_LADON_SSH,
        remote_cmd,
    ]
    try:
        rc, out, err = _run_command(cmd, PROBE_TIMEOUT + 15)
    except subprocess.TimeoutExpired:
        return LadonResult("home", target, domain, False, False, "SSH_TIMEOUT", "home ladon ssh timeout")
    except OSError as exc:
        return LadonResult("home", target, domain, False, False, "SSH_ERROR", str(exc))
    if rc != 0:
        parsed = parse_ladon_json(out or err, "home", target, domain)
        if parsed.code != "LADON_PARSE_ERROR":
            return parsed
        return LadonResult("home", target, domain, False, False, "SSH_EXIT_" + str(rc), (err or out).strip()[:500])
    return parse_ladon_json(out, "home", target, domain)


def confirmed_ladon(vantage: str, target: str, targetcfg: dict[str, Any]) -> LadonResult:
    if targetcfg.get("ladon", True) is False or not str(targetcfg.get("domain") or "").strip():
        return skipped_ladon(vantage, target, targetcfg)
    domain = str(targetcfg["domain"]).strip()
    runner = run_ladon_home if vantage == "home" else run_ladon_local
    last = LadonResult(vantage, target, domain, False, False, "NO_ATTEMPTS")
    for attempt in range(1, CONFIRM_ATTEMPTS + 1):
        last = runner(target, domain)
        log.info("ladon %s/%s %s -> ok=%s blocked=%s code=%s", vantage, target, domain, last.ok, last.blocked, last.code)
        if last.ok:
            return last
        if not last.blocked:
            return last
        if attempt < CONFIRM_ATTEMPTS:
            delay = CONFIRM_DELAY + (random.uniform(-CONFIRM_JITTER, CONFIRM_JITTER) if CONFIRM_JITTER > 0 else 0)
            delay = max(0.0, delay)
            log.warning("ladon %s/%s bad (%s), retry %d/%d after %.1fs", vantage, target, last.code, attempt + 1, CONFIRM_ATTEMPTS, delay)
            time.sleep(delay)
    return last


def run_vantage_matrix(targets: dict[str, dict[str, Any]]) -> dict[str, dict[str, LadonResult]]:
    matrix: dict[str, dict[str, LadonResult]] = {}
    for target, targetcfg in targets.items():
        matrix[target] = {
            "ru": confirmed_ladon("ru", target, targetcfg),
            "home": confirmed_ladon("home", target, targetcfg),
        }
    return matrix


def signature_target_for_link(link: str, cfg: dict[str, Any]) -> str:
    return str(cfg.get("signature_target") or link)


def signature_codes_for_link(link: str, cfg: dict[str, Any], matrix: dict[str, dict[str, LadonResult]]) -> list[str]:
    target = signature_target_for_link(link, cfg)
    return [
        f"{vantage}:{res.code}"
        for vantage, res in sorted((matrix.get(target) or {}).items())
        if res.blocked
    ]


def _request_json(url: str, method: str = "GET", payload: dict[str, Any] | None = None, token: str = "") -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        header = "X-Goida-Switch-Token" if "9099" in url else "Authorization"
        headers[header] = token if header.startswith("X-") else f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode() or "{}")


def switch_status() -> dict[str, Any]:
    return _request_json(BIND_SWITCH_URL.rstrip("/") + "/status", token=switch_token())


def switch_link(link: str, stage: str, mode: str = "active") -> dict[str, Any]:
    body = {"link": link, "stage": stage, "mode": mode}
    if not APPLY:
        log.warning("dry-run switch %s", body)
        return {"ok": True, "dry_run": True, **body}
    return _request_json(BIND_SWITCH_URL.rstrip("/") + "/switch", method="POST", payload=body, token=switch_token())


def bot_action(action: str, enabled: bool) -> dict[str, Any]:
    body = {"action": action, "enabled": enabled}
    if not APPLY:
        log.warning("dry-run bot action %s", body)
        return {"ok": True, "dry_run": True, **body}
    return _request_json(BOT_ACTION_URL, method="POST", payload=body, token=bot_token())


def decide_link(
    link: str,
    cfg: dict[str, Any],
    current_stage: str,
    vantage_results: dict[str, dict[str, LadonResult]] | None = None,
    transport_smokes: dict[str, ProbeResult] | None = None,
) -> LinkDecision:
    primary = default_stage(cfg)
    fallback = fallback_stage(cfg)
    transport_smokes = transport_smokes or {
        primary: confirmed_stage(link, cfg, primary),
        fallback: confirmed_stage(link, cfg, fallback),
    }
    primary_res = transport_smokes[primary]
    fallback_res = transport_smokes[fallback]
    primary_strong_ok = primary_res.ok and not primary_res.weak
    fallback_strong_ok = fallback_res.ok and not fallback_res.weak
    codes = signature_codes_for_link(link, cfg, vantage_results or {})
    signature_bad = bool(codes)

    desired_stage = current_stage
    desired_mode = "active"
    action = "noop"
    reason = "primary smoke ok"

    if primary_strong_ok:
        desired_stage = primary
        if current_stage != primary and RESTORE_PRIMARY:
            action = "restore-primary"
            reason = "primary smoke recovered"
        elif current_stage != primary:
            action = "primary-ok-hold"
            reason = "primary smoke ok but auto restore disabled"
    elif primary_res.ok:
        desired_stage = current_stage
        action = "noop"
        reason = "primary probe ok but weak; no switch decision without transport smoke"
    elif fallback_strong_ok and signature_bad:
        desired_stage = fallback
        if current_stage != fallback:
            action = "fallback"
            reason = "primary Ladon signature bad and primary smoke failed; fallback smoke ok"
        else:
            reason = "already on fallback; primary Ladon signature bad and primary smoke failed"
    elif fallback_res.ok and signature_bad:
        desired_stage = current_stage
        action = "noop"
        reason = "primary Ladon signature bad and fallback probe ok but weak; no switch without transport smoke"
    elif fallback_res.ok:
        desired_stage = current_stage
        action = "noop"
        reason = "primary smoke failed but no Ladon blocking signature; hold current stage"
    else:
        desired_stage = fallback
        desired_mode = "disabled" if DISABLE_FAILED_LINKS else "repair"
        action = desired_mode
        reason = "primary and fallback smokes failed after confirmation"
        if signature_bad:
            reason += "; Ladon signature also bad"

    return LinkDecision(
        link=link,
        current_stage=current_stage,
        desired_stage=desired_stage,
        desired_mode=desired_mode,
        primary_ok=primary_res.ok,
        fallback_ok=fallback_res.ok,
        signature_bad=signature_bad,
        signature_codes=codes,
        action=action,
        decision_reason=reason,
    )


def apply_decision(decision: LinkDecision) -> None:
    if decision.action in ("noop", "primary-ok-hold"):
        return
    switch_link(decision.link, decision.desired_stage, decision.desired_mode)


def serialize_matrix(matrix: dict[str, dict[str, LadonResult]]) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        target: {vantage: asdict(result) for vantage, result in vantage_map.items()}
        for target, vantage_map in matrix.items()
    }


def serialize_smokes(smokes: dict[str, dict[str, ProbeResult]]) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        link: {stage: asdict(result) for stage, result in stage_map.items()}
        for link, stage_map in smokes.items()
    }


def write_state(
    decisions: list[LinkDecision],
    all_foreign_down: bool,
    vantage_results: dict[str, dict[str, LadonResult]] | None = None,
    transport_smokes: dict[str, dict[str, ProbeResult]] | None = None,
) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": int(time.time()),
        "all_foreign_down": all_foreign_down,
        "vantage_results": serialize_matrix(vantage_results or {}),
        "transport_smokes": serialize_smokes(transport_smokes or {}),
        "decisions": [asdict(d) for d in decisions],
    }
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    tmp.replace(STATE_FILE)


def main() -> int:
    links = load_links()
    targets = load_signature_targets()
    try:
        status = switch_status()
        current = status.get("links") or {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.error("switch status unavailable: %s", exc)
        return 2

    vantage_results = run_vantage_matrix(targets)
    transport_smokes: dict[str, dict[str, ProbeResult]] = {}
    decisions = []
    for link, cfg in links.items():
        primary = default_stage(cfg)
        fallback = fallback_stage(cfg)
        transport_smokes[link] = {
            primary: confirmed_stage(link, cfg, primary),
            fallback: confirmed_stage(link, cfg, fallback),
        }
        current_stage = str((current.get(link) or {}).get("stage") or primary)
        decision = decide_link(link, cfg, current_stage, vantage_results, transport_smokes[link])
        decisions.append(decision)
        log.info(
            "%s current=%s primary_ok=%s fallback_ok=%s signature_bad=%s action=%s desired=%s/%s reason=%s",
            link, current_stage, decision.primary_ok, decision.fallback_ok, decision.signature_bad,
            decision.action, decision.desired_mode, decision.desired_stage, decision.decision_reason,
        )
        apply_decision(decision)

    all_foreign_down = bool(decisions) and all(not d.primary_ok and not d.fallback_ok for d in decisions)
    bot_action("foreign_exits_down", all_foreign_down)
    write_state(decisions, all_foreign_down, vantage_results, transport_smokes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
