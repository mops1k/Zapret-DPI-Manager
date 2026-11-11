#!/usr/bin/env python3
"""Стенд политики пароля sudo (без root и сети).

Фейковый ``sudo`` в temp-каталоге эмулирует NOPASSWD, системный timestamp и
проверку пароля, temp-HOME изолирует файл «Запомнить». Проверяется:
- passwordless sudo — провайдер (модалка) не спрашивается;
- без «Запомнить» — новое действие снова спрашивает пароль;
- «Запомнить» — пароль в файле 0600, следующее действие без вопроса;
- отмена в провалке модалки отменяет команду;
- протухший сохранённый пароль удаляется и запрашивается заново;
- пароль передаётся sudo через stdin, а не в argv.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAKE_SUDO = r"""#!/bin/bash
# Фейковый sudo стенда: NOPASSWD, timestamp в файле состояния, проверка пароля.
state="${ZAPRET_TEST_STATE:?}"
mode="${ZAPRET_TEST_MODE:-password}"
secret="${ZAPRET_TEST_PASSWORD:?}"
log="${ZAPRET_TEST_LOG:?}"
printf '%s\n' "$*" >> "$log"

use_k=0
use_s=0
cmd=()
i=0
args=("$@")
while [ "$i" -lt "${#args[@]}" ]; do
  a="${args[$i]}"
  case "$a" in
    -k) use_k=1; i=$((i+1));;
    -n) i=$((i+1));;
    -S) use_s=1; i=$((i+1));;
    -p) i=$((i+2));;
    -A) i=$((i+1));;
    *) cmd+=("$a"); i=$((i+1));;
  esac
done

if [ "$use_k" = 1 ]; then
  rm -f "$state"
fi

if [ "$mode" != "nopassword" ]; then
  if [ -f "$state" ]; then
    :
  elif [ "$use_s" = 1 ]; then
    read -r password || password=""
    if [ "$password" != "$secret" ]; then
      printf 'Sorry, try again.\n' >&2
      exit 1
    fi
    : > "$state"
  else
    printf 'sudo: a password is required\n' >&2
    exit 1
  fi
fi

if [ "${cmd[0]:-}" = "true" ] || [ -z "${cmd[0]:-}" ]; then
  exit 0
fi
exit 0
"""

PASSWORD = "s3cret"


class Stand:
    """Temp-окружение стенда: fake sudo, HOME, лог и состояние."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="zapret_sudo_check_"))
        self.bin_dir = self.root / "bin"
        self.home = self.root / "home"
        self.state = self.root / "state"
        self.log = self.root / "sudo.log"
        self.bin_dir.mkdir()
        self.home.mkdir()
        fake = self.bin_dir / "sudo"
        fake.write_text(FAKE_SUDO, encoding="utf-8")
        fake.chmod(0o755)
        os.environ["HOME"] = str(self.home)
        os.environ["PATH"] = f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["ZAPRET_TEST_STATE"] = str(self.state)
        os.environ["ZAPRET_TEST_PASSWORD"] = PASSWORD
        os.environ["ZAPRET_TEST_LOG"] = str(self.log)
        os.environ["ZAPRET_TEST_MODE"] = "password"

    def reset(self, mode: str = "password") -> None:
        os.environ["ZAPRET_TEST_MODE"] = mode
        for path in (self.state, self.log):
            path.unlink(missing_ok=True)
        from core.sudo_helper import clear_password_provider, forget_cached_password

        clear_password_provider()
        forget_cached_password()

    def calls(self) -> int:
        return len(self.log.read_text(encoding="utf-8").splitlines()) if self.log.exists() else 0
    def cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)


class Provider:
    """Провайдер пароля, считающий обращения (как Qt-модалка)."""

    def __init__(self, password: str | None) -> None:
        self.password = password
        self.calls = 0

    def __call__(self) -> str | None:
        self.calls += 1
        return self.password


def main() -> int:
    stand = Stand()
    failures: list[str] = []
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))
        if not ok:
            failures.append(name)

    try:
        from core import sudo_helper

        # 1. passwordless: провайдер не нужен.
        stand.reset(mode="nopassword")
        provider = Provider(PASSWORD)
        sudo_helper.set_password_provider(provider)
        code, _out, err = sudo_helper.run_sudo(["true"], timeout=10)
        check("passwordless без вопроса", code == 0 and provider.calls == 0,
              f"code={code} calls={provider.calls} err={err!r}")

        # 2. Без «Запомнить»: новое действие снова спрашивает.
        stand.reset()
        provider = Provider(PASSWORD)
        sudo_helper.set_password_provider(provider)
        code, _out, _err = sudo_helper.run_sudo(["true"], timeout=10)
        first_calls = provider.calls
        sudo_helper.clear_session_password()
        code2, _out2, _err2 = sudo_helper.run_sudo(["true"], timeout=10)
        check("запрос на каждое действие",
              code == 0 and code2 == 0 and first_calls == 1 and provider.calls == 2,
              f"code={code}/{code2} calls={first_calls}->{provider.calls}")
        check("файла «Запомнить» нет", not sudo_helper.has_cached_password())

        # 3. Отмена модалки отменяет команду.
        stand.reset()
        provider = Provider(None)
        sudo_helper.set_password_provider(provider)
        code, _out, err = sudo_helper.run_sudo(["true"], timeout=10)
        check("отмена отменяет команду",
              code == 1 and "Ввод пароля отменён" in err,
              f"code={code} err={err!r}")

        # 4. «Запомнить»: пароль в файле 0600, следующее действие без вопроса.
        stand.reset()
        provider = Provider(PASSWORD)
        sudo_helper.set_password_provider(provider)
        code, _out, _err = sudo_helper.run_sudo(["true"], timeout=10)
        check("действие с модалкой выполнено", code == 0 and provider.calls == 1)
        sudo_helper.save_cached_password(PASSWORD)
        mode_bits = stat.S_IMODE(sudo_helper.cache_file().stat().st_mode)
        check("файл «Запомнить» 0600", mode_bits == 0o600, f"mode={oct(mode_bits)}")

        # Новый «запуск приложения»: провайдер новый, системный timestamp сброшен.
        stand.state.unlink(missing_ok=True)
        provider = Provider("не должен вызываться")
        sudo_helper.set_password_provider(provider)
        code, _out, err = sudo_helper.run_sudo(["true"], timeout=10)
        check("«Запомнить» — без вопроса",
              code == 0 and provider.calls == 0,
              f"code={code} calls={provider.calls} err={err!r}")

        # 5. Протухший сохранённый пароль: файл удаляется, спросить заново.
        stand.reset()
        sudo_helper.save_cached_password("wrong")
        provider = Provider(PASSWORD)
        sudo_helper.set_password_provider(provider)
        code, _out, err = sudo_helper.run_sudo(["true"], timeout=10)
        check("протухший пароль перезапрошен",
              code == 0 and provider.calls == 1 and not sudo_helper.has_cached_password(),
              f"code={code} calls={provider.calls} err={err!r}")

        # 6. Пароль не попадает в argv и проверка работает.
        log_text = stand.log.read_text(encoding="utf-8") if stand.log.exists() else ""
        check("пароль не в argv", PASSWORD not in log_text)
        ok, detail = sudo_helper.check_sudo_password(PASSWORD)
        check("верный пароль принят", ok, detail)
        ok, detail = sudo_helper.check_sudo_password("wrong")
        check("неверный пароль отклонён",
              not ok and detail == "Неверный пароль sudo", detail)

        # 7. passwordless не создаёт файл «Запомнить».
        stand.reset(mode="nopassword")
        provider = Provider(PASSWORD)
        sudo_helper.set_password_provider(provider)
        sudo_helper.run_sudo(["true"], timeout=10)
        check("passwordless без файла", not sudo_helper.has_cached_password())
    finally:
        from core.sudo_helper import clear_password_provider

        clear_password_provider()
        stand.cleanup()

    for name, ok, detail in checks:
        mark = "OK" if ok else "FAIL"
        suffix = f" ({detail})" if detail and not ok else ""
        print(f"  [{mark}] {name}{suffix}")
    print(f"\nСтенд пароля sudo: {len(checks) - len(failures)}/{len(checks)} проверок")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
