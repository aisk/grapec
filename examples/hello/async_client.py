import asyncio
from datetime import datetime, timezone

import grapec
from models import Greeter, HelloRequest, Priority, Tag


async def main() -> None:
    async with grapec.AsyncClient("grpc://localhost:50051") as client:
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
        replies = await asyncio.gather(*(client.call(Greeter.say_hello, r, timeout=5) for r in requests))
        for reply in replies:
            print(reply.message)


asyncio.run(main())
