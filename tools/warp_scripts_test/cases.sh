#!/bin/bash
# Кейсы стенда: bash cases.sh <номер|A|B>
# Печатает фактические результаты (код возврата, ключевые строки журнала) и PASS/FAIL.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/stand.sh"

case1() {
    echo "### Кейс 1: transport=auto, USQUE_FAIL_QUIC=1 (QUIC не работает, должен быть откат на HTTP/2)"
    stand_setup
    default_warp_txt
    echo "команда: run_start USQUE_FAIL_QUIC=1  ->  PATH=bin WARP_DIR=fake-warp STATE_DIR=state bash fake-warp/warp_start.sh"
    run_start USQUE_FAIL_QUIC=1
    local rc=$?
    echo "код возврата: $rc"
    expect_rc "$rc" 0
    expect_count "ровно две попытки usque" "usque: argv: nativetun" "$JOURNAL" 2
    local first second
    first=$(grep -F 'usque: argv: nativetun' "$JOURNAL" | head -n 1)
    second=$(grep -F 'usque: argv: nativetun' "$JOURNAL" | tail -n 1)
    case "$first" in
        *--http2*) bad "первая попытка содержит --http2" ;;
        *) ok "первая попытка без --http2" ;;
    esac
    case "$second" in
        *--http2*) ok "вторая попытка с --http2" ;;
        *) bad "вторая попытка без --http2" ;;
    esac
    expect_file "маркер подключения" "$STATE/connected"
    expect_order "маршрут до endpoint'а (через gw eth0) раньше 0.0.0.0/1" \
        "ip: argv: route replace 162.159.198.2/32 via 192.168.1.1 dev eth0" \
        "ip: argv: route replace 0.0.0.0/1 dev warp0" "$JOURNAL"
    expect_order "0.0.0.0/1 раньше 128.0.0.0/1" \
        "ip: argv: route replace 0.0.0.0/1 dev warp0" \
        "ip: argv: route replace 128.0.0.0/1 dev warp0" "$JOURNAL"
    expect_grep "DNS: resolvectl dns warp0 1.1.1.1 1.0.0.1" "resolvectl: dns warp0 1.1.1.1 1.0.0.1" "$JOURNAL"
    expect_grep "DNS: resolvectl domain warp0 ~." "resolvectl: domain warp0 ~." "$JOURNAL"
    local transport
    transport=$(cat "$STATE/transport" 2>/dev/null)
    if [ "$transport" = "http2" ]; then ok "state/transport = http2"; else bad "state/transport = '${transport:-<нет>}', ожидался http2"; fi
    show_journal
    summary
}

case2() {
    echo "### Кейс 2: transport=quic, подключение с первой попытки"
    stand_setup
    default_warp_txt
    write_warp_txt 'enabled=1' 'mode=full' 'transport=quic' 'sni=consumer-masque.cloudflareclient.com' 'iface=warp0' 'mtu=1280' 'dns=link' 'help_tunnel=1' 'lists='
    echo "команда: run_start (transport=quic)"
    run_start
    local rc=$?
    echo "код возврата: $rc"
    expect_rc "$rc" 0
    expect_count "одна попытка usque" "usque: argv: nativetun" "$JOURNAL" 1
    case "$(grep -F 'usque: argv: nativetun' "$JOURNAL" | head -n 1)" in
        *--http2*) bad "в попытке есть --http2" ;;
        *) ok "попытка без --http2" ;;
    esac
    expect_file "маркер подключения" "$STATE/connected"
    local transport
    transport=$(cat "$STATE/transport" 2>/dev/null)
    if [ "$transport" = "quic" ]; then ok "state/transport = quic"; else bad "state/transport = '${transport:-<нет>}', ожидался quic"; fi
    show_journal
    summary
}

case3() {
    echo "### Кейс 3: USQUE_FAIL_ALWAYS=1 (usque всегда падает, хуков нет)"
    stand_setup
    default_warp_txt
    echo "команда: run_start USQUE_FAIL_ALWAYS=1"
    run_start USQUE_FAIL_ALWAYS=1
    local rc=$?
    echo "код возврата: $rc"
    expect_rc "$rc" 1
    expect_count "обе попытки usque в журнале" "usque: argv: nativetun" "$JOURNAL" 2
    expect_grep "снятие 0.0.0.0/1" "ip: argv: route del 0.0.0.0/1 dev warp0" "$JOURNAL"
    expect_grep "снятие 128.0.0.0/1" "ip: argv: route del 128.0.0.0/1 dev warp0" "$JOURNAL"
    expect_nofile "маркера подключения нет" "$STATE/connected"
    show_journal
    summary
}

case4() {
    echo "### Кейс 4: остановка после успешного старта (warp_stop.sh), затем повторный запуск"
    stand_setup
    default_warp_txt
    run_start USQUE_FAIL_QUIC=1 > /dev/null 2>&1
    local rc=$?
    echo "старт: код возврата $rc"
    local pid
    pid=$(head -n 1 "$STATE/usque.pid" 2>/dev/null)
    echo "usque PID: ${pid:-<нет>}, интерфейс warp0: $([ -e "$IFACES/warp0" ] && echo есть || echo нет), маркер connected: $([ -f "$STATE/connected" ] && echo есть || echo нет)"
    echo "команда: run_stop  ->  bash fake-warp/warp_stop.sh"
    run_stop
    rc=$?
    echo "код возврата (1-я остановка): $rc"
    expect_rc "$rc" 0
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then bad "процесс usque $pid всё ещё жив"; kill -9 "$pid" 2>/dev/null; else ok "процесс usque ${pid:-<нет>} остановлен"; fi
    expect_grep "снятие 0.0.0.0/1" "ip: argv: route del 0.0.0.0/1 dev warp0" "$JOURNAL"
    expect_grep "снятие 128.0.0.0/1" "ip: argv: route del 128.0.0.0/1 dev warp0" "$JOURNAL"
    expect_grep "resolvectl revert warp0" "resolvectl: revert warp0" "$JOURNAL"
    expect_grep "удаление интерфейса (WARP_STOP=1)" "ip: argv: link del warp0" "$JOURNAL"
    expect_nofile "state/connected удалён" "$STATE/connected"
    expect_nofile "state/usque.pid удалён" "$STATE/usque.pid"
    expect_nofile "state/transport удалён" "$STATE/transport"
    if [ -e "$IFACES/warp0" ]; then bad "маркер интерфейса warp0 остался"; else ok "интерфейс warp0 удалён"; fi
    echo "команда: run_stop (повторно)"
    run_stop
    rc=$?
    echo "код возврата (2-я остановка, идемпотентность): $rc"
    expect_rc "$rc" 0
    show_journal
    summary
}

case5() {
    echo "### Кейс 5: некорректный mode=weird"
    stand_setup
    write_warp_txt 'enabled=1' 'mode=weird' 'transport=auto' 'sni=consumer-masque.cloudflareclient.com' 'iface=warp0' 'mtu=1280' 'dns=link' 'help_tunnel=1' 'lists='
    local out rc
    out=$(run_start 2>&1)
    rc=$?
    echo "код возврата: $rc"
    echo "вывод: $out" | sed -e 's/^/   | /'
    expect_rc "$rc" 1
    case "$out" in
        *"недопустимый mode='weird'"*) ok "понятное сообщение про mode" ;;
        *) bad "нет сообщения про недопустимый mode" ;;
    esac
    expect_count "usque не запускался" "usque: argv: nativetun" "$JOURNAL" 0
    summary
}

case6() {
    echo "### Кейс 6: недопустимый iface (с пробелом)"
    stand_setup
    write_warp_txt 'enabled=1' 'mode=full' 'transport=auto' 'sni=consumer-masque.cloudflareclient.com' 'iface=warp 0' 'mtu=1280' 'dns=link' 'help_tunnel=1' 'lists='
    local out rc
    out=$(run_start 2>&1)
    rc=$?
    echo "код возврата: $rc"
    echo "вывод: $out" | sed -e 's/^/   | /'
    expect_rc "$rc" 1
    case "$out" in
        *"недопустимое имя интерфейса 'warp 0'"*) ok "понятное сообщение про iface" ;;
        *) bad "нет сообщения про недопустимый iface" ;;
    esac
    expect_count "usque не запускался" "usque: argv: nativetun" "$JOURNAL" 0
    summary
}

case7() {
    echo "### Кейс 7: warp.txt отсутствует (значения по умолчанию)"
    stand_setup
    rm -f "$WARPTXT"
    local out rc
    out=$(run_start 2>&1)
    rc=$?
    echo "код возврата: $rc"
    echo "вывод: $out" | sed -e 's/^/   | /'
    expect_rc "$rc" 0
    case "$out" in
        *"не найден, использую значения по умолчанию"*) ok "залогировано использование значений по умолчанию" ;;
        *) bad "нет сообщения об отсутствии файла состояния" ;;
    esac
    case "$out" in
        *"Настройки: mode=full transport=auto iface=warp0 mtu=1280"*) ok "применены значения по умолчанию" ;;
        *) bad "значения по умолчанию не совпали с ожидаемыми" ;;
    esac
    summary
}

caseA() {
    echo "### Доп. кейс A: pid-файл потерян — страховка pgrep/pkill в warp_stop.sh"
    stand_setup
    default_warp_txt
    run_start USQUE_FAIL_QUIC=1 > /dev/null 2>&1
    local pid
    pid=$(head -n 1 "$STATE/usque.pid" 2>/dev/null)
    rm -f "$STATE/usque.pid"
    echo "usque PID $pid жив: $(kill -0 "$pid" 2>/dev/null && echo да || echo нет); pid-файл удалён вручную"
    echo "команда: run_stop"
    run_stop > /dev/null 2>&1
    local rc=$?
    echo "код возврата: $rc"
    expect_rc "$rc" 0
    expect_grep "pgrep вызывался" "pgrep: -f" "$JOURNAL"
    sleep 0.5
    if kill -0 "$pid" 2>/dev/null; then
        bad "процесс usque $pid остался жив: страховка не нашла процесс"
        kill -9 "$pid" 2>/dev/null
    else
        ok "процесс usque $pid остановлен страховкой"
    fi
    grep -F 'pgrep: -f' "$JOURNAL" | sed -e 's/^/   | /'
    grep -F 'pkill:' "$JOURNAL" | sed -e 's/^/   | /'
    summary
}

caseB() {
    echo "### Доп. кейс B: подключения нет — фактический таймаут ожидания (WARP_CONNECT_TIMEOUT=4, transport=quic)"
    stand_setup
    write_warp_txt 'enabled=1' 'mode=full' 'transport=quic' 'sni=consumer-masque.cloudflareclient.com' 'iface=warp0' 'mtu=1280' 'dns=link' 'help_tunnel=1' 'lists='
    local t0 t1 rc
    t0=$(date +%s)
    run_start WARP_CONNECT_TIMEOUT=4 USQUE_NEVER_CONNECT=1 > /dev/null 2>&1
    rc=$?
    t1=$(date +%s)
    echo "код возврата: $rc; прошло секунд: $((t1 - t0)) (WARP_CONNECT_TIMEOUT=4, сообщение скрипта обещает 4s)"
    expect_rc "$rc" 1
    expect_grep "сообщение о таймауте" "не подключился за 4s" "$JOURNAL"
    if [ "$((t1 - t0))" -ge 3 ]; then
        ok "ожидание близко к CONNECT_TIMEOUT (>= 3 с)"
    else
        bad "ожидание $((t1 - t0)) с заметно меньше CONNECT_TIMEOUT=4 с"
    fi
    summary
}

caseC() {
    echo "### Доп. кейс C: туннель подключился, но данные не проходят (PROBE_FAIL=1)"
    stand_setup
    echo "команда: run_start PROBE_FAIL=1 (transport=quic) -> самопроверка не должна оставить полный туннель"
    run_start PROBE_FAIL=1 > /dev/null 2>&1
    local rc=$?
    echo "код возврата: $rc"
    expect_rc "$rc" 1
    expect_nofile "маркер подключения не поставлен" "$STATE/connected"
    expect_file "причина сохранена для приложения" "$STATE/last_probe_failed"
    expect_grep "снятие 0.0.0.0/1 (автополкат)" "ip: argv: route del 0.0.0.0/1 dev warp0" "$JOURNAL"
    expect_grep "снятие 128.0.0.0/1 (автополкат)" "ip: argv: route del 128.0.0.0/1 dev warp0" "$JOURNAL"
    expect_grep "DNS возвращён" "resolvectl: revert warp0" "$JOURNAL"
    expect_grep "понятное сообщение" "Данные через туннель не проходят" "$JOURNAL"
    expect_grep "самопроверка выполнялась" "curl: argv:" "$JOURNAL"
    show_journal
    summary
}

caseD() {
    echo "### Доп. кейс D: селективный режим — списки, исключения и uidrange"
    stand_setup
    write_warp_txt 'enabled=1' 'mode=select' 'transport=quic' 'sni=consumer-masque.cloudflareclient.com' \
        'iface=warp0' 'mtu=1280' 'dns=link' 'help_tunnel=1' \
        'lists=ipset-all,ipset-telegram' 'exclude=ipset-exclude' 'uids=1000-1000'
    run_start > /dev/null 2>&1
    local rc=$?
    echo "код возврата: $rc"
    expect_rc "$rc" 0
    expect_file "маркер подключения" "$STATE/connected"
    expect_grep "таблица 100: default через туннель" \
        "ip: argv: route replace default dev warp0 table 100" "$JOURNAL"
    expect_grep "исключение уходит в main" \
        "ip: argv: rule add to 198.51.100.0/24 lookup main priority 10000" "$JOURNAL"
    expect_grep "v4-диапазон в таблицу 100" \
        "ip: argv: rule add to 203.0.113.0/24 lookup 100 priority 11000" "$JOURNAL"
    expect_grep "v6-диапазон через ip -6" \
        "ip: argv: -6 rule add to 2001:db8:1::/48 lookup 100 priority 11001" "$JOURNAL"
    expect_grep "второй список (telegram)" \
        "ip: argv: rule add to 192.0.2.10 lookup 100 priority 11002" "$JOURNAL"
    expect_grep "uidrange по пользователю" \
        "ip: argv: rule add uidrange 1000-1000 lookup 100 priority 12000" "$JOURNAL"
    expect_order "исключение раньше выбранных диапазонов" \
        "rule add to 198.51.100.0/24 lookup main priority 10000" \
        "rule add to 203.0.113.0/24 lookup 100 priority 11000" "$JOURNAL"
    expect_grep "DNS остался системным" "DNS системный" "$JOURNAL"
    if grep -q "resolvectl: dns" "$JOURNAL"; then
        bad "в селективном режиме DNS туннеля настраивать нельзя"
    else
        ok "resolvectl dns не вызывался"
    fi
    expect_file "список правил для остановки" "$STATE/iprules"
    show_journal
    summary
}

caseE() {
    echo "### Доп. кейс E: селективный режим — правила не завернули трафик (самопроверка и откат)"
    stand_setup
    write_warp_txt 'enabled=1' 'mode=select' 'transport=quic' 'sni=consumer-masque.cloudflareclient.com' \
        'iface=warp0' 'mtu=1280' 'dns=link' 'help_tunnel=1' \
        'lists=ipset-all' 'exclude=' 'uids='
    run_start WARP_TEST_ROUTE_GET_MAIN=1 > /dev/null 2>&1
    local rc=$?
    echo "код возврата: $rc"
    expect_rc "$rc" 1
    expect_nofile "маркер подключения не поставлен" "$STATE/connected"
    expect_file "причина сохранена для приложения" "$STATE/last_probe_failed"
    expect_grep "правила сняты" "ip: argv: rule del priority 11000" "$JOURNAL"
    expect_grep "таблица 100 убрана" "ip: argv: route del default dev warp0 table 100" "$JOURNAL"
    expect_grep "понятное сообщение" "Самопроверка селективного режима не прошла" "$JOURNAL"
    show_journal
    summary
}

caseF() {
    echo "### Доп. кейс F: аварийная очистка снимает правила без файла /run/warp/iprules"
    stand_setup
    default_warp_txt
    printf '%s\n' 'family=4 to 203.0.113.0/24 lookup 100 priority 11000' \
        'family=4 to 1.1.1.0/24 lookup 100 priority 11001' > "$IPRULES"
    rm -f "$STATE/iprules"
    run_stop > /dev/null 2>&1
    local rc=$?
    echo "код возврата: $rc"
    expect_rc "$rc" 0
    expect_grep "первое правило снято перебором" "ip: argv: rule del priority 11000" "$JOURNAL"
    expect_grep "второе правило снято перебором" "ip: argv: rule del priority 11001" "$JOURNAL"
    if grep -q 'priority 1100' "$IPRULES"; then
        bad "правила остались в таблице"
    else
        ok "правила удалены из таблицы"
    fi
    expect_grep "таблица 100 убрана" "ip: argv: route del default dev warp0 table 100" "$JOURNAL"
    show_journal
    summary
}

"$@"
