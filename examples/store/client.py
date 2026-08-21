import grapec
from models import Item, NotFound, Store

store = Store("thrift://127.0.0.1:9090", timeout=5)
store.put(Item(key="apples", count=3, tags=["fruit"]))
store.put(Item(key="pears", count=2, tags=["fruit"], note="ripe"))
print(store.get("apples", limit=10))
print(store.get("pears", limit=10))
print("total:", store.total())

try:
    store.get("plums", 1)
except NotFound as exc:
    print("not found:", exc.key)

try:
    store.put(Item(key="locked", count=1, tags=[]))
except grapec.RpcError as exc:
    # the server raised Locked, which this client never declared
    print("undeclared exception:", exc.code.name, exc.message)

print("removed:", store.remove("pears"), "total:", store.total())
grapec.close(store)
