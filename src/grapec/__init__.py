"""grapec: declare plain Python classes, call remote services with them.

Example::

    import grapec

    @grapec.struct(package="example.hello.v1")
    class HelloRequest:
        name: str

    @grapec.struct(package="example.hello.v1")
    class HelloReply:
        message: str

    @grapec.service(package="example.hello.v1")
    class Greeter:
        @grapec.name("SayHello")
        def say_hello(self, request: HelloRequest) -> HelloReply: ...

    client = grapec.Client("grpc://localhost:50051")
    reply = client.call(Greeter.say_hello, HelloRequest(name="x"))
"""

from ._client import Client
from ._codec import EncodeError
from ._errors import GrapecError, RpcError, Status, TransportError
from ._proto import export_proto
from ._schema import Id, SchemaError, is_struct
from ._service import name, service
from ._struct import struct
from ._wire import WireError

__all__ = [
    "struct",
    "Id",
    "is_struct",
    "service",
    "name",
    "export_proto",
    "Client",
    "Status",
    "GrapecError",
    "RpcError",
    "TransportError",
    "SchemaError",
    "EncodeError",
    "WireError",
]
