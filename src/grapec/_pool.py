"""Idle connection bookkeeping shared by the sync and async clients.

The pool never blocks and never creates connections itself, the owner does
that outside the lock. The lock is only held for list operations, so it is
safe to use from asyncio code as well.
"""

from __future__ import annotations

import threading
from typing import Generic, TypeVar

from ._errors import TransportError

C = TypeVar("C")


class Pool(Generic[C]):
    def __init__(self, max_idle: int) -> None:
        self._max_idle = max_idle
        self._idle: list[C] = []
        self._lock = threading.Lock()
        self.closed = False

    def __len__(self) -> int:
        return len(self._idle)

    def take(self) -> C | None:
        """Pop an idle connection, or None if a new one must be created."""
        with self._lock:
            if self.closed:
                raise TransportError("session is closed")
            return self._idle.pop() if self._idle else None

    def give_back(self, conn: C) -> bool:
        """Return True if the connection was kept, False if the caller must close it."""
        with self._lock:
            if self.closed or len(self._idle) >= self._max_idle:
                return False
            self._idle.append(conn)
            return True

    def drain(self) -> list[C]:
        """Mark the pool closed and hand out every idle connection for closing."""
        with self._lock:
            self.closed = True
            idle, self._idle = self._idle, []
            return idle
