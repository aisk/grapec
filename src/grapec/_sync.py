"""Blocking socket transport for :class:`GrpcProtocol`."""

from __future__ import annotations

import socket
import time

from ._errors import RpcError, Status, TransportError
from ._grpc import IO_ERRORS, GrpcProtocol, Metadata, tls_context

_RECV_SIZE = 65536


class GrpcConnection:
    def __init__(self, host: str, port: int, *, tls: bool, connect_timeout: float | None) -> None:
        authority = f"{host}:{port}"
        try:
            sock = socket.create_connection((host, port), timeout=connect_timeout)
            if tls:
                sock = tls_context().wrap_socket(sock, server_hostname=host)
                if sock.selected_alpn_protocol() != "h2":
                    sock.close()
                    raise TransportError("server did not negotiate HTTP/2")
        except OSError as exc:
            raise TransportError(f"cannot connect to {authority}: {exc}") from exc
        self._sock = sock
        self._proto = GrpcProtocol(authority, tls=tls)
        self._sock.sendall(self._proto.data_to_send())

    @property
    def healthy(self) -> bool:
        return self._proto.healthy

    def close(self) -> None:
        try:
            self._sock.sendall(self._proto.close())
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def unary(
        self,
        path: str,
        payload: bytes,
        *,
        timeout: float | None,
        metadata: Metadata | None,
        compression: str | None,
    ) -> bytes:
        proto = self._proto
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
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
                else:
                    self._sock.settimeout(None)
                proto.feed(self._sock.recv(_RECV_SIZE))
            return proto.result()
        except RpcError:
            raise
        except socket.timeout as exc:
            proto.abort()
            raise RpcError(Status.DEADLINE_EXCEEDED, "deadline exceeded") from exc
        except TransportError:
            proto.abort()
            raise
        except IO_ERRORS as exc:
            proto.abort()
            raise TransportError(str(exc)) from exc
