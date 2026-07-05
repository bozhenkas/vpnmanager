#!/usr/bin/env python3
"""
cf_api.py — общий клиент Cloudflare DNS API (A-записи).

Канонический helper для парковки доменов при смене IP. Лифт проверенной логики
из ip-watchdog/watchdog.py (cf_get_record/cf_update_record/dns_switch), но без
модульных глобалов: токен/зона передаются явно, чтобы один код переиспользовали
и infra-backend (FRA, write-path смены IP), и сам watchdog.

Самодостаточный single-file (только stdlib, без относительных импортов) —
грузится и как `from subscription.cf_api import CloudflareDNS`, и по пути через
importlib для co-deploy рядом со standalone-скриптами.

ВНИМАНИЕ: watchdog.py пока держит ИНЛАЙН-копию этих функций (он деплоится одним
файлом на home-сервер). При правках логики Cloudflare синхронизировать оба места
до момента, пока watchdog не перейдёт на co-deploy этого модуля (Phase 2).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("cf-api")

CF_API = "https://api.cloudflare.com/client/v4"


class CloudflareError(RuntimeError):
    pass


class CloudflareDNS:
    """тонкий клиент CF DNS: чтение/патч A-записи, парковка домена на IP."""

    def __init__(self, token: str, zone_id: str, *, timeout: float = 15.0):
        if not token or not zone_id:
            raise CloudflareError("CF token and zone_id are required")
        self.token = token
        self.zone_id = zone_id
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{CF_API}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise CloudflareError(f"CF API {method} {path}: {e.code} {e.read().decode()}") from e

    def get_record(self, name: str) -> dict:
        resp = self._request("GET", f"/zones/{self.zone_id}/dns_records?type=A&name={name}")
        records = resp.get("result", [])
        if not records:
            raise CloudflareError(f"A-запись {name} не найдена в Cloudflare")
        return records[0]

    def get_current_ip(self, name: str) -> str:
        return str(self.get_record(name).get("content", "")).strip()

    def update_record(self, record_id: str, new_ip: str, *, ttl: int = 60, proxied: bool = False) -> None:
        self._request("PATCH", f"/zones/{self.zone_id}/dns_records/{record_id}", {
            "content": new_ip,
            "ttl": ttl,
            "proxied": proxied,
        })
        log.info("cloudflare: A record %s → %s", record_id, new_ip)

    def park_domain(self, name: str, new_ip: str) -> bool:
        """указать A-запись `name` на `new_ip`. True если изменили, False если уже так."""
        record = self.get_record(name)
        current_ip = str(record.get("content", "")).strip()
        if current_ip == new_ip:
            log.info("cloudflare: A %s уже %s", name, new_ip)
            return False
        self.update_record(record["id"], new_ip)
        log.info("cloudflare: A %s → %s (был %s)", name, new_ip, current_ip)
        return True
