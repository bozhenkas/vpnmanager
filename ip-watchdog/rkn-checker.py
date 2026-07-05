#!/usr/bin/env python3
"""
rkn-checker.py — проверяет доступность VPN-эндпоинтов через прямые TCP/TLS/HTTP пробы.
шлёт JSON-результат на /rknstatus в бот каждый запуск.
алерт через /notify только при изменении статуса.

Не зависит от rkn-check бинаря (убрана зависимость от --json флага из rkn-block-checker 0.4.x/0.5.x).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import random
import socket
import ssl
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# конфиг из env
# ---------------------------------------------------------------------------

NOTIFY_URL    = os.environ.get("NOTIFY_URL", "")
NOTIFY_TOKEN  = os.environ.get("NOTIFY_TOKEN", "")
STATUS_URL    = os.environ.get("STATUS_URL", "")
STATE_FILE    = Path(os.environ.get("STATE_FILE", "/var/lib/rkn-checker/state.json"))
PROBE_TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "10"))
# подозрение на блок подтверждаем несколькими пробами, разнесёнными по окну
# 10–30 с (анти-ложные срабатывания ТСПУ): 3 попытки × ~10 с ≈ окно ~20 с.
CONFIRM_ATTEMPTS = int(os.environ.get("CONFIRM_ATTEMPTS", "3"))
CONFIRM_DELAY = float(os.environ.get("CONFIRM_DELAY", "10"))
CONFIRM_JITTER = float(os.environ.get("CONFIRM_JITTER", "0"))
# RKN_CHECK оставлен для backward compat, но больше не используется (прямые пробы)
RKN_CHECK     = os.environ.get("RKN_CHECK", "/opt/rkn-checker/rkn-check")
PRIMARY_IP    = os.environ.get("PRIMARY_IP", "45.91.54.152")
BACKUP_IP     = os.environ.get("BACKUP_IP", "45.91.53.93")
DOMAIN        = os.environ.get("DOMAIN", "ru.goida.fun")
YOUTUBE_PROXY = os.environ.get("YOUTUBE_PROXY", "")
WATCHDOG_MODULE = os.environ.get("WATCHDOG_MODULE", "/opt/rkn-checker/watchdog.py")

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
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36\r\n"
    "Accept: text/html,application/xhtml+xml,*/*;q=0.9\r\n"
    "Accept-Language: ru-RU,ru;q=0.9,en;q=0.8\r\n"
    "Connection: close\r\n"
)

# статический fallback на случай отсутствия живого cluster-конфига (см. build_endpoints).
# critical=True → retry при падении, critical=False → просто пишем в статус без retry.
# no_verify=True → для REALITY-нод (TLS-сертификат заведомо не совпадает с доменом).
STATIC_ENDPOINTS = [
    {"label": "domain",       "url": f"https://{DOMAIN}/",      "status_key": "domain_status",    "critical": True},
    {"label": "primary_ip",   "url": f"https://{PRIMARY_IP}/",  "status_key": "primary_ip_status", "critical": True},
    {"label": "backup_ip",    "url": f"https://{BACKUP_IP}/",   "status_key": "backup_ip_status",  "critical": False},
    {"label": "fin.goida.fun","url": "https://fin.goida.fun/",  "critical": False, "no_verify": True},
]


def load_cluster_config_module():
    """грузим cluster_config.py по пути — co-deploy рядом или из subscription/."""
    env_path = os.environ.get("CLUSTER_CONFIG_MODULE", "").strip()
    candidates = [
        Path(env_path) if env_path else None,
        Path(__file__).with_name("cluster_config.py"),
        Path(__file__).resolve().parent.parent / "subscription" / "cluster_config.py",
    ]
    path = next((item for item in candidates if item is not None and item.is_file()), None)
    if path is None:
        raise FileNotFoundError("cluster_config.py not found")
    spec = importlib.util.spec_from_file_location("cluster_config_probe", path)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load cluster_config.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_endpoints() -> list[dict]:
    """эндпоинты из live cluster-инвентаря; при ЛЮБОЙ неудаче — статический fallback."""
    try:
        cc = load_cluster_config_module()
        cfg = cc.load_cluster_from_env()
        if cfg is not None:
            eps = cfg.probe_endpoints()
            if eps:
                log.info("rkn endpoints from cluster config v%s: %d", cfg.version, len(eps))
                return eps
    except Exception as e:
        log.warning("cluster config unavailable, using static endpoints: %s", e)
    return STATIC_ENDPOINTS


# Telegram-пробы: research DPI на ISP-уровне
TELEGRAM_PROBE = os.environ.get("TELEGRAM_PROBE", "1") == "1"
TELEGRAM_ENDPOINTS = [
    {"label": "telegram_org",  "url": "https://telegram.org/"},
    {"label": "t_me",          "url": "https://t.me/"},
    {"label": "telegram_api",  "url": "https://api.telegram.org/"},
    {"label": "telegram_core", "url": "https://core.telegram.org/"},
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("rkn-checker")

# эндпоинты строятся после инициализации логгера
ENDPOINTS = build_endpoints()


@dataclass
class TspuStatus:
    domain_status: str
    primary_ip_status: str
    backup_ip_status: str
    media_quality: str
    checked_at: datetime

    def blocked_alert(self) -> bool:
        return self.primary_ip_status != "OK" and self.domain_status != "OK"


# ---------------------------------------------------------------------------
# прямые TCP/TLS/HTTP пробы (stdlib only, без rkn-check бинаря)
# ---------------------------------------------------------------------------

def _http_verdict(data: bytes) -> str:
    """определяем вердикт по HTTP-ответу: OK / HTTP_STUB / BAD_HTTP"""
    head, _, body = data.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode("latin1", "ignore")
    text = body[:2000].decode("utf-8", "ignore").lower()
    if " 451 " in f" {status} ":
        return "HTTP_STUB"
    if any(marker in text for marker in STUB_MARKERS):
        return "HTTP_STUB"
    # 200/301/302/403 от нашего сервера = нормально
    for code in (" 200 ", " 301 ", " 302 ", " 403 ", " 404 ", " 400 "):
        if code in f" {status} ":
            return "OK"
    return "BAD_HTTP" if status.startswith("HTTP/") else "TCP_RESET"


def tcp_probe(host: str, port: int, timeout: float) -> tuple[bool, int, Optional[str]]:
    """TCP connect. возвращает (ok, tcp_ms, error_str)"""
    t0 = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True, int((time.monotonic() - t0) * 1000), None
    except socket.timeout:
        return False, int((time.monotonic() - t0) * 1000), "TIMEOUT"
    except ConnectionRefusedError:
        return False, int((time.monotonic() - t0) * 1000), "REFUSED"
    except OSError as e:
        return False, int((time.monotonic() - t0) * 1000), str(e)


def full_probe(url: str, timeout: float, verify_tls: bool = True, sni: Optional[str] = None) -> dict:
    """полная TCP+TLS+HTTP проба. возвращает dict с verdict, tcp_ok, tcp_ms, tls_ms, ms."""
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme
    host = parsed.hostname or parsed.netloc
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    server_hostname = sni or host

    t0 = time.monotonic()
    raw = None
    tcp_ms = 0
    tls_ms = 0

    # TCP connect
    try:
        t1 = time.monotonic()
        raw = socket.create_connection((host, port), timeout=timeout)
        raw.settimeout(timeout)
        tcp_ms = int((time.monotonic() - t1) * 1000)
    except socket.timeout:
        return _verdict_result("TIMEOUT", False, False, 0, 0)
    except ConnectionRefusedError:
        return _verdict_result("REFUSED", False, False, 0, 0)
    except OSError as e:
        return _verdict_result("DOWN", False, False, 0, 0, str(e))

    tcp_ok = True

    # если HTTP (не HTTPS) — просто TCP достаточно
    if scheme == "http":
        if raw:
            raw.close()
        return _verdict_result("OK", True, False, tcp_ms, 0)

    # TLS handshake
    try:
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        t2 = time.monotonic()
        tls = ctx.wrap_socket(raw, server_hostname=server_hostname)
        tls_ms = int((time.monotonic() - t2) * 1000)
    except socket.timeout:
        if raw:
            try: raw.close()
            except Exception: pass
        return _verdict_result("TLS_BLOCK", True, False, tcp_ms, 0, "tls timeout")
    except ConnectionResetError:
        return _verdict_result("TLS_BLOCK", True, False, tcp_ms, 0, "tls reset")
    except ssl.SSLError as e:
        return _verdict_result("TLS_BLOCK", True, False, tcp_ms, 0, str(e))
    except OSError as e:
        return _verdict_result("DOWN", True, False, tcp_ms, 0, str(e))

    # HTTP GET
    try:
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"{GENERIC_HEADERS}\r\n"
        ).encode()
        tls.sendall(req)
        data = b""
        while len(data) < 8192:
            chunk = tls.recv(4096)
            if not chunk:
                break
            data += chunk
        verdict = _http_verdict(data)
    except socket.timeout:
        verdict = "HTTP_TIMEOUT"
    except Exception as e:
        verdict = "HTTP_ERROR"
        log.debug("http recv error %s: %s", url, e)
    finally:
        try: tls.close()
        except Exception: pass

    return _verdict_result(verdict, True, True, tcp_ms, tls_ms)


def _verdict_result(verdict: str, tcp_ok: bool, tls_ok: bool,
                    tcp_ms: int, tls_ms: int, detail: str = "") -> dict:
    return {
        "verdict": verdict,
        "ok":      verdict == "OK",
        "tcp_ok":  tcp_ok,
        "tls_ok":  tls_ok,
        "tcp_ms":  tcp_ms,
        "tls_ms":  tls_ms,
        "ms":      tcp_ms + tls_ms,
        "detail":  detail,
    }


def probe_endpoint(ep: dict, timeout: float) -> dict:
    """пробируем один endpoint, возвращаем result dict."""
    url  = ep["url"]
    label = ep["label"]

    # для bare-IP эндпоинтов: не проверяем TLS cert (он выписан на домен, не IP)
    # достаточно TCP достижимости → verify_tls=False
    is_bare_ip = label in ("primary_ip", "backup_ip")
    verify_tls = not is_bare_ip

    raw = full_probe(url, timeout, verify_tls=verify_tls)
    verdict = raw["verdict"]
    ms = raw["ms"]
    tcp_ok = raw["tcp_ok"]

    log.info("%-20s → %-14s %dms", label, verdict, ms)
    return {
        "label":    label,
        "url":      url,
        "ok":       raw["ok"],
        "tcp_ok":   tcp_ok,
        "tcp_open_tls_blocked": verdict == "TLS_BLOCK" and tcp_ok and not raw["tls_ok"],
        "verdict":  verdict,
        "raw_verdict": verdict,
        "confidence": "direct",
        "ms":       ms,
        "sys_ip":   None,
        "notes":    [raw["detail"]] if raw["detail"] else [],
    }


def normalized_status(result: dict) -> str:
    return "OK" if result.get("ok") else str(result.get("verdict") or "UNKNOWN")


def ip_reach_status(result: dict) -> str:
    # Проба к bare-IP (https://<ip>/) идёт с SNI=IP, а сертификат выписан на домен.
    # verify_tls=False, поэтому TLS_BLOCK не должен возникать, но TCP-достижимость достаточна.
    if result.get("ok") or result.get("tcp_ok"):
        return "OK"
    return str(result.get("verdict") or "UNKNOWN")


def run_probes_once(endpoints: list[dict]) -> list[dict]:
    """прогоняем все эндпоинты, возвращаем список результатов"""
    results = []
    for ep in endpoints:
        try:
            results.append(probe_endpoint(ep, PROBE_TIMEOUT))
        except Exception as e:
            log.error("probe error %s: %s", ep["label"], e)
            results.append({
                "label": ep["label"], "url": ep["url"],
                "ok": False, "tcp_ok": False, "verdict": "ERROR",
                "tcp_open_tls_blocked": False, "raw_verdict": "ERROR",
                "confidence": "direct", "ms": 0, "sys_ip": None,
                "notes": [str(e)],
            })
    return results


def run_probes(endpoints: list[dict]) -> list[dict]:
    """плохой результат подтверждаем несколькими попытками (анти-ТСПУ ложные срабатывания).
    Retry только если упал хотя бы один critical endpoint — некритичные не задерживают."""
    best = run_probes_once(endpoints)

    critical_eps = {ep["label"] for ep in endpoints if ep.get("critical")}
    critical_bad = any(
        not r.get("ok") for r in best if r.get("label") in critical_eps
    )
    if not critical_bad:
        return best

    for attempt in range(2, CONFIRM_ATTEMPTS + 1):
        delay = CONFIRM_DELAY + (random.uniform(0, CONFIRM_JITTER) if CONFIRM_JITTER > 0 else 0)
        log.warning("bad status seen, retry %d/%d after %.1fs", attempt, CONFIRM_ATTEMPTS, delay)
        time.sleep(delay)

        retry = run_probes_once(endpoints)
        # для каждого эндпоинта берём лучший результат (OK > остальное)
        for i, r in enumerate(retry):
            if r.get("ok") and not best[i].get("ok"):
                best[i] = r
            elif not best[i].get("ok") and not r.get("ok"):
                # оба плохи — берём свежий (актуальнее)
                best[i] = r

        if all(r.get("ok") for r in best):
            log.info("bad status cleared after retry %d/%d", attempt, CONFIRM_ATTEMPTS)
            break

    return best


# ---------------------------------------------------------------------------
# watchdog.py модуль — youtube media probe
# ---------------------------------------------------------------------------

def load_watchdog_module():
    candidates = [
        Path(WATCHDOG_MODULE),
        Path(__file__).with_name("watchdog.py"),
        Path(__file__).resolve().parent.parent / "ip-watchdog" / "watchdog.py",
    ]
    path = next((item for item in candidates if item.exists()), None)
    if path is None:
        raise FileNotFoundError(f"watchdog.py not found (WATCHDOG_MODULE={WATCHDOG_MODULE})")
    spec = importlib.util.spec_from_file_location("watchdog_probe", path)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load watchdog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_youtube_media() -> dict:
    try:
        module = load_watchdog_module()
        return module.check_youtube_media(YOUTUBE_PROXY or None)
    except Exception as e:
        log.warning("youtube media check failed: %s", e)
        return {
            "ok": False, "verdict": "UNKNOWN",
            "latency_ms": 0, "status": 0,
            "detail": str(e), "url": "",
        }


def check_service_profiles() -> dict:
    """расширенные TCP/UDP профили из watchdog.py (опционально)"""
    try:
        module = load_watchdog_module()
        fn = getattr(module, "service_profiles", None)
        if fn is None:
            return {}
        return fn()
    except Exception as e:
        log.warning("service profile check failed: %s", e)
        return {}


def parse_youtube_result(raw: dict) -> dict:
    verdict = str(raw.get("verdict") or "UNKNOWN")
    ok = verdict == "OK"
    ms = int(raw.get("latency_ms") or 0)
    log.info("%-20s → %-14s %dms", "youtube_media", verdict, ms)
    return {
        "label": "youtube_media",
        "url":   raw.get("url", ""),
        "ok":    ok,
        "tcp_ok": ok,
        "verdict": verdict,
        "confidence": "media",
        "ms":    ms,
        "sys_ip": None,
        "notes": [raw.get("detail", "")],
        "http_status": raw.get("status", 0),
    }


def media_quality_from_verdict(verdict: str) -> str:
    if verdict == "OK":
        return "OK"
    if verdict in ("TIMEOUT", "TCP_RESET", "TLS_BLOCK"):
        return "BLOCKED"
    if verdict in ("HTTP_ERROR", "CONTENT_MISMATCH"):
        return "DEGRADED"
    return "UNKNOWN"


def build_tspu_status(results: list[dict], checked_at: datetime) -> TspuStatus:
    by_label = {r["label"]: r for r in results}
    media_result = by_label.get("youtube_media", {})
    return TspuStatus(
        domain_status=normalized_status(by_label.get("domain", {})),
        primary_ip_status=ip_reach_status(by_label.get("primary_ip", {})),
        backup_ip_status=ip_reach_status(by_label.get("backup_ip", {})),
        media_quality=media_quality_from_verdict(str(media_result.get("verdict") or "UNKNOWN")),
        checked_at=checked_at,
    )


def tspu_alert_text(status: TspuStatus) -> str:
    lines = [
        f"domain_status: <b>{status.domain_status}</b>",
        f"primary_ip: <b>{status.primary_ip_status}</b>",
        f"backup_ip: <b>{status.backup_ip_status}</b>",
        f"media_quality: <b>{status.media_quality}</b>",
    ]
    return "\n".join(lines)


def should_send_blocked_alert(prev: dict, status: TspuStatus) -> bool:
    prev_status = TspuStatus(
        domain_status=str(prev.get("domain_status", "OK")),
        primary_ip_status=str(prev.get("primary_ip_status", "OK")),
        backup_ip_status=str(prev.get("backup_ip_status", "OK")),
        media_quality=str(prev.get("media_quality", prev.get("youtube_media_status", "OK"))),
        checked_at=parse_checked_at(prev.get("checked_at")),
    )
    if status.blocked_alert():
        return not prev_status.blocked_alert()
    if prev_status.blocked_alert() and not status.blocked_alert():
        return True
    return False


def parse_checked_at(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    if isinstance(value, (int, float)) and value:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return datetime.fromtimestamp(0, tz=timezone.utc)


def tspu_status_payload(status: TspuStatus) -> dict:
    data = asdict(status)
    data["checked_at"] = status.checked_at.isoformat()
    return data


# ---------------------------------------------------------------------------
# state / HTTP
# ---------------------------------------------------------------------------

def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def write_state(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data))


def _post_json(url: str, body: dict) -> None:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {NOTIFY_TOKEN}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        log.info("POST %s → %d", url, r.status)


def tg_alert(text: str) -> None:
    if not NOTIFY_URL or not NOTIFY_TOKEN:
        return
    try:
        _post_json(NOTIFY_URL, {"text": text})
    except Exception as e:
        log.warning("alert failed: %s", e)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    # основные эндпоинты
    main_results = run_probes(ENDPOINTS)

    # youtube media
    yt_raw = check_youtube_media()
    yt_result = parse_youtube_result(yt_raw)

    all_results = main_results + [yt_result]

    # Telegram-пробы
    telegram_results: list[dict] = []
    if TELEGRAM_PROBE:
        telegram_results = run_probes(TELEGRAM_ENDPOINTS)

    ts = int(time.time())
    tspu_status = build_tspu_status(all_results, datetime.fromtimestamp(ts, tz=timezone.utc))
    payload = {
        "ts": ts,
        "results": all_results,
        "telegram_results": telegram_results,
        "service_profiles": check_service_profiles(),
        "tspu_status": tspu_status_payload(tspu_status),
    }

    if STATUS_URL and NOTIFY_TOKEN:
        try:
            _post_json(STATUS_URL, payload)
        except Exception as e:
            log.warning("status post failed: %s", e)

    # blocked-alert только по связке primary_ip + domain
    prev = read_state()
    if should_send_blocked_alert(prev.get("tspu_status", {}), tspu_status):
        tg_alert(tspu_alert_text(tspu_status))

    write_state(payload)


if __name__ == "__main__":
    main()
