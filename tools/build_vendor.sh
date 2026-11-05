#!/bin/bash
# Собирает prebuilt-бандл PySide6 для поставки без установки системных пакетов.
#
# Запускается в CI (.github/workflows/release.yml) и локально для проверки:
#   tools/build_vendor.sh [каталог dist]
#
# Результат:
#   dist/vendor/               — распакованный бандл (vendor/PySide6, vendor/shiboken6);
#   dist/pyside6_vendor.tar.gz — ассет релиза, его скачивает установщик.
#
# Колесо abi3: одно на любой Python >= 3.10 (SteamOS 3.5/3.6 — 3.11, 3.7/3.8 — 3.13).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DIST_DIR="${1:-$REPO_ROOT/dist}"

PYSIDE_VERSION="${PYSIDE_VERSION:-6.12.0}"
PY_TAG="3.10"
PLATFORM_TAG="manylinux_2_34_x86_64"
ABI_TAG="abi3"
PYTHON_BIN="${PYTHON_BIN:-python3}"

WHEELS_DIR="$DIST_DIR/wheels"
VENDOR_DIR="$DIST_DIR/vendor"
ARCHIVE="$DIST_DIR/pyside6_vendor.tar.gz"

rm -rf "$VENDOR_DIR" "$WHEELS_DIR"
mkdir -p "$WHEELS_DIR" "$VENDOR_DIR"

echo "Скачивание колёс PySide6-Essentials==$PYSIDE_VERSION, shiboken6==$PYSIDE_VERSION ($PLATFORM_TAG, $ABI_TAG)..."
"$PYTHON_BIN" -m pip download \
    --no-deps \
    --only-binary=:all: \
    --platform "$PLATFORM_TAG" \
    --python-version "$PY_TAG" \
    --implementation cp \
    --abi "$ABI_TAG" \
    --dest "$WHEELS_DIR" \
    "PySide6-Essentials==$PYSIDE_VERSION" \
    "shiboken6==$PYSIDE_VERSION"

echo "Распаковка колёс..."
for wheel in "$WHEELS_DIR"/*.whl; do
    "$PYTHON_BIN" -m zipfile -e "$wheel" "$VENDOR_DIR"
done

# Тексты лицензий Qt/PySide6 из vendor_licenses/ (LGPL-3.0 + NOTICE): бандл
# распространяет неизменённые библиотеки, поэтому лицензии едут вместе с ними.
mkdir -p "$VENDOR_DIR/LICENSES"
LICENSES_SRC="$REPO_ROOT/vendor_licenses"
if [ ! -d "$LICENSES_SRC" ]; then
    echo "нет каталога vendor_licenses с текстами лицензий" >&2
    exit 1
fi
cp -a "$LICENSES_SRC/." "$VENDOR_DIR/LICENSES/"
if [ -z "$(ls -A "$VENDOR_DIR/LICENSES")" ]; then
    echo "каталог vendor_licenses пуст" >&2
    exit 1
fi

echo "Очистка лишнего..."
find "$VENDOR_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$VENDOR_DIR" -name '*.pyi' -type f -delete 2>/dev/null || true
rm -rf "$VENDOR_DIR"/*.dist-info
rm -rf "$VENDOR_DIR/PySide6/Qt/translations"
rm -rf "$VENDOR_DIR/PySide6/include"
rm -rf "$VENDOR_DIR/PySide6/typesystems"
rm -rf "$VENDOR_DIR/PySide6/scripts"
rm -rf "$VENDOR_DIR/shiboken6/include"

echo "Проверка импорта из бандла..."
PYTHONPATH="$VENDOR_DIR" "$PYTHON_BIN" -c \
    "import PySide6.QtCore, PySide6.QtGui, PySide6.QtQml, PySide6.QtQuick, PySide6.QtQuickControls2"

echo "Упаковка ${ARCHIVE##*/}..."
tar czf "$ARCHIVE" -C "$DIST_DIR" vendor
rm -rf "$WHEELS_DIR"

printf 'vendor: %s\n' "$(du -sh "$VENDOR_DIR" | cut -f1)"
printf 'архив %s: %s\n' "${ARCHIVE##*/}" "$(du -h "$ARCHIVE" | cut -f1)"
