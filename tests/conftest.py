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
        f"--grpc_python_out={out}",
        str(HERE / "oracle.proto"),
        str(HERE / "rpc.proto"),
    ])
    assert rc == 0
    sys.path.insert(0, str(out))
    return importlib.import_module("oracle_pb2")


@pytest.fixture(scope="session")
def rpc_server(oracle):
    """A real grpcio server for tests/rpc.proto, yields its port."""
    import time
    from concurrent import futures

    import grpc

    import rpc_pb2
    import rpc_pb2_grpc

    class Greeter(rpc_pb2_grpc.GreeterServicer):
        def SayHello(self, request, context):
            md = {k: (v if isinstance(v, str) else v.hex()) for k, v in context.invocation_metadata()}
            context.set_trailing_metadata((("x-echo", request.name),))
            return rpc_pb2.HelloReply(message=f"hello {request.name} {len(request.blob)}", metadata=md)

        def Fail(self, request, context):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "bad name: " + request.name)

        def Slow(self, request, context):
            time.sleep(1)
            return rpc_pb2.HelloReply(message="late")

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    rpc_pb2_grpc.add_GreeterServicer_to_server(Greeter(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    yield port
    server.stop(0)
