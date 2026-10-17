#!/bin/bash
# WARP (usque): хук --on-connect — маршруты, DNS и маркер подключения.
#
# usque вызывает этот скрипт, когда туннель поднялся, и передаёт параметры
# через окружение: USQUE_IFACE, USQUE_ENDPOINT, USQUE_EVENT, USQUE_MODE,
# USQUE_IPV, USQUE_TARGET. Скрипт должен быть быстрым: usque ждёт его.
#
# Что делает:
#   1) маршрут до endpoint'а через физический шлюз (иначе трафик к endpoint'у
#      уйдёт в сам туннель и он замкнётся сам на себя);
#   2) режим full — 0.0.0.0/1 и 128.0.0.0/1 в туннель (перекрывает default,
#      не трогая сам default); режим select — таблица 100 + правила `ip rule`
#      на выбранные диапазоны, исключения и (по желанию) uidrange;
#      маршруты включаются ТОЛЬКО после успешной самопроверки, иначе сеть
#      осталась бы без выхода;
#   3) DNS: per-link через resolvectl (файл /etc/resolv.conf не переписываем);
#      в селективном режиме системный DNS не трогаем — маршрутизация идёт по
#      IP-диапазонам, а не по именам;
#   4) маркер /run/warp/connected — по нему warp_start.sh понимает, что туннель
#      рабочий, и не откатывается на другой транспорт.
set -uo pipefail

# Пути: по умолчанию как в юните; переопределяются окружением для отладки и тестов.
WARP_DIR="${WARP_DIR:-/opt/warp}"
STATE_DIR="${STATE_DIR:-/run/warp}"
ACCOUNT="${ACCOUNT:-$WARP_DIR/account.json}"

# Селективный режим: своя таблица маршрутизации и диапазоны приоритетов правил
# (исключения — раньше остальных, то есть с меньшим номером приоритета).
SELECT_TABLE=100
PRIO_EXCLUDE_BASE=10000
PRIO_LIST_BASE=11000
PRIO_UID_BASE=12000
SELECT_MAX_RULES=500

log() { logger -t warp -- "$*" 2>/dev/null || true; echo "$*"; }
warn() { logger -t warp -p warning -- "$*" 2>/dev/null || true; echo "$*" >&2; }

[ "$(id -u)" -eq 0 ] || { echo "Please run as root" >&2; exit 1; }

mkdir -p "$STATE_DIR"

MODE="full"
DNS_MODE="link"
# PROBE_OK=0 — самопроверка данных не прошла (маркер подключения не ставим).
PROBE_OK=1
IFACE="${USQUE_IFACE:-}"
SELECT_LISTS=""
SELECT_EXCLUDE=""
SELECT_UIDS=""
LISTS_DIR=""
if [ -f "$STATE_DIR/config" ]; then
    while IFS='=' read -r key value || [ -n "$key" ]; do
        value="${value%$'\r'}"
        case "$key" in
            mode) MODE="$value" ;;
            dns) DNS_MODE="$value" ;;
            iface) [ -n "$IFACE" ] || IFACE="$value" ;;
            lists) SELECT_LISTS="$value" ;;
            exclude) SELECT_EXCLUDE="$value" ;;
            uids) SELECT_UIDS="$value" ;;
            lists_dir) LISTS_DIR="$value" ;;
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

# IP или подсеть в нотации CIDR (для селективного режима).
is_cidr_like() {
    local value="$1"
    local addr prefix
    if [[ "$value" == */* ]]; then
        addr="${value%%/*}"
        prefix="${value##*/}"
        [[ "$prefix" =~ ^[0-9]{1,3}$ ]] || return 1
        is_ip_like "$addr"
        return $?
    fi
    is_ip_like "$value"
}

cidr_family() {
    # 6 для IPv6 (в записи есть двоеточия), иначе 4
    if [[ "$1" == *:* ]]; then
        echo 6
    else
        echo 4
    fi
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

mark_probe_failed() {
    date -Is > "$STATE_DIR/probe_failed" 2>/dev/null || : > "$STATE_DIR/probe_failed"
    PROBE_OK=0
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

# --- Селективный режим ----------------------------------------------------

# collect_cidrs "<имена списков через запятую>" — печатает адреса и подсети из
# files/lists/<имя>.txt и <имя>_user.txt без повторов и комментариев.
collect_cidrs() {
    local names="$1"
    local name path line seen=""
    local IFS=','
    for name in $names; do
        [ -n "$name" ] || continue
        for path in "$LISTS_DIR/$name.txt" "$LISTS_DIR/${name}_user.txt"; do
            [ -f "$path" ] || continue
            while IFS= read -r line || [ -n "$line" ]; do
                line="${line%%#*}"
                line="$(printf '%s' "$line" | tr -d ' \t\r')"
                [ -n "$line" ] || continue
                is_cidr_like "$line" || continue
                case ",$seen," in
                    *",$line,"*) continue ;;
                esac
                seen="$seen,$line"
                printf '%s\n' "$line"
            done < "$path"
        done
    done
}

add_select_rule() {
    # add_select_rule <family> <priority> <аргументы ip rule...>
    local family="$1"
    local prio="$2"
    shift 2
    local family_flag=""
    [ "$family" = "6" ] && family_flag="-6"
    local out=""
    if ! out=$(ip $family_flag rule add "$@" priority "$prio" 2>&1); then
        warn "Не удалось добавить правило маршрутизации ($* priority $prio): $out"
        return 1
    fi
    printf '%s %s\n' "$family" "$prio" >> "$RULES_FILE"
    return 0
}

rollback_select_routing() {
    if [ -f "$RULES_FILE" ]; then
        local family prio
        while read -r family prio; do
            [ -n "${prio:-}" ] || continue
            if [ "$family" = "6" ]; then
                ip -6 rule del priority "$prio" > /dev/null 2>&1 || true
            else
                ip rule del priority "$prio" > /dev/null 2>&1 || true
            fi
        done < "$RULES_FILE"
        rm -f "$RULES_FILE"
    fi
    ip route del default dev "$IFACE" table "$SELECT_TABLE" > /dev/null 2>&1 || true
}

# --- Маршрут до endpoint'а (нужен в обоих режимах) -------------------------
ensure_endpoint_route() {
    ENDPOINT_OK=0
    if [ -z "$ENDPOINT" ]; then
        warn "Не удалось определить IP endpoint'а: туннель не настраиваю, чтобы не потерять связь."
        return 1
    fi
    local route_tool="ip route"
    local default_route=""
    if [[ "$ENDPOINT" == *:* ]]; then
        route_tool="ip -6 route"
        default_route=$(ip -6 route show default 2>/dev/null | head -n 1 || true)
    else
        route_tool="ip route"
        default_route=$(ip -4 route show default 2>/dev/null | head -n 1 || true)
    fi
    local gw="" dev=""
    read -r gw dev < <(
        printf '%s\n' "$default_route" |
            awk '{for (i = 1; i <= NF; i++) { if ($i == "via") g = $(i + 1); if ($i == "dev") d = $(i + 1) } } END { print g, d }'
    )

    if $route_tool show "$ENDPOINT/32" 2>/dev/null | grep -q .; then
        # Маршрут уже есть (например, свой /32 у провайдера) — не наш.
        rm -f "$STATE_DIR/route_owned"
        ENDPOINT_OK=1
        log "Маршрут до endpoint'а $ENDPOINT уже настроен системой."
    elif [ -z "$dev" ]; then
        warn "Не найден физический маршрут по умолчанию: туннель не настраиваю."
    elif $route_tool replace "$ENDPOINT/32" ${gw:+via "$gw"} dev "$dev" 2>/dev/null; then
        : > "$STATE_DIR/route_owned"
        ENDPOINT_OK=1
        log "Маршрут до endpoint'а: $ENDPOINT/32 via ${gw:-<без шлюза>} dev $dev."
    else
        warn "Не удалось добавить маршрут до endpoint'а $ENDPOINT: туннель не настраиваю."
    fi
    [ "$ENDPOINT_OK" = "1" ]
}

# --- IP endpoint'а: сначала из окружения usque, иначе из аккаунта ---
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
    warn "Endpoint '$ENDPOINT' не похож на IP-адрес: туннель настраивать нельзя."
    ENDPOINT=""
fi
echo "$TRANSPORT" > "$STATE_DIR/transport" 2>/dev/null || true

RULES_FILE="$STATE_DIR/iprules"

if [ "$MODE" = "full" ]; then
    if ensure_endpoint_route; then
        ip route replace 0.0.0.0/1 dev "$IFACE" ||
            warn "Не удалось направить 0.0.0.0/1 в $IFACE."
        ip route replace 128.0.0.0/1 dev "$IFACE" ||
            warn "Не удалось направить 128.0.0.0/1 в $IFACE."
        log "Весь IPv4-трафик направлен в $IFACE (endpoint $ENDPOINT)."
        if probe_through_tunnel; then
            log "Самопроверка: данные через $IFACE проходят."
        else
            warn "Самопроверка не прошла: данные через туннель не ходят — снимаю полный туннель."
            rollback_full_tunnel
            mark_probe_failed
        fi
    fi
elif [ "$MODE" = "select" ]; then
    SELECT_ROUTED=0
    SELECT_CHECK_CIDR=""
    rm -f "$RULES_FILE"
    : > "$RULES_FILE"
    if [ -z "$LISTS_DIR" ] || [ ! -d "$LISTS_DIR" ]; then
        warn "Каталог списков ($LISTS_DIR) недоступен: селективный режим не настроен."
    elif [ -z "$SELECT_LISTS" ]; then
        warn "Не выбрано ни одного списка: селективный режим не настроен."
    elif ensure_endpoint_route; then
        # Таблица 100: сам по себе default в ней никого не заворачивает —
        # трафик попадает туда только по правилам ниже.
        if ip route replace default dev "$IFACE" table "$SELECT_TABLE" 2>/dev/null; then
            prio="$PRIO_EXCLUDE_BASE"
            while IFS= read -r cidr; do
                [ -n "$cidr" ] || continue
                add_select_rule "$(cidr_family "$cidr")" "$prio" to "$cidr" lookup main || true
                prio=$((prio + 1))
            done < <(collect_cidrs "$SELECT_EXCLUDE")
            if [ "$prio" -gt "$PRIO_EXCLUDE_BASE" ]; then
                log "Исключения из туннеля: $((prio - PRIO_EXCLUDE_BASE)) диапазон(ов)."
            fi

            prio="$PRIO_LIST_BASE"
            while IFS= read -r cidr; do
                [ -n "$cidr" ] || continue
                if [ "$prio" -ge $((PRIO_LIST_BASE + SELECT_MAX_RULES)) ]; then
                    warn "Слишком много диапазонов для туннеля (лимит $SELECT_MAX_RULES): остальные пропущены."
                    break
                fi
                if add_select_rule "$(cidr_family "$cidr")" "$prio" to "$cidr" lookup "$SELECT_TABLE"; then
                    SELECT_ROUTED=$((SELECT_ROUTED + 1))
                    [ -n "$SELECT_CHECK_CIDR" ] || SELECT_CHECK_CIDR="$cidr"
                fi
                prio=$((prio + 1))
            done < <(collect_cidrs "$SELECT_LISTS")

            prio="$PRIO_UID_BASE"
            for uid in ${SELECT_UIDS//,/ }; do
                [ -n "$uid" ] || continue
                case "$uid" in
                    *[!0-9-]*) warn "Недопустимый uidrange '$uid' пропущен."; continue ;;
                esac
                if add_select_rule 4 "$prio" uidrange "$uid" lookup "$SELECT_TABLE"; then
                    SELECT_ROUTED=$((SELECT_ROUTED + 1))
                fi
                prio=$((prio + 1))
            done
        else
            warn "Не удалось создать таблицу $SELECT_TABLE: селективный режим не настроен."
        fi
    fi

    if [ "$SELECT_ROUTED" -gt 0 ] && [ -n "$SELECT_CHECK_CIDR" ]; then
        check_addr="${SELECT_CHECK_CIDR%%/*}"
        if [[ "$check_addr" == *:* ]]; then
            check_route=$(ip -6 route get "$check_addr" 2>/dev/null | head -n 1 || true)
        else
            check_route=$(ip route get "$check_addr" 2>/dev/null | head -n 1 || true)
        fi
        if printf '%s' "$check_route" | grep -q " dev $IFACE"; then
            log "Селективный режим: $SELECT_ROUTED правил(о), $SELECT_CHECK_CIDR идёт через $IFACE."
        else
            warn "Самопроверка селективного режима не прошла: $SELECT_CHECK_CIDR не уходит через $IFACE — снимаю правила."
            rollback_select_routing
            mark_probe_failed
        fi
    elif [ "$SELECT_ROUTED" -eq 0 ]; then
        warn "Селективный режим: ни одного диапазона не завернуто — правила снимаю."
        rollback_select_routing
        mark_probe_failed
    fi
fi

# --- DNS туннельного интерфейса (per-link, без правки /etc/resolv.conf) ---
if [ "$PROBE_OK" = "1" ] && [ "$MODE" = "full" ] && [ "$DNS_MODE" = "link" ]; then
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
elif [ "$MODE" = "select" ] && [ "$PROBE_OK" = "1" ]; then
    log "Селективный режим: DNS системный (маршрутизация идёт по IP-диапазонам)."
fi

if [ "$PROBE_OK" = "1" ]; then
    date -Is > "$STATE_DIR/connected" 2>/dev/null || : > "$STATE_DIR/connected"
    log "WARP подключён: $IFACE, транспорт $TRANSPORT, режим $MODE."
else
    # Маркер не ставим: warp_start.sh должен считать попытку неудачной и
    # (в режиме «авто») попробовать другой транспорт.
    log "WARP подключился, но трафик через туннель не пошёл: маршруты сняты, маркер не поставлен."
fi
exit 0
