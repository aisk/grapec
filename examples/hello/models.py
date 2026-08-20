import enum
from datetime import datetime
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
    tags: list[Tag]
    sent_at: datetime | None
    # keep field 5 aligned with the server side proto, field 4 was removed
    trace_id: Annotated[str, Id(5)]


@grapec.struct(package="example.hello.v1")
class HelloReply:
    message: str
    extra: dict[str, str]


@grapec.service(package="example.hello.v1")
class Greeter:
    @grapec.name("SayHello")
    def say_hello(self, request: HelloRequest) -> HelloReply: ...
