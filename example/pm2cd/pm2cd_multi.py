"""既存 PM2CD Ctrl を ProcessExecutor で実行する最小 multi 版。"""

import time
from dataclasses import fields
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
        self._move_context = {}

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

    def _to_motor_status(
        self,
        status: Any,
    ) -> Optional[MotorStatus]:
        """worker の戻り値を公開用 MotorStatus に変換する。"""
        if status is None:
            return None

        values = {
            field.name: getattr(status, field.name)
            for field in fields(MotorStatus)
        }

        return MotorStatus(**values)

    def _backlash_enabled(
        self,
        backlash: Union[str, bool, None],
    ) -> bool:
        """バックラッシュ補正を使用する指定か判定する。"""
        if backlash is True:
            return True

        if backlash is False:
            return False

        if backlash is None:
            return False

        if not isinstance(backlash, str):
            return False

        return backlash.strip().upper() in ("B", "S")

    def _motion_active(
        self,
        status: MotorStatus,
    ) -> bool:
        """STS? の情報から軸が動作状態か判定する。"""
        if status.is_busy:
            return True

        if status.is_driving:
            return True

        return status.direction != "S"

    def stop(
        self,
        motor: int,
    ) -> bool:
        """指定モーターを停止する。"""
        return self._execute(
            "stop",
            motor,
        )

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
        status = self._execute(
            "get_status",
            motor,
        )

        return self._to_motor_status(status)

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
        result = self._execute(
            "move",
            motor,
            pulse,
            backlash=backlash,
        )

        if result:
            self._move_context[motor] = {
                "target_position": pulse,
                "backlash_enabled": self._backlash_enabled(backlash),
            }

        return result

    def wait_for_complete(
        self,
        motor: int,
        timeout_sec: float = 30.0,
        interval_sec: float = 0.1,
        target_position: Optional[int] = None,
    ) -> Optional[MotorStatus]:
        """指定モーターの位置決め移動または停止完了を待つ。

        待機中はポーリングごとに現在位置を表示する。
        """
        deadline = time.monotonic() + timeout_sec

        context = self._move_context.get(motor)
        backlash_enabled = False
        if context is not None:
            if target_position is None:
                target_position = context.get("target_position")
            backlash_enabled = bool(context.get("backlash_enabled"))

        plain_inactive_count = 0
        target_inactive_seen = False
        correction_progress_seen = False

        while True:
            status = self.get_status(motor)
            if status is None:
                return None

            print(f"[POSITION] motor {motor}: {status.position}")
            active = self._motion_active(status)

            if target_position is None:
                if active:
                    plain_inactive_count = 0
                else:
                    plain_inactive_count += 1

                if plain_inactive_count >= 2:
                    return status

            elif not backlash_enabled:
                if not active and status.position == target_position:
                    self._move_context.pop(motor, None)
                    return status

            else:
                at_target = status.position == target_position

                if target_inactive_seen:
                    if active or not at_target:
                        correction_progress_seen = True

                if not active and at_target:
                    if target_inactive_seen or correction_progress_seen:
                        self._move_context.pop(motor, None)
                        return status

                    target_inactive_seen = True

            if time.monotonic() >= deadline:
                if target_position is None:
                    self._logger.error(
                        f"wait_for_complete(): motor {motor} が停止しませんでした"
                    )
                else:
                    self._logger.error(
                        "wait_for_complete(): motor {} が目標位置 {} で完了しませんでした".format(
                            motor,
                            target_position,
                        )
                    )
                return None

            time.sleep(interval_sec)

    def close(self) -> bool:
        """worker 側 PM2CD と ProcessExecutor を終了する。"""
        result = False

        try:
            result = self._execute("close")
        finally:
            self._executor.close()

        return result
