#!/bin/bash
# Стенд установщика: разблокировка SteamOS вокруг установки пакетов (от root)
# и резервная установка PySide6 через pip.
#
# Проверяет только извлечённые функции из installer/install_zapret.sh (блоки помечены
# маркерами «>>> steamos helpers»/«>>> pyside6 install helpers»), сам установщик не запускается.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
INSTALLER="$REPO_ROOT/installer/install_zapret.sh"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

FAILED=0
pass() { echo "  ok: $*"; }
fail() { echo "  FAIL: $*" >&2; FAILED=1; }

extract_block() {
    local start="$1"
    local end="$2"
    [ -f "$INSTALLER" ] || fail "нет файла $INSTALLER"
    awk -v start="$start" -v end="$end" '
        $0 ~ start { inside = 1 }
        inside { print }
        inside && $0 ~ end { exit }
    ' "$INSTALLER"
}

extract_block '^# >>> steamos helpers' '^# <<< steamos helpers' > "$TMP_DIR/steamos_helpers.sh"
extract_block '^# >>> pyside6 install helpers' '^# <<< pyside6 install helpers' > "$TMP_DIR/pyside6_helpers.sh"

[ -s "$TMP_DIR/steamos_helpers.sh" ] || { echo "FAIL: не найден блок steamos helpers" >&2; exit 1; }
[ -s "$TMP_DIR/pyside6_helpers.sh" ] || { echo "FAIL: не найден блок pyside6 install helpers" >&2; exit 1; }

cat > "$TMP_DIR/os-steamos" <<'EOF'
NAME="SteamOS"
ID=steamos
ID_LIKE=archlinux
EOF

cat > "$TMP_DIR/os-arch" <<'EOF'
NAME="Arch Linux"
ID=arch
EOF

# Подменный steamos-readonly: настоящий бинарь есть только на SteamOS.
mkdir -p "$TMP_DIR/bin"
cat > "$TMP_DIR/bin/steamos-readonly" <<'EOF'
#!/bin/bash
exit 0
EOF
chmod +x "$TMP_DIR/bin/steamos-readonly"

# Подменный curl: отдаёт заранее собранные «ассеты релиза» (бандл и SHA256SUMS).
cat > "$TMP_DIR/bin/curl" <<'EOF'
#!/bin/bash
out=""
url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        --retry) shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
printf 'curl: %s\n' "$url" >> "$CALLS"
case "$url" in
    */SHA256SUMS)
        [ -n "${STUB_VENDOR_SUMS:-}" ] || exit 22
        cp "$STUB_VENDOR_SUMS" "$out"
        ;;
    *)
        [ -n "${STUB_VENDOR_ARCHIVE:-}" ] || exit 22
        cp "$STUB_VENDOR_ARCHIVE" "$out"
        ;;
esac
EOF
chmod +x "$TMP_DIR/bin/curl"

# Фикстуры: «бандл» с vendor/PySide6 и SHA256SUMS к нему.
mkdir -p "$TMP_DIR/vendor_stage/vendor/PySide6/Qt/qml/QtQuick"
: > "$TMP_DIR/vendor_stage/vendor/PySide6/__init__.py"
tar czf "$TMP_DIR/pyside6_vendor.tar.gz" -C "$TMP_DIR/vendor_stage" vendor
VENDOR_SHA="$(sha256sum "$TMP_DIR/pyside6_vendor.tar.gz" | awk '{print $1}')"
printf '%s  pyside6_vendor.tar.gz\n' "$VENDOR_SHA" > "$TMP_DIR/SHA256SUMS"
printf '%s  pyside6_vendor.tar.gz\n' "0000000000000000000000000000000000000000000000000000000000000000" \
    > "$TMP_DIR/SHA256SUMS.bad"

# Драйвер: подменяет run_sudo и python3, логирует вызовы, подключает извлечённые функции.
cat > "$TMP_DIR/driver.sh" <<'EOF'
#!/bin/bash
set -u
CALLS="$1"
COMMAND="$2"
: > "$CALLS"
export CALLS

run_sudo() {
    printf 'run_sudo: %s\n' "$*" >> "$CALLS"
    case "$*" in
        "pacman -Si "*) return "${STUB_PACMAN_SI_RC:-0}" ;;
        "pacman -S --noconfirm "*) return "${STUB_PACMAN_INSTALL_RC:-0}" ;;
        "pacman-key "*) return "${STUB_PACMAN_KEY_RC:-0}" ;;
        "pacman -Sy") return "${STUB_PACMAN_SY_RC:-0}" ;;
        "rm -f "*) return 0 ;;
        *) return 0 ;;
    esac
}

python3() {
    printf 'python3: %s\n' "$*" >> "$CALLS"
    case "$*" in
        "-m pip --version") return "${STUB_PIP_VERSION_RC:-0}" ;;
        "-m pip install "*) return "${STUB_PIP_INSTALL_RC:-0}" ;;
        "-c import "*) return "${STUB_IMPORT_RC:-0}" ;;
        *) return 0 ;;
    esac
}

# shellcheck source=/dev/null
source "$STEAMOS_HELPERS"
# shellcheck source=/dev/null
source "$PYSIDE6_HELPERS"

PKG_MANAGER="${PKG_MANAGER:-pacman}"
PKG_UPDATE="${PKG_UPDATE:-pacman -Sy}"
PKG_INSTALL="${PKG_INSTALL:-pacman -S --noconfirm}"
QML_PKGS="${QML_PKGS:-pyside6 qt6-declarative}"
SYSTEM_TYPE="${SYSTEM_TYPE:-steamos}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

eval "$COMMAND"
EOF

run_case() {
    # run_case <имя> <os-release> <команда> [переменные окружения...]
    local name="$1"
    local os_release="$2"
    local command="$3"
    shift 3

    local calls="$TMP_DIR/calls-$name.txt"
    ZAPRET_OS_RELEASE="$os_release" \
    ZAPRET_LOG_FILE="$TMP_DIR/log-$name.txt" \
    PATH="$TMP_DIR/bin:$PATH" \
    STEAMOS_HELPERS="$TMP_DIR/steamos_helpers.sh" \
    PYSIDE6_HELPERS="$TMP_DIR/pyside6_helpers.sh" \
    "$@" \
    bash "$TMP_DIR/driver.sh" "$calls" "$command"
}

assert_order() {
    local calls="$1"
    shift
    local expected=("$@")
    local line="" idx=0
    while IFS= read -r line; do
        if [ "$idx" -lt "${#expected[@]}" ] && [ "$line" = "${expected[$idx]}" ]; then
            idx=$((idx + 1))
        fi
    done < "$calls"
    [ "$idx" -eq "${#expected[@]}" ]
}

echo "Стенд установщика (SteamOS + pip-фолбэк)"

# 1. Разблокировка до установки пакета и блокировка после — на SteamOS.
run_case unlock_lock "$TMP_DIR/os-steamos" \
    'steamos_unlock; run_sudo pacman -S --noconfirm zenity; steamos_lock'
if assert_order "$TMP_DIR/calls-unlock_lock.txt" \
    "run_sudo: steamos-readonly disable" \
    "run_sudo: pacman -S --noconfirm zenity" \
    "run_sudo: steamos-readonly enable"; then
    pass "unlock -> установка -> lock, все вызовы через run_sudo (root)"
else
    fail "неверный порядок вызовов: $(tr '\n' ' ' < "$TMP_DIR/calls-unlock_lock.txt")"
fi

# 2. На не-SteamOS разблокировка не выполняется.
run_case non_steamos "$TMP_DIR/os-arch" \
    'steamos_unlock; steamos_lock; steamos_prepare_pacman'
if [ -s "$TMP_DIR/calls-non_steamos.txt" ]; then
    fail "на Arch выполнены привилегированные вызовы: $(tr '\n' ' ' < "$TMP_DIR/calls-non_steamos.txt")"
else
    pass "на не-SteamOS steamos-readonly/pacman не вызываются"
fi

# 3. Подготовка pacman: ключи archlinux и база пакетов.
run_case prepare "$TMP_DIR/os-steamos" 'steamos_prepare_pacman'
if assert_order "$TMP_DIR/calls-prepare.txt" \
    "run_sudo: pacman-key --init" \
    "run_sudo: pacman-key --populate archlinux" \
    "run_sudo: pacman -Sy"; then
    pass "prepare: pacman-key --init/--populate archlinux и pacman -Sy"
else
    fail "prepare: неверная последовательность: $(tr '\n' ' ' < "$TMP_DIR/calls-prepare.txt")"
fi

# 4. Ошибка установки пакета ОС включает pip-фолбэк.
run_case pip_fallback "$TMP_DIR/os-steamos" 'install_pyside6_packages' \
    env STUB_PACMAN_INSTALL_RC=1 STUB_PIP_INSTALL_RC=0 STUB_IMPORT_RC=0
if assert_order "$TMP_DIR/calls-pip_fallback.txt" \
    "run_sudo: pacman -S --noconfirm pyside6 qt6-declarative" \
    "python3: -m pip install --user --upgrade --break-system-packages PySide6-Essentials"; then
    pass "при сбое pacman ставится PySide6 через pip"
else
    fail "pip-фолбэк не сработал: $(tr '\n' ' ' < "$TMP_DIR/calls-pip_fallback.txt")"
fi

# 5. Пакета нет в репозитории — сразу pip, без попытки pacman -S.
run_case pip_no_repo "$TMP_DIR/os-steamos" 'install_pyside6_packages' \
    env STUB_PACMAN_SI_RC=1 STUB_PIP_INSTALL_RC=0 STUB_IMPORT_RC=0
if grep -q "run_sudo: pacman -S --noconfirm pyside6" "$TMP_DIR/calls-pip_no_repo.txt"; then
    fail "при отсутствии пакета в репозитории pacman -S всё равно вызывается"
elif grep -q "python3: -m pip install " "$TMP_DIR/calls-pip_no_repo.txt"; then
    pass "пакет отсутствует в репозитории — установка через pip"
else
    fail "pip-фолбэк не запущен: $(tr '\n' ' ' < "$TMP_DIR/calls-pip_no_repo.txt")"
fi

# 6. Если не помогли ни пакет ОС, ни pip — функция возвращает ошибку.
CALLS="$TMP_DIR/calls-exit.txt"
ZAPRET_OS_RELEASE="$TMP_DIR/os-steamos" \
ZAPRET_LOG_FILE="$TMP_DIR/log-exit.txt" \
PATH="$TMP_DIR/bin:$PATH" \
STEAMOS_HELPERS="$TMP_DIR/steamos_helpers.sh" \
PYSIDE6_HELPERS="$TMP_DIR/pyside6_helpers.sh" \
STUB_PACMAN_INSTALL_RC=1 STUB_PIP_INSTALL_RC=1 STUB_IMPORT_RC=1 \
bash "$TMP_DIR/driver.sh" "$CALLS" 'install_pyside6_packages'
exit_code=$?
if [ "$exit_code" -eq 0 ]; then
    fail "install_pyside6_packages вернула успех при полном провале"
else
    pass "при полном провале install_pyside6_packages возвращает ошибку"
fi

# 7. Prebuilt-бандл: скачивание, SHA256 и импорт проверены — путь к vendor возвращён.
if run_case vendor_ok "$TMP_DIR/os-steamos" \
    'vendor_dir=$(pyside6_vendor_prepare) && [ -d "$vendor_dir/PySide6" ]' \
    env STUB_VENDOR_ARCHIVE="$TMP_DIR/pyside6_vendor.tar.gz" \
        STUB_VENDOR_SUMS="$TMP_DIR/SHA256SUMS" \
        STUB_IMPORT_RC=0; then
    if grep -q "vendor: SHA256 совпал" "$TMP_DIR/log-vendor_ok.txt"; then
        pass "vendor-бандл: SHA256 проверен, vendor/PySide6 распакован"
    else
        fail "vendor-бандл распакован, но SHA256 не проверялся"
    fi
else
    fail "vendor-бандл не подготовился: $(tr '\n' ' ' < "$TMP_DIR/calls-vendor_ok.txt" 2>/dev/null)"
fi

# 8. Несовпавший SHA256 блокирует использование бандла.
run_case vendor_bad_sha "$TMP_DIR/os-steamos" \
    'pyside6_vendor_prepare >/dev/null && exit 1 || exit 0' \
    env STUB_VENDOR_ARCHIVE="$TMP_DIR/pyside6_vendor.tar.gz" \
        STUB_VENDOR_SUMS="$TMP_DIR/SHA256SUMS.bad"
if grep -q "vendor: SHA256 не совпал" "$TMP_DIR/log-vendor_bad_sha.txt"; then
    pass "vendor-бандл с неверным SHA256 отвергнут"
else
    fail "бандл с неверным SHA256 не был отвергнут"
fi

# 9. PYSIDE6_SKIP_VENDOR=true — бандл не скачивается (резервный путь пакет/pip).
run_case vendor_skip "$TMP_DIR/os-steamos" \
    'pyside6_vendor_prepare >/dev/null && exit 1 || exit 0' \
    env PYSIDE6_SKIP_VENDOR=true
if grep -q "^curl:" "$TMP_DIR/calls-vendor_skip.txt"; then
    fail "при PYSIDE6_SKIP_VENDOR=true бандл всё равно скачивается"
else
    pass "PYSIDE6_SKIP_VENDOR=true отключает скачивание бандла"
fi

echo ""
if [ "$FAILED" -ne 0 ]; then
    echo "СТЕНД ПРОВАЛЕН" >&2
    exit 1
fi
echo "СТЕНД ПРОЙДЕН"