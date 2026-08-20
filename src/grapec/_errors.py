"""Error types shared by all transports."""

from __future__ import annotations

import enum


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
    """Base class for everything grapec raises at call time."""


class RpcError(GrapecError):
    """The remote side answered the call with a non OK status."""

    def __init__(self, code: Status, message: str = "", details: bytes = b"") -> None:
        super().__init__(f"{code.name}: {message}" if message else code.name)
        self.code = code
        self.message = message
        self.details = details


class TransportError(GrapecError):
    """The call failed below the application level, the connection is unusable."""
