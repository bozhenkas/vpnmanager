from __future__ import annotations

import base64
import copy
import html
import json
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from subscription.ru_routing import (
    DIAGNOSTIC_PROXY_SITES,
    DISCORD_VOICE_PORTS,
    DISCORD_ZAPRET_SITES,
    HAPP_ROUTING_PROFILE,
    HAPP_ROUTING_NEO_PROFILE,
    RU_DIRECT_IPS,
    RU_DIRECT_SITES,
    YOUTUBE_ZAPRET_SITES,
    xray_routing as _xray_routing,
)
from subscription.xhttp import build_xhttp_settings

WL_JSON_PREFIX = "#goida-wl-json:"


def _b64_json(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()


HAPP_ROUTING_LINE = "happ://routing/onadd/" + _b64_json(HAPP_ROUTING_PROFILE)
HAPP_ROUTING_NEO_LINE = "happ://routing/onadd/" + _b64_json(HAPP_ROUTING_NEO_PROFILE)
DEFAULT_DESCRIPTION = (
    "smart — оптимальный сервер.\n"
    "ru-zapret — для ютуба без рекламы (телеграм и дискорд тоже работают)\n"
    "для youtube-shorts рекомендуется выбирать fin/swe, на smart и ru-zapret хорошо работают только длинные видео\n\n"

    "в случае проблем пишите в бота\n"
    "t.me/vpngoidabot"
)
DEFAULT_SUPPORT_URL = "https://t.me/vpngoidabot"
HAPP_DOWNLOAD_LINKS = [
    {
        "platform": "ios",
        "title": "iOS",
        "description": "iPhone и iPad",
        "actions": [
            ("AppStore [ru]", "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973"),
            ("AppStore [global]", "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215"),
        ],
    },
    {
        "platform": "android",
        "title": "Android",
        "description": "телефоны и планшеты",
        "actions": [
            ("Google Play", "https://play.google.com/store/apps/details?id=com.happproxy"),
            ("APK", "https://github.com/Happ-proxy/happ-android/releases/latest"),
        ],
    },
    {
        "platform": "windows",
        "title": "Windows",
        "description": "Windows 10/11",
        "actions": [("GitHub Releases", "https://github.com/Happ-proxy/happ-desktop/releases/latest")],
    },
    {
        "platform": "macos",
        "title": "macOS",
        "description": "Mac Intel и Apple Silicon",
        "actions": [
            ("AppStore [ru]", "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973"),
            ("AppStore [global]", "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215"),
            ("DMG", "https://github.com/Happ-proxy/happ-desktop/releases/latest"),
        ],
    },
    {
        "platform": "appletv",
        "title": "Apple TV",
        "description": "tvOS",
        "actions": [("App Store", "https://apps.apple.com/us/app/happ-proxy-utility-for-tv/id6748297274")],
    },
    {
        "platform": "androidtv",
        "title": "Android TV",
        "description": "TV и приставки",
        "actions": [("Google Play", "https://play.google.com/store/apps/details?id=com.happproxy")],
    },
]


def decode_happ_routing_line(line: str = HAPP_ROUTING_LINE) -> dict[str, Any]:
    payload = line.rsplit("/", 1)[1]
    return json.loads(base64.b64decode(payload).decode())


def happ_routing_line(proxy_sites: list[str] | None = None) -> str:
    profile = copy.deepcopy(HAPP_ROUTING_PROFILE)
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


def deleted_sub_content() -> str:
    return fake_vless_content("пользователь удален", ["пользователь удален", "обратитесь в @vpngoidabot"])


def unsupported_client_content() -> str:
    return "\n".join([
        fake_vless_content("клиент не поддерживается", ["клиент не поддерживается", "скачайте Happ"]),
        "# скачайте Happ",
    ])


def limit_exceeded_content(user_limit: int) -> str:
    return fake_vless_content(
        "goida :) - лимит превышен",
        [
            f"лимит: {user_limit} устройства",
            "купите больше в @vpngoidabot",
        ],
    )


def fake_vless_content(title: str, messages: list[str]) -> str:
    stub = "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443/?type=tcp&security=none"
    return "\n".join(
        [f"#profile-title: {title}"]
        + [f"{stub}#{urllib.parse.quote(message)}" for message in messages]
    )


def rename_remark(line: str, suffix: str) -> str:
    base, sep, fragment = line.partition("#")
    if not sep or fragment.endswith(suffix):
        return line
    return f"{base}#{fragment}{suffix}"


def pick_line_by_remark(lines: list[str], needle: str) -> str | None:
    for line in lines:
        if line.startswith("vless://") and SubscriptionEngine._link_remark(line) == needle:
            return line
    return None


def invite_temp_hour_content(real_line: str, deep_link: str) -> str:
    fake = fake_vless_content(
        "доступ временный (1 час)",
        ["инструкция в описании подписки"],
    )
    fake_lines = fake.splitlines()
    return "\n".join([fake_lines[0], real_line] + fake_lines[1:])


def invite_temp_hour_description(deep_link: str) -> str:
    return "\n".join([
        "доступ временный: 1 час.",
        "чтобы продолжить, нажмите на значок самолётика/поделиться справа сверху и привяжите Telegram.",
        "после привязки Telegram откроется полноценный триал на 7 дней.",
    ])


def invite_hour_expired_content(deep_link: str) -> str:
    return fake_vless_content(
        "доступ истёк — привяжи телеграм",
        [
            "инструкция в описании подписки",
        ],
    )


def invite_hour_expired_description(deep_link: str) -> str:
    return "\n".join([
        "час временного доступа закончился.",
        "VPN снова откроется после привязки Telegram.",
        "нажмите на значок самолётика/поделиться справа сверху и привяжите Telegram.",
        "после привязки Telegram откроется полноценный триал на 7 дней.",
    ])


def invite_expired_content() -> str:
    return fake_vless_content(
        "приглашение больше не активно",
        [
            "срок приглашения истёк",
            "обратитесь к тому, кто вас пригласил, или в @vpngoidabot",
        ],
    )


def invite_banned_content() -> str:
    return fake_vless_content(
        "подписка закончилась",
        ["подписка закончилась", "обратитесь в @vpngoidabot"],
    )


def invite_trial_expired_content() -> str:
    return fake_vless_content(
        "триал закончился",
        ["триал закончился", "чтобы продолжить — оформи оплату в приложении"],
    )


@dataclass
class SubscriptionRequest:
    token: str
    client_ip: str = ""
    user_agent: str = ""
    kind: str = "plain"


@dataclass
class SubscriptionResponse:
    status: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)


class SubscriptionEngine:
    def __init__(
        self,
        *,
        domain: str,
        inbounds: dict[str, dict[str, Any]],
        hydra_inbounds: dict[str, dict[str, Any]] | None = None,
        hydra_country_names: dict[str, str] | None = None,
        hysteria_link: str = "",
        server_ips: set[str] | None = None,
        default_ip_limit: int = 4,
    ) -> None:
        self.domain = domain
        self.inbounds = inbounds
        self.hydra_inbounds = hydra_inbounds or {}
        self.hydra_country_names = hydra_country_names or {}
        self.hysteria_link = hysteria_link
        self.server_ips = server_ips or set()
        self.default_ip_limit = default_ip_limit

    def normal_headers(
        self,
        body: str,
        *,
        routing: str = "",
        description: str = DEFAULT_DESCRIPTION,
        support_url: str = DEFAULT_SUPPORT_URL,
        upload: int = 0,
        download: int = 0,
        total: int = 0,
        expire: int = 0,
        username: str = "",
    ) -> dict[str, str]:
        title = f"goida {username}".strip() if username else "goida :)"
        return {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "inline",
            "Profile-Update-Interval": "2",
            "Profile-Title": "base64:" + base64.b64encode(title.encode()).decode(),
            "Subscription-Userinfo": f"upload={upload}; download={download}; total={total}; expire={expire}",
            "Support-Url": support_url,
            "Profile-Web-Page-Url": support_url,
            "Announce": "base64:" + base64.b64encode(description.encode()).decode(),
            "Routing-Enable": "false",
            "routing": "happ://routing/off",
        }

    @staticmethod
    def legacy_headers() -> dict[str, str]:
        return {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "inline",
            "Profile-Update-Interval": "2",
        }

    def generate_plain(
        self,
        *,
        username: str,
        clients_by_key: dict[str, dict[str, Any]],
        hydra_clients_by_key: dict[str, dict[str, Any]] | None = None,
        custom_sub: str = "",
        hysteria_enabled: bool = False,
        wl_enabled: bool = False,
        wl_links: list[str] | None = None,
        description: str = DEFAULT_DESCRIPTION,
        support_url: str = DEFAULT_SUPPORT_URL,
        include_happ_metadata: bool = True,
    ) -> str:
        lines = []
        if include_happ_metadata:
            lines.extend([
                f"#profile-title: goida {username}".rstrip() if username else "#profile-title: goida :)",
                f"#profile-web-page-url: {support_url}",
            ])
        if include_happ_metadata and description:
            lines.extend([f"# {line}" if line else "#" for line in description.splitlines()])
        for key, ib in self.inbounds.items():
            if key == "smart-pro" and username != "bozhenkas":
                continue
            client = clients_by_key.get(key)
            if not client or not client.get("enable", True):
                continue
            lines.append(self.vless_link(client, ib, key, username))

        for key, client in (hydra_clients_by_key or {}).items():
            if not client or not client.get("enable", True):
                continue
            ib = self.hydra_inbounds.get(key)
            if not ib:
                continue
            lines.append(self.hydra_link(client, ib, key))

        if hysteria_enabled and self.hysteria_link:
            lines.append(self.hysteria_link)
        if custom_sub:
            for line in custom_sub.splitlines():
                line = line.strip()
                if not line:
                    continue
                lines.append(line)
        # whitelist-сервера всегда в конце списка — даже после hydra/custom_sub
        if wl_enabled:
            lines.extend([line for line in (wl_links or []) if line.strip()])
        return "\n".join(lines)

    def vless_link(self, client: dict[str, Any], inbound: dict[str, Any], key: str, username: str) -> str:
        stream = self._stream(inbound)
        network = stream.get("network") or "ws"
        security = stream.get("security") or "tls"
        if security == "none" and network in ("ws", "grpc", "httpupgrade"):
            security = "tls"
        params: dict[str, str] = {
            "type": network,
            "security": security,
        }
        if client.get("flow") and network == "tcp":
            params["flow"] = str(client["flow"])
        if security == "tls":
            tls = stream.get("tlsSettings") or {}
            sni = tls.get("serverName") or self.domain
            params["sni"] = sni
            if tls.get("fingerprint"):
                params["fp"] = tls["fingerprint"]
            if tls.get("alpn"):
                params["alpn"] = ",".join(tls["alpn"]) if isinstance(tls["alpn"], list) else str(tls["alpn"])
        elif security == "reality":
            reality = stream.get("realitySettings") or {}
            params["sni"] = self._first(reality.get("serverNames"), self.domain)
            params["pbk"] = str(reality.get("settings", {}).get("publicKey") or reality.get("publicKey") or "")
            params["sid"] = self._first(reality.get("shortIds"), "")
            params["spx"] = self._first(reality.get("spiderX"), "/")
            if reality.get("fingerprint"):
                params["fp"] = str(reality["fingerprint"])

        if network == "ws":
            ws = stream.get("wsSettings") or {}
            params["path"] = ws.get("path") or inbound.get("path") or "/"
            host = ws.get("host") or (ws.get("headers") or {}).get("Host") or self.domain
            params["host"] = host
        elif network == "grpc":
            grpc = stream.get("grpcSettings") or {}
            if grpc.get("serviceName"):
                params["serviceName"] = grpc["serviceName"]
        elif network == "httpupgrade":
            hu = stream.get("httpupgradeSettings") or {}
            params["path"] = hu.get("path") or "/"
            if hu.get("host"):
                params["host"] = hu["host"]

        remark = self.remark(key, username, inbound)
        query = urllib.parse.urlencode(params, doseq=False, safe="/,:")
        return f"vless://{client['id']}@{self.domain}:443/?{query}#{urllib.parse.quote(remark)}"

    def hydra_link(self, client: dict[str, Any], inbound: dict[str, Any], key: str) -> str:
        country = self.hydra_country_names.get(key, key)
        remark = urllib.parse.quote(f"{country} (hydra) {inbound.get('flag', '')}".strip())
        path = urllib.parse.quote(inbound.get("path") or "/")
        params = f"type=ws&security=tls&sni={self.domain}&path={path}&host={self.domain}"
        return f"vless://{client['id']}@{self.domain}:443/?{params}#{remark}"

    def generate_clash(self, plain_body: str) -> str:
        proxies = []
        for line in plain_body.splitlines():
            if line.startswith("vless://"):
                proxy = self._vless_to_clash(line)
                if proxy:
                    proxies.append(proxy)
        names = [p["name"] for p in proxies]
        data = {
            "proxies": proxies,
            "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": names + ["DIRECT"]}],
            "rules": [
                "GEOSITE,category-ru,DIRECT",
                "GEOIP,RU,DIRECT",
                "MATCH,PROXY",
            ],
        }
        return self._simple_yaml(data)

    def generate_clash_unsupported(self) -> str:
        first = "📱 Приложение не поддерживается!"
        second = "❗ Скачайте приложение HAPP!"
        proxies = [
            {
                "name": first,
                "type": "vless",
                "server": "127.0.0.1",
                "port": 443,
                "uuid": "00000000-0000-0000-0000-000000000000",
                "network": "tcp",
                "udp": True,
                "packet-encoding": "xudp",
                "tls": False,
            },
            {
                "name": second,
                "type": "vless",
                "server": "127.0.0.1",
                "port": 443,
                "uuid": "00000000-0000-0000-0000-000000000000",
                "network": "tcp",
                "udp": True,
                "packet-encoding": "xudp",
                "tls": False,
            },
        ]
        data = {
            "proxies": proxies,
            "proxy-groups": [
                {"name": "→ Remnawave", "type": "select", "proxies": [first, second, "DIRECT", "REJECT"]},
            ],
            "rules": [f"MATCH,{second}"],
        }
        return self._simple_yaml(data)

    def browser_stub_html(
        self,
        *,
        logo_svg: str = "",
        support_url: str = DEFAULT_SUPPORT_URL,
        subscription_url: str = "",
    ) -> str:
        logo = logo_svg.strip()
        if logo:
            logo_html = f'<div class="logo" aria-label="goida logo">{logo}</div>'
        else:
            logo_html = '<div class="wordmark">goida</div>'

        tabs = "\n".join(
            f'<button class="platform-tab" type="button" data-platform="{item["platform"]}">{html.escape(item["title"])}</button>'
            for item in HAPP_DOWNLOAD_LINKS
        )
        cards = "\n".join(self._download_card(item) for item in HAPP_DOWNLOAD_LINKS)
        support = html.escape(support_url)
        sub_url = subscription_url or f"https://{self.domain}/subscribe/"
        connect_url = "happ://add/" + self._happ_add_url(sub_url)
        return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>goida subscription</title>
  <style>
    @font-face {{
      font-family: "Inter";
      src: url("https://web.goida.fun/fonts/Inter-Regular.ttf") format("truetype");
      font-weight: 400;
      font-style: normal;
      font-display: swap;
    }}
    @font-face {{
      font-family: "Inter";
      src: url("https://web.goida.fun/fonts/Inter-Medium.ttf") format("truetype");
      font-weight: 500;
      font-style: normal;
      font-display: swap;
    }}
    * {{
      box-sizing: border-box;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: -0.04em;
    }}
    html {{ letter-spacing: -0.04em; }}
    html, body {{ margin: 0; min-height: 100%; }}
    body {{
      min-height: 100vh;
      background:
        radial-gradient(75% 58% at 16% 7%, rgba(54,210,158,.36), transparent 58%),
        radial-gradient(62% 48% at 92% 28%, rgba(37,116,156,.30), transparent 62%),
        radial-gradient(88% 64% at 46% 112%, rgba(23,126,92,.42), transparent 68%),
        linear-gradient(158deg, #0b3a2f 0%, #06221b 46%, #020d0a 100%);
      color: rgba(246,255,249,.94);
      display: grid;
      place-items: start center;
      padding: calc(18px + env(safe-area-inset-top)) 16px calc(24px + env(safe-area-inset-bottom));
      font-weight: 400;
      overflow-x: hidden;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: -20%;
      pointer-events: none;
      background:
        radial-gradient(42% 34% at 18% 80%, rgba(48,209,158,.18), transparent 62%),
        radial-gradient(38% 30% at 78% 12%, rgba(255,255,255,.055), transparent 68%),
        linear-gradient(120deg, transparent 0 36%, rgba(255,255,255,.035) 49%, transparent 63%);
      filter: blur(28px);
      opacity: .85;
    }}
    main {{
      position: relative;
      width: min(100%, 640px);
      min-height: calc(100vh - 42px);
      display: grid;
      align-content: start;
      gap: 12px;
    }}
    .logo {{
      color: rgba(246,255,249,.94);
    }}
    .logo svg {{
      width: min(170px, 48vw);
      height: auto;
      display: block;
      margin: 6px auto 12px;
      filter: drop-shadow(0 8px 22px rgba(0,0,0,.24));
    }}
    .logo svg *,
    .logo svg [fill],
    .logo svg [stroke] {{
      fill: currentColor !important;
      stroke: currentColor !important;
    }}
    .logo svg [fill="none"] {{
      fill: none !important;
    }}
    .wordmark {{ font-size: 42px; font-weight: 500; font-style: italic; text-align: center; }}
    h1 {{
      margin: 0;
      font-size: clamp(32px, 8vw, 44px);
      font-weight: 500;
      line-height: 1.04;
      text-align: center;
      padding: 18px 4px;
    }}
    .panel {{
      width: 100%;
      display: grid;
      gap: 14px;
      text-align: left;
      justify-items: stretch;
      padding: clamp(18px, 4vw, 24px);
      border-radius: 18px;
      background: rgba(255,255,255,.070);
      border: 1px solid rgba(255,255,255,.125);
      box-shadow: 0 10px 24px rgba(0,0,0,.20);
      backdrop-filter: blur(24px) saturate(1.18);
      -webkit-backdrop-filter: blur(24px) saturate(1.18);
      overflow: hidden;
      -webkit-mask-image: -webkit-radial-gradient(white, black);
    }}
    .lead {{
      margin: 0;
      color: rgba(226,244,235,.66);
      font-size: 17px;
      font-weight: 400;
      line-height: 1.25;
    }}
    .tabs {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }}
    .platform-tab {{
      border: 0;
      border-radius: 14px;
      min-height: 44px;
      padding: 0 10px;
      background: rgba(255,255,255,.070);
      color: rgba(226,244,235,.70);
      font: inherit;
      font-size: 16px;
      cursor: pointer;
      border: 1px solid rgba(255,255,255,.08);
      transition: transform .18s ease, background .18s ease, color .18s ease, box-shadow .18s ease;
    }}
    .platform-tab:hover {{ transform: translateY(-1px); }}
    .platform-tab.is-active {{ background: rgba(48,209,158,.24); color: rgba(246,255,249,.98); }}
    .downloads {{
      width: 100%;
      text-align: left;
    }}
    .connect-panel {{
      gap: 12px;
    }}
    .connect-area {{
      display: grid;
      justify-items: stretch;
      gap: 12px;
      padding-top: 4px;
    }}
    .download-card {{
      display: none;
      width: 100%;
      min-height: 0;
      border-radius: 18px;
      background: rgba(0,0,0,.14);
      border: 1px solid rgba(255,255,255,.12);
      padding: 16px;
      align-content: space-between;
      gap: 14px;
    }}
    .download-card.is-active {{ display: grid; }}
    .download-card.has-many-actions {{ width: 100%; min-width: 0; }}
    .download-card h2 {{
      margin: 0 0 6px;
      font-size: 28px;
      font-weight: 500;
      line-height: 1.08;
    }}
    .download-card p {{
      margin: 0;
      font-size: 16px;
      line-height: 1.25;
      color: rgba(226,244,235,.66);
    }}
    .actions {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 8px;
    }}
    .actions a,
    .connect-button {{
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 48px;
      border-radius: 14px;
      background: rgba(255,255,255,.10);
      color: rgba(246,255,249,.98);
      text-decoration: none;
      font-size: 16px;
      font-weight: 500;
      line-height: 1;
      padding: 0 16px;
      white-space: nowrap;
      border: 1px solid rgba(255,255,255,.18);
      box-shadow: 0 6px 16px rgba(0,0,0,.18);
      transition: transform .18s ease, background .18s ease, color .18s ease, box-shadow .18s ease;
    }}
    .actions a:hover,
    .connect-button:hover {{
      transform: translateY(-1px);
    }}
    .actions a:active,
    .connect-button:active {{ transform: translateY(0) scale(.99); }}
    .actions a.secondary {{ background: rgba(255,255,255,.070); color: rgba(226,244,235,.88); }}
    .caption,
    .actions sup {{
      font-size: 11px;
      line-height: 1.2;
      color: rgba(226,244,235,.66);
    }}
    .actions sup {{
      margin-left: 2px;
      position: relative;
      top: -0.35em;
    }}
    .connect-button {{
      width: 100%;
      text-transform: lowercase;
    }}
    .support {{
      color: rgba(226,244,235,.66);
      font-size: 15px;
      line-height: 1.35;
      text-align: center;
      padding: 10px 8px 0;
    }}
    .support a {{ color: inherit; text-underline-offset: 4px; }}
    .copy-area {{ width: 100%; }}
    .copy-row {{ display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: stretch; }}
    #sub-url-input {{
      min-width: 0;
      min-height: 48px;
      padding: 0 14px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,.13);
      background: rgba(0,0,0,.14);
      font: inherit;
      font-size: 15px;
      color: rgba(226,244,235,.78);
      outline: none;
    }}
    #copy-btn {{
      min-height: 48px;
      padding: 0 18px;
      border: 0;
      border-radius: 14px;
      background: rgba(255,255,255,.10);
      color: rgba(246,255,249,.98);
      border: 1px solid rgba(255,255,255,.18);
      font: inherit;
      font-size: 15px;
      font-weight: 500;
      cursor: pointer;
      white-space: nowrap;
    }}
    @media (max-width: 760px) {{
      h1 {{ font-size: clamp(24px, calc(8vw - 8px), 36px); }}
      .platform-tab {{ font-size: 15px; min-height: 42px; }}
      .actions {{ grid-template-columns: 1fr; }}
      .copy-row {{ grid-template-columns: 1fr; }}
    }}
    @media (min-width: 761px) and (max-width: 1100px) {{
      main {{ width: min(100%, 720px); }}
    }}
    @media (min-width: 900px) {{
      main {{ width: min(100%, 760px); }}
      .tabs {{ grid-template-columns: repeat(6, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <main>
    {logo_html}
    <h1>эту ссылку нельзя открыть<br>в браузере :(</h1>
    <section class="panel download-panel" aria-label="скачивание happ">
      <p class="lead">скачивай happ и подключайся!</p>
      <nav class="tabs" aria-label="платформа">{tabs}</nav>
      <div class="downloads">{cards}</div>
    </section>
    <section class="panel connect-panel" aria-label="подключение happ">
      <div class="connect-area" id="connect-area">
        <p class="lead">уже скачал?</p>
        <a class="connect-button" id="connect-btn" href="{html.escape(connect_url)}" rel="noopener noreferrer">подключить</a>
        <div id="copy-area" class="copy-area" hidden>
          <p class="caption">скопируй ссылку и добавь в Happ вручную:</p>
          <div class="copy-row">
            <input id="sub-url-input" type="text" readonly value="{html.escape(sub_url)}">
            <button id="copy-btn" type="button" onclick="copyUrl()">скопировать</button>
          </div>
        </div>
      </div>
    </section>
    <div class="support">если возникли проблемы,<br>смело пиши <a href="https://t.me/bozhenkas">t.me/bozhenkas</a> | <a href="https://vk.com/bozhenkas">vk.com/bozhenkas</a></div>
  </main>
  <script>
    const tabs = [...document.querySelectorAll('.platform-tab')];
    const cards = [...document.querySelectorAll('.download-card')];
    function activate(platform) {{
      tabs.forEach(tab => tab.classList.toggle('is-active', tab.dataset.platform === platform));
      cards.forEach(card => card.classList.toggle('is-active', card.dataset.platform === platform));
      const isWin = platform === 'windows';
      document.getElementById('connect-btn').hidden = isWin;
      document.getElementById('copy-area').hidden = !isWin;
    }}
    tabs.forEach(tab => tab.addEventListener('click', () => activate(tab.dataset.platform)));
    function copyUrl() {{
      const inp = document.getElementById('sub-url-input');
      const btn = document.getElementById('copy-btn');
      navigator.clipboard.writeText(inp.value).then(() => {{
        btn.textContent = 'скопировано ✓';
        setTimeout(() => {{ btn.textContent = 'скопировать'; }}, 2000);
      }}).catch(() => {{
        inp.select();
        document.execCommand('copy');
        btn.textContent = 'скопировано ✓';
        setTimeout(() => {{ btn.textContent = 'скопировать'; }}, 2000);
      }});
    }}
    const guess = /android/i.test(navigator.userAgent) ? 'android' : /iphone|ipad|ios/i.test(navigator.userAgent) ? 'ios' : /mac/i.test(navigator.userAgent) ? 'macos' : /windows/i.test(navigator.userAgent) ? 'windows' : 'ios';
    activate(guess);
  </script>
</body>
</html>"""

    @staticmethod
    def _download_card(item: dict[str, Any]) -> str:
        actions = []
        for idx, (label, url) in enumerate(item["actions"]):
            cls = "" if idx == 0 else ' class="secondary"'
            actions.append(
                f'<a{cls} href="{html.escape(url)}" rel="noopener noreferrer">'
                f'{SubscriptionEngine._action_label_html(label)}</a>'
            )
        many = " has-many-actions" if len(actions) >= 3 else ""
        return (
            f'<article class="download-card{many}" data-platform="{html.escape(item["platform"])}">'
            f'<div><h2>{html.escape(item["title"])}</h2><p>{html.escape(item["description"])}</p></div>'
            f'<div class="actions" style="--action-count:{len(actions)}">{"".join(actions)}</div>'
            f'</article>'
        )

    @staticmethod
    def _action_label_html(label: str) -> str:
        if label.endswith(" [ru]"):
            return f'{html.escape(label[:-5])} <sup>[ru]</sup>'
        if label.endswith(" [global]"):
            return f'{html.escape(label[:-9])} <sup>[global]</sup>'
        return html.escape(label)

    @staticmethod
    def _happ_add_url(url: str) -> str:
        # полностью кодируем URL чтобы Windows URI handler не интерпретировал
        # слэши пути как часть happ:// структуры (иначе "protocol is unknown")
        return urllib.parse.quote(url, safe="")

    def generate_json_profile(
        self,
        plain_body: str,
        *,
        ru_direct: bool = True,
        diagnostic_proxy: bool = False,
        adblock: bool = False,
    ) -> str:
        profiles = self.json_profiles(plain_body, ru_direct=ru_direct, diagnostic_proxy=diagnostic_proxy,
                                      adblock=adblock)
        if len(profiles) == 1:
            return json.dumps(profiles[0], ensure_ascii=False, indent=2)
        return json.dumps(profiles, ensure_ascii=False, indent=2)

    def json_profiles(
        self,
        plain_body: str,
        *,
        ru_direct: bool = True,
        diagnostic_proxy: bool = False,
        adblock: bool = False,
    ) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        for line in plain_body.splitlines():
            if line.startswith(WL_JSON_PREFIX):
                profile = self._wl_json_profile(line)
                if profile:
                    profiles.append(profile)
                continue
            if not line.startswith("vless://"):
                if line.startswith(("hysteria2://", "hy2://")):
                    outbound = self._json_outbound_hysteria2(line)
                    if outbound:
                        profiles.append(self._json_config(
                            outbound,
                            self._json_routing(ru_direct=ru_direct, diagnostic_proxy=diagnostic_proxy),
                            self._link_remark(line),
                            adblock=adblock,
                        ))
                continue
            # «Оптимальный» → клиентский балансировщик fin/fra (swe fallback, reserve last) вместо /smart
            if self._is_smart_line(line):
                smart = self._json_smart_balanced(plain_body, line, ru_direct=ru_direct, diagnostic_proxy=diagnostic_proxy, adblock=adblock)
                if smart:
                    profiles.append(smart)
                    continue
            outbound = self._json_outbound(line)
            if not outbound:
                continue
            profiles.append(self._json_config(outbound, self._json_routing(ru_direct=ru_direct, diagnostic_proxy=diagnostic_proxy), self._link_remark(line), adblock=adblock))
        return profiles

    @staticmethod
    def _wl_json_profile(line: str) -> dict[str, Any] | None:
        payload = line[len(WL_JSON_PREFIX):].strip()
        if not payload:
            return None
        try:
            padded = payload + "=" * (-len(payload) % 4)
            profile = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        except Exception:
            return None
        if not isinstance(profile, dict):
            return None
        outbounds = profile.get("outbounds")
        if not isinstance(outbounds, list):
            return None
        if not any(isinstance(o, dict) and o.get("protocol") == "vless" for o in outbounds):
            return None
        return copy.deepcopy(profile)

    @staticmethod
    def _json_inbounds() -> list[dict[str, Any]]:
        return [
            {
                "tag": "socks",
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth"},
                "sniffing": {"enabled": True, "routeOnly": False, "destOverride": ["http", "tls", "quic"]},
            },
            {
                "tag": "http",
                "listen": "127.0.0.1",
                "port": 10809,
                "protocol": "http",
                "settings": {"allowTransparent": False},
                "sniffing": {"enabled": True, "routeOnly": False, "destOverride": ["http", "tls", "quic"]},
            },
        ]

    def _json_config(self, outbound: dict[str, Any], routing: dict[str, Any], remark: str = "",
                     adblock: bool = False) -> dict[str, Any]:
        config: dict[str, Any] = {
            "log": {"loglevel": "warning"},
            "dns": self._json_dns(adblock=adblock),
            "inbounds": self._json_inbounds(),
            "outbounds": [
                outbound,
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"},
            ],
            "routing": routing,
        }
        if remark:
            config["remarks"] = remark
        return config

    # ── «Оптимальный»: клиентский балансировщик fin/fra (swe fallback) ──
    # Логика роутинга SMART перенесена с RU-сервера на клиент. Трафик всё равно
    # идёт через RU (пути /fin /fra /swe), но выбор выхода и RU-direct делает клиент.
    SMART_REMARK = "Оптимальный 🇸🇨"
    RESERVE_PREFIX = "Резервный"
    # целевой режим после пересборки: smart балансит FIN/SWE; FRA выводится в dummy.
    _SMART_RELAYS = (("proxy-fin", "/fin"), ("proxy-swe", "/swe"), ("zapret", "/direct"))
    # ── ИНЦИДЕНТ 2026-06-25: FIN/FRA/SWE недоступны у хостинг-провайдера ──
    # Временно «Оптимальный» балансит не fin/fra/swe (мертвы), а живые hydra-выходы
    # через наш же RU-вход (пути /hydra-*). uuid тот же (юзеры в hydra-сквадах после
    # task6 backfill). Откат: SMART_OVER_HYDRA=False + редеплой ИЛИ восстановить файл
    # из /root/deploy-backups/<ts>-fin-fra-swe-outage/engine.py.bak.
    # См. docs/incident-20260625-fin-fra-swe-provider-outage.md.
    SMART_OVER_HYDRA = os.environ.get("SMART_OVER_HYDRA", "1").lower() in ("1", "true", "yes")
    _SMART_RELAYS_HYDRA = (("proxy-hydra-nl", "/hydra-nl"), ("proxy-hydra-de", "/hydra-de"),
                           ("proxy-hydra-pol", "/hydra-pol"), ("proxy-hydra-tur", "/hydra-tur"),
                           ("zapret", "/direct"))

    def _is_smart_line(self, line: str) -> bool:
        return self._link_remark(line) == self.SMART_REMARK

    @staticmethod
    def _smart_relay_outbound(base: dict[str, Any], tag: str, path: str) -> dict[str, Any]:
        ob = copy.deepcopy(base)
        ob["tag"] = tag
        ws = ob.get("streamSettings", {}).get("wsSettings")
        if isinstance(ws, dict):
            ws["path"] = path
        return ob

    @staticmethod
    def _smart_routing(*, ru_direct: bool, diagnostic_proxy: bool,
                       selector: list[str], fallback: str) -> dict[str, Any]:
        rules: list[dict[str, Any]] = [
            {"type": "field", "protocol": ["bittorrent"], "outboundTag": "direct"},
            {"type": "field", "domain": list(YOUTUBE_ZAPRET_SITES), "outboundTag": "zapret"},
            {"type": "field", "domain": list(DISCORD_ZAPRET_SITES), "outboundTag": "zapret"},
        ]
        for port_range in DISCORD_VOICE_PORTS:
            rules.append({
                "type": "field",
                "network": "udp",
                "port": port_range,
                "outboundTag": "zapret",
            })
        if diagnostic_proxy:
            rules.append({"type": "field", "domain": DIAGNOSTIC_PROXY_SITES, "balancerTag": "smart-balancer"})
        if ru_direct:
            rules.extend([
                {"type": "field", "ip": RU_DIRECT_IPS, "outboundTag": "direct"},
                {"type": "field", "domain": RU_DIRECT_SITES, "outboundTag": "direct"},
            ])
        rules.append({"type": "field", "network": "tcp,udp", "balancerTag": "smart-balancer"})
        return {
            "domainMatcher": "hybrid",
            "domainStrategy": "IPIfNonMatch",
            "balancers": [{
                "tag": "smart-balancer",
                "selector": selector,
                "fallbackTag": fallback,
                "strategy": {
                    "type": "leastPing",
                },
            }],
            "rules": rules,
        }

    def _json_smart_balanced(self, plain_body: str, smart_line: str, *,
                             ru_direct: bool, diagnostic_proxy: bool,
                             adblock: bool = False) -> dict[str, Any] | None:
        base = self._json_outbound(smart_line)
        if not base or base.get("streamSettings", {}).get("network") != "ws":
            return None  # balancer полагается на ws-пути /fin /fra /swe /direct (или /hydra-* в инцидент-режиме)
        relays_spec = self._SMART_RELAYS_HYDRA if self.SMART_OVER_HYDRA else self._SMART_RELAYS
        relays = [self._smart_relay_outbound(base, tag, path) for tag, path in relays_spec]
        # «Резервный» (gRPC Reality, ДРУГОЙ хост reserve.goida.fun) — последний фоллбэк,
        # если все WS-выходы через ru.goida.fun недоступны (блок IP ТСПУ).
        reserve = None
        for ln in plain_body.splitlines():
            if ln.startswith("vless://") and self._link_remark(ln).startswith(self.RESERVE_PREFIX):
                ro = self._json_outbound(ln)
                if ro:
                    ro["tag"] = "proxy-reserve"
                    reserve = ro
                break
        if self.SMART_OVER_HYDRA:
            # инцидент-режим: балансим живые hydra-выходы, reserve (→NL) — фоллбэк
            if reserve is not None:
                selector = ["proxy-hydra-nl", "proxy-hydra-de", "proxy-hydra-pol", "proxy-hydra-tur"]
                fallback = "proxy-reserve"
                extra = [reserve]
            else:
                selector = ["proxy-hydra-nl", "proxy-hydra-pol", "proxy-hydra-tur"]
                fallback = "proxy-hydra-de"
                extra = []
        elif reserve is not None:
            selector = ["proxy-fin", "proxy-swe"]
            fallback = "proxy-reserve"
            extra = [reserve]
        else:
            selector = ["proxy-fin"]
            fallback = "proxy-swe"
            extra = []
        return {
            "log": {"loglevel": "warning"},
            "dns": self._json_dns(adblock=adblock),
            "burstObservatory": {
                "pingConfig": {
                    "destination": "http://www.gstatic.com/generate_204",
                    "interval": "1m",
                    "timeout": "3s",
                    "sampling": 1,
                },
                "subjectSelector": selector,
            },
            "inbounds": self._json_inbounds(),
            "outbounds": relays + extra + [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"},
            ],
            "routing": self._smart_routing(ru_direct=ru_direct, diagnostic_proxy=diagnostic_proxy,
                                           selector=selector, fallback=fallback),
            "remarks": self.SMART_REMARK,
        }

    # AdGuard Home (FIN) — DoH-эндпоинт жёсткого adblock (ads+trackers).
    # Резолвинг зарубежки выходит с финского egress, не с RU IP — закрывает
    # вектор детекта «русский резолвер». Фронт: standalone nginx :5443 →
    # AdGuard backend 127.0.0.1:5444 (UI остаётся приватным на 127.0.0.1).
    ADBLOCK_DOH = "https+local://fin.goida.fun:5443/dns-query"

    @staticmethod
    def _json_dns(adblock: bool = False) -> dict[str, Any]:
        if adblock:
            servers = [
                SubscriptionEngine.ADBLOCK_DOH,
                "https+local://1.1.1.1/dns-query",  # фоллбэк если AdGuard недоступен
                "localhost",
            ]
        else:
            servers = [
                "https+local://1.1.1.1/dns-query",
                "https+local://8.8.8.8/dns-query",
                "localhost",
            ]
        return {
            "servers": servers,
            "queryStrategy": "UseIPv4",
            "disableCache": False,
        }

    def _json_outbound(self, line: str) -> dict[str, Any] | None:
        parsed = urllib.parse.urlparse(line)
        if parsed.scheme != "vless" or not parsed.hostname or not parsed.username:
            return None
        qs = urllib.parse.parse_qs(parsed.query)
        network = self._one(qs, "type", "ws")
        security = self._one(qs, "security", "tls")
        outbound: dict[str, Any] = {
            "tag": "proxy",
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": parsed.hostname,
                    "port": parsed.port or 443,
                    "users": [{
                        "id": parsed.username,
                        "encryption": "none",
                    }],
                }]
            },
            "streamSettings": {
                "network": network,
                "security": security,
            },
        }
        flow = self._one(qs, "flow", "")
        if flow:
            outbound["settings"]["vnext"][0]["users"][0]["flow"] = flow
        stream = outbound["streamSettings"]
        if network == "ws":
            stream["wsSettings"] = {
                "path": self._one(qs, "path", "/"),
                "headers": {"Host": self._one(qs, "host", parsed.hostname)},
            }
        elif network == "grpc":
            grpc_settings: dict[str, Any] = {
                "serviceName": self._one(qs, "serviceName", ""),
            }
            mode = self._one(qs, "mode", "")
            if mode == "multi":
                grpc_settings["multiMode"] = True
            elif mode == "gun":
                # Happ/Xray 26: gun mode явно, не только multiMode=false
                grpc_settings["mode"] = "gun"
            else:
                grpc_settings["multiMode"] = False
            stream["grpcSettings"] = grpc_settings
        elif network == "xhttp":
            path = self._one(qs, "path", "/")
            mode = self._one(qs, "mode", "")
            host = self._one(qs, "host", "") or self._one(qs, "sni", "")
            if security == "reality":
                stream["xhttpSettings"] = build_xhttp_settings(
                    path=path,
                    host=host,
                    mode=mode or "stream-one",
                )
            else:
                xhttp: dict[str, Any] = {"path": path, "mode": mode or "auto"}
                if host:
                    xhttp["host"] = host
                stream["xhttpSettings"] = xhttp
        elif network == "tcp":
            stream["tcpSettings"] = {"header": {"type": self._one(qs, "headerType", "none")}}
        if security == "tls":
            stream["tlsSettings"] = {
                "serverName": self._one(qs, "sni", parsed.hostname),
                "fingerprint": self._one(qs, "fp", "chrome"),
            }
        elif security == "reality":
            reality = {
                "serverName": self._one(qs, "sni", parsed.hostname),
                "publicKey": self._one(qs, "pbk", ""),
                "shortId": self._one(qs, "sid", ""),
                "fingerprint": self._one(qs, "fp", "chrome"),
            }
            spider_x = self._one(qs, "spx", "")
            if spider_x:
                reality["spiderX"] = spider_x
            stream["realitySettings"] = {k: v for k, v in reality.items() if v != ""}
        return outbound

    def _json_outbound_hysteria2(self, line: str) -> dict[str, Any] | None:
        parsed = urllib.parse.urlparse(line)
        if parsed.scheme not in ("hysteria2", "hy2") or not parsed.hostname:
            return None
        auth = parsed.username or ""
        if not auth:
            return None
        qs = urllib.parse.parse_qs(parsed.query)
        outbound: dict[str, Any] = {
            "tag": "proxy",
            "protocol": "hysteria",
            "settings": {
                "version": 2,
                "address": parsed.hostname,
                "port": parsed.port or 443,
            },
            "streamSettings": {
                "network": "hysteria",
                "security": "tls",
                "tlsSettings": {
                    "serverName": self._one(qs, "sni", parsed.hostname),
                    "alpn": ["h3"],
                },
                "hysteriaSettings": {
                    "version": 2,
                    "auth": auth,
                },
            },
        }
        obfs = self._one(qs, "obfs", "")
        obfs_password = self._one(qs, "obfs-password", "") or self._one(qs, "obfs_password", "")
        if obfs == "salamander" and obfs_password:
            outbound["streamSettings"]["finalmask"] = {
                "udp": [{"type": "salamander", "settings": {"password": obfs_password}}],
            }
        return outbound

    @staticmethod
    def _json_routing(*, ru_direct: bool, diagnostic_proxy: bool = False) -> dict[str, Any]:
        if not ru_direct:
            return {"rules": [{"type": "field", "network": "tcp,udp", "outboundTag": "proxy"}]}
        return _xray_routing(diagnostic_proxy=diagnostic_proxy)

    @staticmethod
    def _link_remark(line: str) -> str:
        try:
            return urllib.parse.unquote(urllib.parse.urlparse(line).fragment or "").strip()
        except Exception:
            return ""

    def guard_ip_limit(
        self,
        *,
        token: str,
        client_ip: str,
        user_agent: str,
        content: str,
        device_rows: set[str],
        user_limit: int,
    ) -> tuple[str, str]:
        if client_ip in self.server_ips:
            return content, ""
        device_id = self.device_id(client_ip, user_agent)
        if not device_id:
            # нет hwid → пропускаем без tracking (не блокируем)
            return content, ""
        # лимит превышен → старое устройство вытесняется в remember_device, новое пропускаем
        return content, device_id

    @staticmethod
    def device_id(ip: str, user_agent: str) -> str:
        hwid = SubscriptionEngine.extract_hwid(user_agent)
        return f"hwid:{hwid}" if hwid else ""

    @staticmethod
    def extract_hwid(user_agent: str) -> str:
        if not user_agent:
            return ""
        if user_agent.startswith("Happ/"):
            parts = user_agent.split("/")
            for part in reversed(parts[1:]):
                value = part.strip()
                if re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
                    continue
                if re.fullmatch(r"[A-Za-z0-9_.:-]{4,128}", value) and value.lower() not in {
                    "happ", "ios", "android", "windows", "macos", "macos catalyst",
                    "apple tv", "android tv",
                }:
                    return value[:128]
        match = re.search(r"(?:hwid|device[-_ ]?id)[:=/ ]([A-Za-z0-9_.:-]{4,128})", user_agent, re.I)
        return match.group(1) if match else ""

    def remark(self, key: str, username: str, inbound: dict[str, Any]) -> str:
        if key == "smart":
            return "Оптимальный 🇸🇨"
        if key == "smart2":
            return "Оптимальный Нео 🇸🇨"
        if key == "smart-pro":
            return f"smart-pro-{username}⚡"
        if key == "zapret":
            return "Русский (YouTube, Discord) 🇷🇺"
        suffix = inbound.get("remark_suffix", "")
        prefix = (inbound.get("prefix") or key).strip("-")
        if prefix == "fin":
            return "Финляндия 🇫🇮"
        if prefix == "swe":
            return "Швеция 🇸🇪"
        if prefix == "fra":
            return "Франция 🇫🇷"
        return f"{prefix}-{username} {suffix}".strip()

    @staticmethod
    def encode_body(body: str) -> str:
        return base64.b64encode(body.encode()).decode()

    @staticmethod
    def _stream(inbound: dict[str, Any]) -> dict[str, Any]:
        raw = inbound.get("stream_settings") or inbound.get("streamSettings") or {}
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return copy.deepcopy(raw)

    @staticmethod
    def _first(value: Any, default: str) -> str:
        if isinstance(value, list) and value:
            return str(value[0])
        if isinstance(value, str) and value:
            return value
        return default

    @staticmethod
    def _one(qs: dict[str, list[str]], key: str, default: str) -> str:
        values = qs.get(key)
        return values[0] if values else default

    def _vless_to_clash(self, link: str) -> dict[str, Any] | None:
        parsed = urllib.parse.urlparse(link)
        if not parsed.username or not parsed.hostname:
            return None
        qs = urllib.parse.parse_qs(parsed.query)
        name = urllib.parse.unquote(parsed.fragment or parsed.hostname)
        network = self._one(qs, "type", "ws")
        security = self._one(qs, "security", "tls")
        proxy: dict[str, Any] = {
            "name": name,
            "type": "vless",
            "server": parsed.hostname,
            "port": parsed.port or 443,
            "uuid": parsed.username,
            "network": network,
            "udp": True,
            "tls": security in ("tls", "reality"),
            "servername": self._one(qs, "sni", self.domain),
        }
        if self._one(qs, "flow", ""):
            proxy["flow"] = self._one(qs, "flow", "")
        if network == "ws":
            proxy["ws-opts"] = {
                "path": self._one(qs, "path", "/"),
                "headers": {"Host": self._one(qs, "host", self.domain)},
            }
        elif network == "xhttp":
            proxy["xhttp-opts"] = {
                "path": self._one(qs, "path", "/"),
                "mode": self._one(qs, "mode", "auto"),
            }
        if security == "reality":
            proxy["reality-opts"] = {
                "public-key": self._one(qs, "pbk", ""),
                "short-id": self._one(qs, "sid", ""),
            }
            proxy["client-fingerprint"] = self._one(qs, "fp", "chrome")
        return proxy

    def _simple_yaml(self, data: Any, indent: int = 0) -> str:
        sp = " " * indent
        if isinstance(data, dict):
            lines = []
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    lines.append(f"{sp}{key}:")
                    lines.append(self._simple_yaml(value, indent + 2))
                else:
                    lines.append(f"{sp}{key}: {self._yaml_scalar(value)}")
            return "\n".join(lines)
        if isinstance(data, list):
            lines = []
            for item in data:
                if isinstance(item, dict):
                    lines.append(f"{sp}-")
                    lines.append(self._simple_yaml(item, indent + 2))
                else:
                    lines.append(f"{sp}- {self._yaml_scalar(item)}")
            return "\n".join(lines)
        return f"{sp}{self._yaml_scalar(data)}"

    @staticmethod
    def _yaml_scalar(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value)
        if text == "" or any(ch in text for ch in ":#{}[],&*?|-<>=!%@\\"):
            return json.dumps(text, ensure_ascii=False)
        return text


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
