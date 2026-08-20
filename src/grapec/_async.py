"""asyncio transport for :class:`GrpcProtocol`."""

from __future__ import annotations

import asyncio

from ._errors import RpcError, Status, TransportError
from ._grpc import IO_ERRORS, GrpcProtocol, Metadata, tls_context

_RECV_SIZE = 65536


class AsyncGrpcConnection:
    def __init__(self, host: str, port: int, *, tls: bool) -> None:
        self._host = host
        self._port = port
        self._tls = tls
        self._proto = GrpcProtocol(f"{host}:{port}", tls=tls)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self, connect_timeout: float | None) -> "AsyncGrpcConnection":
        authority = f"{self._host}:{self._port}"
        try:
            async with asyncio.timeout(connect_timeout):
                self._reader, self._writer = await asyncio.open_connection(
                    self._host,
                    self._port,
                    ssl=tls_context() if self._tls else None,
                    server_hostname=self._host if self._tls else None,
                )
            if self._tls:
                ssl_obj = self._writer.get_extra_info("ssl_object")
                if ssl_obj is None or ssl_obj.selected_alpn_protocol() != "h2":
                    self._writer.close()
                    raise TransportError("server did not negotiate HTTP/2")
            await self._write(self._proto.data_to_send())
        except (OSError, TimeoutError) as exc:
            raise TransportError(f"cannot connect to {authority}: {exc}") from exc
        return self

    @property
    def healthy(self) -> bool:
        return self._proto.healthy and self._writer is not None and not self._writer.is_closing()

    def close(self) -> None:
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
            await self._writer.wait_closed()
        except Exception:
            pass

    async def unary(
        self,
        path: str,
        payload: bytes,
        *,
        timeout: float | None,
        metadata: Metadata | None,
        compression: str | None,
    ) -> bytes:
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
            proto.abort()
            raise RpcError(Status.DEADLINE_EXCEEDED, "deadline exceeded") from exc
        except TransportError:
            proto.abort()
            raise
        except IO_ERRORS as exc:
            proto.abort()
            raise TransportError(str(exc)) from exc

    async def _write(self, data: bytes) -> None:
        assert self._writer is not None
        if data:
            self._writer.write(data)
            await self._writer.drain()
