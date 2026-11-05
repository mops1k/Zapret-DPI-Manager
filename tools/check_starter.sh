#!/bin/bash
# Стенд zapret/system/starter.sh: служба запускается и без выбранной стратегии.
#
# starter.sh работает от root и знает абсолютные пути (/opt/zapret, /etc/passwd,
# /run/zapret), поэтому стенд берёт его копию с переписанными путями и запускает
# с заглушками nft/iptables/sysctl/nfqws: проверяются выбор правил и сообщения,
# а не реальный сетевой стек. Root и сеть не нужны.
#
# Что проверяется:
#   1. пустой config.txt + files/strategy/General → правила из General, nfqws запущен;
#   2. пустой config.txt без стратегии по умолчанию → код 1 и внятное сообщение;
#   3. --filter-tcp=443 и --filter-udp=* (nftables) → tcp-правило с dport, udp без dport;
#   4. то же для iptables → tcp с --dports, udp без multiport;
#   5. config.txt отсутствует → тот же путь по умолчанию, а не отказ.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
STARTER="$REPO_ROOT/zapret/system/starter.sh"

SANDBOX="$(mktemp -d)"
NFQWS_STUB_PIDS=()

cleanup() {
    local pid
    for pid in "${NFQWS_STUB_PIDS[@]:-}"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null
    done
    if [ -f "$SANDBOX/state/nfqws.pids" ]; then
        while IFS= read -r pid; do
            [ -n "$pid" ] && kill "$pid" 2>/dev/null
        done < "$SANDBOX/state/nfqws.pids"
    fi
    rm -rf "$SANDBOX"
}
trap cleanup EXIT

FAILED=0
pass() { echo "  ok: $*"; }
fail() { echo "  FAIL: $*" >&2; FAILED=1; }

[ -f "$STARTER" ] || { echo "FAIL: нет файла $STARTER" >&2; exit 1; }

# --- заглушки системных утилит -------------------------------------------

mkdir -p "$SANDBOX/bin" "$SANDBOX/opt/zapret" "$SANDBOX/run" "$SANDBOX/state"

cat > "$SANDBOX/bin/nft" <<'EOF'
#!/bin/bash
# Заглушка nft: журнал вызовов и имитация «list chain» для проверки стартера.
state="$STARTER_STUB_STATE"
printf '%s\n' "$*" >> "$state/nft.log"
if [ "${1:-}" = "list" ]; then
    if grep -q 'prerouting' "$state/nft-rules.log" 2>/dev/null; then
        echo "table inet zapret {"
        echo "	chain prerouting {"
        echo "		tcp sport { 443 } ct reply packets 1-6 queue flags bypass to 200"
        echo "	}"
        echo "}"
    fi
    exit 0
fi
if [ "${1:-}" = "add" ] && [ "${2:-}" = "rule" ]; then
    printf '%s\n' "$*" >> "$state/nft-rules.log"
fi
exit 0
EOF

cat > "$SANDBOX/bin/iptables" <<'EOF'
#!/bin/bash
# Заглушка iptables/ip6tables: журнал правил и «-S ZAPRET» для проверки стартера.
state="$STARTER_STUB_STATE"
tool="$(basename "$0")"
printf '%s %s\n' "$tool" "$*" >> "$state/iptables.log"
case " $* " in
    *" -S "*)
        if [ -s "$state/iptables-rules.log" ]; then
            echo "-A ZAPRET -p tcp -m multiport --dports 443 -j NFQUEUE --queue-num 200 --queue-bypass"
        fi
        ;;
    *" -I "* | *" -A "*)
        printf '%s %s\n' "$tool" "$*" >> "$state/iptables-rules.log"
        ;;
esac
exit 0
EOF
cp "$SANDBOX/bin/iptables" "$SANDBOX/bin/ip6tables"

cat > "$SANDBOX/bin/sysctl" <<'EOF'
#!/bin/bash
# Заглушка sysctl: значение читается, запись «удаётся».
if [ "${1:-}" = "-n" ]; then
    echo 0
fi
exit 0
EOF

cat > "$SANDBOX/bin/pidof" <<'EOF'
#!/bin/bash
# nfqws заведомо не запущен: стенд проверяет путь первого старта.
exit 1
EOF

cat > "$SANDBOX/bin/logname" <<'EOF'
#!/bin/bash
# Без терминала (как у systemd) logname не отвечает.
exit 1
EOF

cat > "$SANDBOX/bin/getent" <<'EOF'
#!/bin/bash
# Пользователь берётся из /etc/passwd стенда, getent для него не нужен.
exit 1
EOF

cat > "$SANDBOX/opt/zapret/nfqws" <<'EOF'
#!/bin/bash
# Заглушка nfqws: фиксирует аргументы и живёт, пока стартер проверяет PID.
state="$STARTER_STUB_STATE"
printf '%s\n' "$*" >> "$state/nfqws.log"
echo "$$" >> "$state/nfqws.pids"
exec sleep 15
EOF
chmod +x "$SANDBOX/bin/"* "$SANDBOX/opt/zapret/nfqws"
echo nftables > "$SANDBOX/opt/zapret/FWTYPE"

# --- копия стартера с путями песочницы ------------------------------------

make_starter_copy() {
    # $1 — значение FWTYPE в /opt/zapret/FWTYPE песочницы
    echo "$1" > "$SANDBOX/opt/zapret/FWTYPE"
    sed -e "s|/opt/zapret|$SANDBOX/opt/zapret|g" \
        -e "s|/etc/passwd|$SANDBOX/passwd|g" \
        -e "s|/run/zapret|$SANDBOX/run/zapret|g" \
        -e "s|/run/warp|$SANDBOX/run/warp|g" \
        -e "s|if \[ \"\$EUID\" -ne 0 \]; then|if false; then|" \
        "$STARTER" > "$SANDBOX/starter.sh"
}

# --- песочница с «установленным» менеджером -------------------------------

prepare_sandbox() {
    rm -rf "$SANDBOX/home" "$SANDBOX/state" "$SANDBOX/run"
    mkdir -p "$SANDBOX/home/deck/Zapret_DPI_Manager/files/lists" \
        "$SANDBOX/home/deck/Zapret_DPI_Manager/files/bin" \
        "$SANDBOX/home/deck/Zapret_DPI_Manager/files/strategy" \
        "$SANDBOX/home/deck/Zapret_DPI_Manager/utils" \
        "$SANDBOX/state" "$SANDBOX/run/zapret"
    cp "$REPO_ROOT"/files/lists/* "$SANDBOX/home/deck/Zapret_DPI_Manager/files/lists/"
    cp "$REPO_ROOT"/files/bin/* "$SANDBOX/home/deck/Zapret_DPI_Manager/files/bin/"
    : > "$SANDBOX/home/deck/Zapret_DPI_Manager/config.txt"
    printf 'deck:x:1000:1000:deck:%s:/bin/bash\n' "$SANDBOX/home/deck" > "$SANDBOX/passwd"
}

run_starter() {
    PATH="$SANDBOX/bin:$PATH" \
        STARTER_STUB_STATE="$SANDBOX/state" \
        env -u SUDO_USER bash "$SANDBOX/starter.sh" 2>&1
}

ZM="$SANDBOX/home/deck/Zapret_DPI_Manager"

# --- 1. пустой config.txt: применяется стратегия по умолчанию -------------

echo "1. Пустой config.txt + files/strategy/General"
prepare_sandbox
cp "$REPO_ROOT/files/strategy/General" "$ZM/files/strategy/General"
make_starter_copy nftables
OUT="$(run_starter)"
RC=$?
if [ "$RC" -eq 0 ]; then
    pass "код выхода 0"
else
    fail "код выхода $RC вместо 0: $(echo "$OUT" | tail -n 3 | tr '\n' ' ')"
fi
if echo "$OUT" | grep -q "применяю стратегию по умолчанию: General"; then
    pass "в журнале сказано, что применена стратегия по умолчанию"
else
    fail "нет сообщения о стратегии по умолчанию"
fi
if grep -q '^add rule inet zapret postrouting tcp' "$SANDBOX/state/nft-rules.log" 2>/dev/null; then
    pass "правила NFQUEUE созданы из General"
else
    fail "правила nftables не созданы: $(cat "$SANDBOX/state/nft.log" 2>/dev/null | tr '\n' ' ')"
fi
if grep -q -- '--filter-tcp=' "$SANDBOX/state/nfqws.log" 2>/dev/null &&
    grep -q -- '--filter-udp=' "$SANDBOX/state/nfqws.log" 2>/dev/null; then
    pass "nfqws запущен с правилами General"
else
    fail "nfqws не получил аргументы стратегии"
fi
if echo "$OUT" | grep -q "nfqws successfully started"; then
    pass "стартер сообщил об успешном запуске nfqws"
else
    fail "нет сообщения об успешном запуске nfqws"
fi

# --- 2. пустой config.txt без стратегии по умолчанию ----------------------

echo "2. Пустой config.txt без files/strategy/General"
prepare_sandbox
make_starter_copy nftables
OUT="$(run_starter)"
RC=$?
if [ "$RC" -ne 0 ]; then
    pass "код выхода $RC (запуск без правил невозможен)"
else
    fail "стартер завершился успешно, хотя правил нет"
fi
if echo "$OUT" | grep -q "файл стратегии по умолчанию не найден"; then
    pass "сообщение объясняет причину и путь"
else
    fail "нет внятного сообщения о пропавшей стратегии: $(echo "$OUT" | tail -n 2 | tr '\n' ' ')"
fi
if grep -q '^add rule' "$SANDBOX/state/nft-rules.log" 2>/dev/null; then
    fail "правила без стратегии всё равно создавались"
else
    pass "правила не создавались"
fi

# --- 3. --filter-udp=* на nftables ---------------------------------------

echo "3. --filter-tcp=443 и --filter-udp=* (nftables)"
prepare_sandbox
printf '%s\n%s\n' \
    '--filter-tcp=443 --dpi-desync=fake --dpi-desync-repeats=2 --new' \
    '--filter-udp=* --dpi-desync=fake --dpi-desync-repeats=2 --new' \
    > "$ZM/config.txt"
make_starter_copy nftables
OUT="$(run_starter)"
RC=$?
if [ "$RC" -eq 0 ]; then
    pass "код выхода 0"
else
    fail "код выхода $RC вместо 0: $(echo "$OUT" | tail -n 3 | tr '\n' ' ')"
fi
if grep -q 'postrouting tcp dport { 443 }' "$SANDBOX/state/nft-rules.log" 2>/dev/null; then
    pass "tcp-правило с портом 443"
else
    fail "tcp-правило с dport { 443 } не создано: $(cat "$SANDBOX/state/nft-rules.log" 2>/dev/null | tr '\n' ' ')"
fi
if grep -q 'postrouting udp ' "$SANDBOX/state/nft-rules.log" 2>/dev/null &&
    ! grep -q 'postrouting udp dport' "$SANDBOX/state/nft-rules.log" 2>/dev/null; then
    pass "udp-правило «все порты» без dport"
else
    fail "udp-правило для * построено неверно"
fi
if echo "$OUT" | grep -q "UDP ports: \* (все порты)"; then
    pass "в журнале порты UDP помечены как «все»"
else
    fail "журнал не сообщает про все UDP-порты"
fi

# --- 4. --filter-udp=* на iptables ---------------------------------------

echo "4. --filter-tcp=443 и --filter-udp=* (iptables)"
prepare_sandbox
printf '%s\n%s\n' \
    '--filter-tcp=443 --dpi-desync=fake --dpi-desync-repeats=2 --new' \
    '--filter-udp=* --dpi-desync=fake --dpi-desync-repeats=2 --new' \
    > "$ZM/config.txt"
make_starter_copy iptables
OUT="$(run_starter)"
RC=$?
if [ "$RC" -eq 0 ]; then
    pass "код выхода 0"
else
    fail "код выхода $RC вместо 0: $(echo "$OUT" | tail -n 3 | tr '\n' ' ')"
fi
if grep -q -- '-p tcp -m multiport --dports 443' "$SANDBOX/state/iptables-rules.log" 2>/dev/null; then
    pass "tcp-правило с --dports 443"
else
    fail "tcp-правило с портом не создано"
fi
if grep -q -- '-p udp .* -j NFQUEUE' "$SANDBOX/state/iptables-rules.log" 2>/dev/null &&
    ! grep -q -- '-p udp -m multiport' "$SANDBOX/state/iptables-rules.log" 2>/dev/null; then
    pass "udp-правило «все порты» без multiport"
else
    fail "udp-правило для * построено неверно: $(grep -- '-p udp' "$SANDBOX/state/iptables-rules.log" 2>/dev/null | tr '\n' ' ')"
fi

# --- 5. config.txt отсутствует: тоже стратегия по умолчанию --------------

echo "5. config.txt отсутствует, files/strategy/General на месте"
prepare_sandbox
cp "$REPO_ROOT/files/strategy/General" "$ZM/files/strategy/General"
rm -f "$ZM/config.txt"
make_starter_copy nftables
OUT="$(run_starter)"
RC=$?
if [ "$RC" -eq 0 ]; then
    pass "код выхода 0"
else
    fail "код выхода $RC вместо 0: $(echo "$OUT" | tail -n 3 | tr '\n' ' ')"
fi
if echo "$OUT" | grep -q "применяю стратегию по умолчанию: General"; then
    pass "стратегия по умолчанию применена и без config.txt"
else
    fail "нет сообщения о стратегии по умолчанию"
fi

echo ""
if [ "$FAILED" -ne 0 ]; then
    echo "СТЕНД ПРОВАЛЕН" >&2
    exit 1
fi
echo "СТЕНД ПРОЙДЕН"
