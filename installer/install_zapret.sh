#!/bin/bash

set -e

# === УНИВЕРСАЛЬНЫЕ ПЕРЕМЕННЫЕ ===
# При запуске через sudo whoami даёт root; используем SUDO_USER для реального пользователя
if [ -n "${SUDO_USER:-}" ]; then
    CURRENT_USER="$SUDO_USER"
    CURRENT_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    [ -z "$CURRENT_HOME" ] && CURRENT_HOME="/home/$SUDO_USER"
else
    CURRENT_USER="$(whoami)"
    CURRENT_HOME="${HOME:-$(getent passwd "$(whoami)" | cut -d: -f6)}"
fi
SUDO_PASSWORD=""
BACKUP_CREATED=false
BACKUP_DIR=""
REBOOT_REQUIRED=false

# Юнит zapret: /etc — запись на immutable-/usr (Bazzite, Silverblue и т.д.)
ZAPRET_SYSTEMD_UNIT_DIR="/etc/systemd/system"
ZAPRET_SYSTEMD_UNIT_PATH="/etc/systemd/system/zapret.service"
ZAPRET_SYSTEMD_UNIT_PATH_LEGACY="/usr/lib/systemd/system/zapret.service"

# Подсистема WARP (usque): скрипты ставятся сюда, движок usque и аккаунт
# Cloudflare ставит само приложение (согласие с ToS даёт пользователь).
WARP_OPT_DIR="/opt/warp"
WARP_SYSTEMD_UNIT_PATH="/etc/systemd/system/warp.service"

# >>> steamos helpers (стенд tools/check_installer_steamos.sh извлекает этот блок по маркерам)
# Все привилегированные команды — только через run_sudo (root), он определён ниже.
LOG_FILE="${ZAPRET_LOG_FILE:-/tmp/zapret_install.log}"
LAST_SUDO_RC=0

installer_log() {
    printf '%s\n' "$*" >> "$LOG_FILE" 2>/dev/null || true
}

# Только Valve SteamOS: ID=steamos в os-release (как core.platform_info.is_valve_steamos).
# Файл переопределяется ZAPRET_OS_RELEASE — этим пользуется стенд.
is_valve_steamos() {
    local os_release="${ZAPRET_OS_RELEASE:-/etc/os-release}"
    [ -f "$os_release" ] || return 1
    local id_line=""
    id_line=$(grep -E '^ID=' "$os_release" | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]') || true
    [ "$id_line" = "steamos" ]
}

steamos_readonly_available() {
    command -v steamos-readonly >/dev/null 2>&1
}

# Запускает команду от root и пишет её результат в лог. Код возврата — в LAST_SUDO_RC.
steamos_run_logged() {
    local rc=0 out=""
    out=$(run_sudo "$@" 2>&1) || rc=$?
    installer_log "sudo $* (код $rc): $out"
    LAST_SUDO_RC=$rc
    return 0
}

# Разблокирует /usr на Valve SteamOS. Идемпотентно: повторный disable безвреден.
steamos_unlock() {
    is_valve_steamos || return 0
    if ! steamos_readonly_available; then
        installer_log "SteamOS: steamos-readonly не найден, разблокировка невозможна"
        return 1
    fi
    installer_log "SteamOS: steamos-readonly disable (root)"
    if run_sudo steamos-readonly disable >>"$LOG_FILE" 2>&1; then
        installer_log "SteamOS: файловая система разблокирована"
        return 0
    fi
    installer_log "SteamOS: ОШИБКА разблокировки файловой системы"
    return 1
}

# Возвращает /usr в режим только для чтения. Ошибка не прерывает установку.
steamos_lock() {
    is_valve_steamos || return 0
    steamos_readonly_available || return 0
    installer_log "SteamOS: steamos-readonly enable (root)"
    if run_sudo steamos-readonly enable >>"$LOG_FILE" 2>&1; then
        installer_log "SteamOS: файловая система заблокирована"
        return 0
    fi
    installer_log "SteamOS: предупреждение: не удалось заблокировать файловую систему"
    return 1
}

# Подготовка pacman на SteamOS: снятие устаревших блокировок, ключи, база пакетов.
steamos_prepare_pacman() {
    is_valve_steamos || return 0
    local lock
    echo "# SteamOS: подготовка pacman (ключи, база пакетов)..."
    for lock in /usr/lib/holo/pacmandb/db.lck /var/lib/pacman/db.lck /var/lib/pacman/db.lck.lock; do
        [ -e "$lock" ] || continue
        run_sudo rm -f "$lock" >>"$LOG_FILE" 2>&1 || true
        installer_log "SteamOS: удалён устаревший файл блокировки: $lock"
    done
    steamos_run_logged pacman-key --init
    steamos_run_logged pacman-key --populate archlinux
    steamos_run_logged pacman -Sy
    if [ "$LAST_SUDO_RC" -ne 0 ]; then
        echo "# SteamOS: pacman -Sy завершился с ошибкой, подробности в $LOG_FILE"
        return 1
    fi
    return 0
}
# <<< steamos helpers

# >>> sudo credential helpers (стенд tools/check_installer_steamos.sh извлекает этот блок)
# Проверка пароля и прав sudo: SteamOS deck часто без пароля (passwd -S → NP),
# тогда sudo -S не работает и установка пакетов падает. Значения паролей не логируются.
SUDO_PASSWORDLESS=false

# Статус пароля пользователя: P (есть), NP (нет), L (заблокирован), пусто (неизвестно).
user_password_status() {
    local user="$1"
    command -v passwd >/dev/null 2>&1 || return 0
    passwd -S "$user" 2>/dev/null | awk '{print $2}' | head -n 1
}

# sudo работает без пароля (NOPASSWD или кэш timestamp).
is_passwordless_sudo() {
    sudo -n true 2>/dev/null
}

# Вывод sudo означает, что у пользователя нет прав sudo, а не что пароль неверен.
sudo_error_is_rights() {
    local err="${1:-}"
    printf '%s\n' "$err" | grep -Eqi "not in the sudoers|is not allowed to run|not allowed to execute|sudoers file|не в файле sudoers|не имеет прав"
}

# Ввод пароля: Zenity при наличии графической сессии, иначе терминал (read -s).
ask_password() {
    local title="$1"
    local prompt="$2"
    local password=""
    if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        zenity --password --title="$title" --text="$prompt" 2>/dev/null
        return 0
    fi
    printf '%s\n' "$prompt" >&2
    read -r -s -p "Пароль: " password
    printf '\n' >&2
    printf '%s\n' "$password"
}

# Пользователю без пароля: запросить (Zenity или терминал) и задать пароль.
set_missing_user_password() {
    local first second
    first=$(ask_password "Задать пароль администратора" \
        "У пользователя $CURRENT_USER нет пароля. Придумайте пароль администратора:")
    if [ -z "$first" ]; then
        printf 'Пароль не задан.\n' >&2
        return 1
    fi
    second=$(ask_password "Подтверждение пароля" "Повторите пароль администратора:")
    if [ "$first" != "$second" ]; then
        printf 'Пароли не совпадают.\n' >&2
        return 1
    fi
    if printf '%s\n%s\n' "$first" "$first" | passwd "$CURRENT_USER" >>"$LOG_FILE" 2>&1; then
        installer_log "Пароль пользователя $CURRENT_USER задан"
        return 0
    fi
    installer_log "Не удалось задать пароль пользователю $CURRENT_USER"
    printf 'Не удалось задать пароль (подробности: %s).\n' "$LOG_FILE" >&2
    return 1
}

# Инструкция, как выдать пользователю права sudo (Zenity или терминал).
show_sudo_rights_help() {
    local text="Пользователь $CURRENT_USER не имеет прав sudo: установка пакетов и службы невозможна.\n\n"
    text+="Выдайте права от имени root или другого администратора:\n"
    text+="• SteamOS/Arch: sudo usermod -aG wheel $CURRENT_USER, затем в /etc/sudoers (через visudo): %wheel ALL=(ALL) ALL\n"
    text+="• Debian/Ubuntu: sudo usermod -aG sudo $CURRENT_USER (в sudoers уже есть %sudo ALL=(ALL:ALL) ALL)\n"
    text+="• Fedora: sudo usermod -aG wheel $CURRENT_USER\n"
    text+="• Если sudo нет ни у кого: зайдите как root (su -) или в Desktop Mode от другого администратора и выполните команды выше.\n\n"
    text+="После этого перезайдите в систему и запустите установщик снова."
    if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        zenity --error --title="Нет прав sudo" --text="$(printf '%b' "$text")" --no-markup --width=520 2>/dev/null || true
    else
        printf '%b\n' "$text" >&2
        printf 'Нажмите Enter для выхода...' >&2
        [ -t 0 ] && read -r _ || true
    fi
}

# Гарантирует готовые sudo-данные: sudo без пароля, либо пароль в SUDO_PASSWORD.
# Если пароля у пользователя нет — предлагает задать его и проверяет права повторно.
prepare_sudo_credentials() {
    local status err rc attempt=1
    SUDO_PASSWORDLESS=false
    if is_passwordless_sudo; then
        SUDO_PASSWORDLESS=true
        installer_log "sudo работает без пароля (NOPASSWD или кэш)"
        return 0
    fi
    status=$(user_password_status "$CURRENT_USER")
    installer_log "Статус пароля пользователя $CURRENT_USER: ${status:-неизвестно}"
    if [ "$status" = "NP" ] || [ "$status" = "L" ]; then
        printf 'У пользователя %s нет пароля администратора.\n' "$CURRENT_USER" >&2
        if ! set_missing_user_password; then
            printf 'Пароль задать не удалось — установка невозможна.\n' >&2
            show_message "Требуется пароль" "Не удалось задать пароль администратора.\nЗадайте его вручную командой passwd $CURRENT_USER и запустите установщик снова."
            return 1
        fi
        status=$(user_password_status "$CURRENT_USER")
        installer_log "Статус пароля после задания: ${status:-неизвестно}"
    fi
    while [ "$attempt" -le 3 ]; do
        SUDO_PASSWORD=$(ask_password "Требуются права администратора" "Введите пароль sudo для $CURRENT_USER:")
        if [ -z "$SUDO_PASSWORD" ]; then
            printf 'Пароль не введён — установка отменена.\n' >&2
            return 1
        fi
        err=$(printf '%s\n' "$SUDO_PASSWORD" | sudo -S -k true 2>&1 >/dev/null)
        rc=$?
        installer_log "Проверка sudo (код $rc)"
        if [ "$rc" -eq 0 ]; then
            return 0
        fi
        installer_log "sudo: $err"
        if sudo_error_is_rights "$err"; then
            show_sudo_rights_help
            return 1
        fi
        printf 'Неверный пароль (попытка %s из 3).\n' "$attempt" >&2
        attempt=$((attempt + 1))
    done
    printf 'Не удалось получить права администратора.\n' >&2
    return 1
}
# <<< sudo credential helpers

# >>> pyside6 install helpers (стенд tools/check_installer_steamos.sh извлекает этот блок по маркерам)
pyside6_import_ok() {
    python3 -c "import PySide6.QtCore, PySide6.QtGui, PySide6.QtQml, PySide6.QtQuick, PySide6.QtQuickControls2" \
        >>"$LOG_FILE" 2>&1
}

# === Prebuilt-бандл PySide6 (ассет релиза, собирается в CI) ===
VENDOR_BUNDLE_ASSET="${VENDOR_BUNDLE_ASSET:-pyside6_vendor.tar.gz}"
PYSIDE6_VENDOR_READY=false
PYSIDE6_VENDOR_DIR=""

vendor_bundle_url() {
    printf '%s\n' "${ZAPRET_VENDOR_URL:-https://github.com/mops1k/Zapret-DPI-Manager/releases/latest/download/$VENDOR_BUNDLE_ASSET}"
}

# Скачивает URL в файл: curl, при его отсутствии — wget.
# Таймауты небольшие: если GitHub недоступен, установщик быстро перейдёт к резервному пути.
fetch_url() {
    local url="$1"
    local out="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 1 --retry-delay 1 --connect-timeout 10 --max-time 600 -o "$out" "$url"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 15 -t 2 -O "$out" "$url"
    else
        return 1
    fi
}

file_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

# Скачивает prebuilt-бандл PySide6, сверяет SHA256 (если SHA256SUMS доступен),
# распаковывает и проверяет импорт. Печатает путь к vendor-каталогу или ничего.
pyside6_vendor_prepare() {
    local work_dir archive url sums expected actual vendor_dir
    if [ "${PYSIDE6_SKIP_VENDOR:-false}" = true ]; then
        return 1
    fi
    work_dir=$(mktemp -d 2>/dev/null) || return 1
    archive="$work_dir/$VENDOR_BUNDLE_ASSET"
    url=$(vendor_bundle_url)
    echo "# Проверка поставляемого PySide6 (vendor-бандл)..." >&2
    if ! fetch_url "$url" "$archive" >>"$LOG_FILE" 2>&1; then
        installer_log "vendor: не удалось скачать $url"
        rm -rf "$work_dir"
        return 1
    fi
    sums="$work_dir/SHA256SUMS"
    if fetch_url "${url%/*}/SHA256SUMS" "$sums" >>"$LOG_FILE" 2>&1; then
        expected=$(awk -v name="$VENDOR_BUNDLE_ASSET" '$2 == name {print $1; exit}' "$sums" 2>/dev/null)
        if [ -n "$expected" ]; then
            actual=$(file_sha256 "$archive")
            if [ -z "$actual" ] || [ "$expected" != "$actual" ]; then
                installer_log "vendor: SHA256 не совпал (ожидался $expected, получен ${actual:-пусто})"
                rm -rf "$work_dir"
                return 1
            fi
            installer_log "vendor: SHA256 совпал"
        else
            installer_log "vendor: в SHA256SUMS нет записи для $VENDOR_BUNDLE_ASSET"
        fi
    else
        installer_log "vendor: SHA256SUMS недоступен, проверка целостности пропущена"
    fi
    if ! tar -xzf "$archive" -C "$work_dir" >>"$LOG_FILE" 2>&1; then
        installer_log "vendor: не удалось распаковать $archive"
        rm -rf "$work_dir"
        return 1
    fi
    vendor_dir="$work_dir/vendor"
    if [ ! -d "$vendor_dir/PySide6" ]; then
        installer_log "vendor: в архиве нет каталога vendor/PySide6"
        rm -rf "$work_dir"
        return 1
    fi
    if ! PYTHONPATH="$vendor_dir" pyside6_import_ok; then
        installer_log "vendor: импорт PySide6 из бандла не удался"
        rm -rf "$work_dir"
        return 1
    fi
    installer_log "vendor: бандл готов: $vendor_dir"
    printf '%s\n' "$vendor_dir"
}

# Есть ли пакет в репозитории текущего пакетного менеджера.
package_in_repo() {
    local pkg="$1"
    case "$PKG_MANAGER" in
        pacman)
            run_sudo pacman -Si "$pkg" >>"$LOG_FILE" 2>&1
            ;;
        apt)
            apt-cache show "$pkg" >/dev/null 2>&1
            ;;
        dnf|yum)
            "$PKG_MANAGER" info "$pkg" >/dev/null 2>&1
            ;;
        zypper)
            zypper --non-interactive info "$pkg" >/dev/null 2>&1
            ;;
        *)
            return 0
            ;;
    esac
}

# Доставляет pip, если его нет в системе (пакетом ОС, от root).
pyside6_ensure_pip() {
    local python_bin="${PYTHON_BIN:-python3}"
    "$python_bin" -m pip --version >>"$LOG_FILE" 2>&1 && return 0
    echo "# pip не найден, ставим пакет pip через $PKG_MANAGER..."
    case "$PKG_MANAGER" in
        pacman) run_sudo pacman -S --noconfirm python-pip >>"$LOG_FILE" 2>&1 || return 1 ;;
        apt) run_sudo apt install -y python3-pip >>"$LOG_FILE" 2>&1 || return 1 ;;
        dnf) run_sudo dnf install -y python3-pip >>"$LOG_FILE" 2>&1 || return 1 ;;
        yum) run_sudo yum install -y python3-pip >>"$LOG_FILE" 2>&1 || return 1 ;;
        zypper) run_sudo zypper --non-interactive install python3-pip >>"$LOG_FILE" 2>&1 || return 1 ;;
        *) installer_log "pip: неизвестный пакетный менеджер '$PKG_MANAGER'"; return 1 ;;
    esac
    "$python_bin" -m pip --version >>"$LOG_FILE" 2>&1
}

# Резервный путь: PySide6 из Python-менеджера пакетов (pip) в ~/.local.
pyside6_install_with_pip() {
    local python_bin="${PYTHON_BIN:-python3}"
    if ! command -v "$python_bin" >/dev/null 2>&1; then
        installer_log "pip: python3 не найден"
        return 1
    fi
    pyside6_ensure_pip || return 1
    echo "# pip: загрузка PySide6 в ~/.local (несколько минут, до ~400 МБ)..."
    # PEP 668 (Arch/SteamOS, Debian/Ubuntu): установка в ~/.local с явным разрешением.
    if "$python_bin" -m pip install --user --upgrade --break-system-packages PySide6-Essentials >>"$LOG_FILE" 2>&1 \
        && pyside6_import_ok; then
        return 0
    fi
    echo "# pip: пробуем полный пакет PySide6..."
    if "$python_bin" -m pip install --user --upgrade --break-system-packages PySide6 >>"$LOG_FILE" 2>&1 \
        && pyside6_import_ok; then
        return 0
    fi
    echo "# pip: пробуем без --break-system-packages (старые версии pip)..."
    if "$python_bin" -m pip install --user --upgrade PySide6-Essentials >>"$LOG_FILE" 2>&1 \
        && pyside6_import_ok; then
        return 0
    fi
    installer_log "pip: установить PySide6 не удалось, подробности в $LOG_FILE"
    return 1
}

# Ставит PySide6 и QML: пакетом ОС, при неудаче — через pip. Печатает протокол прогресса для show_progress.
install_pyside6_packages() {
    echo "10"
    echo "# Подготовка к установке PySide6 и QML..."

    if [ "$SYSTEM_TYPE" = "immutable" ]; then
        echo "30"
        echo "# Установка PySide6 в слой системы rpm-ostree..."
        local pkg rpm_ok=true
        for pkg in $QML_PKGS; do
            run_sudo rpm -q "$pkg" >>"$LOG_FILE" 2>&1 || rpm_ok=false
        done
        if [ "$rpm_ok" = true ]; then
            echo "100"
            echo "# Пакет $QML_PKGS уже установлен в системе"
            return 0
        fi
        echo "50"
        echo "# Выполнение: rpm-ostree install $QML_PKGS"
        if run_sudo rpm-ostree install $QML_PKGS >>"$LOG_FILE" 2>&1; then
            echo "REBOOT_REQUIRED=true" > /tmp/zapret_pyside6_reboot
            echo "100"
            echo "# Пакет $QML_PKGS добавлен в следующий слой системы, нужна перезагрузка"
            return 0
        fi
        installer_log "rpm-ostree: установка $QML_PKGS не удалась"
        echo "ERROR: ошибка установки через rpm-ostree" >&2
        return 1
    fi

    if is_valve_steamos; then
        steamos_prepare_pacman || true
    fi

    echo "20"
    local pkg repo_ok=true
    for pkg in $QML_PKGS; do
        echo "# Проверка пакета в репозитории: $pkg"
        if ! package_in_repo "$pkg"; then
            repo_ok=false
        fi
    done

    if [ "$repo_ok" = true ]; then
        if [ -n "$PKG_UPDATE" ]; then
            echo "30"
            echo "# Обновление репозиториев ($PKG_MANAGER)..."
            run_sudo $PKG_UPDATE >>"$LOG_FILE" 2>&1 || true
        fi
        echo "50"
        echo "# Установка пакета $QML_PKGS через $PKG_MANAGER..."
        if run_sudo $PKG_INSTALL $QML_PKGS >>"$LOG_FILE" 2>&1 && pyside6_import_ok; then
            echo "100"
            echo "# PySide6 и QML успешно установлены"
            return 0
        fi
        echo "80"
        echo "# Пакеты ОС не дали рабочего PySide6, переходим на pip"
    else
        echo "80"
        echo "# Пакета нет в репозитории, ставим PySide6 через pip"
    fi

    echo "85"
    if pyside6_install_with_pip && pyside6_import_ok; then
        echo "100"
        echo "# PySide6 установлен через pip в домашний каталог"
        return 0
    fi
    echo "ERROR: PySide6 не удалось установить ни пакетом ОС, ни через pip" >&2
    return 1
}
# <<< pyside6 install helpers

# === ПЕРЕМЕННЫЕ ДЛЯ РЕЖИМА ОТОБРАЖЕНИЯ ===
USE_ZENITY=true
ZENITY_INSTALLED=false

# === ФУНКЦИЯ: Установка zenity если нет (пароль уже получен в prepare_sudo_credentials) ===
install_zenity_if_needed() {
    echo "Проверка наличия zenity..."

    if type zenity &> /dev/null; then
        ZENITY_INSTALLED=true
        USE_ZENITY=true
        echo "Zenity найден, используем графический интерфейс"
        return 0
    fi

    echo "Zenity не найден, пробуем установить..."

    # SteamOS: /usr только для чтения — разблокируем на время установки (от root).
    local zenity_unlocked=false
    if is_valve_steamos; then
        if steamos_unlock; then
            zenity_unlocked=true
            echo "SteamOS: файловая система разблокирована для установки zenity"
        else
            echo "SteamOS: не удалось разблокировать файловую систему"
        fi
    fi

    # Пробуем разные способы установки: все команды — от root через run_sudo.
    local installed=false
    if command -v pacman &> /dev/null; then
        echo "Пробуем установить через pacman..."
        if run_sudo pacman -S --noconfirm zenity >>"$LOG_FILE" 2>&1; then
            installed=true
        else
            echo "Не удалось установить через pacman, пробуем другой метод..."
            run_sudo pacman -Syy --noconfirm >>"$LOG_FILE" 2>&1 || true
            run_sudo pacman -S --noconfirm zenity >>"$LOG_FILE" 2>&1 || true
        fi
    fi

    if [ "$installed" != true ] && command -v apt &> /dev/null; then
        echo "Пробуем установить через apt..."
        run_sudo apt update >>"$LOG_FILE" 2>&1 || true
        run_sudo apt install -y zenity >>"$LOG_FILE" 2>&1 && installed=true
    fi

    if [ "$installed" != true ] && command -v dnf &> /dev/null; then
        echo "Пробуем установить через dnf..."
        run_sudo dnf install -y zenity >>"$LOG_FILE" 2>&1 && installed=true
    fi

    if [ "$installed" != true ] && command -v zypper &> /dev/null; then
        echo "Пробуем установить через zypper..."
        run_sudo zypper install -y zenity >>"$LOG_FILE" 2>&1 && installed=true
    fi

    # Возвращаем защиту /usr на SteamOS независимо от результата установки.
    if [ "$zenity_unlocked" = true ]; then
        steamos_lock || true
    fi

    if type zenity &> /dev/null; then
        ZENITY_INSTALLED=true
        USE_ZENITY=true
        echo ""
        echo "Zenity успешно установлен, используем графический интерфейс"
        return 0
    fi
    ZENITY_INSTALLED=false
    USE_ZENITY=false
    echo ""
    echo "Не удалось установить Zenity. Переходим в консольный режим."
    echo "Установка будет продолжена без графического интерфейса."
    echo "Нажмите Enter для продолжения..."
    read
    return 1
}

# === ФУНКЦИЯ: Определение типа системы ===
detect_system_type() {
    echo "Определение типа системы..."

    # Сначала Valve SteamOS (есть pacman, но ID=steamos — не «просто Arch»)
    if is_valve_steamos; then
        SYSTEM_TYPE="steamos"
        PKG_MANAGER="pacman"
        PKG_UPDATE="pacman -Sy"
        PKG_INSTALL="pacman -S --noconfirm"
        QML_PKGS="pyside6 qt6-declarative"
        echo "Обнаружена Valve SteamOS (ID=steamos, pacman)"

    elif command -v pacman &> /dev/null; then
        SYSTEM_TYPE="arch"
        PKG_MANAGER="pacman"
        PKG_UPDATE="pacman -Sy"
        PKG_INSTALL="pacman -S --noconfirm"
        QML_PKGS="pyside6 qt6-declarative"
        echo "Обнаружена Arch Linux система (pacman)"

    elif [ -f /run/ostree-booted ] && command -v rpm-ostree &> /dev/null; then
        SYSTEM_TYPE="immutable"
        PKG_MANAGER="rpm-ostree"
        PKG_UPDATE=""  # rpm-ostree не требует обновления
        PKG_INSTALL="rpm-ostree install"
        QML_PKGS="python3-pyside6 qt6-qtdeclarative"
        echo "Обнаружена ostree+rpm-ostree система (Bazzite, Fedora Atomic и т.д.)"

    elif command -v apt &> /dev/null; then
        SYSTEM_TYPE="debian"
        PKG_MANAGER="apt"
        PKG_UPDATE="apt update -qq"
        PKG_INSTALL="apt install -y"
        QML_PKGS="python3-pyside6.qml qml6-module-qtquick-controls qml6-module-qtquick-layouts qml6-module-qtquick-effects"
        echo "Обнаружена Debian/Ubuntu система (apt)"

    elif command -v dnf &> /dev/null; then
        SYSTEM_TYPE="fedora"
        PKG_MANAGER="dnf"
        PKG_UPDATE="dnf check-update"
        PKG_INSTALL="dnf install -y"
        QML_PKGS="python3-pyside6 qt6-qtdeclarative"
        echo "Обнаружена Fedora/RHEL система (dnf)"

    elif command -v yum &> /dev/null; then
        SYSTEM_TYPE="centos"
        PKG_MANAGER="yum"
        PKG_UPDATE="yum check-update"
        PKG_INSTALL="yum install -y"
        QML_PKGS="python3-pyside6 qt6-qtdeclarative"
        echo "Обнаружена CentOS/RHEL система (yum)"

    elif command -v zypper &> /dev/null; then
        SYSTEM_TYPE="suse"
        PKG_MANAGER="zypper"
        PKG_UPDATE="zypper refresh"
        PKG_INSTALL="zypper install -y"
        QML_PKGS="python3-pyside6 qt6-qtdeclarative"
        echo "Обнаружена openSUSE система (zypper)"

    else
        SYSTEM_TYPE="unknown"
        PKG_MANAGER="unknown"
        PKG_UPDATE=""
        PKG_INSTALL=""
        QML_PKGS=""
        echo "Не удалось определить тип системы"
        return 1
    fi

    return 0
}

# === ФУНКЦИЯ: Проверка базовых зависимостей (python3, sudo, systemd, nft/iptables, wget/curl) ===
ensure_base_dependencies() {
    echo "Проверка базовых зависимостей..."

    local missing=()
    command -v python3 >/dev/null 2>&1 || missing+=("python3")
    command -v sudo >/dev/null 2>&1 || missing+=("sudo")
    command -v systemctl >/dev/null 2>&1 || missing+=("systemd")
    if ! command -v nft >/dev/null 2>&1 && ! command -v iptables >/dev/null 2>&1; then
        missing+=("nftables")
    fi
    command -v wget >/dev/null 2>&1 || missing+=("wget")
    command -v curl >/dev/null 2>&1 || missing+=("curl")

    if [ "${#missing[@]}" -eq 0 ]; then
        echo "Базовые зависимости на месте"
        return 0
    fi

    echo "Не хватает: ${missing[*]}"
    if [ -z "$PKG_INSTALL" ]; then
        echo "Не удалось определить пакетный менеджер — установите вручную: ${missing[*]}" >&2
        return 1
    fi

    if [ -z "$SUDO_PASSWORD" ] && [ "$SUDO_PASSWORDLESS" != true ]; then
        SUDO_PASSWORD=$(ask_password "Требуются права администратора" "Введите пароль sudo для установки зависимостей:")
        if [ -z "$SUDO_PASSWORD" ]; then
            echo "Пароль не введён — установка зависимостей пропущена" >&2
            return 1
        fi
    fi

    # SteamOS: /usr только для чтения — установка пакетов только после разблокировки (от root).
    local steamos_unlocked=false
    if is_valve_steamos; then
        if steamos_unlock; then
            steamos_unlocked=true
            steamos_prepare_pacman || true
        else
            echo "SteamOS: не удалось разблокировать файловую систему — пакеты не установятся" >&2
            return 1
        fi
    fi

    local result=1
    if [ -n "$PKG_UPDATE" ]; then
        run_sudo $PKG_UPDATE >>"$LOG_FILE" 2>&1 || true
    fi
    if run_sudo $PKG_INSTALL "${missing[@]}" >>"$LOG_FILE" 2>&1; then
        echo "Зависимости установлены: ${missing[*]}"
        result=0
    else
        echo "Не удалось установить: ${missing[*]} (подробности: $LOG_FILE)" >&2
    fi

    if [ "$steamos_unlocked" = true ]; then
        steamos_lock || true
    fi
    return "$result"
}

# === ФУНКЦИЯ: Проверка и установка PySide6 (QML) ===
install_pyside6_if_needed() {
    echo "Проверка наличия PySide6 и QML (GUI Python)..."

    if pyside6_import_ok; then
        echo "PySide6 найден"
        return 0
    fi

    # Приоритет — поставляемый prebuilt-бандл: системные пакеты и /usr не трогаем вообще.
    local vendor_dir=""
    if vendor_dir=$(pyside6_vendor_prepare); then
        PYSIDE6_VENDOR_READY=true
        PYSIDE6_VENDOR_DIR="$vendor_dir"
        echo "Используется поставляемый PySide6 (vendor-бандл)"
        return 0
    fi
    PYSIDE6_VENDOR_READY=false

    if [ -z "$SYSTEM_TYPE" ]; then
        detect_system_type || true
    fi

    if [ "$SYSTEM_TYPE" = "immutable" ]; then
        local pkg all_installed=true
        for pkg in $QML_PKGS; do
            rpm -q "$pkg" &>/dev/null || all_installed=false
        done
        if [ "$all_installed" = true ]; then
            echo "Пакет $QML_PKGS уже установлен в системе"
            return 0
        fi
        echo "Пакет $QML_PKGS не найден, требуется установка"
    fi

    if ! detect_system_type; then
        local manual_text="Не удалось определить тип системы.\n\nУстановите PySide6 и QML-модули вручную:\n• Debian/Ubuntu: sudo apt install python3-pyside6.qml qml6-module-qtquick-controls qml6-module-qtquick-layouts qml6-module-qtquick-effects\n• Fedora: sudo dnf install python3-pyside6 qt6-qtdeclarative\n• Arch/SteamOS: sudo pacman -S pyside6 qt6-declarative\n• openSUSE: sudo zypper install python3-pyside6\n• Bazzite/Fedora Silverblue: sudo rpm-ostree install python3-pyside6 qt6-qtdeclarative\n• Любая система: python3 -m pip install --user PySide6\n\nЗатем снова запустите скрипт установки."

        if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
            local formatted_text
            formatted_text=$(printf "%b" "$manual_text")
            zenity --error --title="Ошибка определения системы" --text="$formatted_text" --no-markup --width=400 2>/dev/null || true
        else
            echo -e "$manual_text"
            echo "Нажмите Enter для продолжения..."
            read
        fi
        return 1
    fi

    local install_message="Для графического интерфейса Zapret DPI Manager требуются PySide6 и QML-модули Qt (Python).\n\n"
    install_message+="Обнаружена система: $SYSTEM_TYPE\n"
    install_message+="Пакетный менеджер: $PKG_MANAGER\n"
    install_message+="Пакет для установки: $QML_PKGS\n\n"
    install_message+="Если пакета нет в репозитории или установка не удастся, PySide6 будет установлен через pip в домашний каталог."

    if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
        local formatted_msg
        formatted_msg=$(printf "%b" "$install_message")
        zenity --info --title="Установка PySide6" --text="$formatted_msg" --no-markup --width=400 2>/dev/null || true
    else
        echo -e "$install_message"
    fi

    # Пароль и права sudo уже проверены в prepare_sudo_credentials.

    # SteamOS: /usr только для чтения — разблокируем ФС на время установки пакетов (от root).
    local steamos_unlocked=false
    if is_valve_steamos; then
        if steamos_unlock; then
            steamos_unlocked=true
            echo "SteamOS: файловая система разблокирована для установки PySide6"
        else
            echo "SteamOS: не удалось разблокировать файловую систему"
        fi
    fi

    local install_status=0
    set +e
    install_pyside6_packages 2>&1 | show_progress "Установка PySide6" "Установка компонентов GUI..."
    install_status=${PIPESTATUS[0]}
    set -e

    if [ -f /tmp/zapret_pyside6_reboot ]; then
        REBOOT_REQUIRED=true
        rm -f /tmp/zapret_pyside6_reboot
        if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
            zenity --info \
                --title="⚠️ Требуется перезагрузка" \
                --text="PySide6 добавлен в систему.\n\nПосле завершения установки Zapret DPI Manager НЕ ЗАБУДЬТЕ ПЕРЕЗАГРУЗИТЬ СИСТЕМУ, иначе программа не сможет запуститься." \
                --width=450 \
                2>/dev/null || true
        else
            echo ""
            echo "⚠️  ВНИМАНИЕ: для завершения установки PySide6 потребуется перезагрузка системы!"
            echo ""
            sleep 3
        fi
    fi

    # Возвращаем защиту /usr независимо от результата.
    if [ "$steamos_unlocked" = true ]; then
        steamos_lock || true
    fi

    if [ "$install_status" -eq 0 ]; then
        echo "PySide6 успешно установлен"
        return 0
    fi

    echo "Ошибка установки PySide6"
    echo "Попробуйте установить вручную:"
    echo "  sudo $PKG_INSTALL $QML_PKGS"
    echo "  python3 -m pip install --user PySide6"
    echo "Подробности: $LOG_FILE"

    if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
        local error_text="Не удалось установить PySide6 автоматически.\n\n"
        error_text+="Тип системы: $SYSTEM_TYPE\n"
        error_text+="Попробуйте установить вручную:\n"
        error_text+="sudo $PKG_INSTALL $QML_PKGS\n"
        error_text+="или: python3 -m pip install --user PySide6\n\n"
        error_text+="Подробности: $LOG_FILE"

        local formatted_error
        formatted_error=$(printf "%b" "$error_text")
        zenity --error --title="Ошибка установки PySide6" --text="$formatted_error" --no-markup --width=400 2>/dev/null || true
    else
        echo "Нажмите Enter для продолжения..."
        read
    fi
    return 1
}

# === АДАПТИВНЫЕ ФУНКЦИИ ОТОБРАЖЕНИЯ ===
show_message() {
    local title="$1"
    local text="$2"

    if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
        # Используем printf для обработки \n символов
        local formatted_text
        formatted_text=$(printf "%b" "$text")
        zenity --info --title="$title" --text="$formatted_text" --no-markup --width=400 2>/dev/null || true
    else
        echo ""
        echo "=== $title ==="
        # В консоли \n обрабатываются автоматически
        echo -e "$text"
        echo ""
        echo "Нажмите Enter для продолжения..."
        read
    fi
}

show_error() {
    local title="$1"
    local text="$2"

    if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
        zenity --error --title="$title" --text="$text" --no-markup --width=400 2>/dev/null || true
    else
        echo ""
        echo "!!! ОШИБКА: $title !!!" >&2
        echo "$text" >&2
        echo ""
    fi
}

show_question() {
    local title="$1"
    local text="$2"
    local ok_label="${3:-OK}"
    local cancel_label="${4:-Отмена}"

    if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
        zenity --question --title="$title" --text="$text" \
               --ok-label="$ok_label" --cancel-label="$cancel_label" \
               --no-markup --width=400 --height=150 2>/dev/null
        return $?
    else
        echo ""
        echo "??? $title ???"
        echo "$text"
        echo ""
        while true; do
            read -p "($ok_label/$cancel_label) [Y/n]: " answer
            case "$answer" in
                [Yy]*|"" ) return 0 ;;
                [Nn]* ) return 1 ;;
                * ) echo "Пожалуйста, введите Y (да) или N (нет)" ;;
            esac
        done
    fi
}

show_progress() {
    local title="$1"
    local text="$2"

    if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
        zenity --progress \
               --title="$title" \
               --text="$text" \
               --percentage=0 \
               --auto-close \
               --no-cancel \
               --width=400 2>/dev/null
    else
        # Консольный режим - показываем шаги по мере их выполнения
        echo ""
        echo ">>> $title <<<"
        echo ">>> $text"
        echo ""

        # Читаем из stdin и показываем прогресс
        local line
        while IFS= read -r line; do
            if [[ "$line" =~ ^([0-9]+)$ ]]; then
                echo "Прогресс: ${BASH_REMATCH[1]}%"
            elif [[ "$line" =~ ^#[[:space:]]*(.+)$ ]]; then
                echo "> ${BASH_REMATCH[1]}"
            fi
        done

        echo ""
        echo ">>> Завершено"
        echo ""
    fi
}

# === ФУНКЦИЯ: Получение sudo доступа (совместимость; основную проверку делает prepare_sudo_credentials) ===
get_sudo_access() {
    if [ -n "$SUDO_PASSWORD" ] || [ "$SUDO_PASSWORDLESS" = true ]; then
        return 0
    fi
    if sudo -n true 2>/dev/null; then
        SUDO_PASSWORDLESS=true
        return 0
    fi
    prepare_sudo_credentials
}

# === ФУНКЦИЯ: Выполнение команд с sudo ===
run_sudo() {
    if [ -n "$SUDO_PASSWORD" ]; then
        echo "$SUDO_PASSWORD" | sudo -S "$@"
    else
        sudo "$@"
    fi
}

# === ФУНКЦИЯ: Запись файла с sudo ===
write_file_with_sudo() {
    local file_path="$1"
    local content="$2"
    local temp_file=$(mktemp)
    echo "$content" > "$temp_file"
    run_sudo cp "$temp_file" "$file_path"
    rm -f "$temp_file"
}

# Запуск GUI в новом сеансе: закрытие окна терминала установщика не шлёт SIGHUP процессу Python
launch_zapret_gui() {
    local dir="$1"
    local script="$2"

    [ -n "$dir" ] && [ -n "$script" ] && [ -d "$dir" ] && [ -f "$dir/$script" ] || return 1

    if command -v setsid >/dev/null 2>&1 && setsid -f true 2>/dev/null; then
        setsid -f sh -c 'cd "$1" && exec python3 "$2"' _ "$dir" "$script" </dev/null >/dev/null 2>&1
        return 0
    fi

    (
        cd "$dir" || exit 1
        nohup python3 "$script" </dev/null >/dev/null 2>&1 &
        disown -h 2>/dev/null || true
    )
}

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
check_for_old_files() {
    OLD_ZAPRET_DIR="$CURRENT_HOME/zapret"
    if [ -d "$OLD_ZAPRET_DIR" ]; then
        echo "Старые файлы найдены: $OLD_ZAPRET_DIR"
        return 0
    else
        echo "Старые файлы не найдены"
        return 1
    fi
}

show_backup_dialog() {
    if show_question "Обнаружены старые файлы" \
        "Обнаружены файлы от старой версии Zapret.\n\nЧто вы хотите сделать?" \
        "Сделать резервную копию" "Пропустить"; then
        echo "Пользователь выбрал: Сделать резервную копию"
        return 0
    else
        echo "Пользователь выбрал: Пропустить"
        return 1
    fi
}

create_backup() {
    local backup_dir="$CURRENT_HOME/zapret_backup_$(date +%Y%m%d_%H%M%S)"
    OLD_OPT_ZAPRET="/opt/zapret"

    echo "Создание резервной копии в: $backup_dir"
    mkdir -p "$backup_dir"
    BACKUP_CREATED=true
    BACKUP_DIR="$backup_dir"

    local backed_up_files=()

    if [ -f "$OLD_OPT_ZAPRET/autohosts.txt" ]; then
        cp "$OLD_OPT_ZAPRET/autohosts.txt" "$backup_dir/" 2>/dev/null && \
            backed_up_files+=("autohosts.txt")
    fi

    if [ -f "$OLD_OPT_ZAPRET/ignore.txt" ]; then
        cp "$OLD_OPT_ZAPRET/ignore.txt" "$backup_dir/" 2>/dev/null && \
            backed_up_files+=("ignore.txt")
    fi

    if [ ${#backed_up_files[@]} -gt 0 ]; then
        local files_list=$(printf "• %s\n" "${backed_up_files[@]}")
        show_message "Резервная копия создана" \
            "Резервная копия создана в:\n$backup_dir\n\nСохраненные файлы:\n$files_list\n\nПосле установки:\n1. Откройте 'Настройки Hosts' в Zapret DPI Manager\n2. Перенесите данные:\n   • Из autohosts.txt в other2.txt\n   • Из ignore.txt в ignore.txt"
    else
        echo "Нет файлов для резервного копирования"
    fi

    return 0
}

remove_old_files() {
    echo "Начинаем удаление старых файлов..."

    local temp_script=$(mktemp)

    cat > "$temp_script" << EOF
#!/bin/bash
# Скрипт запускается через run_sudo — то есть уже от root: steamos-readonly ниже
# не требует sudo, но и не должен вызываться иначе.
echo "10"
echo "# Разблокировка системы SteamOS..."
steamos-readonly disable 2>/dev/null || true
sleep 0.5

echo "30"
echo "# Остановка службы zapret..."
systemctl disable zapret 2>/dev/null || true
systemctl stop zapret 2>/dev/null || true
sleep 0.5

echo "50"
echo "# Удаление файла службы..."
rm -f "$ZAPRET_SYSTEMD_UNIT_PATH" "$ZAPRET_SYSTEMD_UNIT_PATH_LEGACY" 2>/dev/null || true
sleep 0.5

echo "70"
echo "# Удаление папки /opt/zapret..."
rm -r /opt/zapret 2>/dev/null || true
sleep 0.5

echo "90"
echo "# Удаление старых пользовательских файлов..."
rm -r "$CURRENT_HOME/zapret" 2>/dev/null || true
rm -r "$CURRENT_HOME/Desktop/Zapret-DPI.desktop" 2>/dev/null || true
sleep 0.5

echo "95"
echo "# Блокировка системы..."
steamos-readonly enable 2>/dev/null || true
sleep 0.5

echo "100"
echo "# Удаление старых файлов завершено"
EOF

    chmod +x "$temp_script"

    if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
        run_sudo bash "$temp_script" 2>&1 | \
        show_progress "Удаление старых файлов" "Подготовка к установке новой версии..."
    else
        echo ">>> Удаление старых файлов <<<"
        run_sudo bash "$temp_script" 2>&1 | \
        while IFS= read -r line; do
            if [[ "$line" =~ ^([0-9]+)$ ]]; then
                echo "Прогресс: ${BASH_REMATCH[1]}%"
            elif [[ "$line" =~ ^#[[:space:]]*(.+)$ ]]; then
                echo "> ${BASH_REMATCH[1]}"
            fi
        done
    fi

    rm -f "$temp_script"
    run_sudo systemctl daemon-reload 2>/dev/null || true
    echo "Старые файлы удалены"
    return 0
}

check_old_versions() {
    echo "Проверка наличия старых версий..."

    if ! check_for_old_files; then
        echo "Старые файлы не найдены, пропускаем"
        BACKUP_CREATED=false
        return 0
    fi

    echo "Старые файлы найдены"
    if ! show_backup_dialog; then
        echo "Пользователь выбрал пропустить бэкап"
        BACKUP_CREATED=false
        remove_old_files
        return 0
    fi

    echo "Пользователь выбрал сделать бэкап"
    create_backup
    remove_old_files
    return 0
}

show_final_message() {
    local backup_dir="$1"
    local backup_created="$2"

    if [ "$backup_created" = true ] && [ -n "$backup_dir" ] && [ -d "$backup_dir" ]; then
        show_message "Установка завершена" \
            "Установка Zapret DPI Manager 2.0 завершена!\n\n✅ Установлены компоненты:\n• Zapret DPI service\n• Zapret DPI Manager GUI\n• Ярлыки на рабочем столе\n\n🚀 Запуск программы:\n• Ярлык на рабочем столе\n• Меню приложений\n• Терминал: cd ~/Zapret_DPI_Manager && python3 main.py\n\n📁 Ваши старые настройки сохранены в:\n$backup_dir\n\n📝 Для восстановления настроек:\n1. Откройте 'Настройки Hosts' в Zapret DPI Manager\n2. Перенесите данные:\n   • Из autohosts.txt перенесите в other2.txt\n   • Из ignore.txt перенесите в ignore.txt\n\n❌ Удаление программы:\nОткройте Zapret DPI Manager -> Настройки -> 'Удалить Zapret'"
    else
        show_message "Установка завершена" \
            "Установка Zapret DPI Manager 2.0 завершена!\n\n✅ Установлены компоненты:\n• Zapret DPI service\n• Zapret DPI Manager GUI\n• Ярлыки на рабочем столе\n\n🚀 Запуск программы:\n• Ярлык на рабочем столе\n• Меню приложений\n• Терминал: cd ~/Zapret_DPI_Manager и python3 main.py\n\n❌ Удаление программы:\nОткройте Zapret DPI Manager -> Настройки -> 'Удалить Zapret'"
    fi
}

# === ОСНОВНАЯ ПРОГРАММА ===
exec 3>&1
exec 1> >(tee -a /tmp/zapret_install.log)
exec 2>&1

echo "=== Zapret DPI Manager установка начата: $(date) ==="
echo "Текущий пользователь: $CURRENT_USER"
echo "Домашняя директория: $CURRENT_HOME"

# Проверка пароля и прав sudo до всех привилегированных шагов:
# на SteamOS у пользователя deck пароля обычно нет, без этого установка пакетов падает.
if ! prepare_sudo_credentials; then
    show_error "Установка отменена" "Нет прав администратора — установка невозможна."
    exit 1
fi

# Пытаемся установить zenity если нет (необязательный шаг — установку не прерываем)
install_zenity_if_needed || true
# Пытаемся установить zenity если нет (необязательный шаг — установку не прерываем)
install_zenity_if_needed || true

# Целевой каталог нужен уже на этапе зависимостей: туда кладётся vendor-бандл PySide6.
TARGET_DIR="$CURRENT_HOME/Zapret_DPI_Manager"

# Проверка и при необходимости установка PySide6 (QML); для rpm-ostree — установка в слой
install_pyside6_if_needed || true

# Базовые зависимости: python3, sudo, systemd, nft/iptables, wget/curl
ensure_base_dependencies || true

# Приветственное окно
show_message "Zapret DPI Manager 2.0" \
    "Добро пожаловать в установку Zapret DPI Manager 2.0!\nЭта программа установит:\n• Zapret DPI сервис\n• Графический интерфейс управления\n• Ярлыки на рабочем столе\n\nНажмите OK для продолжения."

# Запрашиваем sudo доступ
echo "Запрос sudo доступа..."
get_sudo_access
echo "Sudo доступ получен"

# Проверяем и удаляем старые версии
OLD_ZAPRET_DIR="$CURRENT_HOME/zapret"
if [ -d "$OLD_ZAPRET_DIR" ]; then
    echo "Найдена старая папка zapret: $OLD_ZAPRET_DIR"
    check_old_versions
    echo "BACKUP_CREATED: $BACKUP_CREATED"
    echo "BACKUP_DIR: $BACKUP_DIR"
fi

# Проверяем наличие старой папки Zapret DPI Manager
if [ -d "$TARGET_DIR" ]; then
    echo "Найдена старая папка Zapret DPI Manager: $TARGET_DIR"

    if show_question "Обнаружена старая версия" \
        "Обнаружена предыдущая версия Zapret DPI Manager.\n\nСоздать резервную копию и установить новую версию?" \
        "Продолжить с резервной копией" "Выйти"; then
        OLD_BACKUP_DIR="${TARGET_DIR}_backup_$(date +%Y%m%d_%H%M%S)"
        echo "Создание резервной копии в: $OLD_BACKUP_DIR"
        if cp -r "$TARGET_DIR" "$OLD_BACKUP_DIR"; then
            # Прежнюю версию удаляем только после успешного скачивания архива (ниже).
            PENDING_REMOVE_DIR="$TARGET_DIR"
            show_message "Резервная копия" "Создана резервная копия:\n$OLD_BACKUP_DIR"
        else
            show_error "Ошибка" "Не удалось создать резервную копию. Установка отменена."
            exit 1
        fi
    else
        show_message "Отменено" "Установка отменена."
        exit 0
    fi
fi

echo "Начинаем основную установку..."

# Основной процесс установки.
# pipefail включаем локально: код конвейера ниже должен отражать результат подshell,
# а не код show_progress (иначе провал внутри подshell маскируется).
set -o pipefail
(
    echo "5"
    echo "# Подготовка к установке..."

    echo "10"
    if is_valve_steamos; then
        echo "# Разблокировка файловой системы SteamOS..."
        # Возвращаем защиту ФС в любом случае, даже при обрыве установки.
        trap 'steamos_lock >/dev/null 2>&1 || true' EXIT
        if ! steamos_unlock; then
            echo "Ошибка разблокировки системы" >&2
            exit 1
        fi
        steamos_prepare_pacman || true
        echo "Система разблокирована"
    else
        echo "# Пропуск разблокировки (не SteamOS)"
    fi

    echo "15"
    echo "# Создание временных файлов..."
    TEMP_DIR=$(mktemp -d)
    chown "$CURRENT_USER:$CURRENT_USER" "$TEMP_DIR"
    echo "Временная директория: $TEMP_DIR"

    echo "20"
    echo "# Скачивание Zapret DPI Manager..."
    ARCHIVE_URL="https://github.com/mops1k/Zapret-DPI-Manager/releases/latest/download/Zapret_DPI_Manager.tar.gz"
    ARCHIVE_PATH="$TEMP_DIR/Zapret_DPI_Manager.tar.gz"

    if ! wget -q -O "$ARCHIVE_PATH" "$ARCHIVE_URL"; then
        echo "Ошибка скачивания архива" >&2
        exit 1
    fi
    echo "Архив скачан: $ARCHIVE_PATH"

    echo "25"
    echo "# Проверка скачанных файлов..."
    if ! tar -tzf "$ARCHIVE_PATH" > /dev/null 2>&1; then
        echo "Архив поврежден" >&2
        exit 1
    fi

    echo "30"
    if [ -n "${PENDING_REMOVE_DIR:-}" ] && [ -d "$PENDING_REMOVE_DIR" ]; then
        echo "# Удаление прежней версии (резервная копия уже создана)..."
        rm -rf "$PENDING_REMOVE_DIR"
    fi
    echo "# Создание папки программы..."
    mkdir -p "$TARGET_DIR"

    echo "40"
    echo "# Распаковка файлов..."
    if ! tar -xzf "$ARCHIVE_PATH" -C "$TARGET_DIR"; then
        echo "Ошибка распаковки" >&2
        exit 1
    fi

    chown -R "$CURRENT_USER:$CURRENT_USER" "$TARGET_DIR"
    echo "Файлы распакованы в: $TARGET_DIR"

    if [ "${PYSIDE6_VENDOR_READY:-false}" = true ] && [ -n "${PYSIDE6_VENDOR_DIR:-}" ] && [ -d "$PYSIDE6_VENDOR_DIR" ]; then
        echo "45"
        echo "# Установка поставляемого PySide6 (vendor-бандл)..."
        mkdir -p "$TARGET_DIR/vendor"
        if cp -a "$PYSIDE6_VENDOR_DIR/." "$TARGET_DIR/vendor/" >>"$LOG_FILE" 2>&1; then
            chown -R "$CURRENT_USER:$CURRENT_USER" "$TARGET_DIR/vendor" 2>/dev/null || true
            rm -rf "$(dirname "$PYSIDE6_VENDOR_DIR")" 2>/dev/null || true
            echo "# PySide6 (vendor) установлен в $TARGET_DIR/vendor"
        else
            echo "Внимание: не удалось скопировать vendor-бандл — ставим PySide6 пакетом/pip" >&2
            PYSIDE6_VENDOR_READY=false
            PYSIDE6_SKIP_VENDOR=true
            install_pyside6_packages >>"$LOG_FILE" 2>&1 || true
        fi
    fi

    echo "50"
    echo "# Установка системных файлов..."
    SYSTEM_SRC_DIR="$TARGET_DIR/zapret/system"
    if [ -d "$SYSTEM_SRC_DIR" ]; then
        run_sudo mkdir -p /opt/zapret
        if ! run_sudo cp -r "$SYSTEM_SRC_DIR/"* /opt/zapret/; then
            echo "Ошибка копирования системных файлов в /opt/zapret" >&2
            exit 1
        fi
        # Единый источник истины для FWTYPE: nft, если доступен, иначе iptables.
        if command -v nft >/dev/null 2>&1; then
            run_sudo bash -c 'printf "nftables\n" > /opt/zapret/FWTYPE'
        else
            run_sudo bash -c 'printf "iptables\n" > /opt/zapret/FWTYPE'
        fi
        for required in FWTYPE starter.sh stopper.sh; do
            if [ ! -f "/opt/zapret/$required" ]; then
                echo "Не найден обязательный файл службы: /opt/zapret/$required" >&2
                exit 1
            fi
        done
        run_sudo chmod -R o+r /opt/zapret/
        echo "Системные файлы скопированы в /opt/zapret"
    else
        echo "Ошибка: в архиве нет каталога zapret/system" >&2
        exit 1
    fi

    echo "60"
    echo "# Установка исполняемых файлов..."
    arch=$(uname -m)
    echo "Архитектура: $arch"
    case "$arch" in
        x86_64) bin_dir="x86_64" ;;
        i386|i686) bin_dir="x86" ;;
        armv7l|armv6l) bin_dir="arm" ;;
        aarch64) bin_dir="arm64" ;;
        *)
            echo "Неподдерживаемая архитектура: $arch" >&2
            exit 1
            ;;
    esac

    BIN_SRC_DIR="$TARGET_DIR/zapret/bins"
    if [ -d "$BIN_SRC_DIR" ]; then
        BIN_PATH="$BIN_SRC_DIR/$bin_dir/nfqws"
        if [ ! -f "$BIN_PATH" ]; then
            BIN_PATH=$(find "$BIN_SRC_DIR" -name "nfqws" -type f 2>/dev/null | head -1)
        fi
        if [ -n "$BIN_PATH" ] && [ -f "$BIN_PATH" ]; then
            if ! run_sudo cp "$BIN_PATH" /opt/zapret/nfqws; then
                echo "Ошибка копирования nfqws" >&2
                exit 1
            fi
            run_sudo chmod +x /opt/zapret/nfqws
            echo "Бинарник скопирован: $BIN_PATH"
        else
            echo "Ошибка: бинарник nfqws не найден для архитектуры $bin_dir" >&2
            exit 1
        fi
    else
        echo "Ошибка: в архиве нет каталога zapret/bins" >&2
        exit 1
    fi

    echo "70"
    echo "# Очистка временных файлов..."
    ZAPRET_DIR="$TARGET_DIR/zapret"
    if [ -d "$ZAPRET_DIR" ]; then
        ZAPRET_OWNER=$(stat -c '%U' "$ZAPRET_DIR")
        if [ "$ZAPRET_OWNER" = "root" ]; then
            run_sudo rm -rf "$ZAPRET_DIR"
        else
            rm -rf "$ZAPRET_DIR"
        fi
        echo "Временная папка zapret удалена"
    fi

    echo "75"
    echo "# Настройка скриптов..."
    for script in starter.sh stopper.sh; do
        if [ ! -f "/opt/zapret/$script" ]; then
            SCRIPT_PATH="$SYSTEM_SRC_DIR/$script"
            if [ -f "$SCRIPT_PATH" ]; then
                run_sudo cp "$SCRIPT_PATH" "/opt/zapret/$script"
                run_sudo chmod +x "/opt/zapret/$script"
                echo "Скрипт $script установлен"
            fi
        else
            run_sudo chmod +x "/opt/zapret/$script"
        fi
    done

    echo "80"
    echo "# Создание системной службы..."
    SERVICE_CONTENT="[Unit]
Description=zapret
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/zapret
ExecStart=/bin/bash /opt/zapret/starter.sh
ExecStop=/bin/bash /opt/zapret/stopper.sh

[Install]
WantedBy=multi-user.target"

    run_sudo mkdir -p "$ZAPRET_SYSTEMD_UNIT_DIR"
    write_file_with_sudo "$ZAPRET_SYSTEMD_UNIT_PATH" "$SERVICE_CONTENT"

    run_sudo chmod 644 "$ZAPRET_SYSTEMD_UNIT_PATH"
    run_sudo rm -f "$ZAPRET_SYSTEMD_UNIT_PATH_LEGACY" 2>/dev/null || true
    echo "Служба systemd создана: $ZAPRET_SYSTEMD_UNIT_PATH"

    echo "85"
    echo "# Запуск службы zapret..."
    # Запуск службы не должен прерывать установку: даже на свежей установке
    # starter.sh поднимает службу на стратегии по умолчанию General, но сбой
    # старта (нет nftables, права, окружение) не должен обрывать установку при
    # set -e — иначе не создались бы ярлыки и включение автозапуска.
    if ! run_sudo systemctl daemon-reload; then
        echo "# Внимание: systemctl daemon-reload завершился с ошибкой" >&2
    fi
    if run_sudo systemctl start zapret.service; then
        echo "# Служба zapret запущена"
    else
        echo "# Внимание: служба zapret не запустилась на первом старте" >&2
        echo "# Без выбранной стратегии служба работает на встроенной General — причина в другом." >&2
        echo "# Журнал для диагностики: journalctl -u zapret.service -b --no-pager" >&2
    fi
    if run_sudo systemctl enable zapret.service; then
        echo "# Автозапуск службы zapret включён"
    else
        echo "# Внимание: не удалось включить автозапуск zapret.service" >&2
    fi

    echo "88"
    echo "# Установка подсистемы WARP (usque)..."
    # Скрипты и юнит WARP ставятся сразу, чтобы приложение не требовало sudo для
    # их установки. Движок usque (MIT) и регистрацию аккаунта выполняет окно WARP
    # приложения: согласие с условиями Cloudflare WARP даёт пользователь.
    # Как и у службы zapret, сбой этих шагов не должен прерывать установку.
    WARP_SRC_DIR="$TARGET_DIR/warp/system"
    if [ -d "$WARP_SRC_DIR" ]; then
        if run_sudo mkdir -p "$WARP_OPT_DIR" &&
            run_sudo cp -f "$WARP_SRC_DIR/"*.sh "$WARP_OPT_DIR/" &&
            run_sudo chmod 755 "$WARP_OPT_DIR/"*.sh; then
            echo "# Скрипты WARP установлены в $WARP_OPT_DIR"
        else
            echo "# Внимание: не удалось установить скрипты WARP в $WARP_OPT_DIR" >&2
        fi
        if [ -f "$WARP_SRC_DIR/warp.service" ]; then
            if write_file_with_sudo "$WARP_SYSTEMD_UNIT_PATH" "$(cat "$WARP_SRC_DIR/warp.service")" &&
                run_sudo chmod 644 "$WARP_SYSTEMD_UNIT_PATH"; then
                echo "# Служба systemd создана: $WARP_SYSTEMD_UNIT_PATH"
            else
                echo "# Внимание: не удалось создать $WARP_SYSTEMD_UNIT_PATH" >&2
            fi
        fi
        # Служба WARP не включается и не запускается: её включает пользователь в
        # окне WARP после установки движка usque и регистрации аккаунта.
        if ! run_sudo systemctl daemon-reload; then
            echo "# Внимание: systemctl daemon-reload завершился с ошибкой" >&2
        fi
    else
        echo "# Внимание: в архиве нет каталога warp/system — подсистема WARP будет недоступна" >&2
    fi

    sleep 2

    echo "Проверка статуса службы..."
    if run_sudo systemctl is-active zapret.service > /dev/null 2>&1; then
        echo "Служба zapret активна"
    else
        echo "Внимание: служба zapret не активна"
    fi

    echo "90"
    echo "# Создание ярлыков на рабочем столе..."
    MAIN_SCRIPT="$TARGET_DIR/main.py"
    ICON_PATH="$TARGET_DIR/ico/zapret.png"

    # Каталог рабочего стола: сперва XDG-настройка (xdg-user-dir DESKTOP),
    # затем известные имена. Ярлык создаётся при первой установке обязательно,
    # каталог при отсутствии создаётся (в Game Mode его может не быть).
    resolve_desktop_dir() {
        local candidate=""
        # Под sudo xdg-user-dir берёт HOME root и возвращает ~root/Desktop,
        # поэтому при запуске установщика от root спрашиваем реального пользователя.
        local xdg_cmd="xdg-user-dir"
        if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && command -v sudo >/dev/null 2>&1; then
            xdg_cmd="sudo -u $SUDO_USER -H xdg-user-dir"
        fi
        if command -v xdg-user-dir >/dev/null 2>&1; then
            candidate="$($xdg_cmd DESKTOP 2>/dev/null || true)"
        fi
        # Годится только каталог внутри домашнего каталога пользователя:
        # иначе ярлык уедет в /root/Desktop или в чужой домашний каталог.
        # Замыкающая «/» убирается, чтобы XDG_DESKTOP_DIR="$HOME/" не считался каталогом.
        candidate="${candidate%/}"
        case "$candidate" in
            "$CURRENT_HOME"/*) ;;
            *) candidate="" ;;
        esac
        if [ -n "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
        for candidate in "$CURRENT_HOME/Desktop" "$CURRENT_HOME/Рабочий стол" "$CURRENT_HOME/desktop"; do
            if [ -d "$candidate" ]; then
                echo "$candidate"
                return 0
            fi
        done
        echo "$CURRENT_HOME/Desktop"
    }

    # Владелец ярлыка: при запуске от root (sudo) файлы принадлежат root,
    # поэтому после создания отдаём их реальному пользователю.
    fix_shortcut_owner() {
        if [ "$(id -u)" -eq 0 ]; then
            chown "$CURRENT_USER:$CURRENT_USER" "$@" 2>/dev/null || true
        fi
    }

    # Ярлык актуален, если указывает на текущий main.py; иначе перезаписываем.
    shortcut_is_current() {
        local path="$1"
        [ -f "$path" ] && grep -qF "Exec=python3 \"$MAIN_SCRIPT\"" "$path" 2>/dev/null
    }

    if [ -f "$MAIN_SCRIPT" ]; then
        DESKTOP_CONTENT="[Desktop Entry]
Encoding=UTF-8
Version=1.0
Type=Application
Name=Zapret DPI Manager
GenericName=Zapret DPI Manager
Comment=GUI Manager for Zapret DPI
Exec=python3 \"$MAIN_SCRIPT\"
Icon=${ICON_PATH:-}
Categories=Network;
Keywords=zapret;security;privacy;
Terminal=false
StartupNotify=true
StartupWMClass=ZapretDPIManager
MimeType=
X-GNOME-UsesNotifications=true
InitialPreference=9
"

        DESKTOP_DIR="$(resolve_desktop_dir)"
        if [ ! -d "$DESKTOP_DIR" ]; then
            echo "# Каталог рабочего стола отсутствует, создаём: $DESKTOP_DIR"
            mkdir -p "$DESKTOP_DIR"
            fix_shortcut_owner "$DESKTOP_DIR"
        fi

        DESKTOP_PATH="$DESKTOP_DIR/Zapret_DPI_Manager.desktop"
        if [ -d "$DESKTOP_DIR" ]; then
            if shortcut_is_current "$DESKTOP_PATH"; then
                echo "Ярлык уже актуален: $DESKTOP_PATH"
            else
                echo "$DESKTOP_CONTENT" > "$DESKTOP_PATH" 2>/dev/null || true
                echo "Ярлык создан: $DESKTOP_PATH"
            fi
            chmod 755 "$DESKTOP_PATH" 2>/dev/null || true
            fix_shortcut_owner "$DESKTOP_PATH"
            if [ -f "$DESKTOP_PATH" ]; then
                echo "# Ярлык на рабочем столе: $DESKTOP_PATH" >&2
            else
                echo "Внимание: ярлык на рабочем столе не создан: $DESKTOP_PATH" >&2
            fi
        else
            echo "Внимание: не удалось создать каталог рабочего стола: $DESKTOP_DIR" >&2
        fi

        APPS_DIR="$CURRENT_HOME/.local/share/applications"
        APPS_PATH="$APPS_DIR/Zapret_DPI_Manager.desktop"

        mkdir -p "$APPS_DIR"
        if shortcut_is_current "$APPS_PATH"; then
            echo "Ярлык в меню приложений уже актуален: $APPS_PATH"
        else
            echo "$DESKTOP_CONTENT" > "$APPS_PATH" 2>/dev/null || true
            echo "Ярлык в меню приложений создан: $APPS_PATH"
        fi
        chmod 644 "$APPS_PATH" 2>/dev/null || true
        fix_shortcut_owner "$APPS_PATH"
        if [ -f "$APPS_PATH" ]; then
            echo "# Ярлык в меню приложений: $APPS_PATH" >&2
        else
            echo "Внимание: ярлык в меню приложений не создан: $APPS_PATH" >&2
        fi
        if command -v update-desktop-database &> /dev/null; then
            update-desktop-database "$APPS_DIR" 2>/dev/null || true
        fi
    else
        echo "Внимание: основной скрипт не найден: $MAIN_SCRIPT"
    fi

    echo "95"
    echo "# Завершение установки..."
    rm -rf "$TEMP_DIR"
    echo "Временные файлы удалены"

    echo "100"
    if is_valve_steamos; then
        echo "# Блокировка файловой системы SteamOS..."
        steamos_lock || true
        echo "Система заблокирована"
    else
        echo "# Пропуск блокировки (не SteamOS)"
    fi

) | show_progress "Установка Zapret DPI Manager 2.0" "Начинаем установку..."
INSTALL_STATUS=$?
set +o pipefail
# Страховка: защита /usr возвращается, даже если установка прервалась.
if is_valve_steamos; then
    steamos_lock >/dev/null 2>&1 || true
fi
if [ "$INSTALL_STATUS" -ne 0 ]; then
    show_error "Ошибка" "Установка была прервана или произошла ошибка.\n\nПроверьте лог: /tmp/zapret_install.log"
    exit 1
fi

echo "Установка завершена успешно"

# Проверка GUI-зависимостей после установки: без PySide6 и QML-файлов менеджер не запустится.
QML_GUI_OK=false
GUI_IMPORT_PATH="$TARGET_DIR"
if [ -d "$TARGET_DIR/vendor" ]; then
    GUI_IMPORT_PATH="$TARGET_DIR/vendor"
fi
if PYTHONPATH="$GUI_IMPORT_PATH" python3 -c "import PySide6.QtCore, PySide6.QtGui, PySide6.QtQml, PySide6.QtQuick, PySide6.QtQuickControls2" 2>/dev/null; then
    if [ -f "$TARGET_DIR/qml/main.qml" ] && [ -f "$TARGET_DIR/qmlui/app.py" ]; then
        QML_GUI_OK=true
        echo "GUI-зависимости на месте: PySide6 и QML-файлы найдены"
    else
        echo "Внимание: в $TARGET_DIR нет qml/main.qml или qmlui/app.py" >&2
    fi
else
    echo "Внимание: PySide6 не импортируется" >&2
fi
if [ "$QML_GUI_OK" != true ]; then
    show_message "Не хватает зависимостей" \
        "Графический интерфейс может не запуститься.\n\nПроверьте, что установлен PySide6 с QML-модулями:\n• Arch/SteamOS: sudo pacman -S pyside6 qt6-declarative\n• Debian/Ubuntu: sudo apt install python3-pyside6.qml qml6-module-qtquick-controls qml6-module-qtquick-layouts qml6-module-qtquick-effects\n• Fedora: sudo dnf install python3-pyside6 qt6-qtdeclarative\n• Любая система (если пакета нет в репозитории): python3 -m pip install --user PySide6\n• Или скачайте pyside6_vendor.tar.gz из релиза и распакуйте каталог vendor в $TARGET_DIR\n\nПодробности установки: /tmp/zapret_install.log"
fi

# Очистка временных файлов PySide6
rm -f /tmp/zapret_pyside6_reboot 2>/dev/null || true


# Показываем финальное сообщение
show_final_message "$BACKUP_DIR" "$BACKUP_CREATED"

# Диалог перезагрузки (если требовалась, например после rpm-ostree install pyside6)
if [ "$REBOOT_REQUIRED" = true ]; then
    if show_question "Перезапустить систему сейчас?" \
        "Для применения изменений (установленные пакеты, например python3-pyside6) требуется перезагрузка.Перезапустить систему сейчас?" \
        "Да" "Нет"; then
        show_message "Перед перезагрузкой" \
            "После перезагрузки запустите программу с ярлыка на рабочем столе."
        run_sudo reboot
    else
        show_message "Напоминание о перезагрузке" \
            "Для применения изменений необходимо перезагрузить систему.\n\nПосле перезагрузки запустите программу с ярлыка на рабочем столе."
    fi
fi

# Автозапуск GUI
if show_question "Запуск программы" "Запустить Zapret DPI Manager сейчас?" "Да" "Нет"; then
    MAIN_SCRIPT="$TARGET_DIR/main.py"
    if [ -f "$MAIN_SCRIPT" ]; then
        echo "Запуск программы (отдельно от этого терминала)..."
        sleep 1
        launch_zapret_gui "$TARGET_DIR" "main.py"
        sleep 2

        if pgrep -f "python3.*main.py" > /dev/null; then
            echo "Программа запущена успешно"
        else
            show_error "Внимание" "Программа могла не запуститься.\nПопробуйте запустить вручную:\ncd ~/Zapret_DPI_Manager && python3 main.py"
        fi
    else
        ALT_SCRIPT="$TARGET_DIR/main_window.py"
        if [ -f "$ALT_SCRIPT" ]; then
            echo "Найден альтернативный скрипт: $ALT_SCRIPT"
            if show_question "Альтернативный скрипт" "Основной скрипт не найден.\nНайден альтернативный: main_window.py\nЗапустить его?" "Да" "Нет"; then
                launch_zapret_gui "$TARGET_DIR" "main_window.py"
                sleep 2
            fi
        else
            show_error "Внимание" "Основной скрипт не найден.\nПроверьте установку.\n\nПроверьте наличие файлов в:\n$TARGET_DIR/"
        fi
    fi
fi



echo "=== Установка завершена: $(date) ==="
