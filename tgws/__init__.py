"""Собственный MTProto-через-WebSocket прокси для Zapret DPI Manager.

Модуль перегоняет трафик Telegram (MTProto obfuscated2) через WebSocket
(`/apiws` на серверах `kws*.web.telegram.org`) — тем же способом, что делает
Flowseal/tg-ws-proxy, но без его кода и без внешних Python-зависимостей:
AES-CTR берётся из `cryptography`, если она есть, иначе из OpenSSL `libcrypto`
через ctypes; TLS и сокеты — из стандартной библиотеки.

Состав:

* :mod:`tgws.crypto` — AES-CTR и контексты перешифровки;
* :mod:`tgws.obfuscated2` — MTProto-хендшейк (dd-секрет), генерация relay-init;
* :mod:`tgws.ws` — минимальный WebSocket-клиент (TLS, upgrade, кадры);
* :mod:`tgws.splitter` — разбиение потока на MTProto-пакеты;
* :mod:`tgws.bridge` — мост «клиент ↔ WS/H2/TCP» с перешифровкой;
* :mod:`tgws.pool` — пул WS-соединений и Cloudflare-фолбэки;
* :mod:`tgws.h2` — минимальный HTTP/2-клиент для медиа-фолбэка;
* :mod:`tgws.faketls` — маскировка под TLS (ee-секрет);
* :mod:`tgws.proxy` — asyncio-сервер и CLI.
"""

__version__ = "1.0.0"
