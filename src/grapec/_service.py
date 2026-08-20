"""The ``service`` class decorator and the ``name`` method decorator."""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any, Callable, TypeVar, get_type_hints

from ._schema import SchemaError, is_struct
from ._struct import _PACKAGE_RE

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

SERVICE_ATTR = "__grapec_service__"
METHOD_ATTR = "__grapec_method__"
NAME_ATTR = "__grapec_name__"


@dataclasses.dataclass(frozen=True)
class ServiceSpec:
    cls: type
    package: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.package}.{self.name}"


@dataclasses.dataclass(frozen=True)
class MethodSpec:
    service: ServiceSpec
    name: str
    request: type
    response: type

    @property
    def path(self) -> str:
        return f"/{self.service.full_name}/{self.name}"


def service(*, package: str, name: str | None = None) -> Callable[[type[T]], type[T]]:
    """Mark a class as a remote service description.

    Methods describe the calls: exactly one parameter besides ``self``, both
    the parameter and the return annotation must be ``@grapec.struct`` classes.
    Method bodies are never executed, ``...`` is enough.
    """
    if not isinstance(package, str) or not _PACKAGE_RE.match(package):
        raise SchemaError(f"invalid package name {package!r}")

    def wrap(cls: type[T]) -> type[T]:
        spec = ServiceSpec(cls, package, name or cls.__name__)
        setattr(cls, SERVICE_ATTR, spec)
        for attr, value in list(vars(cls).items()):
            if attr.startswith("_") or not inspect.isfunction(value):
                continue
            setattr(value, METHOD_ATTR, _build_method(spec, value))
        return cls

    return wrap


def name(wire_name: str) -> Callable[[F], F]:
    """Use a different name on the wire than the Python method name."""
    if not isinstance(wire_name, str) or not wire_name.isidentifier():
        raise SchemaError(f"invalid method name {wire_name!r}")

    def wrap(func: F) -> F:
        setattr(func, NAME_ATTR, wire_name)
        return func

    return wrap


def _build_method(svc: ServiceSpec, func: Callable[..., Any]) -> MethodSpec:
    where = f"{svc.cls.__qualname__}.{func.__name__}"
    params = list(inspect.signature(func).parameters.values())
    if len(params) != 2 or params[0].name != "self":
        raise SchemaError(f"{where}: expected signature (self, request)")
    hints = get_type_hints(func)
    request = hints.get(params[1].name)
    response = hints.get("return")
    if request is None or not is_struct(request):
        raise SchemaError(f"{where}: request parameter must be annotated with a struct")
    if response is None or not is_struct(response):
        raise SchemaError(f"{where}: return annotation must be a struct")
    wire_name = getattr(func, NAME_ATTR, func.__name__)
    return MethodSpec(svc, wire_name, request, response)


def method_of(func: Any) -> MethodSpec:
    spec = getattr(func, METHOD_ATTR, None)
    if spec is None:
        raise SchemaError(f"{func!r} is not a method of a grapec service")
    return spec
