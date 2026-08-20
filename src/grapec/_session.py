"""The protocol neutral ``Session`` and ``AsyncSession``, connection owners.

Users normally go through ``grapec.Client`` subclasses, which create or
share a session. ``Session.call`` is the low level entry point.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, TypeVar
from urllib.parse import SplitResult, urlsplit

from ._errors import RpcError
from ._pool import Pool
from ._service import CallDetails, MethodSpec, method_of

Req = TypeVar("Req")
Resp = TypeVar("Resp")

Metadata = dict[str, str | bytes]


class Connection(Protocol):
    """What a sync transport provides. One call at a time per connection."""

    @property
    def healthy(self) -> bool: ...

    def poll(self) -> None:
        """Consume anything the peer sent while idle, may turn ``healthy`` false. Never blocks."""

    def close(self, *, flush: bool = True) -> None: ...

    def unary(
        self,
        path: str,
        payload: bytes,
        *,
        timeout: float | None,
        metadata: Metadata | None,
        compression: str | None,
    ) -> tuple[bytes, Metadata, Metadata]: ...


class AsyncConnection(Protocol):
    """What an async transport provides."""

    @property
    def healthy(self) -> bool: ...

    async def poll(self) -> None: ...

    def close(self, *, flush: bool = True) -> None: ...

    async def aclose(self) -> None: ...

    async def unary(
        self,
        path: str,
        payload: bytes,
        *,
        timeout: float | None,
        metadata: Metadata | None,
        compression: str | None,
    ) -> tuple[bytes, Metadata, Metadata]: ...


ConnectionFactory = Callable[[], Connection]
AsyncConnectionFactory = Callable[[], Awaitable[AsyncConnection]]


class TransportOptions:
    """What the URL scheme handlers get to build connections."""

    def __init__(self, url: SplitResult, connect_timeout: float | None, ssl: Any) -> None:
        self.url = url
        self.connect_timeout = connect_timeout
        self.ssl = ssl


def _host_port(url: SplitResult, default_port: int) -> tuple[str, int]:
    if not url.hostname:
        raise ValueError(f"missing host in {url.geturl()!r}")
    return url.hostname, url.port or default_port


def _grpc_sync(opts: TransportOptions, tls: bool) -> ConnectionFactory:
    from ._sync import GrpcConnection

    host, port = _host_port(opts.url, 443 if tls else 80)
    return lambda: GrpcConnection(host, port, tls=tls, connect_timeout=opts.connect_timeout, ssl_context=opts.ssl)


def _grpc_async(opts: TransportOptions, tls: bool) -> AsyncConnectionFactory:
    from ._async import AsyncGrpcConnection

    host, port = _host_port(opts.url, 443 if tls else 80)
    return lambda: AsyncGrpcConnection(host, port, tls=tls, ssl_context=opts.ssl).connect(opts.connect_timeout)


_SYNC_SCHEMES: dict[str, Callable[[TransportOptions], ConnectionFactory]] = {
    "grpc": lambda opts: _grpc_sync(opts, False),
    "grpcs": lambda opts: _grpc_sync(opts, True),
}
_ASYNC_SCHEMES: dict[str, Callable[[TransportOptions], AsyncConnectionFactory]] = {
    "grpc": lambda opts: _grpc_async(opts, False),
    "grpcs": lambda opts: _grpc_async(opts, True),
}
_TLS_SCHEMES = frozenset({"grpcs"})


class _BaseSession:
    """Options and request preparation shared by both clients."""

    def __init__(
        self,
        url: str,
        *,
        max_idle: int,
        max_idle_time: float | None,
        timeout: float | None,
        connect_timeout: float | None,
        compression: str | None,
        ssl: Any,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.compression = compression
        self._parsed = urlsplit(url)
        self._connect_timeout = connect_timeout
        self._ssl = ssl
        self._pool: Pool[Any] = Pool(max_idle, max_idle_time)
        if ssl is not None:
            import ssl as _ssl

            if not isinstance(ssl, _ssl.SSLContext):
                raise TypeError(f"ssl must be an ssl.SSLContext, got {type(ssl).__qualname__}")
            if self._parsed.scheme not in _TLS_SCHEMES:
                raise ValueError(f"ssl is only used with TLS schemes, not {self._parsed.scheme!r}")

    def _lookup(self, schemes: dict[str, Any]) -> Any:
        try:
            make = schemes[self._parsed.scheme]
        except KeyError:
            raise ValueError(f"unsupported scheme {self._parsed.scheme!r} in {self.url!r}") from None
        return make(TransportOptions(self._parsed, self._connect_timeout, self._ssl))

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


def _record(details: CallDetails | None, headers: Metadata, trailers: Metadata) -> None:
    if details is not None:
        details.headers = headers
        details.trailers = trailers


class Session(_BaseSession):
    """Owns pooled connections to one server.

    ``url`` selects the protocol by scheme, for example ``grpc://host:50051``
    or ``grpcs://host:443``. Connections are pooled, at most ``max_idle`` idle
    connections are kept and none longer than ``max_idle_time`` seconds.
    A connection that fails below the application level is dropped, the
    error is raised to the caller and never retried.

    ``compression`` (``"gzip"`` or ``"deflate"``) compresses outgoing
    requests. Compressed responses are always accepted. ``ssl`` is an
    ``ssl.SSLContext`` for ``grpcs://`` URLs, the default context verifies
    against the system trust store.
    """

    def __init__(
        self,
        url: str,
        *,
        max_idle: int = 4,
        max_idle_time: float | None = 60,
        timeout: float | None = None,
        connect_timeout: float | None = 10,
        compression: str | None = None,
        ssl: Any = None,
    ) -> None:
        super().__init__(
            url,
            max_idle=max_idle,
            max_idle_time=max_idle_time,
            timeout=timeout,
            connect_timeout=connect_timeout,
            compression=compression,
            ssl=ssl,
        )
        self._factory: ConnectionFactory = self._lookup(_SYNC_SCHEMES)

    def call(
        self,
        method: Callable[[Any, Req], Resp],
        request: Req,
        *,
        timeout: float | None = None,
        metadata: Metadata | None = None,
        compression: str | None = None,
        details: CallDetails | None = None,
    ) -> Resp:
        """Invoke ``method`` (for example ``Greeter.say_hello``) with ``request``.

        Works with methods of both ``Client`` and ``AsyncClient`` subclasses.
        ``details`` receives the response headers and trailers.
        """
        spec, payload, timeout, compression = self._prepare(method, request, timeout, compression)
        conn = self._acquire()
        try:
            raw, headers, trailers = conn.unary(spec.path, payload, timeout=timeout, metadata=metadata, compression=compression)
        except RpcError as exc:
            _record(details, exc.headers, exc.trailers)
            raise
        finally:
            self._release(conn)
        _record(details, headers, trailers)
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
            for conn in self._pool.drain():
                conn.close(flush=False)
        except Exception:
            pass

    def _acquire(self) -> Connection:
        while True:
            conn, stale = self._pool.take()
            for old in stale:
                old.close()
            if conn is None:
                return self._factory()
            conn.poll()
            if conn.healthy:
                return conn
            conn.close()

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
        max_idle_time: float | None = 60,
        timeout: float | None = None,
        connect_timeout: float | None = 10,
        compression: str | None = None,
        ssl: Any = None,
    ) -> None:
        super().__init__(
            url,
            max_idle=max_idle,
            max_idle_time=max_idle_time,
            timeout=timeout,
            connect_timeout=connect_timeout,
            compression=compression,
            ssl=ssl,
        )
        self._factory: AsyncConnectionFactory = self._lookup(_ASYNC_SCHEMES)

    async def call(
        self,
        method: Callable[[Any, Req], Resp],
        request: Req,
        *,
        timeout: float | None = None,
        metadata: Metadata | None = None,
        compression: str | None = None,
        details: CallDetails | None = None,
    ) -> Resp:
        spec, payload, timeout, compression = self._prepare(method, request, timeout, compression)
        conn = await self._acquire()
        try:
            raw, headers, trailers = await conn.unary(spec.path, payload, timeout=timeout, metadata=metadata, compression=compression)
        except RpcError as exc:
            _record(details, exc.headers, exc.trailers)
            raise
        finally:
            await self._release(conn)
        _record(details, headers, trailers)
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
            for conn in self._pool.drain():
                conn.close(flush=False)
        except Exception:
            pass

    async def _acquire(self) -> AsyncConnection:
        while True:
            conn, stale = self._pool.take()
            for old in stale:
                old.close()
            if conn is None:
                return await self._factory()
            await conn.poll()
            if conn.healthy:
                return conn
            conn.close()

    async def _release(self, conn: AsyncConnection) -> None:
        if not (conn.healthy and self._pool.give_back(conn)):
            await conn.aclose()
