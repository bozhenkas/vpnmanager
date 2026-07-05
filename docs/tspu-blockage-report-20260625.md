# tspu/blockage report — 2026-06-25

> Snapshot собран read-only 2026-06-25 00:20–00:38 MSK.
> Цель: дать devops-инженеру компактную картину текущих блокировок, протоколов и точек отказа.
> Секреты/ключи/uuid/auth намеренно не включены.

## short verdict

Не всё заблокировано.

Живы как входные точки:
- RU primary `ru.goida.fun` / `45.91.54.152`
- RESERVE `reserve.goida.fun` / `31.77.169.26`

Фактически отрезаны для российских точек наблюдения:
- FIN `77.110.108.57`
- FRA `95.163.152.210`
- SWE `89.22.230.5`
- backup/smart2/lite IP `45.91.53.93`

Критично: проблема уже не только в клиентских Happ ping/n/a. RU Remnawave backend сейчас теряет foreign node API `:58443` таймаутами, то есть control-plane RU→FIN/FRA/SWE тоже не проходит.

HY2 UDP обход, который раньше спасал TCP freeze, сейчас тоже не даёт рабочего forwarding: `hysteria-client-*` на RU и RESERVE active, но в логах постоянные `timeout: no recent network activity`; проба через локальные forward-порты не даёт нормального TLS/REALITY handshake.

## inventory used

Live inventory from `python3 scripts/fetch-cluster.py --check`:

| id | role | status | ip | domain | transports |
|---|---|---:|---|---|---|
| ru | primary | active | `45.91.54.152` | `ru.goida.fun` | ws-tls, subscription-ingress |
| fin | exit | active | `77.110.108.57` | `fin.goida.fun` | grpc-reality:443 |
| swe | exit | active | `89.22.230.5` | `swe.goida.fun` | xhttp-reality:443 |
| fra | control | active | `95.163.152.210` | `fra.goida.fun` | reality:443 |
| backup | backup | backup | `45.91.53.93` | - | smart2-xhttp-reality:7443, smart-lite-hy2:8443 |
| reserve | reserve | active | `31.77.169.26` | `reserve.goida.fun` | grpc-reality:443, hy2-egress-failover:fin/swe |
| home | residential | active | `78.107.88.21` | - | home/residential exit |

Known stale/legacy IPs were ignored for this report.

## tools and vantage points

Used:
- `rkn-check` on HOME (`78.107.88.21`, residential Russian ISP)
- `ladon` / LadonGo `PortScan` on HOME, temporarily downloaded to `/tmp/ladon-goida` and removed after scan
- direct TCP reachability checks from RU and RESERVE with `/dev/tcp`
- service/log checks on RU and RESERVE

Not done:
- no production mutation
- no Remnawave restart
- no firewall/config changes
- no full real Happ shadow-smoke after the incident
- no direct shell check on FIN/FRA/SWE because SSH `:17904` timed out from current/RU paths

## rkn-check results from HOME

Manual `rkn-check`:

| target | result |
|---|---|
| `https://ru.goida.fun/` DNS expected `45.91.54.152` | OK, TCP 12ms, TLS 19ms, cert `ru.goida.fun` |
| `https://ru.goida.fun/` direct `45.91.54.152:443` | OK, TCP 19ms, TLS 20ms |
| `https://reserve.goida.fun/` direct `31.77.169.26:443`, no-verify | OK, TCP 47ms, TLS 59ms |
| `https://fin.goida.fun/` direct `77.110.108.57:443`, no-verify | FAILED, TCP timeout, reported `IP block` |
| `https://fra.goida.fun/` direct `95.163.152.210:443`, no-verify | FAILED, TCP timeout, reported `IP block` |
| `https://swe.goida.fun/` direct `89.22.230.5:443`, no-verify | FAILED, TCP timeout, reported `IP block` |
| backup `45.91.53.93:7443` with SNI `ru.goida.fun`, no-verify | FAILED, TCP timeout, reported `IP block` |
| backup `45.91.53.93:8443` with SNI `ru.goida.fun`, no-verify | FAILED, TCP timeout, reported `IP block` |
| FIN `77.110.108.57:8443/tcp` | FAILED, TCP timeout |
| SWE `89.22.230.5:8443/tcp` | FAILED, TCP timeout |

Accumulated HOME `rkn-checker` state at `2026-06-24T21:21:21Z`:

| label | verdict |
|---|---|
| domain `https://ru.goida.fun/` | OK |
| primary_ip `https://45.91.54.152/` | OK |
| backup_ip `https://45.91.53.93/` | TIMEOUT |
| `fin.goida.fun` | TIMEOUT |
| `swe.goida.fun` | TIMEOUT |
| youtube_media | TCP_RESET |
| telegram_org / t_me / telegram_api / telegram_core | DOWN |
| `reserve_reality_443` | OK, tcp about 78ms |
| `backup_hy2_8443_udp` | reported OK, but only UDP send, not a real HY2 handshake |

Important caveat: `backup_hy2_8443_udp ok` in checker is weak. It only proves a UDP send did not error locally. Real HY2 forwarding is failing in service logs.

## ladon PortScan from HOME

Ports scanned: `443,7443,8443,58443,17904,30080`.

| ip | ladon result |
|---|---|
| `45.91.54.152` RU | open: `443`, `8443`, `58443`, `17904`, `30080`; no open `7443` |
| `31.77.169.26` RESERVE | open: `443`, `17904`; no open `7443/8443/58443/30080` |
| `45.91.53.93` backup | no open ports from scanned set |
| `77.110.108.57` FIN | no open ports from scanned set |
| `95.163.152.210` FRA | no open ports from scanned set |
| `89.22.230.5` SWE | no open ports from scanned set |

HOME self-scan, caveat NAT/hairpin:

| ip | ladon result |
|---|---|
| `78.107.88.21` HOME | open: `443`, `7443`, `58443`, `1722` |

## TCP reachability from RU

Ports checked: `443,58443,17904,8443`.

| target | result from RU |
|---|---|
| FIN `77.110.108.57` | all checked TCP ports fail |
| FRA `95.163.152.210` | all checked TCP ports fail |
| SWE `89.22.230.5` | all checked TCP ports fail |
| RESERVE `31.77.169.26` | `443` OK, `17904` OK, `58443` fail, `8443` fail |
| backup `45.91.53.93` | `443` OK, `58443` OK, `17904` OK, `8443` fail |
| HOME `78.107.88.21` | `7443` OK, `58443` OK, `1722` OK; `443/8443/17904` fail from RU |

## TCP reachability from RESERVE

Ports checked: `443,58443,17904,8443`.

| target | result from RESERVE |
|---|---|
| FIN `77.110.108.57` | all checked TCP ports fail |
| FRA `95.163.152.210` | all checked TCP ports fail |
| SWE `89.22.230.5` | all checked TCP ports fail |
| RU `45.91.54.152` | `443`, `58443`, `17904`, `8443` OK |
| backup `45.91.53.93` | all checked TCP ports fail |

Interpretation: RESERVE is reachable from users/HOME and can reach RU, but cannot reach FIN/FRA/SWE/backup over tested TCP. Its egress design depends on HY2 UDP to FIN/SWE; that path is currently timing out at forwarding layer.

## service state and logs

### RU

`vpn-bot.service`:
- active after restart at `2026-06-24 21:29:49 UTC`
- logs show repeated Telegram API errors through local proxy: `Connection refused`
- one direct curl to `127.0.0.1:9090/rknstatus` failed during restart/window; not treated as primary VPN root cause

`remnawave` Docker:
- container up/healthy
- backend logs repeatedly lose foreign nodes:
  - FRA `95.163.152.210:58443` timeout
  - FIN `77.110.108.57:58443` timeout
  - SWE `89.22.230.5:58443` timeout
- repeated messages:
  - `health check attempt ... timeout of 15000ms exceeded`
  - `Lost connection to Node ...`
  - `Pre-check failed ... timeout of 15000ms exceeded`

`hysteria-client-fin.service` / `hysteria-client-swe.service` on RU:
- both active since 2026-06-19
- SWE process high memory footprint observed: about 233 MB current, peak 1.2 GB
- logs show repeated forwarding failures:
  - `TCP forwarding error`
  - `connect error: timeout: no recent network activity`
- local probes through `127.0.0.1:17450/17451` did not produce valid TLS handshake (`errno=104` / timeout)

### RESERVE

`xray-reserve.service`:
- active/running
- ingress `:443` reachable from HOME

`hysteria-client-fin.service` / `hysteria-client-swe.service` on RESERVE:
- both active
- logs show constant forwarding failures:
  - `TCP forwarding error`
  - `connect error: timeout: no recent network activity`
- local probes through `127.0.0.1:17450/17451` returned `errno=104`

## current protocol map

### Client-visible subscription profiles

| visible server | expected client path | current observation |
|---|---|---|
| `Оптимальный` | client VLESS WS/TLS to RU `:443`, then client-side balancer `/fin` `/fra` `/swe`, fallback reserve | RU ingress is reachable, but foreign backends/control-plane are down from RU; likely degraded/n/a |
| `Оптимальный Лайт` | Hysteria2 to backup IP `45.91.53.93:8443/udp` | HOME rkn TCP to `8443` fails; checker UDP-send is not enough; likely n/a from affected Russian networks |
| `Резервный` | VLESS Reality gRPC to `reserve.goida.fun:443`, then RESERVE HY2 egress to FIN/SWE | ingress reachable, but RESERVE HY2 egress logs are timing out, so foreign traffic likely broken/degraded |
| `Финляндия` | VLESS WS/TLS via RU `/fin`, backend FIN | backend unreachable from RU; also direct FIN blocked from HOME |
| `Франция` | VLESS WS/TLS via RU `/fra`, backend FRA | backend unreachable from RU; direct FRA blocked from HOME |
| `Швеция` | VLESS WS/TLS via RU `/swe`, backend SWE | backend unreachable from RU; direct SWE blocked from HOME |
| `Русский` / direct-zapret | VLESS WS/TLS via RU `/direct` | RU reachable; should be evaluated separately for specific RU/zapret destinations |

### Inter-server / backend protocols

| path | protocol | current status |
|---|---|---|
| RU → FIN/SWE backend | HY2 UDP tunnel with TCP forwarding to Reality/XHTTP | active units, but forwarding timeout |
| RU → FRA backend | direct TCP Reality / node API | TCP fails to FRA checked ports; Remnawave health timeout |
| RESERVE → FIN/SWE egress | HY2 UDP tunnel with TCP forwarding to `127.0.0.1:17905` on FIN/SWE | active units, but forwarding timeout |
| RU → foreign node API | TCP `:58443` | FIN/FRA/SWE timeout |
| HOME/client → RU | TCP/TLS `:443` | OK |
| HOME/client → RESERVE | TCP/TLS `:443` | OK |
| HOME/client → backup `45.91.53.93` | `:7443`/`:8443` | timeout/IP block |

## likely root-cause classes

1. New or expanded TSPU filtering affects Russian/residential and Russian-DC paths to foreign IPs:
   - HOME → FIN/FRA/SWE TCP blocked
   - RU → FIN/FRA/SWE TCP blocked
   - RESERVE → FIN/FRA/SWE TCP blocked
   - RU Remnawave node API to FIN/FRA/SWE times out

2. HY2 UDP is no longer a working bypass to FIN/SWE, or remote HY2/egress side is broken:
   - RU/RESERVE `hysteria-client-*` active but forwarding fails
   - no direct shell to FIN/SWE from current paths to verify remote `hysteria-server` / `xray-ru4-egress`
   - checker's UDP-send OK is insufficient and currently misleading

3. Backup `45.91.53.93` is blocked from HOME and RESERVE, but reachable from RU on some TCP ports:
   - HOME: `rkn-check` and `ladon` show blocked/no open scanned ports
   - RU: `443/58443/17904` OK, `8443` fail
   - RESERVE: all checked TCP ports fail

4. RU primary itself is not currently the blocked edge:
   - HOME `rkn-check` OK
   - HOME `ladon` sees expected public/control ports
   - `remnawave` container healthy, although foreign nodes are disconnected

## what would help fix

Immediate non-mutating checks from an out-of-band non-RU path:
- SSH or provider console into FIN/FRA/SWE.
- Check:
  - `systemctl status hysteria-server`
  - `systemctl status xray-ru4-egress`
  - `systemctl status remnanode` or Docker remnanode status
  - listeners: `ss -lntup`
  - firewall: `ufw status`, provider firewall/security groups
  - logs around `2026-06-24 21:00Z` onward

If FIN/SWE services are healthy:
- Treat this as a new TSPU event that blocks both direct TCP and current HY2 path from Russian-side hosts.
- Add/try a new transit not in the current block set:
  - new VPS/IP in another ASN/country
  - HOME/residential as temporary egress/transit if acceptable
  - FRA only if reachable from a non-RU transit; RU→FRA is currently blocked

If FIN/SWE services are unhealthy:
- Restore remote HY2/egress services first, then rerun:
  - real HY2 forwarding test, not UDP-send
  - Xray/Happ-equivalent subscription smoke
  - Remnawave node health check

Monitoring improvement:
- Replace `backup_hy2_8443_udp` UDP-send check with an actual HY2/tcpForwarding smoke:
  - connect to local forward port
  - complete TLS/REALITY/Xray or at least TLS handshake to expected remote
  - classify forwarding timeout as hard fail

Operational warning:
- Do not call this fixed from `rkn-check OK` on `ru.goida.fun` or from active systemd units alone.
- Proof needs real client-equivalent path:
  - exact current subscription body
  - Xray/Happ-equivalent run
  - exit IP / HTTP 204
  - correlated RU/foreign/reserve logs

## raw command notes

Representative commands used:

```bash
python3 scripts/fetch-cluster.py --check

/opt/rkn-checker/rkn-check --url https://ru.goida.fun/ --expected-ip 45.91.54.152 --timeout 8
/opt/rkn-checker/rkn-check --url https://fin.goida.fun/ --host 77.110.108.57 --no-verify --timeout 8
/opt/rkn-checker/rkn-check --url https://fra.goida.fun/ --host 95.163.152.210 --no-verify --timeout 8
/opt/rkn-checker/rkn-check --url https://swe.goida.fun/ --host 89.22.230.5 --no-verify --timeout 8
/opt/rkn-checker/rkn-check --url https://reserve.goida.fun/ --host 31.77.169.26 --no-verify --timeout 8

/tmp/ladon-goida <ip> PortScan 443,7443,8443,58443,17904,30080

for h in 77.110.108.57 95.163.152.210 89.22.230.5 31.77.169.26 45.91.53.93; do
  for p in 443 58443 17904 8443; do
    timeout 5 bash -c "</dev/tcp/$h/$p" >/dev/null 2>&1 && echo "$h:$p tcp_ok" || echo "$h:$p tcp_fail"
  done
done

systemctl status hysteria-client-fin.service hysteria-client-swe.service --no-pager
journalctl -u hysteria-client-fin.service -u hysteria-client-swe.service -n 120 --no-pager
docker logs --since 20m remnawave 2>&1 | grep -E "Lost connection|health check|Pre-check|connected|Node"
```
