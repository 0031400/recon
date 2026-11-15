"""Asyncio frame IO on top of crypto.py (mirrors frit/aio.py).

Encrypted wire format:  [2B ciphertext len][ciphertext]
Plaintext inside:        [2B frame type][payload]
"""

from __future__ import annotations

import asyncio
import struct

from . import crypto, protocol

_LEN_SIZE = 2
_LEN = struct.Struct(">H")


async def read_frame(reader: asyncio.StreamReader, key: bytes) -> bytes:
    """Read one encrypted frame and return its decrypted plaintext."""
    length_bytes = await reader.readexactly(_LEN_SIZE)
    (length,) = _LEN.unpack(length_bytes)
    if length <= 0 or length % 16 != 0:
        raise crypto.CryptoError(f"invalid frame length: {length}")
    ciphertext = await reader.readexactly(length)
    return crypto.decrypt_body(key, _LEN.pack(length) + ciphertext)


async def write_frame(writer: asyncio.StreamWriter, key: bytes, plaintext: bytes) -> None:
    """Encrypt plaintext and write one frame, then drain."""
    blob = crypto.encrypt_body(key, plaintext)
    writer.write(blob)
    await writer.drain()


async def read_typed(reader: asyncio.StreamReader, key: bytes) -> tuple[int, bytes]:
    return protocol.decode_frame(await read_frame(reader, key))


async def send_ctrl(writer: asyncio.StreamWriter, key: bytes, obj: dict) -> None:
    await write_frame(writer, key, protocol.encode_ctrl(obj))


async def send_data(writer: asyncio.StreamWriter, key: bytes, payload: bytes) -> None:
    await write_frame(writer, key, protocol.encode_data(payload))
