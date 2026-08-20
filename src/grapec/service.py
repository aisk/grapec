"""``Client`` / ``AsyncClient`` base classes, the ``name`` decorator and call options.

A service is declared by subclassing ``grapec.Client`` (or ``AsyncClient``
for ``async def`` methods) with a ``package`` class argument::

    class Greeter(grapec.Client, package="example.hello.v1"):
        @grapec.name("SayHello")
        def say_hello(self, request: HelloRequest) -> HelloReply: ...

    greeter = Greeter("grpc://localhost:50051", timeout=5)
    reply = greeter.say_hello(HelloRequest(name="x"))

The base classes own ``__init__`` and keep no public attributes, so method
names never clash with grapec internals. Calls accept ``timeout``,
``metadata`` and ``compression`` keyword arguments at runtime. Declare
``**options: Unpack[grapec.CallOptions]`` on a method if you want type
checkers to know about them. Closing goes through ``grapec.close``.
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any, Callable, TypedDict, TypeVar, get_type_hints

from .schema import SchemaError, is_struct
from .struct import _PACKAGE_RE

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

SERVICE_ATTR = "__grapec_service__"
METHOD_ATTR = "__grapec_method__"
NAME_ATTR = "__grapec_name__"
_SESSION_ATTR = "__grapec_session__"
_OWNED_ATTR = "__grapec_owns_session__"
_BOUND_ATTR = "__grapec_bound__"


class CallDetails:
    """Response metadata of one call, pass an instance as ``details=`` to receive it.

    ``headers`` and ``trailers`` are filled after the call returned or raised
    ``RpcError``. Binary values (``-bin`` keys) are ``bytes``.
    """

    __slots__ = ("headers", "trailers")

    def __init__(self) -> None:
        self.headers: dict[str, str | bytes] = {}
        self.trailers: dict[str, str | bytes] = {}

    def __repr__(self) -> str:
        return f"CallDetails(headers={self.headers!r}, trailers={self.trailers!r})"


class CallOptions(TypedDict, total=False):
    """Keyword arguments every remote method accepts."""

    timeout: float | None
    metadata: dict[str, str | bytes] | None
    compression: str | None
    details: CallDetails | None


_OPTION_KEYS = frozenset(CallOptions.__annotations__)


@dataclasses.dataclass(frozen=True)
class ServiceSpec:
    cls: type
    package: str
    name: str
    is_async: bool

    @property
    def full_name(self) -> str:
        return f"{self.package}.{self.name}"


@dataclasses.dataclass(frozen=True)
class MethodSpec:
    service: ServiceSpec
    name: str
    request: type
    response: type
    python_name: str

    @property
    def path(self) -> str:
        return f"/{self.service.full_name}/{self.name}"


def name(wire_name: str) -> Callable[[F], F]:
    """Use a different name on the wire than the Python method name."""
    if not isinstance(wire_name, str) or not wire_name.isidentifier():
        raise SchemaError(f"invalid method name {wire_name!r}")

    def wrap(func: F) -> F:
        setattr(func, NAME_ATTR, wire_name)
        return func

    return wrap


class _RemoteMethod:
    """Replaces the declared method on the class. Carries the spec, calls on access."""

    def __init__(self, spec: MethodSpec, func: Callable[..., Any]) -> None:
        self.spec = spec
        self.__wrapped__ = func
        self.__doc__ = func.__doc__
        self.__name__ = func.__name__
        self.__qualname__ = func.__qualname__
        setattr(self, METHOD_ATTR, spec)

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        session = _session_of(instance)
        bound = instance.__dict__.setdefault(_BOUND_ATTR, {})
        try:
            return bound[self.spec.python_name]
        except KeyError:
            call = bound[self.spec.python_name] = _bind(self.spec, session)
            return call

    def __repr__(self) -> str:
        return f"<remote method {self.spec.path}>"


def _bind(spec: MethodSpec, session: Any) -> Callable[..., Any]:
    def call(request: Any, /, **options: Any) -> Any:
        unknown = set(options) - _OPTION_KEYS
        if unknown:
            raise TypeError(f"{spec.python_name}() got unexpected keyword argument(s): {', '.join(sorted(unknown))}")
        return session.call(spec, request, **options)

    call.__name__ = spec.python_name
    call.__qualname__ = f"{spec.service.cls.__qualname__}.{spec.python_name}"
    call.__doc__ = getattr(spec.service.cls, spec.python_name).__doc__
    setattr(call, METHOD_ATTR, spec)
    return call


def _session_of(instance: Any) -> Any:
    session = instance.__dict__.get(_SESSION_ATTR)
    if session is None:
        raise TypeError(f"{type(instance).__qualname__} was not initialised, call its __init__")
    return session


class _ClientBase:
    __grapec_async__ = False

    def __init_subclass__(cls, *, package: str | None = None, name: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__module__ == __name__:
            return  # Client and AsyncClient themselves
        if package is None:
            parent = getattr(cls, SERVICE_ATTR, None)
            if parent is None:
                raise SchemaError(f"{cls.__qualname__}: missing `package=` class argument")
            package = parent.package
        if not isinstance(package, str) or not _PACKAGE_RE.match(package):
            raise SchemaError(f"{cls.__qualname__}: invalid package name {package!r}")
        spec = ServiceSpec(cls, package, name or cls.__name__, cls.__grapec_async__)
        setattr(cls, SERVICE_ATTR, spec)
        for attr, value in list(vars(cls).items()):
            if attr.startswith("_") or not inspect.isfunction(value):
                continue
            setattr(cls, attr, _RemoteMethod(_build_method(spec, attr, value), value))
        if name is not None:
            # an explicit wire name applies to inherited methods as well
            for attr, value in remote_methods(cls).items():
                if attr not in vars(cls):
                    setattr(cls, attr, _RemoteMethod(dataclasses.replace(value.spec, service=spec), value.__wrapped__))

    def __init__(self, target: Any, **options: Any) -> None:
        from .session import AsyncSession, Session

        wanted = AsyncSession if type(self).__grapec_async__ else Session
        if isinstance(target, str):
            session = wanted(target, **options)
            owned = True
        elif isinstance(target, wanted):
            if options:
                raise TypeError("options are only accepted together with a URL, configure the session instead")
            session = target
            owned = False
        elif isinstance(target, (Session, AsyncSession)):
            raise TypeError(f"{type(self).__qualname__} needs a {wanted.__name__}, got {type(target).__name__}")
        else:
            raise TypeError(f"expected a URL or {wanted.__name__}, got {type(target).__qualname__}")
        self.__dict__[_SESSION_ATTR] = session
        self.__dict__[_OWNED_ATTR] = owned

    def __repr__(self) -> str:
        session = self.__dict__.get(_SESSION_ATTR)
        return f"<{type(self).__qualname__} {getattr(session, 'url', 'uninitialised')}>"

    def __del__(self) -> None:
        try:
            if self.__dict__.get(_OWNED_ATTR):
                self.__dict__[_SESSION_ATTR].close()
        except Exception:
            pass


class Client(_ClientBase):
    """Base class for sync service clients. Subclass with ``package=...`` and ``def`` methods.

    Construct with a URL plus ``Session`` options (``max_idle``, ``timeout``,
    ``connect_timeout``, ``compression``) or with an existing ``Session`` to
    share connections between several clients.
    """

    __grapec_async__ = False


class AsyncClient(_ClientBase):
    """Base class for asyncio service clients. Subclass with ``package=...`` and ``async def`` methods."""

    __grapec_async__ = True


def _build_method(svc: ServiceSpec, attr: str, func: Callable[..., Any]) -> MethodSpec:
    where = f"{svc.cls.__qualname__}.{attr}"
    if inspect.iscoroutinefunction(func) != svc.is_async:
        base = "AsyncClient" if svc.is_async else "Client"
        kind = "async def" if svc.is_async else "def"
        raise SchemaError(f"{where}: methods of a {base} subclass must be declared with `{kind}`")
    params = list(inspect.signature(func).parameters.values())
    if len(params) == 3 and params[2].kind is inspect.Parameter.VAR_KEYWORD:
        params.pop()
    if len(params) != 2 or params[0].name != "self" or params[1].kind is inspect.Parameter.VAR_KEYWORD:
        raise SchemaError(f"{where}: expected signature (self, request) or (self, request, **options)")
    hints = get_type_hints(func, include_extras=False)
    request = hints.get(params[1].name)
    response = hints.get("return")
    if request is None or not is_struct(request):
        raise SchemaError(f"{where}: request parameter must be annotated with a struct")
    if response is None or not is_struct(response):
        raise SchemaError(f"{where}: return annotation must be a struct")
    wire_name = getattr(func, NAME_ATTR, attr)
    return MethodSpec(svc, wire_name, request, response, attr)


def remote_methods(cls: type) -> dict[str, _RemoteMethod]:
    """All remote methods of a client class, inherited ones first, in declaration order."""
    out: dict[str, _RemoteMethod] = {}
    for klass in reversed(cls.__mro__):
        for attr, value in vars(klass).items():
            if isinstance(value, _RemoteMethod):
                out[attr] = value  # overrides keep the position of the inherited method
    return out


def method_of(func: Any) -> MethodSpec:
    if isinstance(func, MethodSpec):
        return func
    spec = getattr(func, METHOD_ATTR, None)
    if spec is None:
        raise SchemaError(f"{func!r} is not a method of a grapec client")
    return spec


# ---------------------------------------------------------------------------
# module level helpers, kept off the classes so user method names never clash


def session_of(client: Any) -> Any:
    """The ``Session`` or ``AsyncSession`` behind a client."""
    if not isinstance(client, _ClientBase):
        raise TypeError(f"expected a grapec client, got {type(client).__qualname__}")
    return _session_of(client)


def close(client: Any) -> None:
    """Close the client's session if the client created it. Shared sessions are left alone."""
    if not isinstance(client, _ClientBase):
        raise TypeError(f"expected a grapec client, got {type(client).__qualname__}")
    if client.__dict__.get(_OWNED_ATTR):
        _session_of(client).close()


async def aclose(client: Any) -> None:
    """Async variant of :func:`close` for ``AsyncClient`` instances, flushes GOAWAY."""
    if not isinstance(client, _ClientBase):
        raise TypeError(f"expected a grapec client, got {type(client).__qualname__}")
    if client.__dict__.get(_OWNED_ATTR):
        session = _session_of(client)
        if hasattr(session, "aclose"):
            await session.aclose()
        else:
            session.close()
