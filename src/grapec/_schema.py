"""Turn a decorated Python class into an internal schema description.

The schema is independent from any wire format. A codec consumes it to
serialize and deserialize instances.
"""

from __future__ import annotations

import dataclasses
import enum
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


TypeSpec = Union[
    ScalarType, EnumType, StructType, TimestampType, DurationType, ListType, MapType
]


@dataclasses.dataclass(frozen=True)
class FieldSpec:
    name: str
    number: int
    type: TypeSpec
    optional: bool


@dataclasses.dataclass(frozen=True)
class StructSchema:
    cls: type
    package: str
    fields: tuple[FieldSpec, ...]

    @property
    def full_name(self) -> str:
        return f"{self.package}.{self.cls.__name__}"

    def by_number(self) -> dict[int, FieldSpec]:
        return {f.number: f for f in self.fields}


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


def split_optional(tp: Any) -> tuple[Any, bool]:
    """Return (inner, True) for ``T | None`` and (tp, False) otherwise."""
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == len(get_args(tp)):
            raise SchemaError(f"unions other than `T | None` are not supported: {tp}")
        if len(args) != 1:
            raise SchemaError(f"only `T | None` unions are supported: {tp}")
        return args[0], True
    return tp, False


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
        item_spec = resolve_type(item, where=where)
        if isinstance(item_spec, (ListType, MapType)):
            raise SchemaError(f"{where}: nested list/dict inside list is not supported")
        inner, opt = split_optional(split_annotated(item)[0])
        if opt:
            raise SchemaError(f"{where}: list items cannot be optional")
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
        inner, optional = split_optional(inner)
        if optional:
            inner, more = split_annotated(inner)
            metadata = metadata + more

        spec = resolve_type(inner, where=where)
        if optional and isinstance(spec, (ListType, MapType)):
            raise SchemaError(f"{where}: list and dict fields cannot be optional")

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

        fields.append(FieldSpec(name, number, spec, optional))

    return StructSchema(cls, package, tuple(fields))


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
    raise AssertionError(spec)


def _enum_value(cls: type[enum.IntEnum], value: int) -> Any:
    try:
        return cls(value)
    except ValueError:
        # proto3 enums are open, keep unknown values as plain ints
        return value
