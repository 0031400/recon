"""Tuning settings for exec / file transfer (chunk size, timeouts, temp dir).

These used to be the whole ``Settings`` in the prior HTTP design; here they are
an embedded ``settings`` object inside the server config. Every field has a
default so an empty/missing object still works.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

MB = 1024 * 1024

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class Settings(BaseModel):
    # --- exec ---
    default_timeout: int = 60
    max_timeout: int = 3600
    max_output_bytes: int = 2 * MB

    # --- transfer (chunk size / threshold = 10 MiB) ---
    chunk_size: int = 10 * MB
    chunk_threshold: int = 10 * MB
    max_transfer_bytes: int = 1024 * MB
    data_dir: Path = DEFAULT_DATA_DIR
    temp_subdir: str = "tmp"
    allow_small_transfer: bool = True

    # --- security (encryption is always on; destructive ops allowed) ---
    allow_destructive: bool = True

    def temp_root(self) -> Path:
        root = self.data_dir / self.temp_subdir
        root.mkdir(parents=True, exist_ok=True)
        return root


def load_settings(data: dict | None) -> Settings:
    """Build Settings from a dict (e.g. the server config's ``settings`` key)."""
    return Settings(**(data or {}))
