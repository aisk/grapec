import importlib
import shutil
import subprocess
import sys
import time
from concurrent import futures
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


def _servicer():
    import grpc
    import rpc_pb2
    import rpc_pb2_grpc

    class Greeter(rpc_pb2_grpc.GreeterServicer):
        def SayHello(self, request, context):
            md = {k: (v if isinstance(v, str) else v.hex()) for k, v in context.invocation_metadata()}
            context.send_initial_metadata((("x-initial", "yes"),))
            context.set_trailing_metadata((("x-echo", request.name), ("x-raw-bin", b"\x01\x02")))
            return rpc_pb2.HelloReply(message=f"hello {request.name} {len(request.blob)}", metadata=md)

        def Fail(self, request, context):
            context.set_trailing_metadata((("x-reason", "test"),))
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "bad name: " + request.name)

        def Slow(self, request, context):
            time.sleep(1)
            return rpc_pb2.HelloReply(message="late")

        def Compressed(self, request, context):
            context.set_compression(grpc.Compression.Gzip)
            return rpc_pb2.HelloReply(message="x" * 10000 + request.name)

    return Greeter()


def _start_server(options=None, credentials=None, host="127.0.0.1"):
    import grpc
    import rpc_pb2_grpc

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4), options=list(options or []))
    rpc_pb2_grpc.add_GreeterServicer_to_server(_servicer(), server)
    if credentials is None:
        port = server.add_insecure_port(f"{host}:0")
    else:
        port = server.add_secure_port(f"{host}:0", credentials)
    server.start()
    return server, port


@pytest.fixture(scope="session")
def rpc_server(oracle):
    """A real grpcio server for tests/rpc.proto, yields its port."""
    server, port = _start_server()
    yield port
    server.stop(0)


@pytest.fixture
def serve(oracle):
    """Factory for short lived servers with custom channel options, returns the port."""
    servers = []

    def start(options=None):
        server, port = _start_server(options)
        servers.append(server)
        return port

    yield start
    for server in servers:
        server.stop(0)


@pytest.fixture(scope="session")
def tls_cert(tmp_path_factory):
    """A self signed certificate for localhost, (certfile, keyfile). Skips without openssl."""
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is not installed")
    out = tmp_path_factory.mktemp("tls")
    cert, key = out / "cert.pem", out / "key.pem"
    subprocess.run(
        [
            openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


@pytest.fixture(scope="session")
def tls_rpc_server(oracle, tls_cert):
    """A grpcio server behind TLS, yields (port, certfile)."""
    import grpc

    cert, key = tls_cert
    creds = grpc.ssl_server_credentials([(key.read_bytes(), cert.read_bytes())])
    server, port = _start_server(credentials=creds)
    yield port, cert
    server.stop(0)
