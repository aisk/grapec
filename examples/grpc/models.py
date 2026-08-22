import enum
from datetime import datetime, timedelta
from typing import Annotated

import grapec
from grapec import Id


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
    tags: list[Tag]                     # repeated Tag
    sent_at: datetime | None            # optional google.protobuf.Timestamp
    # keep field 5 aligned with the server side proto, field 4 was removed
    trace_id: Annotated[str, Id(5)] = ""
    payload: Tag | str | None           # oneof payload { Tag payload_tag; string payload_str; }
    ttl: timedelta = timedelta(0)       # google.protobuf.Duration
    nonce: bytes = b""


@grapec.struct(package="example.hello.v1")
class HelloReply:
    message: str
    extra: dict[str, str]               # map<string, string>


@grapec.struct(package="example.hello.v1")
class CountRequest:
    pass


@grapec.struct(package="example.hello.v1")
class CountReply:
    greetings: int                      # int64


class Greeter(grapec.Client, package="example.hello.v1"):
    @grapec.name("SayHello")
    def say_hello(self, request: HelloRequest) -> HelloReply: ...


class Stats(grapec.Client, package="example.hello.v1"):
    @grapec.name("Count")
    def count(self, request: CountRequest) -> CountReply: ...


class AsyncGreeter(grapec.AsyncClient, package="example.hello.v1", name="Greeter"):
    @grapec.name("SayHello")
    async def say_hello(self, request: HelloRequest) -> HelloReply: ...
