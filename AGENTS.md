# goida-vpn · Codex L0 (always loaded)

> Точка входа Codex. Общая память едина для всех агентов; глубину грузить по задаче — см. memory-map ниже.
> Прод заморожен после инцидента 2026-06-05: не мутировать прод без явного запроса (см. `tasks.md`).

## identity
- self-hosted VPN cluster (Remnawave-first) + Telegram mgmt-бот (`vpn-bot`) + client-bot/Mini App + deployer-bot.
- owner: Leonid (`bozhenkas`, TG `294057781`).
- стек: Python 3.12, bash. `vpn-bot.py` — single-file, без внешнего pip. Стиль: terse, lowercase, русские комменты и логи.

## hard rules
- не угадывать IP/порты/ключи/пути — `secrets-and-logic.md` или live-сервер.
- backup перед любой мутацией прода — путь backup-файла в ответе раньше команды правки.
- НЕ direct-SQL переименовывать Remnawave-теги `REMNA_VLESS_TCP_REALITY_7443` / `HOME_VLESS_TCP_REALITY_7443` (чистит node mappings).
- Telegram-трафик — через foreign, НИКОГДА в `DIRECT`.
- Happ-подписка остаётся JSON-routing, не откатывать в plain base64.
- НЕ обновлять Remnawave panel пока flow-патч не persistent.
- `googleapis/gstatic/googleusercontent` — никогда в YouTube/zapret-листы.
- «починено» только после воспроизведения симптома: `/` 200 + WS `101` + nodes connected + sub JSON `200` НЕ доказывают real Happ-путь (нужен shadow-smoke).
- дважды одна ошибка → web-поиск 3–5 решений, не повторять то же действие.
- секреты (.env/токены/creds/SSH) — только в gitignored `.claude/`, никогда в tracked `docs/`.

## startup load order (token-economy)
0. **live-инвентарь кластера:** `python3 scripts/fetch-cluster.py --check` — единый source-of-truth по IP/доменам/статусам нод. Live АВТОРИТЕТЕН по IP; расхождение с памятью → побеждает live, пометить stale (см. hard rule о расхождении ниже).
1. **always:** этот файл (`AGENTS.md`) + `.claude/soul.md` (характер).
2. **перед исследованием:** `.claude/memory/MEMORY.md` — общая карта памяти и правила её обновления.
3. **по задаче, on-demand:** грузить только нужные файлы из memory-map.
   - состояние/задачи → `memory/facts.md`, `memory/tasks.md`.
   - инфра/IP/порты/роутинг (живой snapshot) → `memory/infrastructure-live-20260605.md`.
   - **глубина + секреты** (реальные IP/токены/пути/creds, deploy-команды, вся опер-логика) → `memory/secrets-and-logic.md` (gitignored).
   - прод-деплой → `memory/prod-deploy-runbook.md`.
   - Remnawave-баги → `wings/remnawave/known-bugs.md`; bot → `wings/bot/ru-bot.md`.
4. **recall/changelog:** `.claude/memory/mempalace-protocol.md`; если MCP доступен — `mempalace_search(..., wing="goida-vpn")` перед ответами о прошлых решениях/инцидентах/истории.
5. опц.: `graphify-out/index.md` (auto-graph), `/graphify query "<вопрос>"`.

## multi-agent coordination
- Перед правками читать `git status --short` и diff затрагиваемых файлов: рядом могут работать Cursor и Claude Code.
- Не откатывать и не переформатировать чужие незавершённые изменения; минимальный diff по своей задаче.
- Один агент одновременно мутирует прод. Остальные в это время — только read-only.
- Новые устойчивые знания писать в канонический файл из `MEMORY.md`, не в agent-specific инструкции.
- MemPalace — общий recall-layer, не замена markdown truth: искать там историю, писать diary/changelog после существенных задач, но current facts обновлять в канонических `.claude/memory/*`.
- Если память расходится с кодом, свежим документом или live-сервером — не «усреднять»: проверить источник и явно обновить/пометить устаревшую запись.

## archive (read-only, не писать)
- `legacy/.Codex/`, `legacy/.claude-old/` — старая память; `legacy/{src,lekanta,smart-pro,research}/` — старый код.
