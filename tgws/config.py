"""Настройки движка TG WS Proxy.

Все значения можно задать аргументами командной строки (``python -m tgws``);
никаких файлов конфигурации движок не читает — параметры приходят из юнита
systemd, которые генерирует приложение.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

#: IP дата-центров Telegram, к которым подключается прокси (WS-хендшейк идёт
#: на эти адреса, SNI — ``kws{dc}.web.telegram.org``).
DC_DEFAULT_IPS: Dict[int, str] = {
    1: "149.154.175.50",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "149.154.171.5",
    203: "91.105.192.100",
}

#: Тестовые DC (для ``--force-test-dc``): id сдвинут на 10000.
DC_TEST_IPS: Dict[int, str] = {
    1: "149.154.175.10",
    2: "149.154.167.40",
    3: "149.154.175.117",
}

WS_PATH = "/apiws"
WS_PATH_TEST = "/apiws_test"

#: Запасные Cloudflare-домены прокси (аналог cfproxy-domains у апстрима).
#: Основной список скачивается с GitHub и кэшируется в ``~/.cache``.
CFPROXY_DEFAULT_DOMAINS: List[str] = [
    "virkgj.com",
    "vmmzovy.com",
    "mkuosckvso.com",
    "zaewayzmplad.com",
    "twdmbzcm.com",
    "awzwsldi.com",
    "clngqrflngqin.com",
    "tjacxbqtj.com",
    "bxaxtxmrw.com",
    "dmohrsgmohcrwb.com",
]


def ws_domains(dc: int, is_media: bool) -> List[str]:
    """SNI-домены для DC (медиа-DC 203 обслуживается как DC2)."""
    if dc == 203:
        dc = 2
    if not is_media:
        return [f"kws{dc}.web.telegram.org"]
    return [f"kws{dc}-1.web.telegram.org", f"kws{dc}.web.telegram.org"]


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 1443
    secret: str = ""
    verbose: bool = False
    log_file: str = ""
    buffer_size: int = 256 * 1024

    #: DC -> целевой IP (по умолчанию — официальные адреса).
    dc_redirects: Dict[int, str] = field(
        default_factory=lambda: dict(DC_DEFAULT_IPS)
    )

    #: Опции фолбэков (по умолчанию всё включено, как у апстрима).
    fallback_cfproxy: bool = True
    cfproxy_user_domains: List[str] = field(default_factory=list)
    cfproxy_worker_domains: List[str] = field(default_factory=list)
    h2_media: bool = True
    disable_secure: bool = False
    pool_size: int = 4

    fake_tls_domain: str = ""
    proxy_protocol: bool = False
    force_test_dc: bool = False

    @property
    def h2_enabled(self) -> bool:
        return (
            self.h2_media
            and self.fallback_cfproxy
            and not self.disable_secure
            and not self.force_test_dc
        )
