"""Tests for crypto framing and the transfer manager (no network)."""

import base64
import hashlib

import pytest

from recon import crypto, protocol
from recon.config_tuning import Settings
from recon.transfer import TransferError, TransferManager

PSK = "unit-test-psk"


def test_crypto_roundtrip():
    key = crypto.derive_key(PSK)
    payload = b'{"hello":"world"}'
    assert crypto.open_(key, crypto.seal(key, payload)) == payload


def test_crypto_wrong_key():
    key = crypto.derive_key(PSK)
    blob = crypto.seal(key, b"data")
    with pytest.raises(crypto.CryptoError):
        crypto.open_(crypto.derive_key("other"), blob)


def test_crypto_tamper_rejected():
    key = crypto.derive_key(PSK)
    blob = bytearray(crypto.seal(key, b"ABCDEFGH" * 3))
    blob[-1] ^= 0xFF
    with pytest.raises(crypto.CryptoError):
        crypto.open_(key, bytes(blob))


def test_protocol_ctrl_roundtrip():
    obj = {"type": "req", "id": 1, "method": "exec", "params": {}}
    frame = protocol.encode_ctrl(obj)
    ftype, payload = protocol.decode_frame(frame)
    assert ftype == protocol.FRAME_TYPE_CTRL
    assert protocol.decode_ctrl(payload) == obj


@pytest.fixture()
def settings(tmp_path):
    s = Settings()
    s.data_dir = tmp_path / "data"
    return s


def test_upload_download_roundtrip(settings, tmp_path):
    settings.chunk_size = 16
    tm = TransferManager(settings)
    payload = bytes(range(256)) * 4
    target = tmp_path / "u.bin"
    digest = hashlib.sha256(payload).hexdigest()

    begin = tm.upload_begin(str(target), len(payload), digest)
    off = begin["next_offset"]
    for i in range(0, len(payload), 16):
        r = tm.upload_chunk(begin["upload_id"], off, base64.b64encode(payload[i:i + 16]).decode())
        off = r["next_offset"]
    done = tm.upload_finish(begin["upload_id"])
    assert target.read_bytes() == payload
    assert done["sha256"] == digest

    db = tm.download_begin(str(target))
    out = bytearray()
    off = 0
    while True:
        r = tm.download_chunk(db["download_id"], off, 16)
        out += base64.b64decode(r["chunk_b64"])
        off = r["next_offset"]
        if r["eof"]:
            break
    tm.download_finish(db["download_id"])
    assert bytes(out) == payload


def test_single_session_only(settings, tmp_path):
    tm = TransferManager(settings)
    tm.upload_begin(str(tmp_path / "a"), 5)
    with pytest.raises(TransferError):
        tm.upload_begin(str(tmp_path / "b"), 5)


def test_sha_mismatch(settings, tmp_path):
    tm = TransferManager(settings)
    begin = tm.upload_begin(str(tmp_path / "x"), 4, "deadbeef")
    tm.upload_chunk(begin["upload_id"], 0, base64.b64encode(b"data").decode())
    with pytest.raises(TransferError):
        tm.upload_finish(begin["upload_id"])
