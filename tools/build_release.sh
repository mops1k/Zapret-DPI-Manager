#!/usr/bin/env bash
# Сборка релизных архивов Zapret DPI Manager.
#
# Состав повторяет релизы проекта:
#   Zapret_DPI_Manager.tar.gz — полный пакет для установки (main.py, core, qml,
#     qmlui, files, ico, utils, zapret + пустой config.txt);
#   zapret_updater.tar.gz — то же для обновления, но без config.txt и
#     пользовательских files/lists/*_user.txt, чтобы не перезаписывать данные.
#
# Использование:
#   tools/build_release.sh                 # собрать в dist/
#   tools/build_release.sh --out-dir DIR   # другой каталог
#
# Публикация релиза на GitHub здесь не делается: см. .github/workflows/release.yml.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${REPO_ROOT}/dist"

while [ $# -gt 0 ]; do
    case "$1" in
        --out-dir) OUT_DIR="$2"; shift 2 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
done

# Пути внутри ~/Zapret_DPI_Manager (в том же виде, что в релизах: с префиксом ./).
# ./warp — скрипты и юнит подсистемы WARP (usque): само приложение устанавливает
# их в /opt/warp, поэтому каталог обязан попадать в оба архива.
PACKAGE_PATHS=(./main.py ./core ./qml ./qmlui ./files ./ico ./utils ./zapret ./warp ./config.txt)

# Пользовательские файлы, которые обновление не должно затирать.
USER_FILES=(
    ./config.txt
    ./files/lists/list-general_user.txt
    ./files/lists/list-exclude_user.txt
    ./files/lists/ipset-all_user.txt
    ./files/lists/ipset-exclude_user.txt
    ./files/lists/list-telegram_user.txt
    ./files/lists/ipset-telegram_user.txt
)

EXCLUDES=(--exclude='__pycache__' --exclude='*.pyc')

for path in "${PACKAGE_PATHS[@]}"; do
    if [ ! -e "${REPO_ROOT}/${path}" ]; then
        echo "нет обязательного пути: ${path}" >&2
        exit 1
    fi
done

VERSION="$(head -n 1 "${REPO_ROOT}/version.txt")"
mkdir -p "${OUT_DIR}"
MANAGER_ARCHIVE="${OUT_DIR}/Zapret_DPI_Manager.tar.gz"
UPDATER_ARCHIVE="${OUT_DIR}/zapret_updater.tar.gz"

tar czf "${MANAGER_ARCHIVE}" -C "${REPO_ROOT}" "${EXCLUDES[@]}" "${PACKAGE_PATHS[@]}"

UPDATER_EXCLUDES=("${EXCLUDES[@]}")
for file in "${USER_FILES[@]}"; do
    UPDATER_EXCLUDES+=("--exclude=${file}")
done
tar czf "${UPDATER_ARCHIVE}" -C "${REPO_ROOT}" "${UPDATER_EXCLUDES[@]}" "${PACKAGE_PATHS[@]}"

echo "версия: ${VERSION}"
for archive in "${MANAGER_ARCHIVE}" "${UPDATER_ARCHIVE}"; do
    printf '%-32s %8s байт, %s записей\n' "$(basename "${archive}")" "$(stat -c%s "${archive}")" "$(tar tzf "${archive}" | wc -l)"
done

# Установщик: архив пересобирается из installer/install_zapret.sh, чтобы копия
# в репозитории и в архиве не расходились.
INSTALLER_SRC="${REPO_ROOT}/installer/install_zapret.sh"
INSTALLER_ARCHIVE="${REPO_ROOT}/install_zapret.tar.gz"
if [ -f "${INSTALLER_SRC}" ]; then
    tar czf "${INSTALLER_ARCHIVE}" -C "$(dirname "${INSTALLER_SRC}")" install_zapret.sh
    printf '%-32s %8s байт, %s записей\n' "install_zapret.tar.gz" "$(stat -c%s "${INSTALLER_ARCHIVE}")" "$(tar tzf "${INSTALLER_ARCHIVE}" | wc -l)"
fi

if tar tzf "${MANAGER_ARCHIVE}" | grep -qE '__pycache__|\.pyc$'; then
    echo "предупреждение: в архив попали __pycache__/*.pyc" >&2
fi

# Подсистема WARP должна быть в обоих архивах целиком: без неё приложение не
# сможет поставить /opt/warp/warp_start.sh и юнит warp.service.
for archive in "${MANAGER_ARCHIVE}" "${UPDATER_ARCHIVE}"; do
    for required in ./warp/system/warp_start.sh ./warp/system/warp_stop.sh ./warp/system/warp.service; do
        if ! tar tzf "${archive}" | grep -qx -- "${required}"; then
            echo "ошибка: в $(basename "${archive}") нет ${required}" >&2
            exit 1
        fi
    done
done
echo "проверка состава WARP: файлы на месте в обоих архивах"
