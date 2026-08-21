"""asyncio transports for :class:`GrpcProtocol` and :class:`ThriftProtocol`."""

from __future__ import annotations

import asyncio
import ssl

from .errors import RpcError, Status, TransportError
from .grpc import IO_ERRORS, GrpcProtocol, Metadata, authority, tls_context
from .thrift import ThriftProtocol

_RECV_SIZE = 65536
_CLOSE_TIMEOUT = 1


class AsyncGrpcConnection:
    def __init__(self, host: str, port: int, *, tls: bool, ssl_context: ssl.SSLContext | None = None) -> None:
        self._host = host
        self._port = port
        self._tls = tls
        self._ssl_context = ssl_context
        self._proto = GrpcProtocol(authority(host, port), tls=tls)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self, connect_timeout: float | None) -> "AsyncGrpcConnection":
        target = authority(self._host, self._port)
        ctx = None
        if self._tls:
            ctx = self._ssl_context or tls_context()
            ctx.set_alpn_protocols(["h2"])
        try:
            async with asyncio.timeout(connect_timeout):
                self._reader, self._writer = await asyncio.open_connection(
                    self._host,
                    self._port,
                    ssl=ctx,
                    server_hostname=self._host if self._tls else None,
                )
            if self._tls:
                ssl_obj = self._writer.get_extra_info("ssl_object")
                if ssl_obj is None or ssl_obj.selected_alpn_protocol() != "h2":
                    raise TransportError("server did not negotiate HTTP/2")
            await self._write(self._proto.data_to_send())
        except (OSError, TimeoutError, TransportError) as exc:
            if self._writer is not None:
                self._writer.close()
            if isinstance(exc, TransportError):
                raise
            raise TransportError(f"cannot connect to {target}: {exc}") from exc
        return self

    @property
    def healthy(self) -> bool:
        return self._proto.healthy and self._writer is not None and not self._writer.is_closing()

    async def poll(self) -> None:
        """Process whatever the peer sent while the connection was idle, without waiting."""
        proto = self._proto
        reader = self._reader
        assert reader is not None
        while proto.healthy:
            if reader.at_eof():
                proto.feed_idle(b"")
                break
            try:
                async with asyncio.timeout(0):
                    data = await reader.read(_RECV_SIZE)
            except TimeoutError:
                break
            except IO_ERRORS:
                proto.abort()
                break
            proto.feed_idle(data)
        if proto.healthy:
            self._write_quietly(proto.data_to_send())

    def close(self, *, flush: bool = True) -> None:
        """Sync close, drops the socket. Use ``aclose`` to flush GOAWAY first."""
        self._proto.close()
        if self._writer is not None:
            self._writer.close()

    async def aclose(self) -> None:
        if self._writer is None:
            return
        try:
            await self._write(self._proto.close())
        except Exception:
            pass
        self._writer.close()
        try:
            # TLS shutdown waits for the peer's close_notify, do not hang on servers that skip it
            async with asyncio.timeout(_CLOSE_TIMEOUT):
                await self._writer.wait_closed()
        except Exception:
            self._writer.transport.abort()

    async def unary(
        self,
        path: str,
        payload: bytes,
        *,
        timeout: float | None,
        metadata: Metadata | None,
        compression: str | None,
    ) -> tuple[bytes, Metadata, Metadata]:
        proto = self._proto
        assert self._reader is not None
        try:
            async with asyncio.timeout(timeout):
                proto.start(path, payload, timeout=timeout, metadata=metadata, compression=compression)
                while True:
                    out = proto.data_to_send()
                    if out:
                        await self._write(out)
                    if proto.done:
                        break
                    proto.feed(await self._reader.read(_RECV_SIZE))
            return proto.result()
        except RpcError:
            raise
        except TimeoutError as exc:
            self._write_quietly(proto.cancel())
            raise RpcError(Status.DEADLINE_EXCEEDED, "deadline exceeded") from exc
        except TransportError:
            proto.abort()
            raise
        except IO_ERRORS as exc:
            proto.abort()
            raise TransportError(str(exc)) from exc
        finally:
            # CancelledError and friends must not leave a half finished call
            # on a connection that goes back to the pool
            if proto.busy:
                proto.abort()

    async def _write(self, data: bytes) -> None:
        assert self._writer is not None
        if data:
            self._writer.write(data)
            await self._writer.drain()

    def _write_quietly(self, data: bytes) -> None:
        """Queue a few control frames without awaiting the drain."""
        if not data or self._writer is None:
            return
        try:
            self._writer.write(data)
        except Exception:
            self._proto.abort()


class AsyncThriftConnection:
    """One framed thrift connection, the asyncio twin of :class:`AsyncGrpcConnection`."""

    def __init__(
        self, host: str, port: int, *, tls: bool, ssl_context: ssl.SSLContext | None = None, service: str | None = None
    ) -> None:
        self._host = host
        self._port = port
        self._tls = tls
        self._ssl_context = ssl_context
        self._proto = ThriftProtocol(service)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self, connect_timeout: float | None) -> "AsyncThriftConnection":
        target = authority(self._host, self._port)
        ctx = (self._ssl_context or tls_context()) if self._tls else None
        try:
            async with asyncio.timeout(connect_timeout):
                self._reader, self._writer = await asyncio.open_connection(
                    self._host, self._port, ssl=ctx, server_hostname=self._host if self._tls else None
                )
        except (OSError, TimeoutError) as exc:
            raise TransportError(f"cannot connect to {target}: {exc}") from exc
        return self

    @property
    def healthy(self) -> bool:
        return self._proto.healthy and self._writer is not None and not self._writer.is_closing()

    async def poll(self) -> None:
        reader = self._reader
        assert reader is not None
        if reader.at_eof():
            self._proto.feed_idle(b"")
            return
        try:
            async with asyncio.timeout(0):
                data = await reader.read(_RECV_SIZE)
        except TimeoutError:
            return
        except IO_ERRORS:
            self._proto.abort()
            return
        self._proto.feed_idle(data)

    def close(self, *, flush: bool = True) -> None:
        self._proto.close()
        if self._writer is not None:
            self._writer.close()

    async def aclose(self) -> None:
        if self._writer is None:
            return
        self._proto.close()
        self._writer.close()
        try:
            async with asyncio.timeout(_CLOSE_TIMEOUT):
                await self._writer.wait_closed()
        except Exception:
            self._writer.transport.abort()

    async def unary(
        self,
        path: str,
        payload: bytes,
        *,
        timeout: float | None,
        metadata: Metadata | None,
        compression: str | None,
    ) -> tuple[bytes, Metadata, Metadata]:
        proto = self._proto
        assert self._reader is not None and self._writer is not None
        try:
            async with asyncio.timeout(timeout):
                proto.start(path, payload)
                self._writer.write(proto.data_to_send())
                await self._writer.drain()
                while not proto.done:
                    proto.feed(await self._reader.read(_RECV_SIZE))
            return proto.result(), {}, {}
        except RpcError:
            raise
        except TimeoutError as exc:
            proto.abort()
            raise RpcError(Status.DEADLINE_EXCEEDED, "deadline exceeded") from exc
        except TransportError:
            proto.abort()
            raise
        except IO_ERRORS as exc:
            proto.abort()
            raise TransportError(str(exc)) from exc
        finally:
            if proto.busy:
                proto.abort()
