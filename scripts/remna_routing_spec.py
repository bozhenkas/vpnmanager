#!/usr/bin/env python3
"""декларативная сборка routing.rules для RU Remnawave profile."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field


SMART_WS = "RU_WS_SMART"
GOIDA_SMART2 = "GOIDA_SMART2"
RESERVE_REALITY = "RU_REALITY_GRPC_RESERVE"
DIRECT_ZAPRET_INBOUND = "RU_WS_DIRECT"
FIN_INBOUND = "RU_WS_FIN"
FRA_INBOUND = "RU_WS_FRA"
SWE_INBOUND = "RU_WS_SWE"
HOME_INBOUND = "RU_WS_HOME"

DIRECT_OUT = "DIRECT"
BLOCK_OUT = "BLOCK"
HOME_OUT = "REMNA_HOME"
BALANCER_SMART = "BALANCER_FOREIGN_SMART"
FIN_OUT = "REMNA_FI"
FRA_OUT = "REMNA_FRA"
SWE_OUT = "REMNA_SWE"

# legacy: reserve в smart-балансере (pre-P.15). default policy — reserve_fin_only=True.
SMART_INBOUNDS = [SMART_WS, GOIDA_SMART2, RESERVE_REALITY]
SMART_ONLY_INBOUNDS = [SMART_WS, GOIDA_SMART2]
FIXED_INBOUNDS = [FIN_INBOUND, FRA_INBOUND, SWE_INBOUND]
CLIENT_INBOUNDS = SMART_INBOUNDS + FIXED_INBOUNDS + [DIRECT_ZAPRET_INBOUND]
CLIENT_INBOUNDS_P15 = SMART_ONLY_INBOUNDS + FIXED_INBOUNDS + [DIRECT_ZAPRET_INBOUND]

RU_SAFETY_IPS = [
    "45.91.54.152/32",
    "83.147.255.0/24",
    "194.117.80.94/32",
    "78.107.88.21/32",
]
CLUSTER_DIRECT_DOMAINS = [
    "domain:ru.goida.fun",
    "domain:web.goida.fun",
    "domain:reserve.goida.fun",
    "domain:ru-4.goida.fun",
    "domain:fin.goida.fun",
    "domain:swe.goida.fun",
    "domain:fra.goida.fun",
]

ZAPRET_SERVICE_DOMAINS = [
    "geosite:youtube",
    "geosite:discord",
    "domain:googlevideo.com",
    "domain:youtube.com",
    "domain:ytimg.com",
    "domain:youtu.be",
    "domain:discord.com",
    "domain:discord.gg",
    "domain:discordapp.com",
    "domain:discordapp.net",
]
YOUTUBE_QUIC_DOMAINS = [
    "geosite:youtube",
    "domain:googlevideo.com",
    "domain:youtube.com",
    "domain:ytimg.com",
    "domain:youtu.be",
]
TELEGRAM_IPS = [
    "91.108.4.0/22",
    "91.108.8.0/22",
    "91.108.12.0/22",
    "91.108.16.0/22",
    "91.108.20.0/22",
    "91.108.56.0/22",
    "95.161.64.0/20",
    "149.154.160.0/20",
    "185.76.151.0/24",
]
TELEGRAM_DOMAINS = [
    "geosite:telegram",
    "domain:t.me",
    "domain:telegram.org",
]


@dataclass
class RoutingSpec:
    """параметры целевой RU routing-policy."""

    direct_outbound: str = DIRECT_OUT
    direct_zapret_outbound: str = DIRECT_OUT
    home_outbound: str = HOME_OUT
    block_outbound: str = BLOCK_OUT
    smart_balancer: str = BALANCER_SMART
    smart_selector: list[str] = field(default_factory=lambda: [FIN_OUT, FRA_OUT])
    smart_fallback: str = SWE_OUT
    # P.15 (default): reserve ingress не в smart-балансере, catch-all -> FIN
    reserve_fin_only: bool = True
    ru_domain_rules: list[str] = field(default_factory=lambda: ["geosite:category-ru"])
    ru_ip_rules: list[str] = field(default_factory=lambda: ["geoip:ru"])
    extra_ru_ip_rules: list[str] = field(default_factory=lambda: ["139.45.0.0/16"])
    cluster_direct_domains: list[str] = field(default_factory=lambda: list(CLUSTER_DIRECT_DOMAINS))
    cluster_direct_ips: list[str] = field(default_factory=lambda: list(RU_SAFETY_IPS))
    zapret_domains: list[str] = field(default_factory=lambda: list(ZAPRET_SERVICE_DOMAINS))
    youtube_quic_domains: list[str] = field(default_factory=lambda: list(YOUTUBE_QUIC_DOMAINS))
    telegram_domains: list[str] = field(default_factory=lambda: list(TELEGRAM_DOMAINS))
    telegram_ips: list[str] = field(default_factory=lambda: list(TELEGRAM_IPS))


def normalize_domain(domain: str) -> str:
    domain = domain.strip().lower()
    return domain.split(":", 1)[1] if domain.startswith("domain:") else domain


def xray_domain(domain: str) -> str:
    domain = normalize_domain(domain)
    return domain if domain.startswith(("geosite:", "regexp:", "keyword:", "full:", "ext:")) else f"domain:{domain}"


def split_xray_domains_and_ips(items: list[str]) -> tuple[list[str], list[str]]:
    domains: list[str] = []
    ips: list[str] = []
    for item in items:
        value = normalize_domain(item)
        try:
            ipaddress.ip_network(value, strict=False)
            ips.append(value)
        except ValueError:
            domains.append(xray_domain(value))
    return domains, ips


def field_rule(**kwargs) -> dict:
    data = {"type": "field"}
    data.update({key: value for key, value in kwargs.items() if value not in (None, [], "")})
    return data


def unique_tags(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(tags))


def build_rules(grouped_manual: dict[str, list[str]], spec: RoutingSpec | None = None) -> list[dict]:
    """строит полный routing.rules без hydra-правил.

    grouped_manual: dict home/direct/foreign -> list domains or cidrs.
    hydra rules intentionally live outside this function and are appended by the apply script.
    """
    spec = spec or RoutingSpec()
    smart_balancer_inbounds = SMART_ONLY_INBOUNDS if spec.reserve_fin_only else SMART_INBOUNDS
    client_inbounds = CLIENT_INBOUNDS_P15 if spec.reserve_fin_only else CLIENT_INBOUNDS
    home_domains, home_ips = split_xray_domains_and_ips(grouped_manual.get("home", []))
    direct_domains, direct_ips = split_xray_domains_and_ips(grouped_manual.get("direct", []))
    foreign_domains, foreign_ips = split_xray_domains_and_ips(grouped_manual.get("foreign", []))

    rules: list[dict] = [
        field_rule(ip=["geoip:private"], outboundTag=spec.direct_outbound),
        field_rule(domain=["geosite:private"], outboundTag=spec.direct_outbound),
        field_rule(protocol=["bittorrent"], outboundTag=spec.block_outbound),
        field_rule(inboundTag=[HOME_INBOUND], outboundTag=spec.home_outbound),
    ]

    if home_domains:
        rules.append(field_rule(ruleTag="manual-home", inboundTag=client_inbounds, domain=home_domains, outboundTag=spec.home_outbound))
    if home_ips:
        rules.append(field_rule(ruleTag="manual-home-ip", inboundTag=client_inbounds, ip=home_ips, outboundTag=spec.home_outbound))
    if direct_domains:
        rules.append(field_rule(ruleTag="manual-direct", inboundTag=client_inbounds, domain=direct_domains, outboundTag=spec.direct_outbound))
    if direct_ips:
        rules.append(field_rule(ruleTag="manual-direct-ip", inboundTag=client_inbounds, ip=direct_ips, outboundTag=spec.direct_outbound))
    zapret_inbounds = smart_balancer_inbounds if spec.reserve_fin_only else SMART_INBOUNDS
    rules.extend([
        field_rule(ruleTag="block-youtube-quic", inboundTag=zapret_inbounds + [DIRECT_ZAPRET_INBOUND], domain=spec.youtube_quic_domains, network="udp", port="443", outboundTag=spec.block_outbound),
        field_rule(ruleTag="proxy-telegram-domain-foreign-smart", inboundTag=zapret_inbounds, domain=spec.telegram_domains, balancerTag=spec.smart_balancer),
        field_rule(ruleTag="proxy-telegram-ip-foreign-smart", inboundTag=zapret_inbounds, ip=spec.telegram_ips, balancerTag=spec.smart_balancer),
        field_rule(ruleTag="direct-zapret-services-domain", inboundTag=zapret_inbounds, domain=spec.zapret_domains, outboundTag=spec.direct_zapret_outbound),
        field_rule(ruleTag="direct-discord-voice-1", inboundTag=zapret_inbounds, network="udp", port="19294-19344", outboundTag=spec.direct_zapret_outbound),
        field_rule(ruleTag="direct-discord-voice-2", inboundTag=zapret_inbounds, network="udp", port="50000-65535", outboundTag=spec.direct_zapret_outbound),
    ])

    if foreign_domains:
        rules.append(field_rule(ruleTag="manual-foreign", inboundTag=smart_balancer_inbounds, domain=foreign_domains, balancerTag=spec.smart_balancer))
    if foreign_ips:
        rules.append(field_rule(ruleTag="manual-foreign-ip", inboundTag=smart_balancer_inbounds, ip=foreign_ips, balancerTag=spec.smart_balancer))

    rules.extend([
        field_rule(ruleTag="direct-goida-cluster-domain", inboundTag=unique_tags(client_inbounds + [RESERVE_REALITY]), domain=spec.cluster_direct_domains, outboundTag=spec.direct_outbound),
        field_rule(ruleTag="direct-goida-cluster-ip", inboundTag=unique_tags(client_inbounds + [RESERVE_REALITY]), ip=spec.cluster_direct_ips, outboundTag=spec.direct_outbound),
        field_rule(ruleTag="direct-ru-domain", inboundTag=client_inbounds, domain=spec.ru_domain_rules, outboundTag=spec.direct_outbound),
        field_rule(ruleTag="direct-ru-ip", inboundTag=client_inbounds, ip=spec.ru_ip_rules + spec.extra_ru_ip_rules, outboundTag=spec.direct_outbound),
        field_rule(ruleTag="direct-catch-all", inboundTag=[DIRECT_ZAPRET_INBOUND], network="tcp,udp", outboundTag=spec.direct_zapret_outbound),
        field_rule(inboundTag=[FIN_INBOUND], network="tcp,udp", outboundTag=FIN_OUT),
        field_rule(inboundTag=[FRA_INBOUND], network="tcp,udp", outboundTag=FRA_OUT),
        field_rule(inboundTag=[SWE_INBOUND], network="tcp,udp", outboundTag=SWE_OUT),
    ])
    if spec.reserve_fin_only:
        rules.append(field_rule(
            ruleTag="reserve-fin-catch-all",
            inboundTag=[RESERVE_REALITY],
            network="tcp,udp",
            outboundTag=FIN_OUT,
        ))
    rules.append(field_rule(
        ruleTag="foreign-smart-catch-all",
        inboundTag=smart_balancer_inbounds,
        network="tcp,udp",
        balancerTag=spec.smart_balancer,
    ))
    return rules


def is_hydra_rule(rule: dict) -> bool:
    target = str(rule.get("outboundTag") or rule.get("balancerTag") or "")
    return target.startswith("HYDRA_") or target.startswith("BALANCER_HYDRA_")
