#!/bin/bash
# Заглушка usque для стенда WARP (играет роль /opt/warp/usque).
#
# Разбирает аргументы `nativetun -c -n -m -s --http2 --on-connect --on-disconnect`,
# пишет командную строку в журнал стенда, затем по сценарию:
#   USQUE_FAIL_ALWAYS=1     — хук не вызывается никогда, через 2 с выход 1;
#   USQUE_FAIL_QUIC=1       — без --http2: хук не вызывается, через 2 с выход 1;
#                             с --http2 — обычное подключение;
#   USQUE_NEVER_CONNECT=1   — хук не вызывается, процесс живёт до сигнала
#                             (проверка таймаута ожидания подключения);
#   иначе                   — «подключается»: создаёт маркер интерфейса,
#                             вызывает --on-connect и остаётся жив;
#                             по TERM вызывает --on-disconnect (как настоящий usque).
#   USQUE_NO_ENDPOINT=1     — не передавать USQUE_ENDPOINT в хук
#                             (проверка чтения endpoint'а из account.json).
J="${WARP_TEST_JOURNAL:-/dev/null}"
IFACES="${WARP_TEST_IFACES:-/dev/null/none}"
jlog() { printf '%s usque: %s\n' "$(date +%H:%M:%S)" "$*" >> "$J"; }

jlog "argv: $*"

IFACE=""
ON_CONNECT=""
ON_DISCONNECT=""
HTTP2=0
while [ $# -gt 0 ]; do
    case "$1" in
        -c) shift ;;
        -n) shift; IFACE="${1:-}" ;;
        -m) shift ;;
        -s) shift ;;
        --http2) HTTP2=1 ;;
        --on-connect) shift; ON_CONNECT="${1:-}" ;;
        --on-disconnect) shift; ON_DISCONNECT="${1:-}" ;;
        *) ;;
    esac
    shift
done

if [ "${USQUE_FAIL_ALWAYS:-0}" = "1" ]; then
    jlog "сценарий FAIL_ALWAYS: хук не вызываю, через 2 с выход 1"
    sleep 2
    exit 1
fi

if [ "${USQUE_FAIL_QUIC:-0}" = "1" ] && [ "$HTTP2" -eq 0 ]; then
    jlog "сценарий FAIL_QUIC: в аргументах нет --http2, хук не вызываю, через 2 с выход 1"
    sleep 2
    exit 1
fi

if [ "${USQUE_NEVER_CONNECT:-0}" = "1" ]; then
    jlog "сценарий NEVER_CONNECT: подключения нет, жду сигнала"
    trap 'jlog "TERM (до подключения)"; exit 0' TERM INT
    while :; do sleep 0.2; done
fi

mkdir -p "$IFACES" 2>/dev/null || true
: > "$IFACES/$IFACE" 2>/dev/null || true
if [ "$HTTP2" -eq 1 ]; then
    EP="162.159.198.2"
else
    EP="162.159.193.10"
fi
jlog "подключился: iface=$IFACE endpoint=$EP http2=$HTTP2 (маркер интерфейса создан)"

export USQUE_IFACE="$IFACE" USQUE_EVENT=connect USQUE_MODE=nativetun USQUE_IPV=4
if [ "${USQUE_NO_ENDPOINT:-0}" = "1" ]; then
    jlog "USQUE_ENDPOINT не передаю: хук должен взять endpoint из account.json"
else
    export USQUE_ENDPOINT="$EP" USQUE_TARGET="$EP"
fi

if [ -n "$ON_CONNECT" ]; then
    jlog "вызываю --on-connect $ON_CONNECT"
    "$ON_CONNECT" >> "$J" 2>&1
    jlog "хук --on-connect завершился с кодом $?"
fi

on_term() {
    jlog "TERM: остановка usque"
    if [ -n "$ON_DISCONNECT" ]; then
        jlog "вызываю --on-disconnect $ON_DISCONNECT"
        "$ON_DISCONNECT" >> "$J" 2>&1
        jlog "хук --on-disconnect завершился с кодом $?"
    fi
    exit 0
}
trap on_term TERM INT
while :; do sleep 0.2; done
