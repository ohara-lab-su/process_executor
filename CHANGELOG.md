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
