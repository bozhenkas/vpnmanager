# goida-vpn — внутренняя документация (sanitized)

> Этот файл **в git-репозитории** → секретов и реальных IP/токенов здесь нет.
> Вся опер-логика + чувствительные детали (реальные IP/порты/SSH/токены/creds/пути/deploy-команды,
> CF zone, whitestore upstream, test-users) живут в **gitignored** файле:
> **`.claude/memory/secrets-and-logic.md`**.
> Live-состояние инфраструктуры: `.claude/memory/infrastructure-live-20260605.md`.
> Что грузить и в каком порядке: `.claude/memory/MEMORY.md`.
>
> Прежняя версия этого файла (2026-05-31) содержала реальные токены/IP/email и устаревшие факты
> (старый RU IP, 3X-UI-first, публичный :7443) — вычищена 2026-06-05.

---

## 1. что это за проект
**goida-vpn** — self-hosted VPN-кластер (~25 пользователей) с Telegram-ботами управления.

- **VPN-ядро:** Xray через Remnawave (panel + nodes), VLESS over WebSocket/TLS на ингрессе, Reality TCP на выходных нодах.
- **Панель:** Remnawave (Docker) на RU-сервере.
- **Management bot:** `vpn-bot.py` — single-file Python, Telegram polling, выдача подписок, управление пользователями, DNS failover.
- **Client bot + Mini App:** aiogram v3 + Telegram WebApp.
- **DPI bypass (РФ):** zapret2 на RU (nftables/nfqws).
- Язык: Python 3.12, bash. Стиль: lowercase, русские комменты. Без внешних pip в основном боте.

## 2. где какая правда (карта памяти)
| нужно | файл (gitignored, `.claude/`) |
|---|---|
| identity, hard rules, порядок загрузки | `../AGENTS.md` (tracked, L0) |
| live-snapshot: IP/порты/ноды/inbounds/routing/squads | `.claude/memory/infrastructure-live-20260605.md` |
| **всё чувствительное + опер-логика + deploy** | `.claude/memory/secrets-and-logic.md` |
| факты + текущее состояние | `.claude/memory/facts.md` |
| backlog / production-freeze | `.claude/memory/tasks.md` |
| прод-деплой (стоп-условия, shadow-smoke, rollback) | `.claude/memory/prod-deploy-runbook.md` |
| Remnawave грабли | `.claude/wings/remnawave/known-bugs.md` |
| боты/подписка | `.claude/wings/bot/ru-bot.md` |

## 3. безопасность / правила (sanitized)
- Секреты (`.env`, токены, CF_TOKEN, NOTIFY_TOKEN, basic-auth, SSH-ключи, paid upstream URLs, реальные sub-токены пользователей) — **только в gitignored `.claude/`**, никогда в `docs/` или коде, который коммитится.
- Перед любой мутацией прода — backup, путь backup-файла в ответе.
- Прод заморожен после инцидента 2026-06-05 (Remnawave migration / Happ routing) — детали `docs/incident-20260605-remnawave-migration.md`.

> Все конкретные значения (IP, порты, пути, имена контейнеров, тэги inbound'ов, squads, команды деплоя)
> намеренно убраны из этого tracked-файла. Смотри `.claude/memory/secrets-and-logic.md`.
