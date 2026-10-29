#!/bin/bash
# Строгий режим: ошибка конфигурации или правил не должна давать «работающую» службу.
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit 1
fi

# Определяем пользователя, который установил Zapret DPI Manager
DETECTED_USER=""
CURRENT_HOME=""

# Способ 1: Используем SUDO_USER (если скрипт запущен через sudo)
if [ -n "${SUDO_USER:-}" ]; then
    DETECTED_USER="$SUDO_USER"
    CURRENT_HOME=$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6 || true)
fi

# Способ 2: Проверяем наличие конфигурации у текущего пользователя сессии
if [ -z "$DETECTED_USER" ] || [ ! -f "$CURRENT_HOME/Zapret_DPI_Manager/config.txt" ]; then
    # Пробуем определить реального пользователя
    REAL_USER=$(logname 2>/dev/null || echo "")
    if [ -n "$REAL_USER" ] && [ "$REAL_USER" != "root" ]; then
        home=$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6 || true)
        if [ -n "$home" ] && [ -f "$home/Zapret_DPI_Manager/config.txt" ]; then
            DETECTED_USER="$REAL_USER"
            CURRENT_HOME="$home"
        fi
    fi
fi

# Способ 3: Ищем любого пользователя с установленной программой
if [ -z "$DETECTED_USER" ] || [ ! -f "$CURRENT_HOME/Zapret_DPI_Manager/config.txt" ]; then
    # Ищем пользователей с домашней директорией, где есть config.txt
    while IFS=: read -r username _ uid _ _ home _; do
        # Пропускаем системных пользователей
        if [ "$uid" -ge 1000 ] || [ "$username" = "deck" ]; then
            if [ -n "$home" ] && [ -f "$home/Zapret_DPI_Manager/config.txt" ]; then
                DETECTED_USER="$username"
                CURRENT_HOME="$home"
                echo "Found Zapret installation for user: $username"
                break
            fi
        fi
    done < /etc/passwd
fi

# Способ 4: Используем первого не-root пользователя с home директорией
if [ -z "$DETECTED_USER" ] || [ ! -f "$CURRENT_HOME/Zapret_DPI_Manager/config.txt" ]; then
    # Находим первого не-root пользователя
    while IFS=: read -r username _ uid _ _ home _; do
        if [ "$uid" -ge 1000 ] && [ "$username" != "nobody" ] && [ -n "$home" ]; then
            DETECTED_USER="$username"
            CURRENT_HOME="$home"
            echo "Using user: $username (first non-root user found)"
            break
        fi
    done < /etc/passwd
fi

# Проверяем, что нашли пользователя
if [ -z "$DETECTED_USER" ] || [ -z "$CURRENT_HOME" ]; then
    echo "Error: Cannot determine user for Zapret DPI Manager"
    echo "Please ensure the program is installed for a non-root user"
    exit 1
fi

# Проверяем наличие конфигурационного файла
CONFIG_FILE="$CURRENT_HOME/Zapret_DPI_Manager/config.txt"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Config file not found at $CONFIG_FILE"
    echo "Please ensure Zapret DPI Manager is properly installed"
    echo "You can run the installer again or check the installation"
    exit 1
fi

echo "Using Zapret DPI Manager installation for user: $DETECTED_USER"
echo "Home directory: $CURRENT_HOME"
echo "Config file: $CONFIG_FILE"

if pidof /opt/zapret/nfqws > /dev/null 2>&1; then
    echo "nfqws is already running."
    exit 0
fi

if [ -f /opt/zapret/FWTYPE ]; then
    content=$(cat /opt/zapret/FWTYPE)
    if [ "$content" = "iptables" ]; then
        FWTYPE=iptables
    elif [ "$content" = "nftables" ]; then
        FWTYPE=nftables
    else
        echo "Error: invalid value in file FWTYPE."
        exit 1
    fi
    echo "FWTYPE=$FWTYPE"
else
    echo "Error: File /opt/zapret/FWTYPE not found."
    exit 1
fi

# СОЗДАЕМ ВРЕМЕННУЮ ДИРЕКТОРИЮ ДЛЯ ФАЙЛОВ
TEMP_DIR=$(mktemp -d)
echo "Using temp directory: $TEMP_DIR"

# КОПИРУЕМ ВСЕ НЕОБХОДИМЫЕ ФАЙЛЫ
echo "Copying files to temp directory..."
cp -f "$CURRENT_HOME/Zapret_DPI_Manager/files/lists/"* "$TEMP_DIR/" 2>/dev/null || true
cp -f "$CURRENT_HOME/Zapret_DPI_Manager/files/bin/"* "$TEMP_DIR/" 2>/dev/null || true

# ОБЪЕДИНЯЕМ ОСНОВНЫЕ И USER-СПИСКИ В ОДИН ФАЙЛ
# Это исключает зависимость от порядка повторяющихся --hostlist/--hostlist-exclude/--ipset-exclude.
merge_unique_lists() {
    local out_file="$1"
    shift
    local inputs=("$@")

    : > "$out_file"
    local existing=()
    for file in "${inputs[@]}"; do
        [ -f "$file" ] || continue
        existing+=("$file")
    done

    if [ "${#existing[@]}" -gt 0 ]; then
        awk '
            {
                sub(/\r$/, "");
                if (!seen[$0]++) print $0
            }
        ' "${existing[@]}" > "$out_file"
    fi
}

merge_unique_lists \
    "$TEMP_DIR/list-general_merged.txt" \
    "$TEMP_DIR/list-general.txt" \
    "$TEMP_DIR/list-general_user.txt"
merge_unique_lists \
    "$TEMP_DIR/list-exclude_merged.txt" \
    "$TEMP_DIR/list-exclude.txt" \
    "$TEMP_DIR/list-exclude_user.txt"
merge_unique_lists \
    "$TEMP_DIR/ipset-exclude_merged.txt" \
    "$TEMP_DIR/ipset-exclude.txt" \
    "$TEMP_DIR/ipset-exclude_user.txt"
merge_unique_lists \
    "$TEMP_DIR/ipset-all_merged.txt" \
    "$TEMP_DIR/ipset-all.txt" \
    "$TEMP_DIR/ipset-all_user.txt"
merge_unique_lists \
    "$TEMP_DIR/list-telegram_merged.txt" \
    "$TEMP_DIR/list-telegram.txt" \
    "$TEMP_DIR/list-telegram_user.txt"
merge_unique_lists \
    "$TEMP_DIR/ipset-telegram_merged.txt" \
    "$TEMP_DIR/ipset-telegram.txt" \
    "$TEMP_DIR/ipset-telegram_user.txt"

# ПРОВЕРЯЕМ НАЛИЧИЕ ФАЙЛА gamefilter.enable В ИСХОДНОЙ ПАПКЕ
GAME_FILTER_TCP_VALUE="12"  # значение по умолчанию для TCP
GAME_FILTER_UDP_VALUE="12"  # значение по умолчанию для UDP
GAME_FILTER_FILE="$CURRENT_HOME/Zapret_DPI_Manager/utils/gamefilter.enable"
GAME_FILTER_MODE_FILE="$CURRENT_HOME/Zapret_DPI_Manager/utils/gamefilter.mode"
GAME_FILTER_MODE="both"
if [ -f "$GAME_FILTER_MODE_FILE" ]; then
    GAME_FILTER_MODE=$(head -n 1 "$GAME_FILTER_MODE_FILE" | tr -d '\r\n' | tr '[:upper:]' '[:lower:]' || true)
fi
if [ "$GAME_FILTER_MODE" != "tcp" ] && [ "$GAME_FILTER_MODE" != "udp" ] && [ "$GAME_FILTER_MODE" != "both" ]; then
    GAME_FILTER_MODE="both"
fi

if [ -f "$GAME_FILTER_FILE" ]; then
    echo "Game filter enabled file found. Using game ports range (mode: $GAME_FILTER_MODE)."
    GAME_FILTER_TCP_PORTS="80,443,27000-27100,3074-3076"
    GAME_FILTER_UDP_PORTS="3000-3010,5050-5060,27000-27100,3478-3481,3074-3076,4380,50000-50200,49152-52000"
    if [ "$GAME_FILTER_MODE" = "tcp" ]; then
        GAME_FILTER_TCP_VALUE="$GAME_FILTER_TCP_PORTS"
        GAME_FILTER_UDP_VALUE="12"
    elif [ "$GAME_FILTER_MODE" = "udp" ]; then
        GAME_FILTER_TCP_VALUE="12"
        GAME_FILTER_UDP_VALUE="$GAME_FILTER_UDP_PORTS"
    else
        GAME_FILTER_TCP_VALUE="$GAME_FILTER_TCP_PORTS"
        GAME_FILTER_UDP_VALUE="$GAME_FILTER_UDP_PORTS"
    fi
else
    echo "Game filter enabled file not found. Using default value 12."
fi

# ДАЕМ ПРАВА НА ЧТЕНИЕ
chmod -R a+r "$TEMP_DIR"

# --- WARP (usque) ---------------------------------------------------------
# WARP должен работать одновременно с zapret, поэтому:
#   * интерфейс туннеля исключаем из NFQUEUE: трафик ВНУТРИ туннеля десинку не
#     поддаётся (он уже зашифрован туннелем) и только мешает его работе;
#   * endpoint'ы туннеля (трафик НАРУЖУ туннеля) исключаем из NFQUEUE, если
#     «лечение через zapret» выключено; если включено — для них добавляются
#     профили nfqws перед правилами пользователя (utils/warp_rules.txt).
WARP_ENABLED=0
WARP_IFACE="warp0"
WARP_HELP="0"
WARP_STATE_FILE="$CURRENT_HOME/Zapret_DPI_Manager/utils/warp.txt"
WARP_RULES_FILE="$CURRENT_HOME/Zapret_DPI_Manager/utils/warp_rules.txt"
if [ -f "$WARP_STATE_FILE" ]; then
    while IFS='=' read -r key value || [ -n "$key" ]; do
        value="${value%$'\r'}"
        case "$key" in
            enabled) WARP_ENABLED="$value" ;;
            iface) WARP_IFACE="$value" ;;
            help_tunnel) WARP_HELP="$value" ;;
        esac
    done < "$WARP_STATE_FILE"
fi
case "$WARP_ENABLED" in 1) ;; *) WARP_ENABLED=0 ;; esac
case "$WARP_HELP" in 1) ;; *) WARP_HELP=0 ;; esac
if ! [[ "$WARP_IFACE" =~ ^[A-Za-z0-9_.-]{1,15}$ ]]; then
    echo "Warning: WARP: недопустимое имя интерфейса '$WARP_IFACE' — исключение по интерфейсу отключено." >&2
    WARP_IFACE=""
fi
if [ "$WARP_ENABLED" = "1" ]; then
    echo "WARP: включён (интерфейс ${WARP_IFACE:-не задан}, лечение через zapret: $WARP_HELP)"
fi

# Условия исключения интерфейса туннеля для nftables (пустое имя — без условия).
WARP_IFACE_NFT_OUT=""
WARP_IFACE_NFT_IN=""
if [ -n "$WARP_IFACE" ]; then
    WARP_IFACE_NFT_OUT="oifname != \"$WARP_IFACE\""
    WARP_IFACE_NFT_IN="iifname != \"$WARP_IFACE\""
fi

# Endpoint'ы WARP: список ведёт приложение (files/lists/ipset-warp.txt);
# фактический адрес текущего подключения берём из /run/warp/endpoint.
WARP_EP_V4=""
WARP_EP_V6=""
add_warp_endpoint() {
    local ip="$1"
    [ -n "$ip" ] || return 0
    if [[ "$ip" == *:* ]]; then
        case ",$WARP_EP_V6," in
            *",$ip,"*) ;;
            *) WARP_EP_V6="${WARP_EP_V6:+$WARP_EP_V6,}$ip" ;;
        esac
    else
        case ",$WARP_EP_V4," in
            *",$ip,"*) ;;
            *) WARP_EP_V4="${WARP_EP_V4:+$WARP_EP_V4,}$ip" ;;
        esac
    fi
}
if [ "$WARP_ENABLED" = "1" ]; then
    if [ -f "$TEMP_DIR/ipset-warp.txt" ]; then
        while IFS= read -r ip || [ -n "$ip" ]; do
            ip="${ip%%#*}"
            ip="$(printf '%s' "$ip" | tr -d ' \t\r')"
            add_warp_endpoint "$ip"
        done < "$TEMP_DIR/ipset-warp.txt"
    fi
    if [ -f /run/warp/endpoint ]; then
        add_warp_endpoint "$(head -n 1 /run/warp/endpoint 2>/dev/null | tr -d ' \t\r\n' || true)"
    fi
fi

# Раскрытие плейсхолдеров в правилах WARP. Набор обязан совпадать с шаблонами
# QUIC_PROFILE/HTTP2_PROFILE в core/warp_manager.py; нераскрытый плейсхолдер —
# ошибка конфигурации, а не повод тихо пропустить правило.
EXPANDED_RULE=""
expand_warp_rule() {
    local value="$1"
    value="${value//\{ipset_warp\}/$TEMP_DIR/ipset-warp.txt}"
    value="${value//\{quicgoogle\}/$TEMP_DIR/quic_initial_www_google_com.bin}"
    value="${value//\{tlsgoogle\}/$TEMP_DIR/tls_clienthello_www_google_com.bin}"
    if [[ "$value" == *"{"*"}"* ]]; then
        echo "ERROR: неизвестный плейсхолдер в правилах WARP: $1" >&2
        return 1
    fi
    EXPANDED_RULE="$value"
    return 0
}

ARGS=""
CONFIG_FILE="$CURRENT_HOME/Zapret_DPI_Manager/config.txt"
echo "Reading config from $CONFIG_FILE"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Config file not found at $CONFIG_FILE"
    exit 1
fi

while IFS= read -r line || [[ -n "$line" ]]; do
    # ЗАМЕНЯЕМ ПУТИ НА ВРЕМЕННЫЕ
    line="${line//\{list_general\}/$TEMP_DIR/list-general_merged.txt}"
    line="${line//\{list_exclude\}/$TEMP_DIR/list-exclude_merged.txt}"
    line="${line//\{ipset_exclude\}/$TEMP_DIR/ipset-exclude_merged.txt}"
    line="${line//\{list_google\}/$TEMP_DIR/list-google.txt}"
    line="${line//\{ipset_all\}/$TEMP_DIR/ipset-all_merged.txt}"
    line="${line//\{ipset_all_user\}/$TEMP_DIR/ipset-all_merged.txt}"
    line="${line//\{ipset_exclude_user\}/$TEMP_DIR/ipset-exclude_merged.txt}"
    line="${line//\{list_telegram\}/$TEMP_DIR/list-telegram_merged.txt}"
    line="${line//\{list_telegram_user\}/$TEMP_DIR/list-telegram_merged.txt}"
    line="${line//\{ipset_telegram\}/$TEMP_DIR/ipset-telegram_merged.txt}"
    line="${line//\{ipset_telegram_user\}/$TEMP_DIR/ipset-telegram_merged.txt}"
    line="${line//\{list_general_user\}/$TEMP_DIR/list-general_merged.txt}"
    line="${line//\{list_exclude_user\}/$TEMP_DIR/list-exclude_merged.txt}"
    line="${line//\{gw\}/$TEMP_DIR/gw.txt}"
    line="${line//\{other\}/$TEMP_DIR/other.txt}"
    line="${line//\{quicgoogle\}/$TEMP_DIR/quic_initial_www_google_com.bin}"
    line="${line//\{tlsgoogle\}/$TEMP_DIR/tls_clienthello_www_google_com.bin}"
    line="${line//\{tls4pda\}/$TEMP_DIR/tls_clienthello_4pda_to.bin}"
    line="${line//\{tlsmax\}/$TEMP_DIR/tls_clienthello_max_ru.bin}"
    line="${line//\{tlsmaxui\}/$TEMP_DIR/tls_clienthello_max_ru_ui.bin}"
    line="${line//\{tls5\}/$TEMP_DIR/tls_clienthello_5.bin}"
    line="${line//\{tls11\}/$TEMP_DIR/tls_clienthello_11.bin}"
    line="${line//\{quic6\}/$TEMP_DIR/quic_6.bin}"
    line="${line//\{quic7\}/$TEMP_DIR/quic_7.bin}"
    line="${line//\{httpfake_ms\}/$TEMP_DIR/http_fake_MS.bin}"
    line="${line//\{stun\}/$TEMP_DIR/stun.bin}"
    line="${line//\{stun2\}/$TEMP_DIR/stun2.bin}"
    line="${line//\{tlssochi\}/$TEMP_DIR/tls_clienthello_sochi_park.bin}"
    line="${line//\{quic4pda\}/$TEMP_DIR/quic_initial_4pda_to.bin}"
    line="${line//\{dbankcloud\}/$TEMP_DIR/quic_initial_dbankcloud_ru.bin}"
    line="${line//\{quic5ka\}/$TEMP_DIR/quic_initial_5ka_ru.bin}"
    line="${line//\{quicrutube\}/$TEMP_DIR/quic_initial_rutube_ru.bin}"
    line="${line//\{quicsteam\}/$TEMP_DIR/quic_initial_steamcommunity_com.bin}"
    line="${line//\{quictencent\}/$TEMP_DIR/quic_initial_tencent_com.bin}"
    line="${line//\{tls5ka\}/$TEMP_DIR/tls_clienthello_5ka_ru.bin}"
    line="${line//\{tlssferum\}/$TEMP_DIR/tls_clienthello_www_sferum_ru.bin}"

    # Алиасы наборов конструктора стратегий: у менеджера один общий ipset-файл
    # (ipset-all_merged) и один общий hostlist (list-general_merged).
    line="${line//\{ipset_all2\}/$TEMP_DIR/ipset-all_merged.txt}"
    line="${line//\{ipset_base\}/$TEMP_DIR/ipset-all_merged.txt}"
    line="${line//\{ipset_cloudflare\}/$TEMP_DIR/ipset-all_merged.txt}"
    line="${line//\{ipset_cloudflare1\}/$TEMP_DIR/ipset-all_merged.txt}"
    line="${line//\{ipset_discord\}/$TEMP_DIR/ipset-all_merged.txt}"
    line="${line//\{ipset_dns\}/$TEMP_DIR/ipset-all_merged.txt}"
    line="${line//\{cloudflare_ipset\}/$TEMP_DIR/ipset-all_merged.txt}"
    line="${line//\{discord\}/$TEMP_DIR/list-general_merged.txt}"
    line="${line//\{telegram\}/$TEMP_DIR/list-telegram_merged.txt}"
    line="${line//\{youtube\}/$TEMP_DIR/list-general_merged.txt}"
    line="${line//\{rutracker\}/$TEMP_DIR/list-general_merged.txt}"
    line="${line//\{hosts\}/$TEMP_DIR/list-general_merged.txt}"
    line="${line//\{netrogat\}/$TEMP_DIR/list-general_merged.txt}"
    line="${line//\{other2\}/$TEMP_DIR/list-general_merged.txt}"
    line="${line//\{russia_blacklist\}/$TEMP_DIR/list-general_merged.txt}"
    line="${line//\{russia_youtube_rtmps\}/$TEMP_DIR/list-general_merged.txt}"

    # ЗАМЕНЯЕМ {GameFilter} НА ЗНАЧЕНИЕ В ЗАВИСИМОСТИ ОТ ПРОТОКОЛА
    if [[ "$line" == *"--filter-tcp"* && "$line" == *"{GameFilter}"* ]]; then
        # Для TCP строк используем TCP значение
        line="${line//\{GameFilter\}/$GAME_FILTER_TCP_VALUE}"
    elif [[ "$line" == *"--filter-udp"* && "$line" == *"{GameFilter}"* ]]; then
        # Для UDP строк используем UDP значение
        line="${line//\{GameFilter\}/$GAME_FILTER_UDP_VALUE}"
    fi

    # УДАЛЯЕМ --wf-* ОПЦИИ
    line="$(echo "$line" | sed -E 's/--wf-(tcp|udp)=[^ ]+//g')"

    # НОРМАЛИЗУЕМ ПРОБЕЛЫ
    line="$(echo "$line" | sed -E 's/  +/ /g' | sed -E 's/^ //;s/ $//')"

    # ДОБАВЛЯЕМ В ARGS
    if [ -n "$line" ]; then
        ARGS+=" $line"
    fi
done < "$CONFIG_FILE"

# Профили WARP для внешнего трафика туннеля: nfqws применяет первый совпавший
# профиль, поэтому они идут ПЕРЕД правилами пользователя.
if [ "$WARP_ENABLED" = "1" ] && [ "$WARP_HELP" = "1" ] && [ -f "$WARP_RULES_FILE" ]; then
    WARP_ARGS=""
    while IFS= read -r line || [ -n "$line" ]; do
        [ -n "${line//[[:space:]]/}" ] || continue
        expand_warp_rule "$line" || exit 1
        WARP_ARGS+=" $EXPANDED_RULE"
    done < "$WARP_RULES_FILE"
    if [ -n "$WARP_ARGS" ]; then
        echo "WARP: профили для внешнего трафика туннеля:$WARP_ARGS"
        ARGS="$WARP_ARGS$ARGS"
    fi
elif [ "$WARP_ENABLED" = "1" ]; then
    echo "WARP: лечение через zapret выключено — endpoint'ы туннеля исключаются из NFQUEUE"
fi

echo "Final ARGS: $ARGS"

# Сохраняем прежнее значение sysctl, чтобы stopper.sh мог его вернуть.
SYSCTL_KEY="net.netfilter.nf_conntrack_tcp_be_liberal"
SYSCTL_STATE_DIR="/run/zapret"
mkdir -p "$SYSCTL_STATE_DIR"
if sysctl -n "$SYSCTL_KEY" > /dev/null 2>&1; then
    # Исходное значение сохраняем один раз: повторные запуски не должны его перезаписывать.
    if [ ! -f "$SYSCTL_STATE_DIR/be_liberal.before" ]; then
        sysctl -n "$SYSCTL_KEY" > "$SYSCTL_STATE_DIR/be_liberal.before" 2>/dev/null || true
    fi
    sysctl -q "$SYSCTL_KEY=1" || echo "Warning: не удалось применить $SYSCTL_KEY=1"
else
    echo "Warning: $SYSCTL_KEY недоступен (модуль nf_conntrack не загружен?)"
fi

if [ "$FWTYPE" = "iptables" ]; then
    TCP_PORTS=$(echo "$ARGS" | tr -s ' ' '\n' | grep '^--filter-tcp=' | sed 's/--filter-tcp=//' | paste -sd, | sed 's/-/:/g' || true)
    UDP_PORTS=$(echo "$ARGS" | tr -s ' ' '\n' | grep '^--filter-udp=' | sed 's/--filter-udp=//' | paste -sd, | sed 's/-/:/g' || true)
elif [ "$FWTYPE" = "nftables" ]; then
    TCP_PORTS=$(echo "$ARGS" | tr -s ' ' '\n' | grep '^--filter-tcp=' | sed 's/--filter-tcp=//' | paste -sd, | sed 's/:/-/g' || true)
    UDP_PORTS=$(echo "$ARGS" | tr -s ' ' '\n' | grep '^--filter-udp=' | sed 's/--filter-udp=//' | paste -sd, | sed 's/:/-/g' || true)
fi

# Удаляем дубликаты портов
TCP_PORTS=$(echo "$TCP_PORTS" | tr ',' '\n' | sort -u | tr '\n' ',' | sed 's/,$//')
UDP_PORTS=$(echo "$UDP_PORTS" | tr ',' '\n' | sort -u | tr '\n' ',' | sed 's/,$//')

# Преобразуем диапазоны обратно в нужный формат
if [ "$FWTYPE" = "iptables" ]; then
    TCP_PORTS=$(echo "$TCP_PORTS" | sed 's/-/:/g')
    UDP_PORTS=$(echo "$UDP_PORTS" | sed 's/-/:/g')
elif [ "$FWTYPE" = "nftables" ]; then
    TCP_PORTS=$(echo "$TCP_PORTS" | sed 's/:/-/g')
    UDP_PORTS=$(echo "$UDP_PORTS" | sed 's/:/-/g')
fi

echo "Configuring $FWTYPE for TCP ports: $TCP_PORTS"
echo "Configuring $FWTYPE for UDP ports: $UDP_PORTS"

if [ "$FWTYPE" = "iptables" ]; then
    # Своя цепочка ZAPRET: не стираем чужие правила Docker/libvirt/VPN/firewalld.
    for tool in iptables ip6tables; do
        "$tool" -t mangle -N ZAPRET 2>/dev/null || true
        "$tool" -t mangle -F ZAPRET
        "$tool" -t mangle -D PREROUTING -j ZAPRET 2>/dev/null || true
        "$tool" -t mangle -D POSTROUTING -j ZAPRET 2>/dev/null || true
        "$tool" -t mangle -I PREROUTING -j ZAPRET
        "$tool" -t mangle -I POSTROUTING -j ZAPRET
    done
elif [ "$FWTYPE" = "nftables" ]; then
    # Повторный запуск не должен падать на существующей таблице/цепочках.
    nft add table inet zapret 2>/dev/null || true
    nft flush table inet zapret 2>/dev/null || true
    nft add chain inet zapret prerouting { type filter hook prerouting priority mangle \; } 2>/dev/null || true
    nft add chain inet zapret postrouting { type filter hook postrouting priority mangle \; } 2>/dev/null || true
fi

if [ "$FWTYPE" = "iptables" ]; then
    # Исключение интерфейса туннеля: проверяем опытным путём, принимает ли
    # iptables -o/-i в пользовательской цепочке (в PREROUTING/POSTROUTING).
    WARP_OUT_FLAGS=""
    WARP_IN_FLAGS=""
    if [ -n "$WARP_IFACE" ]; then
        iptables -t mangle -N ZAPRET_PROBE 2>/dev/null || true
        if iptables -t mangle -A ZAPRET_PROBE -o "$WARP_IFACE" -j RETURN 2>/dev/null &&
            iptables -t mangle -A ZAPRET_PROBE -i "$WARP_IFACE" -j RETURN 2>/dev/null; then
            WARP_OUT_FLAGS="! -o $WARP_IFACE"
            WARP_IN_FLAGS="! -i $WARP_IFACE"
        else
            echo "Warning: iptables не принимает -o/-i в цепочке ZAPRET — внутренний трафик туннеля не исключается." >&2
        fi
        iptables -t mangle -F ZAPRET_PROBE 2>/dev/null || true
        iptables -t mangle -X ZAPRET_PROBE 2>/dev/null || true
    fi

    add_ipt_rule() {
        local proto=$1
        local ports=$2
        local qnum=$3
        local extra_flags=$4
        local out_flags=$5
        local in_flags=$6

        iptables -t mangle -I ZAPRET -p "$proto" -m multiport --dports "$ports" \
            $out_flags $extra_flags -j NFQUEUE --queue-num "$qnum" --queue-bypass
        iptables -t mangle -I ZAPRET -p "$proto" -m multiport --sports "$ports" \
            $in_flags $extra_flags -j NFQUEUE --queue-num "$qnum" --queue-bypass
        ip6tables -t mangle -I ZAPRET -p "$proto" -m multiport --dports "$ports" \
            $out_flags $extra_flags -j NFQUEUE --queue-num "$qnum" --queue-bypass
        ip6tables -t mangle -I ZAPRET -p "$proto" -m multiport --sports "$ports" \
            $in_flags $extra_flags -j NFQUEUE --queue-num "$qnum" --queue-bypass
    }

    if [ -n "$TCP_PORTS" ]; then
        add_ipt_rule "tcp" "$TCP_PORTS" "200" "-m connbytes --connbytes-dir=original --connbytes-mode=packets --connbytes 1:12" "$WARP_OUT_FLAGS" "$WARP_IN_FLAGS"
    fi
    if [ -n "$UDP_PORTS" ]; then
        add_ipt_rule "udp" "$UDP_PORTS" "200" "-m connbytes --connbytes-dir=original --connbytes-mode=packets --connbytes 1:12" "$WARP_OUT_FLAGS" "$WARP_IN_FLAGS"
    fi

    if [ -n "$TCP_PORTS" ]; then
        add_ipt_rule "tcp" "$TCP_PORTS" "200" "-m connbytes --connbytes-dir=reply --connbytes-mode=packets --connbytes 1:6" "$WARP_OUT_FLAGS" "$WARP_IN_FLAGS"
    fi
    if [ -n "$UDP_PORTS" ]; then
        add_ipt_rule "udp" "$UDP_PORTS" "200" "-m connbytes --connbytes-dir=reply --connbytes-mode=packets --connbytes 1:6" "$WARP_OUT_FLAGS" "$WARP_IN_FLAGS"
    fi

    # Endpoint'ы туннеля вне NFQUEUE (лечение выключено): правила вставляются
    # последними, поэтому оказываются в начале цепочки — до правил NFQUEUE.
    if [ "$WARP_ENABLED" = "1" ] && [ "$WARP_HELP" = "0" ]; then
        if [ -n "$WARP_EP_V4" ]; then
            for ip in ${WARP_EP_V4//,/ }; do
                iptables -t mangle -I ZAPRET 1 -d "$ip" -j RETURN
                iptables -t mangle -I ZAPRET 1 -s "$ip" -j RETURN
            done
        fi
        if [ -n "$WARP_EP_V6" ]; then
            for ip in ${WARP_EP_V6//,/ }; do
                ip6tables -t mangle -I ZAPRET 1 -d "$ip" -j RETURN
                ip6tables -t mangle -I ZAPRET 1 -s "$ip" -j RETURN
            done
        fi
        if [ -n "$WARP_EP_V4$WARP_EP_V6" ]; then
            echo "WARP: endpoint'ы туннеля исключены из NFQUEUE (v4: ${WARP_EP_V4:-нет}, v6: ${WARP_EP_V6:-нет})"
        fi
    fi

elif [ "$FWTYPE" = "nftables" ]; then

    # Endpoint'ы туннеля вне NFQUEUE (лечение выключено): правила добавляются
    # первыми, то есть раньше правил NFQUEUE ниже.
    if [ "$WARP_ENABLED" = "1" ] && [ "$WARP_HELP" = "0" ]; then
        if [ -n "$WARP_EP_V4" ]; then
            nft add rule inet zapret postrouting ip daddr { $WARP_EP_V4 } return
            nft add rule inet zapret prerouting ip saddr { $WARP_EP_V4 } return
        fi
        if [ -n "$WARP_EP_V6" ]; then
            nft add rule inet zapret postrouting ip6 daddr { $WARP_EP_V6 } return
            nft add rule inet zapret prerouting ip6 saddr { $WARP_EP_V6 } return
        fi
        echo "WARP: endpoint'ы туннеля исключены из NFQUEUE (v4: ${WARP_EP_V4:-нет}, v6: ${WARP_EP_V6:-нет})"
    fi

    if [ -n "$TCP_PORTS" ]; then
        nft add rule inet zapret postrouting tcp dport { $TCP_PORTS } $WARP_IFACE_NFT_OUT ct original packets 1-12 queue num 200 bypass
        nft add rule inet zapret prerouting tcp sport { $TCP_PORTS } $WARP_IFACE_NFT_IN ct reply packets 1-6 queue num 200 bypass
    fi

    if [ -n "$UDP_PORTS" ]; then
        nft add rule inet zapret postrouting udp dport { $UDP_PORTS } $WARP_IFACE_NFT_OUT ct original packets 1-12 queue num 200 bypass
        nft add rule inet zapret prerouting udp sport { $UDP_PORTS } $WARP_IFACE_NFT_IN ct reply packets 1-6 queue num 200 bypass
    fi
fi

# ПРОВЕРЯЕМ, ЧТО ПРАВИЛА NFQUEUE РЕАЛЬНО СОЗДАНЫ
if [ "$FWTYPE" = "iptables" ]; then
    if ! iptables -t mangle -S ZAPRET 2>/dev/null | grep -q -- '-j NFQUEUE'; then
        echo "ERROR: правила NFQUEUE не созданы (iptables)" >&2
        exit 1
    fi
elif [ "$FWTYPE" = "nftables" ]; then
    # nft печатает правило как «queue flags bypass to 200», а не «queue num 200».
    if ! nft list chain inet zapret prerouting 2>/dev/null | grep -qE 'queue.*to 200'; then
        echo "ERROR: правила NFQUEUE не созданы (nftables)" >&2
        exit 1
    fi
fi

# ЗАПУСКАЕМ NFQWS С ВРЕМЕННЫМИ ФАЙЛАМИ
echo "Starting nfqws with temp files..."
if [ "${1:-}" = "--foreground" ]; then
    /opt/zapret/nfqws --qnum=200 --uid=0:0 $ARGS
else
    /opt/zapret/nfqws --qnum=200 --uid=0:0 $ARGS &
fi
NFQWS_PID=$!

# ПРОВЕРЯЕМ ЗАПУСК
sleep 2
if ps -p $NFQWS_PID > /dev/null; then
    echo "nfqws successfully started with PID: $NFQWS_PID"
    # УДАЛЯЕМ ВРЕМЕННЫЕ ФАЙЛЫ ПОСЛЕ УСПЕШНОГО ЗАПУСКА
    rm -rf "$TEMP_DIR"
    echo "Temp files cleaned up"
else
    echo "ERROR: nfqws failed to start!"
    echo "Check above for errors"
    rm -rf "$TEMP_DIR"
    exit 1
fi
