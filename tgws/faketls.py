"""Маскировка под TLS (ee-секрет) для обхода DPI по SNI.

Протокол (как в MTProxy и tg-ws-proxy): клиент с секретом ``ee<secret><domain>``
открывает соединение не obfuscated2-init'ом, а настоящим TLS ClientHello, в
random которого спрятаны HMAC от secrета и текущая метка времени. Прокси
проверяет HMAC, отвечает «правдоподобным» ServerHello (TLS 1.3) и дальше
обменивается TLS ApplicationData-записями, внутри которых идёт обычный
obfuscated2-поток.

Криптографии здесь нет: ServerHello и записи — маскировка, ключи не
вырабатываются (клиент тоже их не использует). Если HMAC не сходится,
соединение проксируется на домен маскировки — для DPI это обычный TLS.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import struct
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

log = logging.getLogger("tgws.faketls")

TLS_RECORD_HANDSHAKE = 0x16
TLS_RECORD_CCS = 0x14
TLS_RECORD_APPDATA = 0x17

TLS_VERSION_12 = b"\x03\x03"
TLS_APPDATA_MAX = 16384

CLIENT_RANDOM_OFFSET = 11
CLIENT_RANDOM_LEN = 32
SESSION_ID_OFFSET = 44
SESSION_ID_LEN = 32
TIMESTAMP_TOLERANCE = 120

EXT_SUPPORTED_VERSIONS = 0x002B
EXT_KEY_SHARE = 0x0033
GROUP_X25519 = 0x001D
GROUP_X25519MLKEM768 = 0x11EC
X25519_KEY_LEN = 32
X25519MLKEM768_CLIENT_SHARE_LEN = 1216
MLKEM768_CIPHERTEXT_LEN = 1088
X25519MLKEM768_GROUP = 0x11EC

CIPHER_PREFERENCE = (0x1301, 0x1303, 0x1302)
CCS_FRAME = b"\x14\x03\x03\x00\x01\x01"
SERVER_RANDOM_OFFSET = 11
APPDATA_MIN = 64
FALLBACK_APPDATA_RANGE = (64, 160)


@dataclass(frozen=True)
class HelloInfo:
    cipher_suites: frozenset
    key_shares: Dict[int, int]


EMPTY_HELLO = HelloInfo(frozenset(), {})


def verify_client_hello(data: bytes, secret: bytes) -> Optional[Tuple[bytes, bytes, int]]:
    """Проверяет HMAC и метку времени в ClientHello.

    Возвращает ``(client_random, session_id, timestamp)`` или ``None``.
    """
    if len(data) < 43 or data[0] != TLS_RECORD_HANDSHAKE or data[5] != 0x01:
        return None

    client_random = data[CLIENT_RANDOM_OFFSET:CLIENT_RANDOM_OFFSET + CLIENT_RANDOM_LEN]
    zeroed = bytearray(data)
    zeroed[CLIENT_RANDOM_OFFSET:CLIENT_RANDOM_OFFSET + CLIENT_RANDOM_LEN] = (
        b"\x00" * CLIENT_RANDOM_LEN
    )
    expected = hmac.new(secret, bytes(zeroed), hashlib.sha256).digest()
    if not hmac.compare_digest(expected[:28], client_random[:28]):
        return None

    ts_xor = bytes(client_random[28 + i] ^ expected[28 + i] for i in range(4))
    timestamp = struct.unpack("<I", ts_xor)[0]
    if abs(int(time.time()) - timestamp) > TIMESTAMP_TOLERANCE:
        return None

    session_id = b"\x00" * SESSION_ID_LEN
    if len(data) >= SESSION_ID_OFFSET + SESSION_ID_LEN and data[43] == 0x20:
        session_id = data[SESSION_ID_OFFSET:SESSION_ID_OFFSET + SESSION_ID_LEN]
    return client_random, session_id, timestamp


def parse_client_hello(data: bytes) -> HelloInfo:
    """Достаёт список шифров и key_share-группы (для правдоподобного ответа)."""
    try:
        pos = 5 + 4 + 2 + CLIENT_RANDOM_LEN
        pos += 1 + data[pos]  # session_id

        suites_len = struct.unpack_from(">H", data, pos)[0]
        pos += 2
        suites_end = pos + suites_len
        if suites_end > len(data):
            return EMPTY_HELLO
        suites = frozenset(
            struct.unpack_from(">H", data, off)[0]
            for off in range(pos, suites_end - 1, 2)
        )
        pos = suites_end

        pos += 1 + data[pos]  # compression methods
        ext_total = struct.unpack_from(">H", data, pos)[0]
        pos += 2
        ext_end = min(pos + ext_total, len(data))
        key_shares: Dict[int, int] = {}
        while pos + 4 <= ext_end:
            ext_type, ext_len = struct.unpack_from(">HH", data, pos)
            pos += 4
            if ext_type == EXT_KEY_SHARE and pos + 2 <= ext_end:
                shares_end = min(
                    pos + 2 + struct.unpack_from(">H", data, pos)[0], ext_end
                )
                off = pos + 2
                while off + 4 <= shares_end:
                    group, key_len = struct.unpack_from(">HH", data, off)
                    off += 4 + key_len
                    if off <= shares_end:
                        key_shares.setdefault(group, key_len)
            pos += ext_len
    except (IndexError, struct.error):
        return EMPTY_HELLO
    return HelloInfo(suites, key_shares)


def _x25519_public_key() -> bytes:
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import (
            X25519PrivateKey,
        )

        return X25519PrivateKey.generate().public_key().public_bytes_raw()
    except Exception:  # noqa: BLE001 - маскировка, ключ не используется
        key = bytearray(os.urandom(X25519_KEY_LEN))
        key[-1] &= 0x7F
        return bytes(key)


def _server_key_share(info: HelloInfo) -> Tuple[int, bytes]:
    if info.key_shares.get(GROUP_X25519MLKEM768) == X25519MLKEM768_CLIENT_SHARE_LEN:
        return (
            GROUP_X25519MLKEM768,
            os.urandom(MLKEM768_CIPHERTEXT_LEN) + _x25519_public_key(),
        )
    return GROUP_X25519, _x25519_public_key()


def _select_cipher_suite(info: HelloInfo) -> int:
    for suite in CIPHER_PREFERENCE:
        if suite in info.cipher_suites:
            return suite
    return CIPHER_PREFERENCE[0]


def build_server_hello(
    secret: bytes,
    client_random: bytes,
    session_id: bytes,
    info: Optional[HelloInfo] = None,
) -> bytes:
    """ServerHello + ChangeCipherSpec + один ApplicationData-рекорд."""
    info = info or EMPTY_HELLO
    group, key_exchange = _server_key_share(info)
    extensions = (
        struct.pack(">HHH", EXT_SUPPORTED_VERSIONS, 2, 0x0304)
        + struct.pack(
            ">HHHH", EXT_KEY_SHARE, 4 + len(key_exchange), group, len(key_exchange)
        )
        + key_exchange
    )
    body = (
        TLS_VERSION_12
        + b"\x00" * 32
        + bytes([len(session_id)])
        + session_id
        + struct.pack(">H", _select_cipher_suite(info))
        + b"\x00"
        + struct.pack(">H", len(extensions))
        + extensions
    )
    message = b"\x02" + struct.pack(">I", len(body))[1:] + body
    server_hello = (
        bytes([TLS_RECORD_HANDSHAKE])
        + TLS_VERSION_12
        + struct.pack(">H", len(message))
        + message
    )

    appdata_len = os.urandom(1)[0] % (FALLBACK_APPDATA_RANGE[1] - APPDATA_MIN + 1) + APPDATA_MIN
    app_record = (
        bytes([TLS_RECORD_APPDATA])
        + TLS_VERSION_12
        + struct.pack(">H", appdata_len)
        + os.urandom(appdata_len)
    )
    response = server_hello + CCS_FRAME + app_record

    server_random = hmac.new(secret, client_random + response, hashlib.sha256).digest()
    final = bytearray(response)
    final[SERVER_RANDOM_OFFSET:SERVER_RANDOM_OFFSET + 32] = server_random
    return bytes(final)


def wrap_tls_record(data: bytes) -> bytes:
    """Оборачивает произвольные данные в ApplicationData-записи TLS."""
    parts = []
    offset = 0
    while offset < len(data):
        chunk = data[offset:offset + TLS_APPDATA_MAX]
        parts.append(b"\x17\x03\x03" + struct.pack(">H", len(chunk)) + chunk)
        offset += len(chunk)
    return b"".join(parts)


class FakeTlsStream:
    """Обёртка над TCP-потоком: чтение/запись внутри TLS-записей."""

    __slots__ = ("_reader", "_writer", "_buf", "_left")

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._buf = bytearray()
        self._left = 0

    async def readexactly(self, n: int) -> bytes:
        while len(self._buf) < n:
            payload = await self._read_payload()
            if not payload:
                raise asyncio.IncompleteReadError(bytes(self._buf), n)
            self._buf.extend(payload)
        result = bytes(self._buf[:n])
        del self._buf[:n]
        return result

    async def read(self, n: int) -> bytes:
        if self._buf:
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
            return chunk
        payload = await self._read_payload()
        if not payload:
            return b""
        if len(payload) > n:
            self._buf.extend(payload[n:])
            return payload[:n]
        return payload

    async def _read_payload(self) -> bytes:
        if self._left > 0:
            data = await self._reader.read(self._left)
            if not data:
                return b""
            self._left -= len(data)
            return data
        while True:
            header = await self._reader.readexactly(5)
            record_type = header[0]
            record_len = struct.unpack(">H", header[3:5])[0]
            if record_type == TLS_RECORD_CCS:
                if record_len:
                    await self._reader.readexactly(record_len)
                continue
            if record_type != TLS_RECORD_APPDATA:
                return b""
            data = await self._reader.read(min(record_len, 65536))
            if not data:
                return b""
            remaining = record_len - len(data)
            if remaining > 0:
                self._left = remaining
            return data

    def write(self, data: bytes) -> None:
        self._writer.write(wrap_tls_record(data))

    async def drain(self) -> None:
        await self._writer.drain()

    def close(self) -> None:
        self._writer.close()

    async def wait_closed(self) -> None:
        await self._writer.wait_closed()

    def get_extra_info(self, name, default=None):
        return self._writer.get_extra_info(name, default)


async def proxy_to_masking_domain(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    initial_data: bytes,
    domain: str,
    label: str = "",
) -> None:
    """Проксирует «не наш» ClientHello на настоящий сайт маскировки."""
    try:
        up_reader, up_writer = await asyncio.wait_for(
            asyncio.open_connection(domain, 443), timeout=10
        )
    except (OSError, asyncio.TimeoutError) as exc:
        log.warning("[%s] маскировка: не удалось подключиться к %s: %r", label, domain, exc)
        return

    try:
        if initial_data:
            up_writer.write(initial_data)
            await up_writer.drain()

        async def relay(src, dst) -> None:
            try:
                while True:
                    chunk = await src.read(16384)
                    if not chunk:
                        break
                    dst.write(chunk)
                    await dst.drain()
            except (ConnectionError, OSError, asyncio.CancelledError):
                pass
            finally:
                try:
                    dst.close()
                    await dst.wait_closed()
                except Exception:  # noqa: BLE001
                    pass

        await asyncio.gather(relay(reader, up_writer), relay(up_reader, writer))
    except Exception:  # noqa: BLE001 - маскировка не должна влиять на работу
        pass
    finally:
        try:
            up_writer.close()
        except Exception:  # noqa: BLE001
            pass
