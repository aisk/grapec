from datetime import datetime, timezone

import grapec
from models import Greeter, HelloRequest, Priority, Tag

req = HelloRequest(
    name="grapec",
    priority=Priority.HIGH,
    tags=[Tag(key="lang", value="python")],
    sent_at=datetime.now(timezone.utc),
    trace_id="abc123",
)

print(req.to_json())

with grapec.Client("grpc://localhost:50051") as client:
    reply = client.call(Greeter.say_hello, req, timeout=5)
    print(reply)

    try:
        client.call(Greeter.say_hello, HelloRequest(name="", trace_id="x"))
    except grapec.RpcError as exc:
        print("server said:", exc.code.name, exc.message)
