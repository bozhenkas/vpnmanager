import base64
import json
import unittest

from subscription import (
    DEFAULT_DESCRIPTION,
    DEFAULT_SUPPORT_URL,
    HAPP_ROUTING_LINE,
    SubscriptionEngine,
    decode_happ_routing_line,
    deleted_sub_content,
    unsupported_client_content,
)


def engine():
    return SubscriptionEngine(
        domain="ru.goida.fun",
        inbounds={
            "smart": {"id": 5, "path": "/smart", "prefix": "", "stream_settings": ws_stream("/smart")},
            "smart-pro": {"id": 16, "path": "/smart-pro", "prefix": "", "stream_settings": ws_stream("/smart-pro")},
            "fi": {"id": 1, "path": "/fi", "prefix": "fin-", "remark_suffix": "🇫🇮", "stream_settings": ws_stream("/fi")},
        },
        hydra_inbounds={
            "nl": {"path": "/nl", "flag": "🇳🇱"},
        },
        hydra_country_names={"nl": "Нидерланды"},
        hysteria_link="hysteria2://secret@example:8443/?sni=fin.goida.fun#hysteria2",
        server_ips={"83.147.255.98"},
    )


def ws_stream(path):
    return {
        "network": "ws",
        "security": "tls",
        "wsSettings": {"path": path, "headers": {"Host": "ru.goida.fun"}},
        "tlsSettings": {"serverName": "ru.goida.fun"},
    }


class SubscriptionEngineTest(unittest.TestCase):
    def test_plain_subscription_preserves_shape_and_extras(self):
        body = engine().generate_plain(
            username="test-sub",
            clients_by_key={
                "smart": {"id": "11111111-1111-1111-1111-111111111111", "enable": True},
                "smart-pro": {"id": "22222222-2222-2222-2222-222222222222", "enable": True},
                "fi": {"id": "33333333-3333-3333-3333-333333333333", "enable": True},
            },
            hydra_clients_by_key={"nl": {"id": "44444444-4444-4444-4444-444444444444", "enable": True}},
            custom_sub="vless://custom@example:443#custom",
            hysteria_enabled=True,
            wl_enabled=True,
            wl_links=["vless://wl@example:443#wl"],
        )
        lines = body.splitlines()
        self.assertTrue(lines[0].startswith("happ://routing/onadd/"))
        self.assertIn("#profile-title: goida :)", lines)
        self.assertIn("#profile-web-page-url: https://t.me/bozhenkas", lines)
        self.assertIn("# smart — оптимальный сервер.", lines)
        self.assertIn("smart-test-sub", base64_safe_decode_links(lines))
        self.assertNotIn("smart-pro-test-sub", base64_safe_decode_links(lines))
        self.assertIn("fin-test-sub", base64_safe_decode_links(lines))
        self.assertIn("hysteria2://secret@example", body)
        self.assertIn("vless://custom@example", body)
        self.assertIn("vless://wl@example", body)

    def test_smart_pro_only_for_owner(self):
        body = engine().generate_plain(
            username="bozhenkas",
            clients_by_key={
                "smart": {"id": "11111111-1111-1111-1111-111111111111", "enable": True},
                "smart-pro": {"id": "22222222-2222-2222-2222-222222222222", "enable": True},
            },
        )
        self.assertIn("smart-pro-bozhenkas", base64_safe_decode_links(body.splitlines()))

    def test_public_ws_link_forces_tls_when_xray_stream_is_plain(self):
        eng = SubscriptionEngine(
            domain="ru.goida.fun",
            inbounds={"smart": {"path": "/smart", "stream_settings": ws_stream("/smart")}},
        )
        eng.inbounds["smart"]["stream_settings"]["security"] = "none"
        link = eng.vless_link(
            {"id": "11111111-1111-1111-1111-111111111111", "enable": True},
            eng.inbounds["smart"],
            "smart",
            "test-sub",
        )
        self.assertIn("security=tls", link)
        self.assertIn("sni=ru.goida.fun", link)

    def test_happ_payload_keeps_current_body_compatibility_key(self):
        payload = decode_happ_routing_line(HAPP_ROUTING_LINE)
        self.assertIn("DirectIp", payload)
        self.assertNotIn("DirectIP", payload)
        self.assertEqual(payload["GlobalProxy"], "true")
        self.assertEqual(payload["DomainStrategy"], "IPIfNonMatch")
        self.assertIn("geoip:ru", payload["DirectIp"])
        self.assertIn("geosite:category-ru", payload["DirectSites"])

    def test_deleted_stub_is_base64_safe(self):
        encoded = SubscriptionEngine.encode_body(deleted_sub_content())
        decoded = base64.b64decode(encoded).decode()
        self.assertIn("пользователь удален", decoded)
        self.assertIn("00000000-0000-0000-0000-000000000000", decoded)

    def test_limit_guard_uses_happ_hwid(self):
        content, device = engine().guard_ip_limit(
            token="token",
            client_ip="1.2.3.4",
            user_agent="Happ/iOS/1.0/HWID-1",
            content="ok",
            device_rows={"hwid:OLD-1", "hwid:OLD-2", "hwid:OLD-3", "hwid:OLD-4"},
            user_limit=4,
        )
        self.assertEqual(device, "hwid:HWID-1")
        self.assertIn("лимит превышен", content)

    def test_limit_guard_rejects_clients_without_hwid(self):
        content, device = engine().guard_ip_limit(
            token="token",
            client_ip="1.2.3.4",
            user_agent="v2raytun ios",
            content="real subscription",
            device_rows=set(),
            user_limit=0,
        )
        self.assertEqual(device, "")
        self.assertIn("клиент не поддерживается", content)
        self.assertIn("скачайте Happ", content)
        self.assertNotIn("real subscription", content)

    def test_limit_guard_accepts_generic_hwid_marker(self):
        content, device = engine().guard_ip_limit(
            token="token",
            client_ip="1.2.3.4",
            user_agent="SomeClient/1.0 hwid=ABC-12345",
            content="real subscription",
            device_rows=set(),
            user_limit=0,
        )
        self.assertEqual(device, "hwid:ABC-12345")
        self.assertEqual(content, "real subscription")

    def test_clash_and_json_outputs_are_parseable_enough(self):
        body = engine().generate_plain(
            username="test-sub",
            clients_by_key={"smart": {"id": "11111111-1111-1111-1111-111111111111", "enable": True}},
        )
        clash = engine().generate_clash(body)
        self.assertIn("proxies:", clash)
        self.assertIn("proxy-groups:", clash)
        self.assertIn("MATCH,PROXY", clash)
        profile = json.loads(engine().generate_json_profile(body))
        self.assertEqual(profile["outbounds"][0]["protocol"], "vless")
        self.assertEqual(profile["outbounds"][0]["streamSettings"]["network"], "ws")
        self.assertNotIn("domainStrategy", profile["routing"])

    def test_json_profile_can_include_ru_direct_rules_for_shadow_subscription(self):
        body = engine().generate_plain(
            username="test-sub",
            clients_by_key={"smart": {"id": "11111111-1111-1111-1111-111111111111", "enable": True}},
        )
        profile = json.loads(engine().generate_json_profile(body, ru_direct=True))
        self.assertEqual(profile["routing"]["domainStrategy"], "IPIfNonMatch")
        self.assertEqual(profile["routing"]["rules"][-1]["outboundTag"], "proxy")
        self.assertIn("geoip:ru", profile["routing"]["rules"][0]["ip"])
        self.assertIn("geosite:category-ru", profile["routing"]["rules"][1]["domain"])
        self.assertIn("10.0.0.0/8", profile["routing"]["rules"][0]["ip"])

    def test_clash_unsupported_stub_does_not_leak_real_nodes(self):
        clash = engine().generate_clash_unsupported()
        self.assertIn("📱 Приложение не поддерживается!", clash)
        self.assertIn("❗ Скачайте приложение HAPP!", clash)
        self.assertIn("packet-encoding: xudp", clash)
        self.assertIn("→ Remnawave", clash)
        self.assertNotIn("smart-test-sub", clash)
        self.assertNotIn("ru.goida.fun", clash)

    def test_browser_stub_contains_downloads_and_no_subscription_body(self):
        page = engine().browser_stub_html(
            logo_svg="<svg></svg>",
            subscription_url="https://ru.goida.fun/subscribe-next/token",
        )
        self.assertIn("эту ссылку нельзя открыть", page)
        self.assertIn("скачивай happ и подключайся", page)
        self.assertIn("https://apps.apple.com/us/app/happ-proxy-utility/id6504287215", page)
        self.assertIn("https://play.google.com/store/apps/details?id=com.happproxy", page)
        self.assertIn("https://github.com/Happ-proxy/happ-desktop/releases/latest", page)
        self.assertIn("AppStore <sup>[ru]</sup>", page)
        self.assertIn("AppStore <sup>[global]</sup>", page)
        self.assertIn("happ://add/https%3A%2F%2Fru.goida.fun/subscribe-next/token", page)
        self.assertIn("connect-button", page)
        self.assertIn("has-many-actions", page)
        self.assertIn("--action-count:3", page)
        self.assertIn('class="logo"', page)
        self.assertNotIn("logo-box", page)
        self.assertNotIn("data-election-tilt", page)
        self.assertNotIn("pointermove", page)
        self.assertIn("t.me/bozhenkas", page)
        self.assertNotIn("vless://", page)

    def test_headers_include_description_support_and_traffic(self):
        headers = engine().normal_headers(
            "body",
            description=DEFAULT_DESCRIPTION,
            support_url=DEFAULT_SUPPORT_URL,
            upload=123,
            download=456,
            total=0,
            expire=0,
        )
        self.assertEqual(headers["Support-Url"], "https://t.me/bozhenkas")
        self.assertEqual(headers["Profile-Web-Page-Url"], "https://t.me/bozhenkas")
        self.assertEqual(headers["Subscription-Userinfo"], "upload=123; download=456; total=0; expire=0")
        self.assertTrue(headers["Announce"].startswith("base64:"))


def base64_safe_decode_links(lines):
    return "\n".join([line for line in lines]).replace("%20", " ")


if __name__ == "__main__":
    unittest.main()
