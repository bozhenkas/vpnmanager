# ip-watchdog

DNS failover для RU-сервера через Cloudflare API.

Запускается на **домашнем сервере** (российский ISP) — видит ТСПУ-блокировки так же, как клиенты.
При блокировке `PRIMARY_IP` переключает A-запись домена на `BACKUP_IP` через Cloudflare API.
При восстановлении — возвращает обратно (auto-recovery).
Алерты доставляются через relay-ручку `/notify` на RU-сервере (Telegram заблокирован у ISP).

## Архитектура

```
[Домашний сервер, ISP: 78.107.88.21]
        │
        ├── TCP+TLS probe → 83.147.255.98:443   (PRIMARY_IP)
        │     fail × 3 → failover
        │
        ├── Cloudflare API → PATCH /dns_records  (TTL=60)
        │     A ru.goida.fun → 83.147.255.168   (BACKUP_IP)
        │
        └── POST https://ru.goida.fun/notify     (relay-алерт)
              → nginx allow 78.107.88.21
              → vpn-bot:9090 /notify
              → Telegram сообщение владельцу
```

## Переменные окружения

| Переменная | Описание | Пример |
|---|---|---|
| `PRIMARY_IP` | основной IP RU-сервера | `83.147.255.98` |
| `BACKUP_IP` | резервный IP RU-сервера | `83.147.255.168` |
| `DOMAIN` | домен для проверки SNI и DNS | `ru.goida.fun` |
| `CHECK_PORT` | порт для TLS-пробы | `443` |
| `CF_TOKEN` | Cloudflare API token (Edit zone DNS) | |
| `CF_ZONE_ID` | Cloudflare Zone ID для goida.fun | |
| `NOTIFY_URL` | URL relay-ручки на RU-сервере | `https://ru.goida.fun/notify` |
| `NOTIFY_TOKEN` | shared secret для `/notify` | |
| `STATE_FILE` | файл с текущим активным IP | `/var/lib/ip-watchdog/state` |
| `FAIL_THRESHOLD` | проб до failover | `3` |
| `PROBE_TIMEOUT` | таймаут одной пробы (сек) | `8` |
| `RETRY_DELAY` | пауза между пробами (сек) | `10` |
| `AUTO_RECOVERY` | вернуть DNS на primary когда восстановится | `1` |

## Деплой на домашний сервер (Linux/systemd)

```bash
# файлы
mkdir -p /opt/ip-watchdog /etc/ip-watchdog /var/lib/ip-watchdog
cp watchdog.py /opt/ip-watchdog/watchdog.py

# env (из watchdog.env.example)
cp watchdog.env.example /etc/ip-watchdog/watchdog.env
# заполнить CF_TOKEN, NOTIFY_TOKEN
chmod 600 /etc/ip-watchdog/watchdog.env

# systemd
cp ../deploy/systemd/ip-watchdog.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ip-watchdog.timer

# проверка
systemctl start ip-watchdog.service
journalctl -u ip-watchdog -n 20
```

## /notify relay на RU-сервере

vpn-bot принимает `POST /notify` и отправляет TG-сообщение владельцу.

**Защита:**
- nginx: `allow 78.107.88.21; deny all;`
- shared token в заголовке `Authorization: Bearer <NOTIFY_TOKEN>`
- IP проверяется повторно в хэндлере через `X-Real-IP`

**Env на RU-сервере** (`/root/vpn-bot/.env`):
```
NOTIFY_TOKEN=...          # тот же что в watchdog.env
NOTIFY_ALLOWED_IP=78.107.88.21
```

## Cloudflare TTL

Перед активацией убедиться что TTL A-записи `ru.goida.fun` = 60 с в Cloudflare.
Иначе failover будет видим клиентам только через час.

## Домашний сервер в SSH config (~/.ssh/config)

```
Host serv
    HostName 192.168.31.176   # или 90.156.225.91 снаружи
    User bozhenkas
    IdentityFile ~/.ssh/id_rsa
    Port 22
```
