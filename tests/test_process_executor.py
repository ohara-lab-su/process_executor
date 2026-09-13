"""ProcessExecutor 本体を検証する pytest。"""

import time
from typing import Any

import pytest

from process_executor import ProcessExecutor
from process_executor import SharedContext


class DummyWorker:
    """pytest で使用する worker-local dummy class。

    Args:
        context: ProcessExecutor が管理する共有 context。
    """

    def __init__(
        self,
        context: SharedContext,
    ) -> None:
        self._context = context

    def add(
        self,
        left: int,
        right: int,
    ) -> int:
        """2値を加算する。

        Args:
            left: 左辺。
            right: 右辺。

        Returns:
            加算結果。
        """
        return left + right

    def sleep(
        self,
        delay_sec: float,
    ) -> float:
        """指定時間だけ待機する。

        Args:
            delay_sec: 待機時間。

        Returns:
            指定された待機時間。
        """
        time.sleep(delay_sec)

        return delay_sec

    def set_shared(
        self,
        key: str,
        value: Any,
    ) -> Any:
        """共有 state を lock 下で更新する。

        Args:
            key: state key。
            value: state value。

        Returns:
            設定した value。
        """
        with self._context.lock:
            self._context.state[key] = value

        return value

    def fail(self) -> None:
        """worker 内例外を発生させる。

        Raises:
            ValueError: 常に発生する pytest 用 error。
        """
        raise ValueError("pytest failure")


def create_dummy_worker(
    context: SharedContext,
) -> DummyWorker:
    """pytest 用 DummyWorker を生成する。

    Args:
        context: ProcessExecutor が管理する共有 context。

    Returns:
        DummyWorker。
    """
    return DummyWorker(
        context=context,
    )


@pytest.fixture
def executor() -> ProcessExecutor:
    """各 test 用 ProcessExecutor を生成する。

    Yields:
        worker_count=2 の ProcessExecutor。
    """
    process_executor = ProcessExecutor(
        worker_factory=create_dummy_worker,
        worker_count=2,
        start_method="spawn",
        log_level="WARNING",
        logger_name="pytest.ProcessExecutor",
    )

    yield process_executor

    process_executor.shutdown()


def test_submit_is_nonblocking(
    executor: ProcessExecutor,
) -> None:
    """submit() が job 完了を待たずに返ることを検証する。"""
    started_at = time.monotonic()

    job_id = executor.submit(
        "sleep",
        0.30,
    )

    elapsed = time.monotonic() - started_at

    assert elapsed < 0.20

    result = executor.wait(
        job_id,
        timeout_sec=2.0,
    )

    assert result == pytest.approx(0.30)
    assert executor.state(job_id) == "succeeded"


def test_two_workers_execute_jobs_in_parallel(
    executor: ProcessExecutor,
) -> None:
    """2 worker が独立 job を並列実行することを検証する。"""
    started_at = time.monotonic()

    first_job_id = executor.submit(
        "sleep",
        0.40,
    )
    second_job_id = executor.submit(
        "sleep",
        0.40,
    )

    executor.wait(
        first_job_id,
        timeout_sec=2.0,
    )
    executor.wait(
        second_job_id,
        timeout_sec=2.0,
    )

    elapsed = time.monotonic() - started_at

    assert elapsed < 0.75


def test_shared_state_is_visible_from_parent(
    executor: ProcessExecutor,
) -> None:
    """worker が変更した共有 state を親 process から参照できることを検証する。"""
    job_id = executor.submit(
        "set_shared",
        "value",
        123,
    )

    result = executor.wait(
        job_id,
        timeout_sec=2.0,
    )

    assert result == 123
    assert executor.shared_context.state["value"] == 123


def test_result_does_not_wait(
    executor: ProcessExecutor,
) -> None:
    """result() が未完了 job を暗黙に待たないことを検証する。"""
    job_id = executor.submit(
        "sleep",
        0.30,
    )

    with pytest.raises(RuntimeError):
        executor.result(job_id)

    executor.wait(
        job_id,
        timeout_sec=2.0,
    )


def test_worker_exception_becomes_failed_job(
    executor: ProcessExecutor,
) -> None:
    """worker 内例外が failed job として保存されることを検証する。"""
    job_id = executor.submit("fail")

    with pytest.raises(RuntimeError):
        executor.wait(
            job_id,
            timeout_sec=2.0,
        )

    job_metadata = executor.job(job_id)

    assert job_metadata is not None
    assert job_metadata["state"] == "failed"
    assert job_metadata["error"]["type"] == "ValueError"
    assert job_metadata["error"]["message"] == "pytest failure"


@pytest.mark.parametrize(
    (
        "left",
        "right",
        "expected",
    ),
    [
        (1, 2, 3),
        (-5, 5, 0),
        (100, 200, 300),
    ],
)
def test_arguments_and_result_cross_process_boundary(
    executor: ProcessExecutor,
    left: int,
    right: int,
    expected: int,
) -> None:
    """引数と戻り値が process 境界を越えることを検証する。"""
    job_id = executor.submit(
        "add",
        left,
        right,
    )

    result = executor.wait(
        job_id,
        timeout_sec=2.0,
    )

    assert result == expected


def test_worker_count_is_started(
    executor: ProcessExecutor,
) -> None:
    """設定数の worker が起動されることを検証する。"""
    deadline = time.monotonic() + 2.0
    active_workers = []

    while True:
        worker_metadata = executor.workers()
        active_workers = []

        for metadata in worker_metadata.values():
            if metadata["state"] != "dead":
                active_workers.append(metadata)

        if len(active_workers) >= 2:
            break

        if time.monotonic() >= deadline:
            pytest.fail("workers were not started")

        time.sleep(0.01)

    assert len(active_workers) >= 2


def test_unknown_job_returns_none(
    executor: ProcessExecutor,
) -> None:
    """存在しない job の state と metadata が None になることを検証する。"""
    assert executor.state("unknown") is None
    assert executor.job("unknown") is None


def test_submit_after_shutdown_is_rejected() -> None:
    """shutdown 後に新しい job を投入できないことを検証する。"""
    process_executor = ProcessExecutor(
        worker_factory=create_dummy_worker,
        worker_count=1,
        start_method="spawn",
        log_level="WARNING",
        logger_name="pytest.shutdown",
    )

    process_executor.shutdown()

    with pytest.raises(RuntimeError):
        process_executor.submit(
            "add",
            1,
            2,
        )
