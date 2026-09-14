#!/usr/bin/env python
# coding: utf-8
"""PM2CD 基本 multi FastAPI サーバー。"""

from ese774_frame.api_server import FastApiServer
from pm2cd_multi import PM2CD

IP_ADDRESS = "10.170.10.62"
DEVICE_PORT = 7777
TIMEOUT_SEC = 3.0
WORKER_COUNT = 3

HOST = "127.0.0.1"
SERVER_PORT = 8000


def create_server() -> FastApiServer:
    """PM2CD 基本 multi 用 FastApiServer を生成する。"""
    return FastApiServer(
        device_cls=PM2CD,
        router_cls=None,
        config=None,
        api_spec=None,
        object_name="pm2cd_multi",
        device_kwargs={
            "ip_address": IP_ADDRESS,
            "port": DEVICE_PORT,
            "timeout_sec": TIMEOUT_SEC,
            "worker_count": WORKER_COUNT,
        },
        lifespan_msg_prefix="PM2CD multi",
    )


def main() -> None:
    """PM2CD 基本 multi FastAPI サーバーを起動する。"""
    server = create_server()
    server.run(
        host=HOST,
        port=SERVER_PORT,
    )


if __name__ == "__main__":
    main()
