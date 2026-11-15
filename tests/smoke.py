"""End-to-end smoke test for the encrypted TCP architecture.

Starts a real recon server on localhost, connects a RemoteConnection client,
and exercises exec + chunked upload/download through the encrypted frames.

No pytest tmp_dir machinery: everything lives under ./data/_smoketest.
"""

import asyncio
import base64
import hashlib
import socket
import sys
from pathlib import Path

from recon import crypto
from recon.client import RemoteConnection
from recon.config import ClientConfig, ServerConfig
from recon.config_tuning import Settings
from recon.server import ReconServer

WORK = Path(__file__).resolve().parent.parent / "data" / "_smoketest"
WORK.mkdir(parents=True, exist_ok=True)
PSK = "smoke-shared-secret"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        raise SystemExit(1)


def test_crypto_roundtrip():
    key = crypto.derive_key(PSK)
    payload = b'{"jsonrpc":"2.0"}'
    blob = crypto.seal(key, payload)
    check("crypto roundtrip", crypto.open_(key, blob) == payload)
    # wrong key must fail auth
    bad = crypto.derive_key("wrong")
    try:
        crypto.open_(bad, blob)
        check("crypto wrong-key rejected", False)
    except crypto.CryptoError:
        check("crypto wrong-key rejected", True)


async def test_e2e():
    port = free_port()
    settings = Settings()
    settings.data_dir = WORK / "data"
    settings.chunk_size = 16  # force multiple chunks

    sconf = ServerConfig(listen=f"tcp://127.0.0.1:{port}", psk=PSK, host="127.0.0.1",
                         port=port, settings=settings)
    server = ReconServer(sconf)
    srv_task = asyncio.create_task(server.start())
    await asyncio.sleep(0.3)

    cconf = ClientConfig(server=f"tcp://127.0.0.1:{port}", psk=PSK, host="127.0.0.1", port=port)
    conn = RemoteConnection(cconf)
    try:
        # host_info
        hi = await conn.call("host_info", {})
        check("host_info", "os" in hi, hi.get("os"))

        # exec
        cmd = "echo recon-e2e" if sys.platform != "win32" else "Write-Output recon-e2e"
        r = await conn.call("exec", {"command": cmd})
        check("exec exit0", r["exit_code"] == 0, f"code={r['exit_code']}")
        check("exec stdout", "recon-e2e" in r["stdout"].lower())

        # chunked upload
        payload = bytes(range(256)) * 8
        target = WORK / "uploaded.bin"
        digest = hashlib.sha256(payload).hexdigest()
        begin = await conn.call("upload_begin",
                                {"path": str(target), "total_size": len(payload), "sha256": digest})
        off = begin["next_offset"]
        for i in range(0, len(payload), 16):
            res = await conn.call("upload_chunk", {
                "upload_id": begin["upload_id"], "offset": off,
                "chunk_b64": base64.b64encode(payload[i:i + 16]).decode()})
            off = res["next_offset"]
        done = await conn.call("upload_finish", {"upload_id": begin["upload_id"]})
        check("upload bytes", done["bytes"] == len(payload))
        check("upload content", target.read_bytes() == payload)
        check("upload sha", done["sha256"] == digest)

        # chunked download
        src = WORK / "source.bin"
        src.write_bytes(payload)
        db = await conn.call("download_begin", {"path": str(src)})
        out = bytearray()
        off = 0
        while True:
            res = await conn.call("download_chunk",
                                  {"download_id": db["download_id"], "offset": off, "length": 16})
            out += base64.b64decode(res["chunk_b64"])
            off = res["next_offset"]
            if res["eof"]:
                break
        await conn.call("download_finish", {"download_id": db["download_id"]})
        check("download content", bytes(out) == payload)
        check("download sha", hashlib.sha256(bytes(out)).hexdigest() == db["sha256"])

        # wrong psk must be rejected at the TCP layer
        bad_conn = RemoteConnection(ClientConfig(server=f"tcp://127.0.0.1:{port}",
                                                 psk="wrong-key", host="127.0.0.1", port=port))
        try:
            await bad_conn.call("host_info", {})
            check("wrong psk rejected", False, "call succeeded with wrong key")
        except Exception:
            check("wrong psk rejected", True)
        finally:
            await bad_conn.close()
    finally:
        await conn.close()
        srv_task.cancel()
        try:
            await srv_task
        except (asyncio.CancelledError, Exception):
            pass

    print("\nALL E2E CHECKS PASSED")


async def main():
    test_crypto_roundtrip()
    await test_e2e()


if __name__ == "__main__":
    asyncio.run(main())
