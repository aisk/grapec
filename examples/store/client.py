from typing import Annotated

import grapec
from grapec import I32


@grapec.struct(package="store")
class NotFound(Exception):
    key: str


@grapec.struct(package="store")
class Item:
    key: str
    count: Annotated[int, I32]
    tags: list[str]


class Store(grapec.Client, package="store"):
    @grapec.raises(NotFound)
    def get(self, key: str, limit: Annotated[int, I32]) -> Item: ...

    def put(self, item: Item) -> None: ...

    def total(self) -> int: ...


store = Store("thrift://127.0.0.1:9090", timeout=5)
store.put(Item(key="apples", count=3, tags=["fruit"]))
print(store.get("apples", limit=10))
print("total:", store.total())
try:
    store.get("pears", 1)
except NotFound as exc:
    print("not found:", exc.key)
