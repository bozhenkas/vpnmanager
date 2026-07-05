# Резерв → учёт трафика юзера на основном RU («ручка»)

> Требование владельца (2026-06-11): резерв `194.117.80.94` обслуживает **тех же
> юзеров**, и ГБ, прошедшие через резерв, **прибавляются к общему счётчику трафика
> того же пользователя** на основном RU. **Маршрутизацию НЕ меняем** — egress
> резерва остаётся FIN-direct (RU-независим). Это чисто учёт (accounting sync).

## Что есть сейчас (verified 2026-06-11)
- Резерв-xray `/etc/xray/reserve-fin.json`: inbound `GOIDA_RESERVE` (27 клиентов,
  **email-теги есть**) + relay `REMNA_FI`. **НЕТ** блоков `stats`/`api`/`policy` →
  per-user трафик сейчас НЕ считается.
- 27 email = те же 27 юзеров, что на RU (общий пул). Маппинг по email/UUID реален.
- На RU Remnawave считает трафик нод сам (node→backend queue); резерв — НЕ нода,
  поэтому его трафик в учёт не попадает.

## Дизайн «ручки» (простая, безопасная, идемпотентная)

### Шаг 1 — включить per-user stats на резерв-xray (разовая правка резерва)
Добавить в `reserve-fin.json`:
- `"stats": {}`
- `"api": {"tag":"api","services":["StatsService"]}`
- `"policy": {"levels":{"0":{"statsUserUplink":true,"statsUserDownlink":true}}}`
- API-inbound `dokodemo-door` на `127.0.0.1:10085` (tag `api`) + routing-правило
  `inboundTag:[api] → outboundTag:api`.
- Бэкап `reserve-fin.json` → restart `xray-reserve-fin.service`.
- ⚠ inbound `GOIDA_RESERVE` (Reality-параметры) НЕ трогать — это клиентская подписка 1:1.

### Шаг 2 — поллер `reserve-traffic-sync` (на резерве, systemd timer)
Каждые N минут (напр. 10):
1. `xray api statsquery --server=127.0.0.1:10085 -pattern "user>>>" -reset`
   → отдаёт uplink+downlink по каждому email **с момента прошлого опроса** и
   обнуляет счётчики (`-reset` = естественная дельта, без хранения last-seen →
   нет двойного счёта).
2. Суммировать up+down по каждому email (= один RU-юзер).
3. Для каждого юзера с дельтой > 0 → добавить байты к его трафику на RU.

### Шаг 3 — кредит трафика на RU (КЛЮЧЕВОЙ вопрос механизма)
Варианты (выбрать безопасный, подтвердить на доке Remnawave перед сборкой):
- **(A) Remnawave API** `POST /api/users/{uuid}/actions/...` или bulk «add usage»
  endpoint (если есть в 2.x) — предпочтительно, не ломает внутренний учёт.
- **(B) Прямой инкремент** `user_traffic` / вставка в `nodes_user_usage_history`
  с фиктивной node — рискует быть перезатёртым backend'ом; только если API нет.
Маппинг email→RU-user UUID: по `users.email`/`username`/`telegram_id` (сверить,
какой идентификатор лежит в email клиента резерва).

### Безопасность / идемпотентность
- `-reset` гарантирует, что один и тот же ГБ не зачтётся дважды.
- Если поллер упал между statsquery(-reset) и записью на RU → теряется одна дельта
  (недоучёт), НЕ переучёт. Приемлемо (консервативно). Для надёжности — писать
  дельту в локальный журнал до отправки, ретраить.
- «Ручка» вкл/выкл: `systemctl enable/disable reserve-traffic-sync.timer`.

## Открытые вопросы (подтвердить до сборки)
- Q-A. Какой Remnawave-API endpoint добавляет usage юзеру в 2.x (или допустим
  прямой DB-инкремент)? → research docs.remna.st / backend routes.
- Q-B. Идентификатор в email клиента резерва (= чему мапить на RU: uuid/email/tg_id)?
- Q-C. Период поллинга (по умолчанию 10 мин) и нужен ли учёт «задним числом» за
  время до включения (нет — стартуем с нуля).

## НЕ делаем
- НЕ меняем egress (остаётся FIN-direct).
- НЕ трогаем inbound `GOIDA_RESERVE` Reality-параметры.
- НЕ делаем резерв Remnawave-нодой (это сменило бы архитектуру и inbound).
