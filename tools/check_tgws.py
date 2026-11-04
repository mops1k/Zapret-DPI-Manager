"""Сквозная проверка движка TG WS Proxy без сети и без Telegram.

Поднимает локальный WebSocket-сервер, который ведёт себя как сторона
Telegram (разбирает relay-init и перешифровывает поток), запускает настоящий
``TgWsProxy`` и эмулирует Telegram Desktop (obfuscated2-хендшейк с dd-секретом,
intermediate-протокол). Проверяется полный путь:

    эмулятор клиента → прокси (расшифровка dd) → WS → «Telegram» (relay-ключи)

Запуск: ``python3 tools/check_tgws.py`` (в CI — отдельным шагом).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tgws.crypto import new_ctr  # noqa: E402
from tgws.obfuscated2 import (  # noqa: E402
    PROTO_TAG_INTERMEDIATE,
    ZERO_64,
)
from tgws.proxy import TgWsProxy  # noqa: E402
from tgws.splitter import MtProtoSplitter  # noqa: E402
from tgws.ws import WebSocket  # noqa: E402

SECRET = os.urandom(16)


def _make_client_handshake(dc: int, media: bool = False) -> tuple[bytes, bytes]:
    """Собирает клиентский obfuscated2-init и возвращает (init, prekey_iv)."""
    while True:
        rnd = bytearray(os.urandom(64))
        if rnd[0] == 0xEF:
            continue
        if bytes(rnd[:4]) in (b"HEAD", b"POST", b"GET ", b"OPTI"):
            continue
        break
    prekey_iv = bytes(rnd[8:56])
    encryptor = new_ctr(hashlib.sha256(prekey_iv[:32] + SECRET).digest(), prekey_iv[32:])
    full = encryptor.update(bytes(rnd))
    keystream = bytes(a ^ b for a, b in zip(full[56:64], rnd[56:64]))
    tail = PROTO_TAG_INTERMEDIATE + struct.pack("<h", -dc if media else dc) + os.urandom(2)
    rnd[56:64] = bytes(a ^ b for a, b in zip(tail, keystream))
    return bytes(rnd), prekey_iv


def _client_cryptos(prekey_iv: bytes):
    """Потоки клиента: к прокси (K после init) и от прокси (reverse-ключ)."""
    up = new_ctr(hashlib.sha256(prekey_iv[:32] + SECRET).digest(), prekey_iv[32:])
    up.update(ZERO_64)
    rev = prekey_iv[::-1]
    down = new_ctr(hashlib.sha256(rev[:32] + SECRET).digest(), rev[32:])
    return down, up


class FakeTelegram:
    """WS-сервер, ведущий себя как Telegram: relay-init + перешифровка."""

    def __init__(self) -> None:
        self.received_plain = bytearray()
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self.relay_init: bytes | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader, writer) -> None:
        ws = await WebSocket.accept(reader, writer, label="fake-telegram")
        if ws is None:
            return
        try:
            relay = await ws.recv()
            if relay is None or len(relay) != 64:
                return
            self.relay_init = relay
            dec = new_ctr(relay[8:40], relay[40:56])
            dec.update(ZERO_64)
            rev = relay[8:56][::-1]
            enc = new_ctr(rev[:32], rev[32:])
            while True:
                data = await ws.recv()
                if data is None:
                    break
                plain = dec.update(data)
                self.received_plain.extend(plain)
                if len(self.received_plain) >= 8:
                    answer = b"\x04\x00\x00\x00PONG"
                    await ws.send(enc.update(answer))
                    break
        finally:
            await ws.close()


async def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(("OK   " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    telegram = FakeTelegram()
    await telegram.start()

    from tgws.config import Config

    cfg = Config(secret=SECRET.hex(), port=0, host="127.0.0.1", h2_media=False)
    proxy = TgWsProxy(cfg)

    async def connector(dc: int, is_media: bool) -> WebSocket:
        return await WebSocket.connect(
            "127.0.0.1",
            port=telegram.port,
            host_header="kws2.web.telegram.org",
            path="/apiws",
            secure=False,
            label="test",
        )

    proxy.pool.direct_connector = connector
    await proxy.start()
    listen_port = proxy._server.sockets[0].getsockname()[1]

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", listen_port)
        init, prekey_iv = _make_client_handshake(2)
        writer.write(init)
        await writer.drain()

        down, up = _client_cryptos(prekey_iv)
        payload = b"\x04\x00\x00\x00PING"
        writer.write(up.update(payload))
        await writer.drain()

        answer = await asyncio.wait_for(reader.readexactly(8), timeout=5)
        plain_answer = down.update(answer)
        check("ответ Telegram дошёл до клиента", plain_answer == b"\x04\x00\x00\x00PONG",
              repr(plain_answer))
        check(
            "прокси отправил клиентские данные в relay-потоке",
            bytes(telegram.received_plain) == payload,
            repr(bytes(telegram.received_plain)),
        )
        check("relay-init 64 байта", (telegram.relay_init or b"")[:0:1] == b""
              and len(telegram.relay_init or b"") == 64)
        writer.close()
        await writer.wait_closed()
    except Exception as exc:  # noqa: BLE001 - отчёт стенда
        check("сквозной туннель", False, f"{type(exc).__name__}: {exc}")
    finally:
        await proxy.close()
        await telegram.stop()

    # --- раздельное тестирование хендшейка и сплиттера ----------------------
    from tgws.obfuscated2 import (
        PROTO_TAG_SECURE,
        build_reencrypt_context,
        generate_relay_init,
        parse_client_handshake,
    )

    for media in (False, True):
        init, _prekey = _make_client_handshake(4, media=media)
        hs = parse_client_handshake(init, SECRET)
        check(
            f"хендшейк DC4{' медиа' if media else ''} разобран",
            hs is not None
            and hs.dc == 4
            and hs.is_media == media
            and hs.proto_tag == PROTO_TAG_INTERMEDIATE,
        )

    wrong = parse_client_handshake(_make_client_handshake(2)[0], os.urandom(16))
    check("чужой секрет отвергнут", wrong is None)

    relay = generate_relay_init(PROTO_TAG_SECURE, 2)
    check("relay-init имеет размер 64", len(relay) == 64)

    init, prekey = _make_client_handshake(2)
    hs = parse_client_handshake(init, SECRET)
    assert hs is not None
    relay = generate_relay_init(hs.proto_tag, hs.dc)
    ctx = build_reencrypt_context(hs, SECRET, relay)
    plain = b"\x04\x00\x00\x00TEST"
    client_enc = new_ctr(
        hashlib.sha256(prekey[:32] + SECRET).digest(), prekey[32:]
    )
    client_enc.update(ZERO_64)
    moved = ctx.clt_dec.update(client_enc.update(plain))
    check("перешифровка клиент→Telegram", moved == plain, repr(moved))

    splitter = MtProtoSplitter(hs.proto_int)
    packet = b"\x04\x00\x00\x00TEST"
    parts = splitter.split(packet, packet)
    check("сплиттер отдаёт целый intermediate-пакет", parts == [packet], repr(parts))
    splitter2 = MtProtoSplitter(hs.proto_int)
    two = packet + packet
    parts2 = splitter2.split(two, two)
    check("сплиттер режет склеенные пакеты", parts2 == [packet, packet], repr(parts2))
    splitter3 = MtProtoSplitter(hs.proto_int)
    half = splitter3.split(packet[:3], packet[:3])
    check("сплиттер ждёт заголовок пакета", half == [], repr(half))

    if failures:
        print(f"\nПРОВАЛОВ: {len(failures)}")
        return 1
    print("\nВсе проверки пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
