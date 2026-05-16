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
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# конфиг из env
# ---------------------------------------------------------------------------

PRIMARY_IP      = os.environ["PRIMARY_IP"]
BACKUP_IP       = os.environ["BACKUP_IP"]
DOMAIN          = os.environ.get("DOMAIN", "ru.goida.fun")
CHECK_PORT      = int(os.environ.get("CHECK_PORT", "443"))
CF_TOKEN        = os.environ["CF_TOKEN"]
CF_ZONE_ID      = os.environ["CF_ZONE_ID"]
TG_TOKEN        = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID      = os.environ.get("TG_CHAT_ID", "")
STATE_FILE      = Path(os.environ.get("STATE_FILE", "/tmp/ip-watchdog.state"))
FAIL_THRESHOLD  = int(os.environ.get("FAIL_THRESHOLD", "3"))
PROBE_TIMEOUT   = int(os.environ.get("PROBE_TIMEOUT", "8"))
RETRY_DELAY     = int(os.environ.get("RETRY_DELAY", "10"))
AUTO_RECOVERY   = os.environ.get("AUTO_RECOVERY", "1") == "1"

CF_API = "https://api.cloudflare.com/client/v4"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("watchdog")


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def probe(ip: str, port: int, sni: str, timeout: int) -> bool:
    """TCP + TLS handshake к IP напрямую (минуя DNS), SNI = домен."""
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((ip, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=sni) as tls:
                tls.do_handshake()
        return True
    except Exception as e:
        log.debug("probe %s:%d failed: %s", ip, port, e)
        return False


def probe_with_retry(ip: str) -> bool:
    for attempt in range(1, FAIL_THRESHOLD + 1):
        if probe(ip, CHECK_PORT, DOMAIN, PROBE_TIMEOUT):
            log.info("probe ok: %s (attempt %d)", ip, attempt)
            return True
        log.warning("probe fail: %s (attempt %d/%d)", ip, attempt, FAIL_THRESHOLD)
        if attempt < FAIL_THRESHOLD:
            time.sleep(RETRY_DELAY)
    return False


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def read_state() -> str:
    try:
        return STATE_FILE.read_text().strip()
    except FileNotFoundError:
        return PRIMARY_IP


def write_state(ip: str) -> None:
    STATE_FILE.write_text(ip)


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


def cf_get_record_id() -> str:
    resp = _cf_request("GET", f"/zones/{CF_ZONE_ID}/dns_records?type=A&name={DOMAIN}")
    records = resp.get("result", [])
    if not records:
        raise RuntimeError(f"A-запись {DOMAIN} не найдена в Cloudflare")
    return records[0]["id"]


def cf_update_record(record_id: str, new_ip: str) -> None:
    _cf_request("PATCH", f"/zones/{CF_ZONE_ID}/dns_records/{record_id}", {
        "content": new_ip,
        "ttl": 60,
        "proxied": False,
    })
    log.info("cloudflare: A %s → %s", DOMAIN, new_ip)


def dns_switch(new_ip: str) -> None:
    record_id = cf_get_record_id()
    cf_update_record(record_id, new_ip)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def tg_alert(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": TG_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning("tg alert failed: %s", e)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    active_ip = read_state()
    backup_ip = BACKUP_IP if active_ip == PRIMARY_IP else PRIMARY_IP

    reachable = probe_with_retry(active_ip)

    if reachable:
        if AUTO_RECOVERY and active_ip == BACKUP_IP and probe_with_retry(PRIMARY_IP):
            log.info("primary восстановлен, возвращаем DNS → %s", PRIMARY_IP)
            dns_switch(PRIMARY_IP)
            write_state(PRIMARY_IP)
            tg_alert(f"✅ {DOMAIN}: primary {PRIMARY_IP} восстановлен, DNS возвращён.")
        return

    log.error("active IP %s недоступен, переключаем на %s", active_ip, backup_ip)
    try:
        dns_switch(backup_ip)
        write_state(backup_ip)
        tg_alert(
            f"⚠️ {DOMAIN}: {active_ip} заблокирован.\n"
            f"DNS переключён на резервный {backup_ip}."
        )
    except Exception as e:
        log.error("failover failed: %s", e)
        tg_alert(f"🔴 {DOMAIN}: {active_ip} упал, но failover не удался: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
