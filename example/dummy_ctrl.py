"""Multiprocess controller built from ProcessExecutor and DummyControl."""

from typing import Any, Dict, Optional

from process_executor import ProcessExecutor, SharedContext

from example.dummy_device import DummyControl


def create_dummy_control(context: SharedContext) -> DummyControl:
    """Create one worker-local DummyControl instance."""
    return DummyControl(context)


class DummyCtrl:
    """Public multiprocess controller for the dummy device."""

    def __init__(
        self,
        worker_count: int = 2,
        start_method: Optional[str] = None,
    ) -> None:
        self._executor = ProcessExecutor(
            worker_factory=create_dummy_control,
            worker_count=worker_count,
            start_method=start_method,
        )

    def set_value(self, value: int) -> str:
        """Submit set_value without waiting."""
        return self._executor.submit("set_value", value)

    def get_value(self) -> str:
        """Submit get_value without waiting."""
        return self._executor.submit("get_value")

    def slow_add(
        self,
        left: int,
        right: int,
        delay_sec: float = 0.2,
    ) -> str:
        """Submit slow_add without waiting."""
        return self._executor.submit(
            "slow_add",
            left,
            right,
            delay_sec,
        )

    def fail(self) -> str:
        """Submit fail without waiting."""
        return self._executor.submit("fail")

    def state(self, job_id: str) -> Optional[str]:
        """Return a job state."""
        return self._executor.state(job_id)

    def job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return one job snapshot."""
        return self._executor.job(job_id)

    def result(self, job_id: str) -> Any:
        """Return a completed job result."""
        return self._executor.result(job_id)

    def wait(
        self,
        job_id: str,
        timeout_sec: Optional[float] = None,
    ) -> Any:
        """Explicitly wait for a job."""
        return self._executor.wait(
            job_id,
            timeout_sec=timeout_sec,
        )

    def workers(self) -> Dict[str, Dict[str, Any]]:
        """Return worker snapshots."""
        return self._executor.workers()

    def close(self) -> None:
        """Close the executor."""
        self._executor.close()

    def __enter__(self) -> "DummyCtrl":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, exc_tb: Any) -> None:
        self.close()
