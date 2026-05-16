# legacy

Архив неактуальных файлов, вынесенных 2026-05-09 и 2026-05-16.

## Что актуально сейчас

- RU live bot: `/root/vpn-bot/vpn-bot.py`
- локальная актуальная копия RU bot: `bot/vpn-bot.py`
- RU live sub-updater: `/opt/sub-updater/updater.py`
- локальная актуальная копия RU sub-updater: `sub-updater/updater.py`
- актуальная память: `.Codex/`

## Что перенесено сюда

### `legacy/bot/`

Архивные дампы подписок/бота, не являющиеся текущим runtime-кодом.

### `legacy/src/bot/`

Старая tracked-версия `src/bot/vpn-bot.py`.
Она короче и не совпадает с live RU ботом после правок 2026-05-05..2026-05-09.
Не использовать для деплоя.

### `legacy/src/sub-updater/`

Старая tracked-версия `src/sub-updater/updater.py`.
Она не совпадает с live RU updater и не содержит актуальный RU hydra mode.
Не использовать для деплоя.

### `legacy/root-snapshots/xray-config.json`

Старый локальный снапшот xray config.
Источник истины для RU сейчас не этот файл, а `/etc/x-ui/x-ui.db`, ключ `settings.xrayTemplateConfig`.

### `legacy/root-snapshots/test.py`

Случайный локальный scratch-файл с невалидным Python-кодом.
Оставлен только как архивный след, не запускать.

### `legacy/smart-pro/`

Старая утилита/прототипы smart-pro routing. Не источник истины для RU live routing.

### `legacy/lekanta/`

Исторический live-снапшот lekanta. Основной проект теперь держит RU как актуальный путь.

### `legacy/web/`

Старые/примерочные web-файлы, не входящие в актуальный RU сайт.

### `legacy/research/vendor/`

Локальный vendor-снапшот upstream 3x-ui, использованный для reverse engineering подписок.

## Что не переносилось

- `bot/`, `subscription/`, `sub-updater/`, `ip-watchdog/`, `deploy/`, `web/` — актуальная RU-структура.
- `deployer-bot/` — отдельный nested git repo, игнорируется корневым git.
