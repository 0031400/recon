"""Configuration for recon server (remote) and client (local MCP bridge).

Two JSON config files, mirroring frit:

Server (``recon server -c data/server.json``):
    {
        "listen": "tcp://0.0.0.0:40000",
        "psk": "shared-secret",
        "debug": false
    }

Client (``recon client -c data/client.json``, or via MCP "args"):
    {
        "server": "tcp://1.2.3.4:40000",
        "psk": "shared-secret",
        "debug": false
    }

The exec/transfer tuning (timeouts, chunk size, temp dir) lives in the
server-side config under an optional ``settings`` key; all fields default.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .config_tuning import Settings
from .config_tuning import load_settings as _load_tuning


class ConfigError(Exception):
    pass


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid json in {path}: {exc}") from exc


def parse_url(url: str) -> tuple[str, int]:
    """Parse 'tcp://host:port' into (host, port)."""
    u = urlparse(url)
    if u.scheme != "tcp":
        raise ConfigError(f"only tcp scheme is supported, got: {url!r}")
    if not u.hostname or not u.port:
        raise ConfigError(f"invalid address (need host and port): {url!r}")
    return u.hostname, u.port


class ServerConfig(BaseModel):
    listen: str
    psk: str
    debug: bool = False
    host: str = ""
    port: int = 0
    settings: Settings = Field(default_factory=Settings)

    @classmethod
    def load(cls, path: str) -> "ServerConfig":
        d = _load_json(path)
        if not d.get("listen"):
            raise ConfigError("server config: 'listen' is required")
        if not d.get("psk"):
            raise ConfigError("server config: 'psk' is required")
        host, port = parse_url(d["listen"])
        settings = _load_tuning(d.get("settings"))
        return cls(
            listen=d["listen"],
            psk=d["psk"],
            debug=bool(d.get("debug", False)),
            host=host,
            port=port,
            settings=settings,
        )


class ClientConfig(BaseModel):
    server: str
    psk: str
    debug: bool = False
    host: str = ""
    port: int = 0

    @classmethod
    def load(cls, path: str) -> "ClientConfig":
        d = _load_json(path)
        if not d.get("server"):
            raise ConfigError("client config: 'server' is required")
        if not d.get("psk"):
            raise ConfigError("client config: 'psk' is required")
        host, port = parse_url(d["server"])
        return cls(
            server=d["server"],
            psk=d["psk"],
            debug=bool(d.get("debug", False)),
            host=host,
            port=port,
        )
