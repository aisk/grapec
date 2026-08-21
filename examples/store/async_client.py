import asyncio

import grapec
from models import AsyncStore, Item


async def main() -> None:
    store = AsyncStore("thrift://127.0.0.1:9090", timeout=5)
    await asyncio.gather(*(store.put(Item(key=f"k{i}", count=i, tags=[])) for i in range(5)))
    items = await asyncio.gather(*(store.get(f"k{i}", limit=1) for i in range(5)))
    print([item.count for item in items], "total:", await store.total())
    await grapec.aclose(store)


asyncio.run(main())
