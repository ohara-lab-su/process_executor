#!/usr/bin/env python
# coding: utf-8
"""PM2CDsingle を ProcessExecutor で実行する shared multi sample。"""

from functools import partial
from multiprocessing.managers import BaseManager
from typing import Any, Optional, Union

from pm2cd.core.communication_tcp import Communication
from pm2cd.core.motor_status import MotorStatus
from pm2cd.pm2cd_single import PM2CDsingle
from process_executor import ProcessExecutor
from x_logger import XLogger


class _CommunicationManager(BaseManager):
    """単一の Communication を所有する manager。"""


_CommunicationManager.register(
    "Communication",
    Communication,
    exposed=(
        "is_connected",
        "connect",
        "disconnect",
        "send",
        "receive",
        "query",
        "close",
    ),
)


def _create_worker(
    context: Any,
    ip_address: str,
    port: int,
    timeout_sec: float,
    channel_count: int,
    communication: Any,
    log_level: str,
) -> PM2CDsingle:
    """共有状態を使用する PM2CDsingle を生成する。"""
    worker = PM2CDsingle(
        ip_address=ip_address,
        port=port,
        timeout_sec=timeout_sec,
        channel_count=channel_count,
        communication=communication,
        log_level=log_level,
    )

    worker._move_context = context.state

    return worker


class PM2CD:
    """PM2CDsingle の移動状態を worker process 間で共有する sample。"""

    def __init__(
        self,
        ip_address: str,
        port: int = 7777,
        timeout_sec: float = 3.0,
        channel_count: int = 2,
        worker_count: int = 2,
        logger: Any = None,
        log_level: str = "INFO",
    ) -> None:
        if logger is None:
            logger = XLogger(
                log_level=log_level,
                logger_name="PM2CDmultiShared",
            )

        self._logger = logger

        worker_log_level = log_level
        if hasattr(logger, "get_log_level"):
            worker_log_level = logger.get_log_level()

        self._communication_manager = _CommunicationManager()
        self._communication_manager.start()

        self._communication = self._communication_manager.Communication(
            ip_address=ip_address,
            port=port,
            timeout_sec=timeout_sec,
            log_level=worker_log_level,
        )

        worker_factory = partial(
            _create_worker,
            ip_address=ip_address,
            port=port,
            timeout_sec=timeout_sec,
            channel_count=channel_count,
            communication=self._communication,
            log_level=worker_log_level,
        )

        self._executor = ProcessExecutor(
            worker_factory=worker_factory,
            worker_count=worker_count,
            start_method="spawn",
            logger=self._logger,
        )

    def _execute(
        self,
        method: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """PM2CDsingle の method を worker process で実行する。"""
        job_id = self._executor.submit(
            method,
            *args,
            **kwargs,
        )
        return self._executor.wait(job_id)

    def stop(
        self,
        motor: Optional[int] = None,
    ) -> bool:
        """指定モーターを停止する。"""
        return self._execute(
            "stop",
            motor,
        )

    def stop_all(self) -> bool:
        """全モーターを停止する。"""
        return self._execute("stop_all")

    def emergency_stop_all(self) -> bool:
        """全モーターを非常停止する。"""
        return self._execute("emergency_stop_all")

    def remote(self) -> bool:
        """REMOTE に切り替える。"""
        return self._execute("remote")

    def get_status(
        self,
        motor: int,
    ) -> Optional[MotorStatus]:
        """指定モーターの状態を取得する。"""
        return self._execute(
            "get_status",
            motor,
        )

    def get_position(
        self,
        motor: int,
    ) -> Optional[int]:
        """指定モーターの現在位置を取得する。"""
        return self._execute(
            "get_position",
            motor,
        )

    def move(
        self,
        motor: int,
        pulse: int,
        backlash: Union[str, bool, None] = True,
    ) -> bool:
        """指定モーターを絶対位置へ移動する。"""
        return self._execute(
            "move",
            motor,
            pulse,
            backlash=backlash,
        )

    def move_relative(
        self,
        motor: int,
        pulse: int,
        backlash: Union[str, bool, None] = True,
    ) -> bool:
        """指定モーターを相対移動する。"""
        return self._execute(
            "move_relative",
            motor,
            pulse,
            backlash=backlash,
        )

    def wait_for_complete(
        self,
        motor: int,
        timeout_sec: float = 30.0,
        interval_sec: float = 0.1,
        target_position: Optional[int] = None,
    ) -> Optional[MotorStatus]:
        """共有された移動コンテキストを使用して完了を待つ。"""
        return self._execute(
            "wait_for_complete",
            motor,
            timeout_sec=timeout_sec,
            interval_sec=interval_sec,
            target_position=target_position,
        )

    def close(self) -> bool:
        """worker と共有 Communication を終了する。"""
        try:
            self._executor.close()
        finally:
            try:
                result = self._communication.close()
            finally:
                self._communication_manager.shutdown()

        return result
