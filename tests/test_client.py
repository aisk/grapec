import socket
import ssl
import threading
import time
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
    from grapec import grpc as _grpc

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


def test_max_conns_caps_open_connections(url):
    """Eight concurrent calls through a cap of two never open a third connection."""
    g = Greeter(url, max_conns=2, max_idle=2)
    session = grapec.session_of(g)
    factory = session._factory
    made = []
    lock = threading.Lock()
    paired = threading.Barrier(2, timeout=10)  # only passable if two calls really run at once

    def counting():
        conn = factory()
        unary = conn.unary

        def held_unary(*args, **kwargs):
            paired.wait()
            return unary(*args, **kwargs)

        conn.unary = held_unary
        with lock:
            made.append(conn)
        return conn

    session._factory = counting
    results = []

    def work(i):
        results.append(g.say_hello(HelloRequest(name=str(i))).message)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == sorted(f"hello {i} 0" for i in range(8))
    assert len(made) == 2
    assert len(session._pool) == 2
    grapec.close(g)


def test_pool_timeout_when_all_connections_are_busy(url):
    g = Greeter(url, max_conns=1, pool_timeout=0.05)
    session = grapec.session_of(g)
    busy = session._acquire()  # the one allowed connection is checked out
    with pytest.raises(grapec.RpcError) as exc:
        g.say_hello(HelloRequest(name="x"))
    assert exc.value.code is grapec.Status.RESOURCE_EXHAUSTED
    session._release(busy)
    assert g.say_hello(HelloRequest(name="ok")).message == "hello ok 0"
    grapec.close(g)


def test_failed_connect_gives_its_permit_back(url):
    g = Greeter(url, max_conns=1, pool_timeout=0.05)
    session = grapec.session_of(g)
    factory = session._factory

    def refuse():
        raise grapec.TransportError("no route")

    session._factory = refuse
    for _ in range(3):
        with pytest.raises(grapec.TransportError):  # not RESOURCE_EXHAUSTED, the permit came back
            g.say_hello(HelloRequest(name="x"))
    session._factory = factory
    assert g.say_hello(HelloRequest(name="ok")).message == "hello ok 0"
    grapec.close(g)


def test_invalid_pool_options(url):
    with pytest.raises(ValueError):
        grapec.Session(url, max_conns=0)
    with pytest.raises(ValueError):
        grapec.Session(url, pool_timeout=-1)


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
    from grapec.service import method_of

    class Child(Greeter):
        def extra(self, request: HelloRequest) -> HelloReply: ...

    assert method_of(Child.extra).path == "/test.rpc.Child/extra"
    assert method_of(Child.say_hello).path == "/test.rpc.Greeter/SayHello"


def test_service_signature_validation():
    # several parameters and plain returns are fine for thrift, gRPC rejects them when the client is built
    class Multi(grapec.Client, package="t"):
        def two(self, a: HelloRequest, b: HelloRequest) -> HelloReply: ...

    class Plain(grapec.Client, package="t"):
        def plain_return(self, request: HelloRequest) -> int: ...

    for cls in (Multi, Plain):
        with pytest.raises(grapec.SchemaError, match="gRPC methods take exactly one struct"):
            cls("grpc://127.0.0.1:1")
        with pytest.raises(grapec.SchemaError):
            grapec.export_proto(cls)

    with pytest.raises(grapec.SchemaError, match="type annotation"):

        class Bad2(grapec.Client, package="t"):
            def untyped(self, request) -> HelloReply: ...

    with pytest.raises(grapec.SchemaError, match="return annotation"):

        class Bad3(grapec.Client, package="t"):
            def no_return(self, request: HelloRequest): ...

    class OptionalRequest(grapec.Client, package="t"):
        def call(self, request: HelloRequest | None) -> HelloReply: ...

    with pytest.raises(grapec.SchemaError, match="gRPC methods take exactly one struct"):
        OptionalRequest("grpc://127.0.0.1:1")

    with pytest.raises(grapec.SchemaError, match="clashes with a call option"):

        class Bad8(grapec.Client, package="t"):
            def clash(self, timeout: int) -> None: ...

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
    from grapec.service import method_of

    class S(grapec.Client, package="a.b", name="Svc"):
        def raw(self, request: HelloRequest) -> HelloReply: ...

        @grapec.name("Renamed")
        def renamed(self, request: HelloRequest) -> HelloReply: ...

        def _helper(self):
            pass

    assert method_of(S.raw).path == "/a.b.Svc/raw"
    assert method_of(S.renamed).path == "/a.b.Svc/Renamed"
    assert S._helper.__name__ == "_helper"


def _idle_connection(client):
    return grapec.session_of(client)._pool._idle[0][0]


def test_timeout_keeps_connection(greeter):
    greeter.say_hello(HelloRequest(name="warm"))
    conn = _idle_connection(greeter)
    with pytest.raises(grapec.RpcError) as info:
        greeter.slow(HelloRequest(name="x"), timeout=0.2)
    assert info.value.code is grapec.Status.DEADLINE_EXCEEDED
    assert _idle_connection(greeter) is conn
    assert greeter.say_hello(HelloRequest(name="after")).message == "hello after 0"
    assert _idle_connection(greeter) is conn


def test_socket_timeout_is_set_per_call(greeter):
    greeter.say_hello(HelloRequest(name="a"), timeout=5)
    conn = _idle_connection(greeter)
    conn._sock.settimeout(0)  # whatever a previous call left behind must not matter
    blob = bytes(range(256)) * 4096
    assert greeter.say_hello(HelloRequest(name="big", blob=blob)).message == f"hello big {len(blob)}"
    assert conn._sock.gettimeout() is None


def test_stale_connection_after_goaway_is_dropped(serve):
    url = f"grpc://127.0.0.1:{serve([('grpc.max_connection_age_ms', 200), ('grpc.max_connection_age_grace_ms', 100)])}"
    g = Greeter(url)
    assert g.say_hello(HelloRequest(name="a")).message == "hello a 0"
    first = _idle_connection(g)
    time.sleep(0.8)
    assert g.say_hello(HelloRequest(name="b")).message == "hello b 0"
    assert _idle_connection(g) is not first
    assert not first.healthy
    grapec.close(g)


def test_max_idle_time_evicts_old_connections(url):
    now = [100.0]
    g = Greeter(url, max_idle_time=30)
    grapec.session_of(g)._pool._clock = lambda: now[0]
    g.say_hello(HelloRequest(name="a"))
    old = _idle_connection(g)
    now[0] += 10
    g.say_hello(HelloRequest(name="b"))
    assert _idle_connection(g) is old
    now[0] += 31
    g.say_hello(HelloRequest(name="c"))
    assert _idle_connection(g) is not old
    assert not old.healthy
    assert len(grapec.session_of(g)._pool) == 1
    grapec.close(g)


def test_call_details_and_error_metadata(greeter):
    details = grapec.CallDetails()
    greeter.say_hello(HelloRequest(name="d"), details=details)
    assert details.headers["x-initial"] == "yes"
    assert details.trailers["x-echo"] == "d"
    assert details.trailers["x-raw-bin"] == b"\x01\x02"
    assert ":status" not in details.headers and "grpc-status" not in details.trailers

    details = grapec.CallDetails()
    with pytest.raises(grapec.RpcError) as info:
        greeter.fail(HelloRequest(name="e"), details=details)
    assert info.value.trailers["x-reason"] == "test"
    assert details.trailers == info.value.trailers
    assert "grpc-message" not in info.value.trailers

    with grapec.Session(grapec.session_of(greeter).url) as session:
        details = grapec.CallDetails()
        session.call(Greeter.say_hello, HelloRequest(name="s"), details=details)
        assert details.trailers["x-echo"] == "s"


def test_bound_methods_are_cached_and_usable_with_session_call(greeter):
    assert greeter.say_hello is greeter.say_hello
    session = grapec.session_of(greeter)
    assert session.call(greeter.say_hello, HelloRequest(name="b")).message == "hello b 0"


def test_subclass_with_name_rebinds_inherited_methods():
    from grapec.service import method_of

    class Renamed(Greeter, name="Greeter2"):
        def extra(self, request: HelloRequest) -> HelloReply: ...

    assert method_of(Renamed.say_hello).path == "/test.rpc.Greeter2/SayHello"
    assert method_of(Renamed.extra).path == "/test.rpc.Greeter2/extra"
    assert method_of(Greeter.say_hello).path == "/test.rpc.Greeter/SayHello"


def test_ipv6_authority():
    from grapec.grpc import authority

    assert authority("::1", 50051) == "[::1]:50051"
    assert authority("localhost", 50051) == "localhost:50051"


def test_ssl_option_validation(url):
    with pytest.raises(ValueError):
        grapec.Session(url, ssl=ssl.create_default_context())
    with pytest.raises(TypeError):
        grapec.Session("grpcs://localhost:1", ssl="yes")  # type: ignore[arg-type]


def test_tls(tls_rpc_server):
    port, cert = tls_rpc_server
    ctx = ssl.create_default_context(cafile=str(cert))
    g = Greeter(f"grpcs://localhost:{port}", ssl=ctx)
    assert g.say_hello(HelloRequest(name="tls")).message == "hello tls 0"
    assert g.say_hello(HelloRequest(name="again")).message == "hello again 0"
    grapec.close(g)
    # the default context does not trust the test certificate
    g = Greeter(f"grpcs://localhost:{port}", connect_timeout=2)
    with pytest.raises(grapec.TransportError):
        g.say_hello(HelloRequest(name="tls"))


def test_keyboard_interrupt_does_not_poison_the_pool(url, monkeypatch):
    class Interrupting:
        def __init__(self, sock):
            self._sock = sock
            self.calls = 0

        def recv(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise BlockingIOError  # the idle poll before the call sees nothing
            raise KeyboardInterrupt

        def __getattr__(self, name):
            return getattr(self._sock, name)

    g = Greeter(url)
    g.say_hello(HelloRequest(name="warm"))
    conn = _idle_connection(g)
    real = conn._sock
    monkeypatch.setattr(conn, "_sock", Interrupting(real))
    with pytest.raises(KeyboardInterrupt):
        g.say_hello(HelloRequest(name="x"))
    assert not conn.healthy
    assert len(grapec.session_of(g)._pool) == 0
    assert g.say_hello(HelloRequest(name="y")).message == "hello y 0"
    grapec.close(g)
