"""Shared context used by worker processes."""

from typing import Any, MutableMapping


class SharedContext:
    """Shared state and lock for worker processes.

    Args:
        jobs: Shared mapping containing job metadata.
        workers: Shared mapping containing worker metadata.
        state: Shared mapping reserved for controller-specific shared state.
        lock: Process-safe lock shared by all workers.
    """

    def __init__(
        self,
        jobs: MutableMapping[str, Any],
        workers: MutableMapping[str, Any],
        state: MutableMapping[str, Any],
        lock: Any,
    ) -> None:
        self.jobs = jobs
        self.workers = workers
        self.state = state
        self.lock = lock
