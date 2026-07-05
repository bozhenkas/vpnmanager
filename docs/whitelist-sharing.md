# Whitelist (WL) — логика и шеринг с партнёрским сервером

> Для ИИ-агента партнёра (lekanta.ru). Описывает, как goida-vpn формирует и отдаёт
> список whitelist-серверов и как его безопасно забрать.

---

## 1. Что такое whitelist в goida-vpn

Whitelist (WL) — это набор **резервных VLESS-серверов** (сторонний провайдер whitestore),
которые клиент использует при жёстких блокировках мобильной связи. Каждый WL-профиль —
самодостаточный xray-конфиг (outbounds + routing + observatory/balancer), работающий
напрямую, без зависимости от инфраструктуры goida.

Источник WL — подписка hydra (whitestore). Скрипт `sync_remna_hydra.py` тянет её каждые
30 минут, валидирует и публикует.

---

## 2. Жизненный цикл WL (на стороне goida)

### 2.1. Парсинг из подписки hydra
`scripts/sync_remna_hydra.py`:
1. `load_subscription()` → JSON-подписка (Happ UA), внутри — массив профилей.
2. `classify_profiles()` → профили с `"Whitelist"` в remarks попадают в `wl_profiles`;
   первый UUID любого vless-outbound сохраняется как `sub_uid` (UID нашего whitestore-аккаунта).
3. `build_wl_lines(wl_profiles, sub_uid)`:
   - для каждого профиля **все** `users[].id` перезаписываются на `sub_uid`
     (строгое использование собственного UID, а не того, что в чужом профиле);
   - проверяется живость хотя бы одного бэкенда (`tcp_alive`, TCP-connect 4 c);
   - мёртвые профили пропускаются;
   - живые **перенумеровываются по порядку**: `Whitelist 1🇷🇺`, `Whitelist 2🇷🇺`, …
4. Результат → `/opt/sub-updater/whitelist_links.txt`, по одной строке на профиль:
   `#goida-wl-json:<base64url(JSON)>`.

### 2.2. Реестр WL (`/opt/wl-registry/wl-list.txt`)
Дополнительный, ручной/централизованный список (команда `/addwl` в боте). При записи
**реальный UUID всегда заменяется на `{uuid}`** (`_mask_wl_entry_uuid`), чтобы файл можно
было безопасно шерить.

### 2.3. Сборка в подписку клиента (`vpn-bot.py` → `_collect_wl_links`)
При отдаче подписки оба источника объединяются:
- `whitelist_links.txt` — доверенный, берётся как есть;
- `wl-list.txt` — `{uuid}` подставляется текущим `sub_uid`; серверы, уже покрытые первым
  файлом, **отбрасываются** (дедупликация по паре `host:port`); мёртвые отсеиваются `tcp_alive`;
- итоговый список **перенумеровывается** `Whitelist 1🇷🇺 … N🇷🇺` без `#`.

Итог: в клиенте нет дублей серверов, у всех один UID, имена идут по порядку.

---

## 3. Эндпоинт для партнёра: `GET /wl/share`

Отдаёт `wl-registry/wl-list.txt` — список WL-серверов **с плейсхолдером `{uuid}`**
(реального UUID там нет, шерить безопасно).

```
GET https://ru.goida.fun/wl/share?token=<TOKEN>
```

### Защита
1. **Токен** — обязателен, передаётся в query `?token=`. Неверный/пустой → `401`.
2. **IP-allowlist по домену** — клиентский IP должен резолвиться из `lekanta.ru`
   (env `WL_SHARE_ALLOWED_DOMAIN`, DNS-кэш 5 мин). Чужой IP → `403`.

### Токен
> Значение — только в gitignored `.claude/memory/secrets-and-logic.md`, не в этом файле
> (этот файл — tracked docs, секреты сюда не пишем). Хранится на сервере в
> `vpn-bot.service.d/wl-share.conf` (env `WL_SHARE_TOKEN`).
> При компрометации — перегенерировать (`openssl rand -hex 24`) и обновить override + рестарт `vpn-bot`.

> **2026-07-05:** `/wl/list` (публичный alias без токена, отдававший тот же файл) убран —
> он был обходом этой же защиты. Единственный путь для партнёра теперь — `/wl/share`.

---

## 4. Как спарсить (инструкция для агента партнёра)

### 4.1. Забрать список
```bash
curl -fsS "https://ru.goida.fun/wl/share?token=<TOKEN>" -o wl-list.txt
```
Значение `<TOKEN>` — по отдельному защищённому каналу, не в этом документе.
Запрос должен идти **с сервера lekanta.ru** (иначе `403`).

### 4.2. Формат файла
Одна запись на строку, два возможных типа:

**(a) Plain VLESS-ссылка** (Reality/TCP):
```
vless://{uuid}@<host>:<port>?security=reality&type=tcp&flow=...&pbk=...&sid=...&sni=...&fp=chrome#<имя>
```

**(b) JSON-маркер** (полный xray-профиль, multi-backend/observatory):
```
#goida-wl-json:<base64url(JSON-профиль)>
```
Декодирование (b):
```python
import base64, json
prefix = "#goida-wl-json:"
payload = line[len(prefix):]
cfg = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
# cfg — это {"remarks":..., "outbounds":[...], "routing":{...}, ...}
```

### 4.3. Подставить свой UUID
Везде, где встречается `{uuid}`, подставить **UUID своей hydra/whitestore-подписки**
(первый UUID из vless-outbound вашей подписки). В обоих типах:
- (a) в plain-ссылке: `vless://{uuid}@...` → `vless://<ВАШ_UUID>@...`;
- (b) в JSON: каждый `outbounds[].settings.vnext[].users[].id == "{uuid}"` → ваш UUID.

> Реальный UUID привязан к аккаунту whitestore. Если у вас отдельный аккаунт — используйте
> СВОЙ UUID, иначе сервер не авторизует соединение.

### 4.4. Валидировать живость
Перед выдачей клиентам проверять каждый бэкенд TCP-connect'ом (host:port, таймаут ~4 c).
Недоступные — не отдавать. Дедуплицировать по паре `host:port`.

---

## 5. Чек-лист

- [ ] запрос идёт с IP, резолвящегося из `lekanta.ru`;
- [ ] токен валиден;
- [ ] `{uuid}` заменён на собственный UUID во всех записях (plain и JSON);
- [ ] мёртвые бэкенды отфильтрованы `tcp_alive`;
- [ ] дубли `host:port` удалены;
- [ ] (опц.) переименование `Whitelist 1 … N` для порядка в клиенте.
