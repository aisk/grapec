import importlib
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture(scope="session")
def oracle(tmp_path_factory):
    """Compile tests/oracle.proto with protoc and return the pb2 module."""
    import grpc_tools
    from grpc_tools import protoc

    out = tmp_path_factory.mktemp("oracle")
    well_known = Path(grpc_tools.__file__).parent / "_proto"
    rc = protoc.main([
        "protoc",
        f"-I{HERE}",
        f"-I{well_known}",
        f"--python_out={out}",
        str(HERE / "oracle.proto"),
    ])
    assert rc == 0
    sys.path.insert(0, str(out))
    try:
        return importlib.import_module("oracle_pb2")
    finally:
        sys.path.remove(str(out))
