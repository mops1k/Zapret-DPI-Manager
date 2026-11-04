"""AES-CTR для MTProto obfuscated2 без обязательных внешних зависимостей.

Если установлен пакет ``cryptography`` — используется он (быстрее). Иначе
работаем через OpenSSL ``libcrypto`` (ctypes): на любой Linux-системе с
OpenSSL она есть, отдельная установка не нужна. Потоковый режим такой же,
как у ``cryptography.hazmat.primitives.ciphers.Cipher(AES-CTR)``: объект
хранит состояние счётчика между вызовами ``update``.

Для CTR шифрование и дешифрование — одна операция, поэтому класс один.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from typing import Optional


class CryptoUnavailable(RuntimeError):
    """Нет ни cryptography, ни libcrypto — работать нечем."""


class AesCtr:
    """Потоковый AES-CTR: ``update(data)`` шифрует или расшифровывает."""

    __slots__ = ("_impl",)

    def __init__(self, key: bytes, iv: bytes) -> None:
        if len(key) not in (16, 24, 32):
            raise ValueError("AES-ключ должен быть 16, 24 или 32 байта")
        if len(iv) != 16:
            raise ValueError("IV должен быть 16 байт")
        self._impl = _make_impl(key, iv)

    def update(self, data: bytes) -> bytes:
        if not data:
            return b""
        return self._impl.update(data)

    def close(self) -> None:
        """Освобождает ресурсы (для libcrypto — обязательно явно)."""
        close = getattr(self._impl, "close", None)
        if close is not None:
            close()


# --- реализация на cryptography ---------------------------------------------


class _CryptographyCtr:
    __slots__ = ("_ctx",)

    def __init__(self, key: bytes, iv: bytes) -> None:
        from cryptography.hazmat.primitives.ciphers import (
            Cipher,
            algorithms,
            modes,
        )

        self._ctx = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()

    def update(self, data: bytes) -> bytes:
        return self._ctx.update(data)

    def close(self) -> None:
        self._ctx = None


# --- реализация на libcrypto (ctypes) ---------------------------------------

_EVP_CTX_NEW = ctypes.CFUNCTYPE(ctypes.c_void_p)
_EVP_CTX_FREE = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
_EVP_ENCRYPT_INIT = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_char_p,
)
_EVP_ENCRYPT_UPDATE = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_char_p,
    ctypes.c_int,
)

_libcrypto: Optional[ctypes.CDLL] = None
_libcrypto_error: Optional[str] = None


def _load_libcrypto() -> ctypes.CDLL:
    global _libcrypto, _libcrypto_error
    if _libcrypto is not None:
        return _libcrypto
    if _libcrypto_error is not None:
        raise CryptoUnavailable(_libcrypto_error)

    candidates = []
    found = ctypes.util.find_library("crypto")
    if found:
        candidates.append(found)
    candidates += ["libcrypto.so.3", "libcrypto.so.1.1", "libcrypto.so"]
    last_error = None
    for name in candidates:
        try:
            lib = ctypes.CDLL(name)
        except OSError as exc:  # noqa: PERF203 - пробуем все варианты
            last_error = exc
            continue
        try:
            lib.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
            lib.EVP_CIPHER_CTX_new.argtypes = []
            lib.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
            lib.EVP_CIPHER_CTX_free.restype = None
            for fn, cipher in (
                ("EVP_aes_128_ctr", "EVP_aes_128_ctr"),
                ("EVP_aes_192_ctr", "EVP_aes_192_ctr"),
                ("EVP_aes_256_ctr", "EVP_aes_256_ctr"),
            ):
                getattr(lib, fn).restype = ctypes.c_void_p
                getattr(lib, fn).argtypes = []
            lib.EVP_EncryptInit_ex.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
            ]
            lib.EVP_EncryptInit_ex.restype = ctypes.c_int
            lib.EVP_EncryptUpdate.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_char_p,
                ctypes.c_int,
            ]
            lib.EVP_EncryptUpdate.restype = ctypes.c_int
        except AttributeError as exc:
            last_error = exc
            continue
        _libcrypto = lib
        return lib

    _libcrypto_error = (
        "OpenSSL libcrypto не найдена (и модуль cryptography не установлен): "
        f"{last_error!r}"
    )
    raise CryptoUnavailable(_libcrypto_error)


class _OpenSslCtr:
    __slots__ = ("_lib", "_ctx")

    _CIPHERS = {16: b"EVP_aes_128_ctr", 24: b"EVP_aes_192_ctr", 32: b"EVP_aes_256_ctr"}

    def __init__(self, key: bytes, iv: bytes) -> None:
        self._lib = _load_libcrypto()
        cipher_name = self._CIPHERS[len(key)].decode()
        cipher = getattr(self._lib, cipher_name)()
        if not cipher:
            raise CryptoUnavailable("EVP_aes_*_ctr вернула NULL")
        ctx = self._lib.EVP_CIPHER_CTX_new()
        if not ctx:
            raise CryptoUnavailable("EVP_CIPHER_CTX_new вернула NULL")
        rc = self._lib.EVP_EncryptInit_ex(ctx, cipher, None, key, iv)
        if rc != 1:
            self._lib.EVP_CIPHER_CTX_free(ctx)
            raise CryptoUnavailable("EVP_EncryptInit_ex завершилась ошибкой")
        self._ctx = ctx

    def close(self) -> None:
        # Освобождаем явно: __del__ в ctypes при завершении интерпретатора
        # приводит к segfault (порядок финализации модулей не определён).
        ctx = getattr(self, "_ctx", None)
        self._ctx = None
        if ctx:
            try:
                self._lib.EVP_CIPHER_CTX_free(ctx)
            except Exception:  # noqa: BLE001 - при выходе из процесса не важно
                pass

    def update(self, data: bytes) -> bytes:
        in_len = len(data)
        out = ctypes.create_string_buffer(in_len + 16)
        out_len = ctypes.c_int(0)
        rc = self._lib.EVP_EncryptUpdate(
            self._ctx, out, ctypes.byref(out_len), data, in_len
        )
        if rc != 1:
            raise CryptoUnavailable("EVP_EncryptUpdate завершилась ошибкой")
        return out.raw[: out_len.value]


def _make_impl(key: bytes, iv: bytes):
    """Выбирает реализацию: cryptography → libcrypto."""
    try:
        import cryptography  # noqa: F401

        return _CryptographyCtr(key, iv)
    except Exception:  # noqa: BLE001 - нет модуля или он сломан
        return _OpenSslCtr(key, iv)


def new_ctr(key: bytes, iv: bytes) -> AesCtr:
    return AesCtr(key, iv)


def available() -> bool:
    """Есть ли чем шифровать (cryptography или libcrypto)."""
    try:
        new_ctr(b"\x00" * 32, b"\x00" * 16).update(b"\x00" * 16)
        return True
    except CryptoUnavailable:
        return False
