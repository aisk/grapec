import asyncio
import ssl

import pytest
import pytest_asyncio

import grapec
from test_client import HelloReply, HelloRequest

pytestmark = pytest.mark.asyncio


class AsyncGreeter(grapec.AsyncClient, package="test.rpc", name="Greeter"):
    @grapec.name("SayHello")
    async def say_hello(self, request: HelloRequest) -> HelloReply: ...

    @grapec.name("Fail")
    async def fail(self, request: HelloRequest) -> HelloReply: ...

    @grapec.name("Slow")
    async def slow(self, request: HelloRequest) -> HelloReply: ...

    @grapec.name("Compressed")
    async def compressed(self, request: HelloRequest) -> HelloReply: ...


@pytest.fixture
def url(rpc_server):
    return f"grpc://127.0.0.1:{rpc_server}"


@pytest_asyncio.fixture
async def greeter(url):
    g = AsyncGreeter(url)
    yield g
    await grapec.aclose(g)


async def test_unary_call(greeter):
    reply = await greeter.say_hello(HelloRequest(name="grapec"))
    assert isinstance(reply, HelloReply)
    assert reply.message == "hello grapec 0"


async def test_large_payload(greeter):
    blob = bytes(range(256)) * 4096
    reply = await greeter.say_hello(HelloRequest(name="big", blob=blob))
    assert reply.message == f"hello big {len(blob)}"


async def test_metadata(greeter):
    reply = await greeter.say_hello(HelloRequest(name="m"), metadata={"x-trace": "abc"})
    assert reply.metadata["x-trace"] == "abc"


async def test_rpc_error_keeps_connection(greeter):
    with pytest.raises(grapec.RpcError) as info:
        await greeter.fail(HelloRequest(name="zed"))
    assert info.value.code is grapec.Status.INVALID_ARGUMENT
    assert info.value.message == "bad name: zed"
    assert (await greeter.say_hello(HelloRequest(name="ok"))).message == "hello ok 0"
    assert len(grapec.session_of(greeter)._pool) == 1


async def test_timeout(greeter):
    with pytest.raises(grapec.RpcError) as info:
        await greeter.slow(HelloRequest(name="x"), timeout=0.2)
    assert info.value.code is grapec.Status.DEADLINE_EXCEEDED
    assert (await greeter.say_hello(HelloRequest(name="after"))).message == "hello after 0"


async def test_compression(greeter):
    reply = await greeter.compressed(HelloRequest(name="z"))
    assert reply.message == "x" * 10000 + "z"
    reply = await greeter.say_hello(HelloRequest(name="c", blob=b"\x00" * 50000), compression="gzip")
    assert reply.message == "hello c 50000"


async def test_concurrent_calls_share_pool(url):
    g = AsyncGreeter(url, max_idle=2)
    results = await asyncio.gather(*(g.say_hello(HelloRequest(name=str(i))) for i in range(8)))
    assert sorted(r.message for r in results) == sorted(f"hello {i} 0" for i in range(8))
    assert len(grapec.session_of(g)._pool) <= 2
    await grapec.aclose(g)


async def test_shared_session(url):
    async with grapec.AsyncSession(url) as session:
        a = AsyncGreeter(session)
        b = AsyncGreeter(session)
        assert (await a.say_hello(HelloRequest(name="a"))).message == "hello a 0"
        assert (await b.say_hello(HelloRequest(name="b"))).message == "hello b 0"
        assert len(session._pool) == 1
        # the low level entry point accepts sync declared methods too
        from test_client import Greeter

        assert (await session.call(Greeter.say_hello, HelloRequest(name="c"))).message == "hello c 0"


async def test_closed_client(url):
    g = AsyncGreeter(url)
    await g.say_hello(HelloRequest(name="x"))
    await grapec.aclose(g)
    with pytest.raises(grapec.TransportError):
        await g.say_hello(HelloRequest(name="x"))


async def test_connection_refused():
    g = AsyncGreeter("grpc://127.0.0.1:1", connect_timeout=1)
    with pytest.raises(grapec.TransportError):
        await g.say_hello(HelloRequest(name="x"))


async def test_wrong_session_type(url):
    with pytest.raises(TypeError):
        AsyncGreeter(grapec.Session(url))


async def test_wrong_request_type(greeter):
    with pytest.raises(TypeError):
        await greeter.say_hello(HelloReply(message="x"))  # type: ignore[arg-type]


def _idle_connection(client):
    return grapec.session_of(client)._pool._idle[0][0]


async def test_cancellation_does_not_poison_the_pool(greeter):
    await greeter.say_hello(HelloRequest(name="warm"))
    task = asyncio.ensure_future(greeter.slow(HelloRequest(name="x")))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(grapec.session_of(greeter)._pool) == 0
    assert (await greeter.say_hello(HelloRequest(name="after"))).message == "hello after 0"


async def test_timeout_keeps_connection(greeter):
    await greeter.say_hello(HelloRequest(name="warm"))
    conn = _idle_connection(greeter)
    with pytest.raises(grapec.RpcError):
        await greeter.slow(HelloRequest(name="x"), timeout=0.2)
    assert _idle_connection(greeter) is conn
    assert (await greeter.say_hello(HelloRequest(name="after"))).message == "hello after 0"
    assert _idle_connection(greeter) is conn


async def test_stale_connection_after_goaway_is_dropped(serve):
    url = f"grpc://127.0.0.1:{serve([('grpc.max_connection_age_ms', 200), ('grpc.max_connection_age_grace_ms', 100)])}"
    g = AsyncGreeter(url)
    assert (await g.say_hello(HelloRequest(name="a"))).message == "hello a 0"
    first = _idle_connection(g)
    await asyncio.sleep(0.8)
    assert (await g.say_hello(HelloRequest(name="b"))).message == "hello b 0"
    assert _idle_connection(g) is not first
    await grapec.aclose(g)


async def test_call_details_and_error_metadata(greeter):
    details = grapec.CallDetails()
    await greeter.say_hello(HelloRequest(name="d"), details=details)
    assert details.headers["x-initial"] == "yes"
    assert details.trailers["x-echo"] == "d"
    with pytest.raises(grapec.RpcError) as info:
        await greeter.fail(HelloRequest(name="e"))
    assert info.value.trailers["x-reason"] == "test"


async def test_tls(tls_rpc_server):
    port, cert = tls_rpc_server
    ctx = ssl.create_default_context(cafile=str(cert))
    g = AsyncGreeter(f"grpcs://localhost:{port}", ssl=ctx)
    assert (await g.say_hello(HelloRequest(name="tls"))).message == "hello tls 0"
    await grapec.aclose(g)
    g = AsyncGreeter(f"grpcs://localhost:{port}", connect_timeout=2)
    with pytest.raises(grapec.TransportError):
        await g.say_hello(HelloRequest(name="tls"))
