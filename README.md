# GRaPeC

Pronunciation (IPA): `/ɡreɪ.peɪk/` (similar to "gray-pay-k").

Declare plain Python classes, call remote services with them.

You write classes that look like dataclasses and a service class with typed
method signatures. grapec turns the classes into dataclasses that speak the
protobuf wire format and lets you call the methods on any standard gRPC
server. No `.proto` files, no code generation, no C extensions. The only
dependency is the pure Python `h2` library.

Current scope: struct serialization and unary calls from sync and asyncio
clients. Streaming and other protocols (thrift) come later, the public API is
kept protocol neutral for that.

## Install

Requires Python 3.12+.

```
pip install grapec
```

## Usage

```python
import enum
from datetime import datetime

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
    name: str
    priority: Priority = Priority.LOW
    tags: list[Tag]
    sent_at: datetime | None


@grapec.struct(package="example.hello.v1")
class HelloReply:
    message: str


class Greeter(grapec.Client, package="example.hello.v1"):
    @grapec.name("SayHello")
    def say_hello(self, request: HelloRequest) -> HelloReply: ...


greeter = Greeter("grpc://localhost:50051", timeout=5)
reply = greeter.say_hello(HelloRequest(name="grapec", tags=[Tag(key="lang", value="python")]))
print(reply.message)
```

Serialization and other views are available on their own as well:

```python
data = bytes(req)                       # or req.to_bytes()
same = HelloRequest.from_bytes(data)
assert same == req

req.to_bytes(codec="thrift")            # thrift binary protocol instead of protobuf
HelloRequest.from_bytes(data, codec="thrift")

req.to_dict()                           # plain Python values, for logs and tests
req.to_json()                           # proto3 JSON mapping, for gateways
HelloRequest.from_dict({...})           # accepts both shapes
HelloRequest.from_json('{"name": "x"}')

print(grapec.export_proto(Greeter))     # the .proto file for the other side
```

The equivalent proto definition, which is what the other side would use:

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
}

message HelloReply {
  string message = 1;
}

service Greeter {
  rpc SayHello (HelloRequest) returns (HelloReply) {}
}
```

## Services and clients

- Declare a service by subclassing `grapec.Client` with a `package=` class argument. Each public method takes one struct and returns a struct. Bodies are never executed, `...` is enough. The base class owns `__init__` and has no public attributes of its own, so method names cannot clash with it.
- Names are used as is on the wire. `@grapec.name("SayHello")` on a method or `name="Greeter"` next to `package=` pick a different wire name.
- `Greeter(url, max_idle=4, max_idle_time=60, timeout=None, connect_timeout=10, compression=None, ssl=None)` connects on first call. The URL scheme selects the protocol: `grpc://host:port` for plaintext, `grpcs://host:port` for TLS. `ssl` takes an `ssl.SSLContext` for private CAs or client certificates, the default context verifies against the system trust store. grapec sets ALPN to `h2` on the context it is given.
- Every method accepts `timeout=`, `metadata=`, `compression=` and `details=` keyword arguments at runtime. Declare `**options: Unpack[grapec.CallOptions]` on a method if you want type checkers to see them.
- `metadata` is a dict of header values. Binary values must be `bytes` under a key ending in `-bin`.
- Connections are pooled per client. After a call the connection goes back to the pool if it is still healthy, up to `max_idle`. Before an idle connection is reused, anything the server sent in the meantime (GOAWAY, a closed socket) is processed without blocking and a connection that turned out dead is replaced silently. Idle connections older than `max_idle_time` seconds are closed. A connection that fails during a call is dropped, the error is raised and never retried.
- A deadline that expires cancels the stream and keeps the connection.
- Pass a `grapec.CallDetails()` as `details=` to receive the response headers and trailers: `d = grapec.CallDetails(); greeter.say_hello(req, details=d); d.trailers["x-request-id"]`. Binary values (`-bin` keys) come back as `bytes`. For a trailers-only response everything is reported as trailers, like other gRPC clients do.
- Subclassing a client class inherits its methods. Pass `name=` on the subclass to call the inherited methods under the new service name, without it they keep the parent's paths.
- To share one pool between several clients, create a `grapec.Session(url, ...)` and pass it instead of a URL: `Greeter(session)`, `Orders(session)`.
- `grapec.close(greeter)` closes the session a client created from a URL. Shared sessions are closed with `session.close()`. Garbage collection closes owned sessions as a fallback.
- Compressed responses (`gzip`, `deflate`) are always accepted. Pass `compression="gzip"` to the client or to a single call to compress requests.
- `session.call(Greeter.say_hello, request, ...)` is the low level entry point and works with methods of both sync and async declared clients.

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

Same options and pooling rules. Concurrent calls each get their own connection from the pool. `grapec.AsyncSession` is the shareable counterpart of `Session`.

Errors:

- `grapec.RpcError` when the server answers with a non OK status. It carries `code` (`grapec.Status`, an `IntEnum` aligned with gRPC status codes), `message`, `details`, and the response metadata as `headers` and `trailers`. Deadline expiry raises `RpcError` with `Status.DEADLINE_EXCEEDED`.
- `grapec.TransportError` when the connection itself fails (refused, reset, protocol error).

## Struct rules

- `@grapec.struct(package=...)` makes the class a keyword only dataclass. `package` is required and becomes the namespace on the wire side.
- Fields are numbered 1, 2, 3, ... in declaration order. Use `Annotated[T, grapec.Id(n)]` to pin a number. Fields after a pinned one continue counting from it.
- Fields without a default are required when constructing, like a dataclass. `list` and `dict` fields default to empty, `T | None` fields default to `None`.
- When decoding, a missing field becomes the zero value (`0`, `""`, `False`, empty list, default instance). A missing `T | None` field becomes `None`.
- Unknown fields in the input are skipped, unknown enum values are kept as plain `int`.
- A union of several types is a `oneof`. Each member gets its own field number (consecutive by default, or `Annotated[A, Id(7)] | Annotated[B, Id(9)]`). Members must be distinguishable by type, so structs and distinct scalars are fine, `int | bool` is not. On the proto side the members are named `<field>_<type>`, for example `choice_inner` and `choice_str`. A oneof always has presence, so the field defaults to `None`, `None` encodes as "not set" and a missing oneof decodes as `None`, whether or not `None` is written in the union.

## Dict and JSON

- `to_dict()` / `from_dict()` use Python field names and Python values (`IntEnum`, `datetime`, `bytes`).
- `to_json()` / `from_json()` follow the proto3 JSON mapping: lowerCamelCase keys, `int` as strings, `bytes` as base64, RFC 3339 timestamps, `"1.5s"` durations, enum names, default values omitted, oneof members under their proto side names.
- `from_dict()` accepts both shapes, so `json.loads` output from any protobuf implementation works too. Nested values may be dicts or struct instances. `null` means "not set": the zero value for plain fields, `None` for `T | None` fields and oneofs.

## Exporting a .proto

`grapec.export_proto(Greeter, OtherStruct, ...)` renders proto3 source for the given structs and client classes and everything they reference. All roots must share one package, structs from other packages are referenced by full name with an `import "<package/path>.proto"`. Python enums carry no package, an enum belongs to the package of the first struct that references it. proto enum values share one scope per package, so two enums of one package must not reuse a value name (`Priority.UNSPECIFIED` and `Color.UNSPECIFIED` together are rejected with a hint to rename, the usual convention is `PRIORITY_UNSPECIFIED`). Enums with aliases get `option allow_alias = true`, enums without a zero value get a generated `<NAME>_UNSPECIFIED = 0`. The output is compiled with `protoc` in the test suite and checked to parse grapec's bytes identically.

## Type mapping

| Python | proto |
|---|---|
| `int` | `int64` |
| `float` | `double` |
| `str` | `string` |
| `bytes` | `bytes` |
| `bool` | `bool` |
| `enum.IntEnum` subclass | `enum` |
| another `@grapec.struct` class | `message` |
| `list[T]` | `repeated T` |
| `dict[K, V]` | `map<K, V>` (keys: `int`, `str`, `bool`) |
| `T \| None` | `optional T` |
| `A \| B \| None` | `oneof` |
| `datetime` | `google.protobuf.Timestamp` (naive values are treated as local time) |
| `timedelta` | `google.protobuf.Duration` |

`int32` and `uint32` share the varint encoding with `int64`, so `int` is `int64` by default. `Annotated[int, grapec.I32]` (also `I8`, `I16`) narrows it, protobuf only changes the exported type to `int32`, thrift needs it to pick the wire type. `uint64` values above 2^63 come back negative. `sint*` (zigzag) and `fixed*` use a different encoding and are not supported. `float32`, `Any` and proto2 features are out of scope for now.

## Thrift

The same structs serialize with the thrift binary protocol through `to_bytes(codec="thrift")` / `from_bytes(data, codec="thrift")`. Field ids are the thrift field ids, so `Id(n)` and the declaration order rules apply unchanged. Differences from the protobuf side:

- Integers must match the width in the thrift IDL, `i64` is the default, use `Annotated[int, grapec.I8 | I16 | I32]` for the others. Sending the wrong width makes a standard server skip the field silently, grapec checks the value range on encode.
- Zero values of plain fields are written (thrift has no implicit presence), `T | None` fields and unset unions are omitted. A Python union is a thrift `union` or a struct of optionals, both look the same on the wire.
- `list[T]` accepts a thrift `set<T>` when decoding but always sends a list. `datetime` and `timedelta` are rejected with `SchemaError`. Field ids above 32767 are rejected too.
- Unknown fields are skipped, a field whose wire type does not match the declaration raises `ThriftError`.

thrift RPC (`thrift://`) is not available yet.

## Example

`examples/hello/server.py` is a plain grpcio server compiled from `hello.proto`, standing in for a service written in any language. `client.py` talks to it with grapec only.

```
cd examples/hello
python server.py &
python client.py
python async_client.py
```

## Development

```
uv sync
uv run pytest
```

Tests compile `tests/oracle.proto` and `tests/rpc.proto` with `grpcio-tools`. Serialization is compared byte for byte against the official protobuf implementation and the client is exercised against a real grpcio server, plaintext and TLS (the TLS tests generate a self signed certificate with `openssl` and are skipped without it).
