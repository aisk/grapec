"""The protocol neutral ``Client`` with a small connection pool."""

from __future__ import annotations

import threading
from typing import Any, Callable, Protocol, TypeVar
from urllib.parse import urlsplit

from ._errors import TransportError
from ._service import method_of

Req = TypeVar("Req")
Resp = TypeVar("Resp")

Metadata = dict[str, str | bytes]


class Connection(Protocol):
    """What a transport must provide. One call at a time per connection."""

    @property
    def healthy(self) -> bool: ...

    def close(self) -> None: ...

    def unary(self, path: str, payload: bytes, *, timeout: float | None, metadata: Metadata | None) -> bytes: ...


ConnectionFactory = Callable[[], Connection]


def _grpc_factory(url: Any, tls: bool, connect_timeout: float | None) -> ConnectionFactory:
    from ._grpc import GrpcConnection

    host = url.hostname
    port = url.port or (443 if tls else 80)
    if not host:
        raise ValueError(f"missing host in {url.geturl()!r}")
    return lambda: GrpcConnection(host, port, tls=tls, connect_timeout=connect_timeout)


_SCHEMES: dict[str, Callable[[Any, float | None], ConnectionFactory]] = {
    "grpc": lambda url, ct: _grpc_factory(url, False, ct),
    "grpcs": lambda url, ct: _grpc_factory(url, True, ct),
}


class Client:
    """Call methods of ``@grapec.service`` classes over the network.

    ``url`` selects the protocol by scheme, for example ``grpc://host:50051``
    or ``grpcs://host:443``. Connections are pooled, at most ``max_idle`` idle
    connections are kept. A connection that fails below the application level
    is dropped, the error is raised to the caller and never retried.
    """

    def __init__(
        self,
        url: str,
        *,
        max_idle: int = 4,
        timeout: float | None = None,
        connect_timeout: float | None = 10,
    ) -> None:
        parsed = urlsplit(url)
        try:
            make = _SCHEMES[parsed.scheme]
        except KeyError:
            raise ValueError(f"unsupported scheme {parsed.scheme!r} in {url!r}") from None
        self.url = url
        self.timeout = timeout
        self._factory = make(parsed, connect_timeout)
        self._max_idle = max_idle
        self._idle: list[Connection] = []
        self._lock = threading.Lock()
        self._closed = False

    def call(
        self,
        method: Callable[[Any, Req], Resp],
        request: Req,
        *,
        timeout: float | None = None,
        metadata: Metadata | None = None,
    ) -> Resp:
        """Invoke ``method`` (for example ``Greeter.say_hello``) with ``request``."""
        spec = method_of(method)
        if not isinstance(request, spec.request):
            raise TypeError(f"{spec.path} expects {spec.request.__qualname__}, got {type(request).__qualname__}")
        if timeout is None:
            timeout = self.timeout

        payload = request.to_bytes()  # type: ignore[attr-defined]
        conn = self._acquire()
        try:
            raw = conn.unary(spec.path, payload, timeout=timeout, metadata=metadata)
        finally:
            self._release(conn)
        return spec.response.from_bytes(raw)  # type: ignore[no-any-return]

    def close(self) -> None:
        with self._lock:
            self._closed = True
            idle, self._idle = self._idle, []
        for conn in idle:
            conn.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _acquire(self) -> Connection:
        while True:
            with self._lock:
                if self._closed:
                    raise TransportError("client is closed")
                if not self._idle:
                    break
                conn = self._idle.pop()
            if conn.healthy:
                return conn
            conn.close()
        return self._factory()

    def _release(self, conn: Connection) -> None:
        keep = conn.healthy
        if keep:
            with self._lock:
                if self._closed or len(self._idle) >= self._max_idle:
                    keep = False
                else:
                    self._idle.append(conn)
        if not keep:
            conn.close()
