"""ProcessExecutor 0.0.0 の pytest。"""

import time
from typing import Any

import pytest

from process_executor import ProcessExecutor
from process_executor import SharedContext


class FactoryWorker:
    """worker_factory 用の worker-local dummy class。"""

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
        """2値を加算する。"""
        return left + right

    def sleep(
        self,
        delay_sec: float,
    ) -> float:
        """指定時間だけ待機する。"""
        time.sleep(delay_sec)
        return delay_sec

    def set_shared(
        self,
        key: str,
        value: Any,
    ) -> Any:
        """共有 state を lock 下で更新する。"""
        with self._context.lock:
            self._context.state[key] = value

        return value

    def fail(self) -> None:
        """worker 内例外を発生させる。"""
        raise ValueError("pytest failure")


class ExistingCtrl:
    """worker_class I/F の検証に使用する既存 class。"""

    def __init__(
        self,
        base: int,
        label: str = "ctrl",
    ) -> None:
        self._base = base
        self._label = label

    def add(
        self,
        value: int,
    ) -> int:
        """constructor 引数を利用して加算する。"""
        return self._base + value

    def label(self) -> str:
        """constructor keyword argument を返す。"""
        return self._label


def create_factory_worker(
    context: SharedContext,
) -> FactoryWorker:
    """FactoryWorker を生成する。"""
    return FactoryWorker(
        context=context,
    )


def wait_workers_ready(
    executor: ProcessExecutor,
    expected_count: int,
    timeout_sec: float = 5.0,
) -> None:
    """worker metadata が登録されるまで待機する。"""
    deadline = time.monotonic() + timeout_sec

    while True:
        workers = executor.workers()
        ready_count = 0

        for metadata in workers.values():
            if metadata["state"] == "idle":
                ready_count += 1

        if ready_count >= expected_count:
            return

        if time.monotonic() >= deadline:
            pytest.fail("workers were not ready")

        time.sleep(0.01)


@pytest.fixture
def executor() -> ProcessExecutor:
    """worker_factory 版 ProcessExecutor を生成する。"""
    process_executor = ProcessExecutor(
        worker_factory=create_factory_worker,
        worker_count=2,
        start_method="spawn",
    )

    wait_workers_ready(
        process_executor,
        expected_count=2,
    )

    yield process_executor

    process_executor.close()


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
    workers = executor.workers()
    active_workers = []

    for metadata in workers.values():
        if metadata["state"] != "dead":
            active_workers.append(metadata)

    assert len(active_workers) >= 2


def test_unknown_job_returns_none(
    executor: ProcessExecutor,
) -> None:
    """存在しない job の state と metadata が None になることを検証する。"""
    assert executor.state("unknown") is None
    assert executor.job("unknown") is None


def test_submit_after_close_is_rejected() -> None:
    """close() 後に新しい job を投入できないことを検証する。"""
    process_executor = ProcessExecutor(
        worker_factory=create_factory_worker,
        worker_count=1,
        start_method="spawn",
    )

    process_executor.close()

    with pytest.raises(RuntimeError):
        process_executor.submit(
            "add",
            1,
            2,
        )


def test_worker_class_instantiates_existing_class() -> None:
    """既存 class を worker_class として直接生成できることを検証する。"""
    process_executor = ProcessExecutor(
        worker_class=ExistingCtrl,
        worker_args=(10,),
        worker_kwargs={
            "label": "existing",
        },
        worker_count=1,
        start_method="spawn",
    )

    try:
        wait_workers_ready(
            process_executor,
            expected_count=1,
        )

        add_job_id = process_executor.submit(
            "add",
            5,
        )
        label_job_id = process_executor.submit("label")

        assert process_executor.wait(
            add_job_id,
            timeout_sec=2.0,
        ) == 15
        assert process_executor.wait(
            label_job_id,
            timeout_sec=2.0,
        ) == "existing"
    finally:
        process_executor.close()


def test_worker_factory_and_worker_class_are_exclusive() -> None:
    """worker_factory と worker_class の同時指定を拒否することを検証する。"""
    with pytest.raises(ValueError):
        ProcessExecutor(
            worker_factory=create_factory_worker,
            worker_class=ExistingCtrl,
            worker_count=1,
        )


def test_worker_args_require_worker_class() -> None:
    """worker_args が worker_class 専用であることを検証する。"""
    with pytest.raises(ValueError):
        ProcessExecutor(
            worker_factory=create_factory_worker,
            worker_args=(1,),
            worker_count=1,
        )


def test_worker_kwargs_require_worker_class() -> None:
    """worker_kwargs が worker_class 専用であることを検証する。"""
    with pytest.raises(ValueError):
        ProcessExecutor(
            worker_factory=create_factory_worker,
            worker_kwargs={
                "base": 1,
            },
            worker_count=1,
        )
