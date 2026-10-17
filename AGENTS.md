# Правила проекта Zapret DPI Manager 2.0

## Сборка и релиз

- Сборка пакетов: `tools/build_release.sh` → `dist/Zapret_DPI_Manager.tar.gz` (полный пакет)
  и `dist/zapret_updater.tar.gz` (автообновление, без `config.txt` и `files/lists/*_user.txt`);
  из `installer/install_zapret.sh` пересобирается `install_zapret.tar.gz`. В оба архива входит
  каталог `warp/` (скрипты и юнит WARP), сборка проверяет его состав.
- Версия хранится в `version.txt` (строка 1) и дублируется в `core/manager_config.py`
  (`current_version`) — менять синхронно. Строка 2 `version.txt` — URL архива обновления.
- Релиз публикуется workflow `.github/workflows/release.yml` (тег `v*` или ручной запуск);
  тег обязан совпадать с `version.txt`. В релиз входят оба архива и `SHA256SUMS`.
- Пуш и публикация релиза — только по прямой просьбе пользователя.

## Проверки перед коммитом

```bash
python3 -m compileall -q core ui tools main.py
bash -n zapret/system/starter.sh zapret/system/stopper.sh installer/install_zapret.sh
for f in warp/system/*.sh; do bash -n "$f"; done
bash tools/check_warp_scripts.sh                    # стенд скриптов WARP (в CI — через sudo)
python3 tools/validate_strategies.py --dry-run     # плейсхолдеры, формат, nfqws --dry-run
python3 tools/convert_flowseal_strategies.py --check
python3 tools/check_bbdpi_convert.py               # конвертер BB DPI (ciadpi) → nfqws + dry-run
ruff check core ui tools main.py                   # конфиг в pyproject.toml
XAUTHORITY=$(ls /run/user/1000/xauth_* | head -1) python3 tools/smoke_ui_material.py  # дымовой тест всех окон
```

CI (`.github/workflows/ci.yml`) выполняет те же шаги, кроме дымового теста окон (стенд WARP
запускается через `sudo`, ему нужен `dev/net/tun`; сам стенд — `tools/warp_scripts_test/`).

## Оформление (Material 3)

- Единая тема: `ui/theme/` (`theme.color(role)`, `theme.font(role)`, `theme.text(...)`,
  `theme.space(step)`, `theme.radius(name)`, `theme.px(dp)`); режим dark/light хранится
  в `utils/theme.txt`, переключается в меню настроек главного окна (`MainWindow.rebuild_ui()`).
- Компоненты: `ui/components/material/` (кнопки, карточки, switch/checkbox/radio,
  поля, списки и таблицы, диалоги, snackbar, прогресс, top app bar, тонкий скроллбар).
- Хардкод-цвета в окнах запрещены: цвета берутся только из ролей темы. Прежние API
  (`create_hover_button`, `custom_messagebox`, `_font` в больших окнах) сохранены.
- Отчёт тестера стратегий открывается окном `ui/windows/report_window.py` (данные —
  `utils/reports/*.json`, рядом с HTML; для старых отчётов работает разбор HTML),
  в браузер ничего не открывается автоматически.

## Архитектура (кратко)

- `main.py` → `MainWindow` (миксины в `ui/windows/main/`) → `core/` → `config.txt`
  → `/opt/zapret/starter.sh` → `nfqws --qnum=200` + правила NFQUEUE.
- Служба: `/etc/systemd/system/zapret.service`, `Type=oneshot`, `RemainAfterExit=yes`,
  `ExecStart=/bin/bash /opt/zapret/starter.sh`, `ExecStop=/bin/bash /opt/zapret/stopper.sh`.
- FWTYPE — единый источник истины `core/platform_info.detect_fwtype()` (nft → `nftables`,
  иначе `iptables`). Правила живут в собственной цепочке `ZAPRET` / таблице `inet zapret`,
  чужие правила (Docker/VPN/firewalld) не трогаются.
- WARP (usque) — независимая от zapret подсистема: `core/warp_manager.py` + `warp/system/*.sh`
  (приложение и установщик ставят их в `/opt/warp`, юнит `/etc/systemd/system/warp.service`),
  состояние — `utils/warp.txt` (enabled/mode/transport/sni/iface/mtu/dns/help_tunnel/lists),
  правила для внешнего трафика туннеля — `utils/warp_rules.txt`, endpoint'ы —
  `files/lists/ipset-warp.txt` (плейсхолдер `{ipset_warp}`). Движок usque (MIT) скачивается
  из GitHub-релизов с проверкой SHA256 уже приложением, аккаунт Cloudflare регистрируется
  только после явного согласия с ToS. `zapret/system/starter.sh` исключает интерфейс туннеля
  из NFQUEUE и либо возвращает endpoint'ы из цепочки (`help_tunnel=0`), либо ставит профили
  WARP перед правилами пользователя (`help_tunnel=1`). `warp_start.sh` после подключения
  сам проверяет, что данные через туннель идут, и иначе снимает маршруты и DNS (полный
  туннель без самопроверки оставлял бы машину без интернета). Порядок включения: zapret
  приводится в рабочее состояние ДО подъёма туннеля, иначе рукопожатие MASQUE ломается.
- Все привилегированные команды — только через `core/sudo_helper.run_sudo` (`sudo -A` +
  `core/askpass.py`). Пароль приложение не хранит, кроме явного кэша 0600
  (`~/.cache/zapret_dpi_manager/sudo.cred`) по галочке «Запомнить».
- Плейсхолдеры `{...}` в `config.txt` раскрывает `zapret/system/starter.sh`; набор
  поддерживаемых плейсхолдеров проверяет `tools/validate_strategies.py`.
- Логи ошибок: `~/.local/state/zapret_dpi_manager/error.log` (0600, ротация).
- Обновление по частям без полного обновления приложения (окно «Обновить Zapret», три
  кнопки — приложение / стратегии / движок): `core/strategies_updater.py` (стратегии и
  payload'ы из Flowseal) и `core/engine_updater.py` (движок nfqws из ImMALWARE/zapret-linux-easy).
  Конвертация `.bat` → файл стратегии живёт в `core/flowseal_convert.py` (в релиз попадает
  только `core/`, поэтому `tools/convert_flowseal_strategies.py` — тонкая обёртка для CI).
  Версия стратегий — SHA коммита Flowseal в `utils/strategies_version.txt` (проверка через
  Atom-ленту коммитов, без лимита GitHub API); бэкапы перед записью — в
  `~/.cache/zapret_dpi_manager/`.
- Стратегии из BB DPI (движок ciadpi, hufrea/byedpi): `core/bbdpi_convert.py` переводит
  строку ciadpi в аргументы nfqws, окно `ui/windows/bbdpi_strategy_window.py` открывается
  из «Сменить стратегию» → «Стратегия из BB DPI». Результат проверяется `nfqws --dry-run`
  и сохраняется в `files/strategy/` с записью в `config.txt`. Опции без аналога в nfqws
  (`-o/-q/-r/-A/-a/-m/-Y/-T/-L/-u/-y`) не переносятся — окно предупреждает о них перед
  применением. Проверка конвертера — `tools/check_bbdpi_convert.py` (шаг CI).
- Обход Telegram: `core/flowseal_convert.manager_extra_rules()` добавляет каждой стратегии
  три правила — TCP по hostlist `{list_telegram}` (веб-версия, `t.me`, API), TCP по ipset
  `{ipset_telegram}` (Telegram Desktop ходит на дата-центры MTProto без SNI, hostlist его
  не поймает) и UDP-звонки (`--filter-l7=stun`). hostlist и ipset в nfqws — разные группы
  фильтров и объединяются по AND (bol-van/zapret#2084), поэтому это отдельные правила.
  Плейсхолдер `{telegram}` (конструктор стратегий) ведёт на Telegram-список.
  Цели Telegram — в `utils/test_targets.txt` (критические: `TelegramWeb`, `TelegramAPI`),
  тестер оценивает их как третий сервис рядом с YouTube/Discord. В окне hostlist — галочки
  «Telegram» (домены в `list-telegram_user.txt`) и «Обход Telegram (TCP + звонки)»
  (добавляет/убирает Telegram-строки в `config.txt`); в окне IPSet — вкладка
  «Диапазоны Telegram» (`ipset-telegram_user.txt`); в «Проверке соединения» — блок Telegram.

## Данные

- `files/strategy/` — готовые стратегии (по строке на правило, `--new` в конце строки).
- `files/lists/` — домены/IP; `*_user.txt` — пользовательские, обновление их не перезаписывает.
  Telegram: `list-telegram.txt` (домены) и `ipset-telegram.txt` (официальные CIDR IPv4+IPv6);
  пользовательские дополнения — `list-telegram_user.txt` и `ipset-telegram_user.txt`;
  подставляются плейсхолдерами `{list_telegram}`, `{list_telegram_user}`, `{ipset_telegram}`,
  `{ipset_telegram_user}` (сливаются base+user, как остальные пары).
- `files/bin/` — payload-бинарники; `zapret/bins/<arch>/nfqws` — движок (исполняемый бит обязателен).
- `files/lists/ipset-warp.txt` — endpoint'ы Cloudflare WARP для правил `help_tunnel`; файл
  обновляет окно WARP (`core.warp_manager.write_ipset`) при регистрации аккаунта.
- `core/strategy_data.py` (`STRATEGY_OPTIONS`) — варианты конструктора стратегии;
  `core/game_presets.py` (`GAME_PRESETS`) — игровые пресеты.
- `tools/flowseal_src/*.bat` — исходники стратегий из Flowseal/zapret-discord-youtube
  (версионируются, чтобы `tools/convert_flowseal_strategies.py --check` работал в CI,
  где локального `.dsh/` нет); `--src` переопределяет каталог.
