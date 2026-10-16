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

# Только Valve SteamOS: ID=steamos в /etc/os-release (как core.platform_info.is_valve_steamos)
is_valve_steamos() {
    [ -f /etc/os-release ] || return 1
    local id_line
    id_line=$(grep -E '^ID=' /etc/os-release | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
    [ "$id_line" = "steamos" ]
}

# === ПЕРЕМЕННЫЕ ДЛЯ РЕЖИМА ОТОБРАЖЕНИЯ ===
USE_ZENITY=true
ZENITY_INSTALLED=false

# === ФУНКЦИЯ: Установка zenity если нет ===
install_zenity_if_needed() {
    echo "Проверка наличия zenity..."

    if type zenity &> /dev/null; then
        ZENITY_INSTALLED=true
        USE_ZENITY=true
        echo "Zenity найден, используем графический интерфейс"
        return 0
    fi

    echo "Zenity не найден, пробуем установить..."
    echo ""

    # Сохраняем текущий пароль если он уже есть
    local temp_password="$SUDO_PASSWORD"

    # Если у нас еще нет пароля, запрашиваем его
    if [ -z "$temp_password" ]; then
        echo "Для установки Zenity требуются права администратора"
        echo "Введите ваш пароль sudo:"
        read -s temp_password
        echo ""

        if [ -z "$temp_password" ]; then
            echo "Пароль не введен. Пропускаем установку Zenity."
            ZENITY_INSTALLED=false
            USE_ZENITY=false
            return 1
        fi

        # Проверяем пароль
        if ! echo "$temp_password" | sudo -S true 2>/dev/null; then
            echo "Неверный пароль. Пропускаем установку Zenity."
            ZENITY_INSTALLED=false
            USE_ZENITY=false
            return 1
        fi

        # Сохраняем верный пароль
        SUDO_PASSWORD="$temp_password"
    fi

    echo "Пытаемся установить Zenity..."

    # Пробуем разные способы установки
    local installed=false

    if command -v pacman &> /dev/null; then
        echo "Пробуем установить через pacman..."
        if echo "$temp_password" | sudo -S pacman -S --noconfirm zenity 2>/dev/null; then
            installed=true
        else
            echo "Не удалось установить через pacman, пробуем другой метод..."
            # Пробуем с обновлением базы данных
            echo "$temp_password" | sudo -S pacman -Syy --noconfirm 2>/dev/null || true
            echo "$temp_password" | sudo -S pacman -S --noconfirm zenity 2>/dev/null || true
        fi
    fi

    # Проверяем другие пакетные менеджеры если pacman не сработал
    if ! $installed && command -v apt &> /dev/null; then
        echo "Пробуем установить через apt..."
        echo "$temp_password" | sudo -S apt update && \
        echo "$temp_password" | sudo -S apt install -y zenity 2>/dev/null && installed=true
    fi

    if ! $installed && command -v dnf &> /dev/null; then
        echo "Пробуем установить через dnf..."
        echo "$temp_password" | sudo -S dnf install -y zenity 2>/dev/null && installed=true
    fi

    if ! $installed && command -v zypper &> /dev/null; then
        echo "Пробуем установить через zypper..."
        echo "$temp_password" | sudo -S zypper install -y zenity 2>/dev/null && installed=true
    fi

    # Проверяем, установился ли zenity
    if type zenity &> /dev/null; then
        ZENITY_INSTALLED=true
        USE_ZENITY=true
        echo ""
        echo "Zenity успешно установлен, используем графический интерфейс"
        return 0
    else
        ZENITY_INSTALLED=false
        USE_ZENITY=false
        echo ""
        echo "Не удалось установить Zenity. Переходим в консольный режим."
        echo "Установка будет продолжена без графического интерфейса."
        echo "Нажмите Enter для продолжения..."
        read
        return 1
    fi
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
        TKINTER_PKG="tk"
        echo "Обнаружена Valve SteamOS (ID=steamos, pacman)"

    elif command -v pacman &> /dev/null; then
        SYSTEM_TYPE="arch"
        PKG_MANAGER="pacman"
        PKG_UPDATE="pacman -Sy"
        PKG_INSTALL="pacman -S --noconfirm"
        TKINTER_PKG="tk"
        echo "Обнаружена Arch Linux система (pacman)"

    elif [ -f /run/ostree-booted ] && command -v rpm-ostree &> /dev/null; then
        SYSTEM_TYPE="immutable"
        PKG_MANAGER="rpm-ostree"
        PKG_UPDATE=""  # rpm-ostree не требует обновления
        PKG_INSTALL="rpm-ostree install"
        TKINTER_PKG="python3-tkinter"
        echo "Обнаружена ostree+rpm-ostree система (Bazzite, Fedora Atomic и т.д.)"

    elif command -v apt &> /dev/null; then
        SYSTEM_TYPE="debian"
        PKG_MANAGER="apt"
        PKG_UPDATE="apt update -qq"
        PKG_INSTALL="apt install -y"
        TKINTER_PKG="python3-tk"
        echo "Обнаружена Debian/Ubuntu система (apt)"

    elif command -v dnf &> /dev/null; then
        SYSTEM_TYPE="fedora"
        PKG_MANAGER="dnf"
        PKG_UPDATE="dnf check-update"
        PKG_INSTALL="dnf install -y"
        TKINTER_PKG="python3-tkinter"
        echo "Обнаружена Fedora/RHEL система (dnf)"

    elif command -v yum &> /dev/null; then
        SYSTEM_TYPE="centos"
        PKG_MANAGER="yum"
        PKG_UPDATE="yum check-update"
        PKG_INSTALL="yum install -y"
        TKINTER_PKG="python3-tkinter"
        echo "Обнаружена CentOS/RHEL система (yum)"

    elif command -v zypper &> /dev/null; then
        SYSTEM_TYPE="suse"
        PKG_MANAGER="zypper"
        PKG_UPDATE="zypper refresh"
        PKG_INSTALL="zypper install -y"
        TKINTER_PKG="python3-tkinter"
        echo "Обнаружена openSUSE система (zypper)"

    else
        SYSTEM_TYPE="unknown"
        PKG_MANAGER="unknown"
        PKG_UPDATE=""
        PKG_INSTALL=""
        TKINTER_PKG=""
        echo "Не удалось определить тип системы"
        return 1
    fi

    return 0
}

# === ФУНКЦИЯ: Установка tkinter с отображением прогресса ===
install_tkinter_with_progress() {
    local temp_password="$1"

    # Создаем временный скрипт для установки с прогрессом
    local temp_script=$(mktemp)

    cat > "$temp_script" << EOF
#!/bin/bash
PASSWORD="$1"

# Функция для выполнения команд с sudo
run_sudo() {
    echo "\$PASSWORD" | sudo -S "\$@" 2>/dev/null
}

echo "10"
echo "# Подготовка к установке tkinter..."

SYSTEM_TYPE="$SYSTEM_TYPE"
PKG_MANAGER="$PKG_MANAGER"
PKG_UPDATE="$PKG_UPDATE"
PKG_INSTALL="$PKG_INSTALL"
TKINTER_PKG="$TKINTER_PKG"

echo "20"
echo "# Определен тип системы: \$SYSTEM_TYPE"

# Особый случай для rpm-ostree (иммутабельные системы)
if [ "\$SYSTEM_TYPE" = "immutable" ]; then
    echo "30"
    echo "# Установка tkinter в слой системы rpm-ostree..."
    
    # Проверяем, не установлен ли уже пакет (опционально)
    if run_sudo rpm -q \$TKINTER_PKG &>/dev/null; then
        echo "80"
        echo "# Пакет \$TKINTER_PKG уже установлен"
        echo "100"
        echo "# Готово"
        exit 0
    fi
    
    echo "50"
    echo "# Выполнение: rpm-ostree install \$TKINTER_PKG"
    echo "# Установка пакета \$TKINTER_PKG (займет некоторое время)..."
    
    # Выполняем установку через rpm-ostree
    if run_sudo rpm-ostree install \$TKINTER_PKG; then
        echo "90"
        echo "# Пакет \$TKINTER_PKG добавлен в следующий слой системы"
        echo "# После перезагрузки tkinter станет доступен"
        echo "100"
        echo "# Установка завершена. ТРЕБУЕТСЯ ПЕРЕЗАГРУЗКА!"
        
        # Устанавливаем флаг перезагрузки
        echo "REBOOT_REQUIRED=true" > /tmp/zapret_tkinter_reboot
        exit 0
    else
        # Проверяем, может пакет уже был в очереди
        if run_sudo rpm-ostree status | grep -q "Pending" && run_sudo rpm-ostree status | grep -q "\$TKINTER_PKG" 2>/dev/null; then
            echo "90"
            echo "# Пакет \$TKINTER_PKG уже ожидает установки после перезагрузки"
            echo "100"
            echo "# ТРЕБУЕТСЯ ПЕРЕЗАГРУЗКА!"
            echo "REBOOT_REQUIRED=true" > /tmp/zapret_tkinter_reboot
            exit 0
        else
            echo "ERROR: Ошибка установки через rpm-ostree" >&2
            exit 1
        fi
    fi
fi

# Для обычных систем (не rpm-ostree) - стандартная установка
if [ -n "\$PKG_UPDATE" ]; then
    echo "30"
    echo "# Обновление репозиториев..."
    run_sudo \$PKG_UPDATE
fi

echo "50"
echo "# Установка пакета \$TKINTER_PKG..."
if run_sudo \$PKG_INSTALL \$TKINTER_PKG; then
    echo "80"
    echo "# Пакет успешно установлен"
else
    echo "ERROR: Ошибка установки через \$PKG_MANAGER" >&2
    exit 1
fi

echo "90"
echo "# Проверка установки tkinter..."

# Для обычных систем проверяем import
if python3 -c "import tkinter" 2>/dev/null; then
    echo "100"
    echo "# tkinter успешно установлен!"
    exit 0
else
    # Если не импортируется, но установка прошла - возможно нужен рестарт
    echo "95"
    echo "# Пакет установлен, но для полной активации может потребоваться перезагрузка"
    echo "100"
    echo "# Готово"
    exit 0
fi
EOF

    chmod +x "$temp_script"

    # Запускаем установку с отображением прогресса
    if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
        bash "$temp_script" "$temp_password" 2>&1 | \
        show_progress "Установка tkinter" "Установка компонентов GUI..."

        local pipestatus=${PIPESTATUS[0]}
        rm -f "$temp_script"
        
        # Всегда возвращаем успех для rpm-ostree, даже если import не проверяли
        if [ "$SYSTEM_TYPE" = "immutable" ]; then
            return 0
        else
            return $pipestatus
        fi
    else
        # Консольный режим
        bash "$temp_script" "$temp_password"
        local result=$?
        rm -f "$temp_script"
        
        # Для rpm-ostree игнорируем результат проверки import
        if [ "$SYSTEM_TYPE" = "immutable" ]; then
            return 0
        else
            return $result
        fi
    fi
}

# === ФУНКЦИЯ: Проверка и установка tkinter ===
install_tkinter_if_needed() {
    echo "Проверка наличия tkinter (GUI Python)..."

    # На SteamOS не проверяем и не ставим tkinter
    if is_valve_steamos; then
        echo "Valve SteamOS: проверка tkinter пропущена"
        return 0
    fi

    # Определяем тип системы, если еще не определен
    if [ -z "$SYSTEM_TYPE" ]; then
        detect_system_type || true
    fi

    # Для rpm-ostree систем не проверяем import, просто проверяем наличие пакета
    if [ "$SYSTEM_TYPE" = "immutable" ]; then
        if rpm -q "$TKINTER_PKG" &>/dev/null; then
            echo "Пакет $TKINTER_PKG уже установлен в системе"
            return 0
        else
            echo "Пакет $TKINTER_PKG не найден, требуется установка"
        fi
    else
        # Для обычных систем проверяем import
        if python3 -c "import tkinter" 2>/dev/null; then
            echo "tkinter найден"
            return 0
        fi
    fi

    # Определяем тип системы
    if ! detect_system_type; then
        local manual_text="Не удалось определить тип системы.\n\nУстановите tkinter вручную:\n• Debian/Ubuntu: sudo apt install python3-tk\n• Fedora: sudo dnf install python3-tkinter\n• Arch: sudo pacman -S tk\n• openSUSE: sudo zypper install python3-tkinter\n• Bazzite/Fedora Silverblue: sudo rpm-ostree install python3-tkinter\n\nЗатем снова запустите скрипт установки."

        if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
            # Для Zenity используем printf для форматирования
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

    # Показываем сообщение о необходимости установки
    local install_message="Для графического интерфейса Zapret DPI Manager требуется модуль tkinter (Python).\n\n"
    install_message+="Обнаружена система: $SYSTEM_TYPE\n"
    install_message+="Пакетный менеджер: $PKG_MANAGER\n"
    install_message+="Пакет для установки: $TKINTER_PKG\n\n"
    install_message+="Сейчас будет выполнена автоматическая установка."

    if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
        local formatted_msg
        formatted_msg=$(printf "%b" "$install_message")
        zenity --info --title="Установка tkinter" --text="$formatted_msg" --no-markup --width=400 2>/dev/null || true
    else
        echo -e "$install_message"
    fi

    local temp_password="$SUDO_PASSWORD"

    if [ -z "$temp_password" ]; then
        if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
            SUDO_PASSWORD=$(get_password "Требуются права администратора" \
                "Введите пароль для установки tkinter:")
            temp_password="$SUDO_PASSWORD"
            if [ -z "$temp_password" ]; then
                local cancel_msg="Пароль не введён. Установка tkinter пропущена.\nУстановите пакет вручную и при необходимости перезапустите установку."
                local formatted_cancel
                formatted_cancel=$(printf "%b" "$cancel_msg")
                zenity --info --title="Пропуск" --text="$formatted_cancel" --no-markup --width=400 2>/dev/null || true
                return 1
            fi
            if ! echo "$temp_password" | sudo -S true 2>/dev/null; then
                zenity --error --title="Ошибка" --text="Неверный пароль. Установка tkinter пропущена." --no-markup --width=400 2>/dev/null || true
                return 1
            fi
        else
            echo "Для установки tkinter требуются права администратора"
            echo "Введите ваш пароль sudo:"
            read -s temp_password
            echo ""
            if [ -z "$temp_password" ]; then
                echo "Пароль не введен. Пропускаем установку tkinter."
                return 1
            fi
            if ! echo "$temp_password" | sudo -S true 2>/dev/null; then
                echo "Неверный пароль. Пропускаем установку tkinter."
                return 1
            fi
            SUDO_PASSWORD="$temp_password"
        fi
    fi

    # Устанавливаем tkinter
    if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
        if install_tkinter_with_progress "$temp_password"; then
            # Проверяем флаг перезагрузки для rpm-ostree
            if [ -f /tmp/zapret_tkinter_reboot ]; then
                REBOOT_REQUIRED=true
                rm -f /tmp/zapret_tkinter_reboot

                # Сразу показываем сообщение о перезагрузке
                zenity --info \
                    --title="⚠️ Требуется перезагрузка" \
                    --text="Пакет tkinter добавлен в систему.\n\nПосле завершения установки Zapret DPI Manager НЕ ЗАБУДЬТЕ ПЕРЕЗАГРУЗИТЬ СИСТЕМУ, иначе программа не сможет запуститься." \
                    --width=450 \
                    2>/dev/null
            fi
            return 0
        else
            local error_text="Не удалось установить tkinter автоматически.\n\n"
            error_text+="Тип системы: $SYSTEM_TYPE\n"
            error_text+="Попробуйте установить вручную:\n"
            error_text+="sudo $PKG_INSTALL $TKINTER_PKG"

            local formatted_error
            formatted_error=$(printf "%b" "$error_text")
            zenity --error --title="Ошибка установки tkinter" --text="$formatted_error" --no-markup --width=400 2>/dev/null || true
            return 1
        fi
    else
        # Консольный режим
        echo "Установка tkinter через $PKG_MANAGER..."

        if [ -n "$PKG_UPDATE" ]; then
            echo "Обновление репозиториев..."
            echo "$temp_password" | sudo -S $PKG_UPDATE 2>/dev/null || true
        fi

        echo "Установка пакета $TKINTER_PKG..."
        if echo "$temp_password" | sudo -S $PKG_INSTALL $TKINTER_PKG 2>/dev/null; then
            # Проверяем флаг перезагрузки для rpm-ostree
            if [ -f /tmp/zapret_tkinter_reboot ]; then
                REBOOT_REQUIRED=true
                rm -f /tmp/zapret_tkinter_reboot

                echo ""
                echo "⚠️  ВНИМАНИЕ: Для завершения установки tkinter"
                echo "   потребуется перезагрузка системы!"
                echo ""
                sleep 3
            fi
            echo "tkinter успешно установлен"
            return 0
        else
            echo "Ошибка установки tkinter"
            echo "Попробуйте установить вручную:"
            echo "sudo $PKG_INSTALL $TKINTER_PKG"
            echo ""
            echo "Нажмите Enter для продолжения..."
            read
            return 1
        fi
    fi
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

get_password() {
    local title="$1"
    local text="$2"

    if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
        zenity --password --title="$title" --text="$text" --no-markup 2>/dev/null
    else
        echo ""
        echo ">>> $title <<<"
        echo "$text"
        read -s -p "Пароль: " password
        echo ""
        echo "$password"
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

# === ФУНКЦИЯ: Получение sudo доступа ===
get_sudo_access() {
    if ! sudo -n true 2>/dev/null; then
        while true; do
            if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
                SUDO_PASSWORD=$(get_password "Требуются права администратора" \
                    "Введите пароль для установки Zapret DPI Manager:")

                if [ $? -ne 0 ]; then
                    show_message "Отменено" "Установка Zapret DPI Manager отменена пользователем."
                    exit 0
                fi
            else
                echo ""
                echo "=== ТРЕБУЮТСЯ ПРАВА АДМИНИСТРАТОРА ==="
                echo "Введите пароль для установки Zapret DPI Manager:"
                read -s SUDO_PASSWORD
                echo ""
            fi

            if [ -z "$SUDO_PASSWORD" ]; then
                if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
                    show_error "Ошибка" "Пароль не может быть пустым! Попробуйте еще раз."
                else
                    echo "ОШИБКА: Пароль не может быть пустым! Попробуйте еще раз."
                fi
                continue
            fi

            if echo "$SUDO_PASSWORD" | sudo -S true 2>/dev/null; then
                break
            else
                if [ "$USE_ZENITY" = true ] && [ "$ZENITY_INSTALLED" = true ]; then
                    show_error "Ошибка" "Неверный пароль! Попробуйте еще раз."
                else
                    echo "ОШИБКА: Неверный пароль! Попробуйте еще раз."
                fi
            fi
        done
    fi
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

# Пытаемся установить zenity если нет (необязательный шаг — установку не прерываем)
install_zenity_if_needed || true

# Проверка и при необходимости установка tkinter (кроме SteamOS); для rpm-ostree — установка в слой
install_tkinter_if_needed || true

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
TARGET_DIR="$CURRENT_HOME/Zapret_DPI_Manager"
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
        trap 'run_sudo steamos-readonly enable >/dev/null 2>&1 || true' EXIT
        if ! run_sudo steamos-readonly disable; then
            echo "Ошибка разблокировки системы" >&2
            exit 1
        fi
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
    ARCHIVE_URL="https://github.com/mops1k/Zapret-DPI-for-Steam-Deck/releases/latest/download/Zapret_DPI_Manager.tar.gz"
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
    # Запуск службы не должен прерывать установку: в свежем config.txt ещё нет
    # выбранной стратегии, starter.sh не создаёт правила NFQUEUE и выходит с
    # кодом 1, поэтому systemctl start возвращает ошибку. При set -e установка
    # обрывалась здесь — до создания ярлыков и включения автозапуска.
    if ! run_sudo systemctl daemon-reload; then
        echo "# Внимание: systemctl daemon-reload завершился с ошибкой" >&2
    fi
    if run_sudo systemctl start zapret.service; then
        echo "# Служба zapret запущена"
    else
        echo "# Внимание: служба zapret не запустилась на первом старте" >&2
        echo "# Это нормально без выбранной стратегии: включите службу в Zapret DPI Manager." >&2
    fi
    if run_sudo systemctl enable zapret.service; then
        echo "# Автозапуск службы zapret включён"
    else
        echo "# Внимание: не удалось включить автозапуск zapret.service" >&2
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
        run_sudo steamos-readonly enable
        echo "Система заблокирована"
    else
        echo "# Пропуск блокировки (не SteamOS)"
    fi

) | show_progress "Установка Zapret DPI Manager 2.0" "Начинаем установку..."
INSTALL_STATUS=$?
set +o pipefail
if [ "$INSTALL_STATUS" -ne 0 ]; then
    show_error "Ошибка" "Установка была прервана или произошла ошибка.\n\nПроверьте лог: /tmp/zapret_install.log"
    exit 1
fi

echo "Установка завершена успешно"

# Очистка временных файлов tkinter
rm -f /tmp/zapret_tkinter_reboot 2>/dev/null || true


# Показываем финальное сообщение
show_final_message "$BACKUP_DIR" "$BACKUP_CREATED"

# Диалог перезагрузки (если требовалась, например после rpm-ostree install tkinter)
if [ "$REBOOT_REQUIRED" = true ]; then
    if show_question "Перезапустить систему сейчас?" \
        "Для применения изменений (установленные пакеты, например python3-tkinter) требуется перезагрузка.Перезапустить систему сейчас?" \
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
