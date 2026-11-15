"""recon client: local stdio MCP server that proxies to a remote recon server.

Architecture (per user's design):

    AI client --stdio(MCP)--> recon (this) --TCP+AES frames--> recon server

MCP tools here simply serialise their arguments into an RPC envelope, send it
over the encrypted TCP link, and return the remote result. The remote performs
the actual command execution / file IO.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import logging

from mcp.server.fastmcp import FastMCP

from . import aio, crypto, protocol
from .config import ClientConfig

log = logging.getLogger("recon.client")


class RemoteConnection:
    """A persistent encrypted TCP link to one recon server."""

    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.key = crypto.derive_key(config.psk)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._ids = itertools.count(1)

    async def _connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self.config.host, self.config.port
        )
        log.info("connected to %s:%s", self.config.host, self.config.port)

    async def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self._reader = self._writer = None

    async def call(self, method: str, params: dict) -> dict:
        async with self._lock:
            if self._writer is None or self._writer.is_closing():
                await self._connect()
            # After the branch above the connection is guaranteed to exist.
            writer, reader = self._writer, self._reader
            if writer is None or reader is None:
                raise RuntimeError("not connected")
            req_id = next(self._ids)
            req = {"type": "req", "id": req_id, "method": method, "params": params}
            try:
                await aio.send_ctrl(writer, self.key, req)
                while True:
                    frame_type, payload = await aio.read_typed(reader, self.key)
                    if frame_type != protocol.FRAME_TYPE_CTRL:
                        continue
                    resp = protocol.decode_ctrl(payload)
                    if resp.get("id") != req_id:
                        continue  # ignore stale
                    if not resp.get("ok"):
                        raise RuntimeError(resp.get("error", "remote error"))
                    return resp.get("result", {})
            except (asyncio.IncompleteReadError, ConnectionError) as exc:
                await self.close()
                raise RuntimeError(f"connection to remote lost: {exc}") from exc
            except crypto.CryptoError as exc:
                await self.close()
                raise RuntimeError(f"crypto/auth failure: {exc}") from exc


def build_client(config: ClientConfig) -> FastMCP:
    conn = RemoteConnection(config)
    mcp = FastMCP(config.name or "recon")

    # ---------------- meta ----------------
    @mcp.tool()
    async def host_info() -> dict:
        """Return basic information about the controlled (remote) host."""
        return await conn.call("host_info", {})

    # ---------------- exec ----------------
    @mcp.tool()
    async def exec(command: str, shell: str = "auto", cwd: str | None = None,
                   timeout: int = 60, env: dict[str, str] | None = None,
                   stdin: str | None = None) -> dict:
        """Execute a shell command on the controlled host and return its result.

        Returns {exit_code, stdout, stderr, duration_ms, truncated}.
        `shell` is "auto" (default), or bash/sh on Linux, pwsh/powershell/cmd
        on Windows.
        """
        return await conn.call(
            "exec",
            {
                "command": command,
                "shell": shell,
                "cwd": cwd,
                "timeout": timeout,
                "env": env,
                "stdin": stdin,
            },
        )

    # ---------------- upload ----------------
    @mcp.tool()
    async def upload_begin(path: str, total_size: int, sha256: str | None = None,
                           resume: bool = False) -> dict:
        """Start a chunked upload to `path` on the controlled host (>= threshold).

        Returns {upload_id, next_offset}. Only one upload at a time.
        """
        return await conn.call(
            "upload_begin",
            {"path": path, "total_size": total_size, "sha256": sha256, "resume": resume},
        )

    @mcp.tool()
    async def upload_chunk(upload_id: str, offset: int, chunk_b64: str) -> dict:
        """Append one base64 chunk at `offset`; returns {received, next_offset}."""
        return await conn.call(
            "upload_chunk",
            {"upload_id": upload_id, "offset": offset, "chunk_b64": chunk_b64},
        )

    @mcp.tool()
    async def upload_finish(upload_id: str) -> dict:
        """Finalize an upload (verify + atomic rename); returns {bytes, sha256, path}."""
        return await conn.call("upload_finish", {"upload_id": upload_id})

    @mcp.tool()
    async def upload_abort(upload_id: str) -> dict:
        """Cancel an upload and delete its temp file."""
        return await conn.call("upload_abort", {"upload_id": upload_id})

    @mcp.tool()
    async def upload(path: str, content_b64: str) -> dict:
        """Upload a small file in one call (base64). Returns {path, bytes, sha256}."""
        return await conn.call("upload", {"path": path, "content_b64": content_b64})

    # ---------------- download ----------------
    @mcp.tool()
    async def download_begin(path: str) -> dict:
        """Open a chunked download; returns {download_id, total_size, sha256}."""
        return await conn.call("download_begin", {"path": path})

    @mcp.tool()
    async def download_chunk(download_id: str, offset: int, length: int) -> dict:
        """Read up to `length` bytes; returns {chunk_b64, eof, next_offset}."""
        return await conn.call(
            "download_chunk",
            {"download_id": download_id, "offset": offset, "length": length},
        )

    @mcp.tool()
    async def download_finish(download_id: str) -> dict:
        """Release a download session."""
        return await conn.call("download_finish", {"download_id": download_id})

    @mcp.tool()
    async def download(path: str, max_bytes: int | None = None) -> dict:
        """Download a small file in one call (base64). Returns content_b64, bytes, sha256."""
        return await conn.call("download", {"path": path, "max_bytes": max_bytes})

    return mcp


def run_client(config: ClientConfig) -> None:
    logging.basicConfig(
        level=logging.DEBUG if config.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    build_client(config).run(transport="stdio")
