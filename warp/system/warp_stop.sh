#!/bin/bash
# WARP (usque): остановка туннеля (ExecStop юнита warp.service).
#
# Скрипт идемпотентен: его можно вызывать, когда туннель и так не поднят.
set -uo pipefail

# Пути: по умолчанию как в юните; переопределяются окружением для отладки и тестов.
WARP_DIR="${WARP_DIR:-/opt/warp}"
STATE_DIR="${STATE_DIR:-/run/warp}"

log() { logger -t warp -- "$*" 2>/dev/null || true; echo "$*"; }
warn() { logger -t warp -p warning -- "$*" 2>/dev/null || true; echo "$*" >&2; }

[ "$(id -u)" -eq 0 ] || { echo "Please run as root" >&2; exit 1; }

IFACE=""
if [ -f "$STATE_DIR/config" ]; then
    IFACE=$(sed -n 's/^iface=//p' "$STATE_DIR/config" | head -n 1 || true)
fi
[ -n "$IFACE" ] || IFACE="warp0"

STOPPED=0

if [ -f "$STATE_DIR/usque.pid" ]; then
    PID=$(head -n 1 "$STATE_DIR/usque.pid" 2>/dev/null || true)
    if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
        log "Останавливаю usque (PID $PID)."
        kill -TERM "$PID" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$PID" 2>/dev/null || break
            sleep 0.5
        done
        if kill -0 "$PID" 2>/dev/null; then
            warn "usque не завершился по TERM, отправляю KILL."
            kill -KILL "$PID" 2>/dev/null || true
        fi
        STOPPED=1
    fi
fi

# Страховка: процессы usque могли остаться без pid-файла.
# Путь берём из WARP_DIR: иначе при нестандартном каталоге страховка молча ничего не находит.
USQUE_PATTERN="^$WARP_DIR/usque nativetun"
if pgrep -f "$USQUE_PATTERN" > /dev/null 2>&1; then
    pkill -TERM -f "$USQUE_PATTERN" 2>/dev/null || true
    sleep 1
    pkill -KILL -f "$USQUE_PATTERN" 2>/dev/null || true
    STOPPED=1
fi

if [ "$STOPPED" -eq 0 ]; then
    log "Процесс usque не найден (туннель уже остановлен)."
fi

# Снимаем маршруты, DNS, маркеры и оставшиеся интерфейсы туннеля.
WARP_STOP=1 USQUE_IFACE="$IFACE" bash "$WARP_DIR/warp_down.sh" || warn "Очистка после остановки WARP завершилась с ошибкой."

exit 0
