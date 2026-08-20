"""Idle connection bookkeeping shared by the sync and async clients.

The pool never blocks and never creates connections itself, the owner does
that outside the lock. The lock is only held for list operations, so it is
safe to use from asyncio code as well.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Generic, TypeVar

from .errors import TransportError

C = TypeVar("C")


class Pool(Generic[C]):
    def __init__(self, max_idle: int, max_idle_time: float | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._max_idle = max_idle
        self._max_idle_time = max_idle_time
        self._clock = clock
        self._idle: list[tuple[C, float]] = []
        self._lock = threading.Lock()
        self.closed = False

    def __len__(self) -> int:
        return len(self._idle)

    def take(self) -> tuple[C | None, list[C]]:
        """Pop the most recently returned idle connection, or None if a new one must be created.

        Also returns the connections that sat idle for longer than
        ``max_idle_time``, the caller must close them.
        """
        with self._lock:
            if self.closed:
                raise TransportError("session is closed")
            stale: list[C] = []
            if self._max_idle_time is not None:
                cutoff = self._clock() - self._max_idle_time
                fresh = []
                for conn, since in self._idle:
                    if since >= cutoff:
                        fresh.append((conn, since))
                    else:
                        stale.append(conn)
                self._idle = fresh
            conn = self._idle.pop()[0] if self._idle else None
            return conn, stale

    def give_back(self, conn: C) -> bool:
        """Return True if the connection was kept, False if the caller must close it."""
        with self._lock:
            if self.closed or len(self._idle) >= self._max_idle:
                return False
            self._idle.append((conn, self._clock()))
            return True

    def drain(self) -> list[C]:
        """Mark the pool closed and hand out every idle connection for closing."""
        with self._lock:
            self.closed = True
            idle, self._idle = self._idle, []
            return [conn for conn, _ in idle]
