"""Call grpcb.in, a public gRPC test server, in plaintext and over TLS.

Its protos live at https://github.com/moul/pb. Nothing to install or start,
the server echoes requests, headers and the error codes you ask for.
"""

import asyncio
import enum

import grapec


# grpcbin.GRPCBin, see grpcbin/grpcbin.proto. Nested messages such as
# DummyMessage.Sub become plain top level structs, only field numbers matter.


@grapec.struct(package="grpcbin")
class EmptyMessage:
    pass


@grapec.struct(package="grpcbin")
class Endpoint:
    path: str
    description: str


@grapec.struct(package="grpcbin")
class IndexReply:
    description: str
    endpoints: list[Endpoint]


@grapec.struct(package="grpcbin")
class Values:
    values: list[str]


@grapec.struct(package="grpcbin")
class HeadersMessage:
    metadata: dict[str, Values]


@grapec.struct(package="grpcbin")
class SpecificErrorRequest:
    code: int
    reason: str


class Enum(enum.IntEnum):
    ENUM_0 = 0
    ENUM_1 = 1
    ENUM_2 = 2


@grapec.struct(package="grpcbin")
class Sub:
    f_string: str


@grapec.struct(package="grpcbin")
class DummyMessage:
    f_string: str = ""
    f_strings: list[str]
    f_int32: int = 0
    f_int32s: list[int]
    f_enum: Enum = Enum.ENUM_0
    f_enums: list[Enum]
    f_sub: Sub | None
    f_subs: list[Sub]
    f_bool: bool = False
    f_bools: list[bool]
    f_int64: int = 0
    f_int64s: list[int]
    f_bytes: bytes = b""
    f_bytess: list[bytes]


class GRPCBin(grapec.Client, package="grpcbin"):
    @grapec.name("Index")
    def index(self, request: EmptyMessage) -> IndexReply: ...

    @grapec.name("DummyUnary")
    def dummy_unary(self, request: DummyMessage) -> DummyMessage: ...

    @grapec.name("HeadersUnary")
    def headers_unary(self, request: EmptyMessage) -> HeadersMessage: ...

    @grapec.name("SpecificError")
    def specific_error(self, request: SpecificErrorRequest) -> EmptyMessage: ...


# addsvc.Add, see addsvc/addsvc.proto


@grapec.struct(package="addsvc")
class SumRequest:
    a: int
    b: int


@grapec.struct(package="addsvc")
class SumReply:
    v: int
    err: str


class Add(grapec.Client, package="addsvc"):
    @grapec.name("Sum")
    def sum(self, request: SumRequest) -> SumReply: ...


class AsyncAdd(grapec.AsyncClient, package="addsvc", name="Add"):
    @grapec.name("Sum")
    async def sum(self, request: SumRequest) -> SumReply: ...


# one session shared by both services, plaintext on port 9000
session = grapec.Session("grpc://grpcb.in:9000", timeout=10)
grpcbin = GRPCBin(session)
add = Add(session)

print(grpcbin.index(EmptyMessage()).description)
print("2 + 3 =", add.sum(SumRequest(a=2, b=3)).v)

# the server echoes the message, including nested and repeated fields
echo = grpcbin.dummy_unary(
    DummyMessage(f_string="hi", f_int32s=[1, 2, 3], f_enum=Enum.ENUM_2, f_sub=Sub(f_string="nested"), f_bytes=b"\x00\xff"),
)
print(echo.f_sub, echo.f_enum.name, echo.f_bytes)

# request metadata comes back in the reply, response headers land in CallDetails
details = grapec.CallDetails()
headers = grpcbin.headers_unary(EmptyMessage(), metadata={"x-demo": "grapec"}, details=details)
print("server saw x-demo =", headers.metadata["x-demo"].values, "and answered with", dict(details.headers))

# ask for a specific status code
try:
    grpcbin.specific_error(SpecificErrorRequest(code=grapec.Status.NOT_FOUND, reason="no such thing"))
except grapec.RpcError as exc:
    print("error:", exc.code.name, exc.message)
session.close()


# the same server over TLS on port 9001, with concurrent async calls
async def main() -> None:
    add = AsyncAdd("grpcs://grpcb.in:9001", timeout=10)
    replies = await asyncio.gather(*(add.sum(SumRequest(a=i, b=i)) for i in range(5)))
    print("doubles:", [r.v for r in replies])
    await grapec.aclose(add)


asyncio.run(main())
