"""Execution backend: the actual work done on the controlled host.

The ``Backend`` owns the platform executor and the single-session transfer
manager, and exposes a uniform ``dispatch(method, params)`` used by both the
remote TCP server (production) and unit tests.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from .config import Settings
from .executors import Executor, get_executor
from .transfer import TransferError, TransferManager, sha256_file


class Backend:
    def __init__(self, settings: Settings, executor: Executor | None = None) -> None:
        self.settings = settings
        self.executor = executor or get_executor(settings)
        self.transfers = TransferManager(settings)

    async def dispatch(self, method: str, params: dict) -> dict:
        handler = getattr(self, f"_m_{method}", None)
        if handler is None:
            raise ValueError(f"unknown method: {method}")
        return await handler(params)

    # ---------------- meta ----------------
    async def _m_host_info(self, params: dict) -> dict:
        return self.executor.host_info()

    # ---------------- exec ----------------
    async def _m_exec(self, params: dict) -> dict:
        r = await self.executor.run(
            params["command"],
            shell=params.get("shell", "auto"),
            cwd=params.get("cwd"),
            timeout=params.get("timeout", 60),
            env=params.get("env"),
            stdin=params.get("stdin"),
        )
        return {
            "exit_code": r.exit_code,
            "stdout": r.stdout,
            "stderr": r.stderr,
            "duration_ms": r.duration_ms,
            "truncated": r.truncated,
        }

    # ---------------- upload ----------------
    async def _m_upload_begin(self, params: dict) -> dict:
        try:
            return self.transfers.upload_begin(
                params["path"],
                params["total_size"],
                params.get("sha256"),
                params.get("resume", False),
            )
        except TransferError as exc:
            raise ValueError(str(exc)) from exc

    async def _m_upload_chunk(self, params: dict) -> dict:
        try:
            return self.transfers.upload_chunk(
                params["upload_id"], params["offset"], params["chunk_b64"]
            )
        except TransferError as exc:
            raise ValueError(str(exc)) from exc

    async def _m_upload_finish(self, params: dict) -> dict:
        try:
            return self.transfers.upload_finish(params["upload_id"])
        except TransferError as exc:
            raise ValueError(str(exc)) from exc

    async def _m_upload_abort(self, params: dict) -> dict:
        try:
            return self.transfers.upload_abort(params["upload_id"])
        except TransferError as exc:
            raise ValueError(str(exc)) from exc

    async def _m_upload(self, params: dict) -> dict:
        raw = base64.b64decode(params["content_b64"].encode("ascii"))
        target = Path(params["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return {"path": str(target), "bytes": len(raw), "sha256": sha256_file(target)}

    # ---------------- download ----------------
    async def _m_download_begin(self, params: dict) -> dict:
        try:
            return self.transfers.download_begin(params["path"])
        except TransferError as exc:
            raise ValueError(str(exc)) from exc

    async def _m_download_chunk(self, params: dict) -> dict:
        try:
            return self.transfers.download_chunk(
                params["download_id"], params["offset"], params["length"]
            )
        except TransferError as exc:
            raise ValueError(str(exc)) from exc

    async def _m_download_finish(self, params: dict) -> dict:
        try:
            return self.transfers.download_finish(params["download_id"])
        except TransferError as exc:
            raise ValueError(str(exc)) from exc

    async def _m_download(self, params: dict) -> dict:
        source = Path(params["path"])
        if not source.is_file():
            raise ValueError(f"file not found: {source}")
        data = source.read_bytes()
        truncated = False
        max_bytes = params.get("max_bytes")
        if max_bytes is not None and len(data) > max_bytes:
            data = data[:max_bytes]
            truncated = True
        return {
            "path": str(source),
            "content_b64": base64.b64encode(data).decode("ascii"),
            "bytes": len(data),
            "sha256": sha256_file(source),
            "truncated": truncated,
        }
