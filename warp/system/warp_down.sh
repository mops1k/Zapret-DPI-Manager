#!/bin/bash
# WARP (usque): снятие маршрутов, DNS и маркеров — идемпотентно.
#
# Вызывается в двух случаях:
#   1) usque как хук --on-disconnect — туннель отвалился (возможно, временно,
#      usque переподключится и вызовет --on-connect снова);
#   2) warp_stop.sh — остановка службы (тогда дополнительно WARP_STOP=1,
#      и скрипт удаляет оставшиеся интерфейсы туннеля).
#
# Скрипт обязан быть безопасным при повторном вызове: все шаги «не падают»
# на отсутствующих объектах.
set -uo pipefail

# Пути: по умолчанию как в юните; переопределяются окружением для отладки и тестов.
STATE_DIR="${STATE_DIR:-/run/warp}"

log() { logger -t warp -- "$*" 2>/dev/null || true; echo "$*"; }
warn() { logger -t warp -p warning -- "$*" 2>/dev/null || true; echo "$*" >&2; }

[ "$(id -u)" -eq 0 ] || { echo "Please run as root" >&2; exit 1; }

IFACE="${USQUE_IFACE:-}"
if [ -z "$IFACE" ] && [ -f "$STATE_DIR/config" ]; then
    IFACE=$(sed -n 's/^iface=//p' "$STATE_DIR/config" | head -n 1 || true)
fi
[ -n "$IFACE" ] || IFACE="warp0"

# Интерфейсы для очистки: настроенный и любые оставшиеся warp* (аварийный случай).
IFACES=("$IFACE")
if [ "${WARP_STOP:-0}" = "1" ]; then
    while read -r name; do
        [ -n "$name" ] || continue
        [ "$name" = "$IFACE" ] && continue
        IFACES+=("$name")
    done < <(ip -o link show 2>/dev/null | sed -n 's/^[0-9]*: \([^:@]*\)[:@].*/\1/p' | grep -E '^warp[0-9]*$' || true)
fi

for name in "${IFACES[@]}"; do
    if command -v resolvectl > /dev/null 2>&1; then
        resolvectl revert "$name" > /dev/null 2>&1 || true
    fi

    ip route del 0.0.0.0/1 dev "$name" > /dev/null 2>&1 || true
    ip route del 128.0.0.0/1 dev "$name" > /dev/null 2>&1 || true

    if [ "${WARP_STOP:-0}" = "1" ] && ip link show "$name" > /dev/null 2>&1; then
        ip link del "$name" > /dev/null 2>&1 || warn "Не удалось удалить интерфейс $name."
        log "Интерфейс $name удалён."
    fi
done

# Селективный режим: правила таблицы 100 снимаем по списку из /run/warp/iprules,
# а если список потерян — по нашим диапазонам приоритетов (аварийный случай).
SELECT_TABLE=100
PRIO_MIN=10000
PRIO_MAX=12999
RULES_FILE="$STATE_DIR/iprules"
removed_rules=0
if [ -f "$RULES_FILE" ]; then
    while read -r family prio; do
        [ -n "${prio:-}" ] || continue
        if [ "$family" = "6" ]; then
            ip -6 rule del priority "$prio" > /dev/null 2>&1 && removed_rules=$((removed_rules + 1))
        else
            ip rule del priority "$prio" > /dev/null 2>&1 && removed_rules=$((removed_rules + 1))
        fi
    done < "$RULES_FILE"
    rm -f "$RULES_FILE"
fi
for family_flag in "" "-6"; do
    while read -r prio; do
        [ -n "$prio" ] || continue
        if [ "$prio" -ge "$PRIO_MIN" ] && [ "$prio" -le "$PRIO_MAX" ]; then
            ip $family_flag rule del priority "$prio" > /dev/null 2>&1 &&
                removed_rules=$((removed_rules + 1))
        fi
    done < <(ip $family_flag rule show 2>/dev/null | sed -n 's/^\([0-9]*\):.*/\1/p' || true)
done
ip route del default dev "$IFACE" table "$SELECT_TABLE" > /dev/null 2>&1 || true
ip -6 route del default dev "$IFACE" table "$SELECT_TABLE" > /dev/null 2>&1 || true
if [ "$removed_rules" -gt 0 ]; then
    log "Селективный режим: снято правил маршрутизации — $removed_rules."
fi

# Маршрут до endpoint'а убираем только если его добавили мы (см. warp_up.sh).
if [ -f "$STATE_DIR/route_owned" ]; then
    ENDPOINT="${USQUE_ENDPOINT:-}"
    if [ -z "$ENDPOINT" ] && [ -f "$STATE_DIR/endpoint" ]; then
        ENDPOINT=$(head -n 1 "$STATE_DIR/endpoint" 2>/dev/null || true)
    fi
    if [ -n "$ENDPOINT" ]; then
        ip route del "$ENDPOINT/32" > /dev/null 2>&1 || true
    fi
    rm -f "$STATE_DIR/route_owned"
fi

if command -v resolvectl > /dev/null 2>&1; then
    resolvectl flush-caches > /dev/null 2>&1 || true
fi

rm -f "$STATE_DIR/connected" "$STATE_DIR/probe_failed" "$STATE_DIR/endpoint" "$STATE_DIR/transport" "$STATE_DIR/usque.pid"

log "WARP остановлен: маршруты, DNS и маркеры сняты."
exit 0
