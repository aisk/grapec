# grapec

Declare plain Python classes, call gRPC and thrift services with them.

```python
import grapec


@grapec.struct(package="connectrpc.eliza.v1")
class SayRequest:
    sentence: str


@grapec.struct(package="connectrpc.eliza.v1")
class SayResponse:
    sentence: str


class ElizaService(grapec.Client, package="connectrpc.eliza.v1"):
    @grapec.name("Say")
    def say(self, request: SayRequest) -> SayResponse: ...


eliza = ElizaService("grpcs://demo.connectrpc.com")
reply = eliza.say(SayRequest(sentence="I feel tired today"))
print(reply.sentence)
```

That talks to the public ELIZA demo at demo.connectrpc.com, paste it into a file and run it.

The type hints are the schema. `@grapec.struct` turns a class into a keyword only dataclass that encodes and decodes itself with the protobuf wire format or the thrift binary protocol, and `grapec.Client` subclasses call methods on a standard gRPC or thrift server. There are no `.proto` or `.thrift` files to write and no generated code to check in. The only dependency is `h2`.

Pronounced `/ɡreɪ.peɪk/`, like "gray-pay-k".

## Install

Requires Python 3.12+.

```
pip install grapec
# or
uv add grapec
```

## Example

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
    tags: list[Tag]                  # repeated Tag
    sent_at: datetime | None         # optional google.protobuf.Timestamp


@grapec.struct(package="example.hello.v1")
class HelloReply:
    message: str


class Greeter(grapec.Client, package="example.hello.v1"):
    @grapec.name("SayHello")
    def say_hello(self, request: HelloRequest) -> HelloReply: ...


greeter = Greeter("grpc://localhost:50051", timeout=5)
reply = greeter.say_hello(HelloRequest(name="grapec", tags=[Tag(key="lang", value="python")]))

data = bytes(reply)                          # protobuf wire format
HelloReply.from_bytes(data)
reply.to_json()                              # proto3 JSON mapping
print(grapec.export_proto(Greeter))          # the .proto for the other side
```

Field numbers follow declaration order, `Annotated[int, grapec.Id(7)]` pins one. The URL scheme picks the transport, `grpc://`, `grpcs://`, `thrift://` or `thrifts://`. Connections are pooled per client, calls accept `timeout=`, `metadata=` and `compression=`. For asyncio, subclass `grapec.AsyncClient` and declare the methods with `async def`.

The same structs work with thrift. Methods may take several parameters, return scalars and declare the exceptions they throw:

```python
from typing import Annotated

import grapec


@grapec.struct(package="store")
class NotFound(Exception):
    key: str


@grapec.struct(package="store")
class Item:
    key: str
    count: Annotated[int, grapec.I32]


class Store(grapec.Client, package="store"):
    @grapec.raises(NotFound)
    def get(self, key: str) -> Item: ...

    def put(self, item: Item) -> None: ...


store = Store("thrift://localhost:9090")
try:
    item = store.get("answer")
except NotFound as exc:
    print("no such key", exc.key)
```

## More

- [docs/reference.md](docs/reference.md) covers struct rules, type mapping, sessions and pooling, dict and JSON views, `.proto` export and the thrift details.
- [examples/eliza.py](examples/eliza.py) and [examples/grpcbin.py](examples/grpcbin.py) call public gRPC services, no setup needed. [examples/grpc](examples/grpc) and [examples/thrift](examples/thrift) run a local grpcio or thriftpy2 server and walk through every feature.

Current scope is struct serialization and unary calls. Streaming is not implemented yet.

## Development

```
uv sync
uv run pytest
```

Serialization is tested against the protobuf and thriftpy2 implementations, the clients against real grpcio and thrift servers.
