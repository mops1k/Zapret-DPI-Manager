#!/bin/bash
# WARP (usque): хук --on-connect — маршруты, DNS и маркер подключения.
#
# usque вызывает этот скрипт, когда туннель поднялся, и передаёт параметры
# через окружение: USQUE_IFACE, USQUE_ENDPOINT, USQUE_EVENT, USQUE_MODE,
# USQUE_IPV, USQUE_TARGET. Скрипт должен быть быстрым: usque ждёт его.
#
# Что делает:
#   1) маршрут до endpoint'а через физический шлюз (иначе после п.2 туннель
#      замкнётся сам на себя: пакеты к endpoint'у уйдут в сам туннель);
#   2) режим full — 0.0.0.0/1 и 128.0.0.0/1 в туннель (перекрывает default,
#      не трогая сам default — так обратный путь остаётся предсказуемым);
#      полный туннель включается ТОЛЬКО если маршрут до endpoint'а встал,
#      иначе сеть осталась бы без выхода;
#   3) DNS: per-link через resolvectl (файл /etc/resolv.conf не переписываем);
#   4) маркер /run/warp/connected — по нему warp_start.sh понимает, что туннель
#      рабочий, и не откатывается на другой транспорт.
set -uo pipefail

# Пути: по умолчанию как в юните; переопределяются окружением для отладки и тестов.
WARP_DIR="${WARP_DIR:-/opt/warp}"
STATE_DIR="${STATE_DIR:-/run/warp}"
ACCOUNT="${ACCOUNT:-$WARP_DIR/account.json}"

log() { logger -t warp -- "$*" 2>/dev/null || true; echo "$*"; }
warn() { logger -t warp -p warning -- "$*" 2>/dev/null || true; echo "$*" >&2; }

[ "$(id -u)" -eq 0 ] || { echo "Please run as root" >&2; exit 1; }

mkdir -p "$STATE_DIR"

MODE="full"
DNS_MODE="link"
# PROBE_OK=0 — самопроверка данных не прошла (маркер подключения не ставим).
PROBE_OK=1
IFACE="${USQUE_IFACE:-}"
if [ -f "$STATE_DIR/config" ]; then
    while IFS='=' read -r key value || [ -n "$key" ]; do
        value="${value%$'\r'}"
        case "$key" in
            mode) MODE="$value" ;;
            dns) DNS_MODE="$value" ;;
            iface) [ -n "$IFACE" ] || IFACE="$value" ;;
        esac
    done < "$STATE_DIR/config"
fi
[ -n "$IFACE" ] || IFACE="warp0"

TRANSPORT="${WARP_ACTIVE_TRANSPORT:-}"
[ -n "$TRANSPORT" ] || TRANSPORT=$(cat "$STATE_DIR/transport" 2>/dev/null || true)
[ -n "$TRANSPORT" ] || TRANSPORT="quic"

# usque отдаёт endpoint как host:port (IPv6 — в квадратных скобках): для `ip`
# нужен голый адрес, иначе маршрут не создаётся (проверено на устройстве).
normalize_endpoint() {
    local value="$1"
    if [[ "$value" =~ ^\[(.+)\]:[0-9]+$ ]]; then
        value="${BASH_REMATCH[1]}"
    elif [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+$ ]]; then
        value="${value%:*}"
    fi
    printf '%s' "$value"
}

is_ip_like() {
    local value="$1"
    if [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        return 0
    fi
    if [[ "$value" == *:* && "$value" =~ ^[0-9a-fA-F:]+$ ]]; then
        return 0
    fi
    return 1
}

# Самопроверка данных через туннель: маршруты могут быть верными, а трафик
# не ходить (провайдер режет MASQUE). Запрос идёт по IP без DNS: 1.1.1.1
# попадает в туннель благодаря маршрутам 0.0.0.0/1 и 128.0.0.0/1.
probe_through_tunnel() {
    if ! command -v curl > /dev/null 2>&1; then
        warn "curl не найден: самопроверка данных через туннель пропущена."
        return 0
    fi
    local attempt code
    for attempt in 1 2; do
        code=$(curl -4 -sS -m 6 -o /dev/null -w '%{http_code}' \
            --resolve one.one.one.one:443:1.1.1.1 \
            https://one.one.one.one/cdn-cgi/trace 2>/dev/null || true)
        if [ "$code" = "200" ]; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# Откат полного туннеля: маршруты и DNS возвращаются в исходное состояние.
rollback_full_tunnel() {
    ip route del 0.0.0.0/1 dev "$IFACE" > /dev/null 2>&1 || true
    ip route del 128.0.0.0/1 dev "$IFACE" > /dev/null 2>&1 || true
    if command -v resolvectl > /dev/null 2>&1; then
        resolvectl revert "$IFACE" > /dev/null 2>&1 || true
        resolvectl flush-caches > /dev/null 2>&1 || true
    fi
}

# IP endpoint'а: сначала из окружения usque, иначе из аккаунта.
json_value() {
    # json_value <ключ> — простое чтение строкового поля из account.json
    grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$ACCOUNT" 2>/dev/null |
        head -n 1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/'
}

ENDPOINT="$(normalize_endpoint "${USQUE_ENDPOINT:-}")"
if [ -z "$ENDPOINT" ]; then
    if [ "$TRANSPORT" = "http2" ]; then
        ENDPOINT=$(json_value endpoint_h2_v4)
    else
        ENDPOINT=$(json_value endpoint_v4)
    fi
    [ -n "$ENDPOINT" ] || ENDPOINT=$(json_value endpoint_v6)
fi

if [ -n "$ENDPOINT" ] && is_ip_like "$ENDPOINT"; then
    echo "$ENDPOINT" > "$STATE_DIR/endpoint" 2>/dev/null || true
else
    warn "Endpoint '$ENDPOINT' не похож на IP-адрес: полный туннель включать нельзя."
    ENDPOINT=""
fi
echo "$TRANSPORT" > "$STATE_DIR/transport" 2>/dev/null || true

if [ "$MODE" = "full" ]; then
    ENDPOINT_OK=0
    # PROBE_OK=0 означает: маршруты полного туннеля сняты, маркер не ставим.
    PROBE_OK=1
    GW=""
    DEV=""
    ROUTE_TOOL="ip route"

    if [ -z "$ENDPOINT" ]; then
        warn "Не удалось определить IP endpoint'а: полный туннель не включаю, чтобы не потерять связь."
    else
        if [[ "$ENDPOINT" == *:* ]]; then
            ROUTE_TOOL="ip -6 route"
            DEFAULT_ROUTE=$(ip -6 route show default 2>/dev/null | head -n 1 || true)
        else
            ROUTE_TOOL="ip route"
            DEFAULT_ROUTE=$(ip -4 route show default 2>/dev/null | head -n 1 || true)
        fi
        read -r GW DEV < <(
            printf '%s\n' "$DEFAULT_ROUTE" |
                awk '{for (i = 1; i <= NF; i++) { if ($i == "via") gw = $(i + 1); if ($i == "dev") dv = $(i + 1) } } END { print gw, dv }'
        )

        if $ROUTE_TOOL show "$ENDPOINT/32" 2>/dev/null | grep -q .; then
            # Маршрут уже есть (например, свой /32 у провайдера) — не наш.
            rm -f "$STATE_DIR/route_owned"
            ENDPOINT_OK=1
            log "Маршрут до endpoint'а $ENDPOINT уже настроен системой."
        elif [ -z "$DEV" ]; then
            warn "Не найден физический маршрут по умолчанию: полный туннель не включаю."
        elif $ROUTE_TOOL replace "$ENDPOINT/32" ${GW:+via "$GW"} dev "$DEV" 2>/dev/null; then
            : > "$STATE_DIR/route_owned"
            ENDPOINT_OK=1
            log "Маршрут до endpoint'а: $ENDPOINT/32 via ${GW:-<без шлюза>} dev $DEV."
        else
            warn "Не удалось добавить маршрут до endpoint'а $ENDPOINT: полный туннель не включаю."
        fi
    fi

    if [ "$ENDPOINT_OK" = "1" ]; then
        ip route replace 0.0.0.0/1 dev "$IFACE" ||
            warn "Не удалось направить 0.0.0.0/1 в $IFACE."
        ip route replace 128.0.0.0/1 dev "$IFACE" ||
            warn "Не удалось направить 128.0.0.0/1 в $IFACE."
        log "Весь IPv4-трафик направлен в $IFACE (endpoint $ENDPOINT)."
        if probe_through_tunnel; then
            log "Самопроверка: данные через $IFACE проходят."
        else
            warn "Самопроверка не прошла: данные через туннель не ходят — снимаю полный туннель."
            PROBE_OK=0
            rollback_full_tunnel
            date -Is > "$STATE_DIR/probe_failed" 2>/dev/null || : > "$STATE_DIR/probe_failed"
        fi
    else
        warn "Полный туннель не включён (нет маршрута до endpoint'а): прямой трафик не тронут."
    fi
elif [ "$MODE" = "select" ]; then
    log "Режим select: маршруты по спискам появятся на следующем этапе, туннель поднят без маршрутов."
fi

# --- DNS туннельного интерфейса (per-link, без правки /etc/resolv.conf) ---
if [ "$PROBE_OK" = "1" ] && [ "$DNS_MODE" = "link" ]; then
    if command -v resolvectl > /dev/null 2>&1 && resolvectl status > /dev/null 2>&1; then
        if resolvectl dns "$IFACE" 1.1.1.1 1.0.0.1 > /dev/null 2>&1 &&
            resolvectl domain "$IFACE" '~.' > /dev/null 2>&1; then
            log "DNS интерфейса $IFACE: 1.1.1.1, 1.0.0.1 (все домены через туннель)."
        else
            warn "resolvectl не применил DNS для $IFACE: DNS остаётся системным."
        fi
    else
        warn "resolvectl недоступен (systemd-resolved не запущен): DNS остаётся системным, /etc/resolv.conf не меняю."
    fi
fi

if [ "$PROBE_OK" = "1" ]; then
    date -Is > "$STATE_DIR/connected" 2>/dev/null || : > "$STATE_DIR/connected"
    log "WARP подключён: $IFACE, транспорт $TRANSPORT, режим $MODE."
else
    # Маркер не ставим: warp_start.sh должен считать попытку неудачной и
    # (в режиме «авто») попробовать другой транспорт.
    log "WARP подключился, но данные не проходят: полный туннель снят, маркер не поставлен."
fi
exit 0
