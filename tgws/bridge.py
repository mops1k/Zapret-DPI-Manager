"""Мост между клиентом Telegram и удалённой стороной с перешифровкой.

Трафик клиента расшифровывается ключом dd-секрета и заново шифруется ключом
relay-init (и наоборот). Удалённой стороной может быть WebSocket (сервер
Telegram или Cloudflare-фолбэк), HTTP/2-туннель или обычный TCP.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Protocol

from .obfuscated2 import ReencryptContext
from .splitter import MtProtoSplitter

log = logging.getLogger("tgws.bridge")


class RemoteChannel(Protocol):
    """Минимальный интерфейс удалённой стороны (WS/H2)."""

    async def send(self, data: bytes) -> None: ...

    async def recv(self) -> Optional[bytes]: ...

    async def close(self) -> None: ...


def _bytes_human(n: int) -> str:
    value = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}ТБ"


async def bridge_channel(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    channel: RemoteChannel,
    ctx: ReencryptContext,
    *,
    label: str = "",
    splitter: Optional[MtProtoSplitter] = None,
    closing: Optional[asyncio.Event] = None,
) -> None:
    """Двунаправленный мост: TCP-клиент ↔ удалённый канал."""
    up_bytes = 0
    down_bytes = 0

    async def tcp_to_remote() -> None:
        nonlocal up_bytes
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    if splitter is not None:
                        tail = splitter.flush()
                        if tail:
                            await channel.send(tail[0])
                    break
                up_bytes += len(chunk)
                plain = ctx.clt_dec.update(chunk)
                encrypted = ctx.tg_enc.update(plain)
                if splitter is not None:
                    parts = splitter.split(encrypted, plain)
                    if not parts:
                        continue
                    send_batch = getattr(channel, "send_batch", None)
                    if len(parts) > 1 and send_batch is not None:
                        await send_batch(parts)
                    else:
                        for part in parts:
                            await channel.send(part)
                else:
                    await channel.send(encrypted)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
            log.debug("[%s] клиент → прокси завершён: %s", label, exc)

    async def remote_to_tcp() -> None:
        nonlocal down_bytes
        try:
            while True:
                data = await channel.recv()
                if data is None:
                    break
                down_bytes += len(data)
                plain = ctx.tg_dec.update(data)
                writer.write(ctx.clt_enc.update(plain))
                await writer.drain()
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as exc:
            log.debug("[%s] прокси → клиент завершён: %s", label, exc)

    tasks = [
        asyncio.create_task(tcp_to_remote(), name=f"{label}:up"),
        asyncio.create_task(remote_to_tcp(), name=f"{label}:down"),
    ]
    if closing is not None:
        tasks.append(asyncio.create_task(closing.wait(), name=f"{label}:stop"))
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except BaseException:  # noqa: BLE001 - гасим всё при закрытии
                pass
        log.info(
            "[%s] сессия закрыта: вверх %s, вниз %s",
            label,
            _bytes_human(up_bytes),
            _bytes_human(down_bytes),
        )
        try:
            await channel.close()
        except BaseException:  # noqa: BLE001
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except BaseException:  # noqa: BLE001
            pass
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass


async def bridge_tcp(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    remote_reader: asyncio.StreamReader,
    remote_writer: asyncio.StreamWriter,
    ctx: ReencryptContext,
    *,
    label: str = "",
) -> None:
    """Тот же мост, но удалённая сторона — обычный TCP (fallback на DC)."""

    async def forward(
        src: asyncio.StreamReader,
        dst: asyncio.StreamWriter,
        *,
        encrypt: bool,
    ) -> None:
        try:
            while True:
                data = await src.read(65536)
                if not data:
                    break
                if encrypt:
                    plain = ctx.clt_dec.update(data)
                    data = ctx.tg_enc.update(plain)
                else:
                    plain = ctx.tg_dec.update(data)
                    data = ctx.clt_enc.update(plain)
                dst.write(data)
                await dst.drain()
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as exc:
            log.debug("[%s] TCP-мост завершён: %s", label, exc)

    tasks = [
        asyncio.create_task(forward(reader, remote_writer, encrypt=True)),
        asyncio.create_task(forward(remote_reader, writer, encrypt=False)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except BaseException:  # noqa: BLE001
                pass
        for stream_writer in (writer, remote_writer):
            try:
                stream_writer.close()
                await stream_writer.wait_closed()
            except BaseException:  # noqa: BLE001
                pass
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
