"""try_move 向けの最小 PM2CD multiprocessing 版。"""

from typing import Optional, Union

from pm2cd.core.communication_tcp import Communication
from pm2cd.core.motor_status import MotorStatus
from pm2cd.pm2cd_device import PM2CDDevice
from process_executor import ProcessExecutor
from process_executor import SharedContext
from x_logger import XLogger


class _PM2CDWorker:
    """worker process 内で PM2CDDevice と Communication を保持する。"""

    def __init__(
        self,
        ip_address: str,
        port: int,
        timeout_sec: float,
        channel_count: int,
        log_level: str,
    ) -> None:
        logger = XLogger(
            log_level=log_level,
            logger_name="PM2CDmulti.worker",
        )

        self._communication = Communication(
            ip_address=ip_address,
            port=port,
            timeout_sec=timeout_sec,
            logger=logger,
            log_level=log_level,
        )
        self._device = PM2CDDevice(
            communication=self._communication,
            channel_count=channel_count,
            logger=logger,
            log_level=log_level,
        )
        self._communication.connect()

    def emergency_stop_all(self) -> bool:
        """全モーターを非常停止する。"""
        return self._device.emergency_stop_all()

    def remote(self) -> bool:
        """REMOTE に切り替える。"""
        return self._device.remote()

    def get_status(
        self,
        motor: int,
    ) -> Optional[MotorStatus]:
        """指定モーターの状態を取得する。"""
        return self._device.status(motor)

    def get_position(
        self,
        motor: int,
    ) -> Optional[int]:
        """指定モーターの現在位置を取得する。"""
        return self._device.position(motor)

    def move(
        self,
        motor: int,
        pulse: int,
        backlash: Union[str, bool, None] = True,
    ) -> bool:
        """指定モーターを絶対位置へ移動する。"""
        return self._device.move(
            motor,
            pulse,
            backlash,
        )

    def close(self) -> bool:
        """TCP 通信を終了する。"""
        return self._communication.close()


class _PM2CDWorkerFactory:
    """spawn 後に worker-local PM2CD 実体を生成する。"""

    def __init__(
        self,
        ip_address: str,
        port: int,
        timeout_sec: float,
        channel_count: int,
        log_level: str,
    ) -> None:
        self._ip_address = ip_address
        self._port = port
        self._timeout_sec = timeout_sec
        self._channel_count = channel_count
        self._log_level = log_level

    def __call__(
        self,
        context: SharedContext,
    ) -> _PM2CDWorker:
        """worker-local instance を生成する。

        Args:
            context: ProcessExecutor の共有 context。

        Returns:
            worker process 内で使用する PM2CD 実体。
        """
        _ = context

        return _PM2CDWorker(
            ip_address=self._ip_address,
            port=self._port,
            timeout_sec=self._timeout_sec,
            channel_count=self._channel_count,
            log_level=self._log_level,
        )


class PM2CD:
    """try_move に必要な API だけを持つ最小 multi 版 PM2CD。

    public I/F は single 版 PM2CD と同じにする。
    完成版 PM2CD / PM2CDsingle は内部で使用しない。
    """

    def __init__(
        self,
        ip_address: str,
        port: int = 7777,
        timeout_sec: float = 3.0,
        channel_count: int = 2,
        logger=None,
        log_level: str = "INFO",
    ) -> None:
        if channel_count != 2:
            raise ValueError(
                f"PM2CDのchannel_countは2です: {channel_count}"
            )

        if logger is None:
            logger = XLogger(
                log_level=log_level,
                logger_name="PM2CDmulti",
            )

        self._logger = logger

        worker_log_level = log_level
        if hasattr(logger, "get_log_level"):
            worker_log_level = logger.get_log_level()

        factory = _PM2CDWorkerFactory(
            ip_address=ip_address,
            port=port,
            timeout_sec=timeout_sec,
            channel_count=channel_count,
            log_level=worker_log_level,
        )

        self._executor = ProcessExecutor(
            worker_factory=factory,
            worker_count=1,
            start_method="spawn",
        )

    def emergency_stop_all(self) -> bool:
        """全モーターを非常停止する。"""
        job_id = self._executor.submit(
            "emergency_stop_all"
        )

        return self._executor.wait(job_id)

    def remote(self) -> bool:
        """REMOTE に切り替える。"""
        job_id = self._executor.submit(
            "remote"
        )

        return self._executor.wait(job_id)

    def get_status(
        self,
        motor: int,
    ) -> Optional[MotorStatus]:
        """指定モーターの状態を取得する。"""
        job_id = self._executor.submit(
            "get_status",
            motor,
        )

        return self._executor.wait(job_id)

    def get_position(
        self,
        motor: int,
    ) -> Optional[int]:
        """指定モーターの現在位置を取得する。"""
        job_id = self._executor.submit(
            "get_position",
            motor,
        )

        return self._executor.wait(job_id)

    def move(
        self,
        motor: int,
        pulse: int,
        backlash: Union[str, bool, None] = True,
    ) -> bool:
        """指定モーターを絶対位置へ移動する。"""
        job_id = self._executor.submit(
            "move",
            motor,
            pulse,
            backlash=backlash,
        )

        return self._executor.wait(job_id)

    def close(self) -> bool:
        """worker 側の通信と ProcessExecutor を終了する。"""
        result = False

        try:
            job_id = self._executor.submit(
                "close"
            )
            result = self._executor.wait(job_id)
        finally:
            self._executor.shutdown()

        return result
