# GRaPeC

Pronunciation (IPA): `/ɡreɪ.peɪk/` (similar to "gray-pay-k").

Declare plain Python classes, call remote services with them.

You write classes that look like dataclasses and a service class with typed
method signatures. grapec turns the classes into dataclasses that speak the
protobuf wire format and lets you call the methods on any standard gRPC
server. No `.proto` files, no code generation, no C extensions. The only
dependency is the pure Python `h2` library.

Current scope: struct serialization and unary calls from a sync client.
Streaming, async and other protocols (thrift) come later, the public API is
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


@grapec.service(package="example.hello.v1")
class Greeter:
    @grapec.name("SayHello")
    def say_hello(self, request: HelloRequest) -> HelloReply: ...


req = HelloRequest(name="grapec", tags=[Tag(key="lang", value="python")])

with grapec.Client("grpc://localhost:50051") as client:
    reply = client.call(Greeter.say_hello, req, timeout=5)
    print(reply.message)
```

Serialization is available on its own as well:

```python
data = bytes(req)                       # or req.to_bytes()
same = HelloRequest.from_bytes(data)
assert same == req
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

## Services and the client

- `@grapec.service(package=...)` marks a class as a service description. Each public method takes exactly one struct argument and returns a struct. Bodies are never executed, `...` is enough.
- Names are used as is on the wire. Use `@grapec.name("SayHello")` on a method or `@grapec.service(package=..., name="Greeter")` to pick a different wire name.
- `grapec.Client(url, max_idle=4, timeout=None, connect_timeout=10)` picks the protocol from the URL scheme: `grpc://host:port` for plaintext, `grpcs://host:port` for TLS.
- `client.call(Service.method, request, timeout=..., metadata=...)` returns a response struct. The return type is inferred from the method signature, so type checkers and IDEs see the right type.
- `metadata` is a dict of header values. Binary values must be `bytes` under a key ending in `-bin`.
- Connections are pooled. After a call the connection goes back to the pool if it is still healthy, up to `max_idle`. A connection that fails at the transport level is dropped, the error is raised and never retried.
- `client.close()` or `with grapec.Client(...) as client:` closes idle connections. `__del__` does the same as a fallback.

Errors:

- `grapec.RpcError` when the server answers with a non OK status. It carries `code` (`grapec.Status`, an `IntEnum` aligned with gRPC status codes), `message` and `details`. Deadline expiry raises `RpcError` with `Status.DEADLINE_EXCEEDED`.
- `grapec.TransportError` when the connection itself fails (refused, reset, protocol error).

## Struct rules

- `@grapec.struct(package=...)` makes the class a keyword only dataclass. `package` is required and becomes the namespace on the wire side.
- Fields are numbered 1, 2, 3, ... in declaration order. Use `Annotated[T, grapec.Id(n)]` to pin a number. Fields after a pinned one continue counting from it.
- Fields without a default are required when constructing, like a dataclass. `list` and `dict` fields default to empty, `T | None` fields default to `None`.
- When decoding, a missing field becomes the zero value (`0`, `""`, `False`, empty list, default instance). A missing `T | None` field becomes `None`.
- Unknown fields in the input are skipped, unknown enum values are kept as plain `int`.

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
| `datetime` | `google.protobuf.Timestamp` (naive values are treated as local time) |
| `timedelta` | `google.protobuf.Duration` |

`int32`, `uint64`, `sint64`, `fixed64` and friends share the varint encoding with `int64` for the common cases, so only `int` is exposed. `float32`, `oneof`, `Any` and proto2 features are out of scope for now.

## Example

`examples/hello/server.py` is a plain grpcio server compiled from `hello.proto`, standing in for a service written in any language. `client.py` talks to it with grapec only.

```
cd examples/hello
python server.py &
python client.py
```

## Development

```
uv sync
uv run pytest
```

Tests compile `tests/oracle.proto` and `tests/rpc.proto` with `grpcio-tools`. Serialization is compared byte for byte against the official protobuf implementation and the client is exercised against a real grpcio server.
