"""The protocol neutral ``Session`` and ``AsyncSession``, connection owners.

Users normally go through ``grapec.Client`` subclasses, which create or
share a session. ``Session.call`` is the low level entry point.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable, Protocol, TypeVar
from urllib.parse import SplitResult, parse_qs, urlsplit

from . import thrift as _thrift
from .errors import RpcError, Status
from .wire import WireError
from .pool import Pool
from .schema import SchemaError
from .service import CallDetails, MethodSpec, method_of, remote_methods

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
        self.query = {k: v[-1] for k, v in parse_qs(url.query).items()}


class _GrpcProtocol:
    """How calls are encoded for gRPC: one struct in, one struct out, metadata allowed."""

    name = "gRPC"
    metadata = True

    def check(self, spec: MethodSpec) -> None:
        if spec.unary_struct is None:
            raise SchemaError(
                f"{spec.service.cls.__qualname__}.{spec.python_name}: gRPC methods take exactly one struct parameter and return a struct"
            )

    def encode(self, spec: MethodSpec, arguments: dict[str, Any]) -> tuple[str, bytes]:
        request_cls, _ = spec.unary_struct  # type: ignore[misc]
        request = arguments[spec.params[0].name]
        if not isinstance(request, request_cls):
            raise TypeError(f"{spec.path} expects {request_cls.__qualname__}, got {type(request).__qualname__}")
        return spec.path, request.to_bytes()

    def decode(self, spec: MethodSpec, raw: bytes) -> Any:
        _, response_cls = spec.unary_struct  # type: ignore[misc]
        try:
            return response_cls.from_bytes(raw)
        except (WireError, UnicodeDecodeError) as exc:
            raise RpcError(Status.INTERNAL, f"malformed reply for {spec.path}: {exc}") from exc


class _ThriftProtocol:
    """How calls are encoded for thrift: an args struct in, a result struct out."""

    name = "thrift"
    metadata = False

    def check(self, spec: MethodSpec) -> None:
        _thrift.method_fields(spec)

    def encode(self, spec: MethodSpec, arguments: dict[str, Any]) -> tuple[str, bytes]:
        return spec.name, _thrift.encode_call(spec, arguments)

    def decode(self, spec: MethodSpec, raw: bytes) -> Any:
        return _thrift.decode_result(spec, raw)


_PROTOCOLS: dict[str, Any] = {
    "grpc": _GrpcProtocol(),
    "grpcs": _GrpcProtocol(),
    "thrift": _ThriftProtocol(),
    "thrifts": _ThriftProtocol(),
}
_checked_services: set[tuple[type, str]] = set()


class _Inherit:
    """Default for the per call options, picks up the session wide value.

    Passing ``None`` explicitly means no timeout / no compression.
    """

    def __repr__(self) -> str:
        return "INHERIT"


INHERIT: Any = _Inherit()


def check_service(session: Any, cls: type) -> None:
    """Make sure every method of ``cls`` can be carried by the session's protocol."""
    protocol = session._protocol
    key = (cls, protocol.name)
    if key in _checked_services:
        return
    for method in remote_methods(cls).values():
        protocol.check(method.spec)
    _checked_services.add(key)


def _host_port(url: SplitResult, default_port: int) -> tuple[str, int]:
    if not url.hostname:
        raise ValueError(f"missing host in {url.geturl()!r}")
    return url.hostname, url.port or default_port


def _grpc_sync(opts: TransportOptions, tls: bool) -> ConnectionFactory:
    from .sync import GrpcConnection

    host, port = _host_port(opts.url, 443 if tls else 80)
    return lambda: GrpcConnection(host, port, tls=tls, connect_timeout=opts.connect_timeout, ssl_context=opts.ssl)


def _grpc_async(opts: TransportOptions, tls: bool) -> AsyncConnectionFactory:
    from .aio import AsyncGrpcConnection

    host, port = _host_port(opts.url, 443 if tls else 80)
    return lambda: AsyncGrpcConnection(host, port, tls=tls, ssl_context=opts.ssl).connect(opts.connect_timeout)


def _thrift_service(opts: TransportOptions) -> str | None:
    """The multiplexed service name from ``?multiplexed=<name>``."""
    value = opts.query.get("multiplexed")
    if not value:
        return None
    return value


def _thrift_sync(opts: TransportOptions, tls: bool) -> ConnectionFactory:
    from .sync import ThriftConnection

    host, port = _host_port(opts.url, 9090)
    service = _thrift_service(opts)
    return lambda: ThriftConnection(host, port, tls=tls, connect_timeout=opts.connect_timeout, ssl_context=opts.ssl, service=service)


def _thrift_async(opts: TransportOptions, tls: bool) -> AsyncConnectionFactory:
    from .aio import AsyncThriftConnection

    host, port = _host_port(opts.url, 9090)
    service = _thrift_service(opts)
    return lambda: AsyncThriftConnection(host, port, tls=tls, ssl_context=opts.ssl, service=service).connect(opts.connect_timeout)


_SYNC_SCHEMES: dict[str, Callable[[TransportOptions], ConnectionFactory]] = {
    "grpc": lambda opts: _grpc_sync(opts, False),
    "grpcs": lambda opts: _grpc_sync(opts, True),
    "thrift": lambda opts: _thrift_sync(opts, False),
    "thrifts": lambda opts: _thrift_sync(opts, True),
}
_ASYNC_SCHEMES: dict[str, Callable[[TransportOptions], AsyncConnectionFactory]] = {
    "grpc": lambda opts: _grpc_async(opts, False),
    "grpcs": lambda opts: _grpc_async(opts, True),
    "thrift": lambda opts: _thrift_async(opts, False),
    "thrifts": lambda opts: _thrift_async(opts, True),
}
_TLS_SCHEMES = frozenset({"grpcs", "thrifts"})


class _BaseSession:
    """Options and request preparation shared by both clients."""

    def __init__(
        self,
        url: str,
        *,
        max_idle: int,
        max_idle_time: float | None,
        max_conns: int | None,
        pool_timeout: float | None,
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
        if max_conns is not None and max_conns < 1:
            raise ValueError(f"max_conns must be at least 1, got {max_conns}")
        if pool_timeout is not None and pool_timeout < 0:
            raise ValueError(f"pool_timeout must not be negative, got {pool_timeout}")
        self._max_conns = max_conns
        self._pool_timeout = pool_timeout
        self._pool: Pool[Any] = Pool(max_idle, max_idle_time)
        try:
            self._protocol = _PROTOCOLS[self._parsed.scheme]
        except KeyError:
            raise ValueError(f"unsupported scheme {self._parsed.scheme!r} in {self.url!r}") from None
        if compression is not None and not self._protocol.metadata:
            raise ValueError(f"compression is not available with {self._protocol.name}")
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

    def _prepare(
        self, method: Any, args: tuple[Any, ...], kwargs: dict[str, Any], timeout: Any, metadata: Metadata | None, compression: Any
    ) -> tuple[MethodSpec, str, bytes, float | None, str | None]:
        spec = method_of(method)
        protocol = self._protocol
        protocol.check(spec)
        if not protocol.metadata and (metadata is not None or compression is not INHERIT):
            raise TypeError(f"metadata and compression are not available with {protocol.name}")
        path, payload = protocol.encode(spec, spec.bind(args, kwargs))
        return (
            spec,
            path,
            payload,
            self.timeout if timeout is INHERIT else timeout,
            self.compression if compression is INHERIT else compression,
        )


def _exhausted(timeout: float | None) -> RpcError:
    return RpcError(Status.RESOURCE_EXHAUSTED, f"no connection available within {timeout}s, the pool is at max_conns")


def _record(details: CallDetails | None, headers: Metadata, trailers: Metadata) -> None:
    if details is not None:
        details.headers = headers
        details.trailers = trailers


class Session(_BaseSession):
    """Owns pooled connections to one server.

    ``url`` selects the protocol by scheme, ``grpc://host:50051``,
    ``grpcs://host:443``, ``thrift://host:9090`` or ``thrifts://host:9090``
    (``?multiplexed=<service>`` for a TMultiplexedProtocol server). Connections are pooled, at most ``max_idle`` idle
    connections are kept and none longer than ``max_idle_time`` seconds.
    A connection that fails below the application level is dropped, the
    error is raised to the caller and never retried.

    A connection carries one call at a time, so N concurrent calls need N
    connections. ``max_conns`` caps how many the session may have open at
    once, ``None`` (the default) means no cap. Once they are all busy a call
    waits for one to come back, at most ``pool_timeout`` seconds (``None``
    waits forever, ``0`` fails right away) before raising ``RpcError`` with
    ``Status.RESOURCE_EXHAUSTED``. Without ``max_conns`` a call never waits
    and ``pool_timeout`` is unused.

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
        max_conns: int | None = None,
        pool_timeout: float | None = None,
        timeout: float | None = None,
        connect_timeout: float | None = 10,
        compression: str | None = None,
        ssl: Any = None,
    ) -> None:
        super().__init__(
            url,
            max_idle=max_idle,
            max_idle_time=max_idle_time,
            max_conns=max_conns,
            pool_timeout=pool_timeout,
            timeout=timeout,
            connect_timeout=connect_timeout,
            compression=compression,
            ssl=ssl,
        )
        self._factory: ConnectionFactory = self._lookup(_SYNC_SCHEMES)
        # bounded so that a permit released without a matching acquire fails loudly
        self._permits = threading.BoundedSemaphore(max_conns) if max_conns is not None else None

    def call(
        self,
        method: Callable[..., Resp],
        /,
        *args: Any,
        timeout: float | None = INHERIT,
        metadata: Metadata | None = None,
        compression: str | None = INHERIT,
        details: CallDetails | None = None,
        **kwargs: Any,
    ) -> Resp:
        """Invoke ``method`` (for example ``Greeter.say_hello``) with its arguments.

        Works with methods of both ``Client`` and ``AsyncClient`` subclasses.
        ``details`` receives the response headers and trailers.
        """
        spec, path, payload, timeout, compression = self._prepare(method, args, kwargs, timeout, metadata, compression)
        conn = self._acquire()
        try:
            raw, headers, trailers = conn.unary(path, payload, timeout=timeout, metadata=metadata, compression=compression)
        except RpcError as exc:
            _record(details, exc.headers, exc.trailers)
            raise
        finally:
            self._release(conn)
        _record(details, headers, trailers)
        return self._protocol.decode(spec, raw)  # type: ignore[no-any-return]

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
        permits = self._permits
        if permits is not None and not permits.acquire(timeout=self._pool_timeout):
            raise _exhausted(self._pool_timeout)
        try:
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
        except BaseException:
            self._release_permit()
            raise

    def _release_permit(self) -> None:
        if self._permits is not None:
            self._permits.release()

    def _release(self, conn: Connection) -> None:
        try:
            if not (conn.healthy and self._pool.give_back(conn)):
                conn.close()
        finally:
            self._release_permit()


class AsyncSession(_BaseSession):
    """asyncio twin of :class:`Session`, same options and pooling rules.

    ``max_conns`` is counted per session, the semaphore behind it binds to
    the loop of the first call, so a session belongs to one event loop.
    """

    def __init__(
        self,
        url: str,
        *,
        max_idle: int = 4,
        max_idle_time: float | None = 60,
        max_conns: int | None = None,
        pool_timeout: float | None = None,
        timeout: float | None = None,
        connect_timeout: float | None = 10,
        compression: str | None = None,
        ssl: Any = None,
    ) -> None:
        super().__init__(
            url,
            max_idle=max_idle,
            max_idle_time=max_idle_time,
            max_conns=max_conns,
            pool_timeout=pool_timeout,
            timeout=timeout,
            connect_timeout=connect_timeout,
            compression=compression,
            ssl=ssl,
        )
        self._factory: AsyncConnectionFactory = self._lookup(_ASYNC_SCHEMES)
        # made on first use, an asyncio.Semaphore binds to the loop that awaits it
        self._permits: asyncio.Semaphore | None = None

    async def call(
        self,
        method: Callable[..., Resp],
        /,
        *args: Any,
        timeout: float | None = INHERIT,
        metadata: Metadata | None = None,
        compression: str | None = INHERIT,
        details: CallDetails | None = None,
        **kwargs: Any,
    ) -> Resp:
        spec, path, payload, timeout, compression = self._prepare(method, args, kwargs, timeout, metadata, compression)
        conn = await self._acquire()
        try:
            raw, headers, trailers = await conn.unary(path, payload, timeout=timeout, metadata=metadata, compression=compression)
        except RpcError as exc:
            _record(details, exc.headers, exc.trailers)
            raise
        finally:
            await self._release(conn)
        _record(details, headers, trailers)
        return self._protocol.decode(spec, raw)  # type: ignore[no-any-return]

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
        if self._max_conns is not None:
            permits = self._permits
            if permits is None:
                permits = self._permits = asyncio.Semaphore(self._max_conns)
            try:
                async with asyncio.timeout(self._pool_timeout):
                    await permits.acquire()
            except TimeoutError:
                raise _exhausted(self._pool_timeout) from None
        try:
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
        except BaseException:
            self._release_permit()
            raise

    def _release_permit(self) -> None:
        if self._permits is not None:
            self._permits.release()

    async def _release(self, conn: AsyncConnection) -> None:
        try:
            if not (conn.healthy and self._pool.give_back(conn)):
                await conn.aclose()
        finally:
            self._release_permit()
