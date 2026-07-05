# Диагноз «Оптимальный 2» (GOIDA_SMART2 / smart2) — 2026-06-11

READ-ONLY диагностика. Никаких мутаций прода не выполнялось. Все проверки —
чтение + воспроизведение симптома (внешний probe). Authenticated generate_204
через реальный UUID **не** выполнен (нет локального xray; spawn контейнера на
проде — мутация, заблокирована классификатором). Это единственный
не-воспроизведённый шаг — см. раздел «Что осталось проверить».

## TL;DR

**Серверная сторона полностью задеплоена и работает.** Inbound `GOIDA_SMART2`
живёт в Xray, слушает `45.91.53.93:7443`, обслуживает 27 юзеров, Reality-handshake
валиден и доступен из публичного интернета. Подписка корректно выдаёт строку
«Оптимальный 2», крипто-параметры (pbk/sid/sni) клиента совпадают с inbound в БД.
Роутинг полностью зеркалит `GOIDA_SMART`. **Обрыва на сервере нет.**

Если у юзеров «не работает» — причина **клиентская**, наиболее вероятно:
SNI `www.microsoft.com` (не русский, как требовала спека → DPI/throttle из РФ) и/или
рассинхрон `mode` (клиент `stream-one` vs сервер `auto`) при capризности
конкретных приложений к xhttp+reality.

## Фактический статус по слоям (всё ✅ на сервере)

| Слой | Статус | Доказательство |
|---|---|---|
| IP `45.91.53.93` на хосте | ✅ поднят | `ip addr`: `inet 45.91.53.93/24 ... ens3` (primary), `45.91.54.152` — alias `ens3:0` |
| Listener `45.91.53.93:7443` | ✅ слушает (xray/rw-core) | `ss -tlnp`: `45.91.53.93:7443 users:(("rw-core",pid=2638))`; внутри `remnanode`: `45.91.53.93:7443 LISTEN 65/rw-core` |
| Inbound в БД | ✅ есть | `config_profile_inbounds` tag=`GOIDA_SMART2`, uuid `dd103fa6-...`, vless/xhttp/reality/7443 |
| Node mapping | ✅ есть | `config_profile_inbounds_to_nodes` → node `fdd0c8a3-...` |
| Squad mapping | ✅ есть, тот же что у SMART | `internal_squad_inbounds` → squad `16ebf9cd-...` (идентичен GOIDA_SMART) |
| Xray реально обслуживает | ✅ 27 юзеров | `docker logs remnanode`: `GOIDA_SMART2 has 27 users`, в hash-payload inbound присутствует |
| Reality handshake (внешний) | ✅ валиден | off-host openssl `45.91.53.93:7443 -servername www.microsoft.com` → `subject=CN=www.microsoft.com`, `Verify return code: 0 (ok)` |
| Публичная доступность 7443 | ✅ открыт | off-host `nc -z 45.91.53.93 7443` → succeeded; ufw inactive, нет блок-правил |
| Reality dest reachable (egress) | ✅ | сервер → `www.microsoft.com:443` `Verify return code: 0` |
| Подписка выдаёт строку | ✅ | bot `127.0.0.1:9090/subscribe/<token>` содержит строку «Оптимальный 2» (см. ниже) |
| Crypto клиент↔БД | ✅ совпадает | pbk `ciaUCiKdRw4JzZhfM3JvkDXfoee0tofGB0mRF6GVK2U` = x25519(privKey из БД); sid `ae85faac` = БД; sni `www.microsoft.com` = БД |
| Routing GOIDA_SMART2 | ✅ зеркало SMART | все 15 правил с GOIDA_SMART также содержат GOIDA_SMART2 (zapret-direct, telegram→balancer, catch-all foreign) |

### Реальная строка подписки (UUID отредактирован)
```
vless://<UUID>@45.91.53.93:7443/?type=xhttp&security=reality&encryption=none
&sni=www.microsoft.com&fp=chrome&pbk=ciaUCiKdRw4JzZhfM3JvkDXfoee0tofGB0mRF6GVK2U
&sid=ae85faac&path=%2Fsmart2&mode=stream-one#Оптимальный 2 🇸🇨
```
flow отсутствует (правильно — Vision несовместим с xhttp). Всё прочее совпадает с
inbound в БД.

## Расхождения local-repo ↔ prod (важно для понимания истории)

1. **nginx TCP-passthrough НЕ задеплоен и НЕ нужен.** Файл
   `deploy/nginx/remna-smart2-7443.conf` (stream → `127.0.0.1:17449`) описывает
   архитектуру, которая **не применялась**: на проде нет `/etc/nginx/stream.d/`,
   нет smart2-конфига в nginx, порт `17449` никто не слушает. Вместо этого Xray
   биндит публичный `45.91.53.93:7443` напрямую (как и задумано в
   `p14_goida_smart2_apply.py` → `listen: 45.91.53.93`). То есть актуальная схема —
   **прямой Xray-listen**, а `remna-smart2-7443.conf` — устаревший/отброшенный
   вариант. Это не баг, но конфиг в репо вводит в заблуждение.

2. **SNI: спека vs прод.** tasks.md п.14 требовал «русский рабочий SNI (кандидат
   `www.sberbank.ru`)». Прод и apply-скрипт по факту используют
   **`www.microsoft.com`**. Это главный кандидат на клиентский обрыв (см. ниже).

3. **`mode`: рассинхрон.** Inbound в БД: `xhttpSettings.mode = "auto"`.
   Клиентская строка (vpn-bot `remnawave_smart2_link`): `mode=stream-one`.
   Сервер `auto` в общем случае принимает любой клиентский режим, поэтому это,
   скорее всего, не фатально — но при конкретных версиях клиентов (Happ/v2rayNG)
   и xhttp+reality встречаются несовместимости. Низкий-средний приоритет.

## Корневая причина — приоритизировано по доказательствам

**Сервер не сломан** (доказано внешним probe + логами + БД). Симптом «не работает»
у юзера — клиентский. Гипотезы по убыванию вероятности:

1. **[ВЫСОКИЙ] SNI `www.microsoft.com` режется DPI из РФ.** Reality прячется под
   `www.microsoft.com`; наш off-host probe прошёл, но он шёл **не из
   зацензуренной RU-сети клиента**. microsoft.com под RU DPI может
   throttle/RST → Reality handshake у реального юзера падает, хотя с нашей точки
   всё «ok». Это ровно урок инцидента: `/`200/WS101/наш probe ≠ реальный путь
   клиента. Спека прямо просила русский SNI (`www.sberbank.ru`) — его проигнорили.

2. **[СРЕДНИЙ] Несовместимость клиент-приложения с xhttp+reality / `mode`.**
   Клиент шлёт `mode=stream-one`, сервер `auto`. Часть приложений в РФ
   (старые Happ/v2rayNG/NekoBox) либо не поддерживают xhttp+reality, либо
   капризны к mode. Симптом будет «у части юзеров на части клиентов».

3. **[НИЗКИЙ] Конкретный пользователь без UUID в clients inbound.** 27 юзеров в
   inbound — но если у тестировавшего юзера squad-членство рассинхронено,
   VLESS-auth отвергнет. Маловероятно (squad идентичен SMART), но это
   единственное, что не покрыто authenticated-тестом.

Гипотезы «IP не поднят / listener мёртв / nginx upstream мёртв / flow-vision на
xhttp / подписка не выдаёт строку / squad не привязан / нода не слушает /
firewall» — **исключены доказательствами** (см. таблицу).

## Что осталось проверить (не воспроизведено)

Authenticated **generate_204 через реальный UUID из зацензуренной RU-сети**.
Только это отличит гипотезу 1 (SNI/DPI) от гипотезы 2 (клиент/mode). С нашего
хоста и с macOS без локального xray этот шаг не выполнить корректно. Нужен:
- временный локальный xray-клиент **на устройстве в РФ** (или RU-VPS вне нашего
  кластера) с этой smart2-строкой → `curl -x socks5h generate_204`;
- параллельно — та же строка, но `sni=www.sberbank.ru` (после фикса), чтобы
  сравнить.

## План фикса по фазам (НЕ выполнять сейчас; бэкап перед каждой мутацией)

### Фаза 0 — подтвердить гипотезу (read-only, без мутаций)
- Прогнать authenticated generate_204 из RU-сети с текущей строкой
  (`sni=www.microsoft.com`, `mode=stream-one`). Если падает на handshake → SNI/DPI.
  Если handshake ок, но 204 нет → routing/auth (но routing уже зеркало SMART).

### Фаза 1 — сменить SNI на русский рабочий (если Фаза 0 подтвердила гипотезу 1)
Бэкап:
```
docker exec remnawave-db psql -U postgres -d postgres -c \
 "CREATE TABLE config_profile_inbounds_bak_<ts>_smart2sni AS \
  SELECT * FROM config_profile_inbounds WHERE tag='GOIDA_SMART2';"
```
1. Подобрать русский SNI, реально живой на :443 и не наш (кандидаты:
   `www.sberbank.ru`, `dzen.ru`, `www.gosuslugi.ru` — проверить Reality-пригодность:
   TLS1.3, X25519, HTTP/2, не CDN с нашим IP). Проверить `openssl s_client` к нему.
2. Обновить `realitySettings.target` и `serverNames[0]` в `raw_inbound` (direct-SQL
   UPDATE по uuid `dd103fa6-...`) **и** `config_profiles.config` если inbound
   дублируется в профиле. **Не** трогать privateKey/shortIds.
3. Синхронно обновить `SMART2_REALITY_SNI` в `/root/vpn-bot/.env` (значение должно
   совпасть с новым serverNames). Перегенерировать подписки/рестарт бота.
4. `docker restart remnawave && sleep 8 && docker restart remnanode` (как в
   p14-скрипте). Проверить ss + Reality probe с новым SNI.
5. Re-test generate_204 из РФ.

### Фаза 2 — выровнять `mode` (если Фаза 0 указала на клиент/mode)
- Вариант A (минимально-рискованный): в БД `xhttpSettings.mode` уже `auto` —
  оставить; в боте сменить клиентский `mode=stream-one` → `mode=auto` (или убрать
  param). Правка в `bot/vpn-bot.py::remnawave_smart2_link`. Только бот, без рестарта
  ноды.
- Вариант B: явно зафиксировать `packet-up`/`stream-up` по тестам конкретных
  клиентов. Решать после Фазы 0.

### Фаза 3 — гигиена репо (не блокирует фикс)
- Пометить `deploy/nginx/remna-smart2-7443.conf` как deprecated/неиспользуемый
  (актуальна схема прямого Xray-listen), либо удалить, чтобы не путал.
- Привести `SMART2_REALITY_SNI` дефолты (apply-скрипт, тесты, бот) к фактически
  выбранному русскому SNI.

## Команды-доказательства (для воспроизведения)
- IP: `ssh ... 'ip addr | grep 45.91.53.93'`
- Listener: `ss -tlnp | grep 7443`; внутри ноды `docker exec remnanode netstat -tlnp | grep 7443`
- Xray обслуживает: `docker logs remnanode | grep GOIDA_SMART2`
- Внешний Reality probe: `openssl s_client -connect 45.91.53.93:7443 -servername www.microsoft.com` (off-host) → Verify return code: 0
- БД inbound: `psql ... "SELECT raw_inbound::text FROM config_profile_inbounds WHERE tag='GOIDA_SMART2';"`
- Подписка: `curl -H 'User-Agent: v2rayNG' http://127.0.0.1:9090/subscribe/<token>` → строка «Оптимальный 2»
- Routing: jsonb-проверка правил (все правила SMART содержат SMART2)
