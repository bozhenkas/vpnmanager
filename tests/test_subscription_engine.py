import base64
import json
import urllib.parse
import unittest

from subscription import (
    DEFAULT_DESCRIPTION,
    DEFAULT_SUPPORT_URL,
    HAPP_ROUTING_LINE,
    SubscriptionEngine,
    WL_JSON_PREFIX,
    decode_happ_routing_line,
    deleted_sub_content,
    happ_routing_line,
    invite_banned_content,
    invite_expired_content,
    invite_hour_expired_content,
    invite_temp_hour_content,
    invite_trial_expired_content,
    pick_line_by_remark,
    rename_remark,
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
        server_ips={"45.91.54.152"},
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
        self.assertIn("#profile-title: goida test-sub", lines)
        self.assertIn("#profile-web-page-url: https://t.me/vpngoidabot", lines)
        self.assertIn("# smart — оптимальный сервер.", lines)
        self.assertIn("Оптимальный", urllib.parse.unquote(body))
        self.assertNotIn("smart-pro-test-sub", base64_safe_decode_links(lines))
        self.assertIn("Финляндия", urllib.parse.unquote(body))
        self.assertIn("hysteria2://secret@example", body)
        self.assertIn("vless://custom@example", body)
        self.assertIn("vless://wl@example", body)
        # whitelist всегда в самом конце — после custom_sub
        self.assertGreater(body.index("vless://wl@example"), body.index("vless://custom@example"))

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

    def test_happ_routing_line_adds_manual_proxy_sites(self):
        line = happ_routing_line(["domain:lk.sut.ru", "domain:cabinet.sut.ru", "domain:lk.sut.ru"])
        payload = decode_happ_routing_line(line)
        self.assertEqual(payload["ProxySites"], ["domain:lk.sut.ru", "domain:cabinet.sut.ru"])
        self.assertIn("geosite:category-ru", payload["DirectSites"])
        self.assertIn("geoip:ru", payload["DirectIp"])

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
        self.assertEqual(content, "ok")

    def test_limit_guard_accepts_current_happ_hwid_format(self):
        content, device = engine().guard_ip_limit(
            token="token",
            client_ip="1.2.3.4",
            user_agent="Happ/4.10.2/ios/2605221402666",
            content="real subscription",
            device_rows={"hwid:2604271945694"},
            user_limit=2,
        )
        self.assertEqual(device, "hwid:2605221402666")
        self.assertEqual(content, "real subscription")

    def test_limit_guard_accepts_legacy_happ_hwid_format(self):
        content, device = engine().guard_ip_limit(
            token="token",
            client_ip="1.2.3.4",
            user_agent="Happ/iOS/1.0/HWID-1",
            content="real subscription",
            device_rows=set(),
            user_limit=2,
        )
        self.assertEqual(device, "hwid:HWID-1")
        self.assertEqual(content, "real subscription")

    def test_happ_user_agent_without_hwid_does_not_use_app_version(self):
        self.assertEqual(SubscriptionEngine.extract_hwid("Happ/4.10.2/ios"), "")
        self.assertEqual(SubscriptionEngine.extract_hwid("Happ/4.10.0"), "")

    def test_limit_guard_passes_clients_without_hwid_untracked(self):
        content, device = engine().guard_ip_limit(
            token="token",
            client_ip="1.2.3.4",
            user_agent="v2raytun ios",
            content="real subscription",
            device_rows=set(),
            user_limit=0,
        )
        self.assertEqual(device, "")
        self.assertEqual(content, "real subscription")

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
        self.assertEqual(profile["routing"]["domainStrategy"], "IPIfNonMatch")
        self.assertEqual(profile["routing"]["domainMatcher"], "hybrid")
        ip_rule = next(r for r in profile["routing"]["rules"] if "ip" in r)
        self.assertIn("geoip:ru", ip_rule["ip"])

    def test_json_profile_embeds_ru_direct_rules(self):
        body = engine().generate_plain(
            username="test-sub",
            clients_by_key={"smart": {"id": "11111111-1111-1111-1111-111111111111", "enable": True}},
        )
        profile = json.loads(engine().generate_json_profile(body, ru_direct=True))
        rules = profile["routing"]["rules"]
        self.assertEqual(profile["routing"]["domainStrategy"], "IPIfNonMatch")
        # «Оптимальный» теперь клиентский балансировщик — последнее правило → balancer
        self.assertEqual(rules[-1].get("balancerTag"), "smart-balancer")
        self.assertEqual(rules[0]["protocol"], ["bittorrent"])
        ip_rule = next(r for r in rules if "ip" in r)
        self.assertIn("geoip:ru", ip_rule["ip"])
        self.assertIn("10.0.0.0/8", ip_rule["ip"])
        dom_rule = next(r for r in rules
                        if r.get("outboundTag") == "direct" and "domain" in r
                        and "geosite:category-ru" in r["domain"])
        self.assertIn("domain:ru", dom_rule["domain"])
        self.assertIn("geosite:vk", dom_rule["domain"])
        self.assertIn("geosite:yandex", dom_rule["domain"])
        self.assertIn("domain:dropp.market", dom_rule["domain"])
        self.assertIn("domain:gradusi.net", dom_rule["domain"])
        self.assertIn("domain:pinterest.com", dom_rule["domain"])
        self.assertIn("domain:pinimg.com", dom_rule["domain"])

    def test_json_profile_can_force_2ip_for_diagnostics(self):
        body = engine().generate_plain(
            username="test-sub",
            clients_by_key={"smart": {"id": "11111111-1111-1111-1111-111111111111", "enable": True}},
        )
        profile = json.loads(engine().generate_json_profile(body, diagnostic_proxy=True))
        rules = profile["routing"]["rules"]
        diag = next(r for r in rules if "domain" in r and "domain:2ip.ru" in r["domain"])
        # диагностические сайты идут через балансер (foreign exit) — проверка IP
        self.assertEqual(diag.get("balancerTag"), "smart-balancer")
        ip_rule = next(r for r in rules if "ip" in r)
        self.assertIn("geoip:ru", ip_rule["ip"])

    def test_json_dns_defaults_to_cloudflare_and_opts_into_adblock(self):
        body = engine().generate_plain(
            username="test-sub",
            clients_by_key={"smart": {"id": "11111111-1111-1111-1111-111111111111", "enable": True}},
        )
        default = json.loads(engine().generate_json_profile(body, ru_direct=True))
        self.assertEqual(default["dns"]["servers"][0], "https+local://1.1.1.1/dns-query")
        self.assertNotIn("dns.goida.fun", json.dumps(default["dns"]))

        adblocked = json.loads(engine().generate_json_profile(body, ru_direct=True, adblock=True))
        self.assertEqual(adblocked["dns"]["servers"][0], "https+local://fin.goida.fun:5443/dns-query")
        # Cloudflare остаётся фоллбэком если AdGuard недоступен
        self.assertIn("https+local://1.1.1.1/dns-query", adblocked["dns"]["servers"])

    def test_smart_profile_is_client_balancer(self):
        body = engine().generate_plain(
            username="test-sub",
            clients_by_key={"smart": {"id": "11111111-1111-1111-1111-111111111111", "enable": True}},
        )
        profile = json.loads(engine().generate_json_profile(body, ru_direct=True))
        # ИНЦИДЕНТ 2026-06-25: SMART_OVER_HYDRA=True → «Оптимальный» балансит hydra-выходы
        # вместо мёртвых fin/fra/swe. Откат теста при SMART_OVER_HYDRA=False: selector
        # = proxy-fin/fra, fallback proxy-swe (см. git-историю / engine.py.bak).
        self.assertEqual(profile["remarks"], "Оптимальный 🇸🇨")
        tags = [o["tag"] for o in profile["outbounds"]]
        self.assertEqual(tags, ["proxy-hydra-nl", "proxy-hydra-de", "proxy-hydra-pol", "proxy-hydra-tur", "zapret", "direct", "block"])
        paths = {o["tag"]: o["streamSettings"]["wsSettings"]["path"] for o in profile["outbounds"][:5]}
        self.assertEqual(paths, {"proxy-hydra-nl": "/hydra-nl", "proxy-hydra-de": "/hydra-de",
                                 "proxy-hydra-pol": "/hydra-pol", "proxy-hydra-tur": "/hydra-tur", "zapret": "/direct"})
        self.assertEqual(profile["burstObservatory"]["subjectSelector"], ["proxy-hydra-nl", "proxy-hydra-pol", "proxy-hydra-tur"])
        bal = profile["routing"]["balancers"][0]
        self.assertEqual(bal["tag"], "smart-balancer")
        self.assertEqual(bal["selector"], ["proxy-hydra-nl", "proxy-hydra-pol", "proxy-hydra-tur"])
        self.assertEqual(bal["fallbackTag"], "proxy-hydra-de")
        self.assertEqual(bal["strategy"], {"type": "leastPing"})
        rules = profile["routing"]["rules"]
        yt = next(r for r in rules if r.get("outboundTag") == "zapret")["domain"]
        self.assertIn("domain:googlevideo.com", yt)
        self.assertIn("geosite:youtube", yt)
        self.assertFalse(any("gstatic" in d or "googleapis" in d or "googleusercontent" in d for d in yt))
        discord = next(r for r in rules if r.get("outboundTag") == "zapret" and "geosite:discord" in r.get("domain", []))
        self.assertIn("domain:discord.com", discord["domain"])
        self.assertFalse(any("telegram" in d or d == "domain:t.me" for d in discord["domain"]))
        self.assertTrue(any(
            r.get("outboundTag") == "zapret"
            and r.get("network") == "udp"
            and r.get("port") == "50000-65535"
            for r in rules
        ))
        self.assertEqual(rules[-1].get("balancerTag"), "smart-balancer")
        self.assertTrue(any(r.get("outboundTag") == "direct" and "ip" in r and "geoip:ru" in r["ip"] for r in rules))
        # инцидент-режим без «Резервного» — selector hydra nl/pol/tur, fallback hydra-de
        bal = profile["routing"]["balancers"][0]
        self.assertEqual(bal["selector"], ["proxy-hydra-nl", "proxy-hydra-pol", "proxy-hydra-tur"])
        self.assertEqual(bal["fallbackTag"], "proxy-hydra-de")

    def test_smart_balancer_adds_reserve_last_resort(self):
        body = "\n".join([
            "vless://11111111-1111-1111-1111-111111111111@ru.goida.fun:443/?type=ws&security=tls&sni=ru.goida.fun&host=ru.goida.fun&path=%2Fsmart#Оптимальный 🇸🇨",
            "vless://22222222-2222-2222-2222-222222222222@reserve.goida.fun:443/?type=grpc&security=reality&serviceName=grpc&mode=gun&sni=web.max.ru&pbk=ABCdef&sid=deadbeef&fp=chrome#Резервный 🇰🇵 (мобильная связь)",
        ])
        profiles = json.loads(engine().generate_json_profile(body, ru_direct=True))
        if isinstance(profiles, dict):
            profiles = [profiles]
        # ИНЦИДЕНТ 2026-06-25: SMART_OVER_HYDRA=True → selector = hydra-выходы, reserve фоллбэк
        sm = next(p for p in profiles if p.get("remarks") == "Оптимальный 🇸🇨")
        tags = [o["tag"] for o in sm["outbounds"]]
        self.assertEqual(tags, ["proxy-hydra-nl", "proxy-hydra-de", "proxy-hydra-pol", "proxy-hydra-tur", "zapret", "proxy-reserve", "direct", "block"])
        bal = sm["routing"]["balancers"][0]
        self.assertEqual(bal["selector"], ["proxy-hydra-nl", "proxy-hydra-de", "proxy-hydra-pol", "proxy-hydra-tur"])
        self.assertEqual(bal["fallbackTag"], "proxy-reserve")
        self.assertEqual(bal["strategy"], {"type": "leastPing"})
        self.assertEqual(sm["burstObservatory"]["subjectSelector"], ["proxy-hydra-nl", "proxy-hydra-de", "proxy-hydra-pol", "proxy-hydra-tur"])
        self.assertNotIn("proxy-reserve", sm["burstObservatory"]["subjectSelector"])
        rsv = next(o for o in sm["outbounds"] if o["tag"] == "proxy-reserve")
        self.assertEqual(rsv["settings"]["vnext"][0]["address"], "reserve.goida.fun")
        self.assertEqual(rsv["streamSettings"]["network"], "grpc")
        self.assertEqual(rsv["streamSettings"]["security"], "reality")

    def test_smart_profile_target_mode_balances_fin_swe_without_fra(self):
        old = SubscriptionEngine.SMART_OVER_HYDRA
        SubscriptionEngine.SMART_OVER_HYDRA = False
        try:
            body = "\n".join([
                "vless://11111111-1111-1111-1111-111111111111@ru.goida.fun:443/?type=ws&security=tls&sni=ru.goida.fun&host=ru.goida.fun&path=%2Fsmart#Оптимальный 🇸🇨",
                "vless://22222222-2222-2222-2222-222222222222@reserve.goida.fun:443/?type=grpc&security=reality&serviceName=grpc&mode=gun&sni=web.max.ru&pbk=ABCdef&sid=deadbeef&fp=chrome#Резервный 🇰🇵 (мобильная связь)",
            ])
            sm = json.loads(engine().generate_json_profile(body, ru_direct=True))
            if isinstance(sm, list):
                sm = next(p for p in sm if p.get("remarks") == "Оптимальный 🇸🇨")
        finally:
            SubscriptionEngine.SMART_OVER_HYDRA = old

        tags = [o["tag"] for o in sm["outbounds"]]
        self.assertEqual(tags, ["proxy-fin", "proxy-swe", "zapret", "proxy-reserve", "direct", "block"])
        paths = {o["tag"]: o["streamSettings"]["wsSettings"]["path"] for o in sm["outbounds"][:3]}
        self.assertEqual(paths, {"proxy-fin": "/fin", "proxy-swe": "/swe", "zapret": "/direct"})
        self.assertNotIn("proxy-fra", tags)
        self.assertEqual(sm["burstObservatory"]["subjectSelector"], ["proxy-fin", "proxy-swe"])
        bal = sm["routing"]["balancers"][0]
        self.assertEqual(bal["selector"], ["proxy-fin", "proxy-swe"])
        self.assertEqual(bal["fallbackTag"], "proxy-reserve")
        rules = sm["routing"]["rules"]
        yt = next(r for r in rules if r.get("outboundTag") == "zapret")["domain"]
        self.assertIn("geosite:youtube", yt)
        discord = next(r for r in rules if r.get("outboundTag") == "zapret" and "geosite:discord" in r.get("domain", []))
        self.assertFalse(any("telegram" in d or d == "domain:t.me" for d in discord["domain"]))
        self.assertTrue(any(r.get("outboundTag") == "direct" and "ip" in r and "geoip:ru" in r["ip"] for r in rules))

    def test_json_profile_expands_full_whitelist_json_marker(self):
        wl_profile = {
            "remarks": "Whitelist#9",
            "outbounds": [
                {
                    "tag": "proxy",
                    "protocol": "vless",
                    "settings": {
                        "vnext": [{
                            "address": "wl.example",
                            "port": 443,
                            "users": [{"id": "11111111-1111-1111-1111-111111111111", "encryption": "none"}],
                        }]
                    },
                    "streamSettings": {"network": "xhttp", "security": "tls"},
                },
                {"tag": "direct", "protocol": "freedom"},
            ],
            "routing": {"rules": [{"type": "field", "outboundTag": "proxy"}]},
        }
        marker = WL_JSON_PREFIX + base64.urlsafe_b64encode(
            json.dumps(wl_profile, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        body = "\n".join([
            "vless://11111111-1111-1111-1111-111111111111@ru.goida.fun:443/?type=ws&security=tls#main",
            marker,
        ])
        profiles = json.loads(engine().generate_json_profile(body))
        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[1]["remarks"], "Whitelist#9")
        self.assertEqual(profiles[1]["outbounds"][0]["streamSettings"]["network"], "xhttp")

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
        self.assertIn("happ://add/https%3A%2F%2Fru.goida.fun%2Fsubscribe-next%2Ftoken", page)
        self.assertIn("connect-button", page)
        self.assertIn("has-many-actions", page)
        self.assertIn("--action-count:3", page)
        self.assertIn('class="logo"', page)
        self.assertNotIn("logo-box", page)
        self.assertNotIn("data-election-tilt", page)
        self.assertNotIn("pointermove", page)
        self.assertIn("t.me/bozhenkas", page)
        self.assertNotIn("vless://", page)

    def test_rename_remark_appends_and_is_idempotent(self):
        line = "vless://11111111-1111-1111-1111-111111111111@ru.goida.fun:443/?type=ws&security=tls&sni=ru.goida.fun&host=ru.goida.fun&path=%2Fsmart#Оптимальный 🇸🇨"
        renamed = rename_remark(line, " [1 час]")
        self.assertEqual(renamed, line + " [1 час]")
        twice = rename_remark(renamed, " [1 час]")
        self.assertEqual(twice, renamed)
        self.assertIn("path=%2Fsmart", renamed)
        self.assertIn("sni=ru.goida.fun", renamed)

    def test_pick_line_by_remark_finds_and_misses(self):
        lines = [
            "#profile-title: goida test",
            "vless://11111111-1111-1111-1111-111111111111@ru.goida.fun:443/?type=ws&security=tls#Оптимальный 🇸🇨",
            "vless://22222222-2222-2222-2222-222222222222@ru.goida.fun:443/?type=ws&security=tls#Финляндия 🇫🇮",
        ]
        found = pick_line_by_remark(lines, "Финляндия 🇫🇮")
        self.assertTrue(found.startswith("vless://2222"))
        self.assertIsNone(pick_line_by_remark(lines, "не найдено"))

    def test_invite_temp_hour_content_has_real_and_fake_lines(self):
        real_line = rename_remark(
            "vless://11111111-1111-1111-1111-111111111111@ru.goida.fun:443/?type=ws&security=tls&sni=ru.goida.fun&host=ru.goida.fun&path=%2Fsmart#Оптимальный 🇸🇨",
            " [1 час]",
        )
        deep_link = "https://t.me/vpngoidabot?start=link_abc123"
        content = invite_temp_hour_content(real_line, deep_link)
        lines = content.splitlines()
        self.assertTrue(lines[0].startswith("#profile-title:"))
        vless_lines = [l for l in lines if l.startswith("vless://")]
        self.assertEqual(len(vless_lines), 2)
        self.assertIn(real_line, vless_lines)
        fake_lines = [l for l in vless_lines if l != real_line]
        self.assertEqual(len(fake_lines), 1)
        self.assertEqual(SubscriptionEngine._link_remark(real_line), "Оптимальный 🇸🇨 [1 час]")
        decoded_fake = urllib.parse.unquote(fake_lines[0])
        self.assertIn(deep_link, decoded_fake)

    def test_invite_hour_expired_content_contains_deep_link(self):
        deep_link = "https://t.me/vpngoidabot?start=link_abc123"
        content = invite_hour_expired_content(deep_link)
        self.assertTrue(content.splitlines()[0].startswith("#profile-title:"))
        decoded = urllib.parse.unquote(content)
        self.assertIn(deep_link, decoded)
        self.assertIn("телеграм", decoded)

    def test_invite_expired_content_mentions_expiry(self):
        content = invite_expired_content()
        self.assertTrue(content.splitlines()[0].startswith("#profile-title:"))
        decoded = urllib.parse.unquote(content)
        self.assertIn("не активно", decoded)
        self.assertIn("vpngoidabot", decoded)

    def test_invite_banned_content_mentions_ended_subscription(self):
        content = invite_banned_content()
        self.assertTrue(content.splitlines()[0].startswith("#profile-title:"))
        decoded = urllib.parse.unquote(content)
        self.assertIn("подписка закончилась", decoded)
        self.assertIn("vpngoidabot", decoded)

    def test_invite_trial_expired_content_mentions_trial(self):
        content = invite_trial_expired_content()
        self.assertTrue(content.splitlines()[0].startswith("#profile-title:"))
        decoded = urllib.parse.unquote(content)
        self.assertIn("триал закончился", decoded)
        self.assertIn("оплату", decoded)

    def test_invite_temp_hour_content_survives_json_profile_conversion(self):
        real_line = rename_remark(
            "vless://11111111-1111-1111-1111-111111111111@ru.goida.fun:443/?type=ws&security=tls&sni=ru.goida.fun&host=ru.goida.fun&path=%2Fsmart#Оптимальный 🇸🇨",
            " [1 час]",
        )
        deep_link = "https://t.me/vpngoidabot?start=link_abc123"
        content = invite_temp_hour_content(real_line, deep_link)
        profiles = engine().json_profiles(content, ru_direct=True)
        self.assertGreaterEqual(len(profiles), 1)
        vless_profiles = [
            p for p in profiles
            if any(o.get("protocol") == "vless" for o in p.get("outbounds", []))
        ]
        self.assertGreaterEqual(len(vless_profiles), 1)
        real_profile = next(p for p in vless_profiles if p.get("remarks") == "Оптимальный 🇸🇨 [1 час]")
        proxy_outbound = next(o for o in real_profile["outbounds"] if o["tag"] == "proxy")
        self.assertEqual(proxy_outbound["protocol"], "vless")
        self.assertEqual(proxy_outbound["streamSettings"]["network"], "ws")

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
        self.assertEqual(headers["Support-Url"], "https://t.me/vpngoidabot")
        self.assertEqual(headers["Profile-Web-Page-Url"], "https://t.me/vpngoidabot")
        self.assertEqual(headers["Subscription-Userinfo"], "upload=123; download=456; total=0; expire=0")
        self.assertTrue(headers["Announce"].startswith("base64:"))


def base64_safe_decode_links(lines):
    return "\n".join([line for line in lines]).replace("%20", " ")


if __name__ == "__main__":
    unittest.main()
