#!/usr/bin/env python3
"""Импорт избранных стратегий zapret из goodbyeDPI-UI (CLI).

Логика конвертации живёт в core/goodbyedpi_ui.py (попадает в релиз), этот
скрипт нужен для CI и ручного обновления:

    python3 tools/import_goodbyedpi_ui.py            # показать
    python3 tools/import_goodbyedpi_ui.py --write    # записать files/strategy
    python3 tools/import_goodbyedpi_ui.py --check    # сверить + nfqws --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.bbdpi_convert import dry_run  # noqa: E402
from core.flowseal_convert import known_placeholders, starter_path, validate  # noqa: E402
from core.goodbyedpi_ui import NAME_MAP, collect  # noqa: E402

# Исходные JSON лежат в репозитории, чтобы проверка работала и в CI
# (локальный .dsh/ в git не попадает). Переопределяется ключом --src.
DEFAULT_SRC = REPO_ROOT / "tools" / "goodbyedpiui_src"
DEFAULT_OUT = REPO_ROOT / "files" / "strategy"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC, help="каталог с JSON goodbyeDPI-UI")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="каталог files/strategy")
    parser.add_argument("--write", action="store_true", help="записать файлы стратегий")
    parser.add_argument("--check", action="store_true", help="сверить с текущими файлами и прогнать nfqws --dry-run")
    args = parser.parse_args()

    if not args.src.is_dir():
        print(f"нет каталога с JSON: {args.src}", file=sys.stderr)
        return 2

    strategies = collect(args.src)
    if not strategies:
        print(f"в {args.src} не найдено избранных JSON ({', '.join(sorted(NAME_MAP))})", file=sys.stderr)
        return 2

    allowed = known_placeholders(starter_path())
    failed = False
    for name, rules in strategies.items():
        problems = validate(rules, name, known=allowed)
        if args.check:
            problems += dry_run(rules)
        status = "OK" if not problems else "ОШИБКА: " + "; ".join(problems)
        if problems:
            failed = True
        target = args.out / name
        marker = ""
        if args.check:
            if target.exists():
                old = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
                marker = " = совпадает" if old == rules else " ≠ отличается"
            else:
                marker = " (новый файл)"
        print(f"{name:24} {len(rules):2} правил  {status}{marker}")
        if args.write and not problems:
            target.write_text("\n".join(rules) + "\n", encoding="utf-8")

    if args.write and not failed:
        print(f"\nзаписано {len(strategies)} файлов в {args.out}")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
