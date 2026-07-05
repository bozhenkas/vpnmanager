#!/usr/bin/env python3
"""
parse_subs.py — парсер сторонних VPN-подписок (xray JSON-config array + classic base64).
Извлекает: серверы, routing rules (direct/proxy домены и IP), DNS-конфиги.
Выход: JSON-дамп в stdout + прогресс в stderr.
"""
from __future__ import annotations

import base64
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

HWID = "up8jf5kjyrzi0013"
UA   = f"Happ/2.1.8/ios/{HWID}"

SUBS = [
    "https://sub.whitestore.club/aNcof2mEn8Gx2npf",
    "https://your-durev.com/sub/Tnb7Jac452ZQpIMeiHyubV",
    "https://sub.9142858.xyz/SQLzPLNuRWqpt4Gk",
    "https://net4.su/keys/5856873492/ZOKQG",
    "https://1984-mini-app.bot.nu/FVKYVRUg34ovmMAp",
    "https://sub.fenvpn.ru/8E904vtb63spAWmn",
]

HAPP_ROUTING_PREFIX = "happ://routing/onadd/"


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_raw(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "X-HWID": HWID, "Accept": "*/*"},
    )
    context = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
        return r.read()


def decode_response(raw: bytes) -> Any:
    """
    Детектирует формат:
      - JSON (массив конфигов или один конфиг)
      - base64-кодированный plain-text (vless:// строки)
      - plain-text vless:// строки
    Возвращает (format_name, data).
    """
    stripped = raw.lstrip()

    # JSON array или object
    if stripped[:1] in (b"[", b"{"):
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
            return "json", data
        except Exception:
            pass

    # base64 → text
    try:
        decoded = base64.b64decode(raw + b"==").decode("utf-8")
        return "plain", decoded
    except Exception:
        pass

    # plain text как есть
    return "plain", raw.decode("utf-8", errors="replace")


# ── xray/sing-box JSON config parser ─────────────────────────────────────────

def _tag_is_direct(tag: str) -> bool:
    tag = (tag or "").lower()
    return tag in ("direct", "bypass", "freedom")


def _tag_is_proxy(tag: str) -> bool:
    tag = (tag or "").lower()
    return tag not in ("direct", "bypass", "freedom", "block", "blocked",
                       "blackhole", "dns-out", "dns", "api", "")


def parse_xray_config(cfg: dict) -> dict:
    """Разбирает один xray/sing-box JSON-конфиг."""
    servers: list[dict] = []
    direct_domains: list[str] = []
    proxy_domains:  list[str] = []
    direct_ips:     list[str] = []
    proxy_ips:      list[str] = []
    dns_info:       dict = {}

    # ── outbounds → серверы ──
    for ob in cfg.get("outbounds", []):
        proto = ob.get("protocol") or ob.get("type") or ""
        tag   = ob.get("tag") or ""
        if proto in ("vless", "vmess", "trojan", "hysteria2", "hy2", "shadowsocks", "ss", "tuic"):
            server = _parse_outbound(ob)
            if server:
                server["tag"] = tag
                servers.append(server)

    # ── routing ──
    routing = cfg.get("routing") or {}
    rules   = routing.get("rules", [])
    for rule in rules:
        # xray стиль
        outbound_tag = rule.get("outboundTag") or rule.get("outbound") or ""
        domains      = rule.get("domain") or rule.get("domain_suffix") or rule.get("domains") or []
        ips          = rule.get("ip") or rule.get("ip_cidr") or []

        if isinstance(domains, str):
            domains = [domains]
        if isinstance(ips, str):
            ips = [ips]

        if _tag_is_direct(outbound_tag):
            direct_domains.extend(domains)
            direct_ips.extend(ips)
        elif _tag_is_proxy(outbound_tag):
            proxy_domains.extend(domains)
            proxy_ips.extend(ips)

    # ── DNS ──
    dns = cfg.get("dns") or {}
    servers_dns = dns.get("servers") or []
    dns_servers = []
    for s in servers_dns:
        if isinstance(s, str):
            dns_servers.append(s)
        elif isinstance(s, dict):
            addr = s.get("address") or s.get("tag") or ""
            entry = {"address": addr}
            if s.get("domains"):
                entry["domains"] = s["domains"]
            if s.get("detour"):
                entry["detour"] = s["detour"]
            dns_servers.append(entry)
    hosts = dns.get("hosts") or {}
    dns_info = {"servers": dns_servers, "hosts": hosts,
                "queryStrategy": dns.get("queryStrategy") or dns.get("strategy") or ""}

    return {
        "servers":        servers,
        "direct_domains": direct_domains,
        "proxy_domains":  proxy_domains,
        "direct_ips":     direct_ips,
        "proxy_ips":      proxy_ips,
        "dns":            dns_info,
    }


def _parse_outbound(ob: dict) -> dict | None:
    proto = ob.get("protocol") or ob.get("type") or ""

    # xray vnext
    if "settings" in ob:
        settings = ob["settings"]
        vnext = settings.get("vnext") or []
        for v in vnext:
            for user in v.get("users", []):
                result = {
                    "proto":  proto,
                    "host":   v.get("address", ""),
                    "port":   v.get("port", 443),
                    "flow":   user.get("flow", ""),
                    "uuid":   user.get("id") or user.get("uuid") or user.get("password") or "",
                }
                stream = ob.get("streamSettings") or {}
                result["network"]  = stream.get("network", "tcp")
                result["security"] = stream.get("security", "none")
                result["stream_settings"] = stream
                if result["security"] == "reality":
                    rs = stream.get("realitySettings") or {}
                    result["sni"] = (rs.get("serverName") or
                                     (rs.get("serverNames") or [""])[0])
                    result["pbk"] = (rs.get("publicKey") or
                                     rs.get("settings", {}).get("publicKey") or "")
                    result["sid"] = ((rs.get("shortIds") or [""])[0] or
                                     rs.get("shortId") or "")
                elif result["security"] == "tls":
                    ts = stream.get("tlsSettings") or {}
                    result["sni"] = ts.get("serverName", "")
                result["remark"] = ob.get("_remark") or ob.get("tag") or ""
                return result
        # servers (shadowsocks style)
        servers = settings.get("servers") or []
        for s in servers:
            return {
                "proto":    proto,
                "host":     s.get("address", ""),
                "port":     s.get("port", 443),
                "method":   s.get("method", ""),
                "network":  "tcp",
                "security": "none",
                "remark":   ob.get("tag", ""),
            }

    # sing-box стиль (server / server_port на верхнем уровне)
    if "server" in ob:
        result = {
            "proto":    proto,
            "host":     ob.get("server", ""),
            "port":     ob.get("server_port") or ob.get("port") or 443,
            "uuid":     ob.get("uuid") or ob.get("password") or "",
            "flow":     ob.get("flow") or "",
            "network":  ob.get("network") or ob.get("transport", {}).get("type", "tcp"),
            "security": "tls" if ob.get("tls") else "none",
            "remark":   ob.get("tag", ""),
        }
        if ob.get("tls") and isinstance(ob["tls"], dict):
            result["sni"] = ob["tls"].get("server_name", "")
        return result

    return None


# ── plain-text vless:// subscription ─────────────────────────────────────────

def parse_plain_vless(text: str) -> dict:
    servers: list[dict] = []
    happ_routings: list[dict] = []

    for line in text.splitlines():
        line = line.strip()
        if line.startswith(HAPP_ROUTING_PREFIX):
            try:
                payload = line[len(HAPP_ROUTING_PREFIX):]
                padded  = payload + "=" * (-len(payload) % 4)
                r = json.loads(base64.b64decode(padded.encode()).decode())
                happ_routings.append(r)
            except Exception:
                pass
        elif line.startswith("vless://") and "0.0.0.0" not in line:
            s = _parse_vless_url(line)
            if s:
                servers.append(s)

    return {"servers": servers, "happ_routings": happ_routings}


def _parse_vless_url(url: str) -> dict | None:
    try:
        p  = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(p.query)

        def one(k: str, d: str = "") -> str:
            v = qs.get(k)
            return v[0] if v else d

        network  = one("type", "tcp")
        security = one("security", "none")
        r: dict = {
            "proto":    "vless",
            "host":     p.hostname or "",
            "port":     p.port or 443,
            "uuid":     p.username or "",
            "flow":     one("flow"),
            "network":  network,
            "security": security,
            "sni":      one("sni"),
            "remark":   urllib.parse.unquote(p.fragment or "").strip(),
        }
        if security == "reality":
            r["pbk"] = one("pbk")
            r["sid"] = one("sid")
        return r
    except Exception:
        return None


# ── aggregate ─────────────────────────────────────────────────────────────────

def aggregate_sub(raw: bytes, source_url: str) -> dict:
    fmt, data = decode_response(raw)
    servers:        list[dict] = []
    direct_domains: list[str]  = []
    proxy_domains:  list[str]  = []
    direct_ips:     list[str]  = []
    proxy_ips:      list[str]  = []
    happ_routings:  list[dict] = []
    dns_configs:    list[dict] = []

    if fmt == "json":
        configs = data if isinstance(data, list) else [data]
        for cfg in configs:
            if not isinstance(cfg, dict):
                continue
            parsed = parse_xray_config(cfg)
            servers.extend(parsed["servers"])
            direct_domains.extend(parsed["direct_domains"])
            proxy_domains.extend(parsed["proxy_domains"])
            direct_ips.extend(parsed["direct_ips"])
            proxy_ips.extend(parsed["proxy_ips"])
            if parsed["dns"]["servers"]:
                dns_configs.append(parsed["dns"])

    elif fmt == "plain":
        result = parse_plain_vless(data)
        servers.extend(result["servers"])
        happ_routings.extend(result["happ_routings"])
        for r in result["happ_routings"]:
            direct_domains.extend(r.get("DirectSites", []))
            proxy_domains.extend(r.get("ProxySites", []))
            direct_ips.extend(r.get("DirectIp", []))

    return {
        "source":         source_url,
        "format":         fmt,
        "servers":        servers,
        "direct_domains": list(dict.fromkeys(direct_domains)),
        "proxy_domains":  list(dict.fromkeys(proxy_domains)),
        "direct_ips":     list(dict.fromkeys(direct_ips)),
        "proxy_ips":      list(dict.fromkeys(proxy_ips)),
        "happ_routings":  happ_routings,
        "dns_configs":    dns_configs,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    all_direct_domains: set[str] = set()
    all_proxy_domains:  set[str] = set()
    all_direct_ips:     set[str] = set()
    all_proxy_ips:      set[str] = set()
    all_servers:        list[dict] = []

    sub_results: list[dict] = []

    for url in SUBS:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Fetching: {url}", file=sys.stderr)
        try:
            raw   = fetch_raw(url)
            result = aggregate_sub(raw, url)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            sub_results.append({"source": url, "error": str(e)})
            continue

        all_direct_domains |= set(result["direct_domains"])
        all_proxy_domains  |= set(result["proxy_domains"])
        all_direct_ips     |= set(result["direct_ips"])
        all_proxy_ips      |= set(result["proxy_ips"])
        all_servers.extend(result["servers"])

        print(f"  format:         {result['format']}", file=sys.stderr)
        print(f"  servers:        {len(result['servers'])}", file=sys.stderr)
        print(f"  direct_domains: {len(result['direct_domains'])}", file=sys.stderr)
        print(f"  direct_ips:     {len(result['direct_ips'])}", file=sys.stderr)
        print(f"  proxy_domains:  {len(result['proxy_domains'])}", file=sys.stderr)
        print(f"  dns_configs:    {len(result['dns_configs'])}", file=sys.stderr)
        for s in result["servers"][:5]:
            print(
                f"  -> {s.get('remark','')[:45]:45s}  "
                f"{s.get('host','')[:35]:35s}:{s.get('port',0)}  "
                f"{s.get('network','')}/{s.get('security','')}",
                file=sys.stderr,
            )
        if len(result["servers"]) > 5:
            print(f"  ... и ещё {len(result['servers'])-5}", file=sys.stderr)
        if result["direct_domains"][:5]:
            print(f"  direct sample: {result['direct_domains'][:5]}", file=sys.stderr)

        sub_results.append(result)
        time.sleep(0.5)

    # дедупликация серверов по host:port
    seen: set[str] = set()
    unique_servers: list[dict] = []
    for s in all_servers:
        key = f"{s.get('host','')}:{s.get('port',0)}"
        if key not in seen:
            seen.add(key)
            unique_servers.append(s)

    merged = {
        "direct_domains":      sorted(all_direct_domains),
        "proxy_domains":       sorted(all_proxy_domains),
        "direct_ips":          sorted(all_direct_ips),
        "proxy_ips":           sorted(all_proxy_ips),
        "unique_servers":      unique_servers,
        "stats": {
            "direct_domains_count":  len(all_direct_domains),
            "proxy_domains_count":   len(all_proxy_domains),
            "direct_ips_count":      len(all_direct_ips),
            "proxy_ips_count":       len(all_proxy_ips),
            "unique_servers_count":  len(unique_servers),
        },
    }

    output = {"subs": sub_results, "aggregated": merged}
    print(json.dumps(output, ensure_ascii=False, indent=2))

    print(f"\n{'='*60}", file=sys.stderr)
    print("ИТОГО:", file=sys.stderr)
    for k, v in merged["stats"].items():
        print(f"  {k}: {v}", file=sys.stderr)


if __name__ == "__main__":
    main()
