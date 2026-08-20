"""Render structs and services as a ``.proto`` file.

grapec never reads this output, it exists so the other side of the wire can
generate code from the same definitions.
"""

from __future__ import annotations

import enum
from typing import Any

from .schema import (
    DurationType,
    EnumType,
    ListType,
    MapType,
    OneOfType,
    ScalarType,
    SchemaError,
    StructSchema,
    StructType,
    TimestampType,
    TypeSpec,
    is_struct,
    schema_of,
)
from .service import SERVICE_ATTR, MethodSpec, ServiceSpec, remote_methods

_SCALAR_NAMES = {"int": "int64", "float": "double", "str": "string", "bytes": "bytes", "bool": "bool"}


def export_proto(*roots: type) -> str:
    """Return proto3 source for the given structs and services and everything they reference.

    All roots must live in the same package. Structs from other packages are
    referenced by their full name and pulled in with ``import`` statements
    named ``<package path>.proto``. Python enums carry no package, each enum
    belongs to the package of the first struct that references it.
    """
    if not roots:
        raise ValueError("export_proto needs at least one struct or service")

    services: list[ServiceSpec] = []
    methods: list[MethodSpec] = []
    structs: dict[type, StructSchema] = {}
    enums: dict[type, str] = {}
    package: str | None = None

    def visit_struct(cls: type) -> None:
        if cls in structs:
            return
        schema = schema_of(cls)
        structs[cls] = schema
        for field in schema.fields:
            visit_type(field.type, schema.package)

    def visit_type(spec: TypeSpec, pkg: str) -> None:
        match spec:
            case StructType(cls):
                visit_struct(cls)
            case EnumType(cls):
                enums.setdefault(cls, pkg)
            case ListType(item):
                visit_type(item, pkg)
            case MapType(_, value):
                visit_type(value, pkg)
            case OneOfType(members):
                for m in members:
                    visit_type(m.type, pkg)

    for root in roots:
        svc = getattr(root, SERVICE_ATTR, None)
        if svc is not None:
            services.append(svc)
            for value in remote_methods(root).values():
                spec = value.spec
                methods.append(spec)
                visit_struct(spec.request)
                visit_struct(spec.response)
            pkg = svc.package
        elif is_struct(root):
            visit_struct(root)
            pkg = schema_of(root).package
        else:
            raise SchemaError(f"{root!r} is neither a struct nor a client class")
        if package is None:
            package = pkg
        elif package != pkg:
            raise SchemaError(f"roots must share one package, got {package!r} and {pkg!r}")

    assert package is not None
    local = [s for s in structs.values() if s.package == package]
    local_enums = [cls for cls, pkg in enums.items() if pkg == package]
    foreign = sorted(({s.package for s in structs.values()} | set(enums.values())) - {package})
    _check_enum_names(local_enums, package)

    needs_timestamp = needs_duration = False

    def type_name(spec: TypeSpec) -> str:
        nonlocal needs_timestamp, needs_duration
        match spec:
            case ScalarType(kind):
                return _SCALAR_NAMES[kind]
            case EnumType(cls):
                pkg = enums[cls]
                return cls.__name__ if pkg == package else f"{pkg}.{cls.__name__}"
            case StructType(cls):
                schema = structs[cls]
                return cls.__name__ if schema.package == package else schema.full_name
            case TimestampType():
                needs_timestamp = True
                return "google.protobuf.Timestamp"
            case DurationType():
                needs_duration = True
                return "google.protobuf.Duration"
            case MapType(key, value):
                return f"map<{type_name(key)}, {type_name(value)}>"
        raise AssertionError(spec)

    body: list[str] = []
    for cls in local_enums:
        body.append(_render_enum(cls))
    for schema in sorted(local, key=lambda s: s.cls.__name__):
        body.append(_render_message(schema, type_name))
    for svc in services:
        own = [m for m in methods if m.service is svc]
        lines = [f"service {svc.name} {{"]
        for m in own:
            lines.append(f"  rpc {m.name} ({type_name(StructType(m.request))}) returns ({type_name(StructType(m.response))});")
        lines.append("}")
        body.append("\n".join(lines))

    head = ['syntax = "proto3";', "", f"package {package};"]
    imports = []
    if needs_timestamp:
        imports.append('import "google/protobuf/timestamp.proto";')
    if needs_duration:
        imports.append('import "google/protobuf/duration.proto";')
    for pkg in foreign:
        imports.append(f'import "{pkg.replace(".", "/")}.proto";')
    if imports:
        head += [""] + imports
    return "\n".join(head) + "\n\n" + "\n\n".join(body) + "\n"


def _check_enum_names(enums: list[type[enum.IntEnum]], package: str) -> None:
    """Enum values share one scope per package in proto, names must not repeat."""
    owner: dict[str, type] = {}
    for cls in enums:
        for member_name in cls.__members__:
            other = owner.setdefault(member_name, cls)
            if other is not cls:
                raise SchemaError(
                    f"enum value {member_name!r} is defined by both {other.__name__} and {cls.__name__} "
                    f"in package {package!r}. proto enum values share one scope per package, "
                    f"rename one of them, for example {cls.__name__.upper()}_{member_name}"
                )


def _render_enum(cls: type[enum.IntEnum]) -> str:
    lines = [f"enum {cls.__name__} {{"]
    members = list(cls.__members__.items())
    if len(members) > len(list(cls)):
        lines.append("  option allow_alias = true;")
    values = {m.value for m in cls}
    if 0 not in values:
        lines.append(f"  {cls.__name__.upper()}_UNSPECIFIED = 0;")
    # proto3 wants the zero value first
    members.sort(key=lambda item: item[1].value != 0)
    for member_name, member in members:
        lines.append(f"  {member_name} = {member.value};")
    lines.append("}")
    return "\n".join(lines)


def _render_message(schema: StructSchema, type_name: Any) -> str:
    lines = [f"message {schema.cls.__name__} {{"]
    for field in schema.fields:
        spec = field.type
        match spec:
            case ListType(item):
                lines.append(f"  repeated {type_name(item)} {field.name} = {field.number};")
            case MapType():
                lines.append(f"  {type_name(spec)} {field.name} = {field.number};")
            case OneOfType(members):
                lines.append(f"  oneof {field.name} {{")
                for m in members:
                    lines.append(f"    {type_name(m.type)} {m.json_name(field.name)} = {m.number};")
                lines.append("  }")
            case _:
                prefix = "optional " if field.optional else ""
                lines.append(f"  {prefix}{type_name(spec)} {field.name} = {field.number};")
    lines.append("}")
    return "\n".join(lines)
