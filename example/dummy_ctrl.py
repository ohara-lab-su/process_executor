"""DummySingle を ProcessExecutor で multiprocessing 化する example。"""

from typing import Any, Optional

from process_executor import ProcessExecutor
from process_executor import SharedContext
from x_logger import XLogger

from dummy_single import DummySingle


def create_dummy_single(context: SharedContext) -> DummySingle:
    """worker ごとの DummySingle を生成する。"""
    _ = context
    return DummySingle()


class DummyCtrl:
    """DummySingle を multiprocessing 化したダミー制御クラス。"""

    def __init__(
        self,
        worker_count: int = 2,
        start_method: Optional[str] = None,
        logger: Optional[XLogger] = None,
    ) -> None:
        if logger is None:
            self._logger = XLogger(
                log_level="INFO",
                logger_name="DummyCtrl",
            )
        else:
            self._logger = logger

        self._executor = ProcessExecutor(
            worker_factory=create_dummy_single,
            worker_count=worker_count,
            start_method=start_method,
        )

        self._logger.info(
            "DummyCtrl started: worker_count=%s",
            worker_count,
        )

    def query(self, name: str) -> str:
        """query job を投入する。"""
        job_id = self._executor.submit("query", name)
        self._logger.info(
            "query submitted: job_id=%s name=%s",
            job_id,
            name,
        )
        return job_id

    def get(self, value: int, delay_sec: float = 0.5) -> str:
        """get job を投入する。"""
        job_id = self._executor.submit("get", value, delay_sec)
        self._logger.info(
            "get submitted: job_id=%s value=%s",
            job_id,
            value,
        )
        return job_id

    def state(self, job_id: str) -> Optional[str]:
        """job state を取得する。"""
        return self._executor.state(job_id)

    def result(self, job_id: str) -> Any:
        """完了済み job の結果を取得する。"""
        result = self._executor.result(job_id)
        self._logger.info(
            "result received: job_id=%s result=%s",
            job_id,
            result,
        )
        return result

    def wait(
        self,
        job_id: str,
        timeout_sec: Optional[float] = None,
    ) -> Any:
        """job の完了を待つ。"""
        result = self._executor.wait(
            job_id,
            timeout_sec=timeout_sec,
        )
        self._logger.info(
            "job completed: job_id=%s result=%s",
            job_id,
            result,
        )
        return result

    def close(self) -> None:
        """ProcessExecutor を終了する。"""
        self._executor.close()
        self._logger.info("DummyCtrl stopped")

    def __enter__(self) -> "DummyCtrl":
        """with 文で使用する DummyCtrl 自身を返す。"""
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        exc_tb: Any,
    ) -> None:
        """with 文終了時に終了処理を行う。"""
        self.close()
