# Remnawave cleanup inventory (2026-06-06)

> Phase A deliverable. Live DB dump attempted 2026-06-06: **RU `45.91.54.152` SSH/HTTPS unreachable** from agent env (port 17904 open, banner exchange timeout; HTTPS timeout). Tables below combine **code analysis** + **`infrastructure-live-20260605.md`** checkpoint. Re-run live queries when RU is reachable before Phase C.

## Blockers

| ID | Blocker | Impact |
|----|---------|--------|
| B1 | RU SSH/HTTPS timeout 2026-06-06 | Cannot verify live hosts/squads/HWID counts; Phase C needs refresh |
| B2 | `sync_remna_hydra.backfill_hydra_members` | Propagates `HYDRA_*_REMNA` to any user ever in a Hydra squad |
| B3 | `remnawave_create_user` | Adds all active Hydra squads on user create |
| B4 | `remnawave_subscription_links(include_hydra=True)` default | Hydra links if squad present, not if `/hydra` enabled |
| B5 | `hydra_get_status` (Remnawave) | Checks squad membership, not vpn-bot flag |
| B6 | `client-web` `CORE_KEYS` / `SRV_MAP` | No `hydra:*` keys in Mini App display map |
| B7 | `client-bot.hydra_client_enabled` | Legacy x-ui only; Remnawave path uses squad membership |

## Nodes (checkpoint 2026-06-05)

| node | address | status |
|------|---------|--------|
| ru-smart-goida | 45.91.54.152 | connected |
| fin-goida | 77.110.108.57 | connected |
| fra-goida | 95.163.152.210 | connected |
| swe-goida | 89.22.230.5 | connected |
| home-goida | 78.107.88.21 | connected |

## Ingress profile `ru-ws-ingress`

UUID: `11111111-7443-4000-8000-000000000001`

| tag (current) | nginx path | loopback | squad | action |
|---------------|------------|----------|-------|--------|
| `RU_WS_SMART` | `/smart` | 17443 | `SMART_RU_REMNA` | rename → `GOIDA_SMART` |
| `RU_WS_DIRECT` | `/direct` | 17444 | `SMART_RU_REMNA` | rename → `GOIDA_RU` |
| `RU_WS_FIN` | `/fin` | 17445 | `SMART_REMNA` | rename → `GOIDA_FIN` |
| `RU_WS_FRA` | `/fra` | 17446 | `FRA` | rename → `GOIDA_FRA` |
| `RU_WS_SWE` | `/swe` | 17447 | `SMART_REMNA` | rename → `GOIDA_SWE` |
| `RU_WS_HOME` | `/home` | 17448 | `HOME_REMNA` | **KEEP** (home-exit) |
| `RU_REALITY_GRPC_RESERVE` | reserve.goida.fun:443 | 2053 | `SMART_RU_REMNA` | rename → `GOIDA_RESERVE` |
| `RU_WS_HYDRA_NL` | `/hydra-nl` | 17460 | `HYDRA_NL_REMNA` | rename → `GOIDA_HYDRA_NL` |
| `RU_WS_HYDRA_DE` | `/hydra-de` | 17461 | `HYDRA_DE_REMNA` | rename → `GOIDA_HYDRA_DE` |
| `RU_WS_HYDRA_POL` | `/hydra-pol` | 17463 | `HYDRA_POL_REMNA` | rename → `GOIDA_HYDRA_POL` |
| `RU_WS_HYDRA_TUR` | `/hydra-tur` | 17464 | `HYDRA_TUR_REMNA` | rename → `GOIDA_HYDRA_TUR` |

Paths **unchanged** in migration (only tags + subscription remarks).

## Foreign / home technical tags (separate profiles)

| tag | profile | action |
|-----|---------|--------|
| `REMNA_VLESS_TCP_REALITY_7443` | foreign-reality | controlled rename → `GOIDA_FOREIGN_REALITY` (atomic with cpitn + node push) |
| `HOME_VLESS_TCP_REALITY_7443` | home-exit | **KEEP** |

## Squads

| squad | inbounds (expected) | notes |
|-------|---------------------|-------|
| `SMART_RU_REMNA` | smart, direct, reserve | subscription core RU |
| `SMART_REMNA` | FIN, SWE + `REMNA_VLESS_TCP_REALITY_7443` | **critical** foreign shared inbound |
| `FRA` | FRA WS + foreign Reality | |
| `HOME_REMNA` | home | do not touch |
| `HYDRA_*_REMNA` | per-country hydra WS | gated by vpn-bot `/hydra` after Phase D |

## Hosts — delete candidates

Public nginx `:7443` **closed** 2026-06-05. Panel `hosts` entries on `:7443` are legacy unless home-exit.

| criterion | action |
|-----------|--------|
| `port = 7443` AND NOT home (`78.107.88.21` / `HOME` remark) | **DELETE** |
| `ru.goida.fun:7443` WS hosts | **DELETE** (subscription uses `:443`) |
| home `78.107.88.21:7443` | **KEEP** |

Verify with before Phase C:

```sql
select remark, address, port, path from hosts order by port, remark;
select remark, address, port from hosts where port=7443;
```

## Subscription output (vpn-bot)

Current `remnawave_subscription_links` (port **443**, not 7443):

| remark | path |
|--------|------|
| Оптимальный 🇸🇨 | `/smart` |
| Резервный 🇰🇵 | reserve.goida.fun:443 gRPC |
| Финляндия 🇫🇮 | `/fin` |
| Франция 🇫🇷 | `/fra` |
| Швеция 🇸🇪 | `/swe` |
| Русский (YouTube, Discord) 🇷🇺 | `/direct` |
| Hydra (сторонний) | `/hydra-{de,nl,pol,tur}` if squads |

## Bots — code map

| component | file | issue |
|-----------|------|-------|
| subscription hydra | `bot/vpn-bot.py` `remnawave_subscription_links` | default `include_hydra=True` |
| hydra toggle | `bot/vpn-bot.py` `/hydra` → `remnawave_set_hydra` | sets squads only, no bot.db flag |
| hydra sync | `scripts/sync_remna_hydra.py` | backfill all hydra-on users to new squads |
| client catalog | `client-bot/client-bot.py` `remnawave_server_catalog` | reserve/fra present; hydra needs flag gate |
| Mini App UI | `client-web/index.html` | missing `hydra:*` in `SRV_MAP` |

## Devices — cleanup targets

Script: `scripts/cleanup_user_devices.py`

| user | layers | synthetic patterns to add |
|------|--------|---------------------------|
| bozhenkas | `bot.db` + `hwid_user_devices` | `shadow`, `shadow-smoke`, smoke HWID |
| test-sub | both | test HWID |
| remnatest | both | test HWID |

Current `SYNTHETIC_RE`: `CHECK|TEST|test-hwid|VK-ROUTE|BOOSTY` — extend for shadow/smoke.

## Routing — do not change this wave

- smart/fin/fra/swe/reserve: Telegram → foreign
- direct (`RU_WS_DIRECT`): owner override — all → `DIRECT` (zapret experiment for Telegram)
- Happ JSON subscription: keep

## Phase transition

| Phase | OK needed | this doc |
|-------|-----------|----------|
| B migration spec | after owner reads inventory | input |
| C prod cleanup | after spec OK + RU reachable | execute scripts |
| D bots | after C shadow OK | code ready in repo |
| E devices | dry-run → OK | script extended |
| F telegram zapret | after F report | rkn-checker probes added |

## Live refresh commands (when SSH works)

```bash
# on RU
docker exec remnawave-db psql -U postgres -d postgres -c "select name,is_connected from nodes;"
docker exec remnawave-db psql -U postgres -d postgres -c "select remark,address,port,path from hosts order by port;"
docker exec remnawave-db psql -U postgres -d postgres -c "select name from internal_squads order by 1;"
sqlite3 /root/vpn-bot/bot.db "select u.name, count(d.device_id) from users u left join user_devices d on d.token=u.token where u.name in ('bozhenkas','test-sub','remnatest') group by u.name;"
```
