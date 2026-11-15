"""Self-built symmetric encryption for MCP-over-HTTP bodies.

Design mirrors the reference project ``frit`` (frit/crypto.py):

- The pre-shared key (PSK) is turned into a fixed AES-128 key with SHA-256:
      aes_key = SHA256(psk)[:16]
- Every encrypted HTTP body is AES-128-ECB + PKCS7.
- Encrypted wire format:  [2 byte big-endian ciphertext length][ciphertext]
    * the 2-byte length is the *ciphertext* length; PKCS7 padding is removed
      on decrypt.
- Authentication middleware: after decryption the first 2 plaintext bytes must
  be one of the hard-coded random frame-type codes (0x66F0). Anything else is
  rejected with 401. This is the same "frame type as auth token" trick frit
  uses for its TCP frames.

NOTE: AES-ECB with a fixed key is weak against traffic analysis (identical
plaintext blocks -> identical ciphertext blocks). It is used here only because
the reference project (frit) uses it and the user asked to follow that.
"""

from __future__ import annotations

import hashlib
import struct

from Crypto.Cipher import AES

# Frame types copied from frit's protocol (random 16-bit constants).
FRAME_TYPE_CTRL = 0x66F0  # control / JSON-RPC message over HTTP

_LEN_SIZE = 2
_HEADER = struct.Struct(">H")


class CryptoError(Exception):
    """Raised when a body cannot be decrypted / authenticated."""


def derive_key(psk: str) -> bytes:
    """Derive the fixed 16-byte AES-128 key from the pre-shared key string."""
    return hashlib.sha256(psk.encode("utf-8")).digest()[:16]


def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes, block: int = 16) -> bytes:
    if not data or len(data) % block != 0:
        raise CryptoError("invalid ciphertext length")
    pad = data[-1]
    if pad < 1 or pad > block:
        raise CryptoError("invalid padding")
    if data[-pad:] != bytes([pad]) * pad:
        raise CryptoError("invalid padding")
    return data[:-pad]


def encrypt_body(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt a plaintext body -> [2B ciphertext len][AES-128-ECB ciphertext]."""
    cipher = AES.new(key, AES.MODE_ECB)
    ciphertext = cipher.encrypt(_pkcs7_pad(plaintext))
    if len(ciphertext) > 0xFFFF:
        raise CryptoError("body too large for a single encrypted frame")
    return _HEADER.pack(len(ciphertext)) + ciphertext


def decrypt_body(key: bytes, blob: bytes) -> bytes:
    """Decrypt [2B ciphertext len][ciphertext] -> plaintext, verifying PKCS7."""
    if len(blob) < _LEN_SIZE + 16:
        raise CryptoError("frame too short")
    (length,) = _HEADER.unpack(blob[:_LEN_SIZE])
    ciphertext = blob[_LEN_SIZE:]
    if length != len(ciphertext):
        raise CryptoError("length mismatch")
    if len(ciphertext) % 16 != 0:
        raise CryptoError("invalid ciphertext length")
    cipher = AES.new(key, AES.MODE_ECB)
    return _pkcs7_unpad(cipher.decrypt(ciphertext))


def seal(key: bytes, payload: bytes, frame_type: int = FRAME_TYPE_CTRL) -> bytes:
    """Prepend the frame-type auth code and encrypt (plaintext -> wire bytes)."""
    return encrypt_body(key, _HEADER.pack(frame_type) + payload)


def open_(key: bytes, blob: bytes) -> bytes:
    """Decrypt and verify the frame-type auth code, returning the payload."""
    plaintext = decrypt_body(key, blob)
    if len(plaintext) < _LEN_SIZE:
        raise CryptoError("frame too short")
    (frame_type,) = _HEADER.unpack(plaintext[:_LEN_SIZE])
    if frame_type != FRAME_TYPE_CTRL:
        raise CryptoError("unknown frame type (auth failed)")
    return plaintext[_LEN_SIZE:]
