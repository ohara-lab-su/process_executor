#!/usr/bin/env python
# coding: utf-8
"""PM2CD FastAPI サーバー起動用モジュール。"""

import argparse
import sys
from pathlib import Path

from ese774_frame.api_server import FastApiServer

PM2CD_DIR = Path(__file__).resolve().parent.parent
if str(PM2CD_DIR) not in sys.path:
    sys.path.insert(0, str(PM2CD_DIR))

from pm2cd_multi import PM2CD


IP_ADDRESS = "10.170.10.62"
DEVICE_PORT = 7777
TIMEOUT_SEC = 3.0
HOST = "127.0.0.1"
SERVER_PORT = 8000


def create_server(
    ip_address: str = IP_ADDRESS,
    device_port: int = DEVICE_PORT,
    timeout_sec: float = TIMEOUT_SEC,
) -> FastApiServer:
    """PM2CD 用 FastApiServer を作成する。"""
    return FastApiServer(
        device_cls=PM2CD,
        router_cls=None,
        config=None,
        api_spec=None,
        object_name="pm2cd",
        device_kwargs={
            "ip_address": ip_address,
            "port": device_port,
            "timeout_sec": timeout_sec,
        },
        lifespan_msg_prefix="PM2CD",
    )


def run(
    ip_address: str = IP_ADDRESS,
    device_port: int = DEVICE_PORT,
    timeout_sec: float = TIMEOUT_SEC,
    host: str = HOST,
    port: int = SERVER_PORT,
    reload: bool = False,
) -> None:
    """PM2CD FastAPI サーバーを起動する。"""
    server = create_server(
        ip_address=ip_address,
        device_port=device_port,
        timeout_sec=timeout_sec,
    )
    server.run(
        host=host,
        port=port,
        reload=reload,
    )


def main() -> None:
    """FastAPI サーバーを起動する。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("ip_address", nargs="?", default=IP_ADDRESS)
    parser.add_argument("--device-port", type=int, default=DEVICE_PORT)
    parser.add_argument("--timeout-sec", type=float, default=TIMEOUT_SEC)
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=SERVER_PORT)
    args = parser.parse_args()

    run(
        ip_address=args.ip_address,
        device_port=args.device_port,
        timeout_sec=args.timeout_sec,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
