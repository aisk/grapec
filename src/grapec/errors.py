"""Error types shared by all transports."""

from __future__ import annotations

import enum
from typing import Any


class Status(enum.IntEnum):
    """Call status codes, aligned with the gRPC status code numbers."""

    OK = 0
    CANCELLED = 1
    UNKNOWN = 2
    INVALID_ARGUMENT = 3
    DEADLINE_EXCEEDED = 4
    NOT_FOUND = 5
    ALREADY_EXISTS = 6
    PERMISSION_DENIED = 7
    RESOURCE_EXHAUSTED = 8
    FAILED_PRECONDITION = 9
    ABORTED = 10
    OUT_OF_RANGE = 11
    UNIMPLEMENTED = 12
    INTERNAL = 13
    UNAVAILABLE = 14
    DATA_LOSS = 15
    UNAUTHENTICATED = 16


class GrapecError(Exception):
    """Base class for call failures.

    Mistakes in the calling program (wrong types, bad schemas, unsupported
    options) raise ``TypeError`` or ``ValueError`` instead.
    """


class RpcError(GrapecError):
    """The remote side answered the call with a non OK status.

    ``headers`` and ``trailers`` hold the response metadata the server sent
    along with the status, binary values (``-bin`` keys) are ``bytes``.
    """

    def __init__(
        self,
        code: Status,
        message: str = "",
        details: bytes = b"",
        *,
        headers: dict[str, Any] | None = None,
        trailers: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code.name}: {message}" if message else code.name)
        self.code = code
        self.message = message
        self.details = details
        self.headers: dict[str, str | bytes] = dict(headers or {})
        self.trailers: dict[str, str | bytes] = dict(trailers or {})


class TransportError(GrapecError):
    """The call failed below the application level, the connection is unusable."""
