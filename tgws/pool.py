"""Пул исходящих соединений к Telegram и Cloudflare-фолбэки.

Прямой путь: WebSocket к IP дата-центра с SNI ``kws{dc}.web.telegram.org``
(домен резолвить не нужно — IP берётся из таблицы DC, как в апстриме; при
блокировке пробуется SNI-fronting). Если он не работает — Cloudflare Worker
(``wss://<домен>/apiws?dst=<IP>&dc=<DC>``), затем CF-proxy
(``kws{dc}.<домен>``), затем обычный TCP на DC.

При неудаче DC помечается «недоступен» на время backoff, чтобы клиенты
пользователя не ждали таймаут на каждое подключение.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from . import cf
from .config import WS_PATH, WS_PATH_TEST, Config, DC_TEST_IPS, ws_domains
from .ws import WebSocket, WsError

log = logging.getLogger("tgws.pool")

#: Сколько держать прямой WS про запас и как часто обновлять.
WS_MAX_AGE = 120.0
WS_POOL_LIMIT = 4
DIRECT_BACKOFF = 90.0
FRONTING_SNI = "sprinthost.ru"
#: Домены, которые точно заблокированы/битые, — общий бэк-офф.
CF_BACKOFF = 180.0


class ChannelUnavailable(Exception):
    """Ни один из способов доставки до Telegram не сработал."""


class UpstreamPool:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._idle: Dict[Tuple[int, bool], Deque[Tuple[WebSocket, float]]] = {}
        self._direct_fail_until: Dict[Tuple[int, bool], float] = {}
        self._tcp_fail_until: Dict[str, float] = {}
        self._fronting_first = False
        self._cf_fail_until: Dict[str, float] = {}
        #: хуки для тестов: (dc, is_media) → готовый WebSocket или None
        self.direct_connector = None

    # --- прямой WebSocket ---------------------------------------------------

    async def acquire_direct(
        self, dc: int, is_media: bool, *, is_test_dc: bool = False
    ) -> Optional[WebSocket]:
        key = (dc, is_media)
        bucket = self._idle.setdefault(key, deque())
        now = time.monotonic()
        while bucket:
            ws, created = bucket.popleft()
            if now - created < WS_MAX_AGE and not ws.raw_closed():
                log.debug("WS из пула: DC%d%s", dc, "m" if is_media else "")
                return ws
            asyncio.create_task(self._quiet_close(ws))

        if self._direct_fail_until.get(key, 0.0) > now:
            return None

        target_ip = self.cfg.dc_redirects.get(dc)
        if not target_ip:
            return None

        path = WS_PATH_TEST if is_test_dc else WS_PATH
        ws = None
        if self.direct_connector is not None:
            try:
                ws = await self.direct_connector(dc, is_media)
            except Exception as exc:  # noqa: BLE001 - подменный коннектор в тестах
                log.debug("тестовый коннектор DC%d упал: %r", dc, exc)
        else:
            ws = await self._connect_direct(target_ip, dc, is_media, path)

        if ws is None:
            self._direct_fail_until[key] = now + DIRECT_BACKOFF
            log.info(
                "DC%d%s: прямой WS недоступен, backoff %.0f с",
                dc,
                "m" if is_media else "",
                DIRECT_BACKOFF,
            )
            return None

        self._direct_fail_until.pop(key, None)
        return ws

    async def _connect_direct(
        self, target_ip: str, dc: int, is_media: bool, path: str
    ) -> Optional[WebSocket]:
        for domain in ws_domains(dc, is_media):
            variants: List[Tuple[bool, bool]] = [(False, True), (True, False)]
            if self._fronting_first:
                variants.reverse()
            for frontal, verify in variants:
                sni = FRONTING_SNI if frontal else None
                try:
                    ws = await WebSocket.connect(
                        target_ip,
                        port=443,
                        host_header=domain,
                        path=path,
                        sni=sni,
                        secure=not self.cfg.disable_secure,
                        verify=verify,
                        timeout=8.0,
                        label=f"{domain}/{target_ip}",
                    )
                except (WsError, OSError, asyncio.TimeoutError) as exc:
                    log.debug(
                        "прямой WS %s через %s (fronting=%s) не вышел: %r",
                        domain,
                        target_ip,
                        frontal,
                        exc,
                    )
                    continue
                self._fronting_first = frontal
                log.info(
                    "прямой WS подключён: %s через %s (fronting=%s)",
                    domain,
                    target_ip,
                    frontal,
                )
                return ws
        return None

    # --- Cloudflare Worker --------------------------------------------------

    async def acquire_worker(
        self, dc: int, is_media: bool, is_test_dc: bool = False
    ) -> Optional[WebSocket]:
        domains = [
            domain
            for domain in self.cfg.cfproxy_worker_domains
            if self._cf_fail_until.get(domain, 0.0) <= time.monotonic()
        ]
        target_ip = (
            DC_TEST_IPS.get(dc) if is_test_dc else self.cfg.dc_redirects.get(dc)
        )
        if not domains or not target_ip:
            return None
        query = urlencode({"dst": target_ip, "dc": str(dc)})
        for domain in domains:
            try:
                ws = await WebSocket.connect(
                    domain,
                    port=443,
                    host_header=domain,
                    path=f"{WS_PATH}?{query}",
                    secure=not self.cfg.disable_secure,
                    timeout=10.0,
                    label=f"worker:{domain}",
                )
            except (WsError, OSError, asyncio.TimeoutError) as exc:
                log.debug("CF worker %s не ответил: %r", domain, exc)
                self._cf_fail_until[domain] = time.monotonic() + CF_BACKOFF
                continue
            log.info("DC%d: подключение через CF worker %s", dc, domain)
            return ws
        return None

    # --- Cloudflare proxy (kws{dc}.<домен>) ---------------------------------

    async def acquire_cfproxy(
        self, dc: int, is_media: bool, is_test_dc: bool = False
    ) -> Optional[WebSocket]:
        path = WS_PATH_TEST if is_test_dc else WS_PATH
        for base in cf.balancer.ordered_for(dc):
            if self._cf_fail_until.get(base, 0.0) > time.monotonic():
                continue
            domain = f"kws{dc}.{base}"
            try:
                ws = await WebSocket.connect(
                    domain,
                    port=443,
                    host_header=domain,
                    path=path,
                    secure=not self.cfg.disable_secure,
                    timeout=10.0,
                    label=f"cf:{domain}",
                )
            except (WsError, OSError, asyncio.TimeoutError) as exc:
                log.debug("CF proxy %s не ответил: %r", domain, exc)
                self._cf_fail_until[base] = time.monotonic() + CF_BACKOFF
                continue
            cf.balancer.mark_working(dc, base)
            log.info("DC%d%s: подключение через CF proxy %s", dc, "m" if is_media else "", domain)
            return ws
        return None

    # --- прямой TCP (аварийный) --------------------------------------------

    async def acquire_tcp(
        self, dc: int, *, is_test_dc: bool = False
    ) -> Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
        target_ip = (
            DC_TEST_IPS.get(dc) if is_test_dc else self.cfg.dc_redirects.get(dc)
        )
        if not target_ip:
            return None
        if self._tcp_fail_until.get(target_ip, 0.0) > time.monotonic():
            return None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target_ip, 443), timeout=8.0
            )
        except (OSError, asyncio.TimeoutError) as exc:
            log.debug("TCP-фолбэк к %s не удался: %r", target_ip, exc)
            self._tcp_fail_until[target_ip] = time.monotonic() + DIRECT_BACKOFF
            return None
        log.info("DC%d: TCP-фолбэк к %s", dc, target_ip)
        return reader, writer

    # --- обслуживание -------------------------------------------------------

    async def _quiet_close(self, ws: WebSocket) -> None:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        for bucket in self._idle.values():
            for ws, _created in bucket:
                await self._quiet_close(ws)
        self._idle.clear()
