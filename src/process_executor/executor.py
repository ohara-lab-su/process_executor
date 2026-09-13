"""Generic multiprocessing job executor."""

import multiprocessing
import time
import traceback
import uuid
from typing import Any, Callable, Dict, Optional

from .shared_context import SharedContext


_STOP = "__PROCESS_EXECUTOR_STOP__"


def _now() -> float:
    """Return the current UNIX timestamp."""
    return time.time()


def _snapshot(value: Any) -> Dict[str, Any]:
    """Convert a manager proxy value into a normal dictionary."""
    if value is None:
        return {}

    return dict(value)


def _worker_main(
    worker_id: str,
    worker_factory: Callable[[SharedContext], Any],
    job_queue: Any,
    jobs: Any,
    workers: Any,
    state: Any,
    lock: Any,
) -> None:
    """Run the generic worker server loop."""
    context = SharedContext(
        jobs=jobs,
        workers=workers,
        state=state,
        lock=lock,
    )
    instance = worker_factory(context)

    workers[worker_id] = {
        "worker_id": worker_id,
        "pid": multiprocessing.current_process().pid,
        "state": "idle",
        "current_job_id": None,
        "current_handler": None,
    }

    while True:
        job = job_queue.get()

        if job == _STOP:
            break

        job_id = job["job_id"]
        handler_name = job["handler"]
        args = job["args"]
        kwargs = job["kwargs"]

        workers[worker_id] = {
            "worker_id": worker_id,
            "pid": multiprocessing.current_process().pid,
            "state": "running",
            "current_job_id": job_id,
            "current_handler": handler_name,
        }

        metadata = _snapshot(jobs.get(job_id))
        metadata["state"] = "running"
        metadata["worker_id"] = worker_id
        metadata["started_at"] = _now()
        jobs[job_id] = metadata

        try:
            handler = getattr(instance, handler_name)
            result = handler(*args, **kwargs)

            metadata = _snapshot(jobs.get(job_id))
            metadata["state"] = "succeeded"
            metadata["result"] = result
            metadata["error"] = None
            metadata["finished_at"] = _now()
            jobs[job_id] = metadata
        except Exception as exc:
            metadata = _snapshot(jobs.get(job_id))
            metadata["state"] = "failed"
            metadata["result"] = None
            metadata["error"] = {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            metadata["finished_at"] = _now()
            jobs[job_id] = metadata

        workers[worker_id] = {
            "worker_id": worker_id,
            "pid": multiprocessing.current_process().pid,
            "state": "idle",
            "current_job_id": None,
            "current_handler": None,
        }

    workers[worker_id] = {
        "worker_id": worker_id,
        "pid": multiprocessing.current_process().pid,
        "state": "stopped",
        "current_job_id": None,
        "current_handler": None,
    }


class ProcessExecutor:
    """Generic multiprocessing execution support.

    The executor does not know device protocols, transports, or controller
    semantics. Each worker owns one instance returned by ``worker_factory``.

    Args:
        worker_factory: Callable that receives ``SharedContext`` and returns
            one worker-local controller instance.
        worker_count: Number of worker processes kept ready.
        start_method: Multiprocessing start method. If omitted, Python's
            platform default is used.
    """

    def __init__(
        self,
        worker_factory: Callable[[SharedContext], Any],
        worker_count: int = 4,
        start_method: Optional[str] = None,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be 1 or greater")

        self._worker_factory = worker_factory
        self._worker_count = worker_count
        self._mp_context = multiprocessing.get_context(start_method)

        self._manager = self._mp_context.Manager()
        self._jobs = self._manager.dict()
        self._workers = self._manager.dict()
        self._state = self._manager.dict()
        self._lock = self._manager.RLock()
        self._job_queue = self._mp_context.Queue()

        self._processes: Dict[str, multiprocessing.Process] = {}
        self._closed = False

        self._start_workers()

    @property
    def shared_context(self) -> SharedContext:
        """Return the shared context for the parent controller."""
        return SharedContext(
            jobs=self._jobs,
            workers=self._workers,
            state=self._state,
            lock=self._lock,
        )

    def submit(self, handler: str, *args: Any, **kwargs: Any) -> str:
        """Submit a handler job and immediately return its job ID."""
        if self._closed:
            raise RuntimeError("ProcessExecutor is already closed")

        if not isinstance(handler, str):
            raise TypeError("handler must be str")

        if handler == "":
            raise ValueError("handler must not be empty")

        self._replenish_workers()

        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {
            "job_id": job_id,
            "handler": handler,
            "args": args,
            "kwargs": kwargs,
            "state": "queued",
            "worker_id": None,
            "result": None,
            "error": None,
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
        }

        self._job_queue.put(
            {
                "job_id": job_id,
                "handler": handler,
                "args": args,
                "kwargs": kwargs,
            }
        )

        return job_id

    def state(self, job_id: str) -> Optional[str]:
        """Return a job state."""
        metadata = self.job(job_id)

        if metadata is None:
            return None

        return metadata.get("state")

    def job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return a snapshot of one job."""
        metadata = self._jobs.get(job_id)

        if metadata is None:
            return None

        return dict(metadata)

    def jobs(self) -> Dict[str, Dict[str, Any]]:
        """Return snapshots of all jobs."""
        result: Dict[str, Dict[str, Any]] = {}

        for job_id, metadata in self._jobs.items():
            result[job_id] = dict(metadata)

        return result

    def worker(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """Return a snapshot of one worker."""
        self._replenish_workers()
        metadata = self._workers.get(worker_id)

        if metadata is None:
            return None

        return dict(metadata)

    def workers(self) -> Dict[str, Dict[str, Any]]:
        """Return snapshots of all workers."""
        self._replenish_workers()
        result: Dict[str, Dict[str, Any]] = {}

        for worker_id, metadata in self._workers.items():
            result[worker_id] = dict(metadata)

        return result

    def result(self, job_id: str) -> Any:
        """Return the result of a completed job."""
        metadata = self.job(job_id)

        if metadata is None:
            raise KeyError(job_id)

        job_state = metadata["state"]

        if job_state == "failed":
            raise RuntimeError(str(metadata.get("error")))

        if job_state != "succeeded":
            raise RuntimeError("job is not completed")

        return metadata.get("result")

    def wait(
        self,
        job_id: str,
        timeout_sec: Optional[float] = None,
        interval_sec: float = 0.01,
    ) -> Any:
        """Explicitly wait for one job and return its result."""
        started_at = time.monotonic()

        while True:
            metadata = self.job(job_id)

            if metadata is None:
                raise KeyError(job_id)

            job_state = metadata["state"]

            if job_state == "succeeded":
                return metadata.get("result")

            if job_state == "failed":
                raise RuntimeError(str(metadata.get("error")))

            if timeout_sec is not None:
                elapsed = time.monotonic() - started_at

                if elapsed >= timeout_sec:
                    raise TimeoutError("waiting for job timed out")

            time.sleep(interval_sec)

    def close(self) -> None:
        """Stop workers and shut down shared resources."""
        if self._closed:
            return

        self._closed = True
        alive_processes = []

        for process in self._processes.values():
            if process.is_alive():
                alive_processes.append(process)

        for _process in alive_processes:
            self._job_queue.put(_STOP)

        for process in alive_processes:
            process.join()

        self._job_queue.close()
        self._manager.shutdown()

    def __enter__(self) -> "ProcessExecutor":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, exc_tb: Any) -> None:
        self.close()

    def _start_workers(self) -> None:
        """Start the requested number of workers."""
        while len(self._processes) < self._worker_count:
            self._start_worker()

    def _start_worker(self) -> None:
        """Start one generic worker."""
        worker_id = uuid.uuid4().hex
        process = self._mp_context.Process(
            target=_worker_main,
            args=(
                worker_id,
                self._worker_factory,
                self._job_queue,
                self._jobs,
                self._workers,
                self._state,
                self._lock,
            ),
        )
        process.daemon = True
        process.start()

        self._processes[worker_id] = process

    def _replenish_workers(self) -> None:
        """Remove dead workers and restore the configured worker count."""
        dead_worker_ids = []

        for worker_id, process in self._processes.items():
            if not process.is_alive():
                dead_worker_ids.append(worker_id)

        for worker_id in dead_worker_ids:
            process = self._processes.pop(worker_id)
            process.join()

            metadata = self._workers.get(worker_id)

            if metadata is not None:
                snapshot = dict(metadata)
                snapshot["state"] = "dead"
                self._workers[worker_id] = snapshot

        while len(self._processes) < self._worker_count:
            self._start_worker()
