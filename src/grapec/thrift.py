"""The thrift binary protocol, struct codec and RPC message layer.

Field ids, requiredness and the container types map directly onto the
schema. Things thrift has no counterpart for (``datetime``, ``timedelta``)
are rejected when the struct is first used with this codec.

:class:`ThriftProtocol` is the sans-IO call state for one framed
connection, the sync and async shells in ``sync.py`` and ``aio.py`` move
its bytes. Only ``TBinaryProtocol`` over ``TFramedTransport`` is spoken,
one call at a time per connection.
"""

from __future__ import annotations

import struct as _struct
from typing import Any

from .errors import GrapecError, RpcError, Status, TransportError
from .protobuf import EncodeError, _check, _check_int
from .schema import (
    DurationType,
    EnumType,
    FieldSpec,
    ListType,
    MapType,
    Member,
    OneOfType,
    ScalarType,
    SchemaError,
    StructSchema,
    StructType,
    TimestampType,
    TypeSpec,
    _enum_value,
    schema_of,
    zero_value,
)

STOP = 0
BOOL = 2
BYTE = 3
DOUBLE = 4
I16 = 6
I32 = 8
I64 = 10
STRING = 11
STRUCT = 12
MAP = 13
SET = 14
LIST = 15

MAX_FIELD_ID = (1 << 15) - 1

_INT_TYPES = {8: BYTE, 16: I16, 32: I32, 64: I64}
_INT_STRUCTS = {BYTE: _struct.Struct(">b"), I16: _struct.Struct(">h"), I32: _struct.Struct(">i"), I64: _struct.Struct(">q")}
_HEADER = _struct.Struct(">Bh")
_FIELD_ID = _struct.Struct(">h")
_I32 = _struct.Struct(">i")
_DOUBLE = _struct.Struct(">d")


class ThriftError(GrapecError, ValueError):
    """Raised when bytes do not follow the thrift binary protocol."""


# ---------------------------------------------------------------------------
# schema checks


_checked: set[type] = set()


def check_schema(schema: StructSchema) -> None:
    """Reject structs that cannot be expressed in thrift, once per class."""
    if schema.cls in _checked:
        return
    _checked.add(schema.cls)  # before the walk, structs may reference themselves
    try:
        for field in schema.fields:
            where = f"{schema.cls.__qualname__}.{field.name}"
            for number in field.numbers():
                if number > MAX_FIELD_ID:
                    raise SchemaError(f"{where}: field id {number} does not fit in thrift's i16")
            _check_type(field.type, where)
    except SchemaError:
        _checked.discard(schema.cls)
        raise


def _check_type(spec: TypeSpec, where: str) -> None:
    match spec:
        case TimestampType() | DurationType():
            raise SchemaError(f"{where}: datetime and timedelta have no thrift counterpart")
        case ListType(item):
            _check_type(item, where)
        case MapType(key, value):
            _check_type(key, where)
            _check_type(value, where)
        case OneOfType(members):
            for m in members:
                _check_type(m.type, where)
        case StructType(cls):
            check_schema(schema_of(cls))


# ---------------------------------------------------------------------------
# encoding


def encode(obj: Any) -> bytes:
    schema = schema_of(type(obj))
    check_schema(schema)
    out = bytearray()
    _encode_struct(out, schema, obj)
    return bytes(out)


def _encode_struct(out: bytearray, schema: StructSchema, obj: Any) -> None:
    encode_fields(out, schema.fields, {f.name: getattr(obj, f.name) for f in schema.fields}, schema.cls.__qualname__)


def encode_fields(out: bytearray, fields: tuple[FieldSpec, ...], values: dict[str, Any], owner: str) -> None:
    """Write ``values`` as a struct body (fields then STOP), also used for call arguments."""
    for field in fields:
        _encode_field(out, field, values[field.name], where=f"{owner}.{field.name}")
    out.append(STOP)


def _encode_field(out: bytearray, field: FieldSpec, value: Any, *, where: str) -> None:
    spec = field.type
    if value is None:
        if field.optional or isinstance(spec, (ListType, MapType)):
            return
        raise EncodeError(f"{where}: None is not allowed, declare the field as `T | None`")
    if isinstance(spec, OneOfType):
        member = spec.pick(value)
        if member is None:
            raise EncodeError(f"{where}: {type(value).__qualname__} is not one of the union members")
        out += _HEADER.pack(type_of(member.type), member.number)
        _encode_value(out, member.type, value, where)
        return
    out += _HEADER.pack(type_of(spec), field.number)
    _encode_value(out, spec, value, where)


def type_of(spec: TypeSpec) -> int:
    match spec:
        case ScalarType(kind="int", width=width):
            return _INT_TYPES[width]
        case ScalarType(kind="bool"):
            return BOOL
        case ScalarType(kind="float"):
            return DOUBLE
        case ScalarType(kind="str") | ScalarType(kind="bytes"):
            return STRING
        case EnumType():
            return I32
        case StructType():
            return STRUCT
        case ListType():
            return LIST
        case MapType():
            return MAP
    raise AssertionError(spec)


def _encode_value(out: bytearray, spec: TypeSpec, value: Any, where: str) -> None:
    match spec:
        case ScalarType(kind="int", width=width):
            n = _check_int(value, where)
            if not -(1 << (width - 1)) <= n < (1 << (width - 1)):
                raise EncodeError(f"{where}: {n} does not fit in {width} bits")
            out += _INT_STRUCTS[_INT_TYPES[width]].pack(n)
        case EnumType():
            n = _check_int(value, where)
            if not -(1 << 31) <= n < (1 << 31):
                raise EncodeError(f"{where}: enum value {n} does not fit in 32 bits")
            out += _I32.pack(n)
        case ScalarType(kind="bool"):
            out.append(1 if _check(value, bool, where) else 0)
        case ScalarType(kind="float"):
            out += _DOUBLE.pack(_check(value, (int, float), where))
        case ScalarType(kind="str"):
            _encode_binary(out, _check(value, str, where).encode("utf-8"))
        case ScalarType(kind="bytes"):
            _encode_binary(out, bytes(_check(value, (bytes, bytearray, memoryview), where)))
        case StructType(cls):
            if not isinstance(value, cls):
                raise EncodeError(f"{where}: expected {cls.__qualname__}, got {type(value).__qualname__}")
            _encode_struct(out, schema_of(cls), value)
        case ListType(item):
            items = _check(value, (list, tuple), where)
            out.append(type_of(item))
            out += _I32.pack(len(items))
            for v in items:
                _encode_value(out, item, v, where)
        case MapType(key, val):
            entries = _check(value, dict, where)
            out.append(type_of(key))
            out.append(type_of(val))
            out += _I32.pack(len(entries))
            for k, v in entries.items():
                _encode_value(out, key, k, where)
                _encode_value(out, val, v, where)
        case _:
            raise AssertionError(spec)


def _encode_binary(out: bytearray, data: bytes) -> None:
    out += _I32.pack(len(data))
    out += data


# ---------------------------------------------------------------------------
# decoding


def decode(cls: type, data: bytes | bytearray | memoryview) -> Any:
    schema = schema_of(cls)
    check_schema(schema)
    value, pos = decode_struct(schema, bytes(data), 0)
    if pos != len(data):
        raise ThriftError(f"{len(data) - pos} trailing bytes after struct")
    return value


def decode_struct(schema: StructSchema, buf: bytes, pos: int) -> tuple[Any, int]:
    values, pos = decode_fields(schema.by_number, buf, pos)
    kwargs = {}
    for field in schema.fields:
        if field.name in values:
            kwargs[field.name] = values[field.name]
        elif field.optional:
            kwargs[field.name] = None
        else:
            kwargs[field.name] = zero_value(field.type)
    return schema.cls(**kwargs), pos


def index_fields(fields: tuple[FieldSpec, ...]) -> dict[int, tuple[FieldSpec, Member | None]]:
    out: dict[int, tuple[FieldSpec, Member | None]] = {}
    for f in fields:
        if isinstance(f.type, OneOfType):
            for m in f.type.members:
                out[m.number] = (f, m)
        else:
            out[f.number] = (f, None)
    return out


def decode_fields(by_number: dict[int, tuple[FieldSpec, Member | None]], buf: bytes, pos: int) -> tuple[dict[str, Any], int]:
    """Read a struct body up to and including STOP, returning the fields that were present."""
    values: dict[str, Any] = {}
    while True:
        ttype, pos = _read_byte(buf, pos)
        if ttype == STOP:
            break
        number, pos = _read(_FIELD_ID, buf, pos)
        entry = by_number.get(number)
        if entry is None:
            pos = skip(buf, pos, ttype)
            continue
        field, member = entry
        spec = member.type if member is not None else field.type
        values[field.name], pos = _decode_value(spec, buf, pos, ttype)
    return values, pos


def _decode_value(spec: TypeSpec, buf: bytes, pos: int, ttype: int) -> tuple[Any, int]:
    expected = type_of(spec)
    if ttype != expected and not (expected == LIST and ttype == SET):
        raise ThriftError(f"type {ttype} does not match declared type {expected}")
    match spec:
        case ScalarType(kind="int"):
            return _read(_INT_STRUCTS[ttype], buf, pos)
        case EnumType(cls):
            raw, pos = _read(_I32, buf, pos)
            return _enum_value(cls, raw), pos
        case ScalarType(kind="bool"):
            raw, pos = _read_byte(buf, pos)
            return raw != 0, pos
        case ScalarType(kind="float"):
            return _read(_DOUBLE, buf, pos)
        case ScalarType(kind="str"):
            raw, pos = _read_binary(buf, pos)
            return raw.decode("utf-8"), pos
        case ScalarType(kind="bytes"):
            return _read_binary(buf, pos)
        case StructType(cls):
            return decode_struct(schema_of(cls), buf, pos)
        case ListType(item):
            etype, pos = _read_byte(buf, pos)
            count, pos = _read(_I32, buf, pos)
            out = []
            for _ in range(_check_count(count)):
                v, pos = _decode_value(item, buf, pos, etype)
                out.append(v)
            return out, pos
        case MapType(key, val):
            ktype, pos = _read_byte(buf, pos)
            vtype, pos = _read_byte(buf, pos)
            count, pos = _read(_I32, buf, pos)
            entries = {}
            for _ in range(_check_count(count)):
                k, pos = _decode_value(key, buf, pos, ktype)
                v, pos = _decode_value(val, buf, pos, vtype)
                entries[k] = v
            return entries, pos
    raise AssertionError(spec)


_SIZES = {BOOL: 1, BYTE: 1, DOUBLE: 8, I16: 2, I32: 4, I64: 8}


def skip(buf: bytes, pos: int, ttype: int) -> int:
    """Skip one value of type ``ttype`` starting at ``pos``."""
    if ttype in _SIZES:
        return _need(buf, pos, _SIZES[ttype])
    if ttype == STRING:
        _, pos = _read_binary(buf, pos)
        return pos
    if ttype == STRUCT:
        while True:
            inner, pos = _read_byte(buf, pos)
            if inner == STOP:
                return pos
            pos = _need(buf, pos, 2)
            pos = skip(buf, pos, inner)
    if ttype in (LIST, SET):
        etype, pos = _read_byte(buf, pos)
        count, pos = _read(_I32, buf, pos)
        for _ in range(_check_count(count)):
            pos = skip(buf, pos, etype)
        return pos
    if ttype == MAP:
        ktype, pos = _read_byte(buf, pos)
        vtype, pos = _read_byte(buf, pos)
        count, pos = _read(_I32, buf, pos)
        for _ in range(_check_count(count)):
            pos = skip(buf, pos, ktype)
            pos = skip(buf, pos, vtype)
        return pos
    raise ThriftError(f"unknown type {ttype}")


def _need(buf: bytes, pos: int, size: int) -> int:
    end = pos + size
    if end > len(buf):
        raise ThriftError("truncated input")
    return end


def _read(st: _struct.Struct, buf: bytes, pos: int) -> tuple[Any, int]:
    end = _need(buf, pos, st.size)
    return st.unpack_from(buf, pos)[0], end


def _read_byte(buf: bytes, pos: int) -> tuple[int, int]:
    end = _need(buf, pos, 1)
    return buf[pos], end


def _read_binary(buf: bytes, pos: int) -> tuple[bytes, int]:
    size, pos = _read(_I32, buf, pos)
    end = _need(buf, pos, _check_count(size))
    return buf[pos:end], end


def _check_count(count: int) -> int:
    if count < 0:
        raise ThriftError(f"negative size {count}")
    return count


# ---------------------------------------------------------------------------
# RPC messages, TBinaryProtocol strict encoding over TFramedTransport

CALL = 1
REPLY = 2
EXCEPTION = 3
ONEWAY = 4

VERSION_1 = 0x80010000
_VERSION_MASK = 0xFFFF0000
_FRAME_LEN = _struct.Struct(">I")
_U32 = _struct.Struct(">I")
MAX_FRAME = 64 * 1024 * 1024

# TApplicationException types to grapec status codes
_APP_EXCEPTION_STATUS = {
    0: Status.UNKNOWN,  # UNKNOWN
    1: Status.UNIMPLEMENTED,  # UNKNOWN_METHOD
    2: Status.INTERNAL,  # INVALID_MESSAGE_TYPE
    3: Status.INTERNAL,  # WRONG_METHOD_NAME
    4: Status.INTERNAL,  # BAD_SEQUENCE_ID
    5: Status.UNKNOWN,  # MISSING_RESULT
    6: Status.INTERNAL,  # INTERNAL_ERROR
    7: Status.INTERNAL,  # PROTOCOL_ERROR
    8: Status.INTERNAL,  # INVALID_TRANSFORM
    9: Status.INTERNAL,  # INVALID_PROTOCOL
    10: Status.UNIMPLEMENTED,  # UNSUPPORTED_CLIENT_TYPE
}
_APP_EXCEPTION_NAMES = {
    0: "UNKNOWN", 1: "UNKNOWN_METHOD", 2: "INVALID_MESSAGE_TYPE", 3: "WRONG_METHOD_NAME",
    4: "BAD_SEQUENCE_ID", 5: "MISSING_RESULT", 6: "INTERNAL_ERROR", 7: "PROTOCOL_ERROR",
    8: "INVALID_TRANSFORM", 9: "INVALID_PROTOCOL", 10: "UNSUPPORTED_CLIENT_TYPE",
}


def encode_message(name: str, mtype: int, seqid: int, body: bytes) -> bytes:
    """One framed message: length prefix, strict header, body."""
    raw = name.encode("utf-8")
    message = _U32.pack(VERSION_1 | mtype) + _I32.pack(len(raw)) + raw + _I32.pack(seqid) + body
    return _FRAME_LEN.pack(len(message)) + message


def decode_message(frame: bytes) -> tuple[str, int, int, bytes]:
    """``(name, type, seqid, body)`` of one unframed message. Raises ``ThriftError``."""
    header, pos = _read(_U32, frame, 0)
    if header & _VERSION_MASK != VERSION_1:
        raise ThriftError(f"bad message version {header:#x}, only the strict binary protocol is supported")
    mtype = header & 0xFF
    raw, pos = _read_binary(frame, pos)
    seqid, pos = _read(_I32, frame, pos)
    return raw.decode("utf-8", "replace"), mtype, seqid, frame[pos:]


def application_error(body: bytes) -> RpcError:
    """Turn a TApplicationException struct into an ``RpcError``."""
    message = ""
    kind = 0
    pos = 0
    while True:
        ttype, pos = _read_byte(body, pos)
        if ttype == STOP:
            break
        number, pos = _read(_FIELD_ID, body, pos)
        if number == 1 and ttype == STRING:
            raw, pos = _read_binary(body, pos)
            message = raw.decode("utf-8", "replace")
        elif number == 2 and ttype == I32:
            kind, pos = _read(_I32, body, pos)
        else:
            pos = skip(body, pos, ttype)
    code = _APP_EXCEPTION_STATUS.get(kind, Status.UNKNOWN)
    name = _APP_EXCEPTION_NAMES.get(kind, str(kind))
    return RpcError(code, f"{name}: {message}" if message else name)


_methods: dict[Any, tuple[tuple[FieldSpec, ...], tuple[FieldSpec, ...], dict[int, Any]]] = {}


def method_fields(spec: Any) -> tuple[tuple[FieldSpec, ...], tuple[FieldSpec, ...], dict[int, Any]]:
    """``(args fields, result fields, result index)`` of a ``MethodSpec``, validated once."""
    cached = _methods.get(spec)
    if cached is not None:
        return cached
    where = f"{spec.service.cls.__qualname__}.{spec.python_name}"
    args = tuple(FieldSpec(p.name, p.number, p.type, p.optional) for p in spec.params)
    for field in args:
        if field.number > MAX_FIELD_ID:
            raise SchemaError(f"{where}: parameter id {field.number} does not fit in thrift's i16")
        _check_type(field.type, f"{where}({field.name})")
    result: list[FieldSpec] = []
    if spec.returns is not None:
        _check_type(spec.returns, f"{where} return")
        result.append(FieldSpec("success", 0, spec.returns, True))
    for number, exc in spec.raises:
        if number > MAX_FIELD_ID:
            raise SchemaError(f"{where}: exception id {number} does not fit in thrift's i16")
        check_schema(schema_of(exc))
        result.append(FieldSpec(f"exception{number}", number, StructType(exc), True))
    cached = (args, tuple(result), index_fields(tuple(result)))
    _methods[spec] = cached
    return cached


def encode_call(spec: Any, arguments: dict[str, Any]) -> bytes:
    """The args struct of a call, ``arguments`` maps parameter names to values."""
    args, _, _ = method_fields(spec)
    out = bytearray()
    encode_fields(out, args, arguments, f"{spec.service.cls.__qualname__}.{spec.python_name}")
    return bytes(out)


def decode_result(spec: Any, body: bytes) -> Any:
    """Return value of a REPLY body, raises the declared exception struct if one was set."""
    _, result, by_number = method_fields(spec)
    try:
        values, pos = decode_fields(by_number, body, 0)
        if pos != len(body):
            raise ThriftError(f"{len(body) - pos} trailing bytes after result")
    except (ThriftError, UnicodeDecodeError) as exc:
        raise RpcError(Status.INTERNAL, f"malformed reply for {spec.name}: {exc}") from exc
    for field in result[1:] if result and result[0].number == 0 else result:
        exc = values.get(field.name)
        if exc is not None:
            raise exc
    if spec.returns is None:
        return None
    if "success" in values:
        return values["success"]
    raise RpcError(Status.UNKNOWN, f"MISSING_RESULT: {spec.name} returned neither a value nor a declared exception")


class ThriftProtocol:
    """Sans-IO client state for one framed thrift connection, one call at a time."""

    def __init__(self, service: str | None = None) -> None:
        self._service = service  # set for TMultiplexedProtocol, prefixes method names
        self._seqid = 0
        self._inbuf = bytearray()
        self._out = b""
        self._call: tuple[str, int] | None = None
        self._reply: tuple[int, bytes] | None = None
        self.dead = False

    @property
    def healthy(self) -> bool:
        return not self.dead

    @property
    def busy(self) -> bool:
        return self._call is not None

    @property
    def done(self) -> bool:
        return self._reply is not None

    def start(self, method: str, args: bytes) -> None:
        """Queue one call. Follow with ``data_to_send`` / ``feed`` until ``done``."""
        if self._call is not None:
            raise TransportError("connection is busy")
        if self.dead:
            raise TransportError("connection is closed")
        self._seqid = (self._seqid + 1) & 0x7FFFFFFF
        name = f"{self._service}:{method}" if self._service else method
        self._call = (method, self._seqid)
        self._reply = None
        self._out = encode_message(name, CALL, self._seqid, args)

    def data_to_send(self) -> bytes:
        out, self._out = self._out, b""
        return out

    def feed(self, data: bytes) -> None:
        """Process bytes read from the network, raises ``TransportError`` on a dead connection."""
        if not data:
            self.dead = True
            raise TransportError("connection closed by peer")
        if self._call is None:
            # nothing was asked, a framed server never speaks first
            self.dead = True
            raise TransportError("unexpected data on an idle connection")
        self._inbuf += data
        if len(self._inbuf) < _FRAME_LEN.size:
            return
        length = _FRAME_LEN.unpack_from(self._inbuf)[0]
        if length > MAX_FRAME:
            self.dead = True
            raise TransportError(f"frame of {length} bytes exceeds the {MAX_FRAME} byte limit")
        if len(self._inbuf) < _FRAME_LEN.size + length:
            return
        frame = bytes(self._inbuf[_FRAME_LEN.size : _FRAME_LEN.size + length])
        del self._inbuf[: _FRAME_LEN.size + length]
        if self._inbuf:
            self.dead = True
            raise TransportError("server sent more than one reply")
        try:
            name, mtype, seqid, body = decode_message(frame)
        except ThriftError as exc:
            self.dead = True
            raise TransportError(str(exc)) from exc
        method, expected = self._call
        if seqid != expected:
            self.dead = True
            raise TransportError(f"reply seqid {seqid} does not match request {expected}")
        if mtype not in (REPLY, EXCEPTION):
            self.dead = True
            raise TransportError(f"unexpected message type {mtype}")
        if name.rpartition(":")[2] != method:
            self.dead = True
            raise TransportError(f"reply for {name!r} while waiting for {method!r}")
        self._reply = (mtype, body)

    def feed_idle(self, data: bytes) -> None:
        """``feed`` for bytes that arrived while no call was active, never raises."""
        try:
            self.feed(data)
        except TransportError:
            pass

    def result(self) -> bytes:
        """Body of the finished call, the result struct. Raises ``RpcError`` for a server side exception."""
        assert self._reply is not None
        mtype, body = self._reply
        self._call = self._reply = None
        if mtype == EXCEPTION:
            try:
                raise application_error(body)
            except ThriftError as exc:
                raise RpcError(Status.INTERNAL, f"malformed TApplicationException: {exc}") from exc
        return body

    def abort(self) -> None:
        """Forget the current call and mark the connection unusable."""
        self._call = self._reply = None
        self._inbuf.clear()
        self.dead = True

    def close(self) -> None:
        self.dead = True
