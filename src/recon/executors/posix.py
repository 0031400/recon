"""POSIX / Linux executor."""

from __future__ import annotations

import os
import shutil

from .base import Executor


class PosixExecutor(Executor):
    def _find(self, candidates: list[str]) -> str | None:
        for c in candidates:
            p = shutil.which(c)
            if p:
                return p
        return None

    def available_shells(self) -> list[str]:
        shells = []
        for name in ("bash", "sh", "zsh", "dash"):
            if shutil.which(name):
                shells.append(name)
        return shells

    def resolve_shell(self, shell: str) -> tuple[str, dict]:
        extra_env = {"PYTHONIOENCODING": "utf-8", "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8")}
        if shell in ("auto", ""):
            shell = "bash"
        if shell in ("bash", "sh", "zsh", "dash"):
            exe = self._find([shell])
            if exe:
                return exe, extra_env
            # fallback chain
            exe = self._find(["bash", "sh"])
            if exe:
                return exe, extra_env
        # Treat an explicit absolute path as the shell executable.
        if os.path.isabs(shell) and os.path.exists(shell):
            return shell, extra_env
        raise FileNotFoundError(f"no usable shell on host (requested {shell!r})")
