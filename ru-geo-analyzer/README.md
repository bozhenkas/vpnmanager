# ru-geo-analyzer

Маленький Go-демон, который чинит промахи `geoip:ru`: смотрит, какие сайты/серверы
реально русские, и предлагает добавить их в direct-роутинг.

## Зачем

`geoip:ru` (MaxMind в xray) ошибается — часть RU-серверов уезжает во VPN
(риск блокировки по ТСПУ + лишний крюк), часть иностранных помечается RU.
Демон активно определяет **реальное** расположение серверов, которые прошли
через RU-ноду, и собирает кандидатов на добавление в `direct`.

## Как работает

```
xray access.log (RU)                    ← источник: реальный трафик юзеров
  └─ logtail: парсит "accepted tcp:HOST:port [inbound -> outbound]"
       └─ берём только HOST, ушедшие на ПРОКСИ (не DIRECT) = потенц. промахи geoip
            └─ detect: консенсус на КАЖДЫЙ IP хоста
                 ├─ RDAP        — страна регистрации блока (авторитетно)   вес 2
                 ├─ Cymru ASN   — DNS-TXT: ASN + страна аллокации + RIR     вес 2
                 ├─ PTR         — reverse DNS .ru/.su/.рф (хинт)            вес 1
                 └─ RTT (опц.)  — активный TCP-пинг с RU-ноды (<18мс = RU)  вес 2
                      → RU, если есть авторитетный RU-голос и RU-вес > не-RU
                 └─ домен = RU только если ВСЕ его IP — RU (без CDN-ложняков)
       └─ store: дедуп, hits, first/last seen, кэш вердиктов, персист
  └─ candidates.json  ← РЕВЬЮ-файл, демон НЕ трогает роутинг сам
```

Потом — отдельный подтверждённый шаг (питон):

```
candidates.json
  └─ scripts/promote_ru_candidates.py   (default dry-run; --apply пишет)
       ├─ фильтр: confidence/hits + суффикс-покрытие (foo.ru уже в domain:ru → skip)
       └─ subscription/ru_direct_auto.py → ru_routing.py → routing.json → подписки
```

`ru_direct_auto.py` (автодетект) и курируемый список (`ru_routing.py`,
`bot/ru_direct_domains.py`, источник 1984.is) держатся **раздельно** — откат =
очистить авто-файл.

## Сборка

```bash
cd ru-geo-analyzer
go build -o ru-geo-analyzer .     # статический бинарь, без внешних зависимостей
go test ./...
```

Кросс-компиляция под прод (RU = linux/amd64):

```bash
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -ldflags="-s -w" -o ru-geo-analyzer .
```

## Деплой на RU (по ОК владельца)

1. Узнать host-путь access.log (лог пишет контейнер remnanode):
   `docker inspect remnanode --format '{{json .Mounts}}' | python3 -m json.tool`
   и прописать в `RGA_LOG_PATH`.
2. `scp ru-geo-analyzer root@RU:/usr/local/bin/`
3. `scp config.example.env root@RU:/etc/ru-geo-analyzer/config.env` (поправить пути).
4. `scp deploy/systemd/ru-geo-analyzer.service root@RU:/etc/systemd/system/`
5. `systemctl daemon-reload && systemctl enable --now ru-geo-analyzer`
6. Через сутки-двое: `cat /var/lib/ru-geo-analyzer/candidates.json` → ревью →
   `python3 scripts/promote_ru_candidates.py --candidates <файл>` (dry-run) →
   `--apply`.

Ресурсы зажаты в юните: `MemoryMax=64M`, `CPUQuota=15%`, `Nice=10`,
`IOSchedulingClass=idle`. Реальный таргет ~15–20 МБ RAM.

## Конфиг

Все параметры — через env (`config.example.env`) или флаги
(`-log`, `-candidates`, `-from-start`, `-rtt`). `RGA_RTT_ENABLE=true` — только на
RU-ноде (активный RTT с другой машины бессмысленен).

## Что демон НЕ делает

- не меняет роутинг и подписки (только пишет candidates.json);
- не трогает Remnawave/ноды;
- direct в Happ = байпас VPN, этот трафик RW и не видит (by design) — задача
  демона лишь в том, чтобы **генератор (ru_routing.py → routing.json) клал
  корректные профили в RW-стек**.
```
