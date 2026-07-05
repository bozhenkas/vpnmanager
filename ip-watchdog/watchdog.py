#!/usr/bin/env python3
"""
ip-watchdog — мониторит доступность primary IP с российского ISP.
при блокировке переключает DNS A-запись goida.fun через Cloudflare API.
при восстановлении primary — возвращает обратно (auto-recovery).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sqlite3
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STUB_MARKERS = (
    "доступ к информационному ресурсу ограничен",
    "доступ ограничен",
    "заблокирован",
    "blocked by",
    "unavailable for legal reasons",
    "roskomnadzor",
)

GENERIC_HEADERS = (
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36\r\n"
    "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8\r\n"
    "Accept-Language: en-US,en;q=0.9\r\n"
    "Connection: close\r\n"
)

# ---------------------------------------------------------------------------
# конфиг из env
# ---------------------------------------------------------------------------

PRIMARY_IP      = os.environ.get("PRIMARY_IP", "")
BACKUP_IP       = os.environ.get("BACKUP_IP", "")
DOMAIN          = os.environ.get("DOMAIN", "ru.goida.fun")
CHECK_PORT      = int(os.environ.get("CHECK_PORT", "443"))
BACKUP_HY2_PORT = int(os.environ.get("BACKUP_HY2_PORT", "8443"))
RESERVE_HOST    = os.environ.get("RESERVE_HOST", "reserve.goida.fun")
RESERVE_PORT    = int(os.environ.get("RESERVE_PORT", "443"))
CF_TOKEN        = os.environ.get("CF_TOKEN", "")
CF_ZONE_ID      = os.environ.get("CF_ZONE_ID", "")
NOTIFY_URL      = os.environ.get("NOTIFY_URL", "")
NOTIFY_TOKEN    = os.environ.get("NOTIFY_TOKEN", "")
STATE_FILE      = Path(os.environ.get("STATE_FILE", "/tmp/ip-watchdog.state"))
LOG_DB          = Path(os.environ.get("LOG_DB", "/var/lib/ip-watchdog/watchdog-log.sqlite"))
MANUAL_OVERRIDE_FILE = Path(os.environ.get("MANUAL_OVERRIDE_FILE", str(STATE_FILE) + ".manual"))
HEALTH_STATE_FILE = Path(os.environ.get("HEALTH_STATE_FILE", str(STATE_FILE) + ".health.json"))
FAIL_THRESHOLD  = int(os.environ.get("FAIL_THRESHOLD", "3"))
PROBE_TIMEOUT   = int(os.environ.get("PROBE_TIMEOUT", "8"))
RETRY_DELAY     = int(os.environ.get("RETRY_DELAY", "10"))
AUTO_RECOVERY   = os.environ.get("AUTO_RECOVERY", "1") == "1"
DNS_APPLY       = os.environ.get("DNS_APPLY", "1").lower() in ("1", "true", "yes", "on")
LADON_BIN       = os.environ.get("LADON_BIN", "/opt/ladon/ladon")
LADON_EXTRA_ARGS = shlex.split(os.environ.get("LADON_EXTRA_ARGS", "-db /opt/ladon/state/engine.db -config /etc/ladon/config.yaml"))
LADON_SUDO      = os.environ.get("LADON_SUDO", "1").lower() in ("1", "true", "yes", "on")
YOUTUBE_MEDIA_URL = os.environ.get("YOUTUBE_MEDIA_URL", "")
YOUTUBE_WATCH_URL = os.environ.get("YOUTUBE_WATCH_URL", "https://www.youtube.com/watch?v=M7lc1UVf-VE")

FOREIGN_TARGETS = {
    "fin": {"flag": "🇫🇮", "domain": os.environ.get("FIN_DOMAIN", "fin.goida.fun")},
    "swe": {"flag": "🇸🇪", "domain": os.environ.get("SWE_DOMAIN", "swe.goida.fun")},
    "fra": {"flag": "🇫🇷", "domain": os.environ.get("FRA_DOMAIN", "fra.goida.fun"), "nonblocking_codes": {"tls_eof"}},
    "reserve": {"flag": "🇰🇵", "domain": RESERVE_HOST},
}

LADON_SIGNATURE_FAILURE_CODES = {
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

# ---------------------------------------------------------------------------
# cluster_config — загружаем один раз; env-переменные имеют приоритет
# ---------------------------------------------------------------------------

def _load_cluster_once():
    import importlib.util as _ilu
    from pathlib import Path as _Path
    env_path = os.environ.get("CLUSTER_CONFIG_MODULE", "").strip()
    candidates = [
        _Path(env_path) if env_path else None,
        _Path(__file__).with_name("cluster_config.py"),
        _Path(__file__).resolve().parent.parent / "subscription" / "cluster_config.py",
    ]
    path = next((p for p in candidates if p and p.is_file()), None)
    if not path:
        return None
    try:
        spec = _ilu.spec_from_file_location("cluster_config_wd", path)
        mod = _ilu.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod.load_cluster_from_env()
    except Exception:
        return None

_cfg = _load_cluster_once()
if _cfg:
    PRIMARY_IP = PRIMARY_IP or _cfg.primary_cf_ip()
    BACKUP_IP  = BACKUP_IP  or _cfg.backup_cf_ip()
    DOMAIN     = DOMAIN     or _cfg.primary_domain()

CF_API = "https://api.cloudflare.com/client/v4"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("watchdog")


# форсируем IPv4 для cloudflare/notify; ipv6 у домашнего сервера может быть пустым
_orig_create_connection = socket.create_connection


def _ipv4_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    host, port = address
    for af, *_, sa in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        sock = socket.socket(af, socket.SOCK_STREAM)
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        try:
            sock.connect(sa)
            return sock
        except OSError:
            sock.close()
    raise OSError(f"cannot connect to {host}:{port} over IPv4")


socket.create_connection = _ipv4_create_connection


# ---------------------------------------------------------------------------
# sqlite log / report
# ---------------------------------------------------------------------------

def _log_conn() -> sqlite3.Connection:
    LOG_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(LOG_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchdog_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            target TEXT NOT NULL CHECK(target IN ('primary', 'backup')),
            verdict TEXT NOT NULL,
            latency_ms INTEGER NOT NULL,
            detail TEXT NOT NULL DEFAULT ''
        )
    """)
    return conn


def log_probe_result(target: str, verdict: str, latency_ms: int, detail: str = "") -> None:
    try:
        with _log_conn() as conn:
            conn.execute(
                "INSERT INTO watchdog_log(ts, target, verdict, latency_ms, detail) VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), target, verdict, latency_ms, detail[:500]),
            )
            conn.execute("""
                DELETE FROM watchdog_log
                WHERE id NOT IN (
                    SELECT id FROM watchdog_log ORDER BY id DESC LIMIT 1000
                )
            """)
    except Exception as e:
        log.warning("watchdog log failed: %s", e)


def print_report(hours: int = 24) -> None:
    since = int(time.time()) - hours * 3600
    with _log_conn() as conn:
        rows = conn.execute("""
            SELECT target, verdict, COUNT(*)
            FROM watchdog_log
            WHERE ts >= ?
            GROUP BY target, verdict
            ORDER BY target, verdict
        """, (since,)).fetchall()

    by_target: dict[str, dict[str, int]] = {}
    for target, verdict, count in rows:
        by_target.setdefault(target, {})[verdict] = count

    print(f"watchdog report: last {hours}h")
    for target in ("primary", "backup"):
        verdicts = by_target.get(target, {})
        total = sum(verdicts.values())
        ok = verdicts.get("OK", 0)
        ok_pct = (ok * 100 / total) if total else 0.0
        parts = ", ".join(f"{verdict}={count}" for verdict, count in sorted(verdicts.items()))
        print(f"{target}: total={total} ok={ok_pct:.1f}% {parts}".rstrip())


def require_runtime_env() -> None:
    missing = [name for name, value in {
        "PRIMARY_IP": PRIMARY_IP,
        "BACKUP_IP": BACKUP_IP,
        "CF_TOKEN": CF_TOKEN,
        "CF_ZONE_ID": CF_ZONE_ID,
    }.items() if not value]
    if missing:
        raise SystemExit("missing env: " + ", ".join(missing))


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def _http_verdict(data: bytes) -> str:
    head, _, body = data.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode("latin1", "ignore")
    text = body[:2000].decode("utf-8", "ignore").lower()
    if " 451 " in f" {status} ":
        return "HTTP_STUB"
    if any(marker in text for marker in STUB_MARKERS):
        return "HTTP_STUB"
    return "OK" if status.startswith("HTTP/") else "BAD_HTTP"


def probe(ip: str, target: str | None = None) -> tuple[bool, str]:
    """ip-aware rkn-style probe: tcp, tls+sni, http host/stub"""
    ctx = ssl.create_default_context()
    raw = None
    probe_start = time.monotonic()
    detail = ""
    try:
        start = time.monotonic()
        raw = socket.create_connection((ip, CHECK_PORT), timeout=PROBE_TIMEOUT)
        raw.settimeout(PROBE_TIMEOUT)
        tcp_ms = int((time.monotonic() - start) * 1000)
    except socket.timeout:
        log.warning("probe %s → TIMEOUT tcp=fail", ip)
        if target:
            log_probe_result(target, "TIMEOUT", int((time.monotonic() - probe_start) * 1000), "tcp timeout")
        return False, "TIMEOUT"
    except ConnectionResetError:
        log.warning("probe %s → TCP_RESET", ip)
        if target:
            log_probe_result(target, "TCP_RESET", int((time.monotonic() - probe_start) * 1000), "tcp reset")
        return False, "TCP_RESET"
    except OSError as e:
        log.warning("probe %s → DOWN tcp error: %s", ip, e)
        if target:
            log_probe_result(target, "DOWN", int((time.monotonic() - probe_start) * 1000), str(e))
        return False, "DOWN"

    try:
        start = time.monotonic()
        with ctx.wrap_socket(raw, server_hostname=DOMAIN) as tls:
            tls_ms = int((time.monotonic() - start) * 1000)
            req = (
                f"GET / HTTP/1.1\r\n"
                f"Host: {DOMAIN}\r\n"
                f"{GENERIC_HEADERS}\r\n"
            ).encode()
            tls.sendall(req)
            chunks = []
            while sum(len(chunk) for chunk in chunks) < 4096:
                chunk = tls.recv(1024)
                if not chunk:
                    break
                chunks.append(chunk)
            verdict = _http_verdict(b"".join(chunks))
            detail = f"tcp={tcp_ms}ms tls={tls_ms}ms"
    except socket.timeout:
        log.warning("probe %s → TLS_BLOCK timeout after tcp=%dms", ip, tcp_ms)
        if target:
            log_probe_result(target, "TLS_BLOCK", int((time.monotonic() - probe_start) * 1000), f"timeout after tcp={tcp_ms}ms")
        return False, "TLS_BLOCK"
    except ConnectionResetError:
        log.warning("probe %s → TLS_BLOCK reset after tcp=%dms", ip, tcp_ms)
        if target:
            log_probe_result(target, "TLS_BLOCK", int((time.monotonic() - probe_start) * 1000), f"reset after tcp={tcp_ms}ms")
        return False, "TLS_BLOCK"
    except ssl.SSLError as e:
        log.warning("probe %s → TLS_BLOCK ssl error after tcp=%dms: %s", ip, tcp_ms, e)
        if target:
            log_probe_result(target, "TLS_BLOCK", int((time.monotonic() - probe_start) * 1000), str(e))
        return False, "TLS_BLOCK"
    except OSError as e:
        log.warning("probe %s → DOWN after tcp=%dms: %s", ip, tcp_ms, e)
        if target:
            log_probe_result(target, "DOWN", int((time.monotonic() - probe_start) * 1000), str(e))
        return False, "DOWN"

    log.info("probe %s → %s tcp=%dms tls=%dms", ip, verdict, tcp_ms, tls_ms)
    if target:
        log_probe_result(target, verdict, int((time.monotonic() - probe_start) * 1000), detail)
    return verdict == "OK", verdict


def probe_with_retry(ip: str, target: str) -> bool:
    for attempt in range(1, FAIL_THRESHOLD + 1):
        ok, verdict = probe(ip, target)
        if ok:
            log.info("probe ok: %s (attempt %d)", ip, attempt)
            return True
        log.warning("probe fail: %s verdict=%s (attempt %d/%d)", ip, verdict, attempt, FAIL_THRESHOLD)
        if attempt < FAIL_THRESHOLD:
            time.sleep(RETRY_DELAY)
    return False


def _extract_json_object(text: str) -> dict:
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


def _ladon_field(data: dict, *names: str):
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


def parse_ladon_probe(text: str) -> dict:
    try:
        data = _extract_json_object(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "blocked": False, "verdict": "LADON_PARSE_ERROR", "detail": str(exc), "latency_ms": 0}
    raw_code = _ladon_field(data, "FailureCode", "failure_code", "code", "Verdict", "verdict") or ""
    verdict = normalize_ladon_failure(raw_code)
    if str(raw_code).strip().upper() in {"OK", "PASS"}:
        verdict = ""
    explicit_ok = _ladon_field(data, "OK", "ok")
    tcp_ok = bool(_ladon_field(data, "TCPOK", "tcp_ok"))
    tls_ok = bool(_ladon_field(data, "TLSOK", "tls_ok"))
    http_ok = bool(_ladon_field(data, "HTTPOK", "http_ok"))
    ok = bool(explicit_ok) if explicit_ok is not None else (not verdict and tcp_ok and tls_ok and http_ok)
    latency = _ladon_field(data, "LatencyMS", "latency_ms", "latency")
    try:
        latency_ms = int(latency or 0)
    except (TypeError, ValueError):
        latency_ms = 0
    return {
        "ok": ok,
        "blocked": verdict in LADON_SIGNATURE_FAILURE_CODES,
        "verdict": verdict or ("OK" if ok else "unknown"),
        "detail": str(_ladon_field(data, "FailureReason", "failure_reason", "reason", "Detail", "detail") or ""),
        "latency_ms": latency_ms,
    }


def ladon_probe_domain(domain: str, target: str | None = None) -> dict:
    started = time.monotonic()
    cmd = [LADON_BIN, *LADON_EXTRA_ARGS, "probe", domain]
    if LADON_SUDO:
        cmd = ["sudo", "-n", *cmd]
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=PROBE_TIMEOUT + 5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result = {"ok": False, "blocked": True, "verdict": "tcp_timeout", "detail": "ladon timeout", "latency_ms": int((time.monotonic() - started) * 1000)}
    except OSError as exc:
        result = {"ok": False, "blocked": False, "verdict": "LADON_EXEC_ERROR", "detail": str(exc), "latency_ms": int((time.monotonic() - started) * 1000)}
    else:
        output = proc.stdout if proc.returncode == 0 else (proc.stdout or proc.stderr)
        result = parse_ladon_probe(output)
        result["latency_ms"] = result.get("latency_ms") or int((time.monotonic() - started) * 1000)
        if proc.returncode != 0 and result["verdict"] == "LADON_PARSE_ERROR":
            result["verdict"] = "LADON_EXIT_" + str(proc.returncode)
            result["detail"] = (proc.stderr or proc.stdout).strip()[:500]
    if target:
        log_probe_result(target, result["verdict"], int(result.get("latency_ms") or 0), result.get("detail", ""))
    log.info("ladon %s → ok=%s blocked=%s verdict=%s", domain, result["ok"], result["blocked"], result["verdict"])
    return result


def health_item(name: str, flag: str, ok: bool, verdict: str, detail: str = "", *, blocked: bool = False, notify: bool = True) -> dict:
    return {
        "name": name,
        "flag": flag,
        "ok": bool(ok),
        "blocked": bool(blocked),
        "verdict": str(verdict or ("OK" if ok else "FAIL")),
        "detail": str(detail or "")[:300],
        "notify": bool(notify),
    }


def build_health_snapshot(current_ip: str, profiles: dict[str, dict]) -> dict[str, dict]:
    snapshot: dict[str, dict] = {}
    domain_result = ladon_probe_domain(DOMAIN, "primary")
    snapshot["ru_domain"] = health_item(
        "ru.goida.fun",
        "🇷🇺",
        domain_result["ok"],
        domain_result["verdict"],
        f"current A={current_ip or '?'}",
        blocked=domain_result["blocked"],
    )

    primary_ok, primary_verdict = probe(PRIMARY_IP, "primary")
    snapshot["ru_primary_ip"] = health_item("RU primary IP", "🇷🇺", primary_ok, primary_verdict, PRIMARY_IP, blocked=not primary_ok)

    backup_ok, backup_verdict = probe(BACKUP_IP, "backup")
    snapshot["ru_backup_ip"] = health_item("RU backup IP", "🇷🇺", backup_ok, backup_verdict, BACKUP_IP, blocked=not backup_ok)

    for key, meta in FOREIGN_TARGETS.items():
        domain = str(meta["domain"])
        result = ladon_probe_domain(domain)
        nonblocking = result["verdict"] in set(meta.get("nonblocking_codes", set()))
        snapshot[key] = health_item(
            domain,
            str(meta["flag"]),
            result["ok"] or nonblocking,
            result["verdict"],
            result.get("detail", ""),
            blocked=result["blocked"],
            notify=not nonblocking,
        )

    hy2 = profiles.get("backup_hy2_8443_udp") or {}
    snapshot["ru_backup_hy2"] = health_item(
        "RU backup HY2",
        "🇷🇺",
        bool(hy2.get("ok")),
        "OK" if hy2.get("ok") else "FAIL",
        f"{BACKUP_IP}:{BACKUP_HY2_PORT} {hy2.get('detail', '')}",
        blocked=False,
        notify=False,
    )
    return snapshot


def read_health_state() -> dict:
    try:
        data = json.loads(HEALTH_STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_health_state(snapshot: dict[str, dict]) -> None:
    HEALTH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": int(time.time()),
        "items": snapshot,
    }
    tmp = HEALTH_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    tmp.replace(HEALTH_STATE_FILE)


def health_alert_text(changes: list[tuple[str, dict, dict | None]], current_ip: str) -> str:
    lines = [f"🛰 <b>watchdog: изменение доступности</b>", f"<code>{DOMAIN}</code> A: <code>{current_ip or '?'}</code>", ""]
    for _key, item, old in changes:
        status = "OK" if item["ok"] else ("BLOCK" if item["blocked"] else "FAIL")
        old_status = "unknown" if not old else ("OK" if old.get("ok") else ("BLOCK" if old.get("blocked") else "FAIL"))
        lines.append(
            f"{item['flag']} <b>{item['name']}</b>: {old_status} → <b>{status}</b> "
            f"<code>{item['verdict']}</code>"
        )
        if item.get("detail"):
            lines.append(f"  <code>{item['detail']}</code>")
    return "\n".join(lines)


def notify_health_changes(snapshot: dict[str, dict], current_ip: str) -> None:
    previous = read_health_state().get("items") or {}
    changes: list[tuple[str, dict, dict | None]] = []
    for key, item in snapshot.items():
        if not item.get("notify", True):
            continue
        old = previous.get(key)
        if not old:
            if not item.get("ok", False):
                changes.append((key, item, None))
            continue
        if bool(old.get("ok")) != bool(item.get("ok")) or bool(old.get("blocked")) != bool(item.get("blocked")):
            changes.append((key, item, old))
    write_health_state(snapshot)
    if changes:
        tg_alert(health_alert_text(changes, current_ip), silent=True)


def ladon_probe_with_retry(domain: str, target: str) -> dict:
    last = {"ok": False, "blocked": False, "verdict": "NO_ATTEMPTS", "detail": "", "latency_ms": 0}
    for attempt in range(1, FAIL_THRESHOLD + 1):
        last = ladon_probe_domain(domain, target)
        if last["ok"]:
            return last
        log.warning("ladon fail: %s verdict=%s (attempt %d/%d)", domain, last["verdict"], attempt, FAIL_THRESHOLD)
        if attempt < FAIL_THRESHOLD:
            time.sleep(RETRY_DELAY)
    return last


def primary_status(current_ip: str) -> tuple[bool, str, bool]:
    """Возвращает (ok, verdict, signature_blocked).

    Когда DNS стоит на primary, Ladon проверяет реальный пользовательский путь
    `ru.goida.fun`. Когда DNS уже на backup, primary можно проверить только
    прямым SNI-пробом по IP: Ladon CLI v1 не умеет raw IP + custom SNI.
    """
    if current_ip in ("", PRIMARY_IP):
        result = ladon_probe_with_retry(DOMAIN, "primary")
        if result["ok"] or result["blocked"]:
            return bool(result["ok"]), str(result["verdict"]), bool(result["blocked"])
        log.warning("ladon ambiguous (%s), using direct primary SNI probe as guard", result["verdict"])
        ok, verdict = probe(PRIMARY_IP, "primary")
        return ok, f"{result['verdict']}+{verdict}", False
    ok, verdict = probe(PRIMARY_IP, "primary")
    return ok, verdict, False


def tcp_probe(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    started = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True, f"tcp={int((time.monotonic() - started) * 1000)}ms"
    except socket.timeout:
        return False, "timeout"
    except OSError as exc:
        return False, str(exc)


def udp_probe(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """best-effort UDP reachability probe: для HY2 нет safe stdlib handshake."""
    started = time.monotonic()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.send(b"\x00")
        return True, f"udp-send={int((time.monotonic() - started) * 1000)}ms"
    except OSError as exc:
        return False, str(exc)
    finally:
        sock.close()


def service_profiles() -> dict[str, dict]:
    backup_hy2_ok, backup_hy2_detail = udp_probe(BACKUP_IP, BACKUP_HY2_PORT)
    reserve_ok, reserve_detail = tcp_probe(RESERVE_HOST, RESERVE_PORT)
    profiles = {
        "backup_hy2_8443_udp": {
            "ok": backup_hy2_ok,
            "host": BACKUP_IP,
            "port": BACKUP_HY2_PORT,
            "transport": "udp",
            "detail": backup_hy2_detail,
        },
        "reserve_reality_443": {
            "ok": reserve_ok,
            "host": RESERVE_HOST,
            "port": RESERVE_PORT,
            "transport": "tcp",
            "detail": reserve_detail,
        },
    }
    for name, item in profiles.items():
        state = "ok" if item["ok"] else "fail"
        log.info("%s → %s %s:%s %s", name, state, item["host"], item["port"], item["detail"])
    return profiles


def _socks5_connect(proxy_addr: str, host: str, port: int, timeout: float) -> socket.socket:
    proxy_host, _, proxy_port_raw = proxy_addr.rpartition(":")
    if not proxy_host or not proxy_port_raw:
        raise ValueError("proxy_addr must be host:port")
    sock = socket.create_connection((proxy_host, int(proxy_port_raw)), timeout=timeout)
    sock.settimeout(timeout)
    sock.sendall(b"\x05\x01\x00")
    if sock.recv(2) != b"\x05\x00":
        sock.close()
        raise OSError("socks5 auth negotiation failed")
    host_raw = host.encode("idna")
    req = b"\x05\x01\x00\x03" + bytes([len(host_raw)]) + host_raw + port.to_bytes(2, "big")
    sock.sendall(req)
    resp = sock.recv(4)
    if len(resp) != 4 or resp[1] != 0:
        sock.close()
        code = resp[1] if len(resp) >= 2 else "short"
        raise OSError(f"socks5 connect failed: {code}")
    atyp = resp[3]
    if atyp == 1:
        to_read = 4
    elif atyp == 3:
        to_read = sock.recv(1)[0]
    elif atyp == 4:
        to_read = 16
    else:
        sock.close()
        raise OSError(f"socks5 atyp failed: {atyp}")
    sock.recv(to_read + 2)
    return sock


def _resolve_youtube_media_url(proxy_addr: str | None = None) -> str:
    if YOUTUBE_MEDIA_URL:
        return YOUTUBE_MEDIA_URL
    if proxy_addr:
        parsed = urllib.parse.urlparse(YOUTUBE_WATCH_URL)
        host = parsed.hostname or ""
        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        raw = None
        try:
            raw = _socks5_connect(proxy_addr, host, port, PROBE_TIMEOUT)
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                req = (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    f"{GENERIC_HEADERS}\r\n"
                ).encode()
                tls.sendall(req)
                status_code, _, body, status_line = _read_http_response(tls, max_body=2_000_000)
                if status_code != 200:
                    raise RuntimeError(f"youtube watch fetch failed: {status_line}")
                page = body.decode("utf-8", "ignore")
        finally:
            if raw:
                try:
                    raw.close()
                except Exception:
                    pass
    else:
        req = urllib.request.Request(
            YOUTUBE_WATCH_URL,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*"},
        )
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            page = resp.read(2_000_000).decode("utf-8", "ignore")
    matches = re.findall(r'https:\\/\\/[^"\\]+googlevideo\.com\\/videoplayback[^"\\]+', page)
    for match in matches:
        url = match.replace("\\/", "/").encode("utf-8").decode("unicode_escape")
        url = urllib.parse.unquote(url)
        if "mime=video" in url or "mime%3Dvideo" in url:
            return url
    raise RuntimeError("youtube media url not found")


def _read_http_response(sock, max_body: int = 4096) -> tuple[int, dict[str, str], bytes, str]:
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin1", "ignore").split("\r\n")
    status_line = lines[0] if lines else ""
    parts = status_line.split()
    status_code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.lower()] = value.strip()
    while len(body) < max_body:
        chunk = sock.recv(min(4096, max_body - len(body)))
        if not chunk:
            break
        body += chunk
    return status_code, headers, body, status_line


def check_youtube_media(proxy_addr: str | None = None) -> dict:
    """range-запрос к реальному googlevideo media endpoint без внешних зависимостей."""
    started = time.monotonic()
    raw = None
    try:
        media_url = _resolve_youtube_media_url(proxy_addr)
        parsed = urllib.parse.urlparse(media_url)
        host = parsed.hostname or ""
        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        if proxy_addr:
            raw = _socks5_connect(proxy_addr, host, port, PROBE_TIMEOUT)
        else:
            raw = socket.create_connection((host, port), timeout=PROBE_TIMEOUT)
            raw.settimeout(PROBE_TIMEOUT)
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Range: bytes=0-131071\r\n"
                f"{GENERIC_HEADERS}\r\n"
            ).encode()
            tls.sendall(req)
            status_code, headers, body, status_line = _read_http_response(tls)
            content_type = headers.get("content-type", "").lower()
            if status_code not in (200, 206):
                verdict = "HTTP_ERROR"
            elif not content_type.startswith("video/") or not body:
                verdict = "CONTENT_MISMATCH"
            else:
                verdict = "OK"
            return {
                "ok": verdict == "OK",
                "verdict": verdict,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "status": status_code,
                "detail": f"{status_line}; content-type={content_type}; bytes={len(body)}",
                "proxy": proxy_addr or "",
                "url": media_url,
            }
    except socket.timeout:
        verdict, detail = "TIMEOUT", "timeout"
    except ConnectionResetError:
        verdict, detail = "TCP_RESET", "tcp reset"
    except ssl.SSLError as e:
        verdict, detail = "TLS_BLOCK", str(e)
    except OSError as e:
        verdict, detail = "TCP_RESET", str(e)
    except Exception as e:
        verdict, detail = "HTTP_ERROR", str(e)
    finally:
        if raw:
            try:
                raw.close()
            except Exception:
                pass
    return {
        "ok": False,
        "verdict": verdict,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "status": 0,
        "detail": detail,
        "proxy": proxy_addr or "",
        "url": media_url if "media_url" in locals() else "",
    }


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def read_state() -> str:
    try:
        return STATE_FILE.read_text().strip()
    except FileNotFoundError:
        return PRIMARY_IP


def state_is_managed(ip: str) -> bool:
    return ip in {PRIMARY_IP, BACKUP_IP}


def write_state(ip: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(ip)


def read_manual_override() -> str:
    try:
        return MANUAL_OVERRIDE_FILE.read_text().strip()
    except FileNotFoundError:
        return ""


def write_manual_override(ip: str) -> None:
    MANUAL_OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANUAL_OVERRIDE_FILE.write_text(ip)


def clear_manual_override() -> None:
    try:
        MANUAL_OVERRIDE_FILE.unlink()
    except FileNotFoundError:
        pass


def read_current_ip_safe() -> str:
    try:
        current_ip = cf_get_current_ip()
        log.info("cloudflare: current A %s → %s", DOMAIN, current_ip)
        return current_ip
    except Exception as e:
        log.warning("cloudflare current ip read failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Cloudflare API
# ---------------------------------------------------------------------------

def _cf_request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{CF_API}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {CF_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"CF API {method} {path}: {e.code} {e.read().decode()}") from e


def cf_get_record() -> dict:
    resp = _cf_request("GET", f"/zones/{CF_ZONE_ID}/dns_records?type=A&name={DOMAIN}")
    records = resp.get("result", [])
    if not records:
        raise RuntimeError(f"A-запись {DOMAIN} не найдена в Cloudflare")
    return records[0]


def cf_get_current_ip() -> str:
    return str(cf_get_record().get("content", "")).strip()


def cf_update_record(record_id: str, new_ip: str) -> None:
    _cf_request("PATCH", f"/zones/{CF_ZONE_ID}/dns_records/{record_id}", {
        "content": new_ip,
        "ttl": 60,
        "proxied": False,
    })
    log.info("cloudflare: A %s → %s", DOMAIN, new_ip)


def dns_switch(new_ip: str) -> None:
    if new_ip not in {PRIMARY_IP, BACKUP_IP}:
        raise RuntimeError(f"refuse DNS switch to unmanaged IP: {new_ip}")
    record = cf_get_record()
    record_id = record["id"]
    current_ip = str(record.get("content", "")).strip()
    if current_ip == new_ip:
        log.info("cloudflare: A %s уже %s", DOMAIN, new_ip)
        return
    if not DNS_APPLY:
        log.warning("dry-run cloudflare: A %s %s → %s", DOMAIN, current_ip, new_ip)
        return
    cf_update_record(record_id, new_ip)


# ---------------------------------------------------------------------------
# alert via /notify relay on RU server (обходит блокировку TG на ISP)
# ---------------------------------------------------------------------------

def tg_alert(text: str, silent: bool = True) -> None:
    if not NOTIFY_URL or not NOTIFY_TOKEN:
        log.warning("alert skipped: NOTIFY_URL/NOTIFY_TOKEN not set")
        return
    body = json.dumps({"text": text, "silent": bool(silent)}).encode()
    req = urllib.request.Request(
        NOTIFY_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {NOTIFY_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            log.info("alert sent: %s", r.status)
    except Exception as e:
        log.warning("alert failed: %s", e)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--report":
        print_report()
        return

    require_runtime_env()
    state_ip = read_state()
    current_ip = read_current_ip_safe()
    manual_ip = read_manual_override()
    profiles = service_profiles()
    health = build_health_snapshot(current_ip, profiles)
    notify_health_changes(health, current_ip)

    if current_ip:
        if manual_ip:
            if current_ip == manual_ip:
                log.info(
                    "manual DNS override активен: A %s → %s, watchdog DNS не меняет",
                    DOMAIN,
                    manual_ip,
                )
                return
            log.info("manual DNS override снят: было %s, сейчас %s", manual_ip, current_ip)
            clear_manual_override()

        if current_ip != state_ip:
            if not state_is_managed(state_ip) and state_is_managed(current_ip):
                log.warning("stale watchdog state migrated: %s → %s", state_ip, current_ip)
                write_state(current_ip)
                state_ip = current_ip
            else:
                log.warning(
                    "manual DNS override detected: state=%s, cloudflare=%s; auto DNS changes paused",
                    state_ip,
                    current_ip,
                )
                write_manual_override(current_ip)
                tg_alert(
                    f"🛑 {DOMAIN}: обнаружен ручной DNS IP {current_ip}.\n"
                    "watchdog не будет менять A-запись, пока ручной IP остаётся активным."
                )
                return

    log.info("priority probe: primary %s via Ladon/current-domain guard", PRIMARY_IP)
    if current_ip in ("", PRIMARY_IP):
        domain_health = health["ru_domain"]
        primary_ip_health = health["ru_primary_ip"]
        primary_ok = bool(domain_health["ok"] and primary_ip_health["ok"])
        primary_verdict = "OK" if primary_ok else f"{domain_health['verdict']}+{primary_ip_health['verdict']}"
        primary_signature_blocked = bool(domain_health["blocked"] or primary_ip_health["blocked"])
    else:
        primary_ip_health = health["ru_primary_ip"]
        primary_ok = bool(primary_ip_health["ok"])
        primary_verdict = str(primary_ip_health["verdict"])
        primary_signature_blocked = bool(primary_ip_health["blocked"])
    if primary_ok:
        log.info("probe ok: %s", PRIMARY_IP)
        if AUTO_RECOVERY and (state_ip != PRIMARY_IP or current_ip not in ("", PRIMARY_IP)):
            log.info("primary доступен, возвращаем DNS → %s", PRIMARY_IP)
            dns_switch(PRIMARY_IP)
            write_state(PRIMARY_IP)
            tg_alert(f"✅ {DOMAIN}: primary {PRIMARY_IP} доступен, DNS возвращён.")
        elif state_ip != PRIMARY_IP:
            write_state(PRIMARY_IP)
        return

    if current_ip in ("", PRIMARY_IP) and not primary_signature_blocked:
        reserve_ok = bool(profiles.get("reserve_reality_443", {}).get("ok"))
        log.error(
            "primary %s недоступен (%s), но Ladon-сигнатура блокировки не подтверждена; DNS не меняем",
            PRIMARY_IP,
            primary_verdict,
        )
        tg_alert(
            f"🔴 {DOMAIN}: primary {PRIMARY_IP} недоступен ({primary_verdict}), "
            "но Ladon не подтвердил сигнатурную блокировку.\n"
            f"DNS не изменён. reserve {RESERVE_HOST}:{RESERVE_PORT}="
            f"{'OK' if reserve_ok else 'FAIL'}."
        )
        sys.exit(1)

    log.error("primary IP %s недоступен (%s), проверяем резервный HTTPS %s", PRIMARY_IP, primary_verdict, BACKUP_IP)
    backup_ok = probe_with_retry(BACKUP_IP, "backup")
    if not backup_ok:
        backup_hy2_ok = bool(profiles.get("backup_hy2_8443_udp", {}).get("ok"))
        reserve_ok = bool(profiles.get("reserve_reality_443", {}).get("ok"))
        log.error("backup HTTPS %s:443 не прошёл проверку, DNS не меняем", BACKUP_IP)
        tg_alert(
            f"🔴 {DOMAIN}: primary {PRIMARY_IP} недоступен ({primary_verdict}), "
            f"backup {BACKUP_IP}:443 тоже не прошёл проверку.\n"
            f"DNS не изменён. HY2 {BACKUP_IP}:{BACKUP_HY2_PORT}="
            f"{'OK' if backup_hy2_ok else 'FAIL'}, reserve {RESERVE_HOST}:{RESERVE_PORT}="
            f"{'OK' if reserve_ok else 'FAIL'}."
        )
        sys.exit(1)

    log.error("primary IP %s недоступен, backup HTTPS прошёл, переключаем DNS на %s", PRIMARY_IP, BACKUP_IP)
    try:
        dns_switch(BACKUP_IP)
        write_state(BACKUP_IP)
        tg_alert(
            f"⚠️ {DOMAIN}: primary {PRIMARY_IP} недоступен.\n"
            f"DNS переключён на резервный HTTPS {BACKUP_IP}."
        )
    except Exception as e:
        log.error("failover failed: %s", e)
        tg_alert(f"🔴 {DOMAIN}: primary {PRIMARY_IP} упал, но failover не удался: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
