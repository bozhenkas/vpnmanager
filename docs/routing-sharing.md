# routing.json — логика и шеринг с партнёрским сервером

> Для ИИ-агента партнёра (lekanta.ru). Описывает, как goida-vpn формирует client-side
> роутинг (`routing.json`) и как его безопасно забрать после закрытия публичного доступа
> (2026-07-05).

---

## 1. Что такое routing.json

`routing.json` — client-side split-tunnel политика xray (`xray_routing`: geoip/geosite
правила `direct`/`proxy`/`block`, hardcoded CIDR-исключения). Генерируется на RU
(`subscription/ru_routing.py`, обновляется `scripts/promote_ru_candidates.py`), кладётся
в `/var/www/html/routing.json`.

**Свои Happ-клиенты его отдельно не запрашивают** — роутинг уже встроен в саму подписку
(`happ://routing/onadd/<base64 JSON>`, `subscription/engine.py`). Файл нужен только внешним
потребителям, которые строят собственную подписку на основе нашей политики роутинга.

---

## 2. Почему закрыт публичный доступ (2026-07-05)

До 2026-07-05 `GET https://ru.goida.fun/routing.json` был публичным, без авторизации,
с `Access-Control-Allow-Origin: *` и часовым кэшем. Любой (включая ТСПУ) мог прочитать
всю политику роутинга — какие домены/подсети идут `direct`, что проксируется, какие CIDR
захардкожены как исключения. Это готовый разведданный для блокировки. Публичный alias
убран из nginx.

---

## 3. Эндпоинт для партнёра: `GET /routing/share`

```
GET https://ru.goida.fun/routing/share?token=<TOKEN>
```

Отдаёт содержимое `routing.json` как есть (`application/json`, `Cache-Control: no-store`).

### Защита (тот же паттерн, что `/wl/share`)
1. **Токен** — обязателен, передаётся в query `?token=`. Неверный/пустой → `401`.
2. **IP-allowlist по домену** — клиентский IP должен резолвиться из `lekanta.ru`
   (env `ROUTING_SHARE_ALLOWED_DOMAIN`, DNS-кэш 5 мин). Чужой IP → `403`.

### Токен
> Значение — только по отдельному защищённому каналу и в gitignored
> `.claude/memory/secrets-and-logic.md`, не в этом файле. Хранится на сервере в
> `vpn-bot.service.d/routing-share.conf` (env `ROUTING_SHARE_TOKEN`).
> При компрометации — перегенерировать (`openssl rand -hex 24`), обновить override
> + рестарт `vpn-bot`, передать новое значение партнёру отдельно.

---

## 4. Как забрать (инструкция для агента партнёра)

### 4.1. Периодический sync (рекомендуемый способ)
Готовый скрипт `scripts/update_lekanta_routing.py` (из репозитория goida-vpn) — деплоится
как `/usr/local/sbin/update-lekanta-routing.py`, запускается `lekanta-routing-update.timer`
(раз в сутки). Атомарно обновляет локальный файл, при сбое сохраняет последний валидный.

Токен передаётся через systemd `EnvironmentFile=-/etc/default/lekanta-routing-update`:
```bash
# на сервере lekanta.ru, значение токена — из отдельного защищённого канала
cat > /etc/default/lekanta-routing-update << 'EOF'
ROUTING_SHARE_TOKEN=<TOKEN>
EOF
chmod 600 /etc/default/lekanta-routing-update
systemctl daemon-reload
systemctl enable --now lekanta-routing-update.timer
systemctl start lekanta-routing-update.service   # разовый прогон для проверки
```

### 4.2. Прямой запрос (если нужен live-fetch в рантайме бота)
```bash
curl -fsS "https://ru.goida.fun/routing/share?token=<TOKEN>" -o routing.json
```
Запрос должен идти **с сервера lekanta.ru** (иначе `403`).

### 4.3. Формат ответа
Тот же JSON, что раньше отдавался публично: `{"_version", "_description", "xray_routing", ...}`.
Валидация (см. `validate()` в `update_lekanta_routing.py`): `_version` — непустая строка,
`xray_routing.domainMatcher` ∈ {hybrid, linear}, `rules` — непустой массив field-правил,
последнее правило — `outboundTag: "proxy"` (fallback).

---

## 5. Чек-лист

- [ ] запрос идёт с IP, резолвящегося из `lekanta.ru`;
- [ ] токен валиден и хранится вне репозитория/доков (только env на сервере);
- [ ] локальный fallback-файл на partner-сервере не теряется при временной недоступности эндпоинта;
- [ ] публичный `/routing.json` больше не запрашивается нигде на стороне партнёра (заменён на `/routing/share`).
