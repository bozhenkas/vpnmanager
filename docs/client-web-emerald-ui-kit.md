# client-web v5 Midnight Emerald — UI-kit и QA

> Scope: клиентский web-app / Telegram Mini App, текущий стиль v5 Midnight Emerald из `client-web/index.html`.
> Документ фиксирует визуальную систему и QA-чеклист. Runtime-код и прод не трогать.

## Source of truth

- Текущая реализация: `client-web/index.html`.
- Шрифт: локальный Inter Regular/Medium, preload + `font-display:block`.
- Базовый принцип: premium dark clean glass поверх matte emerald mesh. Без декоративных бликов на карточках, без grid/noise-паттерна в основном фоне, без тяжёлого marketing hero.

## Tokens

### Type

Все элементы наследуют Inter и `letter-spacing:-0.04em`.

| token | value | use |
|---|---:|---|
| `--t-title` | `28px` | username / главный идентификатор ключа |
| `--t-section` | `20px` | заголовки карточек, sheet title, крупные значения |
| `--t-headline` | `17px` | body base, device labels |
| `--t-sub` | `15px` | кнопки, tab labels, secondary headings |
| `--t-caption` | `13px` | captions, meta, helper text |

Rules:
- weights только `400` и `500`;
- `body` = `17px / 1.4`, `h2` = `20px / 1.2`, `h3` = `17px / 1.3`;
- не масштабировать type через viewport units;
- длинные строки в узких блоках должны переноситься, а не сжимать layout.

### Color

| token | value | use |
|---|---|---|
| `--c-ink` | `#f6fff9` | основной bright foreground |
| `--c-fg` | `rgba(246,255,249,.94)` | body text |
| `--c-fg2` | `rgba(226,244,235,.66)` | secondary text |
| `--c-fg3` | `rgba(226,244,235,.42)` | tertiary/disabled text |
| `--c-sep` | `rgba(255,255,255,.10)` | separators |
| `--c-green` / `--c-emerald` | `#30d19e` | positive/accent |
| `--c-blue` / `--c-aqua` | `#4aa3ff` | secondary accent |
| `--c-red` | `#ff3b30` | destructive |

Background:
- page base `#03120d`;
- matte mesh: emerald + deep teal radial gradients over `linear-gradient(158deg, #0b3a2f 0%, #06221b 46%, #020d0a 100%)`;
- background may have soft blur overlay and dark vertical shade;
- avoid one-note green-only screens: keep dark neutral depth, white glass, and small blue/aqua support.

### Radius

| token | value | use |
|---|---:|---|
| `--r-card` | `18px` | cards, segmented buttons |
| `--r-btn` | `14px` | buttons, notices, URL box |
| `--r-sm` | `10px` | icon buttons, ticks, small tiles |
| `--r-pill` | `999px` | pills, switches, loader track |

Exceptions:
- sheet top radius `22px 22px 0 0`;
- plan number tile currently `22px`;
- splash/logo object can exceed UI radii because it is not a control.

### Spacing

Current rhythm:
- app shell: max width `640px`, horizontal padding `16px`, mobile `14px`;
- app vertical gap `12px`, mobile `10px`;
- card padding `18px 20px`, mobile `16px`;
- row gap `12px`;
- two-column grid gap `10px`;
- segmented control inner gap `4px`;
- download tabs gap `6px`, actions gap `8px`;
- sheet padding `18px 20px 20px`, gap `12px`, button gap `10px`.

Safe areas:
- top padding includes `env(safe-area-inset-top)`;
- bottom padding includes `env(safe-area-inset-bottom)`;
- sheet must respect bottom safe area if controls are added near the bottom edge.

### Motion

| token | value | use |
|---|---|---|
| `--ease` | `cubic-bezier(.2,.8,.2,1)` | standard component motion |
| `--dur` | `.22s` | small controls |

Rules:
- panels slide with directional `--dx`, around `.34s`;
- accordions animate measured `max-height` with opacity/translate;
- sheets use `visibility` + panel transform to avoid transparent first frame;
- slider drag disables thumb transition while dragging;
- respect `prefers-reduced-motion: reduce` for splash field and panel animation.

## Components

### Clean Glass Cards

Canonical card:
- `background: rgba(255,255,255,.070)`;
- emphasized card: `rgba(255,255,255,.080)`;
- border `rgba(255,255,255,.125)`;
- shadow `0 10px 24px rgba(0,0,0,.20)`;
- backdrop `blur(24px) saturate(1.18)`;
- `overflow:hidden`, `contain:layout`, `isolation:isolate`, `translateZ(0)`.

Hard rule:
- `.card::before` and `.card::after` stay disabled;
- no glare gradients, lens overlays, decorative pseudo-elements, or nested cards.

### Segmented Controls

Use for top nav and compact mutually-exclusive choices.

- wrapper: glass card stack, sticky top `8px`, `grid-template-columns:repeat(3,1fr)`, gap/padding `4px`;
- button: min-height `44px`, radius `18px`, `15px/500`;
- inactive text `--c-fg2`;
- active background `rgba(255,255,255,.115)`, text `--c-ink`, no shadow.

### Switches

Use for binary settings.

- size `51x31`;
- track background off `rgba(255,255,255,.11)`, on `rgba(48,209,158,.24)`;
- border `rgba(255,255,255,.18)`;
- thumb `25x25`, `rgba(245,255,250,.86)`, shadow `0 3px 8px rgba(0,0,0,.22)`;
- fill uses emerald goo `rgba(48,209,158,.50)`;
- active state widens fill and squashes thumb subtly.

States:
- disabled switch must reduce opacity and block pointer events;
- when native subscription cannot honor a toggle, render disabled with explicit short caption instead of pretending the switch works.

### Sliders

Current payment slider is flat emerald, not glass/lens/specular.

- hit area height `48px`;
- track top `21px`, height `5px`, pill radius;
- track `rgba(226,244,235,.15)`;
- fill `linear-gradient(90deg, rgba(48,209,158,.54), rgba(48,209,158,.92))`;
- thumb `34x34`, `#dff8ee`, border `rgba(255,255,255,.28)`, shadow `0 4px 10px rgba(0,0,0,.22)`;
- while dragging: no `left` transition, thumb `#f2fff8`.

QA note:
- `--thumb-pos` must be measured after panel insertion, resize, and orientation changes;
- raw pointer percent should move the thumb continuously, desired value can still snap to integer ticks.

### Buttons

Primary:
- min-height `48px`, radius `14px`, padding `0 20px`;
- background `rgba(255,255,255,.10)`;
- border `rgba(255,255,255,.22)`;
- shadow `0 6px 16px rgba(0,0,0,.18)`;
- text `15px/500`, color `--c-ink`;
- active scale `.97`.

Ghost:
- background `rgba(255,255,255,.070)`;
- border `--card-bd`;
- blur `--gl-blur`;
- no shadow.

Danger:
- background `--c-red`;
- white text;
- shadow `0 6px 18px rgba(255,59,48,.28)`.

Icon buttons:
- `36x36`, radius `10px`;
- icon `18x18`, stroke `currentColor`;
- use icons for copy/chevron/add/remove where possible.

### Sheets

Use for confirmations and destructive actions.

- overlay `rgba(0,0,0,.42)`;
- hidden state uses `visibility:hidden` + `pointer-events:none`;
- panel max-width `640px`, bottom aligned;
- radius `22px 22px 0 0`;
- background `rgba(255,255,255,.08)`;
- backdrop `blur(30px) saturate(1.18)`;
- top border `rgba(255,255,255,.16)`;
- shadow `0 -10px 28px rgba(0,0,0,.24)`;
- title `20px/500`, text `15px`, line-height `1.5`.

Interaction:
- open by forcing layout once, then adding `.show` in `requestAnimationFrame`;
- close only on overlay click or explicit button;
- destructive button is last and uses danger style.

### Download Panels

Pattern: accordion card with unframed header.

- `.dl-card` starts with `gap:0`, open `gap:14px`;
- header is transparent, two-column `1fr auto`, no nested card;
- chevron `32x32`, pill glass, rotates `180deg`;
- body uses measured `--dl-h`, opacity, and small upward translate;
- tabs: `repeat(3,1fr)`, min-height `40px`, radius `10px`, caption type;
- actions: usually `repeat(2,1fr)`, min-height `44px`, radius `14px`.

Responsive:
- if labels clip on Android TV / Windows / iOS narrow widths, split into more rows before reducing type;
- browser `/happ/<token>` download block stays outside `.card` when it needs an unframed header.

### Loaders And Splash

Loading card:
- centered glass card, min-height `min(360px, 48vh)`;
- loader lens `104x52`, pill, subtle white glass;
- emerald goo blob + narrow shine are allowed only inside loader, not on ordinary cards;
- progress track width `min(220px, 72vw)`, height `5px`.

Launch splash:
- full-screen emerald field, z-index above app;
- logo/word visible while app warms;
- reveal only after all gates pass: rendered app, warmed critical assets, minimum splash duration;
- do not hide splash on click;
- avoid blank emerald frame between splash and content/error.

Critical warmup should include:
- Inter 400/500;
- logo and splash icon assets;
- Telegram script or local fallback;
- rendered content or rendered error state.

## Product States To Add

These are approved client-app UX states; implement later in runtime code.

Subscription status text:
- active: `подписка активна до <date>`;
- expiring: `истекает через N дней`;
- expired: `подписка истекла`.

Payment request state:
- pending: `заявка ожидает подтверждения`;
- show it near the tariff card/request button;
- while pending, do not show the primary CTA as if a new identical request can be sent;
- keep old plan and requested plan visible if backend returns both.

## Responsive Rules

- First viewport is the app itself, not a landing page.
- Max app width `640px`; no full-width cards on desktop beyond that shell.
- Mobile breakpoint currently `480px`: reduce app/card padding and stack `.grid2`.
- Fixed-format controls must have stable dimensions: nav buttons, icon buttons, switches, slider thumb, ticks, chevrons, device icons.
- Text must not overlap or clip inside buttons, pills, tabs, cards, or sheets.
- Prefer wrapping and grid row changes over smaller viewport-based type.
- Keep safe-area padding on iOS.
- Test Telegram Mini App viewport, normal browser viewport, and `/happ/<token>` browser-stub route separately.

## QA Checklist

### Visual

- [ ] Page reads as Midnight Emerald: dark teal/emerald mesh, clean glass, no purple/beige/blue-slate drift.
- [ ] Cards have no decorative `::before`/`::after`, glare, lens, or nested card look.
- [ ] Main slider is flat emerald: no glass lens/specular spans; thumb is not stuck at the left edge.
- [ ] Segmented control active state is visible but quiet; inactive labels remain readable.
- [ ] Switch off/on states are distinct and do not resize rows.
- [ ] Download and device accordions open without layout jumps.
- [ ] Sheet first frame is opaque enough; no transparent flash.
- [ ] Splash never disappears into an empty emerald background.

### Letter Spacing

- [ ] Global rule remains `letter-spacing:-0.04em`.
- [ ] `button, input, textarea, select, a` inherit `letter-spacing`.
- [ ] Long Russian labels still fit or wrap cleanly with `-0.04em`.
- [ ] Captions at `13px` remain readable on iOS and Android Telegram webview.
- [ ] Logo/SVG strokes and icons are not distorted by text letter-spacing.
- [ ] No local component silently resets to `letter-spacing:0` unless there is a measured reason.

### Responsive

- [ ] Check widths: 320, 360, 390, 430, 480, 640 px.
- [ ] Check iOS Safari / Telegram Mini App with safe-area top and bottom.
- [ ] Check Android Telegram webview for button text clipping.
- [ ] Check desktop browser at narrow and wide widths; app stays centered at max `640px`.
- [ ] Check Windows manual-copy/download captions if `/happ/<token>` is changed.
- [ ] Rotate device: slider thumb and accordion heights resync.

### Motion And State

- [ ] `prefers-reduced-motion: reduce` disables splash field animation and panel animation.
- [ ] Swipe navigation does not fight slider dragging.
- [ ] Slider drag follows raw pointer smoothly and snaps desired value predictably.
- [ ] Plan card updates price, badge, ticks, and button without layout thrash.
- [ ] Pending tariff request state is visually distinct from idle request state.
- [ ] Subscription active/expiring/expired states are clear without relying only on color.

### Functional Smoke For UI Changes

- [ ] Local HTML parses.
- [ ] Inline JS parses.
- [ ] Font preloads do not 404 in the served environment.
- [ ] Critical warmup resolves both success and error render paths.
- [ ] Copy button, Happ link, download tabs, devices accordion, servers accordion, reminders switch, plan request sheet all still respond.

