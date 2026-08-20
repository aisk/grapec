"""The ``struct`` class decorator."""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Callable, TypeVar, dataclass_transform, get_type_hints

from . import _codec, _dict
from ._schema import PACKAGE_ATTR, SchemaError, split_annotated, split_union

T = TypeVar("T")

_PACKAGE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_STRING_LIST = re.compile(r"^(typing\.)?(List|list)\[")
_STRING_DICT = re.compile(r"^(typing\.)?(Dict|dict)\[")
_STRING_OPTIONAL = re.compile(r"(^(typing\.)?Optional\[)|(\|\s*None\s*$)|(^None\s*\|)")


@dataclass_transform(kw_only_default=True)
def struct(*, package: str) -> Callable[[type[T]], type[T]]:
    """Turn a plain annotated class into a serializable struct.

    ``package`` is the namespace the struct lives in on the wire side, for
    example ``"example.hello.v1"``. The decorated class becomes a keyword
    only dataclass with ``to_bytes`` / ``from_bytes`` added.
    """
    if not isinstance(package, str) or not _PACKAGE_RE.match(package):
        raise SchemaError(f"invalid package name {package!r}")

    def wrap(cls: type[T]) -> type[T]:
        if dataclasses.is_dataclass(cls):
            raise SchemaError(f"{cls.__qualname__} is already a dataclass")
        _inject_defaults(cls)
        cls = dataclasses.dataclass(kw_only=True)(cls)
        setattr(cls, PACKAGE_ATTR, package)
        cls.to_bytes = _to_bytes  # type: ignore[attr-defined]
        cls.__bytes__ = _to_bytes  # type: ignore[attr-defined]
        cls.from_bytes = classmethod(_from_bytes)  # type: ignore[attr-defined]
        cls.to_dict = _dict.to_dict  # type: ignore[attr-defined]
        cls.from_dict = classmethod(_dict.from_dict)  # type: ignore[attr-defined]
        cls.to_json = _dict.to_json  # type: ignore[attr-defined]
        cls.from_json = classmethod(_dict.from_json)  # type: ignore[attr-defined]
        return cls

    return wrap


def _to_bytes(self: Any) -> bytes:
    return _codec.encode(self)


def _from_bytes(cls: type[T], data: bytes | bytearray | memoryview) -> T:
    return _codec.decode(cls, data)


def _inject_defaults(cls: type) -> None:
    """Give list, dict, optional and oneof fields an implicit default.

    Everything else stays required, like a regular dataclass.
    """
    annotations = cls.__dict__.get("__annotations__", {})
    try:
        hints: dict[str, Any] | None = get_type_hints(
            cls, include_extras=True, localns={cls.__name__: cls}
        )
    except Exception:
        hints = None

    for name, raw in annotations.items():
        if name in cls.__dict__:
            continue
        kind = _classify(hints[name]) if hints is not None else _classify_string(raw)
        if kind == "list":
            setattr(cls, name, dataclasses.field(default_factory=list))
        elif kind == "dict":
            setattr(cls, name, dataclasses.field(default_factory=dict))
        elif kind == "optional":
            setattr(cls, name, None)


def _classify(hint: Any) -> str | None:
    inner, _ = split_annotated(hint)
    members, optional = split_union(inner)
    if optional or len(members) > 1:
        # `T | None` and oneofs (`A | B`) both default to None
        return "optional"
    inner, _ = split_annotated(members[0])
    origin = getattr(inner, "__origin__", None)
    if origin is list:
        return "list"
    if origin is dict:
        return "dict"
    return None


def _classify_string(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return _classify(raw)
    text = raw.strip()
    if text.startswith("Annotated["):
        text = text[len("Annotated[") :].split(",", 1)[0].strip()
    if _STRING_OPTIONAL.search(text) or _top_level_union(text) or text.startswith(("Union[", "typing.Union[")):
        return "optional"
    if _STRING_LIST.match(text):
        return "list"
    if _STRING_DICT.match(text):
        return "dict"
    return None


def _top_level_union(text: str) -> bool:
    """True for ``A | B`` but not for ``list[A | B]``."""
    depth = 0
    for ch in text:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        elif ch == "|" and depth == 0:
            return True
    return False
