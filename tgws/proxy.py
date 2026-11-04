"""Asyncio-сервер прокси: принимает MTProto-клиентов и ведёт их в Telegram.

Порядок фолбэков для каждого клиента (как у апстрима):

1. пул прямых WebSocket к DC (``kws{dc}.web.telegram.org`` через IP DC);
2. Cloudflare Worker-домены пользователя (``/apiws?dst=…&dc=…``);
3. Cloudflare-proxy домены (``kws{dc}.<домен>``), для медиа — HTTP/2;
4. обычный TCP к DC (когда WebSocket недоступен совсем).
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
import sys
from typing import Optional, Tuple

from . import cf
from .config import Config
from .obfuscated2 import (
    HANDSHAKE_LEN,
    ClientHandshake,
    build_reencrypt_context,
    generate_relay_init,
    parse_client_handshake,
)
from .pool import UpstreamPool
from .splitter import MtProtoSplitter
from .bridge import bridge_channel, bridge_tcp

log = logging.getLogger("tgws.proxy")

#: Код ошибки для журнала, когда клиент пришёл не с нашим секретом.
BAD_HANDSHAKE_DRAIN = 64 * 1024


def _set_nodelay(writer: asyncio.StreamWriter) -> None:
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        import socket as _socket

        sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
    except OSError:
        pass


class TgWsProxy:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.pool = UpstreamPool(cfg)
        self._server: Optional[asyncio.AbstractServer] = None
        self._clients: set[asyncio.Task] = set()
        self._stopping = False
        self.h2_pool = None
        self._cf_worker_pool = None

    # --- служебное ----------------------------------------------------------

    @property
    def secret_bytes(self) -> bytes:
        return bytes.fromhex(self.cfg.secret)

    def link(self) -> str:
        host = self.cfg.host
        if host in ("0.0.0.0", "::"):
            host = _local_ipv4()
        return (
            f"tg://proxy?server={host}&port={self.cfg.port}"
            f"&secret=dd{self.cfg.secret}"
        )

    def fake_tls_link(self) -> str:
        if not self.cfg.fake_tls_domain:
            return ""
        domain_hex = self.cfg.fake_tls_domain.encode("ascii").hex()
        return (
            f"tg://proxy?server={_local_ipv4() if self.cfg.host in ('0.0.0.0', '::') else self.cfg.host}"
            f"&port={self.cfg.port}&secret=ee{self.cfg.secret}{domain_hex}"
        )

    # --- жизненный цикл -----------------------------------------------------

    async def start(self) -> None:
        cf.balancer.update_domains(cf.initial_domains())
        if self.cfg.cfproxy_user_domains:
            cf.balancer.update_domains(self.cfg.cfproxy_user_domains)
        elif self.cfg.fallback_cfproxy:
            cf.start_refresh()

        if self.cfg.h2_enabled:
            try:
                from .h2 import H2MediaPool

                self.h2_pool = H2MediaPool(self.cfg)
            except Exception as exc:  # noqa: BLE001 - H2 не критичен
                log.warning("HTTP/2-фолбэк недоступен: %r", exc)
                self.h2_pool = None

        self._server = await asyncio.start_server(
            self._on_client, self.cfg.host, self.cfg.port
        )
        for sock in self._server.sockets or ():
            try:
                import socket as _socket

                sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
            except OSError:
                pass

        log.info("=" * 60)
        log.info("  Telegram MTProto WS Bridge Proxy")
        log.info("  Слушает       %s:%d", self.cfg.host, self.cfg.port)
        log.info("  Секрет        %s", self.cfg.secret)
        if self.cfg.fake_tls_domain:
            log.info("  Fake TLS      %s", self.cfg.fake_tls_domain)
        log.info("  DC: %s", ", ".join(
            f"{dc}={ip}" for dc, ip in sorted(self.cfg.dc_redirects.items())
        ) or "—")
        if self.cfg.fallback_cfproxy:
            log.info(
                "  CF-фолбэк     %s",
                ", ".join(self.cfg.cfproxy_user_domains) or "авто",
            )
        if self.cfg.cfproxy_worker_domains:
            log.info("  CF worker     %s", ", ".join(self.cfg.cfproxy_worker_domains))
        if self.h2_pool is not None:
            log.info("  CF H2 медиа   включено")
        log.info("  Ссылка для Telegram:")
        log.info("    %s", self.link())
        if self.fake_tls_link():
            log.info("    %s", self.fake_tls_link())
        log.info("=" * 60)

    async def serve(self, stop_event: Optional[asyncio.Event] = None) -> None:
        await self.start()
        assert self._server is not None
        try:
            if stop_event is None:
                await self._server.serve_forever()
            else:
                waiters = [
                    asyncio.create_task(self._server.serve_forever()),
                    asyncio.create_task(stop_event.wait()),
                ]
                try:
                    await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in waiters:
                        task.cancel()
                    await asyncio.gather(*waiters, return_exceptions=True)
        finally:
            await self.close()

    async def close(self) -> None:
        self._stopping = True
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            self._server = None
        tasks = list(self._clients)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._clients.clear()
        await self.pool.close()
        if self.h2_pool is not None:
            try:
                await self.h2_pool.close()
            except Exception:  # noqa: BLE001
                pass
            self.h2_pool = None

    # --- клиенты ------------------------------------------------------------

    def _on_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._stopping:
            writer.close()
            return
        task = asyncio.create_task(self._handle_client(reader, writer))
        self._clients.add(task)
        task.add_done_callback(self._clients.discard)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        _set_nodelay(writer)
        peer = writer.get_extra_info("peername")
        label = f"{peer[0]}:{peer[1]}" if peer else "?"
        try:
            init = await self._read_client_init(reader, writer, label)
            if init is None:
                return
            handshake, clt_reader, clt_writer = init

            relay_init = generate_relay_init(
                handshake.proto_tag,
                -handshake.dc if handshake.is_media else handshake.dc,
            )
            ctx = build_reencrypt_context(
                handshake, self.secret_bytes, relay_init
            )

            ok = await self._attach(handshake, relay_init, ctx, clt_reader, clt_writer, label)
            if not ok:
                log.warning("[%s] DC%d: нет доступного транспорта", label, handshake.dc)

        except asyncio.CancelledError:
            raise
        except asyncio.IncompleteReadError:
            log.debug("[%s] клиент отключился на хендшейке", label)
        except asyncio.TimeoutError:
            log.debug("[%s] таймаут хендшейка", label)
        except (ConnectionError, OSError) as exc:
            log.debug("[%s] обрыв соединения: %r", label, exc)
        except Exception as exc:  # noqa: BLE001 - один клиент не должен ронять сервер
            log.error("[%s] неожиданная ошибка: %r", label, exc, exc_info=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except BaseException:  # noqa: BLE001
                pass

    async def _read_client_init(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        label: str,
    ) -> Optional[Tuple[ClientHandshake, object, object]]:
        """Читает init клиента: obfuscated2 (dd) или fake-TLS (ee)."""
        if self.cfg.proxy_protocol:
            try:
                header = await asyncio.wait_for(reader.readline(), timeout=10)
            except asyncio.IncompleteReadError:
                return None
            if not header.startswith(b"PROXY "):
                log.debug("[%s] ожидалась PROXY-строка, получено %r", label, header[:40])
                return None

        try:
            first = await asyncio.wait_for(reader.readexactly(1), timeout=10)
        except asyncio.IncompleteReadError:
            return None

        if first[0] == 0x16 and self.cfg.fake_tls_domain:
            return await self._read_fake_tls(reader, writer, first, label)

        try:
            rest = await asyncio.wait_for(
                reader.readexactly(HANDSHAKE_LEN - 1), timeout=10
            )
        except asyncio.IncompleteReadError:
            return None

        handshake_bytes = first + rest
        handshake = parse_client_handshake(handshake_bytes, self.secret_bytes)
        if handshake is None:
            log.warning("[%s] плохой хендшейк (не тот секрет?)", label)
            await self._drain(reader)
            return None
        log.debug(
            "[%s] хендшейк: DC%d%s proto=%#x",
            label,
            handshake.dc,
            " медиа" if handshake.is_media else "",
            handshake.proto_int,
        )
        return handshake, reader, writer

    async def _read_fake_tls(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        first: bytes,
        label: str,
    ) -> Optional[Tuple[ClientHandshake, object, object]]:
        from . import faketls

        try:
            header_rest = await asyncio.wait_for(reader.readexactly(4), timeout=10)
            record_len = struct.unpack(">H", header_rest[3:5])[0]
            record_body = await asyncio.wait_for(
                reader.readexactly(record_len), timeout=10
            )
        except asyncio.IncompleteReadError:
            return None
        record = first + header_rest + record_body

        tls = faketls.verify_client_hello(record, self.secret_bytes)
        if tls is None:
            log.debug("[%s] fake-TLS: ClientHello не наш — маскировка", label)
            await faketls.proxy_to_masking_domain(
                reader, writer, record, self.cfg.fake_tls_domain, label
            )
            return None

        client_random, session_id, _timestamp = tls
        await asyncio.sleep(0.008 + 0.016 * (os.urandom(1)[0] / 255.0))
        writer.write(
            faketls.build_server_hello(
                self.secret_bytes,
                client_random,
                session_id,
                faketls.parse_client_hello(record),
            )
        )
        await writer.drain()

        stream = faketls.FakeTlsStream(reader, writer)
        try:
            handshake_bytes = await asyncio.wait_for(
                stream.readexactly(HANDSHAKE_LEN), timeout=10
            )
        except asyncio.IncompleteReadError:
            return None

        handshake = parse_client_handshake(handshake_bytes, self.secret_bytes)
        if handshake is None:
            log.warning("[%s] fake-TLS: init внутри TLS не разобран", label)
            return None
        return handshake, stream, stream

    async def _drain(self, reader: asyncio.StreamReader) -> None:
        remaining = BAD_HANDSHAKE_DRAIN
        while remaining > 0:
            try:
                chunk = await asyncio.wait_for(
                    reader.read(min(4096, remaining)), timeout=3
                )
            except Exception:  # noqa: BLE001
                return
            if not chunk:
                return
            remaining -= len(chunk)

    # --- маршрутизация ------------------------------------------------------

    async def _attach(
        self,
        handshake: ClientHandshake,
        relay_init: bytes,
        ctx,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        label: str,
    ) -> bool:
        dc = handshake.dc
        is_media = handshake.is_media
        is_test_dc = self.cfg.force_test_dc or dc >= 10000
        if dc >= 10000:
            dc -= 10000

        try:
            splitter = MtProtoSplitter(handshake.proto_int)
        except ValueError:
            splitter = None

        # 1. прямой WS (пул)
        ws = await self.pool.acquire_direct(dc, is_media, is_test_dc=is_test_dc)
        if ws is not None:
            await ws.send(relay_init)
            await bridge_channel(
                reader, writer, ws, ctx, label=label, splitter=splitter
            )
            return True

        # 2. Cloudflare Worker
        ws = await self.pool.acquire_worker(dc, is_media, is_test_dc)
        if ws is not None:
            await ws.send(relay_init)
            await bridge_channel(
                reader, writer, ws, ctx, label=label, splitter=splitter
            )
            return True

        # 3. Cloudflare proxy / HTTP/2 (медиа)
        if is_media and self.h2_pool is not None:
            channel = await self.h2_pool.open(dc, label, handshake)
            if channel is not None:
                await channel.start(relay_init)
                await bridge_channel(
                    reader, writer, channel, ctx, label=label, splitter=None
                )
                return True

        ws = await self.pool.acquire_cfproxy(dc, is_media, is_test_dc)
        if ws is not None:
            await ws.send(relay_init)
            await bridge_channel(
                reader, writer, ws, ctx, label=label, splitter=splitter
            )
            return True

        # 4. обычный TCP к DC
        tcp = await self.pool.acquire_tcp(dc, is_test_dc=is_test_dc)
        if tcp is not None:
            remote_reader, remote_writer = tcp
            remote_writer.write(relay_init)
            await remote_writer.drain()
            await bridge_tcp(
                reader, writer, remote_reader, remote_writer, ctx, label=label
            )
            return True
        return False


def _local_ipv4() -> str:
    import socket as _socket

    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _setup_logging(cfg: Config) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if cfg.verbose else logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-5s  %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)
    if cfg.log_file:
        try:
            from logging.handlers import RotatingFileHandler

            handler = RotatingFileHandler(
                cfg.log_file, maxBytes=2 * 1024 * 1024, backupCount=2
            )
            handler.setFormatter(fmt)
            root.addHandler(handler)
        except OSError as exc:
            log.error("лог-файл %s недоступен: %s", cfg.log_file, exc)


def make_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Telegram MTProto ↔ WebSocket прокси (Zapret DPI Manager)"
    )
    parser.add_argument("--host", default="127.0.0.1", help="адрес прослушивания")
    parser.add_argument("--port", type=int, default=1443, help="порт прослушивания")
    parser.add_argument(
        "--secret",
        default="",
        help="секрет MTProto (32 hex); пусто — сгенерировать случайный",
    )
    parser.add_argument(
        "--dc-ip",
        action="append",
        metavar="DC:IP",
        default=None,
        help="целевой IP для DC (можно несколько раз)",
    )
    parser.add_argument(
        "--cfproxy-domain",
        action="append",
        default=None,
        metavar="DOMAIN",
        help="свой Cloudflare-домен для фолбэка (kws{dc}.<домен>)",
    )
    parser.add_argument(
        "--cfproxy-worker-domain",
        action="append",
        default=None,
        metavar="DOMAIN",
        help="Cloudflare Worker домен для фолбэка",
    )
    parser.add_argument("--no-h2", action="store_true", help="выключить HTTP/2-медиа")
    parser.add_argument(
        "--no-cfproxy", action="store_true", help="выключить Cloudflare-фолбэк"
    )
    parser.add_argument(
        "--no-secure", action="store_true", help="CF-фолбэк по HTTP (порт 80)"
    )
    parser.add_argument(
        "--fake-tls-domain",
        default="",
        metavar="DOMAIN",
        help="домен маскировки fake-TLS (включает ee-секрет)",
    )
    parser.add_argument(
        "--proxy-protocol",
        action="store_true",
        help="принимать PROXY protocol v1",
    )
    parser.add_argument(
        "--force-test-dc",
        action="store_true",
        help="все соединения — в тестовые DC Telegram",
    )
    parser.add_argument("--log-file", default="", help="файл журнала")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    return parser


def config_from_args(argv=None) -> Config:
    args = make_parser().parse_args(argv)
    cfg = Config(
        host=args.host,
        port=args.port,
        verbose=args.verbose,
        log_file=args.log_file,
        fallback_cfproxy=not args.no_cfproxy,
        cfproxy_user_domains=list(args.cfproxy_domain or []),
        cfproxy_worker_domains=list(args.cfproxy_worker_domain or []),
        h2_media=not args.no_h2,
        disable_secure=args.no_secure,
        fake_tls_domain=args.fake_tls_domain,
        proxy_protocol=args.proxy_protocol,
        force_test_dc=args.force_test_dc,
    )
    if args.dc_ip:
        redirects = {}
        if None in args.dc_ip:
            cfg.dc_redirects = {}
        else:
            for item in args.dc_ip:
                try:
                    dc_text, ip_text = item.split(":", 1)
                    redirects[int(dc_text)] = ip_text.strip()
                except ValueError as exc:
                    raise SystemExit(f"неверный --dc-ip {item!r}: {exc}")
            cfg.dc_redirects = redirects

    if args.secret:
        secret = args.secret.strip().lower()
        if len(secret) != 32:
            raise SystemExit("--secret должен быть 32 hex-символа")
        try:
            bytes.fromhex(secret)
        except ValueError:
            raise SystemExit("--secret должен быть hex-строкой")
        cfg.secret = secret
    else:
        cfg.secret = os.urandom(16).hex()
    return cfg


def main(argv=None) -> None:
    cfg = config_from_args(argv)
    _setup_logging(cfg)
    proxy = TgWsProxy(cfg)
    try:
        asyncio.run(proxy.serve())
    except KeyboardInterrupt:
        log.info("остановлено пользователем")


if __name__ == "__main__":
    main()
