import threading

import pytest

import grapec


@grapec.struct(package="test.rpc")
class HelloRequest:
    name: str
    blob: bytes = b""


@grapec.struct(package="test.rpc")
class HelloReply:
    message: str
    metadata: dict[str, str]


@grapec.service(package="test.rpc")
class Greeter:
    @grapec.name("SayHello")
    def say_hello(self, request: HelloRequest) -> HelloReply: ...

    @grapec.name("Fail")
    def fail(self, request: HelloRequest) -> HelloReply: ...

    @grapec.name("Slow")
    def slow(self, request: HelloRequest) -> HelloReply: ...

    @grapec.name("Missing")
    def missing(self, request: HelloRequest) -> HelloReply: ...


@pytest.fixture
def client(rpc_server):
    with grapec.Client(f"grpc://127.0.0.1:{rpc_server}") as c:
        yield c


def test_unary_call(client):
    reply = client.call(Greeter.say_hello, HelloRequest(name="grapec"))
    assert isinstance(reply, HelloReply)
    assert reply.message == "hello grapec 0"
    assert reply.metadata["user-agent"].startswith("grapec")


def test_large_payload_crosses_flow_control_window(client):
    blob = bytes(range(256)) * 4096  # 1 MiB, larger than the default 64 KiB window
    reply = client.call(Greeter.say_hello, HelloRequest(name="big", blob=blob))
    assert reply.message == f"hello big {len(blob)}"


def test_metadata(client):
    reply = client.call(
        Greeter.say_hello,
        HelloRequest(name="m"),
        metadata={"x-trace": "abc", "x-raw-bin": b"\x00\xff"},
    )
    assert reply.metadata["x-trace"] == "abc"
    assert reply.metadata["x-raw-bin"] == "00ff"


def test_reserved_metadata_rejected(client):
    with pytest.raises(ValueError):
        client.call(Greeter.say_hello, HelloRequest(name="m"), metadata={"grpc-timeout": "1S"})
    with pytest.raises(ValueError):
        client.call(Greeter.say_hello, HelloRequest(name="m"), metadata={"x-raw": b"\x00"})


def test_rpc_error(client):
    with pytest.raises(grapec.RpcError) as info:
        client.call(Greeter.fail, HelloRequest(name="zed"))
    assert info.value.code is grapec.Status.INVALID_ARGUMENT
    assert info.value.message == "bad name: zed"
    # the connection is still fine after an application level error
    assert client.call(Greeter.say_hello, HelloRequest(name="ok")).message == "hello ok 0"


def test_unimplemented(client):
    with pytest.raises(grapec.RpcError) as info:
        client.call(Greeter.missing, HelloRequest(name="x"))
    assert info.value.code is grapec.Status.UNIMPLEMENTED


def test_timeout(client):
    with pytest.raises(grapec.RpcError) as info:
        client.call(Greeter.slow, HelloRequest(name="x"), timeout=0.2)
    assert info.value.code is grapec.Status.DEADLINE_EXCEEDED
    assert client.call(Greeter.say_hello, HelloRequest(name="after")).message == "hello after 0"


def test_default_timeout(rpc_server):
    with grapec.Client(f"grpc://127.0.0.1:{rpc_server}", timeout=0.2) as c:
        with pytest.raises(grapec.RpcError):
            c.call(Greeter.slow, HelloRequest(name="x"))
        assert c.call(Greeter.slow, HelloRequest(name="x"), timeout=5).message == "late"


def test_wrong_request_type(client):
    with pytest.raises(TypeError):
        client.call(Greeter.say_hello, HelloReply(message="x"))  # type: ignore[arg-type]


def test_not_a_service_method(client):
    def plain(self, request: HelloRequest) -> HelloReply: ...

    with pytest.raises(grapec.SchemaError):
        client.call(plain, HelloRequest(name="x"))


def test_pool_reuses_connections(client):
    for _ in range(3):
        client.call(Greeter.say_hello, HelloRequest(name="x"))
    assert len(client._idle) == 1


def test_pool_concurrent_calls(rpc_server):
    with grapec.Client(f"grpc://127.0.0.1:{rpc_server}", max_idle=2) as c:
        results = []

        def work(i):
            results.append(c.call(Greeter.say_hello, HelloRequest(name=str(i))).message)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == sorted(f"hello {i} 0" for i in range(8))
        assert len(c._idle) <= 2


def test_closed_client(rpc_server):
    c = grapec.Client(f"grpc://127.0.0.1:{rpc_server}")
    c.call(Greeter.say_hello, HelloRequest(name="x"))
    c.close()
    with pytest.raises(grapec.TransportError):
        c.call(Greeter.say_hello, HelloRequest(name="x"))


def test_connection_refused():
    with grapec.Client("grpc://127.0.0.1:1", connect_timeout=1) as c:
        with pytest.raises(grapec.TransportError):
            c.call(Greeter.say_hello, HelloRequest(name="x"))


def test_unsupported_scheme():
    with pytest.raises(ValueError):
        grapec.Client("http://localhost:1")


def test_service_signature_validation():
    with pytest.raises(grapec.SchemaError):

        @grapec.service(package="t")
        class Bad:
            def two(self, a: HelloRequest, b: HelloRequest) -> HelloReply: ...

    with pytest.raises(grapec.SchemaError):

        @grapec.service(package="t")
        class Bad2:
            def untyped(self, request) -> HelloReply: ...

    with pytest.raises(grapec.SchemaError):

        @grapec.service(package="t")
        class Bad3:
            def plain_return(self, request: HelloRequest) -> int: ...


def test_service_names():
    from grapec._service import method_of

    @grapec.service(package="a.b", name="Svc")
    class S:
        def raw(self, request: HelloRequest) -> HelloReply: ...

        @grapec.name("Renamed")
        def renamed(self, request: HelloRequest) -> HelloReply: ...

        def _helper(self):
            pass

    assert method_of(S.raw).path == "/a.b.Svc/raw"
    assert method_of(S.renamed).path == "/a.b.Svc/Renamed"
