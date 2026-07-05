# goida-intermesh — actuator переключения RU→FIN/SWE/FRA транспорта

> Локальная ручка для будущего анализатора. Здесь только «как переключить».
> Решение «когда переключать» будет жить в отдельном analyzer/watchdog.

## модель

Целевая схема Remnawave-first:

```text
client -> nginx /fin|/swe|/fra -> remnanode ru-ws-ingress
  -> GOIDA_FIN/SWE/FRA inbound
  -> Remnawave routing
  -> REMNA_FI/SWE/FRA outbound
       primary: real exit :443 / epilepsy
       fallback: local Hysteria2+salamander tcpForwarding port
```

- `xhttp-reality` = основной транспорт RU→exit: xHTTP Reality, внешне только `:443`.
- `epilepsy` = primary для FRA: PostgreSQL-camouflage chain через локальный RU forward `127.0.0.1:17452`.
- `hy2-fallback` = fallback: Hysteria2+salamander, внешне `:8443/udp` или отдельный `:7443/udp`; на RU это локальный socks5 или xray-compatible tcpForwarding.
- `disabled` = outbound уводится в локальный closed target (`127.0.0.1:9`) или отдельный block-профиль, чтобы не выпускать трафик через сломанный выход.
- `repair` = линк помечен как ремонтный, но routing направляется на выбранный stage для shadow-проверки анализатором/оператором.

`switch.py` в backend `remnawave` не владеет секретами: он читает live `ru-ws-ingress` из Remnawave Postgres, меняет только `settings.vnext[0].address/port` у нужного `REMNA_*` outbound, создаёт backup table `config_profiles_bak_*`, сохраняет profile и рестартит `remnanode`.

## API

Bind только `127.0.0.1:9099`, авторизация заголовком `X-Goida-Switch-Token`.

```text
GET  /status
POST /switch {"link":"fin","stage":"xhttp-reality"}
POST /switch {"link":"fin","stage":"hy2-fallback"}
POST /switch {"link":"fra","stage":"epilepsy"}
POST /switch {"link":"fra","stage":"hy2-fallback"}
POST /switch {"link":"fin","stage":"down"}
POST /switch {"link":"fin","mode":"repair","stage":"hy2-fallback"}
POST /probe  {"link":"fin"}
```

Aliases для совместимости:

- `primary`, `xhttp`, `reality` -> `xhttp-reality`
- `epilepsy`, `postgres`, `pg`, `postgres-camouflage` -> `epilepsy`
- `fallback`, `hy2`, `hysteria2`, `hysteria2-salamander` -> `hy2-fallback`
- `down`, `off`, `block`, `disabled` -> `disabled`

## config

`/etc/goida-intermesh/links.json`:

```json
{
  "fin": {
    "listen": 18001,
    "inbound": "in-fin",
    "outbounds": {
      "xhttp-reality": "out-fin-xhttp-reality",
      "hy2-fallback": "out-fin-hy2-fallback"
    },
    "probe": {
      "xhttp-reality": "77.110.108.57:443",
      "hy2-fallback": "127.0.0.1:17455"
    }
  }
}
```

Env:

- `SWITCH_BACKEND=remnawave` — целевой backend.
- `SWITCH_REMNA_PROFILE_NAME=ru-ws-ingress`.
- `SWITCH_REMNA_RESTART_CMD="docker restart remnanode"`.
- `SWITCH_APPLY=0` — dry-run/тесты без `systemctl`.

## деплой-контур

1. Убедиться, что HY2 tcpForwarding ports живы: `17455` FIN, `17456` SWE, `17454` FRA.
2. Поставить `goida-intermesh-switch.service` с `SWITCH_BACKEND=remnawave`.
3. Для каждого `/switch` backend сам делает backup table, меняет только `REMNA_*` target и рестартит `remnanode`.
4. После каждого flip — real shadow через live subscription/profile до `204` + expected exit-IP.
5. Analyzer позднее вызывает эту ручку локально; решение «когда переключать» не живёт в switch.

## experimental xray backend

`SWITCH_BACKEND=xray` и `xray-intermesh.service` оставлены как dev/rollback path. Он был проверен через localhost `18001/18002/18003`, но не является целевой архитектурой после требования Remnawave-centric.

## legacy

`SWITCH_BACKEND=socat` оставлен для ранней модели:

```text
127.0.0.1:18001 -> goida-stage@fin -> TARGET
```

Это rollback/dev-path, не целевая архитектура. Для него нужен `goida-stage@.service`.
