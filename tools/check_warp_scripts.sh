#!/usr/bin/env bash
# Проверка скриптов подсистемы WARP (warp/system/*.sh) на стенде с заглушками.
#
# Стенд запускает НАСТОЯЩИЕ скрипты из warp/system с подменёнными usque, ip,
# resolvectl, logger, systemctl и «таблицей маршрутов», поэтому проверяет логику
# без root, без /dev/net/tun и без сети: порядок маршрутов, автополкат при
# отсутствии данных, откат транспорта QUIC→HTTP/2, идемпотентную остановку.
#
# Скрипты требуют root и наличие /dev/net/tun, поэтому стенд создаёт симлинк
# /dev/net/tun -> /dev/null и подменяет `id`. Для этого нужен root: локально
# запускайте через sudo, в CI шаг идёт с sudo (раннеры GitHub дают его без пароля).
#
# Использование:
#   sudo bash tools/check_warp_scripts.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAND_DIR="$ROOT/tools/warp_scripts_test"
CASES=(case1 case2 case3 case4 case5 case6 case7 caseA caseB caseC caseD caseE caseF)

if [ ! -f "$STAND_DIR/cases.sh" ]; then
    echo "нет стенда: $STAND_DIR/cases.sh" >&2
    exit 2
fi

pass_total=0
fail_total=0
failed_cases=()

for case_name in "${CASES[@]}"; do
    output="$(bash "$STAND_DIR/cases.sh" "$case_name" 2>&1)"
    summary="$(printf '%s\n' "$output" | grep -E 'ИТОГО: PASS=' | tail -n 1)"
    if [ -z "$summary" ]; then
        echo "СБОЙ: $case_name не дошёл до итога"
        printf '%s\n' "$output" | tail -n 15
        fail_total=$((fail_total + 1))
        failed_cases+=("$case_name")
        continue
    fi
    read -r case_pass case_fail <<<"$(printf '%s\n' "$summary" |
        sed -n 's/.*PASS=\([0-9]*\) FAIL=\([0-9]*\).*/\1 \2/p')"
    pass_total=$((pass_total + case_pass))
    fail_total=$((fail_total + case_fail))
    printf '%-7s PASS=%-3s FAIL=%s\n' "$case_name" "$case_pass" "$case_fail"
    if [ "$case_fail" != "0" ]; then
        failed_cases+=("$case_name")
        printf '%s\n' "$output" | grep -E '   FAIL:' || true
    fi
done

echo "---"
echo "итого: PASS=$pass_total FAIL=$fail_total (кейсов: ${#CASES[@]})"
if [ "$fail_total" != "0" ]; then
    echo "провалились: ${failed_cases[*]}" >&2
    exit 1
fi
echo "стенд WARP: все проверки пройдены"
exit 0
