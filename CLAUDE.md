# goida-vpn · Claude Code L0

> Точка входа Claude Code. Не хранить здесь факты проекта: общая память лежит в `.claude/memory/`.

## startup
0. **Спарсить LIVE-инвентарь кластера:** `python3 scripts/fetch-cluster.py --check`. Это единый source-of-truth по IP/доменам/статусам серверов (`.claude/cluster/cluster.json`, на прод-нодах — расшифрованный `cluster.age`). Live-конфиг АВТОРИТЕТЕН по IP: при расхождении с `infrastructure-live-*.md`/`secrets-and-logic.md` побеждает live — пометить stale-память, не «усреднять». `--check` показывает публичные IP из памяти, которых нет в live.
1. Прочитать `AGENTS.md` — общие hard rules проекта.
2. Прочитать `.claude/soul.md` — стиль, границы и operational posture.
3. Прочитать `.claude/memory/MEMORY.md` — карту памяти, приоритет источников и write policy.
4. Прочитать `.claude/memory/mempalace-protocol.md` при задачах про историю/решения/changelog или когда нужен recall вне коротких L0/L1.
5. Загрузить только файлы, нужные для текущей задачи.

## Claude Code policy
- Project memory в `.claude/memory/` — source of truth для Codex, Cursor и Claude Code.
- MemPalace — общий recall/index-layer для Codex, Cursor и Claude Code; искать там историю и писать короткий diary/changelog после существенных задач, но не переносить project facts в auto-memory Claude и не создавать параллельный индекс памяти.
- Перед изменениями проверить `git status --short` и diff затрагиваемых файлов: в worktree могут работать другие агенты.
- Не откатывать чужие изменения. Делать минимальный diff; при пересечении сначала перечитать файл.
- Прод по умолчанию read-only. Мутация — только по явному запросу владельца, с backup и по runbook.
- После задачи обновлять память только если изменился устойчивый факт, решение, active task или runbook; историю не выдавать за live-состояние.

## pointers
- состояние / backlog: `.claude/memory/facts.md`, `.claude/memory/tasks.md`
- live-инфра: `.claude/memory/infrastructure-live-20260605.md`
- секреты / deploy / точные значения: `.claude/memory/secrets-and-logic.md`
- прод-runbook: `.claude/memory/prod-deploy-runbook.md`
- Remnawave: `.claude/wings/remnawave/known-bugs.md`
- bot / Mini App / subscriptions: `.claude/wings/bot/ru-bot.md`
- MemPalace MCP: `.claude/memory/mempalace-protocol.md`, wrapper `scripts/mempalace-home-mcp.sh`
