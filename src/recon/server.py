"""recon server: encrypted TCP service running on the controlled host.

Wire: frit-style AES-128-ECB frames (see crypto.py / aio.py).
Plaintext inside a CTRL frame is a JSON RPC envelope:

    request :  {"type": "req", "id": <int>, "method": "<name>", "params": {...}}
    response:  {"type": "res", "id": <int>, "ok": true,  "result": {...}}
               {"type": "res", "id": <int>, "ok": false, "error": "..."}

The first decrypted frame must be a valid CTRL frame (frame type 0x66F0) and a
well-formed request; otherwise the connection is dropped (auth middleware).
"""

from __future__ import annotations

import asyncio
import logging

from . import aio, crypto, protocol
from .backend import Backend
from .config import ServerConfig

log = logging.getLogger("recon.server")


class ReconServer:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.key = crypto.derive_key(config.psk)
        self.backend = Backend(config.settings)
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self.config.host, self.config.port
        )
        log.info("recon server listening on %s:%s", self.config.host, self.config.port)
        async with self._server:
            await self._server.serve_forever()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        log.info("client connected: %s", peer)
        try:
            while True:
                try:
                    frame_type, payload = await aio.read_typed(reader, self.key)
                except (asyncio.IncompleteReadError, ConnectionError):
                    break
                except crypto.CryptoError as exc:
                    log.warning("crypto/auth failure from %s: %s", peer, exc)
                    break

                if frame_type != protocol.FRAME_TYPE_CTRL:
                    log.warning("unexpected frame type from %s", peer)
                    break

                try:
                    req = protocol.decode_ctrl(payload)
                    resp = await self._dispatch(req)
                except protocol.ProtocolError as exc:
                    log.warning("bad request from %s: %s", peer, exc)
                    break
                except Exception as exc:  # noqa: BLE001
                    log.exception("dispatch error: %s", exc)
                    resp = {"type": "res", "id": None, "ok": False, "error": str(exc)}

                await aio.send_ctrl(writer, self.key, resp)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            log.info("client disconnected: %s", peer)

    async def _dispatch(self, req: dict) -> dict:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        if not isinstance(method, str):
            return {"type": "res", "id": req_id, "ok": False, "error": "missing method"}
        try:
            result = await self.backend.dispatch(method, params)
            return {"type": "res", "id": req_id, "ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"type": "res", "id": req_id, "ok": False, "error": str(exc)}


def run_server(config: ServerConfig) -> None:
    logging.basicConfig(
        level=logging.DEBUG if config.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(ReconServer(config).start())
