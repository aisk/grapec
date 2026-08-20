"""Dict and JSON views of structs.

``to_dict`` keeps Python values (enums, datetimes, bytes) and Python field
names, it is meant for logging and tests. ``to_json`` follows the proto3 JSON
mapping (lowerCamelCase keys, int64 as strings, bytes as base64, RFC 3339
timestamps, enum names) so the output can be fed to gRPC gateways and other
protobuf implementations. ``from_dict`` accepts both shapes.
"""

from __future__ import annotations

import base64
import enum
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .codec import EncodeError
from .schema import (
    DurationType,
    EnumType,
    FieldSpec,
    ListType,
    MapType,
    OneOfType,
    ScalarType,
    StructType,
    TimestampType,
    TypeSpec,
    _enum_value,
    schema_of,
    zero_value,
)

_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


# ---------------------------------------------------------------------------
# to_dict / to_json


def to_dict(obj: Any, *, json_mode: bool = False) -> dict[str, Any]:
    schema = schema_of(type(obj))
    out: dict[str, Any] = {}
    for field in schema.fields:
        value = getattr(obj, field.name)
        if json_mode and _is_json_default(field, value):
            continue
        where = f"{schema.cls.__qualname__}.{field.name}"
        if json_mode and isinstance(field.type, OneOfType):
            member = field.type.pick(value)
            if member is None:
                raise EncodeError(f"{where}: {type(value).__qualname__} is not one of the union members")
            out[camel(member.json_name(field.name))] = _dump(member.type, value, True, where=where)
            continue
        key = camel(field.name) if json_mode else field.name
        out[key] = _dump(field.type, value, json_mode, where=where)
    return out


def to_json(obj: Any, **kwargs: Any) -> str:
    return json.dumps(to_dict(obj, json_mode=True), **kwargs)


def _is_json_default(field: FieldSpec, value: Any) -> bool:
    if value is None:
        return True
    if field.optional or isinstance(field.type, OneOfType):
        return False
    if isinstance(field.type, (ListType, MapType)):
        return not value
    if isinstance(field.type, (StructType, TimestampType, DurationType)):
        return False
    return value == zero_value(field.type)


def _dump(spec: TypeSpec, value: Any, js: bool, *, where: str) -> Any:
    if value is None:
        return None
    match spec:
        case ScalarType(kind="int"):
            return str(value) if js else value
        case ScalarType(kind="float"):
            if js and value != value:
                return "NaN"
            if js and value in (float("inf"), float("-inf")):
                return "Infinity" if value > 0 else "-Infinity"
            return value
        case ScalarType(kind="bytes"):
            return base64.b64encode(value).decode("ascii") if js else value
        case ScalarType():
            return value
        case EnumType():
            if not js:
                return value
            return value.name if isinstance(value, enum.Enum) else value
        case StructType():
            return to_dict(value, json_mode=js)
        case TimestampType():
            return _format_timestamp(value) if js else value
        case DurationType():
            return _format_duration(value) if js else value
        case ListType(item):
            return [_dump(item, v, js, where=where) for v in value]
        case MapType(key, item):
            return {
                (str(k).lower() if js and key.kind == "bool" else str(k) if js else k): _dump(item, v, js, where=where)
                for k, v in value.items()
            }
        case OneOfType():
            member = spec.pick(value)
            if member is None:
                raise EncodeError(f"{where}: {type(value).__qualname__} is not one of the union members")
            return _dump(member.type, value, js, where=where)
    raise AssertionError(spec)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.astimezone()
    value = value.astimezone(timezone.utc)
    text = value.strftime("%Y-%m-%dT%H:%M:%S")
    if value.microsecond:
        frac = f"{value.microsecond:06d}"
        text += "." + (frac[:3] if frac.endswith("000") else frac)
    return text + "Z"


def _format_duration(value: timedelta) -> str:
    total_us = (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds
    sign = "-" if total_us < 0 else ""
    seconds, us = divmod(abs(total_us), 1_000_000)
    text = f"{sign}{seconds}"
    if us:
        frac = f"{us:06d}"
        text += "." + (frac[:3] if frac.endswith("000") else frac)
    return text + "s"


# ---------------------------------------------------------------------------
# from_dict / from_json


def from_dict(cls: type, data: dict[str, Any]) -> Any:
    if not isinstance(data, dict):
        raise TypeError(f"{cls.__qualname__}.from_dict expects a dict, got {type(data).__qualname__}")
    schema = schema_of(cls)
    by_key: dict[str, tuple[FieldSpec, TypeSpec]] = {}
    for field in schema.fields:
        by_key[field.name] = by_key[camel(field.name)] = (field, field.type)
        if isinstance(field.type, OneOfType):
            for member in field.type.members:
                name = member.json_name(field.name)
                by_key[name] = by_key[camel(name)] = (field, member.type)

    kwargs: dict[str, Any] = {}
    for key, raw in data.items():
        entry = by_key.get(key)
        if entry is None:
            continue  # unknown keys are skipped, like unknown fields on the wire
        field, spec = entry
        where = f"{cls.__qualname__}.{field.name}"
        if raw is None:
            # proto3 JSON: null means "unset", the zero value for implicit
            # presence fields, None for optional fields and oneofs
            kwargs[field.name] = None if field.optional else zero_value(field.type)
            continue
        kwargs[field.name] = _load(spec, raw, where=where)

    for field in schema.fields:
        if field.name not in kwargs:
            kwargs[field.name] = None if field.optional else zero_value(field.type)
    return cls(**kwargs)


def from_json(cls: type, text: str | bytes) -> Any:
    return from_dict(cls, json.loads(text))


def _load(spec: TypeSpec, raw: Any, *, where: str) -> Any:
    match spec:
        case ScalarType(kind="int"):
            return _load_int(raw, where)
        case ScalarType(kind="float"):
            if isinstance(raw, str):
                if raw in ("NaN", "Infinity", "-Infinity"):
                    return float(raw.replace("Infinity", "inf"))
                return float(raw)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise TypeError(f"{where}: expected a number, got {raw!r}")
            return float(raw)
        case ScalarType(kind="str"):
            if not isinstance(raw, str):
                raise TypeError(f"{where}: expected a string, got {raw!r}")
            return raw
        case ScalarType(kind="bool"):
            if isinstance(raw, bool):
                return raw
            if raw in ("true", "false"):
                return raw == "true"
            raise TypeError(f"{where}: expected a bool, got {raw!r}")
        case ScalarType(kind="bytes"):
            if isinstance(raw, (bytes, bytearray)):
                return bytes(raw)
            if isinstance(raw, str):
                return base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=None if "+" in raw or "/" in raw else b"-_")
            raise TypeError(f"{where}: expected bytes or base64, got {raw!r}")
        case EnumType(cls):
            if isinstance(raw, str):
                try:
                    return cls[raw]
                except KeyError:
                    raise ValueError(f"{where}: unknown enum name {raw!r}") from None
            return _enum_value(cls, _load_int(raw, where))
        case StructType(cls):
            if isinstance(raw, cls):
                return raw
            return from_dict(cls, raw)
        case TimestampType():
            if isinstance(raw, datetime):
                return raw
            if isinstance(raw, str):
                return _parse_timestamp(raw, where)
            raise TypeError(f"{where}: expected a datetime or RFC 3339 string, got {raw!r}")
        case DurationType():
            if isinstance(raw, timedelta):
                return raw
            if isinstance(raw, str):
                return _parse_duration(raw, where)
            raise TypeError(f"{where}: expected a timedelta or duration string, got {raw!r}")
        case ListType(item):
            if not isinstance(raw, (list, tuple)):
                raise TypeError(f"{where}: expected a list, got {raw!r}")
            return [_load(item, v, where=where) for v in raw]
        case MapType(key, item):
            if not isinstance(raw, dict):
                raise TypeError(f"{where}: expected a dict, got {raw!r}")
            return {_load(key, k, where=where): _load(item, v, where=where) for k, v in raw.items()}
        case OneOfType(members):
            # a Python value of a member type is taken as is, otherwise try
            # each member in declaration order
            member = spec.pick(raw)
            if member is not None and not isinstance(member.type, StructType):
                return raw
            if member is not None and isinstance(raw, member.type.cls):
                return raw
            errors = []
            for m in members:
                try:
                    return _load(m.type, raw, where=where)
                except (TypeError, ValueError) as exc:
                    errors.append(str(exc))
            raise TypeError(f"{where}: value matches no union member: {'; '.join(errors)}")
    raise AssertionError(spec)


def _load_int(raw: Any, where: str) -> int:
    if isinstance(raw, bool):
        raise TypeError(f"{where}: expected an int, got {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            pass
    raise TypeError(f"{where}: expected an int, got {raw!r}")


_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2})(\.\d+)?([Zz]|[+-]\d{2}:?\d{2})$")


def _parse_timestamp(raw: str, where: str) -> datetime:
    m = _TS_RE.match(raw)
    if not m:
        raise ValueError(f"{where}: invalid RFC 3339 timestamp {raw!r}")
    base, frac, tz = m.groups()
    value = datetime.strptime(base[:10] + "T" + base[11:], "%Y-%m-%dT%H:%M:%S")
    if frac:
        value = value.replace(microsecond=int((frac[1:] + "000000")[:6]))
    if tz in ("Z", "z"):
        return value.replace(tzinfo=timezone.utc)
    sign = 1 if tz[0] == "+" else -1
    offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[-2:])) * sign
    return (value - offset).replace(tzinfo=timezone.utc)


_DUR_RE = re.compile(r"^(-)?(\d+)(\.\d+)?s$")


def _parse_duration(raw: str, where: str) -> timedelta:
    m = _DUR_RE.match(raw)
    if not m:
        raise ValueError(f"{where}: invalid duration {raw!r}")
    sign, seconds, frac = m.groups()
    us = int((frac[1:] + "000000")[:6]) if frac else 0
    value = timedelta(seconds=int(seconds), microseconds=us)
    return -value if sign else value
