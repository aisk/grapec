import asyncio

import pytest
import pytest_asyncio

import grapec
from test_client import Greeter, HelloReply, HelloRequest

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(rpc_server):
    async with grapec.AsyncClient(f"grpc://127.0.0.1:{rpc_server}") as c:
        yield c


async def test_unary_call(client):
    reply = await client.call(Greeter.say_hello, HelloRequest(name="grapec"))
    assert isinstance(reply, HelloReply)
    assert reply.message == "hello grapec 0"


async def test_large_payload(client):
    blob = bytes(range(256)) * 4096
    reply = await client.call(Greeter.say_hello, HelloRequest(name="big", blob=blob))
    assert reply.message == f"hello big {len(blob)}"


async def test_metadata(client):
    reply = await client.call(Greeter.say_hello, HelloRequest(name="m"), metadata={"x-trace": "abc"})
    assert reply.metadata["x-trace"] == "abc"


async def test_rpc_error_keeps_connection(client):
    with pytest.raises(grapec.RpcError) as info:
        await client.call(Greeter.fail, HelloRequest(name="zed"))
    assert info.value.code is grapec.Status.INVALID_ARGUMENT
    assert info.value.message == "bad name: zed"
    assert (await client.call(Greeter.say_hello, HelloRequest(name="ok"))).message == "hello ok 0"
    assert len(client._pool) == 1


async def test_timeout(client):
    with pytest.raises(grapec.RpcError) as info:
        await client.call(Greeter.slow, HelloRequest(name="x"), timeout=0.2)
    assert info.value.code is grapec.Status.DEADLINE_EXCEEDED
    assert (await client.call(Greeter.say_hello, HelloRequest(name="after"))).message == "hello after 0"


async def test_compression(client):
    reply = await client.call(Greeter.compressed, HelloRequest(name="z"))
    assert reply.message == "x" * 10000 + "z"
    reply = await client.call(Greeter.say_hello, HelloRequest(name="c", blob=b"\x00" * 50000), compression="gzip")
    assert reply.message == "hello c 50000"


async def test_concurrent_calls_share_pool(rpc_server):
    async with grapec.AsyncClient(f"grpc://127.0.0.1:{rpc_server}", max_idle=2) as c:
        results = await asyncio.gather(*(c.call(Greeter.say_hello, HelloRequest(name=str(i))) for i in range(8)))
        assert sorted(r.message for r in results) == sorted(f"hello {i} 0" for i in range(8))
        assert len(c._pool) <= 2


async def test_closed_client(rpc_server):
    c = grapec.AsyncClient(f"grpc://127.0.0.1:{rpc_server}")
    await c.call(Greeter.say_hello, HelloRequest(name="x"))
    await c.aclose()
    with pytest.raises(grapec.TransportError):
        await c.call(Greeter.say_hello, HelloRequest(name="x"))


async def test_connection_refused():
    async with grapec.AsyncClient("grpc://127.0.0.1:1", connect_timeout=1) as c:
        with pytest.raises(grapec.TransportError):
            await c.call(Greeter.say_hello, HelloRequest(name="x"))


async def test_wrong_request_type(client):
    with pytest.raises(TypeError):
        await client.call(Greeter.say_hello, HelloReply(message="x"))  # type: ignore[arg-type]
