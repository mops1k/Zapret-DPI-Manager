"""Минимальный WebSocket (RFC 6455) на asyncio без внешних зависимостей.

Нужен ровно один сценарий: туннель MTProto к серверам Telegram по
`/apiws` (binary-подпротокол). Поэтому реализованы: HTTP-upgrade через TLS,
клиентские кадры с маскированием, сборка фрагментированных сообщений,
ответы на ping, close-кадры. Серверная часть (``accept``) нужна тестам и
используется в ``tools/check_tgws.py``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import ssl
import struct
from typing import Dict, List, Optional, Tuple

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE_LEN = 32 * 1024 * 1024
MAX_FRAME_LEN = 32 * 1024 * 1024

_st_BB = struct.Struct(">BB")
_st_BBH = struct.Struct(">BBH")
_st_BBQ = struct.Struct(">BBQ")
_st_H = struct.Struct(">H")
_st_Q = struct.Struct(">Q")

_XOR_TABLES = tuple(bytes(b ^ key for b in range(256)) for key in range(256))


def _xor_mask(data: bytes, mask: bytes) -> bytes:
    """XOR данных 4-байтной маской (быстро для больших блоков)."""
    length = len(data)
    if length == 0:
        return data
    if length < 512:
        repeated = (mask * (length // 4 + 1))[:length]
        value = int.from_bytes(data, "big") ^ int.from_bytes(repeated, "big")
        return value.to_bytes(length, "big")
    masked = bytearray(data)
    for offset in range(4):
        masked[offset::4] = masked[offset::4].translate(_XOR_TABLES[mask[offset]])
    return bytes(masked)


class WsError(Exception):
    """Ошибка WebSocket-соединения."""


class WsHandshakeError(WsError):
    """Сервер не переключился на WebSocket."""

    def __init__(
        self,
        status_code: int,
        status_line: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self.status_line = status_line
        self.headers = headers or {}
        super().__init__(f"HTTP {status_code}: {status_line}")

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308)


def _build_frame(opcode: int, data: bytes, mask: bool) -> bytes:
    length = len(data)
    first = 0x80 | opcode
    if mask:
        key = os.urandom(4)
        masked = _xor_mask(data, key)
        if length < 126:
            return _st_BB.pack(first, 0x80 | length) + key + masked
        if length < 65536:
            return _st_BBH.pack(first, 0x80 | 126, length) + key + masked
        return _st_BBQ.pack(first, 0x80 | 127, length) + key + masked
    if length < 126:
        return _st_BB.pack(first, length) + data
    if length < 65536:
        return _st_BBH.pack(first, 126, length) + data
    return _st_BBQ.pack(first, 127, length) + data


def _accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


class WebSocket:
    """WebSocket поверх готовых asyncio reader/writer."""

    __slots__ = ("reader", "writer", "_closed", "_frag", "_mask_out", "_label")

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        mask_out: bool = True,
        label: str = "",
    ) -> None:
        self.reader = reader
        self.writer = writer
        self._closed = False
        self._frag = bytearray()
        self._mask_out = mask_out
        self._label = label

    # --- открытие соединения ------------------------------------------------

    @staticmethod
    async def connect(
        host: str,
        *,
        port: int = 443,
        host_header: Optional[str] = None,
        path: str = "/apiws",
        sni: Optional[str] = None,
        secure: bool = True,
        verify: bool = True,
        timeout: float = 10.0,
        subprotocol: str = "binary",
        label: str = "",
    ) -> "WebSocket":
        """Открывает WS-соединение и проверяет ответ 101."""
        server_name = sni if sni is not None else host_header or host
        context = None
        if secure:
            context = ssl.create_default_context()
            if not verify:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host,
                    port,
                    ssl=context,
                    server_hostname=server_name if secure else None,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise WsError(f"таймаут подключения к {host}:{port}") from exc
        except (OSError, ssl.SSLError) as exc:
            raise WsError(f"подключение к {host}:{port} не удалось: {exc}") from exc

        try:
            sock = writer.get_extra_info("socket")
            if sock is not None:
                try:
                    import socket as _socket

                    sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
                except OSError:
                    pass
            await WebSocket._client_handshake(
                reader,
                writer,
                host_header or server_name,
                path,
                timeout,
                subprotocol,
            )
        except BaseException:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            raise
        return WebSocket(reader, writer, mask_out=True, label=label)

    @staticmethod
    async def _client_handshake(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host_header: str,
        path: str,
        timeout: float,
        subprotocol: str,
    ) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
        )
        if subprotocol:
            request += f"Sec-WebSocket-Protocol: {subprotocol}\r\n"
        request += "\r\n"
        writer.write(request.encode("ascii"))
        await writer.drain()

        lines: List[str] = []
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if line in (b"\r\n", b"\n", b""):
                break
            lines.append(line.decode("utf-8", errors="replace").strip())
        if not lines:
            raise WsError("пустой ответ на WebSocket-апгрейд")

        parts = lines[0].split(" ", 2)
        try:
            status = int(parts[1]) if len(parts) >= 2 else 0
        except ValueError:
            status = 0
        headers: Dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()

        if status != 101:
            raise WsHandshakeError(status, lines[0], headers)
        expected = _accept_key(key)
        got = headers.get("sec-websocket-accept", "")
        if got != expected:
            raise WsError(
                f"неверный Sec-WebSocket-Accept: {got!r} вместо {expected!r}"
            )

    @staticmethod
    async def accept(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        timeout: float = 10.0,
        label: str = "",
    ) -> Optional["WebSocket"]:
        """Серверная сторона апгрейда (для тестов)."""
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        if not request_line:
            return None
        headers: Dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            text = line.decode("utf-8", errors="replace").strip()
            if ":" in text:
                name, value = text.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        key = headers.get("sec-websocket-key", "")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {_accept_key(key)}\r\n"
            "Sec-WebSocket-Protocol: binary\r\n"
            "\r\n"
        )
        writer.write(response.encode("ascii"))
        await writer.drain()
        return WebSocket(reader, writer, mask_out=False, label=label)

    # --- передача -----------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    def raw_closed(self) -> bool:
        try:
            return (
                self._closed
                or self.writer.transport.is_closing()
                or self.reader.at_eof()
                or self.reader.exception() is not None
            )
        except Exception:  # noqa: BLE001
            return True

    async def send(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionError("WebSocket закрыт")
        self.writer.write(_build_frame(OP_BINARY, data, self._mask_out))
        await self.writer.drain()

    async def send_batch(self, parts: List[bytes]) -> None:
        if self._closed:
            raise ConnectionError("WebSocket закрыт")
        for part in parts:
            self.writer.write(_build_frame(OP_BINARY, part, self._mask_out))
        await self.writer.drain()

    async def recv(self) -> Optional[bytes]:
        """Следующее бинарное сообщение; ``None`` — соединение закрыто."""
        while not self._closed:
            opcode, payload, fin = await self._read_frame()

            if opcode == OP_CLOSE:
                self._closed = True
                try:
                    self.writer.write(
                        _build_frame(OP_CLOSE, payload[:2], self._mask_out)
                    )
                    await self.writer.drain()
                except Exception:  # noqa: BLE001 - закрываемся как получится
                    pass
                return None
            if opcode == OP_PING:
                try:
                    self.writer.write(
                        _build_frame(OP_PONG, payload, self._mask_out)
                    )
                    await self.writer.drain()
                except Exception:  # noqa: BLE001
                    pass
                continue
            if opcode == OP_PONG:
                continue
            if opcode in (OP_CONT, OP_TEXT, OP_BINARY):
                if fin and not self._frag:
                    return payload
                self._frag.extend(payload)
                if len(self._frag) > MAX_MESSAGE_LEN:
                    raise WsError(f"WS-сообщение слишком большое: {len(self._frag)}")
                if not fin:
                    continue
                message = bytes(self._frag)
                self._frag.clear()
                return message
            continue
        return None

    async def close(self) -> None:
        try:
            if not self._closed:
                self._closed = True
                try:
                    self.writer.write(_build_frame(OP_CLOSE, b"", self._mask_out))
                    await self.writer.drain()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    # --- кадры --------------------------------------------------------------

    async def _read_frame(self) -> Tuple[int, bytes, bool]:
        header = await self.reader.readexactly(2)
        fin = bool(header[0] & 0x80)
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if length == 126:
            length = _st_H.unpack(await self.reader.readexactly(2))[0]
        elif length == 127:
            length = _st_Q.unpack(await self.reader.readexactly(8))[0]
        if length > MAX_FRAME_LEN:
            raise WsError(f"WS-кадр слишком большой: {length}")
        masked = bool(header[1] & 0x80)
        key = await self.reader.readexactly(4) if masked else b""
        payload = await self.reader.readexactly(length) if length else b""
        if masked:
            payload = _xor_mask(payload, key)
        return opcode, payload, fin
