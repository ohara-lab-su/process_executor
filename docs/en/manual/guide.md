# process-executor


`process-executor` is an execution layer that runs ordinary Python class method calls as jobs in multiprocessing worker processes.

This Pages document was written from the supplied implementations of `executor.py` and `shared_context.py` alone. The existing README and CHANGELOG were not used as sources for this description.

The purpose of this library is not to turn an existing class into a class specifically designed for multiprocessing. Instead, it creates one instance of an ordinary class inside each worker process, resolves the handler name received from a Queue to a method using `getattr()`, and executes that method as a job.

```text
caller / application
        |
        | submit("move", ...)
        v
+-------------------------+
| ProcessExecutor         |
|                         |
| jobs / workers / state  |
| multiprocessing.Queue   |
+------------+------------+
             |
       +-----+-----+
       |           |
       v           v
   Worker A     Worker B
       |           |
       v           v
  Ctrl instance  Ctrl instance
       |           |
       v           v
   handler(...) handler(...)
```

The responsibilities are therefore separated as follows.

```text
Existing class
    └─ Actual processing, device control, computation, protocols, domain logic

ProcessExecutor
    └─ Processes, Queues, job IDs, status, results, exceptions, worker lifecycle

SharedContext
    └─ Metadata / state / locks explicitly shared between processes
```

---

# Quick Start

## Use an existing class directly as a worker

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

`Calculator` contains neither `multiprocessing.Process` nor `Queue`.

Conceptually, the worker process performs the following operations.

```python
instance = Calculator()

job = job_queue.get()
handler = getattr(instance, job["handler"])
result = handler(*job["args"], **job["kwargs"])
```

A worker-local instance is not created for just one job. It is created once when the worker process starts and is reused for multiple jobs retrieved by the same worker.

---

# 1. What ProcessExecutor abstracts

Running a Python class with multiprocessing requires substantial infrastructure beyond the actual processing logic.

For example, suppose the processing logic consists only of the following.

```python
class Controller:
    def move(self, position):
        ...
```

If multiprocessing support is implemented directly inside `Controller`, that class must also handle the following responsibilities.

```text
Controller
├─ move()
├─ Process creation
├─ Queue creation
├─ worker loop
├─ job ID
├─ job state
├─ result
├─ exception transport
├─ worker state
├─ worker restart
└─ shutdown
```

This mixes the meaning of `move()` with process management in a single class.

ProcessExecutor extracts the latter responsibilities into generic execution infrastructure.

```text
Controller
└─ move()

ProcessExecutor
├─ Process
├─ Queue
├─ Manager
├─ jobs
├─ workers
├─ state
├─ lock
├─ submit
├─ result
├─ wait
└─ close
```

This separation keeps existing classes usable without multiprocessing, while allowing multiprocess execution to be composed at a higher level only where needed.

---

# 2. Constructor overview

`ProcessExecutor.__init__()` first determines how workers will be created, then constructs the multiprocessing infrastructure.

The implementation follows this sequence.

```text
1. Validate worker_count
2. Validate worker_factory / worker_class arguments
3. Determine the worker factory
4. Obtain the multiprocessing context
5. Create the Manager
6. Create the jobs dictionary
7. Create the workers dictionary
8. Create the state dictionary
9. Create the RLock
10. Create the Queue
11. Create the parent-process Process table
12. Set closed=False
13. Start workers until worker_count is reached
```

The multiprocessing context is obtained using:

```python
multiprocessing.get_context(start_method)
```

The Manager, Queue, and Process objects are therefore created from the same multiprocessing context.

When `start_method=None`, Python uses the default selected for that platform. ProcessExecutor does not itself contain code that forces a particular start method on each platform.

---

# 3. worker_count

`worker_count` must be at least 1.

```python
if worker_count < 1:
    raise ValueError("worker_count must be 1 or greater")
```

The configured value is the number of worker processes that the executor aims to maintain throughout its lifetime.

The constructor initially starts that many workers.

Later, if a dead worker is detected, replacement workers are started until the pool returns to the same `worker_count`.

The current source does not provide a public API for changing `worker_count` after construction.

This is therefore not a dynamically autoscaling pool, but a worker pool that maintains the fixed target size specified in the constructor.

---

# 4. worker_class and worker_factory

There are two ways to create worker-local instances.

## worker_class

Use this when an existing class can be used directly.

```python
executor = ProcessExecutor(
    worker_class=Controller,
    worker_args=("COM3",),
    worker_kwargs={"timeout": 1.0},
)
```

Internally, a `_WorkerClassFactory` is created.

This factory is called inside the worker process and performs the following operation.

```python
Controller(
    *worker_args,
    **worker_kwargs,
)
```

`SharedContext` is passed to `_WorkerClassFactory.__call__()`, but is not passed to the existing controller class.

Consequently, an existing class does not need a constructor signature specific to ProcessExecutor.

## worker_factory

Use this when creating a worker-local instance requires SharedContext.

```python
def create_controller(context):
    return Controller(
        shared_state=context.state,
        lock=context.lock,
    )


executor = ProcessExecutor(
    worker_factory=create_controller,
)
```

Inside the worker process, the factory is invoked as follows.

```python
instance = worker_factory(context)
```

`worker_factory` and `worker_class` cannot be specified together.

When `worker_factory` is used, `worker_args` and `worker_kwargs` cannot be specified either.

This distinction separates two usage patterns.

```text
Use an existing class without modification
        -> worker_class

Create an instance by injecting process-shared dependencies
        -> worker_factory
```

---

# 5. Instance held by a worker process

At the beginning of the worker main function, a `SharedContext` is created.

```python
context = SharedContext(
    jobs=jobs,
    workers=workers,
    state=state,
    lock=lock,
)
```

Next, the following is executed exactly once:

```python
instance = worker_factory(context)
```

The worker then enters the `while True` job loop.

Consequently, the lifetime of a worker-local instance is:

```text
worker process start
        |
        v
instance creation
        |
        v
job 1
        |
        v
job 2
        |
        v
job 3
        |
        v
STOP
        |
        v
worker process end
```

The instance is not recreated for each job.

This means that a stateful controller can be retained within a worker.

However, the instances belonging to worker A and worker B reside in different processes.

```text
Worker A                     Worker B
---------                    ---------
Controller A                 Controller B
self.x = 10                  self.x = 20
```

Ordinary instance attributes are not shared.

---

# 6. SharedContext

`SharedContext` is a very small class.

It holds only four objects:

```text
jobs
workers
state
lock
```

Its constructor stores the supplied objects directly as instance attributes.

`SharedContext` does not itself implement IPC.

The actual interprocess sharing is provided by proxy objects created by `ProcessExecutor` through a Manager.

```text
SharedContext
    |
    +-- jobs    -> Manager.dict()
    +-- workers -> Manager.dict()
    +-- state   -> Manager.dict()
    `-- lock    -> Manager.RLock()
```

`SharedContext` is an interface that groups these objects together and passes them to the worker factory.

---

# 7. jobs, workers, and state are not the same dictionary

All three are Manager dictionaries, but their responsibilities differ.

`jobs` is used by `ProcessExecutor` to manage job execution metadata.

`workers` is used by `ProcessExecutor` to manage worker execution metadata.

`state` is a shared mapping whose schema is not defined by the executor core.

```text
jobs
    semantics defined by the executor

workers
    semantics defined by the executor

state
    semantics defined by the controller / application
```

This separation is important.

There is no need to mix controller-specific state into job metadata.

Conversely, there is no need to manage `ProcessExecutor` worker state as generic application state keys.

---

# 8. lock

The lock is a Manager `RLock`.

```python
self._lock = self._manager.RLock()
```

This lock is passed to workers through `SharedContext`.

However, `ProcessExecutor` does not automatically acquire the lock before or after executing a handler.

In other words, there is no generic lock around:

```python
handler = getattr(instance, handler_name)
result = handler(*args, **kwargs)
```

Therefore, when `worker_count > 1`, handlers in different workers can execute concurrently.

If shared state or a single resource requires mutual exclusion, the controller can be designed to use `SharedContext.lock`.

This design lets the application define only the critical sections that need protection, rather than unconditionally serializing every handler.

---

# 9. submit

`submit()` does not execute a handler call directly.

It first checks the closed flag.

If the executor is closed, it raises:

```python
RuntimeError("ProcessExecutor is already closed")
```

The handler must be a string.

A non-string handler raises `TypeError`, and an empty string raises `ValueError`.

Next, `_replenish_workers()` is called.

A job ID is then generated as a UUID4 hexadecimal string.

```python
job_id = uuid.uuid4().hex
```

Job metadata is created with the `queued` state.

```text
job_id
handler
args
kwargs
state = queued
worker_id = None
result = None
error = None
created_at
started_at = None
finished_at = None
```

An execution payload is then placed on the Queue.

The Queue payload does not contain all metadata; it contains only:

```text
job_id
handler
args
kwargs
```

Finally, `submit()` returns the job ID.

Thus, the return value of `submit()` is not the handler result.

---

# 10. Meaning of a job ID

A job ID is a UUID4 hexadecimal string identifying a single submission.

Submitting the same handler with the same arguments twice produces two different job IDs.

```text
submit("move", 100) -> job A
submit("move", 100) -> job B
```

The job ID is used to call:

```python
executor.state(job_id)
executor.job(job_id)
executor.result(job_id)
executor.wait(job_id)
```

A job ID is distinct from a worker ID.

Because a job is created before it is assigned to a worker, its `worker_id` is `None` while the job is queued.

---

# 11. queued

`queued` is the state immediately after `submit` creates the metadata.

At this stage:

```text
worker_id = None
started_at = None
finished_at = None
result = None
error = None
```

The Queue contains the job payload, but the worker that will retrieve it has not yet been determined.

ProcessExecutor does not provide an API that allows the caller to specify a worker ID when submitting a job.

Consequently, job routing is delegated to the generic shared Queue.

---

# 12. worker startup

`_start_worker()` creates a new worker ID using UUID4 hex.

It then creates a multiprocessing Process.

The target is `_worker_main`.

The following values are passed:

```text
worker_id
worker_factory
job_queue
jobs
workers
state
lock
```

The Process is configured as a daemon.

```python
process.daemon = True
```

It is then started with `start()`.

Finally, it is stored in the parent process's `_processes` dictionary as:

```text
worker_id -> Process object
```

---

# 13. Difference between the parent's _processes and the shared workers

`_processes` is an ordinary Python dictionary.

The parent process uses it to hold `Process` objects.

```text
_processes
worker_id -> multiprocessing.Process
```

By contrast, `workers` is a Manager dictionary.

It contains:

```text
worker_id
pid
state
current_job_id
current_handler
```

In other words:

```text
_processes
    For controlling the process lifecycle

workers
    For observing worker status
```

Distinguishing these two structures is important for understanding the internal architecture.

---

# 14. worker readiness

Worker metadata is not registered by `_start_worker()` in the parent process.

It is registered only after the worker process actually starts, creates a SharedContext, and constructs an instance through the worker factory:

```python
workers[worker_id] = {
    ...
    "state": "idle",
}
```

The sequence is therefore:

```text
Process.start()
        |
        | A time gap may occur
        v
factory(context)
        |
        v
workers[worker_id] = idle
```

The constructor has no barrier that waits until all workers reach `idle`.

Therefore, the source does not guarantee that initialization of every worker-local controller has finished at the instant the constructor returns.

---

# 15. idle

After creation of the worker-local instance completes, the worker metadata becomes `idle`.

```text
worker_id
pid
state = idle
current_job_id = None
current_handler = None
```

An idle worker waits for the next job using `Queue.get()`.

The Queue is shared by all workers.

Therefore, when multiple workers are idle, whichever worker retrieves a job from the Queue executes that job.

There is no affinity to a particular worker.

---

# 16. A worker retrieves a job

A worker blocks on:

```python
job = job_queue.get()
```

When it receives a normal job, it extracts the following from the payload:

```text
job_id
handler
args
kwargs
```

It then updates the worker metadata to `running`.

```text
state = running
current_job_id = job_id
current_handler = handler_name
```

The PID and worker ID are also retained in the metadata.

At this point, it is possible to observe which worker is executing which handler for which job.

---

# 17. Transitioning a job to running

After updating the worker metadata, the worker takes a snapshot of the target job's metadata from the jobs dictionary.

It then updates the following fields:

```text
state = running
worker_id = current worker
started_at = current UNIX time
```

Thus, the transition from `queued` to `running` occurs when a worker actually retrieves the job.

`started_at` is not set at submission time.

The difference between `created_at` and `started_at` includes the time the job spent waiting in the Queue.

---

# 18. handler dispatch

The execution target is obtained with:

```python
handler = getattr(instance, handler_name)
```

It is then invoked as:

```python
result = handler(*args, **kwargs)
```

ProcessExecutor does not preregister a signature for each handler.

Python determines at runtime whether an attribute corresponding to the handler name exists and whether the arguments match its signature.

If the handler name does not exist, `getattr()` raises an exception.

If the arguments are invalid, the method call raises an exception.

Both cases enter the worker's `except Exception` block and are recorded as job failures.

---

# 19. Handler names and the security boundary

`submit()` validates only that the handler name:

```text
is a str
is not an empty string
```

There is no code that prohibits methods whose names begin with `_`, and there is no allowlist.

In other words, the dispatch mechanism in the source targets any attribute retrievable by:

```python
getattr(instance, handler_name)
```

If a handler name is accepted directly from an external HTTP request or similar input, responsibility for restricting the handlers that may be exposed belongs to the upper layer.

ProcessExecutor itself should not be regarded as a remote procedure call security boundary.

---

# 20. succeeded

When a handler returns normally, its result is saved in the job metadata.

```text
state = succeeded
result = handler return value
error = None
finished_at = current UNIX time
```

The meaning of `succeeded` is important here.

ProcessExecutor only checks that the Python handler call returned without raising an exception.

For example, even if a handler returns:

```python
return False
```

this is not an exception.

The executor-level job state is therefore `succeeded`.

Consequently, physical-device success and domain-specific validation are the responsibility of the handler/controller.

---

# 21. failed

If handler execution raises an `Exception`, the job state becomes `failed`.

The stored error contains:

```text
type
message
traceback
```

The type is obtained from:

```python
exc.__class__.__name__
```

The message is obtained from:

```python
str(exc)
```

The traceback is obtained from:

```python
traceback.format_exc()
```

The result is set to `None`.

`finished_at` is also set.

---

# 22. Worker behavior after handler failure

A worker process does not terminate merely because its handler failed.

After exception handling, the worker metadata returns to:

```text
state = idle
current_job_id = None
current_handler = None
```

Thus:

```text
job failure
    !=
worker failure
```

An ordinary Python `Exception` is isolated as the failure of one job, and the same worker-local instance can process the next job.

However, if a handler partially changes the instance state before raising an exception, there is no automatic rollback of that instance state.

---

# 23. state(job_id)

Internally, `state()` calls `job(job_id)`.

If the job does not exist, it returns `None`.

If the job exists, it returns the metadata field `"state"`.

Therefore, an unknown job ID produces `None`, not an exception.

This differs from `result()` and `wait()`, which raise `KeyError` for an unknown job ID.

---

# 24. job(job_id)

`job()` retrieves metadata from the shared jobs dictionary.

If the job does not exist, it returns `None`.

Otherwise, it converts the metadata to an ordinary dictionary:

```python
dict(metadata)
```

It does not expose the Manager proxy directly.

The returned dictionary is a snapshot.

If a worker subsequently updates the shared metadata, a previously retrieved local dictionary is not updated automatically.

---

# 25. jobs()

`jobs()` iterates over all jobs and converts each metadata entry into an ordinary dictionary.

The return value is a dictionary of:

```text
job_id -> metadata snapshot
```

The source contains no code that deletes completed jobs.

Therefore, metadata for jobs submitted during the executor's lifetime accumulates in the jobs dictionary.

This retention should be considered when submitting large numbers of jobs over a long period.

---

# 26. worker(worker_id)

`worker()` calls `_replenish_workers()` before reading metadata.

It is therefore not a purely read-only operation.

If a dead worker exists, this call may trigger the launch of a replacement worker.

If the specified worker ID does not exist in the workers dictionary, it returns `None`.

Otherwise, it returns a metadata snapshot.

---

# 27. workers()

`workers()` also calls `_replenish_workers()` first.

It then returns snapshots of all metadata entries in the workers dictionary.

During replenishment, metadata for a dead worker is updated to `dead`, but the entry is not removed from the workers dictionary.

A replacement worker is added under a new UUID worker ID.

Consequently, `workers()` may include metadata for workers that died previously, rather than only workers currently alive.

---

# 28. result(job_id)

`result()` retrieves a completed result.

It does not wait.

For an unknown job ID:

```text
KeyError(job_id)
```

For a failed job:

```text
RuntimeError(str(error_metadata))
```

For a job whose state is not `succeeded`:

```text
RuntimeError("job is not completed")
```

For a succeeded job:

```text
metadata["result"]
```

There is no mechanism that re-raises the exception from the worker process in the parent process as the same exception class.

---

# 29. wait(job_id)

`wait()` is a polling loop.

It records the start time using `time.monotonic()`.

Inside the loop, it repeatedly calls `job(job_id)`.

If the job succeeded, it returns the result.

If the job failed, it raises `RuntimeError`.

If the timeout is exceeded, it raises `TimeoutError`.

Otherwise, it waits using:

```python
time.sleep(interval_sec)
```

and checks again.

This is not a notification-based wait using a `Condition` or `Event`.

---

# 30. A timeout does not cancel execution

Even if `wait(timeout_sec=...)` times out, it does not remove the job from the Queue.

It does not stop a running handler.

It does not change the job state to `failed`.

Therefore, a timeout means:

```text
the caller stops waiting in this wait call
```

It is not an execution deadline.

After the timeout, a worker may continue executing the job, and the job may subsequently become `succeeded`.

---

# 31. Cancellation

The source does not provide `cancel(job_id)`.

There is also no API for selectively removing a queued job from the Queue.

There is no mechanism for passing a cancellation token to a running handler.

There is no API for terminating a worker process on a per-job basis.

Therefore, cancellation is not a feature of the current ProcessExecutor.

---

# 32. Priority

The job queue is an ordinary multiprocessing Queue.

The job payload has no priority field.

It is not a priority queue.

Therefore, the source does not implement high/normal/low priority scheduling.

---

# 33. Ordering

`submit` calls `Queue.put` sequentially.

However, when `worker_count` is greater than one, jobs execute concurrently.

Consequently, completion order is not guaranteed to match submission order.

```text
submit A
submit B

Worker 1: A ----long----> complete
Worker 2: B -> complete
```

In this example, B can finish before A.

ProcessExecutor does not provide a gather API that rearranges completed results into submission order.

---

# 34. Job metadata timestamps

Each job has three timestamps.

`created_at` is recorded at submission.

`started_at` is recorded when a worker marks the job as running.

`finished_at` is recorded when the job becomes succeeded or failed.

Thus, the following values can be calculated:

```text
queue wait
    = started_at - created_at

handler execution
    = finished_at - started_at

total latency
    = finished_at - created_at
```

ProcessExecutor itself has no duration property, but durations can be derived from its metadata.

---

# 35. Worker metadata

Worker metadata contains:

```text
worker_id
pid
state
current_job_id
current_handler
```

When idle, the current job and handler are None.

When running, they contain the executing job ID and handler name.

When stopped, both are None again.

For a dead worker, the parent process's replenishment logic changes the state of the existing metadata.

This metadata can be used for monitoring or status endpoints.

---

# 36. Worker ID

A worker ID is generated as UUID4 hex each time a worker process starts.

It is not a pool-slot index.

If a worker dies and a replacement starts, the replacement receives a different ID.

The implementation therefore treats a worker ID as the identity of a particular process instance.

---

# 37. Stop sentinel

The worker loop uses the special string:

```text
__PROCESS_EXECUTOR_STOP__
```

When a worker receives this value from the Queue, it exits the loop instead of processing the value as a job payload.

After exiting the loop, it updates its worker metadata to `stopped`.

A stop sentinel is not an ordinary job with a job ID.

---

# 38. close()

`close()` has an idempotency guard that ignores calls after the first.

First, it sets `_closed=True`.

It then collects the live processes from `_processes`.

It puts one stop sentinel into the Queue for each live worker.

Next, it joins the live processes in sequence.

Finally, it closes the Queue and shuts down the Manager.

Under normal shutdown, this sequence expects each worker to receive STOP from the Queue and exit its loop.

---

# 39. close() and running jobs

A STOP sentinel is an item in the Queue.

If a worker is currently executing a handler, it cannot read STOP until that handler returns or raises an exception and the worker returns to `Queue.get()`.

Thus, `close()` does not forcibly interrupt a running handler.

The current `process.join()` has no timeout.

If a handler never returns, `close()` may wait indefinitely.

---

# 40. Context manager

`__enter__()` returns the executor itself.

`__exit__()` calls `close()`.

Thus:

```python
with ProcessExecutor(...) as executor:
    ...
```

allows the lifecycle to be tied to a scope.

However, using a context manager does not make it possible to forcibly terminate a running handler.

Its shutdown semantics are the same as `close()`.

---

# 41. Dead worker detection

`_replenish_workers()` scans the parent's `_processes` table.

It calls:

```python
process.is_alive()
```

on each Process.

Worker IDs for which this returns False are added to a dead list.

It then pops the dead processes from the table and joins them.

If worker metadata exists, it takes a snapshot and updates it to:

```text
state = dead
```

Finally, it starts new workers until the number of entries in `_processes` returns to `worker_count`.

---

# 42. When replenishment is triggered

Replenishment is not monitored continuously by a background thread.

In the source, it is invoked at least from:

```text
submit()
worker()
workers()
```

Therefore, this is not an architecture in which an independent watchdog immediately creates a replacement the moment a worker dies.

The failure is detected when the next public operation that invokes replenishment is executed.

---

# 43. Worker recovery and job recovery are separate

Consider a worker that dies from a process-level failure while executing a handler.

Its worker metadata may still contain `current_job_id`.

The job metadata may remain in the `running` state.

Replenishment detects the dead worker and starts a replacement.

However, the source does not contain code that performs the following:

```text
Retrieve current_job_id from the dead worker
        |
        v
Mark that job as failed
```

Consequently, even if the pool size is restored, the state of the running job is not automatically repaired.

This is an important failure boundary.

---

# 44. Process-level failure

A Python Exception raised inside a handler can be caught by `try/except`.

However, the following are different cases:

```text
Process killed by the OS
Native extension crash
Segmentation fault
Abnormal process termination
```

In these cases, the exception handling in the worker loop may not execute.

The code that would update the job to `failed` cannot run either.

The current recovery mechanism in ProcessExecutor is limited to replenishing worker processes.

---

# 45. Factory failure

Before entering the job loop, the worker main function executes:

```python
instance = worker_factory(context)
```

This call is outside the `try/except` used for handler execution.

Therefore, if the factory or the `worker_class` constructor fails, there is no path that records that failure as `failed` in the job metadata.

No job has been retrieved yet, so the failure is not associated with a particular job either.

---

# 46. Persistent startup failure

Consider a configuration in which the worker factory always fails.

The parent can still successfully call `Process.start()`.

The child then exits because of the factory failure.

When replenishment later detects the dead process, it creates a replacement worker.

However, if the configuration is unchanged, the replacement may encounter the same factory failure.

The source does not implement worker startup retry backoff, a startup failure counter, or a circuit breaker.

---

# 47. Daemon workers

Worker processes are daemons.

A daemon process is subordinate to its parent process.

Also, a daemon process in Python multiprocessing cannot create child processes.

Consequently, there are constraints on a nested worker architecture that launches another `multiprocessing.Process` from within a handler.

The implementation is best understood as a single-level worker pool.

---

# 48. Manager

Manager is used to create the following shared objects:

```text
jobs
workers
state
lock
```

Manager proxies allow separate processes to read and write metadata through an interface resembling a Python dictionary.

Unlike direct access to a shared-memory array, however, proxy communication incurs overhead.

This executor is designed to share metadata and coarse-grained state.

---

# 49. Queue

The Queue is the channel for job dispatch.

The authoritative job metadata is not stored exclusively inside the Queue.

A Queue item is removed when consumed by a worker, but the metadata in the `jobs` dictionary remains.

Therefore, even after a job completes, the parent can look up its state, result, and error by job ID.

The responsibilities are separated as follows:

```text
Queue
    Delivery of execution requests

jobs
    Retention of execution records
```

---

# 50. args / kwargs

The `args` and `kwargs` passed to `submit` are used in two places.

First, they are stored in the job metadata.

They are also included in the Queue payload.

The worker expands the `args` and `kwargs` from the Queue payload when calling the handler.

Thus, passing very large objects as arguments can incur costs both for IPC and for metadata retention.

The source does not impose an argument size limit.

---

# 51. Result storage

A handler's return value is stored in the metadata in the jobs Manager dictionary.

Consequently, a result must also be an object that the Manager can handle across processes.

For workloads that return large quantities of very large numerical arrays, the IPC overhead of this design needs to be considered.

ProcessExecutor does not provide a specialized shared-memory ndarray transport implementation.

---

# 52. Job retention

Completed jobs are not removed from the jobs dictionary.

The source does not provide:

```text
delete_job
purge_jobs
TTL
max_history
```

Therefore, when an executor has a long lifetime and the number of submissions keeps growing, retained metadata and results also accumulate.

This is the other side of a design that allows results to be retrieved later by job ID.

---

# 53. Dead worker metadata retention

The Process object for a dead worker is removed from `_processes`.

However, its metadata is not removed from the workers Manager dictionary.

It remains there with its state set to `dead`.

A replacement worker is added with a new ID.

Therefore, `workers()` may include historical dead workers.

The current table of live processes and the worker-history metadata are separate.

---

# 54. Logger

The constructor accepts a `logger` argument and retains it as:

```python
self._logger = logger
```

However, the supplied source contains no logging calls that use `_logger`.

Nor does it contain code that passes the logger to the worker main function.

Thus, the current implementation only establishes that the parent executor retains a logger reference.

It does not establish that a worker-safe logging system is implemented.

---

# 55. Snapshot helper

`_snapshot()` is a helper that converts a Manager proxy value into an ordinary dictionary.

For `None`, it returns an empty dictionary.

Otherwise, it returns `dict(value)`.

When a worker updates job metadata, it takes a snapshot of the current metadata, modifies the fields, and assigns the dictionary back to the jobs dictionary.

In other words, the pattern is to create and replace a dictionary snapshot rather than directly mutating nested fields.

---

# 56. Timestamp helper

`_now()` simply returns:

```python
time.time()
```

It is used for wall-clock timestamps in job metadata.

By contrast, `wait()` uses `time.monotonic()` to measure timeouts.

These serve different purposes:

```text
time.time()
    Time recorded as metadata

time.monotonic()
    Measurement of elapsed time for timeouts
```

---

# 57. Obtaining SharedContext from the parent

The `shared_context` property returns a new SharedContext object for the parent controller.

It contains proxy references to the same objects used by workers:

```text
jobs
workers
state
lock
```

Therefore, not only the worker factory but also the parent can access shared state and the lock.

This can be used to design controller-specific shared state that the parent and workers access in common.

---

# 58. SharedContext does not define a domain schema

The source does not prescribe what should be stored in `state`.

For example, an application can define keys such as:

```text
target_position
last_direction
sequence
mode
```

However, these are not standard ProcessExecutor fields.

Consequently, each application must define its state schema explicitly.

A generic dictionary provides flexibility, but the meaning of its keys and their types is the application's responsibility.

---

# 59. When to use shared state

If worker B needs information updated by worker A for its next job, ordinary instance attributes alone are insufficient.

```text
Worker A
Controller A
self.target = 100

Worker B
Controller B
self.target is a different object
```

If that target represents the logical state of the controller as a whole, it is a candidate for storage in `SharedContext.state`.

On the other hand, state that is local to worker A, such as its internal cache, can remain an instance attribute.

Shared state should be limited to information that needs to be shared semantically, rather than sharing everything merely because multiprocessing is in use.

---

# 60. When to use the shared lock

When multiple workers perform read-modify-write operations on the same state, several operations may need to be treated as one atomic critical section.

In that case, code can use:

```python
with context.lock:
    current = context.state.get("counter", 0)
    context.state["counter"] = current + 1
```

ProcessExecutor does not provide a separate lock for each state key.

It provides one generic RLock.

If finer-grained locking is required, it must be added in the application's architecture.

---

# 61. One physical resource and multiple workers

Having multiple worker-local controller instances does not necessarily mean there are multiple physical resources, and the reverse is also true.

For example, if a single TCP connection must be maintained to one device, a structure such as the following may be necessary:

```text
Worker Ctrl A ----+
Worker Ctrl B ----+--> resource owner / proxy --> TCP connection x1
Worker Ctrl C ----+
```

ProcessExecutor does not implement TCP connection ownership.

Execution in worker processes and ownership of physical resources are separate concerns.

---

# 62. What increasing worker_count means

Increasing `worker_count` increases the number of worker processes that can execute handlers concurrently.

Whether this constitutes safe parallelism, however, depends on the resources used by the handlers.

For CPU calculations, the number of CPU cores and IPC overhead matter.

For device control, it matters whether the device protocol permits concurrent commands.

A single connection may require a serialization layer.

ProcessExecutor does not automatically determine an appropriate `worker_count`.

---

# 63. CPU-bound jobs

Because multiprocessing processes are used, execution takes place in separate processes rather than Python threads.

Structurally, this also allows CPU-bound Python code to run on multiple workers.

However, transferring job arguments and results through Queue/Manager introduces overhead.

When submitting large numbers of very small calculations, process IPC costs may dominate.

The source does not implement batching.

---

# 64. I/O-bound jobs

Handlers performing blocking I/O can also execute in worker processes.

The caller process can receive a job ID immediately after submission.

On the other hand, when multiple workers share the same physical I/O resource, the executor core does not decide how mutual exclusion or connection ownership should be managed.

Thus, an I/O-bound workload does not necessarily justify simply increasing `worker_count`.

---

# 65. Higher-level synchronous API

Although ProcessExecutor is job-based internally, its higher-level public API does not have to expose jobs.

```python
class MultiController:
    def __init__(self):
        self._executor = ProcessExecutor(
            worker_class=Controller,
            worker_count=2,
        )

    def move(self, position):
        job_id = self._executor.submit("move", position)
        return self._executor.wait(job_id)
```

To callers, this appears to be an ordinary synchronous method.

This approach hides the multiprocessing infrastructure inside a facade.

---

# 66. Higher-level asynchronous API

If job IDs should be exposed through the public API, the interface can instead look like this:

```python
def start_move(self, position):
    return self._executor.submit("move", position)

def move_state(self, job_id):
    return self._executor.state(job_id)
```

For an HTTP server, this separates the lifetime of a request from that of a long-running job.

ProcessExecutor itself has no knowledge of HTTP.

It only provides the job model.

---

# 67. Boundary with FastAPI and similar frameworks

ProcessExecutor is not a web framework.

It does not handle request parsing, authentication, response schemas, or HTTP status codes.

Nevertheless, it provides building blocks for a higher-level design such as:

```text
POST /jobs
    -> submit()
    -> job_id

GET /jobs/{id}
    -> job()

GET /jobs/{id}/result
    -> result()
```

If a handler name is passed directly from external input, an allowlist should be implemented in the higher-level layer, as discussed earlier.

---

# 68. Unknown job IDs

The behavior for an unknown ID differs between APIs.

```text
state(id)  -> None
job(id)    -> None
result(id) -> KeyError
wait(id)   -> KeyError
```

This distinction must be considered when implementing a higher-level API wrapper.

For example, when mapping these cases to HTTP 404, both `None` and `KeyError` can be normalized into the same application-level not-found response.

---

# 69. Exceptions from failed results

Calling `result()` or `wait()` for a failed job raises:

```python
RuntimeError(str(metadata.get("error")))
```

The entire error dictionary is converted to a string.

The original exception's traceback remains in the job metadata.

For detailed diagnostics, it can be accessed through:

```python
metadata = executor.job(job_id)
metadata["error"]["traceback"]
```

---

# 70. Result objects and snapshots

Although the metadata returned by `job()` is a snapshot, ProcessExecutor does not make an additional copy of the result object contained in that metadata to account for its mutability.

It merely converts the metadata retrieved from the Manager dictionary into a `dict` before returning it.

This is not a deep-copy API.

The semantics of complex mutable results depend on multiprocessing/Manager serialization and the object type.

---

# 71. Scope of submit argument validation

`submit` validates only the type of `handler` and whether it is an empty string.

It does not perform type validation on `args` or `kwargs`.

Nor does it check compatibility with the handler signature at submission time.

Consequently, invalid arguments cause a failed job when the handler is called in the worker process.

This is not an executor that returns an immediate validation error before accepting a job.

---

# 72. Worker class constructor arguments

`worker_args=None` is converted to an empty tuple.

`worker_kwargs=None` is converted to an empty dictionary.

In the `worker_class` path, `_WorkerClassFactory` retains these values.

Each worker process uses the same configured `args` and `kwargs` to create its own independent instance.

There is no mechanism that automatically assigns different constructor arguments to individual workers.

If this is needed, the design must use process context or external configuration within `worker_factory`.

---

# 73. Worker ID passed to the factory

The `worker_factory` signature is:

```python
worker_factory(context)
```

The worker ID itself is not included in the factory arguments.

Worker metadata is registered in `SharedContext.workers` only after the factory call.

Thus, the contract does not directly pass the factory its own worker ID.

If the constructor needs a worker-specific identity, the current factory interface does not provide it directly.

---

# 74. Failure before worker metadata registration

An idle record is created in worker metadata only after the factory succeeds.

If the factory fails, the `workers` dictionary may never contain metadata for that worker ID.

Replenishment detects dead processes through the parent's `_processes` collection.

If metadata is `None`, it does not update the state to `dead`.

Therefore, a worker that fails during startup is not guaranteed to remain in the historical metadata returned by `workers()`.

---

# 75. Worker stop metadata

On receiving `STOP`, the worker breaks out of the `while` loop.

It then sets its metadata state to `stopped`.

This is the normal shutdown path.

The `dead` state is used when the parent detects an abnormal or non-alive process.

```text
normal STOP -> stopped

unexpected death detected by parent -> dead
```

These are distinct cases.

---

# 76. Metadata after close

`close` calls `Manager.shutdown()`.

Consequently, the lifecycle does not support continued normal use of shared Manager proxies after `close`.

`submit` explicitly checks `_closed`.

Not every other read API checks `_closed`, but because the Manager itself has shut down, `close` should be treated as the end of the executor's usable lifetime.

---

# 77. Idempotency of close

The beginning of `close` is:

```python
if self._closed:
    return
```

This prevents duplicate shutdown when a context manager's exit overlaps with an explicit `close` call.

```python
executor.close()
executor.close()
```

The second call does nothing.

---

# 78. A None result

When a handler successfully returns `None`, the job state is `succeeded` and its result is `None`.

A failed job also has `None` in its metadata result field.

Therefore, the result field alone cannot distinguish success from failure.

Always interpret it together with the state.

`result()` checks the state before returning the result, so it makes this distinction internally.

---

# 79. A None error

On the normal `queued`, `running`, and `succeeded` paths, `error` is `None`.

For a failed job, `error` is a dictionary.

When a handler succeeds, saving the result explicitly sets `error=None`.

The `error` field in job metadata is intended for exception transport.

It is not a field for domain-level warnings or validation messages.

---

# 80. Metadata update pattern

A worker takes a snapshot of the current metadata from the `jobs` dictionary, changes fields, and reassigns the entire dictionary value.

```python
metadata = _snapshot(jobs.get(job_id))
metadata["state"] = "running"
...
jobs[job_id] = metadata
```

The same pattern is used for `succeeded` and `failed` updates.

Under this pattern, a metadata update is sent to the Manager as a dictionary replacement.

If future extensions allow multiple actors to update the same job metadata concurrently, the conflict semantics will need consideration.

At present, execution fields are primarily updated by the worker that obtained the job.

---

# 81. The jobs dictionary and locking

ProcessExecutor's own job metadata updates do not explicitly use `SharedContext.lock`.

Instead, they rely on Manager dictionary operations and snapshot replacement.

`SharedContext.lock` is provided for generic controller-side shared state.

If a future feature allows multiple processes to perform complex read-modify-write operations on the same job metadata, atomicity will need to be considered separately.

---

# 82. The workers dictionary and locking

Worker metadata updates likewise do not explicitly use a lock.

Each worker replaces the record associated with its own worker ID.

The parent-side replenishment logic updates the record of a dead worker to `state=dead`.

Depending on the timing of worker termination and parent-side detection, the final visible metadata state may depend on process scheduling.

---

# 83. Number of STOP messages in the Queue

`close` counts the number of alive processes and puts the same number of STOP messages into the Queue.

The intention is for each alive worker to consume one STOP message.

The count is based on alive processes, not dead processes.

However, because STOP messages enter the same Queue as ordinary jobs, any ordinary jobs already in the Queue may be processed before the STOP messages under FIFO ordering.

`close` is not implemented as an immediate shutdown that purges queued jobs.

---

# 84. close and queued jobs

There is no code that removes ordinary jobs remaining in the Queue when `close` is called.

Instead, STOP messages are added for the number of alive workers.

Consequently, depending on Queue ordering, workers may retrieve and process queued jobs before receiving STOP.

The semantics are not “cancel every job that has not started at the instant `close` is called.”

---

# 85. Concurrency between submit and close

The source does not provide a thread lock around the `_closed` flag.

It does not specify a strict thread-safety contract for calling `submit` and `close` concurrently from multiple threads in the same parent process.

ProcessExecutor's primary concurrency model is based on worker processes.

Additional verification is required before treating it as a library that guarantees concurrent parent-side API calls from multiple threads.

---

# 86. multiprocessing start methods and platforms

By using `get_context(start_method)`, the executor can consistently use an explicitly selected start method.

In environments such as macOS, where `spawn` is commonly used, it is important that the worker class or factory be importable and that the application use a main guard.

```python
if __name__ == "__main__":
    main()
```

ProcessExecutor cannot generate this guard automatically.

Providing it is the responsibility of the application.

---

# 87. pickle and worker factories

With `spawn`, target arguments must be serialized to start a worker process.

The worker factory or `_WorkerClassFactory` crosses a process boundary.

Defining classes and factories at module top level is generally easier to work with, as is typical of Python multiprocessing.

ProcessExecutor itself does not use an additional serializer such as cloudpickle.

---

# 88. Problems with passing physical device objects directly

The source does not establish that an already-open device object, socket, serial object, or similar resource can always be shared safely by passing it from the parent through `worker_args`.

This depends on the process boundary and start method.

How multiple workers serialize access to the same physical connection is a separate concern.

Creating resources inside workers, or designing an explicit IPC owner/proxy architecture, can therefore make responsibilities clearer.

---

# 89. Worker-local connections

If the `worker_class` constructor opens a connection, each of the `worker_count` workers executes that constructor.

Connections may therefore be created separately for each worker.

```text
worker_count = 4
Controller constructor opens TCP
        ->
Up to four TCP connections
```

Whether this is correct depends on the device's semantics.

ProcessExecutor does not control connection cardinality.

---

# 90. A single resource broker

If a physical resource must have a single owner, one possible architecture places that owner in one process or service and uses proxies from worker-local controllers.

```text
worker 1 --+
worker 2 --+--> proxy --> owner --> device
worker 3 --+
```

This is not a feature implemented by ProcessExecutor itself.

Nevertheless, separating the executor from resource ownership makes it possible to separate generic worker scheduling from device-specific serialization.

---

# 91. Job state machine

The job state transitions directly confirmed from the source are:

```text
submit
  |
  v
queued
  |
  | worker receives
  v
running
  |
  +-- handler returns -----> succeeded
  |
  `-- Exception ----------> failed
```

There are no states such as `cancelled`, `retrying`, `timeout`, or `lost`.

There is also no transition that automatically moves a running job to another state when its worker process dies.

---

# 92. Worker state machine

Conceptually, worker states follow:

```text
factory success
      |
      v
    idle
      |
      | job
      v
   running
      |
      +-- job complete --> idle
      |
      `-- STOP is read only after returning to queue loop

idle -- STOP --> stopped
```

When the parent detects that a process has died, its metadata is set to `dead`.

`dead` is not a state that a worker sets for itself during its normal loop.

---

# 93. Do not invent states outside the state machine

When an upper-level application displays job states in its UI, it is clearer to keep ProcessExecutor states separate from application-specific states.

For example, a physical device being `moving` and an executor job being `running` are different concepts.

```text
executor state: running

device state: moving
```

If a handler issues a device command and returns immediately, the executor job may be `succeeded` while the device is still `moving`.

Domain-specific states therefore need to be represented separately by the controller.

---

# 94. Handler granularity

ProcessExecutor treats one method call as one job.

However, the caller decides the granularity of the methods submitted.

```text
send_one_byte()
```

Submitting very large numbers of extremely fine-grained operations like this increases IPC overhead.

```text
measure_sample()
```

Grouping a meaningful, coarse-grained operation into one job can make the execution layer more useful.

The source does not enforce a particular granularity.

---

# 95. Long-running handlers

A long-running handler occupies one worker for its entire execution.

If four long-running handlers execute with `worker_count=4`, the fifth job waits in the Queue.

ProcessExecutor does not provide preemption.

It also cannot move a running handler to another worker partway through execution.

---

# 96. Starvation and fairness

The Queue is a generic multiprocessing Queue.

The source has neither handler priorities nor per-client quotas.

When long and short jobs share the same Queue, short jobs may wait depending on worker availability.

A strict scheduling fairness policy is not a feature provided by ProcessExecutor.

---

# 97. Backpressure

`submit` calls `Queue.put`.

No `maxsize` is specified when creating the Queue.

The source has no backpressure API that limits the number of outstanding jobs.

If producers continue submitting faster than workers can process jobs, queued work and job metadata accumulate.

For long-running servers, admission control at a higher layer may be appropriate.

---

# 98. Persistence

`jobs`, `workers`, and `state` reside in the Manager process's memory.

`close` calls `Manager.shutdown()`.

This is not a persistent database.

There is no mechanism to preserve job history after the parent process exits.

The implementation is not designed as a durable task queue.

---

# 99. Remote execution

Workers are `Process` instances in the same Python multiprocessing environment.

There is no network-based remote worker registration.

There is no message broker either.

This is not an executor distributed across multiple hosts.

It is an execution layer that uses process isolation within a single host.

---

# 100. Scope of ProcessExecutor

As established from the source, ProcessExecutor is applicable when methods of an existing Python class need to execute in local multiprocessing workers while the parent tracks the states of jobs and workers.

It is particularly useful when the following requirements occur together:

```text
Do not modify an existing class
Reuse worker processes
Need job IDs
Retrieve state/result/error later
Need multiple workers
Share some state between processes
```

---

# 101. Features Currently Not Provided by ProcessExecutor

Listing features absent from the source clarifies the boundaries of the implementation.

```text
cancel
retry policy
priority
job TTL
job purge
persistent storage
remote worker
dynamic resize
worker affinity
handler allowlist
execution deadline
startup readiness barrier
running-job recovery
metrics exporter
automatic resource broker
```

If any of these features are required, additional design work is necessary.

---

# 102. Example: Simple Computation

```python
class MathWorker:
    def square(self, value):
        return value * value
```

```python
with ProcessExecutor(
    worker_class=MathWorker,
    worker_count=4,
) as executor:
    ids = [
        executor.submit("square", value)
        for value in range(10)
    ]

    values = [
        executor.wait(job_id)
        for job_id in ids
    ]
```

Job IDs and submission order can be retained, but actual completion order depends on worker scheduling.

---

# 103. Example: Inspecting Job State

```python
job_id = executor.submit("run", argument)

print(executor.state(job_id))
print(executor.job(job_id))
```

A higher-level UI can display job status by periodically retrieving job metadata.

However, `job()` returns a snapshot. Retaining the same dictionary does not cause it to update automatically.

The metadata must be fetched again.

---

# 104. Example: Inspecting Workers

```python
workers = executor.workers()

for worker_id, metadata in workers.items():
    print(
        worker_id,
        metadata["pid"],
        metadata["state"],
        metadata["current_handler"],
    )
```

This lets you inspect the handler currently running.

Note that calling `workers()` itself may trigger worker replenishment.

---

# 105. Example: Shared Counter

```python
class CounterWorker:
    def __init__(self, state, lock):
        self._state = state
        self._lock = lock

    def increment(self):
        with self._lock:
            value = self._state.get("count", 0)
            value += 1
            self._state["count"] = value
            return value


def factory(context):
    return CounterWorker(
        state=context.state,
        lock=context.lock,
    )
```

In this example, all workers access the same `state["count"]` rather than a worker-local attribute.

The read-modify-write sequence is protected by a lock.

---

# 106. Example: Reading Shared State from the Parent

```python
context = executor.shared_context

with context.lock:
    current = context.state.get("count", 0)
```

The `shared_context` property gives the parent access to the same Manager proxy.

This is useful when workers and the parent use the same state schema.

---

# 107. Example: Synchronous Facade

```python
class MultiController:
    def __init__(self):
        self._executor = ProcessExecutor(
            worker_class=Controller,
            worker_count=2,
        )

    def query(self, command):
        job_id = self._executor.submit("query", command)
        return self._executor.wait(job_id)

    def close(self):
        self._executor.close()
```

The caller does not need to know about ProcessExecutor directly.

Actual processing can be moved to worker processes while preserving an existing synchronous interface.

---

# 108. Example: Job-Oriented Facade

```python
class JobController:
    def __init__(self):
        self._executor = ProcessExecutor(
            worker_class=Controller,
            worker_count=2,
        )

    def submit_query(self, command):
        return self._executor.submit("query", command)

    def job(self, job_id):
        return self._executor.job(job_id)

    def result(self, job_id):
        return self._executor.result(job_id)
```

This design exposes job IDs through the application API.

It is well suited to background operations in servers and GUIs.

---

# 109. Example: Checking Again After a Timeout

```python
job_id = executor.submit("long_operation")

try:
    result = executor.wait(job_id, timeout_sec=1.0)
except TimeoutError:
    print(executor.state(job_id))
```

Because a timeout does not cancel execution, you can check again later:

```python
print(executor.state(job_id))
```

The job state can be queried again in this way.

If the handler has completed, the state will have changed to `succeeded` or `failed`.

---

# 110. Example: Traceback of a Failed Job

```python
job_id = executor.submit("operation")

try:
    executor.wait(job_id)
except RuntimeError:
    metadata = executor.job(job_id)
    print(metadata["error"]["type"])
    print(metadata["error"]["message"])
    print(metadata["error"]["traceback"])
```

The worker traceback is stored in the error metadata.

The original exception object is not reconstructed in the parent process.

---

# 111. Example: close

```python
executor = ProcessExecutor(
    worker_class=Controller,
    worker_count=2,
)

try:
    ...
finally:
    executor.close()
```

Alternatively:

```python
with ProcessExecutor(
    worker_class=Controller,
    worker_count=2,
) as executor:
    ...
```



The context-manager form makes the resource lifecycle easier to express explicitly.

---

# 112. API Reference

The API Reference on these Pages is not maintained manually.

On each build, `docs/build.sh` runs:

```bash
sphinx-apidoc \
    -o docs/api \
    src/process_executor \
    -f \
    --module-first
```



Sphinx `autodoc` then processes the generated `.rst` files.

This automatically reflects the following source-code information on Pages:

```text
module
class
method
signature
type annotation
docstring
inheritance
source view
```



---

# 113. Roles of the Manual and Generated API Reference

The generated API Reference identifies the available classes and methods, together with their signatures and docstrings.

However, the following concepts are difficult to understand from individual source docstrings alone:

```text
Lifetime of a worker-local instance
Difference between jobs and the Queue
Difference between _processes and workers metadata
Difference between worker recovery and job recovery
Difference between wait timeout and execution cancellation
Meaning of SharedContext.state
Relationship between a single physical resource and worker_count
```

The manual explains these concepts.

Thus, the manual is not a copy of the API listing.

---

# 114. documentation build

For a local build, run the following from the repository root:

```bash
bash docs/build.sh
```



The script performs these steps:

```text
Remove docs/api
Remove docs/_build
Generate API stubs with sphinx-apidoc
Generate HTML with sphinx-build
Create .nojekyll
```



The HTML output directory is:

```text
docs/_build/html/
```



---

# 115. GitHub Pages Workflow

The delivered artifact uses the requested path:

```text
github/workflows/pages.yml
```



In the actual GitHub repository, GitHub Actions requires the file at:

```text
.github/workflows/pages.yml
```



The workflow is triggered by pushes to main or manual dispatch. It installs Python and the documentation dependencies, runs `docs/build.sh`, and deploys the resulting HTML as a Pages artifact.

---

# 116. Keeping Source and Documentation in Sync

Because the API reference is generated from the source at build time, additions and removals in the public API are reflected automatically.

The manual describes execution semantics and therefore requires manual updates when internal behavior changes.

For example:

```text
Add job cancellation
Add worker crash recovery
Add a readiness barrier
Add job purging
```

If such features are added, their methods will appear in the generated API, but their design implications must also be documented in the manual.

---

# 117. Areas to Review When Changing the Design

When changing job metadata, review:

```text
submit
worker running update
success/failure update
state
job/jobs
result/wait
```



When changing worker metadata, review:

```text
worker startup
running
idle
stopped
dead
worker/workers
replenish
```



When changing SharedContext, review:

```text
parent shared_context property
worker main
worker_factory
controller-side usage
```



When changing the lifecycle, review:

```text
STOP
Queue
join
Manager shutdown
dead worker
queued/running job
```



---

# 118. Current Implementation at a Glance

```text
ProcessExecutor.__init__
        |
        +--> get_context
        +--> Manager
        |     +--> jobs
        |     +--> workers
        |     +--> state
        |     `--> RLock
        |
        +--> Queue
        |
        `--> start workers
                |
                v
          _worker_main
                |
                +--> SharedContext
                +--> factory(context)
                +--> worker = idle
                |
                `--> Queue.get loop
                        |
                        +--> STOP -> stopped
                        |
                        `--> job
                             |
                             +--> worker running
                             +--> job running
                             |
                             +--> getattr(instance, handler)
                             +--> handler(*args, **kwargs)
                             |
                             +--> succeeded + result
                             |        or
                             `--> failed + error
                                      |
                                      v
                                  worker idle
```

This diagram summarizes the core structure of the current source code.

---

# 119. Summary

ProcessExecutor is a generic execution layer for reusing existing Python classes in multiprocessing workers.

The most important separation of responsibilities is:

```text
Meaning of a method
    -> worker-local class

Which process executes a method
    -> ProcessExecutor

Job execution record
    -> jobs

Worker execution record
    -> workers

State shared across the controller
    -> SharedContext.state

Process-safe critical section
    -> SharedContext.lock
```

This separation provides a common worker pool, job IDs, state/result/error handling, worker observation, shared context, and shutdown without embedding multiprocessing infrastructure in existing classes.

It is equally important not to mistake features absent from the current source—such as cancellation, priorities, persistent queues, remote execution, and automatic job recovery—for supported functionality.

The API listing is generated mechanically from source, while the manual explains the execution model and boundaries of responsibility.

# 120. Source-derived API inspection

## `__init__`
Source line: `182`

Calls appearing in the implementation:

- `ValueError`
- `_WorkerClassFactory`
- `multiprocessing.get_context`
- `self._manager.RLock`
- `self._manager.dict`
- `self._mp_context.Manager`
- `self._mp_context.Queue`
- `self._start_workers`

Explicit raises:

- `ValueError('worker_count must be 1 or greater')`
- `ValueError('worker_factory or worker_class must be specified')`
- `ValueError('worker_factory and worker_class cannot be used together')`
- `ValueError('worker_args is only valid with worker_class')`
- `ValueError('worker_kwargs is only valid with worker_class')`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `shared_context`
Source line: `248`

Docstring:

> Return the shared context for the parent controller.

Calls appearing in the implementation:

- `SharedContext`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `submit`
Source line: `257`

Docstring:

> Submit a handler job and immediately return its job ID.

Calls appearing in the implementation:

- `RuntimeError`
- `TypeError`
- `ValueError`
- `_now`
- `isinstance`
- `self._job_queue.put`
- `self._replenish_workers`
- `uuid.uuid4`

Explicit raises:

- `RuntimeError('ProcessExecutor is already closed')`
- `TypeError('handler must be str')`
- `ValueError('handler must not be empty')`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `state`
Source line: `296`

Docstring:

> Return a job state.

Calls appearing in the implementation:

- `metadata.get`
- `self.job`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `job`
Source line: `305`

Docstring:

> Return a snapshot of one job.

Calls appearing in the implementation:

- `dict`
- `self._jobs.get`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `jobs`
Source line: `314`

Docstring:

> Return snapshots of all jobs.

Calls appearing in the implementation:

- `dict`
- `self._jobs.items`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `worker`
Source line: `323`

Docstring:

> Return a snapshot of one worker.

Calls appearing in the implementation:

- `dict`
- `self._replenish_workers`
- `self._workers.get`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `workers`
Source line: `333`

Docstring:

> Return snapshots of all workers.

Calls appearing in the implementation:

- `dict`
- `self._replenish_workers`
- `self._workers.items`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `result`
Source line: `343`

Docstring:

> Return the result of a completed job.

Calls appearing in the implementation:

- `KeyError`
- `RuntimeError`
- `metadata.get`
- `self.job`
- `str`

Explicit raises:

- `KeyError(job_id)`
- `RuntimeError(str(metadata.get('error')))`
- `RuntimeError('job is not completed')`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `wait`
Source line: `360`

Docstring:

> Explicitly wait for one job and return its result.

Calls appearing in the implementation:

- `KeyError`
- `RuntimeError`
- `TimeoutError`
- `metadata.get`
- `self.job`
- `str`
- `time.monotonic`
- `time.sleep`

Explicit raises:

- `KeyError(job_id)`
- `RuntimeError(str(metadata.get('error')))`
- `TimeoutError('waiting for job timed out')`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `close`
Source line: `391`

Docstring:

> Stop workers and shut down shared resources.

Calls appearing in the implementation:

- `alive_processes.append`
- `process.is_alive`
- `process.join`
- `self._job_queue.close`
- `self._job_queue.put`
- `self._manager.shutdown`
- `self._processes.values`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `__enter__`
Source line: `412`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `__exit__`
Source line: `415`

Calls appearing in the implementation:

- `self.close`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `_start_workers`
Source line: `418`

Docstring:

> Start the requested number of workers.

Calls appearing in the implementation:

- `len`
- `self._start_worker`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `_start_worker`
Source line: `423`

Docstring:

> Start one generic worker.

Calls appearing in the implementation:

- `process.start`
- `self._mp_context.Process`
- `uuid.uuid4`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `_replenish_workers`
Source line: `443`

Docstring:

> Remove dead workers and restore the configured worker count.

Calls appearing in the implementation:

- `dead_worker_ids.append`
- `dict`
- `len`
- `process.is_alive`
- `process.join`
- `self._processes.items`
- `self._processes.pop`
- `self._start_worker`
- `self._workers.get`

This list is supplementary information extracted from the supplied source AST. Sphinx autodoc generates the authoritative API representation from source at build time.

## `SharedContext`

`SharedContext` stores its four constructor arguments as attributes with the same names.

- `jobs`
- `workers`
- `state`
- `lock`

The supplied source defines no additional methods or validation.
