# grapec reference

The complete rules behind the [README](../README.md) introduction. Everything here applies to both protocols unless a section says otherwise, the protocol specific parts are collected under [gRPC](#grpc) and [Thrift](#thrift).

- [Structs](#structs)
  - [Fields](#fields)
  - [Type mapping](#type-mapping)
  - [Unions](#unions)
  - [Bytes](#bytes)
  - [Dict and JSON](#dict-and-json)
- [Clients](#clients)
  - [Declaring a client](#declaring-a-client)
  - [Calling](#calling)
  - [Sessions and pooling](#sessions-and-pooling)
  - [asyncio](#asyncio)
  - [Errors](#errors)
- [gRPC](#grpc)
  - [Exporting a .proto](#exporting-a-proto)
- [Thrift](#thrift)
- [Examples and development](#examples-and-development)

## Structs

`@grapec.struct(package=...)` turns a class into a keyword only dataclass that knows how to serialize itself. `package` is required and names the namespace on the wire side (`example.hello.v1` in proto, the `.thrift` file's namespace). The class body stays plain Python, all wire metadata goes into `Annotated[...]`.

```python
import enum
from datetime import datetime
from typing import Annotated

import grapec


class Priority(enum.IntEnum):
    UNSPECIFIED = 0
    LOW = 1
    HIGH = 2


@grapec.struct(package="example.hello.v1")
class Tag:
    key: str
    value: str


@grapec.struct(package="example.hello.v1")
class HelloRequest:
    name: str                                   # 1
    priority: Priority = Priority.LOW           # 2
    tags: list[Tag]                             # 3, defaults to []
    sent_at: datetime | None                    # 4, defaults to None
    trace: Annotated[bytes, grapec.Id(10)]      # 10
    retries: Annotated[int, grapec.I32]         # 11, int32 in proto, i32 in thrift


@grapec.struct(package="example.hello.v1")
class HelloReply:
    message: str
```

A struct may subclass `Exception` to become a thrift `exception`, see [Thrift](#thrift). `grapec.is_struct(obj)` tells whether a class or instance is a struct.

### Fields

- Fields are numbered 1, 2, 3, ... in declaration order. `Annotated[T, grapec.Id(n)]` pins a number and the fields after it continue counting from `n`. Reordering fields changes their numbers, grapec does not protect against that.
- Fields without a default are required when constructing, like a dataclass. `list` and `dict` fields default to empty, `T | None` fields and unions default to `None`.
- When decoding, a missing field becomes the zero value (`0`, `""`, `False`, empty list, a default instance for nested structs). A missing `T | None` field becomes `None`.
- Unknown fields in the input are skipped. Unknown enum values are kept as plain `int`.
- Encoding a value of the wrong type raises `grapec.EncodeError` (a `TypeError`). A class that cannot be turned into a schema raises `grapec.SchemaError` (also a `TypeError`) when it is decorated, or at the first use for errors that need the whole class graph.

### Type mapping

| Python | proto | thrift |
|---|---|---|
| `int` | `int64` | `i64` |
| `Annotated[int, grapec.I32]` (also `I8`, `I16`, `I64`) | `int32` (`int64` for `I64`) | `i32` (`i8`, `i16`, `i64`) |
| `float` | `double` | `double` |
| `str` | `string` | `string` |
| `bytes` | `bytes` | `binary` |
| `bool` | `bool` | `bool` |
| `enum.IntEnum` subclass | `enum` | `enum` (`i32`) |
| another `@grapec.struct` class | `message` | `struct` |
| `list[T]` | `repeated T` | `list<T>` |
| `dict[K, V]` | `map<K, V>` (keys: `int`, `str`, `bool`) | `map<K, V>` |
| `T \| None` | `optional T` | optional field |
| `A \| B \| None` | `oneof` | `union` |
| `datetime` | `google.protobuf.Timestamp` (naive values are treated as local time) | rejected |
| `timedelta` | `google.protobuf.Duration` | rejected |

Notes on integers:

- `int` is `int64` by default. The width markers `grapec.I8`, `I16`, `I32` and `I64` narrow it. For protobuf a narrow width only changes the exported proto type, the varint encoding is the same and only the 64 bit range is checked. For thrift the width selects the wire type, must match the IDL and the value range is checked on encode, see [Thrift](#thrift).
- `uint64` values above 2^63 come back negative. `sint*` (zigzag) and `fixed*` use a different encoding and are not supported.
- `float32`, `Any` and proto2 features are out of scope for now.

### Unions

A union of several types is a proto `oneof` or a thrift `union`:

```python
@grapec.struct(package="example.hello.v1")
class Event:
    payload: Tag | str | None
    pinned: Annotated[Tag, grapec.Id(7)] | Annotated[int, grapec.Id(9)]
```

- Each member gets its own field number, consecutive by default or pinned with `Annotated[T, grapec.Id(n)]` per member.
- Members must be distinguishable by type. Structs and distinct scalars are fine, `int | bool` is rejected.
- A union always has presence: the field defaults to `None`, `None` encodes as "not set", and a missing union decodes as `None`, whether or not `None` is written in the annotation.
- On the proto side the members are named `<field>_<type>`, for example `payload_tag` and `payload_str`.

### Bytes

```python
data = bytes(req)                               # protobuf wire format, same as req.to_bytes()
same = HelloRequest.from_bytes(data)

req.to_bytes(codec="thrift")                    # thrift binary protocol
HelloRequest.from_bytes(data, codec="thrift")
```

`codec` is `"protobuf"` (default) or `"thrift"`. Bytes that cannot be decoded raise `grapec.WireError` for protobuf and `grapec.ThriftError` for thrift, both are `ValueError` subclasses. A field whose wire type does not match the declaration is an error too, not skipped. Repeated occurrences of a nested message overwrite instead of merging.

### Dict and JSON

```python
req.to_dict()                                   # {"name": "x", "priority": <Priority.LOW: 1>, "tags": [], "sent_at": None, ...}
req.to_json()                                   # '{"name": "x", "priority": "LOW", "retries": "1"}'
HelloRequest.from_dict({...})                   # accepts both shapes
HelloRequest.from_json('{"name": "x"}')
```

- `to_dict()` / `from_dict()` use Python field names and Python values (`IntEnum`, `datetime`, `bytes`), meant for logs and tests.
- `to_json()` / `from_json()` follow the proto3 JSON mapping, meant for gateways: lowerCamelCase keys, 64 bit `int` as strings (`I8`, `I16` and `I32` as numbers), `bytes` as base64, RFC 3339 timestamps, `"1.5s"` durations, enum names, default values omitted, union members under their proto side names.
- `from_dict()` accepts both shapes, so `json.loads` output from any protobuf implementation works too. Nested values may be dicts or struct instances. `null` means "not set": the zero value for plain fields, `None` for `T | None` fields and unions.

## Clients

### Declaring a client

```python
class Greeter(grapec.Client, package="example.hello.v1"):
    @grapec.name("SayHello")
    def say_hello(self, request: HelloRequest) -> HelloReply: ...


greeter = Greeter("grpc://localhost:50051", timeout=5)
```

- Subclass `grapec.Client` with a `package=` class argument and declare the methods with `def`. Bodies are never executed, `...` is enough.
- Names are used as is on the wire, nothing is converted between snake_case and PascalCase. `@grapec.name("SayHello")` on a method or `name="Greeter"` next to `package=` pick a different wire name.
- The method shape depends on the protocol. gRPC methods take one struct and return a struct. Thrift methods may take several parameters of any supported type and return a scalar, container, struct or `None`. A shape the protocol cannot carry is rejected with `SchemaError` when the client is constructed, so one class can serve both protocols as long as its methods fit.
- Parameter names never reach the wire, but they must not be named like a call option (`timeout`, `metadata`, `compression`, `details`), that is rejected at class definition.
- `Client` has no public attributes of its own, so method names cannot clash with it. Helpers live at module level: `grapec.close(client)`, `grapec.aclose(client)`, `grapec.session_of(client)`.
- Subclassing a client class inherits its methods. Pass `name=` on the subclass to call the inherited methods under the new service name, without it they keep the parent's paths.

### Calling

```python
reply = greeter.say_hello(req, timeout=2, metadata={"x-tenant": "acme"})

d = grapec.CallDetails()
greeter.say_hello(req, details=d)
d.trailers["x-request-id"]
```

- Calls accept positional and keyword arguments like a normal Python call.
- Every method also accepts `timeout=`, `metadata=`, `compression=` and `details=` keyword arguments. Declare `**options: Unpack[grapec.CallOptions]` on a method if you want type checkers to see them.
- `timeout` is in seconds and overrides the client's default, an explicit `timeout=None` disables it for that call. Expiry raises `RpcError` with `Status.DEADLINE_EXCEEDED`. The deadline covers sending the request and reading the reply on a connection, it does not include waiting for a pooled connection (`pool_timeout`) or establishing a new one (`connect_timeout`), so a call can take up to the sum of the three. Likewise `compression=None` sends that call uncompressed even when the client sets a default.
- `metadata` is a dict of request headers. Binary values must be `bytes` under a key ending in `-bin`.
- `compression` is `"gzip"` or `"deflate"` and compresses the request. Compressed responses are always accepted.
- `details` takes a `grapec.CallDetails()` and fills its `headers` and `trailers` dicts after the call returned or raised `RpcError`. Binary values (`-bin` keys) come back as `bytes`.
- `metadata`, `compression` and `details` are gRPC concepts, see [Thrift](#thrift) for what happens there.

### Sessions and pooling

A client constructed from a URL owns a `grapec.Session` with these options:

```python
Greeter(url, max_idle=4, max_idle_time=60, max_conns=None, pool_timeout=None, timeout=None, connect_timeout=10, compression=None, ssl=None)
```

- The URL scheme selects the protocol: `grpc://host:port` and `grpcs://host:port` for gRPC over plaintext and TLS, `thrift://` and `thrifts://` for thrift. Nothing connects until the first call.
- `ssl` takes an `ssl.SSLContext` for private CAs or client certificates, the default context verifies against the system trust store. For `grpcs://` grapec sets ALPN to `h2` on the context it is given, for `thrifts://` the context is used as is.
- `timeout` is the default per call deadline, `connect_timeout` bounds establishing a connection, `compression` is the default request compression.
- A connection carries one call at a time, so eight concurrent calls need eight connections. After a call the connection goes back to the pool if it is still healthy, up to `max_idle`, and idle connections older than `max_idle_time` seconds are closed. Before an idle connection is reused, anything the server sent in the meantime (GOAWAY, a closed socket) is processed without blocking and a connection that turned out dead is replaced silently.
- `max_conns` caps how many connections a session may have open at once, `None` means no cap. When they are all busy the next call waits for one to be returned, for at most `pool_timeout` seconds (`None` waits forever, `0` fails immediately), then raises `RpcError` with `Status.RESOURCE_EXHAUSTED`. `max_idle` only decides how many are kept once they fall idle, so `max_conns=8, max_idle=8` serves a burst of eight without reconnecting next time.
- A connection that fails during a call is dropped, the error is raised and the call is never retried. An error from the server (non OK status) does not count as a failure and keeps the connection.
- To share one pool between several clients, create the session yourself and pass it instead of a URL. Options are only accepted together with a URL, a shared session is configured when it is created.

```python
session = grapec.Session("grpc://localhost:50051", max_conns=16)
greeter = Greeter(session)
orders = Orders(session)
session.close()
```

- `grapec.close(client)` closes the session a client created from a URL and leaves a shared session alone. Garbage collection closes owned sessions as a fallback. `grapec.session_of(client)` returns the session behind a client.
- `session.call(Greeter.say_hello, request, ..., timeout=, metadata=, compression=, details=)` is the low level entry point. It takes the unbound method, then the method's arguments, and works with methods of both sync and async declared clients.

### asyncio

Subclass `grapec.AsyncClient` and declare the methods with `async def`:

```python
class AsyncGreeter(grapec.AsyncClient, package="example.hello.v1", name="Greeter"):
    @grapec.name("SayHello")
    async def say_hello(self, request: HelloRequest) -> HelloReply: ...


greeter = AsyncGreeter("grpc://localhost:50051")
reply = await greeter.say_hello(req, timeout=5)
replies = await asyncio.gather(*(greeter.say_hello(r) for r in requests))
await grapec.aclose(greeter)
```

Same call options and pooling rules. Concurrent calls each get their own connection from the pool and `max_conns` bounds how many. `grapec.AsyncSession` is the shareable counterpart of `Session`, closed with `await session.aclose()`. A session with `max_conns` belongs to the event loop of its first call.

### Errors

grapec separates two kinds of failures. Mistakes in the calling program raise the built in `TypeError` / `ValueError` family, like the standard library does, and are not meant to be caught: a value of the wrong type raises `grapec.EncodeError`, a class that cannot be a schema raises `grapec.SchemaError` (both `TypeError` subclasses), a call option the protocol has no concept of raises a plain `TypeError`. Failures of the call itself subclass `grapec.GrapecError`:

- `grapec.RpcError`: the server answered with a non OK status. It carries `code` (`grapec.Status`, an `IntEnum` aligned with the gRPC status codes), `message`, `details` (raw bytes), and the response metadata as `headers` and `trailers`. grapec itself uses `Status.DEADLINE_EXCEEDED` for an expired timeout, `Status.RESOURCE_EXHAUSTED` for a `pool_timeout` and `Status.INTERNAL` for a reply it cannot decode.
- `grapec.TransportError`: the connection itself failed (refused, reset, protocol error). The connection is discarded, the next call opens a new one.
- `grapec.WireError` and `grapec.ThriftError`: bytes that do not follow the protobuf or thrift encoding. Inside a call they are wrapped in `RpcError` with `Status.INTERNAL`, directly from `from_bytes` they are raised as is.

Declared thrift exceptions are raised as themselves, see [Thrift](#thrift).

## gRPC

`grpc://` and `grpcs://` speak gRPC over HTTP/2 with protobuf payloads. Unary calls only, streaming is not implemented yet.

- Methods take exactly one struct and return one struct. The request class is checked at call time.
- Request headers from `metadata=` and response headers and trailers through `details=` are passed as is. For a trailers-only response everything is reported as trailers, like other gRPC clients do.
- An expired deadline cancels the stream with `RST_STREAM` and keeps the connection.
- `gzip` and `deflate` are available for `compression`.

This is the proto definition equivalent to the structs and client above, which is what the other side would use:

```proto
syntax = "proto3";
package example.hello.v1;

message Tag {
  string key = 1;
  string value = 2;
}

message HelloRequest {
  string name = 1;
  Priority priority = 2;
  repeated Tag tags = 3;
  optional google.protobuf.Timestamp sent_at = 4;
  bytes trace = 10;
  int32 retries = 11;
}

message HelloReply {
  string message = 1;
}

service Greeter {
  rpc SayHello (HelloRequest) returns (HelloReply);
}
```

### Exporting a .proto

`grapec.export_proto(Greeter, OtherStruct, ...)` renders proto3 source for the given structs and client classes and everything they reference.

- All roots must share one package. Structs from other packages are referenced by full name with an `import "<package/path>.proto"`.
- Python enums carry no package, an enum belongs to the package of the first struct that references it. proto enum values share one scope per package, so two enums of one package must not reuse a value name (`Priority.UNSPECIFIED` and `Color.UNSPECIFIED` together are rejected with a hint to rename, the usual convention is `PRIORITY_UNSPECIFIED`). Enums with aliases get `option allow_alias = true`, enums without a zero value get a generated `<NAME>_UNSPECIFIED = 0`.
- Methods that are not one struct in, one struct out cannot be exported and raise `SchemaError`.
- The output is compiled with `protoc` in the test suite and checked to parse grapec's bytes identically.

## Thrift

`thrift://host:9090` and `thrifts://` speak `TBinaryProtocol` over `TFramedTransport`. Add `?multiplexed=<ServiceName>` to the URL for a `TMultiplexedProtocol` server. `TCompactProtocol`, `THeaderTransport`, oneway methods and a `.thrift` exporter are not implemented.

```python
@grapec.struct(package="store")
class NotFound(Exception):                      # a thrift `exception` is a struct that can be raised
    key: str


@grapec.struct(package="store")
class Item:
    key: str
    count: Annotated[int, grapec.I32]
    tags: list[str]


class Store(grapec.Client, package="store"):
    @grapec.raises(NotFound)                    # thrift `throws`, in field id order
    def get(self, key: str, limit: Annotated[int, grapec.I32]) -> Item: ...
    def put(self, item: Item) -> None: ...
    def total(self) -> int: ...


store = Store("thrift://localhost:9090", timeout=5)
try:
    item = store.get("k", limit=10)
except NotFound as exc:
    print("no such key", exc.key)
```

Methods:

- Parameters become the fields of the thrift args struct, numbered 1, 2, ... in order or pinned with `Annotated[T, grapec.Id(n)]`. Any supported type works as a parameter or return type, `-> None` is a `void` method.
- `@grapec.raises(A, B)` lists the exception structs a method may raise, each a `@grapec.struct` that subclasses `Exception`. They take result field ids 1, 2, ... in that order, `Annotated[B, grapec.Id(5)]` pins one. Duplicate classes or ids are rejected. A server side exception the client did not declare surfaces as `RpcError` with `Status.UNKNOWN`. gRPC sessions ignore `@raises`.
- Exception structs compare and hash by value, pickle, and must not use field names that `BaseException` owns (`args`, `add_note`, ...).
- `TApplicationException` replies become `RpcError`: `UNKNOWN_METHOD` maps to `Status.UNIMPLEMENTED`, protocol level failures to `Status.INTERNAL`. A reply that cannot be decoded raises `RpcError` with `Status.INTERNAL` as well and the connection is kept, because the frame boundary was intact.
- `metadata=` and `compression=` raise `TypeError`, thrift has no headers. `details=` stays empty. An expired deadline drops the connection, thrift cannot cancel a call in flight.
- One session carries the multiplexed service name from its URL, so clients of two multiplexed services need two sessions.

Structs:

- Integers must match the width in the IDL, `i64` is the default, use `Annotated[int, grapec.I8 | I16 | I32]` for the others. A standard server silently skips a field sent with the wrong width, grapec checks the value range on encode.
- Zero values of plain fields are written (thrift has no implicit presence), `T | None` fields and unset unions are omitted. A Python union is a thrift `union` or a struct of optionals, both look the same on the wire.
- `list[T]` accepts a thrift `set<T>` when decoding but always sends a list, so a field declared `set<T>` on the server side is skipped by standard servers when you send it (a `Set` marker may come later if needed).
- `datetime` and `timedelta` are rejected with `SchemaError`, so are field ids above 32767.
- Unknown fields are skipped, a field whose wire type does not match the declaration raises `ThriftError`.

## Examples and development

`examples/eliza.py` and `examples/grpcbin.py` call public gRPC services (demo.connectrpc.com over TLS, grpcb.in in plaintext and TLS) and need nothing but a network connection. `examples/grpc/server.py` is a plain grpcio server compiled from `hello.proto`, standing in for a service written in any language, `client.py` and `async_client.py` talk to it with grapec only. `examples/thrift/` is the same for thrift, with a thriftpy2 server. See `examples/README.md`.

```
python examples/eliza.py
python examples/grpcbin.py

cd examples/grpc
python server.py &
python client.py
python async_client.py

cd examples/thrift
python server.py &
python client.py
```

```
uv sync
uv run pytest
```

Serialization is compared byte for byte against the official protobuf implementation (`tests/oracle.proto`, compiled with `grpcio-tools`) and against thriftpy2 (`tests/oracle.thrift`). The clients are exercised against a real grpcio server, plaintext and TLS (the TLS tests generate a self signed certificate with `openssl` and are skipped without it), and a real thriftpy2 server.
