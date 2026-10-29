"""Конвертер стратегий BB DPI (ciadpi / byedpi) в аргументы nfqws.

BB DPI (ByeByeDPI для Android) применяет движок ciadpi (hufrea/byedpi) —
локальный SOCKS/прозрачный прокси с собственным синтаксисом опций.
Менеджер работает на nfqws (zapret) через NFQUEUE, поэтому строку ciadpi
нельзя использовать напрямую: модуль переводит её в ближайшие по смыслу
аргументы nfqws и отдельно сообщает, что перенести не удалось.

Проект «Zapret DPI Manager» © Aleksandr Kvintilyanov.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Короткие опции ciadpi -> канонические длинные имена.
SHORT_OPTIONS = {
    "i": "ip",
    "p": "port",
    "D": "daemon",
    "w": "pidfile",
    "E": "transparent",
    "c": "max-conn",
    "I": "conn-ip",
    "b": "buf-size",
    "g": "def-ttl",
    "F": "tfo",
    "A": "auto",
    "L": "auto-mode",
    "u": "cache-ttl",
    "y": "cache-dump",
    "T": "timeout",
    "K": "proto",
    "H": "hosts",
    "j": "ipset",
    "V": "pf",
    "R": "round",
    "s": "split",
    "d": "disorder",
    "o": "oob",
    "q": "disoob",
    "f": "fake",
    "t": "ttl",
    "S": "md5sig",
    "O": "fake-offset",
    "l": "fake-data",
    "e": "oob-data",
    "n": "fake-sni",
    "Q": "fake-tls-mod",
    "M": "mod-http",
    "r": "tlsrec",
    "m": "tlsminor",
    "a": "udp-fake",
    "Y": "drop-sack",
    "N": "no-domain",
    "U": "no-udp",
    "x": "debug",
}

# Опции без значения.
FLAG_OPTIONS = {"daemon", "transparent", "tfo", "md5sig", "drop-sack", "no-domain", "no-udp"}

# Опции, которые не влияют на обход трафика: у менеджера своя служба и порты.
SERVICE_OPTIONS = {
    "ip",
    "port",
    "daemon",
    "pidfile",
    "transparent",
    "max-conn",
    "conn-ip",
    "buf-size",
    "tfo",
    "debug",
    "no-domain",
}

# Опции ciadpi, которым нет аналога в nfqws (переносятся только предупреждением).
UNSUPPORTED_OPTIONS = {
    "oob": "OOB-отправка (-o/--oob) в nfqws отсутствует",
    "disoob": "OOB + disorder (-q/--disoob) в nfqws отсутствует",
    "tlsrec": "разрезание TLS-записи (-r/--tlsrec) в nfqws отсутствует",
    "auto": "автоматические группы (-A/--auto) в nfqws отсутствуют",
    "auto-mode": "режим авто-кеша (-L/--auto-mode) в nfqws отсутствует",
    "cache-ttl": "время жизни кеша (-u/--cache-ttl) в nfqws отсутствует",
    "cache-dump": "выгрузка кеша (-y/--cache-dump) в nfqws отсутствует",
    "timeout": "таймаут авто-режима (-T/--timeout) в nfqws отсутствует",
    "round": "применение к N-му запросу (-R/--round) в nfqws отсутствует",
    "oob-data": "OOB-байт (-e/--oob-data) в nfqws отсутствует",
    "tlsminor": "подмена версии TLS (-m/--tlsminor) в nfqws отсутствует",
    "drop-sack": "игнорирование SACK (-Y/--drop-sack) в nfqws отсутствует",
}

# Плейсхолдер -> файл, на который его раскрывает starter.sh (для nfqws --dry-run).
PLACEHOLDER_FILES = {
    "list_general": "files/lists/list-general.txt",
    "list_general_user": "files/lists/list-general_user.txt",
    "list_exclude": "files/lists/list-exclude.txt",
    "list_exclude_user": "files/lists/list-exclude_user.txt",
    "list_google": "files/lists/list-google.txt",
    "ipset_all": "files/lists/ipset-all.txt",
    "ipset_all_user": "files/lists/ipset-all_user.txt",
    "ipset_exclude": "files/lists/ipset-exclude.txt",
    "ipset_exclude_user": "files/lists/ipset-exclude_user.txt",
    "list_telegram": "files/lists/list-telegram.txt",
    "list_telegram_user": "files/lists/list-telegram_user.txt",
    "ipset_telegram": "files/lists/ipset-telegram.txt",
    "ipset_telegram_user": "files/lists/ipset-telegram_user.txt",
    "ipset_warp": "files/lists/ipset-warp.txt",
    "gw": "files/lists/gw.txt",
    "other": "files/lists/other.txt",
    "tlsgoogle": "files/bin/tls_clienthello_www_google_com.bin",
    "tls4pda": "files/bin/tls_clienthello_4pda_to.bin",
    "tlsmax": "files/bin/tls_clienthello_max_ru.bin",
    "tlsmaxui": "files/bin/tls_clienthello_max_ru_ui.bin",
    "tls5": "files/bin/tls_clienthello_5.bin",
    "tls11": "files/bin/tls_clienthello_11.bin",
    "quic6": "files/bin/quic_6.bin",
    "quic7": "files/bin/quic_7.bin",
    "httpfake_ms": "files/bin/http_fake_MS.bin",
    "stun": "files/bin/stun.bin",
    "stun2": "files/bin/stun2.bin",
    "tlssochi": "files/bin/tls_clienthello_sochi_park.bin",
    "quicgoogle": "files/bin/quic_initial_www_google_com.bin",
    "quic4pda": "files/bin/quic_initial_4pda_to.bin",
    "dbankcloud": "files/bin/quic_initial_dbankcloud_ru.bin",
}

ARCH_MAP = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "i386": "x86",
    "i686": "x86",
    "armv6l": "arm",
    "armv7l": "arm",
    "aarch64": "arm64",
    "arm64": "arm64",
}


@dataclass
class ConvertResult:
    """Результат конвертации строки BB DPI."""

    rules: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Готовый текст стратегии (по строке на правило)."""
        return "\n".join(self.rules)

    @property
    def ok(self) -> bool:
        """Есть ли что применять."""
        return bool(self.rules)


def package_root(app_root: Path | str | None = None) -> Path:
    """Корень приложения (репозиторий или установленный менеджер)."""
    if app_root is not None:
        return Path(app_root)
    return Path(__file__).resolve().parents[1]


def nfqws_binary(app_root: Path | str | None = None) -> Path | None:
    """Путь к бинарнику nfqws: сначала пакет приложения, затем /opt/zapret."""
    root = package_root(app_root)
    arch = ARCH_MAP.get(os.uname().machine, "x86_64")
    candidate = root / "zapret" / "bins" / arch / "nfqws"
    if candidate.is_file():
        return candidate
    installed = Path("/opt/zapret/nfqws")
    if installed.is_file():
        return installed
    return None


def safe_strategy_name(name: str) -> str:
    """Имя файла стратегии без разделителей пути и лишних символов."""
    cleaned = re.sub(r"[^\w\-. ]", "_", name.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return cleaned or "BBDI_Custom"


def tokenize(command: str) -> list[str]:
    """Разбирает строку на токены, переживая незакрытые кавычки."""
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _is_option_token(token: str) -> bool:
    """Похож ли токен на опцию (нужно, чтобы отличать её от значения вида «-1»)."""
    if token.startswith("--"):
        return True
    return len(token) > 1 and token.startswith("-") and token[1] in SHORT_OPTIONS


def parse_options(command: str) -> list[tuple[str, str | None, str]]:
    """Список (каноническое имя, значение, исходный токен) для строки ciadpi."""
    tokens = tokenize(command)
    options: list[tuple[str, str | None, str]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            name, separator, inline = token[2:].partition("=")
            if name in FLAG_OPTIONS:
                options.append((name, inline if separator else None, token))
            elif separator:
                options.append((name, inline, token))
            else:
                value = None
                if index + 1 < len(tokens) and not _is_option_token(tokens[index + 1]):
                    value = tokens[index + 1]
                    index += 1
                options.append((name, value, token))
        elif token.startswith("-") and len(token) > 1:
            short = token[1]
            name = SHORT_OPTIONS.get(short, short)
            if name in FLAG_OPTIONS:
                options.append((name, None, token))
            else:
                rest = token[2:]
                if rest:
                    options.append((name, rest.lstrip("="), token))
                else:
                    value = None
                    if index + 1 < len(tokens) and not _is_option_token(tokens[index + 1]):
                        value = tokens[index + 1]
                        index += 1
                    options.append((name, value, token))
        else:
            options.append(("", token, token))
        index += 1
    return options


def _pos_positions(value: str | None, warnings: list[str]) -> list[str]:
    """Позиция ciadpi (``offset[:repeats:skip][+flags]``) -> позиции nfqws."""
    if not value:
        warnings.append("позиция разбиения не указана")
        return []
    body, _, flags = value.partition("+")
    parts = body.split(":")
    try:
        offset = int(parts[0])
    except ValueError:
        warnings.append(f"не разобрана позиция «{value}»")
        return []
    repeats = int(parts[1]) if len(parts) > 1 and parts[1] else 1
    skip = int(parts[2]) if len(parts) > 2 and parts[2] else 0

    marker = None
    if "s" in flags:
        marker = "sniext"
    elif "h" in flags:
        marker = "host"
    elif "e" in flags:
        marker = "endhost"
        warnings.append(f"флаг «+e» в позиции «{value}» передан как endhost — проверьте работу")
    elif "m" in flags:
        marker = "midsld"
        warnings.append(f"флаг «+m» в позиции «{value}» передан как midsld — проверьте работу")
    elif "n" in flags:
        offset = 0

    if marker is None and offset == 0:
        offset = 1
        warnings.append(f"позиция 0 в «{value}» заменена на 1: nfqws не принимает 0")

    positions: list[str] = []
    for index in range(max(repeats, 1)):
        shift = offset + index * skip
        if marker is None:
            positions.append(str(shift))
        elif shift == 0:
            positions.append(marker)
        else:
            positions.append(f"{marker}+{shift}" if shift > 0 else f"{marker}{shift}")
    return positions


def _sort_positions(positions: list[str]) -> list[str]:
    """Уникальные позиции: положительные по возрастанию, затем отрицательные, затем маркеры."""
    unique = list(dict.fromkeys(positions))
    numeric = sorted((int(pos) for pos in unique if pos.lstrip("-").isdigit()), key=lambda num: (num < 0, abs(num)))
    markers = [pos for pos in unique if not pos.lstrip("-").isdigit()]
    return [str(num) for num in numeric] + markers


def _dedupe(items: list[str]) -> list[str]:
    """Убирает повторы, сохраняя порядок."""
    return list(dict.fromkeys(items))


def _hex_payload(value: str) -> str:
    """Строка ``:...`` из ciadpi -> hex-литерал nfqws (0x...)."""
    raw = value[1:]
    return "0x" + raw.encode("utf-8").hex()


def convert(command: str, *, use_hostlist: bool = False) -> ConvertResult:
    """Строка BB DPI (ciadpi) -> аргументы nfqws.

    ``use_hostlist`` добавляет ограничение нашим списком доменов; без него
    стратегия применяется ко всему TCP, как в BB DPI.
    """
    result = ConvertResult()
    options = parse_options(command)
    if not options:
        result.warnings.append("пустая строка стратегии")
        return result

    split_positions: list[str] = []
    disorder_positions: list[str] = []
    fake = False
    fake_payload: str | None = None
    fake_offset: str | None = None
    fake_tls_mods: list[str] = []
    fake_sni: str | None = None
    ttl: str | None = None
    fooling: list[str] = []
    http_mods: list[str] = []
    l7_protocols: list[str] = []
    ipv4_only = False
    hostlist_domains: str | None = None
    ipset_ips: str | None = None
    tcp_ports: str | None = None
    udp_fake: str | None = None
    orig_ttl: str | None = None
    no_udp = False

    for name, value, raw in options:
        if name in SERVICE_OPTIONS:
            continue
        if name in UNSUPPORTED_OPTIONS:
            result.unsupported.append(f"{raw} — {UNSUPPORTED_OPTIONS[name]}")
        elif name == "no-udp":
            no_udp = True
        elif name == "split":
            split_positions += _pos_positions(value, result.warnings)
        elif name == "disorder":
            disorder_positions += _pos_positions(value, result.warnings)
        elif name == "fake":
            fake = True
            if value:
                _pos_positions(value, result.warnings)
                result.warnings.append("позиция fake в nfqws не настраивается: фейк идёт в начале")
        elif name == "ttl":
            ttl = value
        elif name == "md5sig":
            fooling.append("md5sig")
        elif name == "fake-offset":
            fake_offset = value
        elif name == "fake-data":
            if value and value.startswith(":"):
                fake_payload = _hex_payload(value)
            else:
                result.unsupported.append(f"{raw} — свой файл фейка не переносится, взят {tls_payload_name()}")
        elif name == "fake-sni":
            fake_sni = value
            if value:
                fake_tls_mods.append(f"sni={value}")
                if any(char in value for char in "?#*"):
                    result.warnings.append(
                        f"подстановочные символы в SNI «{value}» nfqws не поддерживает — передано как есть"
                    )
        elif name == "fake-tls-mod":
            if value and value.startswith("rand"):
                fake_tls_mods.append("rnd")
            else:
                result.unsupported.append(f"{raw} — режим фейка TLS перенесён не полностью")
        elif name == "mod-http":
            for flag in (value or "").replace(",", ""):
                if flag == "h":
                    http_mods.append("--hostcase")
                elif flag == "d":
                    http_mods.append("--domcase")
                elif flag == "r":
                    http_mods.append("--hostnospace")
                    result.warnings.append("mod-http=r передан как --hostnospace (близкая, но не та же правка)")
        elif name == "proto":
            for flag in (value or "").replace(",", ""):
                if flag == "t":
                    l7_protocols.append("tls")
                elif flag == "h":
                    l7_protocols.append("http")
                elif flag == "i":
                    ipv4_only = True
                elif flag == "u":
                    result.warnings.append("--proto=u (UDP) в nfqws задаётся фильтром портов, не L7")
        elif name == "hosts":
            if value and value.startswith(":"):
                hostlist_domains = value[1:].replace(" ", ",")
            else:
                result.unsupported.append(f"{raw} — файл доменов не переносится автоматически")
        elif name == "ipset":
            if value and value.startswith(":"):
                ipset_ips = value[1:].replace(" ", ",")
            else:
                result.unsupported.append(f"{raw} — файл ipset не переносится автоматически")
        elif name == "pf":
            tcp_ports = value
        elif name == "def-ttl":
            orig_ttl = value
            result.warnings.append("--def-ttl передан как --orig-ttl (TTL всех пакетов, не только фейка)")
        elif name == "udp-fake":
            udp_fake = value
        else:
            result.unsupported.append(f"{raw} — неизвестная для конвертера опция")

    modes: list[str] = []
    if fake:
        modes.append("fake")
    positions = _sort_positions(disorder_positions + split_positions)
    if disorder_positions:
        modes.append("multidisorder")
    elif split_positions:
        modes.append("multisplit")
    if split_positions and disorder_positions:
        result.warnings.append("split и disorder объединены в multidisorder — порядок сегментов может отличаться")
    if not modes:
        modes.append("tamper")
        if not http_mods:
            result.warnings.append("в строке нет методов обхода — сформирован режим tamper")

    args = [f"--filter-tcp={tcp_ports or '80,443'}"]
    if ipv4_only:
        args.append("--filter-l3=ipv4")
    if l7_protocols:
        args.append(f"--filter-l7={','.join(dict.fromkeys(l7_protocols))}")
    if use_hostlist:
        args += ["--hostlist={list_general}", "--hostlist-exclude={list_exclude}"]
    if hostlist_domains:
        args.append(f"--hostlist-domains={hostlist_domains}")
    if ipset_ips:
        args.append(f"--ipset-ip={ipset_ips}")
    args.append(f"--dpi-desync={','.join(modes)}")
    if positions:
        args.append(f"--dpi-desync-split-pos={','.join(positions)}")
    if fake:
        payload = fake_payload or tls_payload_name()
        if fake_offset:
            payload = f"+{fake_offset}@{payload}"
        args.append(f"--dpi-desync-fake-tls={payload}")
        if fake_tls_mods:
            args.append(f"--dpi-desync-fake-tls-mod={','.join(dict.fromkeys(fake_tls_mods))}")
        if ttl:
            args.append(f"--dpi-desync-ttl={ttl}")
        if fooling:
            args.append(f"--dpi-desync-fooling={','.join(dict.fromkeys(fooling))}")
    elif fake_sni or fake_tls_mods:
        result.warnings.append("параметры фейка указаны без --fake: nfqws их проигнорирует")
    if orig_ttl:
        args.append(f"--orig-ttl={orig_ttl}")
    args += http_mods
    args.append("--new")
    result.rules.append(" ".join(args))

    if udp_fake and not no_udp:
        udp_args = ["--filter-udp=443", "--dpi-desync=fake"]
        if udp_fake.isdigit() and udp_fake != "1":
            udp_args.append(f"--dpi-desync-repeats={udp_fake}")
        udp_args += [f"--dpi-desync-fake-quic={quic_payload_name()}", "--new"]
        result.rules.append(" ".join(udp_args))
        result.warnings.append("UDP-фейк (-a) перенесён приблизительно: отдельный профиль по порту 443")
    elif udp_fake and no_udp:
        result.warnings.append("UDP-фейк (-a) пропущен из-за -U/--no-udp")

    result.warnings = _dedupe(result.warnings)
    result.unsupported = _dedupe(result.unsupported)
    return result


def tls_payload_name() -> str:
    """Наш проверенный TLS-фейк для nfqws."""
    return "{tlsgoogle}"


def quic_payload_name() -> str:
    """Наш проверенный QUIC-фейк для nfqws."""
    return "{quicgoogle}"


def dry_run(rules: list[str], *, app_root: Path | str | None = None, timeout: int = 20) -> list[str]:
    """Прогон строк через ``nfqws --dry-run``; возвращает список проблем."""
    binary = nfqws_binary(app_root)
    if binary is None:
        return ["не найден бинарник nfqws"]
    root = package_root(app_root)
    problems: list[str] = []
    for index, rule in enumerate(rules, start=1):
        args = rule
        for name, rel in PLACEHOLDER_FILES.items():
            target = root / rel
            args = args.replace("{" + name + "}", str(target) if target.exists() else "12")
        try:
            proc = subprocess.run(
                [str(binary), "--dry-run", "--qnum=200", *args.split()],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            problems.append(f"строка {index}: nfqws не запустился ({error})")
            continue
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()
            problems.append(f"строка {index}: nfqws --dry-run код {proc.returncode}: {tail[-1] if tail else ''}")
    return problems
