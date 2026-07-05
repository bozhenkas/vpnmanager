#!/usr/bin/env python3
"""Собирает единый актуальный RU-direct роутинг из ВСЕХ источников.

Источники:
  - subscription/engine.py  : RU_DIRECT_SITES, RU_DIRECT_IPS (клиентский Happ-роутинг)
  - bot/ru_direct_domains.py: RU_DIRECT_DOMAINS (детальные сабдомены, src 1984.is)
  - sub-updater/updater.py  : _RU_HOME_MAC_DOMAINS, _RU_CUSTOM_DIRECT_DOMAINS, _RU_IP_LEAK_DOMAINS
  - живая подписка hydra (whitestore) — /tmp/hydra.json (direct rules)
  - CURATED_EXTRA          : VK-хранилища/CDN + Yandex/Mail на .com/.net/.me (regexp .ru их НЕ ловит)

Выход:
  - tmp/ru-direct-routing.happ.json   — Happ routing profile (DirectSites/DirectIp)
  - tmp/ru-direct-domains.txt         — плоский список доменов (для xray geosite-стиля)
"""
from __future__ import annotations
import importlib.util, json, os, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def norm(d: str) -> str:
    """нормализуем к виду domain:X / geosite:X / regexp:X / ip оставляем как есть."""
    d = d.strip()
    if not d:
        return ""
    if d.startswith(("geosite:", "regexp:", "keyword:", "full:", "ext:", "domain:")):
        return d
    # голый домен -> domain:
    return "domain:" + d.lower()


# --- VK-хранилища/CDN + Yandex/Mail/прочие RU на не-.ru TLD (главное, что не ловит regexp .ru) ---
CURATED_EXTRA = [
    # VK ecosystem storage/CDN
    "domain:userapi.com",        # sun*-*.userapi.com (фото), psv*.userapi.com (видео-хранилище)
    "domain:vk-cdn.net", "domain:vkuser.net", "domain:vkuservideo.net",
    "domain:vkuserlive.net", "domain:vk-portal.net", "domain:vk.me",
    "domain:vkmessenger.com", "domain:vkmessenger.app", "domain:mycdn.me",
    "domain:vkcdn.ru", "domain:vkgroup.net", "domain:vkforms.ru",
    "domain:vkvideo.ru", "domain:vkplay.ru", "domain:vkplay.live",
    "domain:vk.company", "domain:vk-apps.com", "domain:vkapps.com",
    # Mail.ru Group
    "domain:my.com", "domain:imgsmail.ru", "domain:mradar.imgsmail.ru",
    "domain:mailru.com", "domain:datacloudmail.ru", "domain:mcs.mail.ru",
    "domain:hb.bizmrg.com",      # VK Cloud / Mail object storage
    # Yandex .com/.net/.st
    "domain:yandexcloud.net", "domain:yastatic.net", "domain:yandex.net",
    "domain:yandex.com", "domain:yandex.st", "domain:mdst.yandex.net",
    "domain:storage.yandexcloud.net", "domain:appmetrica.yandex.net",
    "domain:dzeninfra.ru", "domain:zen.yandex.ru",
    # банки/финтех на не-.ru
    "domain:tbank-online.com", "domain:tcsbank.ru", "domain:sberbank.com",
    "domain:tochka.com", "domain:tochka-tech.com", "domain:gpb.ru",
    "domain:sberdevices.ru", "domain:sbercloud.ru", "domain:cloud.ru",
    # маркетплейсы/CDN на не-.ru
    "domain:ozonusercontent.com", "domain:ozone.ru", "domain:wbbasket.ru",
    "domain:wbcdn.net", "domain:wbstatic.net", "domain:2gis.com",
    "domain:cdnvideo.com", "domain:cdnvideo.ru", "domain:ngenix.net",
    "domain:gcorelabs.com", "domain:gcore.com",   # RU-origin CDN
    "domain:aliexpress.ru",
    # медиа/прочее RU на не-.ru
    "domain:habr.com", "domain:max.ru",
]

# IP-наборы: RU-сети крупных провайдеров/банков (для IP-литералов и обхода geoip-промахов)
CURATED_IPS = [
    "geoip:ru",
    # LAN / private (как в hydra) — локалка всегда direct
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "127.0.0.0/8", "224.0.0.0/4", "::1/128", "fc00::/7", "fe80::/10",
    # T-Bank / банки (из hydra)
    "212.233.73.228/32", "212.233.73.46/32", "85.192.34.0/23",
    # Yandex
    "5.45.192.0/18", "5.255.192.0/18", "37.9.64.0/18", "77.88.0.0/18",
    "87.250.224.0/19", "93.158.128.0/18", "95.108.128.0/17",
    "213.180.192.0/21", "178.154.128.0/18",
    # VK / Mail.ru
    "87.240.128.0/18", "95.142.192.0/20", "185.32.187.0/24", "217.20.144.0/20",
]


def main():
    domains: set[str] = set()
    ips: set[str] = set()

    # 1. engine.py
    eng = load("subscription/engine.py", "eng")
    for d in eng.RU_DIRECT_SITES:
        domains.add(norm(d))
    for ip in eng.RU_DIRECT_IPS:
        ips.add(ip.strip())

    # 2. ru_direct_domains.py
    rdd = load("bot/ru_direct_domains.py", "rdd")
    for d in rdd.RU_DIRECT_DOMAINS:
        domains.add(norm(d))

    # 3. sub-updater
    os.environ.setdefault("SUB_UPDATER_LOG_PATH", "/tmp/su.log")
    su = load("sub-updater/updater.py", "su")
    IPV4 = re.compile(r"^domain:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(/\d+)?$")
    for lst in (su._RU_HOME_MAC_DOMAINS, su._RU_CUSTOM_DIRECT_DOMAINS, su._RU_IP_LEAK_DOMAINS):
        for d in lst:
            if IPV4.match(d):                    # настоящий «domain:IP» -> в ips
                ips.add(d.split(":", 1)[1])
            else:
                domains.add(norm(d))

    # 4. hydra live (если есть дамп)
    hy = pathlib.Path("/tmp/hydra.json")
    if hy.exists():
        cfg = json.load(open(hy))[0]
        for r in cfg.get("routing", {}).get("rules", []):
            if r.get("outboundTag") == "direct":
                for d in r.get("domain", []):
                    # gaming geosites не «русские» — пропускаем
                    if d in ("geosite:epicgames", "geosite:riot", "geosite:steam", "geosite:twitch"):
                        continue
                    domains.add(norm(d))
                for ip in r.get("ip", []):
                    if not ip.startswith(("10.", "127.", "172.16", "192.168", "169.254", "::1", "fc00", "fe80")):
                        ips.add(ip)

    # 5. curated
    for d in CURATED_EXTRA:
        domains.add(norm(d))
    for ip in CURATED_IPS:
        ips.add(ip)

    # широкие RU-TLD правила — гарантируют ВСЕ .ru/.su/.рф напрямую
    for r in ("geosite:category-ru", "regexp:.*\\.ru$", "regexp:.*\\.su$",
              "regexp:.*\\.xn--p1ai$", "domain:ru", "domain:su", "domain:xn--p1ai"):
        domains.add(r)

    domains.discard("")

    # нормализуем IP: голый IPv4 -> /32 (убирает дубли «X» и «X/32»)
    norm_ips: set[str] = set()
    for ip in ips:
        ip = ip.strip()
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
            ip += "/32"
        norm_ips.add(ip)
    ips = norm_ips

    # схлопывание: если есть domain:X, убираем domain:Y где Y — поддомен X
    bare = {d.split(":", 1)[1] for d in domains if d.startswith("domain:")}
    parents = sorted(bare, key=len)
    keep_bare: set[str] = set()
    for b in parents:
        if any(b == p or b.endswith("." + p) for p in keep_bare):
            continue
        keep_bare.add(b)
    domains = {d for d in domains if not d.startswith("domain:")} | {"domain:" + b for b in keep_bare}

    # сортировка: geosite/regexp вперёд, потом domain:
    def sortkey(x):
        pri = 0 if x.startswith(("geosite:", "regexp:")) else 1
        return (pri, x)
    dom_sorted = sorted(domains, key=sortkey)
    ip_sorted = sorted(ips, key=lambda x: (0 if x.startswith("geoip:") else 1, x))

    out_dir = ROOT / "tmp"
    out_dir.mkdir(exist_ok=True)

    # Happ routing profile (берём базовый из engine, заменяем Direct*)
    import copy
    profile = copy.deepcopy(eng.HAPP_ROUTING_PROFILE)
    profile["Name"] = "goida.fun - RU direct (full)"
    profile["DirectSites"] = dom_sorted
    profile["DirectIp"] = ip_sorted
    (out_dir / "ru-direct-routing.happ.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "ru-direct-domains.txt").write_text("\n".join(dom_sorted) + "\n", encoding="utf-8")

    # --- полный xray routing JSON c geoip-проверкой ---
    # geoip:ru уже включён в ip_sorted (через CURATED_IPS), отделим private
    ip_ru = [x for x in ip_sorted if not x.startswith(("10.", "127.", "172.16", "192.168",
              "169.254.", "224.", "255.255", "::1", "fc00", "fe80"))]
    xray_routing = {
        "routing": {
            "domainStrategy": "IPIfNonMatch",   # неопознанный домен -> резолв -> сверка с geoip:ru
            "domainMatcher": "hybrid",
            "rules": [
                {"type": "field", "outboundTag": "direct", "ip": ["geoip:private"]},
                {"type": "field", "outboundTag": "direct", "domain": dom_sorted},
                {"type": "field", "outboundTag": "direct", "ip": ip_ru},
                {"type": "field", "outboundTag": "proxy", "network": "tcp,udp"},
            ],
        }
    }
    (out_dir / "ru-direct-routing.xray.json").write_text(
        json.dumps(xray_routing, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"domains: {len(dom_sorted)}  ips: {len(ip_sorted)}  (geoip:ru + RU-сети: {len(ip_ru)})")
    print("written: tmp/ru-direct-routing.happ.json, tmp/ru-direct-routing.xray.json, tmp/ru-direct-domains.txt")
    # сводка по «не-.ru» доменам (то, что критично — regexp .ru их не ловит)
    nonru = [d for d in dom_sorted if d.startswith("domain:") and not d.endswith((".ru", ".su"))
             and not d.split(":",1)[1].replace(".","").isdigit()]
    print(f"\nнЕ-.ru доменов (VK-CDN/банки/yandex .com/.net и т.п.): {len(nonru)}")
    for d in nonru:
        print(" ", d)


if __name__ == "__main__":
    main()
