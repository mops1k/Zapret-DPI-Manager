#!/bin/bash
# Стенд для проверки warp/system/*.sh: без root, без /dev/net/tun, без сети.
# Использование: source stand.sh, затем stand_setup / default_warp_txt / run_start ...
# Пути берутся из окружения (шаг 1), поэтому настоящие скрипты запускаются как есть.

STAND="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$STAND/../.." && pwd)"
BIN="$STAND/bin"
FAKE="$STAND/fake-warp"
STDHOME="$STAND/home"
STATE="$STAND/state"
JOURNAL="$STAND/journal.log"
IFACES="$STAND/ifaces"
ROUTES="$STAND/routes"
IPRULES="$STAND/iprules"
USQUE_LOG="$STAND/warp-usque.log"
# Списки для селективного режима живут в домашнем каталоге стенда.
LISTS="$STDHOME/Zapret_DPI_Manager/files/lists"
WARPTXT="$STDHOME/Zapret_DPI_Manager/utils/warp.txt"

setup_tun() {
    # Настоящего /dev/net/tun в стенде нет и root'а тоже: подставляем симлинк на
    # /dev/null, чтобы проверка `[ -c /dev/net/tun ]` в скрипте прошла.
    # Сам TUN никто не открывает: usque — заглушка, устройство не используется.
    if [ ! -c /dev/net/tun ]; then
        mkdir -p /dev/net 2>/dev/null || true
        ln -sf /dev/null /dev/net/tun 2>/dev/null || true
    fi
}

stand_setup() {
    setup_tun
    rm -rf "$STATE" "$IFACES" "$ROUTES" "$IPRULES" "$JOURNAL" "$USQUE_LOG" "$USQUE_LOG.1"
    mkdir -p "$STATE" "$IFACES" "$STDHOME/Zapret_DPI_Manager/utils" "$FAKE" "$LISTS"
    : > "$JOURNAL"
    : > "$ROUTES"
    : > "$IPRULES"
    # Списки для селективного режима: маленькие, но с IPv4, IPv6 и исключением.
    printf '%s\n' '# стенд' '203.0.113.0/24' '2001:db8:1::/48' > "$LISTS/ipset-all.txt"
    printf '%s\n' '198.51.100.0/24' > "$LISTS/ipset-exclude.txt"
    printf '%s\n' '192.0.2.10' > "$LISTS/ipset-telegram.txt"
    # Копии настоящих скриптов из репозитория: стенд всегда проверяет текущие версии.
    cp "$REPO/warp/system/warp_start.sh" "$FAKE/warp_start.sh"
    cp "$REPO/warp/system/warp_stop.sh" "$FAKE/warp_stop.sh"
    cp "$REPO/warp/system/warp_up.sh" "$FAKE/warp_up.sh"
    cp "$REPO/warp/system/warp_down.sh" "$FAKE/warp_down.sh"
    cp "$STAND/stub-usque.sh" "$FAKE/usque"
    cp "$STAND/account.json" "$FAKE/account.json"
    chmod +x "$FAKE"/warp_*.sh "$FAKE/usque" "$BIN"/*
}

write_warp_txt() { printf '%s\n' "$@" > "$WARPTXT"; }

default_warp_txt() {
    write_warp_txt \
        'enabled=1' \
        'mode=full' \
        'transport=auto' \
        'sni=consumer-masque.cloudflareclient.com' \
        'iface=warp0' \
        'mtu=1280' \
        'dns=link' \
        'help_tunnel=1' \
        'lists=' \
        'exclude=' \
        'uids='
}

# env -i: у скриптов «чистое» окружение, как у юнита systemd.
stand_env() {
    env -i \
        PATH="$BIN:/usr/bin:/bin" \
        HOME="$STDHOME" \
        WARP_HOME="$STDHOME" \
        WARP_DIR="$FAKE" \
        STATE_DIR="$STATE" \
        LOG_FILE="$USQUE_LOG" \
        WARP_TEST_JOURNAL="$JOURNAL" \
        WARP_TEST_IFACES="$IFACES" \
        WARP_TEST_ROUTES="$ROUTES" \
        WARP_TEST_RULES="$IPRULES" \
        "$@"
}

run_start() { stand_env "$@" bash "$FAKE/warp_start.sh"; }
run_stop() { stand_env "$@" bash "$FAKE/warp_stop.sh"; }

# --- Проверки ---
PASS=0
FAIL=0
ok() { printf '   PASS: %s\n' "$*"; PASS=$((PASS + 1)); }
bad() { printf '   FAIL: %s\n' "$*"; FAIL=$((FAIL + 1)); }

expect_rc() {
    if [ "$1" = "$2" ]; then ok "код возврата = $2"; else bad "код возврата = $1, ожидался $2"; fi
}

expect_file() {
    if [ -f "$2" ]; then ok "$1: файл есть ($2)"; else bad "$1: файла нет ($2)"; fi
}

expect_nofile() {
    if [ -f "$2" ]; then bad "$1: файл не должен существовать ($2)"; else ok "$1: файла нет ($2)"; fi
}

expect_grep() {
    if grep -qF -- "$2" "$3"; then ok "$1"; else bad "$1: не найдено '$2' в $3"; fi
}

expect_count() {
    local n
    n=$(grep -cF -- "$2" "$3")
    if [ "$n" = "$4" ]; then ok "$1: найдено $n"; else bad "$1: найдено $n, ожидалось $4"; fi
}

expect_order() {
    local l1 l2
    l1=$(grep -nF -- "$2" "$4" | head -n 1 | cut -d: -f1)
    l2=$(grep -nF -- "$3" "$4" | head -n 1 | cut -d: -f1)
    if [ -n "$l1" ] && [ -n "$l2" ] && [ "$l1" -lt "$l2" ]; then
        ok "$1 (строки журнала $l1 < $l2)"
    else
        bad "$1 (строки журнала: '$2' -> ${l1:-нет}, '$3' -> ${l2:-нет})"
    fi
}

show_journal() {
    echo "   --- журнал стенда ($JOURNAL) ---"
    sed -e 's/^/   | /' "$JOURNAL"
    if [ -s "$ROUTES" ]; then
        echo "   --- таблица маршрутов ---"
        sed -e 's/^/   | /' "$ROUTES"
    fi
    if [ -f "$STATE/transport" ]; then
        echo "   --- state/transport: $(cat "$STATE/transport") ---"
    fi
}

summary() {
    echo "   ИТОГО: PASS=$PASS FAIL=$FAIL"
    return 0
}
