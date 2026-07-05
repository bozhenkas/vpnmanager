# Reserve (194.117.80.94 / reserve.goida.fun / ru-4) → FIN-direct: состояние и план

> Дата: 2026-06-11. Режим: **read-only recon**. Никаких мутаций прода не выполнялось.
> Цель владельца: резервный сервер `194.117.80.94` должен сам выводить трафик в FIN
> напрямую (`77.110.108.57`), независимо от основного RU `45.91.54.152`.
>
> **ГЛАВНЫЙ ВЫВОД: это уже реализовано.** Reserve больше НЕ nginx-relay на RU:2053.
> Он самостоятельный xray, который egress'ит в FIN через приватный SSH-туннель,
> и от основного RU `45.91.54.152` сейчас НЕ зависит. См. ниже доказательства.

---

## 1. Текущее состояние reserve `194.117.80.94`

Хост: `ru-4.goida.fun`, uptime сервера 12 дней. SSH `-p 22 -i ~/.ssh/id_rsa` (rsa-ключ
работает; внутри также слушает `127.0.0.1:17905`-туннель и проброшенный `127.0.0.1:17905` от ssh).

### Что слушает (`ss -tlnp`)
| порт | процесс | роль |
|---|---|---|
| `0.0.0.0:443` | `xray` (pid 160851, `/etc/xray/reserve-fin.json`) | **клиентский inbound `GOIDA_RESERVE`** (VLESS Reality gRPC) |
| `127.0.0.1:17905` | `ssh` (pid 258107) | локальный конец SSH-туннеля до FIN |
| `*:2096`, `*:25565` | `x-ui` (pid 171803) | **legacy 3x-ui панель/инбаунд, service `disabled` но `active`** |
| `127.0.0.1:62789`, `127.0.0.1:11111` | второй `xray` (от x-ui) | legacy x-ui xray |
| `0.0.0.0:22`, `127.0.0.1:17905` (ssh) | sshd | управление |

`docker ps` → **docker не установлен**. nginx бинарь есть, но `systemctl is-active nginx` = **failed** (не запущен). То есть **никакого nginx stream-relay уже нет** — память `infrastructure-live` («backup ingress relay (L4 nginx stream)») и `secrets-and-logic` («relay ru-4: L4 TCP-passthrough :443 → RU:2053 … dormant») **устарели**.

### Как сейчас устроен egress (фактическая цепочка)

```
Клиент (Happ, подписка "Резервный 🇰🇵")
  │  VLESS Reality gRPC, SNI web.max.ru, на reserve.goida.fun:443
  ▼
[reserve 194.117.80.94]  xray  inbound GOIDA_RESERVE (:443)
  │  routing: GOIDA_RESERVE (tcp,udp) → outbound REMNA_FI
  │  REMNA_FI = VLESS → 127.0.0.1:17905 (security none)
  ▼
[ssh -L 127.0.0.1:17905 → 77.110.108.57:17905]  (ru4-fin-tunnel.service, autossh-подобный, Restart=always)
  ▼
[FIN 77.110.108.57]  xray  /etc/xray/ru4-egress.json
  │  inbound RU4_PRIVATE (:17905, vless, security none)
  │  routing: RU4_PRIVATE (tcp,udp) → outbound DIRECT (freedom)
  ▼
Интернет с IP FIN 77.110.108.57
```

Подтверждено access-логом reserve (`/var/log/xray/access.log`), запись 2026-06-11:
`accepted tcp:github.com:443 [GOIDA_RESERVE -> REMNA_FI] email: u7` — живой трафик идёт FIN-direct.

### Сервисы и аптаймы
- `xray-reserve-fin.service` — *"xray reserve ru-4 -> FIN (P.15b)"* — **active/running**, с 2026-06-07 21:34.
  Unit: `ExecStart=/usr/local/bin/xray run -config /etc/xray/reserve-fin.json`, `Restart=on-failure`, `WantedBy=multi-user.target`.
- `ru4-fin-tunnel.service` — *"Persistent ru-4 to FIN private tunnel"* — **active/running**
  (перезапущен 2026-06-11 16:09; `Restart=always`). Ключ `/root/.ssh/ru4_fin_tunnel`.
- FIN-сторона: xray `/etc/xray/ru4-egress.json` (pid 2914585, с 2026-06-07), inbound `RU4_PRIVATE`
  слушает `*:17905`. authorized_keys на FIN: `restrict,port-forwarding … ru4-fin-tunnel` (туннель-only ключ).

### Зависимость от основного RU `45.91.54.152`
**Нет.** Цепочка reserve → SSH-туннель → FIN → DIRECT не проходит через RU вообще.
Если выключить основной RU `45.91.54.152`, reserve продолжит работать (клиенты идут на
`reserve.goida.fun:443` напрямую, egress в FIN). Единственная зависимость reserve — **FIN**
(`77.110.108.57`, SSH-порт 17904) для туннеля. Требование владельца — «резерв независим от RU» —
**де-факто выполнено**.

---

## 2. Разграничение: задача 15 vs требование владельца

| | Задача 15 в `tasks.md` (✅ 2026-06-06) | Требование владельца (этот документ) |
|---|---|---|
| Где | На **основном RU** `45.91.54.152` (config_profiles Remnawave) | На **самом reserve-сервере** `194.117.80.94` |
| Что | inbound `GOIDA_RESERVE` (он же `RU_REALITY_GRPC_RESERVE` :2053) → outbound `REMNA_FI` | xray на reserve egress'ит в FIN, минуя RU |
| Семантика | Когда трафик резерва **доходил до RU** (через старый relay), на RU его сразу гнали в FI | Сделать reserve **полностью независимым** от RU (свой egress в FIN) |
| Файл-артефакт | `scripts/p15_goida_apply.py`, бэкап `config_profiles_bak_20260606_175734_reserve_fin_only` | `/etc/xray/reserve-fin.json` + туннель (P.15b) |

`bot/manual_routes.py` (строки 31–46, 226) — это спека RU-стороны (задача 15): `REMNA_RESERVE = "RU_REALITY_GRPC_RESERVE"`, правило `reserve-fin-catch-all` `GOIDA_RESERVE → REMNA_FI`. Это **НЕ** про egress самого reserve-VPS.

**Важно:** задача 15 на RU и текущая схема reserve-VPS — параллельные, не конфликтуют, но
**relay-путь reserve→RU:2053 более не используется** (nginx-relay снят, xray на reserve выводит сам).
RU-правило `GOIDA_RESERVE → REMNA_FI` остаётся «спящей страховкой» на случай, если кто-то
вернёт relay-режим; на живой трафик оно сейчас не влияет (трафик до R:2053 не доходит).
В `vpn-bot.py:71` это уже зафиксировано: *«резервный вход: VPS ru-4 … P.15b — локальный xray TCP Reality :443 -> FIN (без RU)»* (формулировка «TCP» неточна — фактически gRPC Reality, см. §3).

---

## 3. Подписка: клиентские параметры reserve (НЕ менять)

`bot/vpn-bot.py` `remnawave_reserve_link()` (строки 854–862) строит строку
**«Резервный 🇰🇵 (мобильная связь)»**:

| параметр | значение в подписке | на live-сервере | совпадает? |
|---|---|---|---|
| host | `reserve.goida.fun` (A → 194.117.80.94) | — | — |
| port | `443` | xray :443 | ✅ |
| type | `grpc` (serviceName `grpc`, multiMode) | grpcSettings serviceName `grpc`, multiMode true | ✅ |
| security | `reality` | reality | ✅ |
| sni / serverNames | `web.max.ru` (`RESERVE_REALITY_SNI`) | `web.max.ru` | ✅ |
| pbk | `UpJ1_AFXOqNJUKlIaqj_C4XUipOd7Eg489xQWmuAbiY` | privateKey даёт ровно этот pubkey (проверено `xray x25519`) | ✅ |
| sid | `1df9284e42105047` | shortIds `["1df9284e42105047"]` | ✅ |
| fp | `chrome` | — | ✅ |
| uuid | per-user из RW | 27 клиентов u1..u27 в inbound | (проверять при добавлении юзеров) |

**Вывод:** клиентская сторона подписки **уже корректно** указывает на самостоятельный xray reserve.
Любой план FIN-direct, который сохраняет inbound `GOIDA_RESERVE` с теми же Reality-параметрами
(SNI/pbk/sid/serviceName/port), **не требует смены клиентских подписок**.

---

## 4. Целевая схема FIN-direct

Поскольку цель уже достигнута, «целевая схема» = либо (А) оставить как есть с укреплением,
либо (Б) убрать зависимость от SSH-туннеля, переведя outbound reserve на прямой VLESS-Reality
к публичному FIN-inbound. Рекомендация — **вариант А** (минимальный риск, ничего в подписке не меняется);
вариант Б опционально, если хочется убрать SSH-туннель как точку отказа.

### Вариант А (рекомендуемый) — сохранить текущую схему, укрепить
- Inbound на reserve: `GOIDA_RESERVE` VLESS Reality gRPC :443 — **без изменений** (подписка цела).
- Outbound `REMNA_FI` → `127.0.0.1:17905` → SSH-туннель → FIN `RU4_PRIVATE` → DIRECT — **без изменений**.
- Укрепление: enable/мониторинг туннеля и xray (см. §5 фазы), резервный авто-рестарт.

### Вариант Б (опциональный) — убрать SSH-туннель, прямой VLESS-Reality reserve→FIN
Заменить на reserve outbound `REMNA_FI` с `127.0.0.1:17905 / security none` на
**прямой VLESS-Reality к публичному FIN-inbound** (FIN inbound `REMNA_VLESS_TCP_REALITY_7443`
на `77.110.108.57:443`, реальные Reality creds FIN — взять из Remnawave/конфига FIN-ноды, НЕ из этого документа):

```jsonc
// reserve outbound (вариант Б) — значения creds брать с FIN-ноды, не хардкодить тут
{
  "tag": "REMNA_FI",
  "protocol": "vless",
  "settings": { "vnext": [{
      "address": "77.110.108.57", "port": 443,
      "users": [{ "id": "<FIN-inbound-uuid>", "encryption": "none", "flow": "xtls-rprx-vision" }] }] },
  "streamSettings": {
    "network": "tcp", "security": "reality",
    "realitySettings": { "serverName": "<FIN SNI>", "publicKey": "<FIN pbk>",
                         "shortId": "<FIN sid>", "fingerprint": "chrome" } }
}
```
Плюс: нет SSH-туннеля (одна точка отказа меньше), egress инкапсулирован в Reality на всём плече RU-зоны→FIN.
Минус: нужно добавить reserve-uuid в клиенты FIN-inbound, держать Reality-creds FIN в синхроне;
если FIN сменит ключи — reserve отвалится (сейчас туннель к этому устойчив).
**Inbound `GOIDA_RESERVE` и подписка при варианте Б тоже не меняются** — меняется только outbound.

### Что НЕ менять в любом варианте
- inbound `GOIDA_RESERVE`: port 443, gRPC serviceName `grpc`/multiMode, SNI `web.max.ru`,
  privateKey (→ pbk `UpJ1_…`), shortId `1df9284e42105047`, список клиентских uuid.
- строку подписки и env `RESERVE_REALITY_SNI/PBK/SID` в `vpn-bot.py`.

---

## 5. План внедрения (фазами; СЕЙЧАС НЕ ВЫПОЛНЯТЬ)

> Поскольку базовая цель уже достигнута, это план укрепления/опц. перехода, не аварийный.
> Перед каждой мутацией — бэкап. Один прод-актор на фазу. Read-only проверка → мутация → shadow-test.

**Фаза 0 — фиксация факта (read-only, безопасно):**
- Обновить память (`infrastructure-live`, `secrets-and-logic`): reserve = самостоятельный xray
  FIN-direct через SSH-туннель, НЕ nginx-relay. Снять пометку «dormant relay».

**Фаза 1 — укрепление текущей схемы (вариант А):**
1. Бэкап: `cp /etc/xray/reserve-fin.json{,.bak-20260611}` на reserve; `cp /etc/xray/ru4-egress.json{,.bak-20260611}` на FIN.
2. `systemctl enable xray-reserve-fin.service ru4-fin-tunnel.service` (проверить, что оба `enabled`,
   чтобы пережили ребут; tunnel сейчас перезапускался 16:09 — убедиться в auto-start).
3. Решить судьбу legacy `x-ui` (`:2096/:25565`, второй xray): если не используется — остановить/disable
   (отдельная фаза с бэкапом x-ui db; сейчас НЕ трогать без проверки, что :443 не зависит от x-ui).
4. Лёгкий healthcheck: проверка `127.0.0.1:17905` reachable + рестарт туннеля при падении.

**Фаза 2 (опц.) — переход на прямой Reality (вариант Б):**
1. Бэкап reserve-fin.json.
2. Добавить reserve-uuid в клиенты FIN-inbound `REMNA_VLESS_TCP_REALITY_7443` (через Remnawave-native, НЕ direct-SQL).
3. Заменить outbound `REMNA_FI` на прямой VLESS-Reality к `77.110.108.57:443` (creds с FIN).
4. `xray -test` конфига → рестарт `xray-reserve-fin` → shadow-test реальной подпиской «Резервный».
5. Только после успешного теста — отключить `ru4-fin-tunnel.service`.
6. Rollback: вернуть reserve-fin.json.bak + re-enable туннель.

**Shadow-test (после любой мутации):** сгенерить реальную подписку тест-юзера, поднять Xray из её
JSON, прогнать трафик через «Резервный», сверить access-log reserve (`GOIDA_RESERVE -> REMNA_FI`)
и FIN egress (выход с `77.110.108.57`). Не полагаться только на `:443` коннект.

---

## 6. Риски

1. **Не сломать существующие подписки.** Reality-параметры reserve (SNI/pbk/sid/serviceName/port)
   совпадают с подпиской 1:1 (проверено). Любая смена privateKey/shortId/SNI/serviceName/порта на
   inbound `GOIDA_RESERVE` мгновенно отвалит всех клиентов «Резервный». **Inbound не трогать** —
   менять только outbound, если вообще.
2. **SSH-туннель — точка отказа (вариант А).** `ru4-fin-tunnel.service` уже `Restart=always`, но
   при долгой недоступности FIN:17904 reserve перестанет выводить трафик. Вариант Б устраняет это,
   но вводит зависимость от синхрона Reality-creds FIN.
3. **Публичный FIN :17905 `security none`.** Inbound `RU4_PRIVATE` на FIN слушает `0.0.0.0:17905`
   без шифрования. Защищён тем, что приходит через loopback-туннель и authorized_keys
   `restrict,port-forwarding`, но порт открыт на внешнем интерфейсе FIN — желательно забиндить на
   `127.0.0.1` или закрыть фаерволом для всех, кроме loopback. (Отдельная hardening-задача.)
4. **Legacy x-ui на reserve.** Второй xray + x-ui (:2096/:25565) — потенциальный источник путаницы и
   поверхность атаки. Перед остановкой убедиться, что основной `xray-reserve-fin` (:443) от него
   не зависит (по конфигам — независим, но проверить на проде перед disable).
5. **Память устарела.** `infrastructure-live-20260605.md` и `secrets-and-logic.md` описывают reserve
   как nginx L4-relay → RU:2053 (dormant). Это неверно для текущего состояния — обновить, иначе
   будущие действия по «памяти» могут поломать рабочую FIN-direct схему.
6. **RU-задача 15 как спящая страховка.** Правило `GOIDA_RESERVE → REMNA_FI` на RU не вредит, но при
   возврате relay-режима пути могут конфликтовать. Не «чистить» его вслепую — это и есть fallback.
