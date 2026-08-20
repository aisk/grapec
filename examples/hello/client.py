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

greeter = Greeter("grpc://localhost:50051", timeout=5)

reply = greeter.say_hello(req)
print(reply)

try:
    greeter.say_hello(HelloRequest(name="", trace_id="x"), timeout=1)
except grapec.RpcError as exc:
    print("server said:", exc.code.name, exc.message)
