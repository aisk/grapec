import asyncio
from datetime import datetime, timezone

from models import AsyncGreeter, HelloRequest, Priority, Tag


async def main() -> None:
    greeter = AsyncGreeter("grpc://localhost:50051", timeout=5)
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


asyncio.run(main())
