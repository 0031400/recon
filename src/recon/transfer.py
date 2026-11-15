"""Chunked file transfer manager.

Design constraints from the spec:
  * chunk size = 10 MiB, single-call shortcut threshold = 10 MiB
  * temp files live under <project>/data/<temp_subdir>/<session_id>/
  * NO concurrency: at most one active upload or download at a time.
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class TransferError(RuntimeError):
    """Raised for illegal transfer state transitions."""


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class UploadSession:
    upload_id: str
    target: Path
    part: Path
    total_size: int
    expected_sha256: str | None = None
    received: int = 0


@dataclass
class DownloadSession:
    download_id: str
    source: Path
    total_size: int
    sha256: str


class TransferManager:
    """Owns the single active upload/download session (no concurrency)."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self._upload: UploadSession | None = None
        self._download: DownloadSession | None = None

    # ---------- helpers ----------
    def _session_dir(self) -> Path:
        root = self.settings.temp_root() / uuid.uuid4().hex
        root.mkdir(parents=True, exist_ok=True)
        return root

    # ---------- upload ----------
    def upload_begin(
        self,
        path: str,
        total_size: int,
        sha256: str | None = None,
        resume: bool = False,
    ) -> dict:
        if self._upload is not None:
            raise TransferError(
                "an upload is already in progress; only one transfer at a time is allowed"
            )
        if total_size > self.settings.max_transfer_bytes:
            raise TransferError(
                f"file too large: {total_size} > max_transfer_bytes {self.settings.max_transfer_bytes}"
            )
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        if resume:
            # Recover any previous partial file for this exact target.
            part = self._find_part_for(target)
            if part is None:
                part = target.with_name(f".{target.name}.part")
                part.touch()
            received = part.stat().st_size
        else:
            part = target.with_name(f".{target.name}.part")
            part.touch()
            received = 0

        upload_id = uuid.uuid4().hex
        self._upload = UploadSession(
            upload_id=upload_id,
            target=target,
            part=part,
            total_size=total_size,
            expected_sha256=sha256,
            received=received,
        )
        return {"upload_id": upload_id, "next_offset": received}

    def _find_part_for(self, target: Path) -> Path | None:
        part = target.with_name(f".{target.name}.part")
        return part if part.exists() else None

    def upload_chunk(self, upload_id: str, offset: int, chunk_b64: str) -> dict:
        sess = self._require_upload(upload_id)
        data = _b64d(chunk_b64)
        if offset != sess.received:
            raise TransferError(
                f"offset mismatch: expected {sess.received}, got {offset}"
            )
        with open(sess.part, "r+b") as f:
            f.seek(offset)
            f.write(data)
        sess.received += len(data)
        return {"received": sess.received, "next_offset": sess.received}

    def upload_finish(self, upload_id: str) -> dict:
        sess = self._require_upload(upload_id)
        if sess.received != sess.total_size:
            raise TransferError(
                f"size mismatch: received {sess.received}, expected {sess.total_size}"
            )
        digest = sha256_file(sess.part)
        if sess.expected_sha256 and digest.lower() != sess.expected_sha256.lower():
            raise TransferError(
                f"sha256 mismatch: got {digest}, expected {sess.expected_sha256}"
            )
        os.replace(sess.part, sess.target)  # atomic same-dir rename
        size = sess.target.stat().st_size
        self._upload = None
        return {"bytes": size, "sha256": digest, "path": str(sess.target)}

    def upload_abort(self, upload_id: str) -> dict:
        sess = self._require_upload(upload_id)
        try:
            sess.part.unlink(missing_ok=True)
        finally:
            self._upload = None
        return {"ok": True}

    def _require_upload(self, upload_id: str) -> UploadSession:
        if self._upload is None or self._upload.upload_id != upload_id:
            raise TransferError(f"no active upload with id {upload_id!r}")
        return self._upload

    # ---------- download ----------
    def download_begin(self, path: str) -> dict:
        if self._download is not None:
            raise TransferError(
                "a download is already in progress; only one transfer at a time is allowed"
            )
        source = Path(path)
        if not source.is_file():
            raise TransferError(f"file not found: {source}")
        size = source.stat().st_size
        digest = sha256_file(source)
        download_id = uuid.uuid4().hex
        self._download = DownloadSession(
            download_id=download_id,
            source=source,
            total_size=size,
            sha256=digest,
        )
        return {"download_id": download_id, "total_size": size, "sha256": digest}

    def download_chunk(self, download_id: str, offset: int, length: int) -> dict:
        sess = self._require_download(download_id)
        if offset < 0:
            raise TransferError("offset must be >= 0")
        length = min(length, self.settings.chunk_size)
        with open(sess.source, "rb") as f:
            f.seek(offset)
            data = f.read(length)
        next_offset = offset + len(data)
        eof = next_offset >= sess.total_size
        return {
            "chunk_b64": _b64e(data),
            "eof": eof,
            "next_offset": next_offset,
        }

    def download_finish(self, download_id: str) -> dict:
        self._require_download(download_id)
        self._download = None
        return {"ok": True}

    def _require_download(self, download_id: str) -> DownloadSession:
        if self._download is None or self._download.download_id != download_id:
            raise TransferError(f"no active download with id {download_id!r}")
        return self._download

    @property
    def busy(self) -> bool:
        return self._upload is not None or self._download is not None
