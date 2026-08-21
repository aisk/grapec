"""Encode and decode struct instances using the protobuf wire format."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import wire as w
from .schema import (
    DurationType,
    EnumType,
    FieldSpec,
    ListType,
    MapType,
    OneOfType,
    ScalarType,
    StructSchema,
    StructType,
    TimestampType,
    TypeSpec,
    _enum_value,
    schema_of,
    zero_value,
)


def _is_packed(spec: TypeSpec) -> bool:
    """proto3 packs repeated numeric scalars and enums by default."""
    if isinstance(spec, EnumType):
        return True
    return isinstance(spec, ScalarType) and spec.kind in ("int", "float", "bool")


class EncodeError(TypeError):
    """Raised when a value does not match its declared type."""


# ---------------------------------------------------------------------------
# encoding


def encode(obj: Any) -> bytes:
    return _encode_struct(schema_of(type(obj)), obj)


def _encode_struct(schema: StructSchema, obj: Any) -> bytes:
    out = bytearray()
    for field in schema.fields:
        value = getattr(obj, field.name)
        _encode_field(out, field, value, where=f"{schema.cls.__qualname__}.{field.name}")
    return bytes(out)


def _encode_field(out: bytearray, field: FieldSpec, value: Any, *, where: str) -> None:
    spec = field.type
    number = field.number

    if isinstance(spec, ListType):
        if value is None:
            value = []
        if _is_packed(spec.item):
            if not value:
                return
            packed = bytearray()
            for item in value:
                packed += _encode_packed_item(spec.item, item, where)
            out += w.encode_tag(number, w.LENGTH)
            out += w.encode_bytes(bytes(packed))
        else:
            for item in value:
                _encode_single(out, number, spec.item, item, where)
        return

    if isinstance(spec, MapType):
        for key, item in (value or {}).items():
            entry = bytearray()
            _encode_single(entry, 1, spec.key, key, where)
            _encode_single(entry, 2, spec.value, item, where)
            out += w.encode_tag(number, w.LENGTH)
            out += w.encode_bytes(bytes(entry))
        return

    if value is None:
        if field.optional:
            return
        raise EncodeError(f"{where}: None is not allowed, declare the field as `T | None`")

    if isinstance(spec, OneOfType):
        member = spec.pick(value)
        if member is None:
            raise EncodeError(f"{where}: {type(value).__qualname__} is not one of the union members")
        _encode_single(out, member.number, member.type, value, where)
        return

    if not field.optional and isinstance(spec, ScalarType) and value == zero_value(spec):
        # proto3 implicit presence, default values are not written
        return
    if not field.optional and isinstance(spec, EnumType) and _check_int(value, where) == 0:
        return

    _encode_single(out, number, spec, value, where)


def _encode_packed_item(spec: TypeSpec, value: Any, where: str) -> bytes:
    match spec:
        case ScalarType(kind="float"):
            return w.encode_double(_check(value, (int, float), where))
        case ScalarType(kind="bool"):
            return w.encode_varint(1 if _check(value, bool, where) else 0)
        case ScalarType(kind="int"):
            return w.encode_varint(_check_int(value, where))
        case EnumType():
            return w.encode_varint(_check_int(value, where))
    raise AssertionError(spec)


def _encode_single(out: bytearray, number: int, spec: TypeSpec, value: Any, where: str) -> None:
    match spec:
        case ScalarType(kind="int") | EnumType():
            out += w.encode_tag(number, w.VARINT)
            out += w.encode_varint(_check_int(value, where))
        case ScalarType(kind="bool"):
            out += w.encode_tag(number, w.VARINT)
            out += w.encode_varint(1 if _check(value, bool, where) else 0)
        case ScalarType(kind="float"):
            out += w.encode_tag(number, w.FIXED64)
            out += w.encode_double(_check(value, (int, float), where))
        case ScalarType(kind="str"):
            out += w.encode_tag(number, w.LENGTH)
            out += w.encode_bytes(_check(value, str, where).encode("utf-8"))
        case ScalarType(kind="bytes"):
            out += w.encode_tag(number, w.LENGTH)
            out += w.encode_bytes(bytes(_check(value, (bytes, bytearray, memoryview), where)))
        case StructType(cls):
            if not isinstance(value, cls):
                raise EncodeError(f"{where}: expected {cls.__qualname__}, got {type(value).__qualname__}")
            out += w.encode_tag(number, w.LENGTH)
            out += w.encode_bytes(_encode_struct(schema_of(cls), value))
        case TimestampType():
            out += w.encode_tag(number, w.LENGTH)
            out += w.encode_bytes(_encode_timestamp(_check(value, datetime, where)))
        case DurationType():
            out += w.encode_tag(number, w.LENGTH)
            out += w.encode_bytes(_encode_duration(_check(value, timedelta, where)))
        case _:
            raise AssertionError(spec)


def _encode_seconds_nanos(seconds: int, nanos: int) -> bytes:
    out = bytearray()
    if seconds:
        out += w.encode_tag(1, w.VARINT) + w.encode_varint(seconds)
    if nanos:
        out += w.encode_tag(2, w.VARINT) + w.encode_varint(nanos)
    return bytes(out)


def _encode_timestamp(value: datetime) -> bytes:
    if value.tzinfo is None:
        value = value.astimezone()
    delta = value - datetime.fromtimestamp(0, tz=timezone.utc)
    seconds = delta.days * 86400 + delta.seconds
    nanos = delta.microseconds * 1000
    return _encode_seconds_nanos(seconds, nanos)


def _encode_duration(value: timedelta) -> bytes:
    total_us = (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds
    seconds, us = divmod(abs(total_us), 1_000_000)
    nanos = us * 1000
    if total_us < 0:
        seconds, nanos = -seconds, -nanos
    return _encode_seconds_nanos(seconds, nanos)


def _check(value: Any, types_: Any, where: str) -> Any:
    if not isinstance(value, types_):
        raise EncodeError(f"{where}: unexpected value {value!r}")
    return value


def _check_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EncodeError(f"{where}: expected int, got {value!r}")
    if not -(1 << 63) <= value < (1 << 63):
        raise EncodeError(f"{where}: {value} does not fit in 64 bits")
    return value


# ---------------------------------------------------------------------------
# decoding


def decode(cls: type, data: bytes | bytearray | memoryview) -> Any:
    return _decode_struct(schema_of(cls), bytes(data))


def _decode_struct(schema: StructSchema, buf: bytes) -> Any:
    by_number = schema.by_number
    values: dict[str, Any] = {}
    pos = 0
    while pos < len(buf):
        number, wire_type, pos = w.decode_tag(buf, pos)
        entry = by_number.get(number)
        if entry is None:
            pos = w.skip_field(buf, pos, wire_type)
            continue
        field, member = entry
        if member is not None:
            values[field.name], pos = _decode_single(member.type, buf, pos, wire_type)
            continue
        pos = _decode_field(values, field, buf, pos, wire_type)

    kwargs = {}
    for field in schema.fields:
        if field.name in values:
            kwargs[field.name] = values[field.name]
        elif field.optional:
            kwargs[field.name] = None
        else:
            kwargs[field.name] = zero_value(field.type)
    return schema.cls(**kwargs)


def _decode_field(values: dict[str, Any], field: FieldSpec, buf: bytes, pos: int, wire_type: int) -> int:
    spec = field.type
    name = field.name

    if isinstance(spec, ListType):
        target = values.setdefault(name, [])
        if wire_type == w.LENGTH and _is_packed(spec.item):
            chunk, pos = w.decode_bytes(buf, pos)
            inner = 0
            while inner < len(chunk):
                item, inner = _decode_single(spec.item, chunk, inner, _wire_type_of(spec.item))
                target.append(item)
            return pos
        item, pos = _decode_single(spec.item, buf, pos, wire_type)
        target.append(item)
        return pos

    if isinstance(spec, MapType):
        target = values.setdefault(name, {})
        entry, pos = w.decode_bytes(buf, pos)
        key: Any = zero_value(spec.key)
        val: Any = zero_value(spec.value)
        inner = 0
        while inner < len(entry):
            num, wt, inner = w.decode_tag(entry, inner)
            if num == 1:
                key, inner = _decode_single(spec.key, entry, inner, wt)
            elif num == 2:
                val, inner = _decode_single(spec.value, entry, inner, wt)
            else:
                inner = w.skip_field(entry, inner, wt)
        target[key] = val
        return pos

    value, pos = _decode_single(spec, buf, pos, wire_type)
    values[name] = value
    return pos


def _wire_type_of(spec: TypeSpec) -> int:
    match spec:
        case ScalarType(kind="float"):
            return w.FIXED64
        case ScalarType(kind="int") | ScalarType(kind="bool") | EnumType():
            return w.VARINT
    return w.LENGTH


def _decode_single(spec: TypeSpec, buf: bytes, pos: int, wire_type: int) -> tuple[Any, int]:
    expected = _wire_type_of(spec)
    if wire_type != expected:
        raise w.WireError(f"wire type {wire_type} does not match declared type")
    match spec:
        case ScalarType(kind="int"):
            raw, pos = w.decode_varint(buf, pos)
            return w.to_signed64(raw), pos
        case ScalarType(kind="bool"):
            raw, pos = w.decode_varint(buf, pos)
            return raw != 0, pos
        case EnumType(cls):
            raw, pos = w.decode_varint(buf, pos)
            return _enum_value(cls, w.to_signed64(raw)), pos
        case ScalarType(kind="float"):
            return w.decode_double(buf, pos)
        case ScalarType(kind="str"):
            raw, pos = w.decode_bytes(buf, pos)
            return raw.decode("utf-8"), pos
        case ScalarType(kind="bytes"):
            return w.decode_bytes(buf, pos)
        case StructType(cls):
            raw, pos = w.decode_bytes(buf, pos)
            return _decode_struct(schema_of(cls), raw), pos
        case TimestampType():
            raw, pos = w.decode_bytes(buf, pos)
            seconds, nanos = _decode_seconds_nanos(raw)
            epoch = datetime.fromtimestamp(0, tz=timezone.utc)
            return epoch + timedelta(seconds=seconds, microseconds=nanos // 1000), pos
        case DurationType():
            raw, pos = w.decode_bytes(buf, pos)
            seconds, nanos = _decode_seconds_nanos(raw)
            return timedelta(seconds=seconds, microseconds=nanos // 1000), pos
    raise AssertionError(spec)


def _decode_seconds_nanos(buf: bytes) -> tuple[int, int]:
    seconds = nanos = 0
    pos = 0
    while pos < len(buf):
        num, wt, pos = w.decode_tag(buf, pos)
        if num == 1 and wt == w.VARINT:
            raw, pos = w.decode_varint(buf, pos)
            seconds = w.to_signed64(raw)
        elif num == 2 and wt == w.VARINT:
            raw, pos = w.decode_varint(buf, pos)
            nanos = w.to_signed64(raw)
        else:
            pos = w.skip_field(buf, pos, wt)
    return seconds, nanos
