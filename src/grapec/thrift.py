"""Encode and decode struct instances using the thrift binary protocol.

Only the struct encoding lives here, message framing for RPC is separate.
Field ids, requiredness and the container types map directly onto the
schema. Things thrift has no counterpart for (``datetime``, ``timedelta``)
are rejected when the struct is first used with this codec.
"""

from __future__ import annotations

import struct as _struct
from typing import Any

from .protobuf import EncodeError, _check, _check_int
from .schema import (
    DurationType,
    EnumType,
    FieldSpec,
    ListType,
    MapType,
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


class ThriftError(ValueError):
    """Raised when bytes do not follow the thrift binary protocol."""


# ---------------------------------------------------------------------------
# schema checks


_checked: set[type] = set()


def check_schema(schema: StructSchema) -> None:
    """Reject structs that cannot be expressed in thrift, once per class."""
    if schema.cls in _checked:
        return
    for field in schema.fields:
        where = f"{schema.cls.__qualname__}.{field.name}"
        for number in field.numbers():
            if number > MAX_FIELD_ID:
                raise SchemaError(f"{where}: field id {number} does not fit in thrift's i16")
        _check_type(field.type, where)
    _checked.add(schema.cls)


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
    for field in schema.fields:
        value = getattr(obj, field.name)
        _encode_field(out, field, value, where=f"{schema.cls.__qualname__}.{field.name}")
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
    by_number = schema.by_number
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

    kwargs = {}
    for field in schema.fields:
        if field.name in values:
            kwargs[field.name] = values[field.name]
        elif field.optional:
            kwargs[field.name] = None
        else:
            kwargs[field.name] = zero_value(field.type)
    return schema.cls(**kwargs), pos


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
