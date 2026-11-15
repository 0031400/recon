"""CLI entry point.

``server`` and ``client`` are peer sub-commands with identical structure. Each
takes only ``-c <config.json>``; every other setting comes from that file and
no environment variables are read.

    recon server -c data/server.json        # run on the controlled host
    recon client -c data/client.json        # local stdio MCP bridge
"""

from __future__ import annotations

import argparse
import sys

from .config import ClientConfig, ConfigError, ServerConfig


def _run_server(config_path: str) -> None:
    from .server import run_server

    run_server(ServerConfig.load(config_path))


def _run_client(config_path: str) -> None:
    from .client import run_client

    run_client(ClientConfig.load(config_path))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="recon",
        description="Encrypted remote-control MCP (config file only)",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    sp = sub.add_parser("server", help="run the encrypted TCP server (on the controlled host)")
    sp.add_argument("-c", "--config", required=True, help="path to server JSON config")

    cp = sub.add_parser("client", help="run the local stdio MCP bridge")
    cp.add_argument("-c", "--config", required=True, help="path to client JSON config")

    args = parser.parse_args()

    try:
        if args.mode == "server":
            _run_server(args.config)
        else:  # "client"
            _run_client(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
