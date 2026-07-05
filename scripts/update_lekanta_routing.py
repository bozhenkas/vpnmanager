#!/usr/bin/env python3
"""Атомарно обновляет routing.json Lekanta, сохраняя последний валидный файл.

С 2026-07-05 routing.json больше не публичный (goida закрыл /routing.json из-за
утечки политики роутинга РКН). Эндпоинт переехал на /routing/share?token=...,
защищённый токеном + IP-allowlist по домену lekanta.ru. Токен передаётся через
env ROUTING_SHARE_TOKEN (см. EnvironmentFile в systemd unit), в код не хардкодить.
"""

import json
import logging
import os
import tempfile
import urllib.parse
import urllib.request


BASE_URL = os.environ.get("ROUTING_BASE_URL", "https://ru.goida.fun/routing/share")
SHARE_TOKEN = os.environ.get("ROUTING_SHARE_TOKEN", "")
OUTPUT = os.environ.get("ROUTING_OUTPUT", "/root/vpn-bot/routing.json")
TIMEOUT = float(os.environ.get("ROUTING_TIMEOUT", "15"))


def _url() -> str:
    if not SHARE_TOKEN:
        raise ValueError("ROUTING_SHARE_TOKEN is not set (see EnvironmentFile)")
    return BASE_URL + "?" + urllib.parse.urlencode({"token": SHARE_TOKEN})


def validate(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("root must be an object")
    version = payload.get("_version")
    routing = payload.get("xray_routing")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("_version is missing")
    if not isinstance(routing, dict):
        raise ValueError("xray_routing must be an object")
    if routing.get("domainMatcher") not in ("hybrid", "linear"):
        raise ValueError("xray_routing.domainMatcher is invalid")
    if routing.get("domainStrategy") not in (
        "AsIs", "IPIfNonMatch", "IPOnDemand", "IPIfMatch",
    ):
        raise ValueError("xray_routing.domainStrategy is invalid")
    rules = routing.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("xray_routing.rules must be a non-empty array")
    if not all(isinstance(rule, dict) and rule.get("type") == "field" for rule in rules):
        raise ValueError("every routing rule must be a field object")
    if rules[-1].get("outboundTag") != "proxy":
        raise ValueError("last routing rule must use proxy")
    return payload


def fetch() -> dict:
    request = urllib.request.Request(_url(), headers={"User-Agent": "lekanta-routing-updater/1.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        if response.status != 200:
            raise ValueError(f"unexpected HTTP status {response.status}")
        return validate(json.load(response))


def install(payload: dict) -> None:
    directory = os.path.dirname(OUTPUT)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".routing.", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, OUTPUT)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        payload = fetch()
        current = None
        try:
            with open(OUTPUT, encoding="utf-8") as stream:
                current = validate(json.load(stream))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        if current == payload:
            logging.info("routing unchanged: %s", payload["_version"])
            return 0
        install(payload)
        logging.info("routing updated: %s", payload["_version"])
        return 0
    except Exception as exc:
        logging.warning("routing update skipped, previous file preserved: %s", exc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
