"""grapec: declare plain Python classes, call remote services with them.

Example::

    import grapec

    @grapec.struct(package="example.hello.v1")
    class HelloRequest:
        name: str

    @grapec.struct(package="example.hello.v1")
    class HelloReply:
        message: str

    class Greeter(grapec.Client, package="example.hello.v1"):
        @grapec.name("SayHello")
        def say_hello(self, request: HelloRequest) -> HelloReply: ...

    greeter = Greeter("grpc://localhost:50051")
    reply = greeter.say_hello(HelloRequest(name="x"))
"""

from .protobuf import EncodeError
from .errors import GrapecError, RpcError, Status, TransportError
from .proto import export_proto
from .schema import I8, I16, I32, I64, Id, SchemaError, is_struct
from .service import (
    AsyncClient,
    CallDetails,
    CallOptions,
    Client,
    aclose,
    close,
    name,
    session_of,
)
from .session import AsyncSession, Session
from .struct import struct
from .thrift import ThriftError
from .wire import WireError

__all__ = [
    "struct",
    "Id",
    "I8",
    "I16",
    "I32",
    "I64",
    "is_struct",
    "Client",
    "AsyncClient",
    "name",
    "CallOptions",
    "CallDetails",
    "close",
    "aclose",
    "session_of",
    "Session",
    "AsyncSession",
    "export_proto",
    "Status",
    "GrapecError",
    "RpcError",
    "TransportError",
    "SchemaError",
    "EncodeError",
    "WireError",
    "ThriftError",
]
