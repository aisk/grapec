from typing import Annotated

import grapec
from grapec import I32, Id


@grapec.struct(package="store")
class NotFound(Exception):              # a thrift `exception`
    key: str


@grapec.struct(package="store")
class Item:
    key: str
    count: Annotated[int, I32]          # thrift integers must match the IDL width, i64 is the default
    tags: list[str]
    note: Annotated[str, Id(5)] | None  # optional field, omitted on the wire when None


class Store(grapec.Client, package="store"):
    @grapec.raises(NotFound)            # thrift `throws`
    def get(self, key: str, limit: Annotated[int, I32]) -> Item: ...

    # the IDL also throws Locked, undeclared here on purpose: it arrives as RpcError
    def put(self, item: Item) -> None: ...

    @grapec.raises(NotFound)
    def remove(self, key: Annotated[str, Id(2)]) -> Annotated[int, I32]: ...

    def total(self) -> int: ...


class AsyncStore(grapec.AsyncClient, package="store", name="Store"):
    @grapec.raises(NotFound)
    async def get(self, key: str, limit: Annotated[int, I32]) -> Item: ...

    async def put(self, item: Item) -> None: ...

    async def total(self) -> int: ...
