"""Command executor abstraction and shared helpers."""

from __future__ import annotations

import asyncio
import locale
import os
import platform
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping


@dataclass
class ExecResult:
    """Structured result of a command execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False


def _decode(raw: bytes) -> str:
    """Decode bytes trying utf-8 first, then the platform preferred encoding."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        for enc in ("gbk", locale.getpreferredencoding(False), "latin-1"):
            try:
                return raw.decode(enc, errors="strict")
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text.encode("utf-8", "ignore")) <= limit:
        return text, False
    # Cut on byte budget, keep it decodable.
    truncated = text.encode("utf-8", "ignore")[:limit].decode("utf-8", "ignore")
    return truncated + "\n... [truncated]", True


class Executor(ABC):
    """Runs a shell command on the local host."""

    def __init__(self, settings) -> None:
        self.settings = settings

    @abstractmethod
    def resolve_shell(self, shell: str) -> tuple[str, dict]:
        """Return (executable_path, extra_env) for the requested shell token."""

    @abstractmethod
    def available_shells(self) -> list[str]:
        """List shell names usable on this host."""

    async def run(
        self,
        command: str,
        shell: str = "auto",
        cwd: str | None = None,
        timeout: int | None = None,
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
    ) -> ExecResult:
        timeout = timeout or self.settings.default_timeout
        timeout = min(timeout, self.settings.max_timeout)

        exe, extra_env = self.resolve_shell(shell)
        full_env = {**os.environ, **extra_env}
        if env:
            full_env.update(env)

        workdir = cwd or os.getcwd()
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                executable=exe,
                cwd=workdir,
                env=full_env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            return ExecResult(
                exit_code=127,
                stdout="",
                stderr=f"failed to start shell {exe!r}: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        data = stdin.encode("utf-8") if stdin is not None else None
        try:
            out_b, err_b = await asyncio.wait_for(
                proc.communicate(input=data), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(
                exit_code=124,
                stdout="",
                stderr=f"command timed out after {timeout}s",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        out, out_trunc = _truncate(_decode(out_b), self.settings.max_output_bytes)
        err, err_trunc = _truncate(_decode(err_b), self.settings.max_output_bytes)
        return ExecResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=out,
            stderr=err,
            duration_ms=int((time.monotonic() - started) * 1000),
            truncated=out_trunc or err_trunc,
        )

    def host_info(self) -> dict:
        return {
            "name": self.settings.name,
            "os": platform.system(),
            "os_version": platform.version(),
            "release": platform.release(),
            "arch": platform.machine(),
            "hostname": platform.node(),
            "user": os.environ.get("USER") or os.environ.get("USERNAME") or "",
            "python": sys.version.split()[0],
            "cwd": os.getcwd(),
            "shells": self.available_shells(),
        }


def get_executor(settings) -> Executor:
    """Pick the platform-specific executor."""
    if platform.system() == "Windows":
        from .windows import WindowsExecutor

        return WindowsExecutor(settings)
    from .posix import PosixExecutor

    return PosixExecutor(settings)
