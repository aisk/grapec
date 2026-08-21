from datetime import datetime, timedelta, timezone

import grapec
from models import CountRequest, Greeter, HelloRequest, Priority, Stats, Tag

req = HelloRequest(
    name="grapec",
    priority=Priority.HIGH,
    tags=[Tag(key="lang", value="python")],
    sent_at=datetime.now(timezone.utc),
    trace_id="abc123",
    payload=Tag(key="source", value="client.py"),
    ttl=timedelta(seconds=90),
    nonce=b"\x00\x01",
)

# serialization round trips, no server needed for these
print(req.to_json())
assert HelloRequest.from_json(req.to_json()) == req
assert HelloRequest.from_bytes(bytes(req)) == req
assert HelloRequest.from_dict(req.to_dict()) == req

# one session, one pool, shared by two services on the same server
session = grapec.Session("grpc://localhost:50051", timeout=5)
greeter = Greeter(session)
stats = Stats(session)

reply = greeter.say_hello(req)
print(reply)

# request headers, response trailers, request compression
details = grapec.CallDetails()
reply = greeter.say_hello(
    HelloRequest(name="tenant user", payload="a oneof string member"),
    metadata={"x-tenant": "acme"},
    compression="gzip",
    details=details,
)
print(reply.extra["tenant"], "request id", details.trailers["x-request-id"])

try:
    greeter.say_hello(HelloRequest(name="", trace_id="x"), timeout=1)
except grapec.RpcError as exc:
    print("server said:", exc.code.name, exc.message)

print("greetings so far:", stats.count(CountRequest()).greetings)
session.close()
