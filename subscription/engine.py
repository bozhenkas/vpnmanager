from __future__ import annotations

import base64
import copy
import html
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


HAPP_ROUTING_PROFILE = {
    "Name": "goida.fun - Smart",
    "GlobalProxy": "true",
    "RemoteDNSType": "DoH",
    "RemoteDNSDomain": "https://cloudflare-dns.com/dns-query",
    "RemoteDNSIP": "1.1.1.1",
    "DomesticDNSType": "DoH",
    "DomesticDNSDomain": "https://dns.yandex.ru/dns-query",
    "DomesticDNSIP": "77.88.8.8",
    "DirectSites": [
        "geosite:category-ru",
        "domain:sberbank.ru",
        "domain:sbrf.ru",
        "domain:sber.ru",
        "domain:tinkoff.ru",
        "domain:tbank.ru",
        "domain:vtb.ru",
        "domain:alfabank.ru",
        "domain:raiffeisen.ru",
        "domain:gazprombank.ru",
        "domain:gosuslugi.ru",
        "domain:esia.gosuslugi.ru",
        "domain:nalog.ru",
        "domain:mos.ru",
        "domain:gov.spb.ru",
        "domain:wildberries.ru",
        "domain:ozon.ru",
        "domain:avito.ru",
        "domain:lamoda.ru",
        "domain:vk.com",
        "domain:vk.ru",
        "domain:ok.ru",
        "domain:hh.ru",
        "domain:2gis.ru",
        "domain:ivi.ru",
        "domain:okko.tv",
        "domain:wink.ru",
        "domain:more.tv",
        "domain:litres.ru",
        "domain:gismeteo.ru",
        "domain:rambler.ru",
        "domain:tutu.ru",
    ],
    "DirectIp": [
        "geoip:ru",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "255.255.255.255/32",
    ],
    "DomainStrategy": "IPIfNonMatch",
    "FakeDNS": "false",
}


def _b64_json(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()


HAPP_ROUTING_LINE = "happ://routing/onadd/" + _b64_json(HAPP_ROUTING_PROFILE)
DEFAULT_DESCRIPTION = (
    "smart — оптимальный сервер.\n"
    "ru-zapret — для ютуба без рекламы (телеграм и дискорд тоже работают)\n"
    "для youtube-shorts рекомендуется выбирать fin/swe, на smart и ru-zapret хорошо работают только длинные видео\n\n"

    "в случае проблем сразу пишите @bozhenkas\n"
    "t.me/bozhenkas"
)
DEFAULT_SUPPORT_URL = "https://t.me/bozhenkas"
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


def deleted_sub_content() -> str:
    msg1 = urllib.parse.quote("пользователь удален")
    msg2 = urllib.parse.quote("обратитесь к @bozhenkas")
    stub = "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443/?type=tcp&security=none"
    return "\n".join([
        "#profile-title: пользователь удален",
        f"{stub}#{msg1}",
        f"{stub}#{msg2}",
    ])


def unsupported_client_content() -> str:
    msg1 = urllib.parse.quote("клиент не поддерживается")
    msg2 = urllib.parse.quote("скачайте Happ")
    stub = "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443/?type=tcp&security=none"
    return "\n".join([
        "#profile-title: клиент не поддерживается",
        "# скачайте Happ",
        f"{stub}#{msg1}",
        f"{stub}#{msg2}",
    ])


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
        routing: str = HAPP_ROUTING_LINE,
        description: str = DEFAULT_DESCRIPTION,
        support_url: str = DEFAULT_SUPPORT_URL,
        upload: int = 0,
        download: int = 0,
        total: int = 0,
        expire: int = 0,
    ) -> dict[str, str]:
        return {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "inline",
            "Profile-Update-Interval": "2",
            "Profile-Title": "base64:" + base64.b64encode("goida :)".encode()).decode(),
            "Subscription-Userinfo": f"upload={upload}; download={download}; total={total}; expire={expire}",
            "Support-Url": support_url,
            "Profile-Web-Page-Url": support_url,
            "Announce": "base64:" + base64.b64encode(description.encode()).decode(),
            "Routing-Enable": "true",
            "Routing": routing,
            "routing": routing,
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
                HAPP_ROUTING_LINE,
                "#profile-title: goida :)",
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
        if wl_enabled:
            lines.extend([line for line in (wl_links or []) if line.strip()])
        if custom_sub:
            lines.extend([line.strip() for line in custom_sub.splitlines() if line.strip()])
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
        sub_url = subscription_url or f"https://{self.domain}/subscribe-next/"
        connect_url = "happ://add/" + self._happ_add_url(sub_url)
        return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>goida subscription</title>
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; min-height: 100%; }}
    body {{
      min-height: 100vh;
      background: var(--gradik, linear-gradient(315deg, #96C8EE 0%, #E3F3FE 85.1%, #DDF4FF 96.15%, #D6F5FF 100%));
      color: #080808;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      display: grid;
      place-items: center;
      padding: 48px 24px;
    }}
    main {{
      width: min(100%, 980px);
      min-height: min(760px, calc(100vh - 96px));
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 120px;
      text-align: center;
    }}
    .logo svg {{
      width: min(325px, 72vw);
      height: auto;
      display: block;
    }}
    .wordmark {{ font-size: 72px; font-weight: 800; font-style: italic; }}
    h1 {{
      margin: 0;
      max-width: 900px;
      text-align: center;
      font-size: 80px;
      font-style: normal;
      font-weight: 400;
      line-height: 110%;
      letter-spacing: -2.1px;
    }}
    .panel {{
      width: min(100%, 760px);
      display: grid;
      gap: 32px;
      text-align: left;
      justify-items: stretch;
    }}
    .lead {{
      margin: 0;
      color: #080808;
      font-size: 28px;
      font-style: normal;
      font-weight: 400;
      line-height: 110%;
      letter-spacing: -0.84px;
    }}
    .tabs {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 8px;
    }}
    .platform-tab {{
      border: 0;
      border-radius: 999px;
      padding: 12px 14px;
      background: rgba(255,255,255,.45);
      color: #080808;
      font: inherit;
      font-size: 18px;
      cursor: pointer;
      transition: transform .18s ease, background .18s ease, color .18s ease, box-shadow .18s ease;
    }}
    .platform-tab:hover {{ transform: translateY(-2px); box-shadow: 0 10px 18px rgba(8,8,8,.08); }}
    .platform-tab.is-active {{ background: #080808; color: #fff; }}
    .downloads {{
      width: max-content;
      max-width: 100%;
      text-align: left;
    }}
    .connect-area {{ display: grid; justify-items: start; gap: 16px; }}
    .download-card {{
      display: none;
      width: min(100%, 430px);
      min-height: 156px;
      border-radius: 8px;
      background: rgba(255,255,255,.36);
      border: 1px solid rgba(8,8,8,.12);
      padding: 22px;
      align-content: space-between;
      gap: 20px;
    }}
    .download-card.is-active {{ display: grid; }}
    .download-card.has-many-actions {{ width: min(660px, calc(100vw - 420px)); min-width: min(600px, 100%); }}
    .download-card h2 {{
      margin: 0 0 6px;
      font-size: 28px;
      font-weight: 500;
      line-height: 110%;
      letter-spacing: -0.84px;
    }}
    .download-card p {{
      margin: 0;
      font-size: 18px;
      line-height: 120%;
      opacity: .72;
    }}
    .actions {{
      display: grid;
      grid-template-columns: repeat(var(--action-count, 2), minmax(0, 1fr));
      gap: 12px;
    }}
    .actions a,
    .connect-button {{
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 44px;
      border-radius: 999px;
      background: #080808;
      color: #fff;
      text-decoration: none;
      font-size: 18px;
      line-height: 1;
      padding: 0 16px;
      white-space: nowrap;
      transition: transform .18s ease, background .18s ease, color .18s ease, box-shadow .18s ease;
    }}
    .actions a:hover,
    .connect-button:hover {{
      transform: translateY(-2px) scale(1.015);
      box-shadow: 0 14px 24px rgba(8,8,8,.16);
    }}
    .actions a:active,
    .connect-button:active {{ transform: translateY(0) scale(.99); }}
    .actions a.secondary {{ background: rgba(255,255,255,.62); color: #080808; }}
    .actions sup {{
      font-size: 11px;
      line-height: 0;
      margin-left: 2px;
      position: relative;
      top: -0.35em;
    }}
    .connect-button {{
      width: fit-content;
      min-width: 150px;
      min-height: 44px;
      background: rgba(8,8,8,.88);
      text-transform: lowercase;
    }}
    .support {{
      color: #080808;
      font-size: 20px;
      line-height: 120%;
      letter-spacing: -0.4px;
    }}
    .support a {{ color: inherit; text-underline-offset: 4px; }}
    @media (max-width: 760px) {{
      body {{ padding: 28px 16px; }}
      main {{ min-height: calc(100vh - 56px); gap: 56px; }}
      h1 {{ font-size: 44px; letter-spacing: -1.2px; }}
      .lead {{ font-size: 22px; letter-spacing: -0.5px; }}
      .tabs {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .downloads {{ width: 100%; }}
      .download-card,
      .download-card.has-many-actions {{ width: 100%; min-width: 0; }}
      .actions {{ grid-template-columns: 1fr; }}
    }}
    @media (min-width: 761px) and (max-width: 1100px) {{
      main {{ gap: 84px; }}
      h1 {{ font-size: 64px; letter-spacing: -1.8px; }}
    }}
  </style>
</head>
<body>
  <main>
    {logo_html}
    <h1>эту ссылку нельзя открыть<br>в браузере :(</h1>
    <section class="panel" aria-label="скачивание happ">
      <p class="lead">скачивай happ и подключайся!</p>
      <nav class="tabs" aria-label="платформа">{tabs}</nav>
      <div class="downloads">{cards}</div>
      <div class="connect-area">
        <p class="lead">уже скачал?</p>
        <a class="connect-button" href="{html.escape(connect_url)}" rel="noopener noreferrer">подключить</a>
      </div>
    </section>
    <div class="support">если возникли проблемы,<br>смело пиши <a href="{support}">t.me/bozhenkas</a></div>
  </main>
  <script>
    const tabs = [...document.querySelectorAll('.platform-tab')];
    const cards = [...document.querySelectorAll('.download-card')];
    function activate(platform) {{
      tabs.forEach(tab => tab.classList.toggle('is-active', tab.dataset.platform === platform));
      cards.forEach(card => card.classList.toggle('is-active', card.dataset.platform === platform));
    }}
    tabs.forEach(tab => tab.addEventListener('click', () => activate(tab.dataset.platform)));
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
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme and parsed.netloc:
            base = urllib.parse.quote(f"{parsed.scheme}://{parsed.netloc}", safe="")
            path = urllib.parse.quote(parsed.path or "/", safe="/")
            query = f"?{urllib.parse.quote(parsed.query, safe='=&')}" if parsed.query else ""
            fragment = f"#{urllib.parse.quote(parsed.fragment, safe='')}" if parsed.fragment else ""
            return f"{base}{path}{query}{fragment}"
        return urllib.parse.quote(url, safe="")

    def generate_json_profile(self, plain_body: str, *, ru_direct: bool = False) -> str:
        outbounds = []
        for idx, line in enumerate(plain_body.splitlines()):
            if not line.startswith("vless://"):
                continue
            parsed = urllib.parse.urlparse(line)
            qs = urllib.parse.parse_qs(parsed.query)
            outbound = {
                "tag": "proxy" if not outbounds else f"proxy-{idx}",
                "protocol": "vless",
                "settings": {
                    "vnext": [{
                        "address": parsed.hostname,
                        "port": parsed.port or 443,
                        "users": [{
                            "id": parsed.username,
                            "encryption": "none",
                            "flow": self._one(qs, "flow", ""),
                        }],
                    }]
                },
                "streamSettings": {
                    "network": self._one(qs, "type", "ws"),
                    "security": self._one(qs, "security", "tls"),
                },
            }
            network = outbound["streamSettings"]["network"]
            security = outbound["streamSettings"]["security"]
            if network == "ws":
                outbound["streamSettings"]["wsSettings"] = {
                    "path": self._one(qs, "path", "/"),
                    "headers": {"Host": self._one(qs, "host", self.domain)},
                }
            if security == "tls":
                outbound["streamSettings"]["tlsSettings"] = {
                    "serverName": self._one(qs, "sni", self.domain),
                    "fingerprint": self._one(qs, "fp", "chrome"),
                }
            outbounds.append(outbound)
        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {"tag": "socks", "listen": "127.0.0.1", "port": 10808, "protocol": "socks", "settings": {"udp": True}},
                {"tag": "http", "listen": "127.0.0.1", "port": 10809, "protocol": "http", "settings": {}},
            ],
            "outbounds": outbounds[:1] + [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"},
            ],
            "routing": self._json_routing(ru_direct=ru_direct),
        }
        return json.dumps(config, ensure_ascii=False, indent=2)

    @staticmethod
    def _json_routing(*, ru_direct: bool) -> dict[str, Any]:
        if not ru_direct:
            return {"rules": [{"type": "field", "network": "tcp,udp", "outboundTag": "proxy"}]}
        return {
            "domainStrategy": HAPP_ROUTING_PROFILE["DomainStrategy"],
            "rules": [
                {
                    "type": "field",
                    "ip": HAPP_ROUTING_PROFILE["DirectIp"],
                    "outboundTag": "direct",
                },
                {
                    "type": "field",
                    "domain": HAPP_ROUTING_PROFILE["DirectSites"],
                    "outboundTag": "direct",
                },
                {
                    "type": "field",
                    "network": "tcp,udp",
                    "outboundTag": "proxy",
                },
            ],
        }

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
            return unsupported_client_content(), ""
        seen = set(device_rows)
        seen.add(device_id)
        if user_limit and len(seen) > user_limit:
            msg = urllib.parse.quote(f"лимит: {user_limit} устройства. если ошибка - @bozhenkas")
            stub = f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443/?type=tcp&security=none#{msg}"
            return f"#profile-title: goida :) - лимит превышен\n{stub}", device_id
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
            if len(parts) >= 4 and parts[3].strip():
                return parts[3].strip()[:128]
        match = re.search(r"(?:hwid|device[-_ ]?id)[:=/ ]([A-Za-z0-9_.:-]{4,128})", user_agent, re.I)
        return match.group(1) if match else ""

    def remark(self, key: str, username: str, inbound: dict[str, Any]) -> str:
        if key == "smart":
            return f"smart-{username} 🇸🇨"
        if key == "smart-pro":
            return f"smart-pro-{username}⚡"
        if key == "zapret":
            return "ru-zapret (discord/youtube) 🇷🇺"
        suffix = inbound.get("remark_suffix", "")
        prefix = (inbound.get("prefix") or key).strip("-")
        if prefix == "fin":
            return f"fin-{username} 🇫🇮"
        if prefix == "swe":
            return f"swe-{username} 🇸🇪"
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
