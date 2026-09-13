"""既存 PM2CD Ctrl を ProcessExecutor で実行する最小 multi 版。"""

from typing import Any, Optional, Union

from pm2cd.core.motor_status import MotorStatus
from pm2cd.pm2cd_ctrl import PM2CD as PM2CDsingle
from process_executor import ProcessExecutor
from x_logger import XLogger


class PM2CD:
    """try_move に必要な API だけを持つ最小 multi 版 PM2CD。

    worker 用 Ctrl は作らない。
    既存の ``pm2cd.pm2cd_ctrl.PM2CD`` を各 worker process 内で
    ProcessExecutor がそのまま生成して使用する。
    """

    def __init__(
        self,
        ip_address: str,
        port: int = 7777,
        timeout_sec: float = 3.0,
        channel_count: int = 2,
        logger: Any = None,
        log_level: str = "INFO",
    ) -> None:
        if logger is None:
            logger = XLogger(
                log_level=log_level,
                logger_name="PM2CDmulti",
            )

        self._logger = logger

        worker_log_level = log_level
        if hasattr(logger, "get_log_level"):
            worker_log_level = logger.get_log_level()

        self._executor = ProcessExecutor(
            worker_class=PM2CDsingle,
            worker_kwargs={
                "ip_address": ip_address,
                "port": port,
                "timeout_sec": timeout_sec,
                "channel_count": channel_count,
                "log_level": worker_log_level,
            },
            worker_count=1,
            start_method="spawn",
            logger=self._logger,
        )

    def _execute(
        self,
        handler: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """既存 PM2CD の public method を worker で実行する。"""
        job_id = self._executor.submit(
            handler,
            *args,
            **kwargs,
        )

        return self._executor.wait(job_id)

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

    def close(self) -> bool:
        """worker 側 PM2CD と ProcessExecutor を終了する。"""
        result = False

        try:
            result = self._execute("close")
        finally:
            self._executor.close()

        return result
