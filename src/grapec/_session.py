"""The protocol neutral ``Session`` and ``AsyncSession``, connection owners.

Users normally go through ``grapec.Client`` subclasses, which create or
share a session. ``Session.call`` is the low level entry point.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, TypeVar
from urllib.parse import SplitResult, urlsplit

from ._pool import Pool
from ._service import MethodSpec, method_of

Req = TypeVar("Req")
Resp = TypeVar("Resp")

Metadata = dict[str, str | bytes]


class Connection(Protocol):
    """What a sync transport provides. One call at a time per connection."""

    @property
    def healthy(self) -> bool: ...

    def close(self) -> None: ...

    def unary(
        self,
        path: str,
        payload: bytes,
        *,
        timeout: float | None,
        metadata: Metadata | None,
        compression: str | None,
    ) -> bytes: ...


class AsyncConnection(Protocol):
    """What an async transport provides."""

    @property
    def healthy(self) -> bool: ...

    def close(self) -> None: ...

    async def aclose(self) -> None: ...

    async def unary(
        self,
        path: str,
        payload: bytes,
        *,
        timeout: float | None,
        metadata: Metadata | None,
        compression: str | None,
    ) -> bytes: ...


ConnectionFactory = Callable[[], Connection]
AsyncConnectionFactory = Callable[[], Awaitable[AsyncConnection]]


def _host_port(url: SplitResult, default_port: int) -> tuple[str, int]:
    if not url.hostname:
        raise ValueError(f"missing host in {url.geturl()!r}")
    return url.hostname, url.port or default_port


def _grpc_sync(url: SplitResult, tls: bool, connect_timeout: float | None) -> ConnectionFactory:
    from ._sync import GrpcConnection

    host, port = _host_port(url, 443 if tls else 80)
    return lambda: GrpcConnection(host, port, tls=tls, connect_timeout=connect_timeout)


def _grpc_async(url: SplitResult, tls: bool, connect_timeout: float | None) -> AsyncConnectionFactory:
    from ._async import AsyncGrpcConnection

    host, port = _host_port(url, 443 if tls else 80)
    return lambda: AsyncGrpcConnection(host, port, tls=tls).connect(connect_timeout)


_SYNC_SCHEMES: dict[str, Callable[[SplitResult, float | None], ConnectionFactory]] = {
    "grpc": lambda url, ct: _grpc_sync(url, False, ct),
    "grpcs": lambda url, ct: _grpc_sync(url, True, ct),
}
_ASYNC_SCHEMES: dict[str, Callable[[SplitResult, float | None], AsyncConnectionFactory]] = {
    "grpc": lambda url, ct: _grpc_async(url, False, ct),
    "grpcs": lambda url, ct: _grpc_async(url, True, ct),
}


class _BaseSession:
    """Options and request preparation shared by both clients."""

    def __init__(
        self,
        url: str,
        *,
        max_idle: int,
        timeout: float | None,
        connect_timeout: float | None,
        compression: str | None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.compression = compression
        self._parsed = urlsplit(url)
        self._connect_timeout = connect_timeout
        self._pool: Pool[Any] = Pool(max_idle)

    def _lookup(self, schemes: dict[str, Any]) -> Any:
        try:
            make = schemes[self._parsed.scheme]
        except KeyError:
            raise ValueError(f"unsupported scheme {self._parsed.scheme!r} in {self.url!r}") from None
        return make(self._parsed, self._connect_timeout)

    def _prepare(self, method: Any, request: Any, timeout: float | None, compression: str | None) -> tuple[MethodSpec, bytes, float | None, str | None]:
        spec = method_of(method)
        if not isinstance(request, spec.request):
            raise TypeError(f"{spec.path} expects {spec.request.__qualname__}, got {type(request).__qualname__}")
        payload = request.to_bytes()
        return (
            spec,
            payload,
            self.timeout if timeout is None else timeout,
            self.compression if compression is None else compression,
        )


class Session(_BaseSession):
    """Owns pooled connections to one server.

    ``url`` selects the protocol by scheme, for example ``grpc://host:50051``
    or ``grpcs://host:443``. Connections are pooled, at most ``max_idle`` idle
    connections are kept. A connection that fails below the application level
    is dropped, the error is raised to the caller and never retried.

    ``compression`` (``"gzip"`` or ``"deflate"``) compresses outgoing
    requests. Compressed responses are always accepted.
    """

    def __init__(
        self,
        url: str,
        *,
        max_idle: int = 4,
        timeout: float | None = None,
        connect_timeout: float | None = 10,
        compression: str | None = None,
    ) -> None:
        super().__init__(url, max_idle=max_idle, timeout=timeout, connect_timeout=connect_timeout, compression=compression)
        self._factory: ConnectionFactory = self._lookup(_SYNC_SCHEMES)

    def call(
        self,
        method: Callable[[Any, Req], Resp],
        request: Req,
        *,
        timeout: float | None = None,
        metadata: Metadata | None = None,
        compression: str | None = None,
    ) -> Resp:
        """Invoke ``method`` (for example ``Greeter.say_hello``) with ``request``.

        Works with methods of both ``Client`` and ``AsyncClient`` subclasses.
        """
        spec, payload, timeout, compression = self._prepare(method, request, timeout, compression)
        conn = self._acquire()
        try:
            raw = conn.unary(spec.path, payload, timeout=timeout, metadata=metadata, compression=compression)
        finally:
            self._release(conn)
        return spec.response.from_bytes(raw)  # type: ignore[no-any-return]

    def close(self) -> None:
        for conn in self._pool.drain():
            conn.close()

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _acquire(self) -> Connection:
        while (conn := self._pool.take()) is not None:
            if conn.healthy:
                return conn
            conn.close()
        return self._factory()

    def _release(self, conn: Connection) -> None:
        if not (conn.healthy and self._pool.give_back(conn)):
            conn.close()


class AsyncSession(_BaseSession):
    """asyncio twin of :class:`Session`, same options and pooling rules."""

    def __init__(
        self,
        url: str,
        *,
        max_idle: int = 4,
        timeout: float | None = None,
        connect_timeout: float | None = 10,
        compression: str | None = None,
    ) -> None:
        super().__init__(url, max_idle=max_idle, timeout=timeout, connect_timeout=connect_timeout, compression=compression)
        self._factory: AsyncConnectionFactory = self._lookup(_ASYNC_SCHEMES)

    async def call(
        self,
        method: Callable[[Any, Req], Resp],
        request: Req,
        *,
        timeout: float | None = None,
        metadata: Metadata | None = None,
        compression: str | None = None,
    ) -> Resp:
        spec, payload, timeout, compression = self._prepare(method, request, timeout, compression)
        conn = await self._acquire()
        try:
            raw = await conn.unary(spec.path, payload, timeout=timeout, metadata=metadata, compression=compression)
        finally:
            await self._release(conn)
        return spec.response.from_bytes(raw)  # type: ignore[no-any-return]

    async def aclose(self) -> None:
        for conn in self._pool.drain():
            await conn.aclose()

    def close(self) -> None:
        """Drop idle connections without waiting, for use outside the event loop."""
        for conn in self._pool.drain():
            conn.close()

    async def __aenter__(self) -> "AsyncSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    async def _acquire(self) -> AsyncConnection:
        while (conn := self._pool.take()) is not None:
            if conn.healthy:
                return conn
            conn.close()
        return await self._factory()

    async def _release(self, conn: AsyncConnection) -> None:
        if not (conn.healthy and self._pool.give_back(conn)):
            await conn.aclose()
