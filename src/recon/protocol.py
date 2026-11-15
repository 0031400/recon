"""Plaintext frame codec (mirrors frit/protocol.py).

A plaintext frame is:  [2 byte big-endian frame type][payload]

Frame types (random 16-bit constants, same as frit):
    FRAME_TYPE_CTRL -> payload is a JSON control / RPC message
    FRAME_TYPE_DATA -> payload is raw file-transfer bytes

The first decrypted frame's type is used as the authentication check on the
server side; anything else is dropped and the connection closed.
"""

from __future__ import annotations

import json
import struct

# Maximum plaintext payload per frame (matches frit).
MAX_PACKET_SIZE = 64 * 1024

FRAME_TYPE_CTRL = 0x66F0  # 26352
FRAME_TYPE_DATA = 0x04EB  # 1259

_VALID_TYPES = (FRAME_TYPE_CTRL, FRAME_TYPE_DATA)
_TYPE = struct.Struct(">H")


class ProtocolError(Exception):
    pass


def encode_frame(frame_type: int, payload: bytes) -> bytes:
    if frame_type not in _VALID_TYPES:
        raise ProtocolError(f"invalid frame type: {frame_type}")
    if len(payload) > MAX_PACKET_SIZE:
        raise ProtocolError("packet too large")
    return _TYPE.pack(frame_type) + payload


def encode_ctrl(obj: dict) -> bytes:
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return encode_frame(FRAME_TYPE_CTRL, payload)


def encode_data(payload: bytes) -> bytes:
    return encode_frame(FRAME_TYPE_DATA, payload)


def decode_frame(plaintext: bytes) -> tuple[int, bytes]:
    if len(plaintext) < 2:
        raise ProtocolError("frame too short")
    (frame_type,) = _TYPE.unpack(plaintext[:2])
    if frame_type not in _VALID_TYPES:
        raise ProtocolError(f"unknown frame type: {frame_type}")
    return frame_type, plaintext[2:]


def decode_ctrl(payload: bytes) -> dict:
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"invalid json: {exc}") from exc
    if not isinstance(obj, dict) or "type" not in obj:
        raise ProtocolError("message missing 'type'")
    return obj
