"""gRPC over HTTP/2 transport, built on the sans-IO ``h2`` library.

This module is the only place that knows about gRPC framing and headers.
The client talks to it through the small :class:`Connection` interface so
other protocols can be added next to it.
"""

from __future__ import annotations

import base64
import socket
import ssl
import struct as _struct
import time
from typing import Any
from urllib.parse import unquote

import h2.config
import h2.connection
import h2.events
import h2.exceptions

from ._errors import RpcError, Status, TransportError

USER_AGENT = "grapec"
_RECV_SIZE = 65536

Metadata = dict[str, str | bytes]


class GrpcConnection:
    """One HTTP/2 connection carrying one call at a time."""

    def __init__(self, host: str, port: int, *, tls: bool, connect_timeout: float | None) -> None:
        self._authority = f"{host}:{port}"
        self._scheme = "https" if tls else "http"
        try:
            sock = socket.create_connection((host, port), timeout=connect_timeout)
            if tls:
                ctx = ssl.create_default_context()
                ctx.set_alpn_protocols(["h2"])
                sock = ctx.wrap_socket(sock, server_hostname=host)
                if sock.selected_alpn_protocol() != "h2":
                    sock.close()
                    raise TransportError("server did not negotiate HTTP/2")
        except OSError as exc:
            raise TransportError(f"cannot connect to {self._authority}: {exc}") from exc
        self._sock = sock
        self._h2 = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
        )
        self._h2.initiate_connection()
        self._flush()
        self._dead = False

    # -- Connection interface -------------------------------------------------

    @property
    def healthy(self) -> bool:
        if self._dead:
            return False
        state = self._h2.state_machine.state
        return state is not h2.connection.ConnectionState.CLOSED

    def close(self) -> None:
        self._dead = True
        try:
            self._h2.close_connection()
            self._flush()
        except Exception:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def unary(self, path: str, payload: bytes, *, timeout: float | None, metadata: Metadata | None) -> bytes:
        """Send one request message, return the single response message."""
        try:
            return self._unary(path, payload, timeout, metadata)
        except RpcError:
            raise
        except TransportError:
            self._dead = True
            raise
        except socket.timeout as exc:
            self._dead = True
            raise RpcError(Status.DEADLINE_EXCEEDED, "deadline exceeded") from exc
        except (OSError, h2.exceptions.H2Error) as exc:
            self._dead = True
            raise TransportError(str(exc)) from exc

    # -- implementation -------------------------------------------------------

    def _unary(self, path: str, payload: bytes, timeout: float | None, metadata: Metadata | None) -> bytes:
        deadline = time.monotonic() + timeout if timeout is not None else None
        self._sock.settimeout(timeout)

        stream_id = self._h2.get_next_available_stream_id()
        self._h2.send_headers(stream_id, self._request_headers(path, timeout, metadata))
        self._send_body(stream_id, _struct.pack(">BI", 0, len(payload)) + payload, deadline)

        call = _Call()
        while not call.ended:
            self._pump(call, stream_id, deadline)

        return call.finish()

    def _request_headers(self, path: str, timeout: float | None, metadata: Metadata | None) -> list[tuple[str, str]]:
        headers = [
            (":method", "POST"),
            (":scheme", self._scheme),
            (":path", path),
            (":authority", self._authority),
            ("te", "trailers"),
            ("content-type", "application/grpc"),
            ("user-agent", USER_AGENT),
            ("grpc-accept-encoding", "identity"),
        ]
        if timeout is not None:
            headers.append(("grpc-timeout", _format_timeout(timeout)))
        for key, value in (metadata or {}).items():
            key = key.lower()
            if key.startswith(":") or key.startswith("grpc-"):
                raise ValueError(f"metadata key {key!r} is reserved")
            if isinstance(value, bytes):
                if not key.endswith("-bin"):
                    raise ValueError(f"binary metadata key {key!r} must end with -bin")
                headers.append((key, base64.b64encode(value).decode("ascii")))
            else:
                headers.append((key, value))
        return headers

    def _send_body(self, stream_id: int, body: bytes, deadline: float | None) -> None:
        view = memoryview(body)
        while view:
            window = min(self._h2.local_flow_control_window(stream_id), self._h2.max_outbound_frame_size)
            if window <= 0:
                self._pump(None, stream_id, deadline)
                continue
            chunk = view[:window]
            view = view[window:]
            self._h2.send_data(stream_id, bytes(chunk), end_stream=not view)
            self._flush()

    def _pump(self, call: _Call | None, stream_id: int, deadline: float | None) -> None:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout()
            self._sock.settimeout(remaining)
        data = self._sock.recv(_RECV_SIZE)
        if not data:
            raise TransportError("connection closed by peer")
        for event in self._h2.receive_data(data):
            if isinstance(event, h2.events.ConnectionTerminated):
                self._dead = True
                raise TransportError(f"connection terminated by peer (error {event.error_code})")
            if getattr(event, "stream_id", None) != stream_id:
                continue
            if isinstance(event, h2.events.DataReceived):
                self._h2.acknowledge_received_data(event.flow_controlled_length, stream_id)
                if call is not None:
                    call.data += event.data
            elif isinstance(event, h2.events.ResponseReceived) and call is not None:
                call.headers = dict(event.headers)
            elif isinstance(event, h2.events.TrailersReceived) and call is not None:
                call.trailers = dict(event.headers)
            elif isinstance(event, h2.events.StreamEnded) and call is not None:
                call.ended = True
            elif isinstance(event, h2.events.StreamReset):
                raise TransportError(f"stream reset by peer (error {event.error_code})")
        self._flush()

    def _flush(self) -> None:
        data = self._h2.data_to_send()
        if data:
            self._sock.sendall(data)


class _Call:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.trailers: dict[str, str] = {}
        self.data = bytearray()
        self.ended = False

    def finish(self) -> bytes:
        http_status = self.headers.get(":status")
        if http_status != "200":
            raise RpcError(_status_from_http(http_status), f"unexpected HTTP status {http_status}")

        # trailers-only responses carry grpc-status in the headers
        meta = self.trailers or self.headers
        raw_status = meta.get("grpc-status")
        if raw_status is None:
            raise TransportError("response is missing grpc-status")
        code = _status_code(raw_status)
        message = unquote(meta.get("grpc-message", ""))
        details_b64 = meta.get("grpc-status-details-bin", "")
        details = base64.b64decode(details_b64 + "=" * (-len(details_b64) % 4)) if details_b64 else b""
        if code is not Status.OK:
            raise RpcError(code, message, details)

        return _unframe(bytes(self.data))


def _unframe(data: bytes) -> bytes:
    if len(data) < 5:
        raise TransportError("response body is truncated")
    compressed, length = _struct.unpack(">BI", data[:5])
    if compressed:
        raise TransportError("compressed responses are not supported")
    if len(data) != 5 + length:
        raise TransportError("response does not contain exactly one message")
    return data[5:]


def _status_code(raw: str) -> Status:
    try:
        return Status(int(raw))
    except ValueError:
        return Status.UNKNOWN


def _status_from_http(http_status: str | None) -> Status:
    mapping = {
        "400": Status.INTERNAL,
        "401": Status.UNAUTHENTICATED,
        "403": Status.PERMISSION_DENIED,
        "404": Status.UNIMPLEMENTED,
        "429": Status.UNAVAILABLE,
        "502": Status.UNAVAILABLE,
        "503": Status.UNAVAILABLE,
        "504": Status.UNAVAILABLE,
    }
    return mapping.get(http_status or "", Status.UNKNOWN)


def _format_timeout(seconds: float) -> str:
    # grpc-timeout carries at most 8 digits, pick the coarsest unit that fits
    for unit, factor in (("n", 1e9), ("u", 1e6), ("m", 1e3), ("S", 1), ("M", 1 / 60), ("H", 1 / 3600)):
        value = int(seconds * factor)
        if value < 100_000_000:
            return f"{max(value, 1)}{unit}"
    return "99999999H"
