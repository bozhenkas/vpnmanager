# smart-pro test tools

## rkn-dpi-auto-updater.py

Тестовый планировщик для будущего smart auto routing.

Что делает:
- читает пул целей из `rkn-dpi-targets.example.txt` или файла из `--targets`;
- запускает `rkn-check --url ...`;
- классифицирует результат как `dpi_only`, `dns_or_ip_block`, `ok`, `unknown`;
- учитывает ручные правила из `xrayTemplateConfig`: `direct`, `home-mac-exit`, `balancer-smart`/foreign;
- пишет JSON-план, но не меняет prod.

Пример на RU:

```bash
python3 /opt/smart-pro/rkn-dpi-auto-updater.py \
  --targets /opt/smart-pro/rkn-dpi-targets.txt \
  --report /var/lib/smart-pro/rkn-dpi-plan.json
```

Будущее применение:
- `dpi_only` -> кандидат в zapret host/ip list;
- routing candidate -> `direct-zapret` для smart;
- ручные правила из бота `direct/home/foreign` должны оставаться выше любых auto rules;
- интегрировать надо в генератор `sub-updater/updater.py`, иначе rebuild routing может стереть auto rules.
