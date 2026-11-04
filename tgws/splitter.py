"""Разбиение потока MTProto на отдельные пакеты (для WS-кадров).

Telegram ожидает от WebSocket-транспорта те же сообщения обфусцированного
протокола, что и по TCP, но по одному в кадре. Поток клиента приходит как
непрерывный TCP-поток, поэтому длины пакетов считаются по заголовку:

* abridged: 1 байт — ``len/4`` (или ``0x7F`` и 3 байта), затем payload;
* intermediate: 4 байта little-endian ``len`` (старший бит — флаг быстрого
  подтверждения), затем payload.

Пустой/битый заголовок отключает разбиение — данные дальше идут «как есть»,
чтобы не порвать соединение.
"""

from __future__ import annotations

import struct
from typing import List, Optional

from .obfuscated2 import (
    PROTO_ABRIDGED,
    PROTO_INTERMEDIATE,
    PROTO_PADDED_INTERMEDIATE,
)

_st_I = struct.Struct("<I")


class MtProtoSplitter:
    __slots__ = ("_proto", "_cipher", "_plain", "_disabled")

    def __init__(self, proto_int: int) -> None:
        if proto_int not in (
            PROTO_ABRIDGED,
            PROTO_INTERMEDIATE,
            PROTO_PADDED_INTERMEDIATE,
        ):
            raise ValueError(f"неизвестный proto tag: {proto_int:#x}")
        self._proto = proto_int
        self._cipher = bytearray()
        self._plain = bytearray()
        self._disabled = False

    @property
    def disabled(self) -> bool:
        return self._disabled

    def split(self, chunk: bytes, plain: bytes) -> List[bytes]:
        """Возвращает готовые к отправке куски (шифрованные)."""
        if not chunk:
            return []
        if self._disabled:
            return [chunk]

        self._cipher.extend(chunk)
        self._plain.extend(plain)

        parts: List[bytes] = []
        offset = 0
        cipher_len = len(self._cipher)
        while offset < cipher_len:
            packet_len = self._next_packet_len(offset, cipher_len - offset)
            if packet_len is None:
                break
            if packet_len <= 0:
                # Заголовок не распознан: отдаём остаток целиком и выключаемся.
                parts.append(bytes(self._cipher[offset:]))
                offset = cipher_len
                self._disabled = True
                break
            parts.append(bytes(self._cipher[offset:offset + packet_len]))
            offset += packet_len

        if offset:
            del self._cipher[:offset]
            del self._plain[:offset]
        return parts

    def flush(self) -> List[bytes]:
        if not self._cipher:
            return []
        tail = bytes(self._cipher)
        self._cipher.clear()
        self._plain.clear()
        return [tail]

    # --- разбор длин --------------------------------------------------------

    def _next_packet_len(self, offset: int, available: int) -> Optional[int]:
        if available <= 0:
            return None
        if self._proto == PROTO_ABRIDGED:
            return self._abridged_len(offset, available)
        return self._intermediate_len(offset, available)

    def _abridged_len(self, offset: int, available: int) -> Optional[int]:
        first = self._plain[offset]
        if first in (0x7F, 0xFF):
            if available < 4:
                return None
            payload_len = int.from_bytes(
                self._plain[offset + 1:offset + 4], "little"
            ) * 4
            header_len = 4
        else:
            payload_len = (first & 0x7F) * 4
            header_len = 1
        if payload_len <= 0:
            return 0
        packet_len = header_len + payload_len
        if available < packet_len:
            return None
        return packet_len

    def _intermediate_len(self, offset: int, available: int) -> Optional[int]:
        if available < 4:
            return None
        payload_len = _st_I.unpack_from(self._plain, offset)[0] & 0x7FFFFFFF
        if payload_len <= 0:
            return 0
        packet_len = 4 + payload_len
        if available < packet_len:
            return None
        return packet_len
