# CHANGELOG

## v0.1.2

### Documentation / GitHub Pages

- Added a Sphinx configuration for GitHub Pages.
- Added build-time API reference generation: `sphinx-apidoc` scans `src/process_executor` and `autodoc` renders the resulting documentation.
- Separated manually maintained Markdown documentation from the API reference generated from source code.
- Added a Quick Start and suggested reading order to the README, retaining the existing detailed descriptions of design, examples, and responsibility boundaries.
- Added a GitHub Actions workflow that runs `docs/build.sh` and deploys `docs/_build/html` to GitHub Pages.
- Worker for PyPI

## v0.1.1

In v0.1.1, the PM2CD multi example was revised to use multiple worker processes while consolidating communication with the physical device into a single connection.

The basic API of the `ProcessExecutor` core is unchanged. This release reorganizes the PM2CD examples using `worker_factory`, `worker_class`, and `SharedContext`, which were introduced in v0.1.0.

## PM2CD multi

Updated `example/pm2cd/multi/` as the basic multi-worker-process example.

The directory layout is as follows.

```text
example/pm2cd/
├── multi/
│   ├── client_sync.py
│   ├── pm2cd_multi.py
│   └── server.py
├── try_move_multi_client.py
└── try_move_multi_local.py
```

In the previous PM2CD multi example, creating a separate `PM2CDsingle` for each worker also created an independent `Communication` instance and TCP connection for each worker. Consequently, multiple communication paths to the same device were established.

In v0.1.1, `multi` creates only one `Communication` instance in the manager process and passes the same Communication proxy to each worker process’s `PM2CDsingle` instance.

```text
PM2CDsingle worker 1 ─┐
PM2CDsingle worker 2 ─┼─> Communication proxy
PM2CDsingle worker 3 ─┘
                           |
                           v
                    Communication
                           |
                           v
                      TCP connection
                           |
                           v
                         PM2CD
```

This arrangement provides the following properties:

- `ProcessExecutor` uses multiple worker processes.
- PM2CD controller logic continues to use the existing `PM2CDsingle` in each worker.
- Physical communication is consolidated into one `Communication` instance.
- No new PM2CD-specific worker class is introduced.



The number of workers is specified with `PM2CD(..., worker_count=...)`.

The example uses `WORKER_COUNT` as a configuration value in the code; it does not accept a command-line option for the worker count.

## PM2CD multi_shared

Added `example/pm2cd/multi_shared/`.

The directory layout is as follows.

```text
example/pm2cd/
├── multi_shared/
│   ├── client_sync.py
│   ├── pm2cd_multi.py
│   └── server.py
├── try_move_multi_shared_client.py
└── try_move_multi_shared_local.py
```

In addition to the communication arrangement used by `multi`, the `multi_shared` example shares the controller’s movement context among worker processes.

PM2CD’s `move()` / `move_relative()` and `wait_for_complete()` use the following information about the preceding positioning operation:

- target position
- backlash setting
- whether backlash compensation is enabled
- backlash value

When each worker only has its own independent `PM2CDsingle`, the movement context cannot be carried over if `move()` and `wait_for_complete()` execute in different workers.

In v0.1.1, `multi_shared` creates `PM2CDsingle` with `worker_factory(context)` and uses `SharedContext.state` as the movement context of `PM2CDsingle`.

```text
                    SharedContext.state
                           ^
                           |
          +----------------+----------------+
          |                |                |
     worker 1         worker 2         worker 3
    PM2CDsingle      PM2CDsingle      PM2CDsingle
          \                |               /
           \               |              /
            +------ Communication proxy ---+
                           |
                           v
                    Communication
                           |
                           v
                         PM2CD
```

This arrangement provides the following properties:across different worker processes,

```text
move()
  |
  v
shared move context
  |
  v
wait_for_complete()
```

the state can be carried over in this way.

Because the multi class handles the sharing mechanism internally, no dedicated shared-state API is added for callers.

## Differences between multi and multi_shared

`multi` demonstrates the basic configuration with multiple workers and one Communication instance.

It does not share controller-specific state, so calls must not depend on worker-local state.

In addition to one Communication instance, `multi_shared` uses `SharedContext` to share PM2CD movement context across workers.

Thus, positioning conditions can be preserved even when `move()` and `wait_for_complete()` run in different workers.

## Previous example

The former multi example, replaced in v0.1.1, was moved to

```text
example/pm2cd/x1_multi/
```

for archival purposes.

`x1_multi` preserves the earlier configuration. The PM2CD multi examples for v0.1.1 are

```text
example/pm2cd/multi/
example/pm2cd/multi_shared/
```

instead.

## Scope of v0.1.1

The principal change in v0.1.1 is the revised multi-process architecture of the PM2CD examples.

`ProcessExecutor` itself does not implement a device protocol or PM2CD-specific communication details.

The PM2CD example shares physical communication through a single `Communication` instance and uses `SharedContext` only when controller-specific state must be shared.

With this arrangement,

```text
ProcessExecutor
    |
    +-- generic worker execution
    +-- SharedContext

PM2CD multi
    |
    +-- PM2CDsingle worker × N
    +-- Communication × 1

PM2CD multi_shared
    |
    +-- PM2CDsingle worker × N
    +-- Communication × 1
    +-- shared move context
```

the examples demonstrate PM2CD operation across multiple worker processes while preserving these responsibility boundaries.

## v0.1.0

### Shared Context

- Added support for sharing data among handlers through `ProcessExecutor`'s `SharedContext`.
- Added a structure in which multiple handlers can access a common `state` through `ProcessExecutor.shared_context`.
- Retained the structure in which an existing single class is used as `worker_class`, with shared-state handling managed on the multi side.
- The existing public API of the single class and the basic structure of the multi class remain usable without changes when sharing is enabled.

### Example

- Added `example/pm2cd/multi_share/`.
- Added a PM2CD example demonstrating data sharing among handlers with `SharedContext`.
- The target position specified by `move()` is stored in shared state and retrieved by a subsequent `wait_for_complete()`.
- Sharing is handled within the multi class; callers do not need a dedicated API to manage the sharing mechanism.
- Added the following files for `multi_share`:
  - `example/pm2cd/multi_share/pm2cd_multi.py`
  - `example/pm2cd/multi_share/server.py`
  - `example/pm2cd/multi_share/client_sync.py`
  - `example/pm2cd/try_move_multi_share_local.py`
  - `example/pm2cd/try_move_multi_share_client.py`
- Provided test configurations for `multi_share` both locally and through a FastAPI server/client.


## v0.0.1

### Test / Example

- Organized test code and examples for verifying `process_executor`.
- Added and organized hardware tests using PM2CD under `example/pm2cd/`.
- Divided PM2CD tests into four configurations:
  - `try_move_single_local.py`
    - Local execution test using the single configuration.
  - `try_move_single_client.py`
    - Execution test using the single configuration through a FastAPI server/client.
  - `try_move_multi_local.py`
    - Local execution test using the multi configuration with `process_executor`.
  - `try_move_multi_client.py`
    - Execution test using the multi configuration with `process_executor` through a FastAPI server/client.
- Organized FastAPI server/client code by configuration:
  - `example/pm2cd/single/`
    - `server.py`
    - `client_sync.py`
  - `example/pm2cd/multi/`
    - `pm2cd_multi.py`
    - `server.py`
    - `client_sync.py`
- Provided separate test configurations for each stage: single, single server/client, multi local, and multi server/client.

### Dependency

- An updated `ese774_frame` is required to handle dataclasses as return values in PM2CD server/client tests.
- These tests rely on dataclass serialization and reconstruction in `ese774_frame`'s FastAPI auto-dispatch.
- Consequently, older versions of `ese774_frame` cannot run tests of APIs returning dataclasses such as PM2CD's `MotorStatus`.


## 0.0.0 - 2026-09-13

This is the initial release of `process-executor`.

This version implements a generic execution layer that runs ordinary Python classes, particularly existing device-control Ctrl classes, in worker processes without requiring them to be rewritten solely for multiprocessing.

### ProcessExecutor

Implemented `ProcessExecutor` with the following features:

- Start the specified number of worker processes.
- Reuse worker processes rather than recreating them for each job.
- Enqueue a handler name, positional arguments, and keyword arguments as a job.
- Resolve and execute a public method on the worker-local instance by handler name.
- Generate job IDs.
- Manage job states as `queued`, `running`, `succeeded`, and `failed`.
- Store job metadata in a shared mapping.
- Store worker metadata in a shared mapping.
- Store the type, message, and traceback of an `Exception` raised inside a worker-local handler in job metadata.
- Implement `state()`.
- Implement `job()`.
- Implement `jobs()`.
- Implement nonblocking result retrieval with `result()`.
- Implement explicit completion waiting with `wait()`.
- Implement worker metadata retrieval with `worker()` and `workers()`.
- Detect dead workers and replenish the pool up to the configured `worker_count`.
- Shut down workers, queues, and the manager with `close()`.
- Implement the context manager protocol.

The shutdown API is `close()`.

### worker_factory

Implemented worker-local instance creation through `worker_factory(context)`.

The factory receives a `SharedContext`.

This interface is intended for cases where shared state or a shared lock is needed when creating a worker instance.

### worker_class

Implemented `worker_class` to reuse existing classes without rewriting them as dedicated worker classes.

The following arguments are supported:

```python
ProcessExecutor(
    worker_class=ExistingCtrl,
    worker_args=(...),
    worker_kwargs={...},
)
```

Each worker process creates `ExistingCtrl(*worker_args, **worker_kwargs)`.

With `worker_class`, device-specific code no longer needs to repeatedly implement a worker class or factory with the same constructor and delegation as an existing Ctrl class.

`worker_factory` and `worker_class` cannot be specified together.

`worker_args` and `worker_kwargs` apply only to `worker_class`.

### SharedContext

Implemented `SharedContext`.

It holds the following shared objects:

- `jobs`
- `workers`
- `state`
- `lock`

`state` is a mapping for application-level shared state.

`lock` is a generic, process-safe `RLock` with no device-specific semantics.

### worker model

Instead of creating a dedicated worker process for each handler, each generic worker holds one worker-local instance and executes different methods for different jobs.

The following worker states are used:

- `idle`
- `running`
- `stopped`
- `dead`

The constructor starts the specified number of worker processes.

In version 0.0.0, the constructor does not wait until every worker has registered itself as `idle`. After startup, each worker process creates its worker-local instance and registers its metadata as `idle` once initialization is complete.

### job model

`submit()` returns a job ID without waiting for the handler to finish.

The following job states are used:

- `queued`
- `running`
- `succeeded`
- `failed`

Job metadata contains:

- `job_id`
- `handler`
- `args`
- `kwargs`
- `state`
- `worker_id`
- `result`
- `error`
- `created_at`
- `started_at`
- `finished_at`

### exception

An `Exception` raised while a handler on a worker-local instance is running is caught as a job failure.

The following information is saved as error metadata:

- exception type
- message
- traceback

Calling `result()` or `wait()` on a failed job raises `RuntimeError`.

### logger

Implemented an interface accepting `ProcessExecutor(..., logger=...)`.

In version 0.0.0, the logger is retained in the parent process; logger objects are not automatically injected into worker processes.

Process-safe rotating log aggregation is outside the responsibilities of the ProcessExecutor core.

### generic example

Included:

```text
example/dummy_single.py
example/dummy_ctrl.py
example/example.py
```

`DummySingle` is an ordinary synchronous class with no knowledge of multiprocessing.

`DummyCtrl` composes `ProcessExecutor` and uses `worker_factory` to execute `DummySingle` in worker processes.

`example.py` submits `get` and `query` as jobs to two workers and checks their states and results.

### PM2CD example

Included:

```text
example/pmcd/try_move.py
example/pmcd/pm2cd_multi.py
example/pmcd/try_move_multi.py
```

`try_move.py` demonstrates single-process execution using the existing `pm2cd.pm2cd_ctrl.PM2CD` directly.

`pm2cd_multi.py` is a limited multi facade that passes the existing `pm2cd.pm2cd_ctrl.PM2CD` to `ProcessExecutor` as `worker_class`.

No PM2CD-specific worker class, `Communication`, or `Device` is reimplemented.

`try_move_multi.py` demonstrates operating real hardware through the multi facade.

The PM2CD example uses `worker_count=1`, because version 0.0.0 does not implement a broker for sharing a single `Communication` / TCP connection across multiple worker processes.

### tests

The pytest test directory is `tests/`.

`tests/test_process_executor.py` verifies the job model, parallel execution, `SharedContext`, exceptions, `close()`, `worker_class`, and `worker_factory` behavior.

`tests/test_example.py` verifies the generic example.

Because the PM2CD example depends on physical hardware, pytest does not operate the hardware.

### Design decisions

- ProcessExecutor is independent of device protocols.
- ProcessExecutor does not prescribe the public API of Ctrl classes.
- Existing Ctrl classes do not need to inherit from a multiprocessing-specific class.
- Reusable architecture uses composition and delegation.
- Handlers and worker processes are not mapped one-to-one.
- A copy of Ctrl dedicated to workers is not required.
- Ordinary existing Ctrl classes are used directly through `worker_class`.
- Use `worker_factory` when `SharedContext` is needed.
- Job submission and completion waiting are separate operations.
- Generic process locks are separate from device-specific exclusion semantics.
- External servers, Web APIs, and gRPC are outside ProcessExecutor's responsibilities.
- Process-safe rotating log aggregation is outside ProcessExecutor's responsibilities.

### Known limitations

- Each worker creates its own independent worker-local instance.
- The constructor does not wait for all workers to finish initialization.
- There is no broker for sharing a single TCP socket, serial port, device handle, or similar resource among worker processes.
- Logger objects are not automatically distributed to worker processes.
- An `Exception` in a worker-local handler is not re-raised in the parent process as its original exception class; instead, a `RuntimeError` is raised based on error metadata.
- Failure to construct a worker-local instance through its constructor or factory is not reported to the parent process as a job failure.
- If a worker process terminates at process level while running a handler, its job is not automatically marked `failed`.
- Calling `wait()` without a timeout may continue waiting indefinitely after a process-level failure.
- Worker `join()` in `close()` has no timeout.
- Worker processes are daemon processes.
- Because `Manager` and `Queue` are used, the implementation is not intended to be an ultra-low-latency shared-memory execution engine.
- A job state of `succeeded` means the Python handler returned normally; it does not mean the physical device has completed its operation.
