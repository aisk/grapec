"""Turn a decorated Python class into an internal schema description.

The schema is independent from any wire format. A codec consumes it to
serialize and deserialize instances.
"""

from __future__ import annotations

import dataclasses
import enum
import functools
import types
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Union, get_args, get_origin, get_type_hints

MAX_FIELD_NUMBER = (1 << 29) - 1
RESERVED_RANGE = range(19000, 20000)


class SchemaError(TypeError):
    """Raised when a class cannot be turned into a schema."""


@dataclasses.dataclass(frozen=True)
class Id:
    """Explicit field number, used inside ``Annotated[T, Id(n)]``."""

    number: int

    def __post_init__(self) -> None:
        if not isinstance(self.number, int) or isinstance(self.number, bool):
            raise SchemaError("Id expects an int")
        if not 1 <= self.number <= MAX_FIELD_NUMBER:
            raise SchemaError(f"field number {self.number} out of range")
        if self.number in RESERVED_RANGE:
            raise SchemaError(f"field number {self.number} is reserved")


Scalar = Literal["int", "float", "str", "bytes", "bool"]


@dataclasses.dataclass(frozen=True)
class ScalarType:
    kind: Scalar


@dataclasses.dataclass(frozen=True)
class EnumType:
    cls: type[enum.IntEnum]


@dataclasses.dataclass(frozen=True)
class StructType:
    cls: type


@dataclasses.dataclass(frozen=True)
class TimestampType:
    pass


@dataclasses.dataclass(frozen=True)
class DurationType:
    pass


@dataclasses.dataclass(frozen=True)
class ListType:
    item: TypeSpec


@dataclasses.dataclass(frozen=True)
class MapType:
    key: ScalarType
    value: TypeSpec


@dataclasses.dataclass(frozen=True)
class Member:
    """One alternative of a oneof, it owns its own field number."""

    number: int
    type: ScalarType | EnumType | StructType | TimestampType | DurationType

    def matches(self, value: Any) -> bool:
        return python_type_of(self.type) is type(value)

    def accepts(self, value: Any) -> bool:
        return isinstance(value, python_type_of(self.type))

    def json_name(self, field_name: str) -> str:
        """Name of this member on the proto side, ``<field>_<type>``."""
        return f"{field_name}_{self.suffix}"

    @property
    def suffix(self) -> str:
        match self.type:
            case ScalarType(kind):
                return kind
            case EnumType(cls) | StructType(cls):
                return _snake(cls.__name__)
            case TimestampType():
                return "timestamp"
            case DurationType():
                return "duration"
        raise AssertionError(self.type)


@dataclasses.dataclass(frozen=True)
class OneOfType:
    members: tuple[Member, ...]

    def pick(self, value: Any) -> Member | None:
        for m in self.members:
            if m.matches(value):
                return m
        for m in self.members:
            if m.accepts(value):
                return m
        return None


TypeSpec = Union[
    ScalarType, EnumType, StructType, TimestampType, DurationType, ListType, MapType, OneOfType
]


@dataclasses.dataclass(frozen=True)
class FieldSpec:
    name: str
    number: int
    type: TypeSpec
    optional: bool

    def numbers(self) -> tuple[int, ...]:
        if isinstance(self.type, OneOfType):
            return tuple(m.number for m in self.type.members)
        return (self.number,)


@dataclasses.dataclass(frozen=True)
class StructSchema:
    cls: type
    package: str
    fields: tuple[FieldSpec, ...]

    @property
    def full_name(self) -> str:
        return f"{self.package}.{self.cls.__name__}"

    @functools.cached_property
    def by_number(self) -> dict[int, tuple[FieldSpec, Member | None]]:
        out: dict[int, tuple[FieldSpec, Member | None]] = {}
        for f in self.fields:
            if isinstance(f.type, OneOfType):
                for m in f.type.members:
                    out[m.number] = (f, m)
            else:
                out[f.number] = (f, None)
        return out


SCHEMA_ATTR = "__grapec_schema__"
PACKAGE_ATTR = "__grapec_package__"

_SCALARS: dict[type, Scalar] = {
    int: "int",
    float: "float",
    str: "str",
    bytes: "bytes",
    bool: "bool",
}


def is_struct(obj: Any) -> bool:
    cls = obj if isinstance(obj, type) else type(obj)
    return PACKAGE_ATTR in cls.__dict__


def python_type_of(spec: TypeSpec) -> type:
    match spec:
        case ScalarType(kind):
            return {"int": int, "float": float, "str": str, "bytes": bytes, "bool": bool}[kind]
        case EnumType(cls) | StructType(cls):
            return cls
        case TimestampType():
            return datetime
        case DurationType():
            return timedelta
        case ListType():
            return list
        case MapType():
            return dict
    raise SchemaError(f"{spec} has no single Python type")


def split_union(tp: Any) -> tuple[list[Any], bool]:
    """Return the non None members of a union and whether None was present."""
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        args = list(get_args(tp))
        members = [a for a in args if a is not type(None)]
        return members, len(members) != len(args)
    return [tp], False


def split_optional(tp: Any) -> tuple[Any, bool]:
    """Return (inner, True) for ``T | None`` and (tp, False) otherwise."""
    members, optional = split_union(tp)
    if len(members) != 1:
        raise SchemaError(f"only `T | None` unions are supported here: {tp}")
    return members[0], optional


def split_annotated(tp: Any) -> tuple[Any, tuple[Any, ...]]:
    if get_origin(tp) is Annotated:
        args = get_args(tp)
        return args[0], args[1:]
    return tp, ()


def resolve_type(tp: Any, *, where: str) -> TypeSpec:
    tp, _ = split_annotated(tp)
    origin = get_origin(tp)

    if origin is list:
        (item,) = get_args(tp) or (Any,)
        _, opt = split_union(split_annotated(item)[0])
        if opt:
            raise SchemaError(f"{where}: list items cannot be optional")
        item_spec = resolve_type(item, where=where)
        if isinstance(item_spec, (ListType, MapType)):
            raise SchemaError(f"{where}: nested list/dict inside list is not supported")
        return ListType(item_spec)

    if origin is dict:
        args = get_args(tp)
        if len(args) != 2:
            raise SchemaError(f"{where}: dict needs key and value types")
        key_spec = resolve_type(args[0], where=where)
        if not isinstance(key_spec, ScalarType) or key_spec.kind in ("float", "bytes"):
            raise SchemaError(f"{where}: dict keys must be int, str or bool")
        value_spec = resolve_type(args[1], where=where)
        if isinstance(value_spec, (ListType, MapType)):
            raise SchemaError(f"{where}: dict values cannot be list or dict")
        return MapType(key_spec, value_spec)

    if origin is Union or origin is types.UnionType:
        raise SchemaError(f"{where}: unions are only supported at the top level of a field")

    if origin is not None:
        raise SchemaError(f"{where}: unsupported type {tp!r}")

    if not isinstance(tp, type):
        raise SchemaError(f"{where}: unsupported annotation {tp!r}")

    if tp in _SCALARS:
        return ScalarType(_SCALARS[tp])
    if tp is datetime:
        return TimestampType()
    if tp is timedelta:
        return DurationType()
    if issubclass(tp, enum.IntEnum):
        return EnumType(tp)
    if issubclass(tp, enum.Enum):
        raise SchemaError(f"{where}: enums must subclass enum.IntEnum")
    if is_struct(tp):
        return StructType(tp)
    raise SchemaError(f"{where}: unsupported type {tp!r}")


def _explicit_id(metadata: tuple[Any, ...], *, where: str) -> int | None:
    ids = [m for m in metadata if isinstance(m, Id)]
    if len(ids) > 1:
        raise SchemaError(f"{where}: more than one Id annotation")
    return ids[0].number if ids else None


def build_schema(cls: type) -> StructSchema:
    package = cls.__dict__.get(PACKAGE_ATTR)
    if package is None:
        raise SchemaError(f"{cls.__qualname__} is not a grapec struct")

    hints = get_type_hints(cls, include_extras=True, localns={cls.__name__: cls})
    fields: list[FieldSpec] = []
    used: set[int] = set()
    last = 0

    for dc_field in dataclasses.fields(cls):
        name = dc_field.name
        where = f"{cls.__qualname__}.{name}"
        hint = hints[name]

        inner, metadata = split_annotated(hint)
        members, optional = split_union(inner)

        def take_number(metadata: tuple[Any, ...]) -> int:
            nonlocal last
            number = _explicit_id(metadata, where=where)
            if number is None:
                number = last + 1
                while number in used or number in RESERVED_RANGE:
                    number += 1
            if number in used:
                raise SchemaError(f"{where}: duplicate field number {number}")
            if number > MAX_FIELD_NUMBER:
                raise SchemaError(f"{where}: field number {number} out of range")
            used.add(number)
            last = number
            return number

        if len(members) > 1:
            spec = _resolve_oneof(members, take_number, where=where)
            # a oneof always has presence, an unset one is None
            fields.append(FieldSpec(name, spec.members[0].number, spec, True))
            continue

        inner, more = split_annotated(members[0])
        metadata = metadata + more
        spec = resolve_type(inner, where=where)
        if optional and isinstance(spec, (ListType, MapType)):
            raise SchemaError(f"{where}: list and dict fields cannot be optional")
        fields.append(FieldSpec(name, take_number(metadata), spec, optional))

    return StructSchema(cls, package, tuple(fields))


def _resolve_oneof(members: list[Any], take_number: Any, where: str) -> OneOfType:
    out: list[Member] = []
    seen: list[type] = []
    for member in members:
        inner, metadata = split_annotated(member)
        spec = resolve_type(inner, where=where)
        if isinstance(spec, (ListType, MapType)):
            raise SchemaError(f"{where}: union members cannot be list or dict")
        py = python_type_of(spec)
        for other in seen:
            if issubclass(py, other) or issubclass(other, py):
                raise SchemaError(f"{where}: union members {other.__name__} and {py.__name__} cannot be told apart")
        seen.append(py)
        out.append(Member(take_number(metadata), spec))
    return OneOfType(tuple(out))


def schema_of(cls: type) -> StructSchema:
    """Return the cached schema of a struct class, building it on first use."""
    cached = cls.__dict__.get(SCHEMA_ATTR)
    if cached is None:
        cached = build_schema(cls)
        setattr(cls, SCHEMA_ATTR, cached)
    return cached


def zero_value(spec: TypeSpec) -> Any:
    """The value a non optional field takes when it is absent on the wire."""
    match spec:
        case ScalarType(kind="int"):
            return 0
        case ScalarType(kind="float"):
            return 0.0
        case ScalarType(kind="str"):
            return ""
        case ScalarType(kind="bytes"):
            return b""
        case ScalarType(kind="bool"):
            return False
        case EnumType(cls):
            return _enum_value(cls, 0)
        case StructType(cls):
            sch = schema_of(cls)
            return cls(**{f.name: None if f.optional else zero_value(f.type) for f in sch.fields})
        case TimestampType():
            return datetime.fromtimestamp(0, tz=timezone.utc)
        case DurationType():
            return timedelta(0)
        case ListType():
            return []
        case MapType():
            return {}
        case OneOfType():
            return None
    raise AssertionError(spec)


def _enum_value(cls: type[enum.IntEnum], value: int) -> Any:
    try:
        return cls(value)
    except ValueError:
        # proto3 enums are open, keep unknown values as plain ints
        return value


def _snake(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and (not name[i - 1].isupper() or (i + 1 < len(name) and name[i + 1].islower())):
            out.append("_")
        out.append(ch.lower())
    return "".join(out)
