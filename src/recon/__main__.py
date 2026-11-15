"""CLI entry point.

Only a config file path may be passed on the command line; every other setting
comes from that file. No environment variables are read.

    recon server -c data/server.json        # run on the controlled host
    recon -c data/client.json               # local stdio MCP bridge
"""

from __future__ import annotations

import argparse
import sys

from .config import ClientConfig, ConfigError, ServerConfig


def _get_config_path(args: argparse.Namespace) -> str:
    path = getattr(args, "server_config", None) or args.config
    if not path:
        raise SystemExit("-c <config.json> is required")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="recon",
        description="Encrypted remote-control MCP (config file only)",
    )
    parser.add_argument("-c", "--config", dest="config", default=None,
                        help="path to JSON config (client mode)")

    sub = parser.add_subparsers(dest="mode")
    sp = sub.add_parser("server", help="run the encrypted TCP server (on the controlled host)")
    sp.add_argument("-c", "--config", dest="server_config", default=None,
                    help="path to server JSON config")

    args = parser.parse_args()
    config_path = _get_config_path(args)

    try:
        if args.mode == "server":
            from .server import run_server

            run_server(ServerConfig.load(config_path))
        else:
            from .client import run_client

            run_client(ClientConfig.load(config_path))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
