"""Конвертер стратегий zapret из goodbyeDPI-UI (winws) в формат менеджера.

Источник стратегий — репозиторий Storik4pro/goodbyeDPI-UI-configs (Apache-2.0),
каталог zapret/configs: JSON с полем ``custom_parameters`` — командная строка
winws.exe с правилами через ``--new``. Формат тот же, что у Flowseal, но с
Windows-обвязкой (``--wf-tcp``/``--wf-udp``/``--wf-l3``, ``--comment``) и путями
к файлам без каталогов (``--hostlist list-general.txt``).

Конвертация механическая:
  * выбрасываются ``--wf-*`` и ``--comment`` — порты nfqws собирает из
    ``--filter-*`` (starter.sh всё равно удаляет ``--wf-*``);
  * имена файлов заменяются плейсхолдерами starter.sh (кроме тех, которых
    нет у менеджера: чужие списки маппятся на общие, payload'ы — на наши
    аналоги; всё это видно в PATH_MAP);
  * к hostlist/ipset-парам добавляются ``*_user``-варианты, как во всех
    наших стратегиях;
  * каждой стратегии добавляются правила обхода Telegram (manager_extra_rules).

Модуль лежит в core/, потому что в релизные архивы попадает core/, но не tools/.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.flowseal_convert import manager_extra_rules, validate

# Имя файла в репозитории goodbyeDPI-UI -> плейсхолдер/имя у менеджера.
# Чужие списки сводятся к общим: у менеджера один hostlist и один ipset.
PATH_MAP = {
    "list-general.txt": "{list_general}",
    "list-exclude.txt": "{list_exclude}",
    "ipset-exclude.txt": "{ipset_exclude}",
    "ipset-all.txt": "{ipset_all}",
    "list-google.txt": "{list_google}",
    "ipset-cloudflare.txt": "{ipset_all}",
    "ipset-amazon.txt": "{ipset_all}",
    "ipset-discord.txt": "{ipset_all}",
    "list-discord.txt": "{list_general}",
    "list-ultimate.txt": "{list_general}",
    "list-youtube.txt": "{list_general}",
    "russia-blacklist.txt": "{list_general}",
    "russia-discord.txt": "{list_general}",
    "russia-youtube.txt": "{list_general}",
    "russia-youtubeQ.txt": "{list_general}",
    "russia-youtubeGV.txt": "{list_general}",
    "russia-youtubeq.txt": "{list_general}",
    "russia-youtubegv.txt": "{list_general}",
    "netrogat.txt": "{list_general}",
    "myhostlist.txt": "{list_general}",
    "tls_clienthello_www_google_com.bin": "{tlsgoogle}",
    "tls_clienthello_4pda_to.bin": "{tls4pda}",
    "tls_clienthello_max_ru.bin": "{tlsmaxui}",
    "TLS_ClientHello_max_ru.bin": "{tlsmaxui}",
    "tls_clienthello_5.bin": "{tls5}",
    "tls_clienthello_11.bin": "{tls11}",
    "quic_initial_www_google_com.bin": "{quicgoogle}",
    "quic_6.bin": "{quic6}",
    "quic_7.bin": "{quic7}",
    "http_fake_MS.bin": "{httpfake_ms}",
    "stun.bin": "{stun}",
    "stun2.bin": "{stun2}",
}

# Номера стратегий goodbyeDPI-UI, которые стоит держать у себя: они несут
# механики, которых нет в стратегиях Flowseal (autottl, split2, dup, udplen,
# WireGuard/ipset-ip, fake-tls-mod rndsni/padencap, свой TLS ClientHello).
NAME_MAP = {
    "1": "GdpiUI_1_Discord",
    "3": "GdpiUI_3_Amazon",
    "12": "GdpiUI_12_FakeTLS",
    "14": "GdpiUI_14_FakeTLS",
    "17": "GdpiUI_17_WireGuard",
    "18": "GdpiUI_18_FakeTLS",
    "22": "GdpiUI_22_MaxRU",
    "23": "GdpiUI_23_MaxRU",
    "25": "GdpiUI_25_MaxRU",
}

WF_RE = re.compile(r"--wf-(?:tcp|udp|l3|l2)(?:=\S+|\s+\S+)")
# Комментарий winws может содержать пробелы («--comment Cloudflare WARP1.1.1.1, 1.0.0.1»),
# поэтому удаляем его вместе со всеми словами до следующей опции.
COMMENT_RE = re.compile(r"--comment(?:=\S+)?(?:\s+(?!-)\S+)*")

# Исключающие фильтры не должны получать общий hostlist/ipset: в UI там лежат
# отдельные списки Discord/YouTube, у менеджера для исключений есть свои файлы.
EXCLUDE_MAP = {
    ("--hostlist-exclude", "list-discord.txt"): "{list_exclude}",
    ("--hostlist-exclude", "list-ultimate.txt"): "{list_exclude}",
    ("--hostlist-exclude", "russia-blacklist.txt"): "{list_exclude}",
    ("--hostlist-exclude", "russia-discord.txt"): "{list_exclude}",
    ("--hostlist-exclude", "russia-youtube.txt"): "{list_exclude}",
    ("--hostlist-exclude", "russia-youtubeQ.txt"): "{list_exclude}",
    ("--ipset-exclude", "ipset-discord.txt"): "{ipset_exclude}",
    ("--ipset-exclude", "ipset-cloudflare.txt"): "{ipset_exclude}",
    ("--ipset-exclude", "ipset-amazon.txt"): "{ipset_exclude}",
}

# Пары «базовый фильтр + пользовательский файл», как в остальных стратегиях.
USER_PAIRS = (
    ("--hostlist={list_general}", "--hostlist={list_general_user}"),
    ("--hostlist-exclude={list_exclude}", "--hostlist-exclude={list_exclude_user}"),
    ("--ipset={ipset_all}", "--ipset={ipset_all_user}"),
    ("--ipset-exclude={ipset_exclude}", "--ipset-exclude={ipset_exclude_user}"),
)


def _join_option_values(cmd: str) -> str:
    """«--filter-udp 443» -> «--filter-udp=443» (единый стиль стратегий)."""
    tokens = cmd.split()
    result: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--") and token != "--new" and "=" not in token and index + 1 < len(tokens):
            value = tokens[index + 1]
            if not value.startswith("--"):
                result.append(f"{token}={value}")
                index += 2
                continue
        result.append(token)
        index += 1
    return " ".join(result)


def convert_command(command: str) -> list[str]:
    """Команда winws из goodbyeDPI-UI -> список правил для nfqws."""
    cmd = command.replace("^!", "!")
    cmd = WF_RE.sub(" ", cmd)
    cmd = COMMENT_RE.sub(" ", cmd)
    cmd = _join_option_values(re.sub(r"\s+", " ", cmd).strip())
    for (option, filename), placeholder in EXCLUDE_MAP.items():
        cmd = cmd.replace(f"{option}={filename}", f"{option}={placeholder}")
    for filename, placeholder in sorted(PATH_MAP.items(), key=lambda item: -len(item[0])):
        cmd = re.sub(
            rf"(?<![A-Za-z0-9_./-]){re.escape(filename)}(?![A-Za-z0-9_.-])",
            placeholder,
            cmd,
        )
    cmd = cmd.replace('"', "")
    cmd = re.sub(r"\s+", " ", cmd).strip()

    for base, user in USER_PAIRS:
        cmd = cmd.replace(base, f"{base} {user}")

    chunks = [chunk.strip() for chunk in cmd.split("--new") if chunk.strip()]
    rules = [f"{chunk} --new" for chunk in chunks]
    rules.extend(manager_extra_rules())
    return rules


def parse_config(text: str) -> str:
    """Текст JSON-файла goodbyeDPI-UI -> строка custom_parameters."""
    data = json.loads(text)
    command = data.get("custom_parameters") or ""
    return " ".join(str(command).split())


def convert_config(text: str) -> list[str]:
    """Текст JSON-файла goodbyeDPI-UI -> список правил."""
    return convert_command(parse_config(text))


def strategy_name(stem: str) -> str:
    """Имя файла стратегии для номера из репозитория."""
    return NAME_MAP.get(stem, f"GdpiUI_{stem}")


def collect(src_dir: Path) -> dict[str, list[str]]:
    """Все избранные JSON из каталога -> {имя стратегии: правила}."""
    result: dict[str, list[str]] = {}
    for path in sorted(Path(src_dir).glob("*.json")):
        stem = path.stem
        if stem not in NAME_MAP:
            continue
        result[NAME_MAP[stem]] = convert_config(path.read_text(encoding="utf-8"))
    return result


def strategy_checks(rules: list[str], known: set[str] | None = None) -> list[str]:
    """Проверки формата (возвращает список проблем)."""
    return validate(rules, known=known)
