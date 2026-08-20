"""grapec: declare plain Python classes, serialize them for cross language RPC.

Example::

    import grapec

    @grapec.struct(package="example.hello.v1")
    class HelloRequest:
        name: str
        tags: list[str]

    data = bytes(HelloRequest(name="x"))
    req = HelloRequest.from_bytes(data)
"""

from ._codec import EncodeError
from ._schema import Id, SchemaError, is_struct
from ._struct import struct
from ._wire import WireError

__all__ = ["struct", "Id", "is_struct", "SchemaError", "EncodeError", "WireError"]
