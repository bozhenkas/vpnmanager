"""
subscription/ru_routing.py — единый источник правды для клиентского роутинга.

Принцип: «все российские сервисы идут НАПРЯМУЮ, всё остальное через VPN».
Попадание русского трафика во VPN = риск блокировки по ТСПУ.

Почему НЕ нужно перечислять каждый поддомен:
  "domain:X" в xray = suffix match: покрывает X и *.X рекурсивно.
  "domain:ru" = ВСЕ *.ru без исключений (mail.ru, yandex.ru, hh.ru, ivi.ru...).
  "domain:userapi.com" = sun1-13.userapi.com, sun9-88.userapi.com и т.д. — всё.
  geosite:vk покрывает vk.com, userapi.com, mycdn.me и сотни CDN-доменов VK.
  geosite:yandex покрывает yandex.net, yastatic.net, yandexcloud.net и т.д.
  Явные записи нужны только для иностранных TLD, которых нет в geosite.

Запустить для генерации JSON:
    python3 subscription/ru_routing.py > routing.json
"""
from __future__ import annotations

import copy
import json
import sys

# Версия — обновлять при изменении списков для отслеживания в git
ROUTING_VERSION = "2026-07-02-r1"

# Авто-детект RU-серверов (промоут из ru-geo-analyzer → candidates.json).
# Курируемый список ниже и авто-список держатся раздельно. Файл может
# отсутствовать на свежем чекауте — тогда деградируем до пустого.
try:
    from subscription.ru_direct_auto import (  # импорт как пакет (engine.py)
        RU_DIRECT_AUTO_DOMAINS as _RU_DIRECT_AUTO_DOMAINS,
        RU_DIRECT_AUTO_IPS as _RU_DIRECT_AUTO_IPS,
    )
except Exception:
    try:
        from ru_direct_auto import (  # запуск как скрипт из subscription/
            RU_DIRECT_AUTO_DOMAINS as _RU_DIRECT_AUTO_DOMAINS,
            RU_DIRECT_AUTO_IPS as _RU_DIRECT_AUTO_IPS,
        )
    except Exception:
        _RU_DIRECT_AUTO_DOMAINS, _RU_DIRECT_AUTO_IPS = [], []

# ── УРОВЕНЬ 0: TLD suffix rules ──────────────────────────────────────────────
# ОДНО правило = ВЕСЬ TLD + все поддомены. Самый мощный уровень.
# domain:ru покрывает: mail.ru, yandex.ru, vk.ru, hh.ru, sber.ru, ivi.ru...
# НЕ нужно перечислять поддомены: они уже покрыты suffix-match.

_TIER0_TLD = [
    "domain:ru",         # все .ru домены без исключений
    "domain:su",         # советский TLD (lib.ru, sovmusic.ru и т.п.)
    "domain:xn--p1ai",   # .рф (IDN Punycode)
    "domain:moscow",     # .москва
    "domain:tatar",      # .татар
]

# ── УРОВЕНЬ 1: geosite экосистемы ────────────────────────────────────────────
# geosite.dat поставляется с xray и обновляется вместе с клиентом.
# geosite:vk      = vk.com, userapi.com, mycdn.me, vkplay.live + сотни поддоменов
# geosite:yandex  = yandex.net, yandex.com, yastatic.net, yandexcloud.net + CDN
# geosite:category-ru = обобщённый список .ru-зоны (backup к domain:ru)
# geosite:category-gov-ru = gov.ru, kremlin.ru, gosuslugi.ru, mos.ru и т.п.

_TIER1_GEOSITE = [
    "geosite:category-ru",       # backup к domain:ru для старых клиентов
    "geosite:category-gov-ru",   # государственные домены
    "geosite:vk",                # полная VK-экосистема включая CDN на иностр. TLD
    "geosite:yandex",            # полная Yandex-экосистема включая CDN на иностр. TLD
]

# ── УРОВЕНЬ 2: VK / ВКонтакте (иностранные TLD) ──────────────────────────────
# geosite:vk их уже покрывает; явный список — страховка для клиентов
# с устаревшим geosite.dat или кастомными сборками xray.
# НЕ НУЖНО: sun1-13.userapi.com, sun9-88.userapi.com — domain:userapi.com покрывает всё.

_TIER2_VK = [
    "domain:vk.com",             # основной домен VK (также в geosite:vk)
    "domain:userapi.com",        # CDN фото/видео — ВСЕ sun*.userapi.com покрыты автоматически
    "domain:vk-cdn.net",
    "domain:vkuser.net",
    "domain:vkuservideo.net",
    "domain:vkuseraudio.net",
    "domain:vkuserlive.net",
    "domain:vk-portal.net",
    "domain:vk.me",
    "domain:vkmessenger.com",
    "domain:vkmessenger.app",
    "domain:mycdn.me",           # OK.ru CDN (все *.mycdn.me)
    "domain:vkgroup.net",
    "domain:vk-apps.com",
    "domain:vkapps.com",
    "domain:vk.company",
    "domain:vkplay.live",
    "domain:mradx.net",          # VK рекламная сеть
]

# ── УРОВЕНЬ 3: Mail.ru Group (иностранные TLD) ────────────────────────────────
# mail.ru и все его поддомены покрыты domain:ru выше.
# Здесь только то, что на иностранных TLD.

_TIER3_MAILRU = [
    "domain:my.com",
    "domain:my.games",
    "domain:mailru.com",
    "domain:hb.bizmrg.com",      # Mail.ru Cloud объектное хранилище
]

# ── УРОВЕНЬ 4: Yandex (иностранные TLD) ──────────────────────────────────────
# yandex.ru и ВСЕ *.yandex.ru покрыты domain:ru.
# geosite:yandex покрывает иностранные TLD; явный список — страховка.
# НЕ НУЖНО перечислять: cloudcdn-m9-*.cdn.yandex.net, strm-rad-*.strm.yandex.net —
# domain:yandex.net покрывает все *.yandex.net автоматически.

_TIER4_YANDEX = [
    "domain:yandex.com",
    "domain:yandex.net",         # покрывает yastatic, strm, cdn, mc, s3 и т.д.
    "domain:yandex.st",          # статика (Yandex Static)
    "domain:yastatic.net",       # фронтенд-бандлы Яндекса (CDN)
    "domain:yandexcloud.net",
    "domain:yandexcloud.com",
    "domain:go.yandex",          # Яндекс GO/Такси
]

# ── УРОВЕНЬ 5: Банки и финтех (иностранные TLD) ───────────────────────────────
# .ru-версии (tinkoff.ru, vtb.ru, sberbank.ru, alfabank.ru) покрыты domain:ru.

_TIER5_BANKS = [
    "domain:tbank-online.com",
    "domain:sberbank.com",
    "domain:tochka.com",
    "domain:tochka-tech.com",
    "domain:qiwi.com",
    "domain:moex.com",           # Московская биржа
]

# ── УРОВЕНЬ 6: Маркетплейсы и ритейл (иностранные TLD) ──────────────────────
# ozon.ru, wildberries.ru, avito.ru — покрыты domain:ru.
# domain:avito.st покрывает 00.img.avito.st..99.img.avito.st — все сразу.
# domain:wbstatic.net покрывает static.nm-static.wbstatic.net и т.д.

_TIER6_RETAIL = [
    "domain:avito.st",           # CDN статика Avito (покрывает NN.img.avito.st)
    "domain:ozonusercontent.com",
    "domain:wbcdn.net",
    "domain:wbstatic.net",       # покрывает static.nm-static.wbstatic.net и т.д.
    "domain:wildberries.by",
    "domain:sbermarket.com",
    "domain:lmru.tech",          # Leroy Merlin CDN
]

# ── УРОВЕНЬ 7: CDN / инфраструктура российского рунета ───────────────────────
# domain:2gis.com покрывает tile0-4.maps.2gis.com, i0-i9.photo.2gis.com и т.д.

_TIER7_CDN = [
    "domain:2gis.com",           # покрывает все *.2gis.com поддомены
    "domain:cdnvideo.com",
    "domain:ngenix.net",
    "domain:gcore.com",          # G-Core Labs (РФ CDN)
    "domain:gcorelabs.com",
]

# ── УРОВЕНЬ 8: Медиа и стриминг (иностранные TLD) ────────────────────────────
# ivi.ru, kion.ru, wink.ru, yappy.ru, rutube.ru — покрыты domain:ru.
# Здесь только сервисы с иностранными TLD.

_TIER8_MEDIA = [
    "domain:okko.tv",
    "domain:premier.one",
    "domain:more.tv",
    "domain:smotreshka.tv",
    "domain:lenta.com",
    "domain:habr.com",
    "domain:gismeteo.com",
]

# ── УРОВЕНЬ 9: Прочие российские сервисы (иностранные TLD) ───────────────────

_TIER9_MISC = [
    "domain:onetwotrip.com",     # OneTwoTrip (авиа/отели)
    "domain:pobeda.aero",        # Победа авиа
    "domain:boosty.to",          # российский Patreon-аналог
    "domain:funpay.com",         # игровые товары/аккаунты
    "domain:mangalib.org",
    "domain:api.cdnlibs.org",
    "domain:2ip.io",             # определение IP (bypass — пусть видит реальный IP)
    "domain:whoer.net",
]

# ── УРОВЕНЬ 10: Epic Games / Unreal Engine + NVIDIA GFE ──────────────────────
# Источники:
#   Домены — из access-лога soll (email:23), 2026-06-24.
#   IP-префиксы — AS26667 + AS11243 (RADB, 2026-06-24); покрывают auth/API/online-services.
#   CDN-загрузки (Akamai, CloudFront) частично прибиты доменом epicgamescdn.com
#   и epicgames-download1.akamaized.net — остальное доберём по мере логов soll.
#
# "domain:X" = suffix match: X и все *.X автоматически.
# epicgames.com → account-public-service-prod.ak.epicgames.com, catalog-..., launcher-...
# unrealengine.com → assets, cdn2, cms-assets, components, editor, static-assets...
# epicgamescdn.com → egs-cloudfront-chunks, eosh и другие CDN endpoint'ы
# epicgames-download1.akamaized.net → Akamai CDN для пакетов движка (конкретный хост)
# nvidia.com → GeForce Experience авторизуется при старте UE на Windows; без неё GFE
#   может блокировать запуск редактора / показывать ошибку.
# nvidiagrid.net → NVIDIA Grid (облачный рендеринг/стриминг) — тоже нужен прямой.
# 4game.com → launcherbff.ru.4game.com — российская платформа, но TLD .com,
#   не покрывается domain:ru.

_TIER10_EPIC = [
    "domain:epicgames.com",                     # весь Epic Games + все субдомены
    "domain:unrealengine.com",                  # Unreal Engine + все субдомены
    "domain:epicgames.net",                     # вспомогательный Epic CDN
    "domain:epicgamescdn.com",                  # CDN для загрузок из Marketplace
    "domain:epicgames-download1.akamaized.net", # Akamai CDN пакетов движка
    "domain:nvidia.com",                        # NVIDIA GFE (login, events, ota, ngx, gx, kaizen...)
    "domain:nvidiagrid.net",                    # NVIDIA Grid (static.nvidiagrid.net)
    "domain:4game.com",                         # 4Game launcher (launcherbff.ru.4game.com)
]

# ── УРОВЕНЬ 11: ручные нерусские исключения ─────────────────────────────────
# Не RU-сервисы, но по решению владельца должны идти мимо VPN.

_TIER11_FOREIGN_DIRECT = [
    "domain:gradusi.net",
    "domain:pinterest.com",
    "domain:pinimg.com",                        # картинки/статика Pinterest
]

# ─────────────────────────────────────────────────────────────────────────────
# Итоговые публичные списки

RU_DIRECT_SITES: list[str] = list(dict.fromkeys(
    _TIER0_TLD
    + _TIER1_GEOSITE
    + _TIER2_VK
    + _TIER3_MAILRU
    + _TIER4_YANDEX
    + _TIER5_BANKS
    + _TIER6_RETAIL
    + _TIER7_CDN
    + _TIER8_MEDIA
    + _TIER9_MISC
    + _TIER10_EPIC
    + _TIER11_FOREIGN_DIRECT
    + _RU_DIRECT_AUTO_DOMAINS   # авто-детект geoip-промахов (ru-geo-analyzer)
))

RU_DIRECT_IPS: list[str] = [
    "geoip:ru",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "224.0.0.0/4",
    "255.255.255.255/32",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
    # Epic Games / Unreal Engine (AS26667 + AS11243, RADB 2026-06-24)
    # Покрывают серверы auth/API/online, не CDN. CDN идут через доменные правила выше.
    "8.39.32.0/22",
    "8.39.36.0/23",
    "8.43.72.0/22",
    "62.67.193.0/24",
    "64.173.148.0/22",
    "66.150.149.0/24",
    "69.25.24.0/24",
    "69.173.144.0/20",
    "168.100.170.0/24",
    "191.215.64.0/18",
    "192.203.231.0/24",
    "195.122.175.0/24",
    "210.176.156.0/24",
    "210.176.158.0/24",
    "213.19.162.0/24",
    "216.109.160.0/20",
    "216.19.192.0/20",
    "216.19.208.0/20",
]

# Авто-детект RU-IP/CIDR, пропущенных geoip:ru (промоут из ru-geo-analyzer).
# Дедуп: добавляем только то, чего ещё нет в списке выше.
RU_DIRECT_IPS += [ip for ip in _RU_DIRECT_AUTO_IPS if ip not in RU_DIRECT_IPS]

# YouTube/googlevideo → zapret-путь (Русский сервер, RU-direct + DPI-bypass).
# ВАЖНО: googleapis/gstatic/googleusercontent СЮДА НЕ кладём (hard rule) — иначе ломается.
YOUTUBE_ZAPRET_SITES: list[str] = [
    "geosite:youtube",
    "domain:googlevideo.com",
    "domain:youtube.com",
    "domain:ytimg.com",
    "domain:youtu.be",
]

# Discord → тот же RU/zapret путь, что и YouTube, но без Telegram:
# Telegram direct с RU сейчас режется ТСПУ, его держим через foreign.
DISCORD_ZAPRET_SITES: list[str] = [
    "geosite:discord",
    "domain:discord.com",
    "domain:discord.gg",
    "domain:discordapp.com",
    "domain:discordapp.net",
]

DISCORD_VOICE_PORTS: list[str] = [
    "19294-19344",
    "50000-65535",
]

# Сайты для принудительного прокси в диагностическом режиме
DIAGNOSTIC_PROXY_SITES: list[str] = [
    "domain:2ip.ru",
    "domain:www.2ip.ru",
    "domain:2ip.io",
    "domain:whoer.net",
    "domain:ipinfo.io",
    "domain:api.ipify.org",
]

# Happ routing profile (не активен — все протоколы используют xray JSON routing)
HAPP_ROUTING_PROFILE: dict = {
    "Name": "goida.fun - Smart local",
    "GlobalProxy": "true",
    "RemoteDNSType": "DoH",
    "RemoteDNSDomain": "https://cloudflare-dns.com/dns-query",
    "RemoteDNSIP": "1.1.1.1",
    "DomesticDNSType": "DoH",
    "DomesticDNSDomain": "https://dns.yandex.ru/dns-query",
    "DomesticDNSIP": "77.88.8.8",
    "DirectSites": RU_DIRECT_SITES,
    "DirectIp": RU_DIRECT_IPS,
    "DomainStrategy": "IPIfNonMatch",
    "FakeDNS": "false",
}

HAPP_ROUTING_NEO_PROFILE: dict = {
    **HAPP_ROUTING_PROFILE,
    "Name": "goida neo",
}


def _b64_json(data: dict) -> str:
    import base64
    import json
    return base64.b64encode(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()).decode()


HAPP_ROUTING_NEO_LINE = "happ://routing/onadd/" + _b64_json(HAPP_ROUTING_NEO_PROFILE)


def happ_routing_neo_line(proxy_sites: list[str] | None = None) -> str:
    profile = copy.deepcopy(HAPP_ROUTING_NEO_PROFILE)
    if proxy_sites:
        existing = list(profile.get("ProxySites", []))
        seen = set(existing)
        for site in proxy_sites:
            site = site.strip()
            if site and site not in seen:
                existing.append(site)
                seen.add(site)
        if existing:
            profile["ProxySites"] = existing
    return "happ://routing/onadd/" + _b64_json(profile)


def xray_routing(*, diagnostic_proxy: bool = False) -> dict:
    """
    Полный xray routing блок для клиентской конфигурации.
    Используется в JSON-профилях подписки (generate_json_profile).
    """
    rules: list[dict] = [
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "direct"},
    ]
    if diagnostic_proxy:
        rules.append({"type": "field", "domain": DIAGNOSTIC_PROXY_SITES, "outboundTag": "proxy"})
    rules.extend([
        {"type": "field", "ip": RU_DIRECT_IPS, "outboundTag": "direct"},
        {"type": "field", "domain": RU_DIRECT_SITES, "outboundTag": "direct"},
        {"type": "field", "network": "tcp,udp", "outboundTag": "proxy"},
    ])
    return {
        "domainMatcher": "hybrid",
        "domainStrategy": "IPIfNonMatch",
        "rules": rules,
    }


def as_json() -> dict:
    """Полный дамп для сохранения в routing.json."""
    return {
        "_version": ROUTING_VERSION,
        "_description": "Клиентский роутинг goida VPN. Все RU-сервисы → direct, остальное → proxy.",
        "xray_routing": xray_routing(),
        "xray_routing_diagnostic": xray_routing(diagnostic_proxy=True),
        "stats": {
            "direct_sites": len(RU_DIRECT_SITES),
            "direct_ips": len(RU_DIRECT_IPS),
            "breakdown": {
                "tld_rules": len(_TIER0_TLD),
                "geosite": len(_TIER1_GEOSITE),
                "vk_ecosystem": len(_TIER2_VK),
                "mailru": len(_TIER3_MAILRU),
                "yandex": len(_TIER4_YANDEX),
                "banks": len(_TIER5_BANKS),
                "retail": len(_TIER6_RETAIL),
                "cdn": len(_TIER7_CDN),
                "media": len(_TIER8_MEDIA),
                "misc": len(_TIER9_MISC),
                "epic_games": len(_TIER10_EPIC),
                "foreign_direct": len(_TIER11_FOREIGN_DIRECT),
            },
        },
    }


if __name__ == "__main__":
    json.dump(as_json(), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
