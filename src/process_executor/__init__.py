"""Reusable multiprocessing execution support."""

from .executor import ProcessExecutor
from .shared_context import SharedContext

__all__ = [
    "ProcessExecutor",
    "SharedContext",
]

__version__ = "0.0.0"
