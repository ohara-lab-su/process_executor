#!/usr/bin/env python
# coding: utf-8
"""PM2CD 用の同期 FastAPI クライアント。"""

from typing import Any, Optional

from ese774_frame.clients.sync_device_client import SyncDeviceClient


class SyncPM2CDClient(SyncDeviceClient):
    """PM2CD FastAPI サーバーへ接続する同期クライアント。"""

    def __init__(
        self,
        server_ip: str = "127.0.0.1",
        server_port: int = 8000,
        logger: Optional[Any] = None,
        log_level: str = "INFO",
    ):
        super().__init__(
            server_ip=server_ip,
            server_port=server_port,
            object_name="pm2cd",
            api_spec=None,
            logger=logger,
            log_level=log_level,
        )

    def close_client(self) -> None:
        """HTTP client を同期的に閉じる。"""
        self._sync_wrap(super().close())
