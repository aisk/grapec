"""gRPC over HTTP/2, built on the sans-IO ``h2`` library.

:class:`GrpcProtocol` holds all protocol knowledge (HTTP/2 state, gRPC
framing, headers, compression) and never touches a socket. The sync and
async connections in ``_sync.py`` and ``_async.py`` only move bytes between
it and the network.
"""

from __future__ import annotations

import base64
import gzip
import struct as _struct
import zlib
from typing import Any
from urllib.parse import unquote

import h2.config
import h2.connection
import h2.events
import h2.exceptions

from ._errors import RpcError, Status, TransportError

USER_AGENT = "grapec"

_ENCODERS = {
    "gzip": gzip.compress,
    "deflate": zlib.compress,
}
_DECODERS = {
    "identity": lambda data: data,
    "gzip": gzip.decompress,
    "deflate": zlib.decompress,
}
ACCEPT_ENCODING = "identity,gzip,deflate"

Metadata = dict[str, str | bytes]

# errors from the IO layer or h2 that mean the connection is unusable
IO_ERRORS = (OSError, h2.exceptions.H2Error, EOFError)


class GrpcProtocol:
    """Sans-IO gRPC client state for one HTTP/2 connection, one call at a time."""

    def __init__(self, authority: str, *, tls: bool) -> None:
        self._authority = authority
        self._scheme = "https" if tls else "http"
        self._h2 = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
        )
        self._h2.initiate_connection()
        self.dead = False
        self._call: _Call | None = None

    @property
    def healthy(self) -> bool:
        if self.dead:
            return False
        return self._h2.state_machine.state is not h2.connection.ConnectionState.CLOSED

    # -- driving the connection ----------------------------------------------

    def start(
        self,
        path: str,
        payload: bytes,
        *,
        timeout: float | None,
        metadata: Metadata | None,
        compression: str | None,
    ) -> None:
        """Queue one unary call. Follow with ``data_to_send`` / ``feed`` until ``done``."""
        if self._call is not None:
            raise TransportError("connection is busy")
        if compression is not None and compression != "identity":
            try:
                payload = _ENCODERS[compression](payload)
            except KeyError:
                raise ValueError(f"unsupported compression {compression!r}") from None
            flag = 1
        else:
            compression = None
            flag = 0
        stream_id = self._h2.get_next_available_stream_id()
        self._h2.send_headers(stream_id, self._request_headers(path, timeout, metadata, compression))
        self._call = _Call(stream_id, _struct.pack(">BI", flag, len(payload)) + payload)

    def data_to_send(self) -> bytes:
        """Bytes to write to the network, pushes as much request body as flow control allows."""
        call = self._call
        if call is not None and call.pending:
            while call.pending:
                window = min(self._h2.local_flow_control_window(call.stream_id), self._h2.max_outbound_frame_size)
                if window <= 0:
                    break
                chunk, call.pending = call.pending[:window], call.pending[window:]
                self._h2.send_data(call.stream_id, bytes(chunk), end_stream=not call.pending)
        return self._h2.data_to_send()

    def feed(self, data: bytes) -> None:
        """Process bytes read from the network."""
        if not data:
            self.dead = True
            raise TransportError("connection closed by peer")
        call = self._call
        for event in self._h2.receive_data(data):
            if isinstance(event, h2.events.ConnectionTerminated):
                self.dead = True
                raise TransportError(f"connection terminated by peer (error {event.error_code})")
            if call is None or getattr(event, "stream_id", None) != call.stream_id:
                continue
            if isinstance(event, h2.events.DataReceived):
                self._h2.acknowledge_received_data(event.flow_controlled_length, call.stream_id)
                call.data += event.data
            elif isinstance(event, h2.events.ResponseReceived):
                call.headers = dict(event.headers)
            elif isinstance(event, h2.events.TrailersReceived):
                call.trailers = dict(event.headers)
            elif isinstance(event, h2.events.StreamEnded):
                call.ended = True
            elif isinstance(event, h2.events.StreamReset):
                self.dead = True
                raise TransportError(f"stream reset by peer (error {event.error_code})")

    @property
    def done(self) -> bool:
        return self._call is not None and self._call.ended

    def result(self) -> bytes:
        """The response message of the finished call, raises ``RpcError`` for non OK status."""
        call = self._call
        assert call is not None and call.ended
        self._call = None
        return call.finish()

    def abort(self) -> None:
        """Forget the current call and mark the connection unusable."""
        self._call = None
        self.dead = True

    def close(self) -> bytes:
        self.dead = True
        try:
            self._h2.close_connection()
            return self._h2.data_to_send()
        except Exception:
            return b""

    # -- headers ---------------------------------------------------------------

    def _request_headers(
        self, path: str, timeout: float | None, metadata: Metadata | None, compression: str | None
    ) -> list[tuple[str, str]]:
        headers = [
            (":method", "POST"),
            (":scheme", self._scheme),
            (":path", path),
            (":authority", self._authority),
            ("te", "trailers"),
            ("content-type", "application/grpc"),
            ("user-agent", USER_AGENT),
            ("grpc-accept-encoding", ACCEPT_ENCODING),
        ]
        if compression is not None:
            headers.append(("grpc-encoding", compression))
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


class _Call:
    def __init__(self, stream_id: int, body: bytes) -> None:
        self.stream_id = stream_id
        self.pending = memoryview(body)
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

        return _unframe(bytes(self.data), self.headers.get("grpc-encoding", "identity"))


def _unframe(data: bytes, encoding: str) -> bytes:
    if len(data) < 5:
        raise TransportError("response body is truncated")
    compressed, length = _struct.unpack(">BI", data[:5])
    if len(data) != 5 + length:
        raise TransportError("response does not contain exactly one message")
    message = data[5:]
    if not compressed:
        return message
    decoder = _DECODERS.get(encoding)
    if decoder is None:
        raise TransportError(f"unsupported response compression {encoding!r}")
    try:
        return decoder(message)
    except (OSError, zlib.error, EOFError) as exc:
        raise TransportError(f"cannot decompress response: {exc}") from exc


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


def tls_context() -> Any:
    import ssl

    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["h2"])
    return ctx
