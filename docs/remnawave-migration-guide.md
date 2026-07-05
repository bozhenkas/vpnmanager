# Руководство по миграции пользователей → Remnawave

> Обновлено: 2026-05-27  
> Статус: bozhenkas — **мигрирован** (эталон).  
> Остальные пользователи — на 3X-UI, миграция по одному.
>
> **Tracked-файл → без секретов.** Реальные токены/UUID/HWID/TG-id — в gitignored
> `.claude/memory/secrets-and-logic.md` (§4.9 test-users). Ниже — placeholder'ы.

---

## Предварительные требования

Выполнить один раз перед первой миграцией:

```bash
# Go/no-go критерии (см. memory/remnawave-decision.md)
# - flow-patch в Remnawave persistent (volume-mount или upstream PR)
# - REMNA_SWE стабилен 7 дней
# - Happ smoke на iOS/macOS пройден
```

---

## Шаги миграции одного пользователя

### 1. Создать пользователя в Remnawave

Через Remnawave Panel (`:30080`) или API:

- **Username** — тот же, что в `bot.db` (регистр важен)
- **Expire** — дата из `client_profiles.paid_until` в bot.db
- **Device limit** — из `client_profiles.device_limit` (обычно 2)
- **Squads** — подобрать по тарифу:
  - базовый: `SMART_REMNA`, `SMART_RU_REMNA`
  - с FIN: добавить `FIN`
  - с Hydra: `HYDRA_POL_REMNA`, `HYDRA_TUR_REMNA`, `HYDRA_NL_REMNA`, `HYDRA_DE_REMNA`

Запомнить `shortUuid` нового пользователя.

### 2. Импортировать трафик из 3X-UI

```bash
# на RU сервере
cd /root/vpn-bot
python3 /opt/scripts/migrate_xui_traffic.py <username> --dry-run  # проверить суммы
python3 /opt/scripts/migrate_xui_traffic.py <username>             # записать в Remnawave
```

Скрипт суммирует up+down по всем inbound-email:
`{user}`, `fin-{user}`, `swe-{user}`, `zapret-{user}`, `usa-{user}`, `pol-{user}`, `tur-{user}`, `nl-{user}`, `de-{user}`, `fi2-{user}`

Флаг хранится в `bot.db::xui_traffic_imported` — повторный запуск безопасен.

### 3. Восстановить пользователя в bot.db

В vpn-bot (Telegram):

```
/readdrw <username>
```

Команда:
- Находит пользователя в Remnawave
- Извлекает или генерирует legacy-token → сохраняет в `users.tag` (Remnawave) и в `users.token` (bot.db)
- Строит `custom_sub` по squads пользователя
- Создаёт `client_profiles` запись
- Возвращает invite-link для client-bot и subscription URL

### 4. Привязать TG ID в client-bot

Пользователь переходит по invite-link из `/readdrw` → открывает Mini App → TG ID записывается в `client_tg_links`.

Или вручную (если TG ID известен):

```bash
sqlite3 /root/vpn-bot/bot.db "
INSERT OR REPLACE INTO client_tg_links (tg_id, username, linked_at)
VALUES (<tg_id>, '<username>', datetime('now'));
"
```

### 5. Smoke-тест подписки

```bash
# на RU сервере или локально
curl -s -H 'User-Agent: Happ/4.10.2/ios/<happ-hwid>' \
  https://ru.goida.fun/subscribe/<token> | python3 -c "
import sys, base64
data = sys.stdin.buffer.read()
try: text = base64.b64decode(data).decode()
except: text = data.decode()
for line in text.splitlines():
    if line.startswith('vless://'):
        print(' ', line.split('#')[-1])
"
```

Ожидаемые серверы (зависит от squads):
- `smart-{username} 🇸🇨`
- `fin-{username} 🇫🇮`
- `fra-{username} 🇫🇷`
- `swe-{username} 🇸🇪`
- `ru-zapret (discord/youtube) 🇷🇺`
- Hydra-страны по squads (Польша / Турция / Нидерланды / Германия)

### 6. Проверить заголовок трафика

```bash
curl -sI -H 'User-Agent: Happ/4.10.2/ios/<happ-hwid>' \
  https://ru.goida.fun/subscribe/<token> | grep subscription-userinfo
# ожидается: download=<байты>; total=0; expire=<unix>
```

`total=0` = безлимит (∞ в Happ). `expire` должен совпадать с датой оплаты.

### 7. Удалить клиентов из 3X-UI (после подтверждения)

```bash
# backup перед удалением
cp /etc/x-ui/x-ui.db /root/deploy-backups/$(date +%Y%m%d)-pre-delete-<username>/x-ui.db.bak

# удалить через 3X-UI Panel или API
# НЕ удалять записи client_traffics — они нужны для статистики
```

---

## Массовая миграция (batch)

```bash
# 1. Dry-run по всем юзерам из bot.db
python3 /opt/scripts/migrate_xui_traffic.py --all --dry-run

# 2. Реальный импорт трафика (только для тех, кто уже создан в Remnawave)
python3 /opt/scripts/migrate_xui_traffic.py --all

# 3. Для каждого юзера — /readdrw <username> в vpn-bot
```

Порядок миграции (рекомендован):
1. Тестовые/неактивные пользователи → проверить механизм
2. Активные с базовым тарифом (без Hydra)
3. Активные с Hydra
4. Пользователи с legacy-только подпиской (custom_sub без Remnawave)

---

## Что НЕ переносится автоматически

| Данные | Где взять |
|--------|-----------|
| WL-статус (whitelist) | `client_profiles.wl_enabled` в bot.db — остаётся |
| device_limit | Устанавливается вручную в Remnawave при создании |
| HWID устройства | Remnawave начинает отслеживать с нуля после миграции |
| Ограничение трафика (totalGB) | Для legacy — был 0 (∞); в Remnawave ставить 0 |
| Telegram-команды `/status` | Работают через remna_mode автоматически после `/readdrw` |

---

## Rollback отдельного пользователя

Если что-то пошло не так:

```bash
# 1. Удалить из users и client_profiles в bot.db
sqlite3 /root/vpn-bot/bot.db "DELETE FROM users WHERE name='<username>';"
sqlite3 /root/vpn-bot/bot.db "DELETE FROM client_profiles WHERE username='<username>';"

# 2. Пользователь вернётся на legacy-путь (3X-UI subscription)
#    если его 3X-UI clients ещё не удалены

# 3. Деактивировать пользователя в Remnawave через Panel
```

---

## Справка: структура bot.db

```sql
-- основная таблица
users (name TEXT, token TEXT, custom_sub TEXT, created_at TEXT)

-- профиль
client_profiles (username TEXT, device_limit INT, paid_until TEXT, wl_enabled INT, updated_at TEXT)

-- TG-привязка для Mini App
client_tg_links (tg_id INTEGER, username TEXT, linked_at TEXT)

-- серверные предпочтения Mini App
client_server_prefs (username TEXT, server_key TEXT, enabled INT)

-- флаг импорта трафика (создаётся migrate_xui_traffic.py)
xui_traffic_imported (username TEXT PRIMARY KEY, imported_at TEXT)
```

---

## Журнал: выполненные миграции

### bozhenkas (мигрирован 2026-05-27) — эталон

- Remnawave: `status=ACTIVE`, `shortUuid=<short-uuid>`, squads: HYDRA_POL/TUR/NL/DE
- bot.db: `users.token=<legacy-token>`, `client_profiles.device_limit=2`
- TG: `<owner-tg-id>` → `client_tg_links`
- Subscription: 9 серверов, `download=~250MB`, `expire=14.06.2026`
- Реальные значения: `.claude/memory/secrets-and-logic.md` §4.9.
- 3X-UI clients: удалены до миграции → исторический трафик не перенесён (0 в 3X-UI)

### rob (трафик импортирован 2026-05-28)

- Трафик из 3X-UI → Remnawave: `up=3.41 GB`, `down=57.71 GB`, `total=61.11 GB` → `t_id=19`
- `xui_traffic_imported` флаг проставлен в bot.db
- Команда: `python3 /opt/scripts/migrate_xui_traffic.py rob`
