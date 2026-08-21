"""thrift RPC against a real thriftpy2 server, sync and async clients."""

import asyncio
from typing import Annotated

import pytest

import grapec
from grapec import I32


@grapec.struct(package="rpc")
class NotFound(Exception):
    key: str


@grapec.struct(package="rpc")
class Busy(Exception):
    retry_after: Annotated[int, I32]


@grapec.struct(package="rpc")
class Item:
    key: str
    count: Annotated[int, I32]
    tags: list[str]


class Store(grapec.Client, package="rpc"):
    @grapec.raises(NotFound, Busy)
    def get(self, key: str, limit: Annotated[int, I32]) -> Item: ...

    def put(self, item: Item) -> None: ...

    def total(self) -> int: ...

    def counts(self, keys: list[str]) -> dict[str, Annotated[int, I32]]: ...

    def slow(self) -> Item: ...

    def boom(self) -> None: ...

    def undeclared(self, key: str) -> Item: ...

    def undeclared_void(self, key: str) -> None: ...

    def nope(self, key: str) -> Item: ...

    def echo_opt(self, n: Annotated[int, I32] | None) -> Annotated[int, I32]: ...

    @grapec.raises(NotFound, Annotated[Busy, grapec.Id(5)])
    def pinned(self, key: str) -> Item: ...


class AsyncStore(grapec.AsyncClient, package="rpc"):
    @grapec.raises(NotFound, Busy)
    async def get(self, key: str, limit: Annotated[int, I32]) -> Item: ...

    async def total(self) -> int: ...

    async def slow(self) -> Item: ...


@pytest.fixture
def store(thrift_server):
    port, _ = thrift_server
    client = Store(f"thrift://127.0.0.1:{port}")
    yield client
    grapec.close(client)


def test_call_with_several_arguments(store):
    assert store.get("k", 3) == Item(key="k", count=3, tags=["a", "b"])
    assert store.get(key="k", limit=3) == store.get("k", limit=3)


def test_void_scalar_and_container_returns(store):
    assert store.put(Item(key="x", count=1, tags=[])) is None
    assert store.total() == 1 << 40
    assert store.counts(["ab", "c"]) == {"ab": 2, "c": 1}


def test_declared_exceptions_are_raised(store):
    with pytest.raises(NotFound) as info:
        store.get("missing", 1)
    assert info.value.key == "missing"
    assert "missing" in str(info.value)
    with pytest.raises(Busy) as info:
        store.get("busy", 1)
    assert info.value.retry_after == 7
    assert store.get("k", 1).key == "k"  # connection is still good


def test_unknown_method_is_unimplemented(store):
    with pytest.raises(grapec.RpcError) as info:
        store.nope("k")
    assert info.value.code is grapec.Status.UNIMPLEMENTED
    assert store.total() == 1 << 40


def test_handler_failures_do_not_poison_the_client(store):
    # thriftpy2 closes the connection on an unexpected handler exception (other servers
    # send TApplicationException INTERNAL_ERROR), either way the next call must work
    with pytest.raises((grapec.TransportError, grapec.RpcError)):
        store.boom()
    # a declared thrift exception the client did not declare in @raises is unknown to us
    with pytest.raises(grapec.RpcError) as info:
        store.undeclared("k")
    assert info.value.code is grapec.Status.UNKNOWN and "not declared with @raises" in info.value.message
    # a void method cannot fall back on a missing result, the unknown field is the only sign
    with pytest.raises(grapec.RpcError) as info:
        store.undeclared_void("k")
    assert info.value.code is grapec.Status.UNKNOWN and "result field 1" in info.value.message
    assert store.total() == 1 << 40


def test_timeout_drops_the_connection(store):
    session = grapec.session_of(store)
    with pytest.raises(grapec.RpcError) as info:
        store.slow(timeout=0.2)
    assert info.value.code is grapec.Status.DEADLINE_EXCEEDED
    assert len(session._pool) == 0
    assert store.total() == 1 << 40


def test_metadata_and_compression_are_rejected(store, thrift_server):
    port, _ = thrift_server
    with pytest.raises(TypeError, match="not available with thrift"):
        store.total(metadata={"a": "b"})
    with pytest.raises(TypeError, match="not available with thrift"):
        store.total(compression="gzip")
    with pytest.raises(ValueError, match="not available with thrift"):
        Store(f"thrift://127.0.0.1:{port}", compression="gzip")


def test_argument_errors(store):
    with pytest.raises(TypeError):
        store.get("k")
    with pytest.raises(TypeError):
        store.get("k", 1, 2)
    with pytest.raises(TypeError):
        store.get("k", 1, bogus=2)
    with pytest.raises(grapec.EncodeError):
        store.get("k", 1 << 40)
    assert store.total() == 1 << 40


def test_session_call_and_details(store):
    session = grapec.session_of(store)
    details = grapec.CallDetails()
    assert session.call(Store.get, "k", 2, details=details).count == 2
    assert details.headers == {} and details.trailers == {}


def test_grpc_only_shapes_are_rejected_for_thrift(thrift_server):
    port, _ = thrift_server
    from datetime import datetime

    class Bad(grapec.Client, package="rpc"):
        def stamp(self, at: datetime) -> None: ...

    with pytest.raises(grapec.SchemaError, match="no thrift counterpart"):
        Bad(f"thrift://127.0.0.1:{port}")


def test_raises_validation():
    with pytest.raises(grapec.SchemaError, match="subclass Exception"):
        grapec.raises(Item)

    with pytest.raises(grapec.SchemaError, match="subclass Exception"):
        grapec.raises(ValueError)


def test_pooled_connection_is_reused(store):
    session = grapec.session_of(store)
    store.total()
    assert len(session._pool) == 1
    store.total()
    assert len(session._pool) == 1


def test_closed_server_connection_is_detected(thrift_server):
    port, _ = thrift_server
    client = Store(f"thrift://127.0.0.1:{port}", max_idle_time=None)
    session = grapec.session_of(client)
    client.total()
    conn = session._acquire()
    conn._sock.close()
    # a dead socket is noticed by poll before the next call uses it
    session._release(conn)
    assert len(session._pool) <= 1
    assert client.total() == 1 << 40
    grapec.close(client)


def test_connection_refused():
    from conftest import _free_port

    client = Store(f"thrift://127.0.0.1:{_free_port()}", connect_timeout=1)
    with pytest.raises(grapec.TransportError, match="cannot connect"):
        client.total()


def test_optional_parameter_keeps_its_width(store):
    # review H1: the width marker was dropped on `Annotated[int, I32] | None`, the server skipped the field
    assert store.echo_opt(5) == 5
    assert store.echo_opt(None) == -1


def test_pinned_exception_id(store):
    # review L5
    with pytest.raises(Busy) as info:
        store.pinned("k")
    assert info.value.retry_after == 5


def test_parameter_named_method_is_fine_by_keyword(thrift_server):
    # review M1: Session.call(self, method, ...) used to swallow the keyword
    port, _ = thrift_server

    class Odd(grapec.Client, package="rpc"):
        @grapec.name("echo_opt")
        def f(self, method: Annotated[int, I32] | None) -> Annotated[int, I32]: ...

    client = Odd(f"thrift://127.0.0.1:{port}")
    assert client.f(method=3) == 3
    grapec.close(client)


def test_malformed_reply_is_an_rpc_error(monkeypatch, store):
    # review M3: decode failures must surface as RpcError and keep the connection
    import grapec.thrift as t

    real = t.decode_result

    def broken(spec, body):
        return real(spec, body + b"\x00")

    monkeypatch.setattr(t, "decode_result", broken)
    with pytest.raises(grapec.RpcError) as info:
        store.total()
    assert info.value.code is grapec.Status.INTERNAL and "trailing" in info.value.message
    monkeypatch.undo()
    assert store.total() == 1 << 40


def test_raises_rejects_duplicates():
    with pytest.raises(grapec.SchemaError, match="twice"):
        grapec.raises(NotFound, NotFound)
    with pytest.raises(grapec.SchemaError, match="duplicate field id"):
        grapec.raises(NotFound, Annotated[Busy, grapec.Id(1)])


def test_exception_structs_pickle_copy_and_hash():
    # review M2, L4, L6
    import copy
    import pickle

    exc = NotFound(key="k")
    assert pickle.loads(pickle.dumps(exc)) == exc
    assert copy.copy(exc) == exc and copy.deepcopy(exc) == exc
    assert hash(NotFound(key="k")) == hash(exc) and {exc, NotFound(key="k")} == {exc}
    with pytest.raises(grapec.SchemaError, match="BaseException"):

        @grapec.struct(package="rpc")
        class Bad(Exception):
            args: list[str]


# -- async ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_calls(thrift_server):
    port, _ = thrift_server
    store = AsyncStore(f"thrift://127.0.0.1:{port}")
    try:
        assert await store.get("k", 5) == Item(key="k", count=5, tags=["a", "b"])
        assert await store.total() == 1 << 40
        with pytest.raises(NotFound):
            await store.get("missing", 1)
        with pytest.raises(grapec.RpcError) as info:
            await store.slow(timeout=0.2)
        assert info.value.code is grapec.Status.DEADLINE_EXCEEDED
        assert await store.total() == 1 << 40
        results = await asyncio.gather(*(store.get(str(i), i) for i in range(5)))
        assert [r.count for r in results] == list(range(5))
    finally:
        await grapec.aclose(store)


@pytest.mark.asyncio
async def test_async_cancel_does_not_poison_pool(thrift_server):
    port, _ = thrift_server
    store = AsyncStore(f"thrift://127.0.0.1:{port}")
    try:
        task = asyncio.create_task(store.slow())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(grapec.session_of(store)._pool) == 0
        assert await store.total() == 1 << 40
    finally:
        await grapec.aclose(store)


@pytest.mark.asyncio
async def test_async_session_call_with_sync_declaration(thrift_server):
    port, _ = thrift_server
    session = grapec.AsyncSession(f"thrift://127.0.0.1:{port}")
    try:
        assert (await session.call(Store.get, "k", 1)).key == "k"
    finally:
        await session.aclose()
