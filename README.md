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
- `Greeter(url, max_idle=4, timeout=None, connect_timeout=10, compression=None)` connects on first call. The URL scheme selects the protocol: `grpc://host:port` for plaintext, `grpcs://host:port` for TLS.
- Every method accepts `timeout=`, `metadata=` and `compression=` keyword arguments at runtime. Declare `**options: Unpack[grapec.CallOptions]` on a method if you want type checkers to see them.
- `metadata` is a dict of header values. Binary values must be `bytes` under a key ending in `-bin`.
- Connections are pooled per client. After a call the connection goes back to the pool if it is still healthy, up to `max_idle`. A connection that fails at the transport level is dropped, the error is raised and never retried.
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

- `grapec.RpcError` when the server answers with a non OK status. It carries `code` (`grapec.Status`, an `IntEnum` aligned with gRPC status codes), `message` and `details`. Deadline expiry raises `RpcError` with `Status.DEADLINE_EXCEEDED`.
- `grapec.TransportError` when the connection itself fails (refused, reset, protocol error).

## Struct rules

- `@grapec.struct(package=...)` makes the class a keyword only dataclass. `package` is required and becomes the namespace on the wire side.
- Fields are numbered 1, 2, 3, ... in declaration order. Use `Annotated[T, grapec.Id(n)]` to pin a number. Fields after a pinned one continue counting from it.
- Fields without a default are required when constructing, like a dataclass. `list` and `dict` fields default to empty, `T | None` fields default to `None`.
- When decoding, a missing field becomes the zero value (`0`, `""`, `False`, empty list, default instance). A missing `T | None` field becomes `None`.
- Unknown fields in the input are skipped, unknown enum values are kept as plain `int`.
- A union of several types is a `oneof`. Each member gets its own field number (consecutive by default, or `Annotated[A, Id(7)] | Annotated[B, Id(9)]`). Members must be distinguishable by type, so structs and distinct scalars are fine, `int | bool` is not. On the proto side the members are named `<field>_<type>`, for example `choice_inner` and `choice_str`. A missing oneof decodes as `None` even when `None` is not in the union.

## Dict and JSON

- `to_dict()` / `from_dict()` use Python field names and Python values (`IntEnum`, `datetime`, `bytes`).
- `to_json()` / `from_json()` follow the proto3 JSON mapping: lowerCamelCase keys, `int` as strings, `bytes` as base64, RFC 3339 timestamps, `"1.5s"` durations, enum names, default values omitted, oneof members under their proto side names.
- `from_dict()` accepts both shapes, so `json.loads` output from any protobuf implementation works too.

## Exporting a .proto

`grapec.export_proto(Greeter, OtherStruct, ...)` renders proto3 source for the given structs and client classes and everything they reference. All roots must share one package, structs from other packages are referenced by full name with an `import "<package/path>.proto"`. The output is compiled with `protoc` in the test suite and checked to parse grapec's bytes identically.

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

`int32` and `uint32` share the varint encoding with `int64`, so only `int` is exposed. `uint64` values above 2^63 come back negative. `sint*` (zigzag) and `fixed*` use a different encoding and are not supported. `float32`, `Any` and proto2 features are out of scope for now.

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

Tests compile `tests/oracle.proto` and `tests/rpc.proto` with `grpcio-tools`. Serialization is compared byte for byte against the official protobuf implementation and the client is exercised against a real grpcio server.
