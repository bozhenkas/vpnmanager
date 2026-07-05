#!/usr/bin/env python3
"""
cluster_config.py — единый source-of-truth IP-инвентаря кластера.

Канонический инвентарь живёт в одном JSON (`cluster.json`), хранится
зашифрованным age (multi-recipient) как `cluster.age` и реплицируется на все
ноды. Каждый консьюмер (vpn-bot, infra-backend, ip-watchdog, rkn-checker)
читает ЛОКАЛЬНУЮ расшифрованную копию через `load_cluster()` — поэтому падение
control-plane не роняет data plane.

Свойства безопасности:
- `legacy_blocklist` проверяется при ЛЮБОМ чтении: если в инвентаре всплывает
  забаненный IP (напр. старый RU), загрузка отклоняется → legacy не вернётся.
- подпись HMAC-SHA256 над canonical JSON (секрет рядом с ключом) проверяется
  до доверия payload'у — защита от подмены кэша/блоба на диске.
- decrypt вынесен в тонкую обёртку над бинарём `age`; ядро (parse/verify/
  blocklist/accessors) — чистый stdlib и тестируется на plaintext-фикстурах.

Главное правило вызывающего кода: при ЛЮБОЙ ошибке `load_cluster()` возвращает
None — консьюмер обязан оставить свои текущие (env/hardcoded) дефолты. Так
выкатка модуля до появления `cluster.age`/ключей не меняет поведение.

Модуль самодостаточен (только stdlib, без относительных импортов), поэтому
грузится и как `from subscription.cluster_config import load_cluster`, и по
пути через importlib — как уже делается с watchdog.py в rkn-checker.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("cluster-config")

# IP, который не должен вернуться ни при каких условиях, даже если кто-то впишет
# его в инвентарь руками. Дополняется значениями из самого конфига
# (`legacy_blocklist`) — здесь зашит исторический baseline на случай пустого/
# битого конфига.
HARDCODED_LEGACY_BLOCKLIST = frozenset({"83.147.255.98"})

SCHEMA_VERSION = 1


class ClusterConfigError(Exception):
    """инвентарь не прошёл валидацию/подпись/blocklist — доверять нельзя."""


@dataclass(frozen=True)
class ClusterConfig:
    """распарсенный и провалидированный инвентарь кластера."""

    raw: dict[str, Any]
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    legacy_blocklist: frozenset[str] = field(default_factory=frozenset)

    # --- базовые выборки --------------------------------------------------

    @property
    def version(self) -> int:
        try:
            return int(self.raw.get("version", 0))
        except (TypeError, ValueError):
            return 0

    def server(self, server_id: str) -> dict[str, Any]:
        return self.servers.get(server_id, {})

    def _by_role(self, role: str) -> list[dict[str, Any]]:
        return [s for s in self.servers.values() if s.get("role") == role]

    def _first_ip(self, role: str) -> str:
        for s in self._by_role(role):
            ip = str(s.get("ip", "")).strip()
            if ip:
                return ip
        return ""

    # --- аксессоры для консьюмеров ---------------------------------------

    def primary_cf_ip(self) -> str:
        """IP primary-ингресса (то, что watchdog/CF держит в A-записи)."""
        return self._first_ip("primary")

    def backup_cf_ip(self) -> str:
        """IP backup-ингресса (куда watchdog переключает A-запись при блоке)."""
        return self._first_ip("backup")

    def primary_domain(self) -> str:
        cf = self.raw.get("cloudflare") or {}
        dom = str(cf.get("primary_domain", "")).strip()
        if dom:
            return dom
        for s in self._by_role("primary"):
            if s.get("domain"):
                return str(s["domain"]).strip()
        return ""

    def active_exit_ips(self) -> set[str]:
        """IP активных foreign-exit'ов (FIN/SWE/...)."""
        return {
            str(s["ip"]).strip()
            for s in self._by_role("exit")
            if s.get("status") == "active" and s.get("ip")
        }

    def server_ip_set(self) -> set[str]:
        """внутренний набор IP для bypass-проверок vpn-bot (SERVER_IPS).

        primary + все активные exit'ы. Зеркалит исторический SERVER_IPS, но
        без хардкода — выводится из инвентаря.
        """
        ips = self.active_exit_ips()
        primary = self.primary_cf_ip()
        if primary:
            ips.add(primary)
        return ips

    def ssh_node(self, server_id: str) -> tuple[str, int]:
        s = self.server(server_id)
        host = str(s.get("ssh_host") or s.get("ip") or "").strip()
        port = int(s.get("ssh_port") or 22)
        return host, port

    def exit_domains(self) -> list[str]:
        return [
            str(s["domain"]).strip()
            for s in self._by_role("exit")
            if s.get("domain")
        ]

    def probe_endpoints(self) -> list[dict[str, str]]:
        """эндпоинты для rkn-checker — выводятся из инвентаря, не хардкод.

        Воспроизводит исторический ENDPOINTS: domain, primary_ip, backup_ip и
        по одному на каждый exit-домен.
        """
        endpoints: list[dict[str, str]] = []
        domain = self.primary_domain()
        if domain:
            endpoints.append({"label": "domain", "url": f"https://{domain}/", "status_key": "domain_status"})
        primary = self.primary_cf_ip()
        if primary:
            endpoints.append({"label": "primary_ip", "url": f"https://{primary}/", "status_key": "primary_ip_status"})
        backup = self.backup_cf_ip()
        if backup:
            endpoints.append({"label": "backup_ip", "url": f"https://{backup}/", "status_key": "backup_ip_status"})
        for dom in self.exit_domains():
            endpoints.append({"label": dom, "url": f"https://{dom}/"})
        return endpoints


# ---------------------------------------------------------------------------
# подпись (HMAC-SHA256 над canonical JSON без поля sig)
# ---------------------------------------------------------------------------

def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """детерминированная сериализация для подписи (sig исключается)."""
    body = {k: v for k, v in payload.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), canonical_bytes(payload), hashlib.sha256).hexdigest()


def verify_payload(payload: dict[str, Any], secret: str) -> bool:
    got = str(payload.get("sig", ""))
    if not got:
        return False
    return hmac.compare_digest(got, sign_payload(payload, secret))


# ---------------------------------------------------------------------------
# валидация / parse
# ---------------------------------------------------------------------------

def _collect_ips(servers: dict[str, dict[str, Any]]) -> set[str]:
    ips: set[str] = set()
    for s in servers.values():
        for key in ("ip", "ssh_host"):
            val = str(s.get(key, "")).strip()
            if val:
                ips.add(val)
    return ips


def parse_cluster(
    raw_bytes: bytes,
    *,
    hmac_secret: str | None = None,
    extra_blocklist: Iterable[str] = (),
) -> ClusterConfig:
    """распарсить + провалидировать инвентарь. Бросает ClusterConfigError.

    hmac_secret=None → подпись НЕ проверяется (используется для plaintext-
    бутстрапа и тестов). Если задан — невалидная/отсутствующая подпись отвергается.
    legacy_blocklist проверяется ВСЕГДА.
    """
    try:
        payload = json.loads(raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ClusterConfigError(f"invalid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise ClusterConfigError("cluster config must be a JSON object")

    servers = payload.get("servers")
    if not isinstance(servers, dict) or not servers:
        raise ClusterConfigError("cluster config has no servers")
    for sid, s in servers.items():
        if not isinstance(s, dict):
            raise ClusterConfigError(f"server {sid} is not an object")
        if not str(s.get("ip", "")).strip():
            raise ClusterConfigError(f"server {sid} has no ip")
        if not str(s.get("role", "")).strip():
            raise ClusterConfigError(f"server {sid} has no role")

    if hmac_secret is not None and not verify_payload(payload, hmac_secret):
        raise ClusterConfigError("HMAC signature mismatch")

    blocklist = set(HARDCODED_LEGACY_BLOCKLIST)
    blocklist.update(str(x).strip() for x in (payload.get("legacy_blocklist") or []) if str(x).strip())
    blocklist.update(str(x).strip() for x in extra_blocklist if str(x).strip())

    present = _collect_ips(servers)
    banned = present & blocklist
    if banned:
        raise ClusterConfigError(f"legacy/blocklisted IP present in inventory: {sorted(banned)}")

    return ClusterConfig(raw=payload, servers=servers, legacy_blocklist=frozenset(blocklist))


# ---------------------------------------------------------------------------
# decrypt (тонкая обёртка над age)
# ---------------------------------------------------------------------------

def age_available() -> bool:
    return shutil.which("age") is not None


def decrypt_age(age_path: str | Path, key_path: str | Path, *, timeout: float = 10.0) -> bytes:
    """расшифровать cluster.age приватным ключом ноды через бинарь age.

    Бросает на отсутствии age/файлов или ошибке расшифровки.
    """
    if not age_available():
        raise ClusterConfigError("age binary not found in PATH")
    age_path = Path(age_path)
    key_path = Path(key_path)
    if not age_path.exists():
        raise ClusterConfigError(f"age blob not found: {age_path}")
    if not key_path.exists():
        raise ClusterConfigError(f"age key not found: {key_path}")
    try:
        proc = subprocess.run(
            ["age", "-d", "-i", str(key_path), str(age_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise ClusterConfigError("age decrypt timed out") from e
    if proc.returncode != 0:
        raise ClusterConfigError(f"age decrypt failed: {proc.stderr.decode('utf-8', 'ignore').strip()}")
    return proc.stdout


# ---------------------------------------------------------------------------
# state-кэш (идиома read_state/write_state из watchdog.py)
# ---------------------------------------------------------------------------

def _read_cache(cache_path: Path) -> bytes | None:
    try:
        return cache_path.read_bytes()
    except OSError:
        return None


def _write_cache(cache_path: Path, raw_bytes: bytes) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp.write_bytes(raw_bytes)
        tmp.replace(cache_path)
    except OSError as e:
        log.warning("cluster cache write failed: %s", e)


# ---------------------------------------------------------------------------
# главный загрузчик
# ---------------------------------------------------------------------------

def load_cluster(
    *,
    age_path: str | Path | None = None,
    key_path: str | Path | None = None,
    plain_path: str | Path | None = None,
    cache_path: str | Path | None = None,
    hmac_secret: str | None = None,
    extra_blocklist: Iterable[str] = (),
    on_fallback=None,
) -> ClusterConfig | None:
    """загрузить инвентарь, НИКОГДА не бросает.

    Порядок: plaintext (если задан) → age-расшифровка → last-good кэш.
    При ЛЮБОЙ неудаче возвращает None — вызывающий код оставляет свои дефолты.
    `on_fallback(reason)` зовётся один раз, если пришлось читать кэш или упасть
    (для одноразового tg_alert).
    """
    extra = tuple(extra_blocklist)
    cache = Path(cache_path) if cache_path else None

    # 1. свежий источник: plaintext или age
    raw_bytes: bytes | None = None
    source_err: str | None = None
    try:
        if plain_path and Path(plain_path).exists():
            raw_bytes = Path(plain_path).read_bytes()
        elif age_path and key_path:
            raw_bytes = decrypt_age(age_path, key_path)
    except (ClusterConfigError, OSError) as e:
        source_err = str(e)

    if raw_bytes is not None:
        try:
            cfg = parse_cluster(raw_bytes, hmac_secret=hmac_secret, extra_blocklist=extra)
            if cache:
                _write_cache(cache, raw_bytes)
            return cfg
        except ClusterConfigError as e:
            source_err = str(e)

    # 2. fallback: last-good кэш
    if cache:
        cached = _read_cache(cache)
        if cached is not None:
            try:
                cfg = parse_cluster(cached, hmac_secret=hmac_secret, extra_blocklist=extra)
                msg = f"cluster: using cached inventory (live load failed: {source_err})"
                log.warning(msg)
                if on_fallback:
                    try:
                        on_fallback(msg)
                    except Exception:
                        pass
                return cfg
            except ClusterConfigError as e:
                source_err = f"cache invalid: {e}; live: {source_err}"

    # 3. ничего нет — None, консьюмер держит дефолты
    msg = f"cluster: no usable inventory ({source_err}); falling back to defaults"
    log.warning(msg)
    if on_fallback:
        try:
            on_fallback(msg)
        except Exception:
            pass
    return None


def load_cluster_from_env(*, on_fallback=None) -> ClusterConfig | None:
    """удобный враппер: пути/секрет из env.

    CLUSTER_AGE_PATH / CLUSTER_KEY_PATH / CLUSTER_PLAIN_PATH /
    CLUSTER_CACHE_PATH / CLUSTER_HMAC_SECRET.
    """
    return load_cluster(
        age_path=os.environ.get("CLUSTER_AGE_PATH") or None,
        key_path=os.environ.get("CLUSTER_KEY_PATH") or None,
        plain_path=os.environ.get("CLUSTER_PLAIN_PATH") or None,
        cache_path=os.environ.get("CLUSTER_CACHE_PATH") or None,
        hmac_secret=os.environ.get("CLUSTER_HMAC_SECRET") or None,
        on_fallback=on_fallback,
    )
