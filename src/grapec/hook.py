from __future__ import annotations

import importlib.abc
import importlib.util
import sys
import types
from pathlib import Path

from . import load

_PB2_SUFFIX = "_pb2"
_PB2_GRPC_SUFFIX = "_pb2_grpc"


def _parse_proto_stem(fullname: str) -> tuple[str, bool] | None:
    if "." in fullname:
        return None
    if fullname.endswith(_PB2_GRPC_SUFFIX):
        stem = fullname[: -len(_PB2_GRPC_SUFFIX)]
        return (stem, True) if stem else None
    if fullname.endswith(_PB2_SUFFIX):
        stem = fullname[: -len(_PB2_SUFFIX)]
        return (stem, False) if stem else None
    return None


def _find_proto_file(proto_stem: str) -> Path | None:
    proto_name = f"{proto_stem}.proto"
    for root in [Path.cwd(), *(Path(item) for item in sys.path if item)]:
        candidate = root / proto_name
        if candidate.is_file():
            return candidate.resolve()
    return None


class _ProtoModuleLoader(importlib.abc.Loader):
    def __init__(self, fullname: str, proto_file: Path, is_grpc: bool) -> None:
        self._fullname = fullname
        self._proto_file = proto_file
        self._is_grpc = is_grpc

    def exec_module(self, module: types.ModuleType) -> None:
        modules = load(str(self._proto_file))
        source_module = modules[1] if self._is_grpc else modules[0]
        for key, value in source_module.__dict__.items():
            if key in {"__name__", "__loader__", "__package__", "__spec__"}:
                continue
            module.__dict__[key] = value


class _ProtoModuleFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        parsed = _parse_proto_stem(fullname)
        if parsed is None:
            return None
        proto_stem, is_grpc = parsed
        proto_file = _find_proto_file(proto_stem)
        if proto_file is None:
            return None
        loader = _ProtoModuleLoader(fullname, proto_file, is_grpc)
        return importlib.util.spec_from_loader(fullname, loader, origin=str(proto_file))


_FINDER = _ProtoModuleFinder()


def install() -> None:
    if _FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _FINDER)


def uninstall() -> None:
    if _FINDER in sys.meta_path:
        sys.meta_path.remove(_FINDER)
