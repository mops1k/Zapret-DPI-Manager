#!/bin/bash
# WARP (usque): запуск туннеля для Zapret DPI Manager.
#
# Устанавливается в /opt/warp/warp_start.sh и запускается юнитом warp.service
# (Type=oneshot, RemainAfterExit=yes). Задача: прочитать настройки пользователя
# (utils/warp.txt), поднять туннель `usque nativetun`, дождаться маркера
# подключения (его ставит хук warp_up.sh) и при автотранспорте откатиться
# с QUIC на HTTP/2.
#
# Скрипт намеренно работает без `set -e`: часть шагов (маршруты, DNS) может
# не примениться на экзотической сети, но туннель от этого ронять не нужно.
# Все ошибки логируются в journald (тег warp) и в /var/log/warp-usque.log.
#
# Переменные окружения (для отладки и тестов):
#   WARP_CONNECT_TIMEOUT — ожидание подключения в секундах (по умолчанию 15)
#   WARP_STATE_FILE      — путь к utils/warp.txt (по умолчанию ищется у пользователя)
set -uo pipefail

# Пути: по умолчанию как в юните; переопределяются окружением для отладки и тестов.
WARP_DIR="${WARP_DIR:-/opt/warp}"
STATE_DIR="${STATE_DIR:-/run/warp}"
LOG_FILE="${LOG_FILE:-/var/log/warp-usque.log}"
USQUE="${USQUE:-$WARP_DIR/usque}"
ACCOUNT="${ACCOUNT:-$WARP_DIR/account.json}"

# Значения по умолчанию; перекрываются файлом состояния.
MODE="full"
TRANSPORT="auto"
SNI="consumer-masque.cloudflareclient.com"
IFACE="warp0"
MTU="1280"
DNS_MODE="link"
HELP_TUNNEL="1"
LISTS=""

# Ожидание подключения: подключение + самопроверка данных (до 2 попыток по 6 с).
CONNECT_TIMEOUT="${WARP_CONNECT_TIMEOUT:-35}"

log() { logger -t warp -- "$*" 2>/dev/null || true; echo "$*"; }
err() { logger -t warp -p err -- "$*" 2>/dev/null || true; echo "$*" >&2; }
die() { err "$*"; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run as root" >&2
    exit 1
fi

mkdir -p "$STATE_DIR"
# Каталог читается приложением (статус туннеля), конфиг хуков — 0600.
chmod 755 "$STATE_DIR" 2>/dev/null || true

# Границы лога: храним текущий и предыдущий запуск usque.
if [ -f "$LOG_FILE" ]; then
    mv -f "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null || true
fi

# --- Определяем домашний каталог пользователя с установленным менеджером ---
detect_home() {
    local candidate home
    for candidate in "${SUDO_USER:-}" "$(logname 2>/dev/null || true)"; do
        [ -n "$candidate" ] || continue
        [ "$candidate" = "root" ] && continue
        home=$(getent passwd "$candidate" 2>/dev/null | cut -d: -f6 || true)
        if [ -n "$home" ] && [ -d "$home/Zapret_DPI_Manager" ]; then
            echo "$home"
            return 0
        fi
    done
    while IFS=: read -r _name _pw uid _gid _gecos home _shell; do
        if [ "$uid" -ge 1000 ] && [ -n "$home" ] && [ -d "$home/Zapret_DPI_Manager" ]; then
            echo "$home"
            return 0
        fi
    done < /etc/passwd
    return 1
}

CURRENT_HOME="${WARP_HOME:-}"
if [ -z "$CURRENT_HOME" ]; then
    CURRENT_HOME=$(detect_home || true)
fi
[ -n "$CURRENT_HOME" ] || die "Не найден каталог Zapret_DPI_Manager: WARP не настроен."
log "Каталог менеджера: $CURRENT_HOME"

STATE_FILE="${WARP_STATE_FILE:-$CURRENT_HOME/Zapret_DPI_Manager/utils/warp.txt}"
if [ ! -f "$STATE_FILE" ]; then
    log "Файл состояния $STATE_FILE не найден, использую значения по умолчанию."
else
    # Читаем только известные ключи, значение — до конца строки, CR обрезаем.
    while IFS='=' read -r key value || [ -n "$key" ]; do
        value="${value%$'\r'}"
        case "$key" in
            enabled) ;;
            mode) MODE="$value" ;;
            transport) TRANSPORT="$value" ;;
            sni) SNI="$value" ;;
            iface) IFACE="$value" ;;
            mtu) MTU="$value" ;;
            dns) DNS_MODE="$value" ;;
            help_tunnel) HELP_TUNNEL="$value" ;;
            lists) LISTS="$value" ;;
            ''|'#'*) ;;
            *) log "Файл состояния: неизвестный ключ '$key' пропущен." ;;
        esac
    done < "$STATE_FILE"
fi

# --- Проверка значений: критичные (маршрутизация, интерфейс) не угадываем ---
case "$MODE" in
    full | select) ;;
    *) die "warp.txt: недопустимый mode='$MODE' (ожидается full или select)." ;;
esac

if ! [[ "$IFACE" =~ ^[A-Za-z0-9_.-]{1,15}$ ]]; then
    die "warp.txt: недопустимое имя интерфейса '$IFACE'."
fi

if ! [[ "$MTU" =~ ^[0-9]{3,4}$ ]] || [ "$MTU" -lt 576 ] || [ "$MTU" -gt 1500 ]; then
    die "warp.txt: недопустимый MTU '$MTU' (допустимо 576-1500)."
fi

case "$TRANSPORT" in
    auto | quic | http2) ;;
    *) log "warp.txt: недопустимый transport='$TRANSPORT', использую auto."; TRANSPORT="auto" ;;
esac

case "$DNS_MODE" in
    link | keep) ;;
    *) log "warp.txt: недопустимый dns='$DNS_MODE', использую link."; DNS_MODE="link" ;;
esac

case "$HELP_TUNNEL" in
    0 | 1) ;;
    *) log "warp.txt: недопустимый help_tunnel='$HELP_TUNNEL', использую 1."; HELP_TUNNEL="1" ;;
esac

if [ -n "$SNI" ] && ! [[ "$SNI" =~ ^[A-Za-z0-9.*_-]+$ ]]; then
    log "warp.txt: недопустимый SNI '$SNI', использую значение по умолчанию."
    SNI="consumer-masque.cloudflareclient.com"
fi

if [ -n "$LISTS" ] && ! [[ "$LISTS" =~ ^[A-Za-z0-9._,:-]+$ ]]; then
    log "warp.txt: недопустимый список lists='$LISTS', игнорирую."
    LISTS=""
fi

log "Настройки: mode=$MODE transport=$TRANSPORT iface=$IFACE mtu=$MTU dns=$DNS_MODE help_tunnel=$HELP_TUNNEL sni=${SNI:-<по умолчанию>}"

# --- Предусловия ---
[ -x "$USQUE" ] || die "Нет исполняемого $USQUE: установите WARP в приложении (окно WARP)."
[ -f "$ACCOUNT" ] || die "Нет файла аккаунта $ACCOUNT: зарегистрируйте WARP в приложении."
[ -c /dev/net/tun ] || die "Устройство /dev/net/tun недоступно: загрузите модуль tun (modprobe tun)."
command -v ip > /dev/null 2>&1 || die "Не найден ip (пакет iproute2)."

if [ "$MODE" = "select" ]; then
    log "Режим select: селективная маршрутизация появится на следующем этапе, сейчас туннель поднимается без маршрутов."
fi

# --- Настройки для хуков (их читают warp_up.sh и warp_down.sh) ---
CONFIG_FILE="$STATE_DIR/config"
{
    printf 'mode=%s\n' "$MODE"
    printf 'iface=%s\n' "$IFACE"
    printf 'dns=%s\n' "$DNS_MODE"
    printf 'help_tunnel=%s\n' "$HELP_TUNNEL"
    printf 'lists=%s\n' "$LISTS"
} > "$CONFIG_FILE"
chmod 600 "$CONFIG_FILE" 2>/dev/null || true

# --- Останавливаем прошлый запуск, чтобы старт был идемпотентным ---
if [ -f "$STATE_DIR/usque.pid" ] || ip link show "$IFACE" > /dev/null 2>&1; then
    log "Найден прошлый запуск WARP: очищаю перед стартом."
    bash "$WARP_DIR/warp_stop.sh" > /dev/null 2>&1 || true
fi

USQUE_PID=""

stop_usque_quiet() {
    if [ -n "$USQUE_PID" ] && kill -0 "$USQUE_PID" 2>/dev/null; then
        kill -TERM "$USQUE_PID" 2>/dev/null || true
        local i
        for i in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$USQUE_PID" 2>/dev/null || break
            sleep 0.5
        done
        kill -KILL "$USQUE_PID" 2>/dev/null || true
    fi
    USQUE_PID=""
    rm -f "$STATE_DIR/usque.pid"
    # Маршруты и DNS снимаем тем же кодом, что и хук --on-disconnect.
    USQUE_IFACE="$IFACE" bash "$WARP_DIR/warp_down.sh" > /dev/null 2>&1 || true
}

start_usque() {
    : > "$STATE_DIR/transport"
    rm -f "$STATE_DIR/connected" "$STATE_DIR/probe_failed" "$STATE_DIR/last_probe_failed"
    WARP_ACTIVE_TRANSPORT="$1"
    export WARP_ACTIVE_TRANSPORT
    shift
    {
        echo "=== $(date -Is) запуск usque: ${*:-} ==="
    } >> "$LOG_FILE"
    "$USQUE" nativetun -c "$ACCOUNT" -n "$IFACE" -m "$MTU" \
        ${SNI:+-s "$SNI"} \
        --always-reconnect \
        --on-connect "$WARP_DIR/warp_up.sh" \
        --on-disconnect "$WARP_DIR/warp_down.sh" \
        "$@" >> "$LOG_FILE" 2>&1 &
    USQUE_PID=$!
    echo "$USQUE_PID" > "$STATE_DIR/usque.pid"
}

wait_connected() {
    # Опрос идёт раз в 0.5 с, поэтому тиков вдвое больше, чем секунд:
    # иначе фактическое ожидание было вдвое короче CONNECT_TIMEOUT.
    local max_ticks=$((CONNECT_TIMEOUT * 2))
    local waited=0
    while [ "$waited" -lt "$max_ticks" ]; do
        if [ -f "$STATE_DIR/connected" ]; then
            return 0
        fi
        if [ -f "$STATE_DIR/probe_failed" ]; then
            err "Данные через туннель не проходят: маршруты сняты автоматически (журнал: journalctl -t warp)."
            return 1
        fi
        if ! kill -0 "$USQUE_PID" 2>/dev/null; then
            err "usque завершился, не подключившись (см. $LOG_FILE)."
            return 1
        fi
        sleep 0.5
        waited=$((waited + 1))
    done
    return 1
}

run_attempt() {
    local transport="$1"
    local title="$2"
    shift 2
    log "Запуск WARP: $title (интерфейс $IFACE, MTU $MTU, SNI ${SNI:-по умолчанию})."
    start_usque "$transport" "$@"
    if wait_connected; then
        log "WARP подключён: $title."
        return 0
    fi
    # Причину неудачной самопроверки сохраняем как «последнюю»: очистка её
    # удаляет, а приложению нужно объяснить пользователю, почему не вышло.
    if [ -f "$STATE_DIR/probe_failed" ]; then
        mv -f "$STATE_DIR/probe_failed" "$STATE_DIR/last_probe_failed" 2>/dev/null || true
        err "WARP подключился ($title), но данные через туннель не проходят."
    else
        err "WARP не подключился за ${CONNECT_TIMEOUT}s: $title."
    fi
    stop_usque_quiet
    return 1
}

case "$TRANSPORT" in
    quic)
        run_attempt "quic" "QUIC/UDP 443" || die "WARP не удалось поднять через QUIC. Журнал: journalctl -t warp -t warp-usque; +$LOG_FILE"
        ;;
    http2)
        run_attempt "http2" "HTTP/2/TCP 443" --http2 || die "WARP не удалось поднять через HTTP/2. Журнал: journalctl -t warp -t warp-usque; +$LOG_FILE"
        ;;
    auto)
        if ! run_attempt "quic" "QUIC/UDP 443"; then
            log "Транспорт QUIC не сработал, пробую HTTP/2 (TCP 443)."
            run_attempt "http2" "HTTP/2/TCP 443" --http2 || die "WARP не удалось поднять ни через QUIC, ни через HTTP/2. Журнал: journalctl -t warp -t warp-usque; +$LOG_FILE"
        fi
        ;;
esac

exit 0
