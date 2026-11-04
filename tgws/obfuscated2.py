"""MTProto obfuscated2: разбор клиентского хендшейка и генерация relay-init.

Формат (как в клиентах Telegram и nfqws): 64 байта, из которых:

* ``[0:8]``   — префикс/служебные байты (не шифруются, отбрасываются);
* ``[8:40]``  — prekey (32 байта);
* ``[40:56]`` — IV (16 байт);
* ``[56:60]`` — proto tag (зашифрован): ``ef ef ef ef`` (abridged),
  ``ee ee ee ee`` (intermediate), ``dd dd dd dd`` (padded intermediate);
* ``[60:62]`` — DC id (int16 little-endian, отрицательный — медиа-DC);
* ``[62:64]`` — случайные байты.

Ключ клиентского потока — ``SHA256(prekey + secret)``, где ``secret`` — 16
байт dd-секрета прокси. Обратное направление (к клиенту) использует ключ от
перевёрнутого блока ``[8:56]``.

Relay-init — «свой» obfuscated2-хендшейк, который прокси отправляет серверу
Telegram первым WebSocket-сообщением: он задаёт ключи потока в обе стороны.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from typing import Optional

from .crypto import AesCtr, new_ctr

HANDSHAKE_LEN = 64
SKIP_LEN = 8
PREKEY_LEN = 32
KEY_LEN = 32
IV_LEN = 16
PROTO_TAG_POS = 56
DC_IDX_POS = 60

PROTO_TAG_ABRIDGED = b"\xef\xef\xef\xef"
PROTO_TAG_INTERMEDIATE = b"\xee\xee\xee\xee"
PROTO_TAG_SECURE = b"\xdd\xdd\xdd\xdd"

PROTO_ABRIDGED = 0xEFEFEFEF
PROTO_INTERMEDIATE = 0xEEEEEEEE
PROTO_PADDED_INTERMEDIATE = 0xDDDDDDDD

_PROTO_TAGS = {
    PROTO_TAG_ABRIDGED: PROTO_ABRIDGED,
    PROTO_TAG_INTERMEDIATE: PROTO_INTERMEDIATE,
    PROTO_TAG_SECURE: PROTO_PADDED_INTERMEDIATE,
}

#: Первый байт не должен быть 0xEF, а первые 4 — совпадать с известными
#: префиксами протоколов (иначе сервер примет init за чужой протокол).
_RESERVED_FIRST_BYTES = frozenset({0xEF})
_RESERVED_STARTS = (
    b"HEAD",
    b"POST",
    b"GET ",
    b"OPTI",
    b"\xee\xee\xee\xee",
    b"\xdd\xdd\xdd\xdd",
    b"\x16\x03\x01\x02",
)

ZERO_64 = b"\x00" * 64


@dataclass(frozen=True)
class ClientHandshake:
    """Разобранный клиентский obfuscated2-хендшейк."""

    dc: int
    is_media: bool
    proto_tag: bytes
    proto_int: int
    prekey_iv: bytes  # client[8:56] — из него строятся ключи


def parse_client_handshake(
    handshake: bytes, secret: bytes
) -> Optional[ClientHandshake]:
    """Проверяет и разбирает 64-байтный клиентский init.

    ``None`` — init не наш: неверный секрет, битые данные или неизвестный
    proto tag.
    """
    if len(handshake) != HANDSHAKE_LEN or len(secret) != 16:
        return None

    prekey_iv = handshake[SKIP_LEN:SKIP_LEN + PREKEY_LEN + IV_LEN]
    key = hashlib.sha256(prekey_iv[:PREKEY_LEN] + secret).digest()
    decryptor = new_ctr(key, prekey_iv[PREKEY_LEN:])
    decrypted = decryptor.update(handshake)

    proto_tag = decrypted[PROTO_TAG_POS:PROTO_TAG_POS + 4]
    proto_int = _PROTO_TAGS.get(proto_tag)
    if proto_int is None:
        return None

    dc_raw = struct.unpack_from("<h", decrypted, DC_IDX_POS)[0]
    if dc_raw == 0 or abs(dc_raw) > 1000:
        # DC 0 и «мусорные» значения — не наш хендшейк
        return None

    return ClientHandshake(
        dc=abs(dc_raw),
        is_media=dc_raw < 0,
        proto_tag=proto_tag,
        proto_int=proto_int,
        prekey_iv=prekey_iv,
    )


def generate_relay_init(proto_tag: bytes, dc_idx: int) -> bytes:
    """Собирает 64-байтный obfuscated2-init для сервера Telegram.

    ``dc_idx`` — DC id со знаком (отрицательный для медиа-DC).
    """
    while True:
        rnd = bytearray(os.urandom(HANDSHAKE_LEN))
        if rnd[0] in _RESERVED_FIRST_BYTES:
            continue
        if bytes(rnd[:4]) in _RESERVED_STARTS:
            continue
        if bytes(rnd[4:8]) == b"\x00\x00\x00\x00":
            continue
        break

    rnd_bytes = bytes(rnd)
    enc_key = rnd_bytes[SKIP_LEN:SKIP_LEN + PREKEY_LEN]
    enc_iv = rnd_bytes[SKIP_LEN + PREKEY_LEN:SKIP_LEN + PREKEY_LEN + IV_LEN]
    encryptor = new_ctr(enc_key, enc_iv)

    tail_plain = proto_tag + struct.pack("<h", dc_idx) + os.urandom(2)
    keystream = encryptor.update(rnd_bytes)[PROTO_TAG_POS:HANDSHAKE_LEN]
    tail_encrypted = bytes(a ^ b for a, b in zip(tail_plain, keystream))

    result = bytearray(rnd_bytes)
    result[PROTO_TAG_POS:HANDSHAKE_LEN] = tail_encrypted
    return bytes(result)


class ReencryptContext:
    """Четыре AES-CTR потока для перешифровки между клиентом и Telegram."""

    __slots__ = ("clt_dec", "clt_enc", "tg_enc", "tg_dec")

    def __init__(
        self,
        clt_dec: AesCtr,
        clt_enc: AesCtr,
        tg_enc: AesCtr,
        tg_dec: AesCtr,
    ) -> None:
        self.clt_dec = clt_dec  # от клиента: расшифровка
        self.clt_enc = clt_enc  # к клиенту: шифрование
        self.tg_enc = tg_enc    # к Telegram: шифрование
        self.tg_dec = tg_dec    # от Telegram: расшифровка

    def close(self) -> None:
        """Освобождает OpenSSL-контексты (см. tgws.crypto)."""
        for ctr in (self.clt_dec, self.clt_enc, self.tg_enc, self.tg_dec):
            try:
                ctr.close()
            except Exception:  # noqa: BLE001
                pass


def build_reencrypt_context(
    handshake: ClientHandshake, secret: bytes, relay_init: bytes
) -> ReencryptContext:
    """Готовит контекст: клиентские ключи (dd-секрет) и relay-ключи."""
    prekey_iv = handshake.prekey_iv

    clt_dec_key = hashlib.sha256(prekey_iv[:PREKEY_LEN] + secret).digest()
    clt_dec = new_ctr(clt_dec_key, prekey_iv[PREKEY_LEN:])
    clt_dec.update(ZERO_64)  # пропускаем уже прочитанный handshake

    reversed_prekey_iv = prekey_iv[::-1]
    clt_enc_key = hashlib.sha256(reversed_prekey_iv[:PREKEY_LEN] + secret).digest()
    clt_enc = new_ctr(clt_enc_key, reversed_prekey_iv[PREKEY_LEN:])

    relay_key = relay_init[SKIP_LEN:SKIP_LEN + PREKEY_LEN]
    relay_iv = relay_init[SKIP_LEN + PREKEY_LEN:SKIP_LEN + PREKEY_LEN + IV_LEN]
    tg_enc = new_ctr(relay_key, relay_iv)
    tg_enc.update(ZERO_64)  # relay_init уходит отдельным WS-сообщением

    relay_reversed = relay_init[SKIP_LEN:SKIP_LEN + PREKEY_LEN + IV_LEN][::-1]
    tg_dec = new_ctr(relay_reversed[:KEY_LEN], relay_reversed[KEY_LEN:])

    return ReencryptContext(clt_dec, clt_enc, tg_enc, tg_dec)
