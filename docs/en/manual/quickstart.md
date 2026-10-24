# Quick Start

This page provides a minimal introduction to `process-executor`. Its content is based on the supplied implementations of `executor.py` and `shared_context.py`.

## Basic usage

Pass an ordinary Python class as `worker_class`.

```python
from process_executor import ProcessExecutor


class Calculator:
    def add(self, a: int, b: int) -> int:
        return a + b


def main() -> None:
    with ProcessExecutor(
        worker_class=Calculator,
        worker_count=2,
        start_method="spawn",
    ) as executor:
        job_id = executor.submit("add", 10, 20)
        result = executor.wait(job_id, timeout_sec=5.0)
        print(result)


if __name__ == "__main__":
    main()
```

The `Calculator` class itself needs no multiprocessing-specific code. A `Calculator` instance is created in the worker process, and the `"add"` handler specified by `submit("add", 10, 20)` is resolved to a method through `getattr(instance, handler_name)`.

## submit and wait

`submit()` returns a job ID, not the result of the operation.

```python
job_id = executor.submit("add", 10, 20)
```

A job is initially registered as `queued`, becomes `running` when a worker picks it up, `succeeded` on a normal return, and `failed` when a regular Python `Exception` occurs.

To wait synchronously for the result:

```python
result = executor.wait(job_id)
```

To specify a timeout:

```python
result = executor.wait(job_id, timeout_sec=10.0)
```

The timeout in `wait()` is a caller-side waiting timeout, not an execution timeout that stops the handler.

## Job state and results

```python
state = executor.state(job_id)
metadata = executor.job(job_id)
all_jobs = executor.jobs()
```

The dictionaries returned by `job()` and `jobs()` are snapshots taken at retrieval time.

To read an already completed result without waiting:

```python
result = executor.result(job_id)
```

`result()` does not wait. It raises `RuntimeError` if the job has not finished.

## Inspecting workers

```python
for worker_id, metadata in executor.workers().items():
    print(
        worker_id,
        metadata["pid"],
        metadata["state"],
        metadata["current_job_id"],
        metadata["current_handler"],
    )
```

To retrieve a single worker, use `executor.worker(worker_id)`.

## Passing constructor arguments

```python
class Controller:
    def __init__(self, host: str, port: int, timeout: float = 1.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def query(self, command: str):
        ...


with ProcessExecutor(
    worker_class=Controller,
    worker_args=("192.168.0.10", 5000),
    worker_kwargs={"timeout": 2.0},
    worker_count=2,
) as executor:
    job_id = executor.submit("query", "STATUS")
    result = executor.wait(job_id)
```

Each worker process creates its own `Controller` instance using the same constructor arguments.

## Using SharedContext

Use `worker_factory` when workers need shared state or a lock.

```python
class CounterController:
    def __init__(self, state, lock):
        self._state = state
        self._lock = lock

    def increment(self):
        with self._lock:
            value = self._state.get("count", 0) + 1
            self._state["count"] = value
            return value


def create_controller(context):
    return CounterController(
        state=context.state,
        lock=context.lock,
    )


with ProcessExecutor(
    worker_factory=create_controller,
    worker_count=4,
) as executor:
    job_ids = [executor.submit("increment") for _ in range(10)]
    results = [executor.wait(job_id) for job_id in job_ids]
```

The `SharedContext` passed to `worker_factory(context)` contains:

```text
context.jobs
context.workers
context.state
context.lock
```

Use `worker_class` to instantiate an existing class directly, or `worker_factory` to construct an instance with `SharedContext`. These two arguments cannot be specified together.

## Shutdown

Normally, use a context manager.

```python
with ProcessExecutor(
    worker_class=Controller,
    worker_count=2,
) as executor:
    job_id = executor.submit("run")
    result = executor.wait(job_id)
```

For explicit lifecycle management:

```python
executor = ProcessExecutor(worker_class=Controller)

try:
    ...
finally:
    executor.close()
```

## Limitations to understand first

The current source does not implement job cancellation, priorities, retries, persistent queues, or remote workers.

`wait(timeout_sec=...)` does not stop a running handler.

If a worker process dies from a process-level failure while executing a handler, the worker pool can replace missing workers, but the affected `running` job is not automatically marked `failed`.

For details on the execution model, failure model, `SharedContext`, worker lifecycle, and resource ownership, consult the main detailed guide. API classes, methods, signatures, and docstrings appear in the API Reference generated from source at build time.
