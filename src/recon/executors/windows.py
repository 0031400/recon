"""Windows executor."""

from __future__ import annotations

import os
import shutil

from .base import Executor


class WindowsExecutor(Executor):
    def _find(self, candidates: list[str]) -> str | None:
        for c in candidates:
            p = shutil.which(c)
            if p:
                return p
        return None

    def available_shells(self) -> list[str]:
        shells = []
        if shutil.which("pwsh"):
            shells.append("pwsh")
        if shutil.which("powershell"):
            shells.append("powershell")
        if shutil.which("cmd"):
            shells.append("cmd")
        return shells

    def resolve_shell(self, shell: str) -> tuple[str, dict]:
        extra_env = {"PYTHONIOENCODING": "utf-8"}
        if shell in ("auto", ""):
            # Prefer PowerShell 7, then Windows PowerShell, then cmd.
            for name in ("pwsh", "powershell", "cmd"):
                exe = self._find([name])
                if exe:
                    return exe, extra_env
            raise FileNotFoundError("no usable shell (pwsh/powershell/cmd) found")
        if shell in ("pwsh", "powershell", "cmd"):
            exe = self._find([shell])
            if exe:
                return exe, extra_env
            raise FileNotFoundError(f"shell {shell!r} not found")
        # Bare "bash"/"sh"/"git-bash" etc. try as-is then fallback.
        exe = self._find([shell])
        if exe:
            return exe, extra_env
        raise FileNotFoundError(f"shell {shell!r} not found")
