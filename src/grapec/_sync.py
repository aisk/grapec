"""Blocking socket transport for :class:`GrpcProtocol`."""

from __future__ import annotations

import socket
import ssl
import time
from typing import Any

from ._errors import RpcError, Status, TransportError
from ._grpc import IO_ERRORS, GrpcProtocol, Metadata, authority, tls_context

_RECV_SIZE = 65536


class GrpcConnection:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        tls: bool,
        connect_timeout: float | None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        target = authority(host, port)
        sock: Any = None
        try:
            sock = socket.create_connection((host, port), timeout=connect_timeout)
            if tls:
                ctx = ssl_context or tls_context()
                ctx.set_alpn_protocols(["h2"])
                sock = ctx.wrap_socket(sock, server_hostname=host)
                if sock.selected_alpn_protocol() != "h2":
                    raise TransportError("server did not negotiate HTTP/2")
            self._proto = GrpcProtocol(target, tls=tls)
            sock.sendall(self._proto.data_to_send())
        except (OSError, TransportError) as exc:
            if sock is not None:
                sock.close()
            if isinstance(exc, TransportError):
                raise
            raise TransportError(f"cannot connect to {target}: {exc}") from exc
        self._sock = sock

    @property
    def healthy(self) -> bool:
        return self._proto.healthy

    def poll(self) -> None:
        """Process whatever the peer sent while the connection was idle.

        Never blocks. A GOAWAY or a closed socket marks the connection
        unhealthy so the pool drops it instead of handing it out.
        """
        proto = self._proto
        try:
            self._sock.settimeout(0)
            while proto.healthy:
                data = self._sock.recv(_RECV_SIZE)
                proto.feed_idle(data)
                if not data:
                    break
        except (BlockingIOError, ssl.SSLWantReadError):
            pass
        except IO_ERRORS:
            proto.abort()
        if proto.healthy:
            self._send_quietly(proto.data_to_send())

    def close(self, *, flush: bool = True) -> None:
        if flush:
            self._send_quietly(self._proto.close())
        else:
            self._proto.close()
        try:
            self._sock.close()
        except OSError:
            pass

    def _send_quietly(self, data: bytes) -> None:
        """Best effort non blocking send of a few control frames."""
        if not data:
            return
        try:
            self._sock.settimeout(0)
            self._sock.sendall(data)
        except IO_ERRORS:
            self._proto.abort()

    def unary(
        self,
        path: str,
        payload: bytes,
        *,
        timeout: float | None,
        metadata: Metadata | None,
        compression: str | None,
    ) -> tuple[bytes, Metadata, Metadata]:
        proto = self._proto
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            # one timeout per call, the loop below only shortens it as the deadline nears
            self._sock.settimeout(timeout)
            proto.start(path, payload, timeout=timeout, metadata=metadata, compression=compression)
            while True:
                out = proto.data_to_send()
                if out:
                    self._sock.sendall(out)
                if proto.done:
                    break
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise socket.timeout()
                    self._sock.settimeout(remaining)
                proto.feed(self._sock.recv(_RECV_SIZE))
            return proto.result()
        except RpcError:
            raise
        except socket.timeout as exc:
            self._send_quietly(proto.cancel())
            raise RpcError(Status.DEADLINE_EXCEEDED, "deadline exceeded") from exc
        except TransportError:
            proto.abort()
            raise
        except IO_ERRORS as exc:
            proto.abort()
            raise TransportError(str(exc)) from exc
        finally:
            # any other exit (KeyboardInterrupt, bugs) must not leave a half
            # finished call on a connection that goes back to the pool
            if proto.busy:
                proto.abort()
