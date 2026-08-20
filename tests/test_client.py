import threading
from typing import Unpack

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


class Greeter(grapec.Client, package="test.rpc"):
    @grapec.name("SayHello")
    def say_hello(self, request: HelloRequest, **options: Unpack[grapec.CallOptions]) -> HelloReply:
        """Say hello."""

    @grapec.name("Fail")
    def fail(self, request: HelloRequest) -> HelloReply: ...

    @grapec.name("Slow")
    def slow(self, request: HelloRequest) -> HelloReply: ...

    @grapec.name("Missing")
    def missing(self, request: HelloRequest) -> HelloReply: ...

    @grapec.name("Compressed")
    def compressed(self, request: HelloRequest) -> HelloReply: ...


@pytest.fixture
def url(rpc_server):
    return f"grpc://127.0.0.1:{rpc_server}"


@pytest.fixture
def greeter(url):
    g = Greeter(url)
    yield g
    grapec.close(g)


def test_unary_call(greeter):
    reply = greeter.say_hello(HelloRequest(name="grapec"))
    assert isinstance(reply, HelloReply)
    assert reply.message == "hello grapec 0"
    assert reply.metadata["user-agent"].startswith("grapec")


def test_method_introspection():
    assert Greeter.say_hello.__name__ == "say_hello"
    assert Greeter.say_hello.__doc__ == "Say hello."
    assert repr(Greeter.say_hello) == "<remote method /test.rpc.Greeter/SayHello>"
    assert repr(Greeter("grpc://h:1")) == "<Greeter grpc://h:1>"


def test_large_payload_crosses_flow_control_window(greeter):
    blob = bytes(range(256)) * 4096  # 1 MiB, larger than the default 64 KiB window
    reply = greeter.say_hello(HelloRequest(name="big", blob=blob))
    assert reply.message == f"hello big {len(blob)}"


def test_metadata(greeter):
    reply = greeter.say_hello(HelloRequest(name="m"), metadata={"x-trace": "abc", "x-raw-bin": b"\x00\xff"})
    assert reply.metadata["x-trace"] == "abc"
    assert reply.metadata["x-raw-bin"] == "00ff"


def test_unknown_call_option(greeter):
    with pytest.raises(TypeError, match="retries"):
        greeter.say_hello(HelloRequest(name="m"), retries=3)


def test_compressed_response(greeter):
    reply = greeter.compressed(HelloRequest(name="z"))
    assert reply.message == "x" * 10000 + "z"


@pytest.mark.parametrize("algo", ["gzip", "deflate"])
def test_compressed_request(url, algo, monkeypatch):
    from grapec import _grpc

    calls = []
    original = _grpc._ENCODERS[algo]
    monkeypatch.setitem(_grpc._ENCODERS, algo, lambda data: calls.append(len(data)) or original(data))

    blob = b"\x00" * 100_000
    g = Greeter(url, compression=algo)
    reply = g.say_hello(HelloRequest(name="c", blob=blob))
    assert reply.message == f"hello c {len(blob)}"
    assert calls == [len(bytes(HelloRequest(name="c", blob=blob)))]
    g.say_hello(HelloRequest(name="c"), compression="identity")
    assert len(calls) == 1
    grapec.close(g)


def test_unknown_compression(greeter):
    with pytest.raises(ValueError):
        greeter.say_hello(HelloRequest(name="c"), compression="brotli")


def test_reserved_metadata_rejected(greeter):
    with pytest.raises(ValueError):
        greeter.say_hello(HelloRequest(name="m"), metadata={"grpc-timeout": "1S"})
    with pytest.raises(ValueError):
        greeter.say_hello(HelloRequest(name="m"), metadata={"x-raw": b"\x00"})


def test_rpc_error(greeter):
    with pytest.raises(grapec.RpcError) as info:
        greeter.fail(HelloRequest(name="zed"))
    assert info.value.code is grapec.Status.INVALID_ARGUMENT
    assert info.value.message == "bad name: zed"
    # the connection is still fine after an application level error
    assert greeter.say_hello(HelloRequest(name="ok")).message == "hello ok 0"


def test_unimplemented(greeter):
    with pytest.raises(grapec.RpcError) as info:
        greeter.missing(HelloRequest(name="x"))
    assert info.value.code is grapec.Status.UNIMPLEMENTED


def test_timeout(greeter):
    with pytest.raises(grapec.RpcError) as info:
        greeter.slow(HelloRequest(name="x"), timeout=0.2)
    assert info.value.code is grapec.Status.DEADLINE_EXCEEDED
    assert greeter.say_hello(HelloRequest(name="after")).message == "hello after 0"


def test_default_timeout(url):
    g = Greeter(url, timeout=0.2)
    with pytest.raises(grapec.RpcError):
        g.slow(HelloRequest(name="x"))
    assert g.slow(HelloRequest(name="x"), timeout=5).message == "late"
    grapec.close(g)


def test_wrong_request_type(greeter):
    with pytest.raises(TypeError):
        greeter.say_hello(HelloReply(message="x"))  # type: ignore[arg-type]


def test_pool_reuses_connections(greeter):
    for _ in range(3):
        greeter.say_hello(HelloRequest(name="x"))
    assert len(grapec.session_of(greeter)._pool) == 1


def test_pool_concurrent_calls(url):
    g = Greeter(url, max_idle=2)
    results = []

    def work(i):
        results.append(g.say_hello(HelloRequest(name=str(i))).message)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == sorted(f"hello {i} 0" for i in range(8))
    assert len(grapec.session_of(g)._pool) <= 2
    grapec.close(g)


def test_shared_session(url):
    class Other(grapec.Client, package="test.rpc", name="Greeter"):
        @grapec.name("SayHello")
        def hi(self, request: HelloRequest) -> HelloReply: ...

    session = grapec.Session(url, max_idle=1)
    a = Greeter(session)
    b = Other(session)
    assert a.say_hello(HelloRequest(name="a")).message == "hello a 0"
    assert b.hi(HelloRequest(name="b")).message == "hello b 0"
    assert grapec.session_of(a) is grapec.session_of(b) is session
    grapec.close(a)  # shared session is left alone
    assert b.hi(HelloRequest(name="b")).message == "hello b 0"
    session.close()
    with pytest.raises(grapec.TransportError):
        a.say_hello(HelloRequest(name="a"))


def test_session_call_low_level(url):
    with grapec.Session(url) as session:
        reply = session.call(Greeter.say_hello, HelloRequest(name="low"))
        assert reply.message == "hello low 0"


def test_constructor_validation(url):
    with pytest.raises(TypeError):
        Greeter(grapec.Session(url), timeout=1)
    with pytest.raises(TypeError):
        Greeter(grapec.AsyncSession(url))
    with pytest.raises(TypeError):
        Greeter(42)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Greeter("http://localhost:1")


def test_closed_client(url):
    g = Greeter(url)
    g.say_hello(HelloRequest(name="x"))
    grapec.close(g)
    with pytest.raises(grapec.TransportError):
        g.say_hello(HelloRequest(name="x"))


def test_connection_refused():
    g = Greeter("grpc://127.0.0.1:1", connect_timeout=1)
    with pytest.raises(grapec.TransportError):
        g.say_hello(HelloRequest(name="x"))


def test_uninitialised_subclass():
    class Sneaky(Greeter):
        def __init__(self):
            pass

    with pytest.raises(TypeError, match="not initialised"):
        Sneaky().say_hello(HelloRequest(name="x"))


def test_inherits_package():
    from grapec._service import method_of

    class Child(Greeter):
        def extra(self, request: HelloRequest) -> HelloReply: ...

    assert method_of(Child.extra).path == "/test.rpc.Child/extra"
    assert method_of(Child.say_hello).path == "/test.rpc.Greeter/SayHello"


def test_service_signature_validation():
    with pytest.raises(grapec.SchemaError):

        class Bad(grapec.Client, package="t"):
            def two(self, a: HelloRequest, b: HelloRequest) -> HelloReply: ...

    with pytest.raises(grapec.SchemaError):

        class Bad2(grapec.Client, package="t"):
            def untyped(self, request) -> HelloReply: ...

    with pytest.raises(grapec.SchemaError):

        class Bad3(grapec.Client, package="t"):
            def plain_return(self, request: HelloRequest) -> int: ...

    with pytest.raises(grapec.SchemaError, match="async def"):

        class Bad4(grapec.AsyncClient, package="t"):
            def sync_method(self, request: HelloRequest) -> HelloReply: ...

    with pytest.raises(grapec.SchemaError, match="`def`"):

        class Bad5(grapec.Client, package="t"):
            async def async_method(self, request: HelloRequest) -> HelloReply: ...

    with pytest.raises(grapec.SchemaError, match="package"):

        class Bad6(grapec.Client):
            pass

    with pytest.raises(grapec.SchemaError):

        class Bad7(grapec.Client, package="1bad"):
            pass


def test_service_names():
    from grapec._service import method_of

    class S(grapec.Client, package="a.b", name="Svc"):
        def raw(self, request: HelloRequest) -> HelloReply: ...

        @grapec.name("Renamed")
        def renamed(self, request: HelloRequest) -> HelloReply: ...

        def _helper(self):
            pass

    assert method_of(S.raw).path == "/a.b.Svc/raw"
    assert method_of(S.renamed).path == "/a.b.Svc/Renamed"
    assert S._helper.__name__ == "_helper"
