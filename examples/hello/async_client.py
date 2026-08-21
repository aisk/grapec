import asyncio
from datetime import datetime, timezone

import grapec
from models import AsyncGreeter, HelloRequest, Priority, Tag


async def main() -> None:
    # five concurrent calls over at most two connections, the rest wait in the pool
    greeter = AsyncGreeter("grpc://localhost:50051", timeout=5, max_conns=2, pool_timeout=5)
    requests = [
        HelloRequest(
            name=f"user{i}",
            priority=Priority.HIGH,
            tags=[Tag(key="lang", value="python")],
            sent_at=datetime.now(timezone.utc),
            trace_id=f"trace{i}",
        )
        for i in range(5)
    ]
    replies = await asyncio.gather(*(greeter.say_hello(r) for r in requests))
    for reply in replies:
        print(reply.message)
    await grapec.aclose(greeter)


asyncio.run(main())
