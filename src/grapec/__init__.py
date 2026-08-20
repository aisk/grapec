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

from ._codec import EncodeError
from ._errors import GrapecError, RpcError, Status, TransportError
from ._proto import export_proto
from ._schema import Id, SchemaError, is_struct
from ._service import AsyncClient, CallDetails, CallOptions, Client, aclose, close, name, session_of
from ._session import AsyncSession, Session
from ._struct import struct
from ._wire import WireError

__all__ = [
    "struct",
    "Id",
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
]
