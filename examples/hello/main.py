from datetime import datetime, timezone

from models import HelloReply, HelloRequest, Priority, Tag

req = HelloRequest(
    name="grapec",
    priority=Priority.HIGH,
    tags=[Tag(key="lang", value="python")],
    sent_at=datetime.now(timezone.utc),
    trace_id="abc123",
)

data = bytes(req)
print("encoded", len(data), "bytes:", data.hex())

decoded = HelloRequest.from_bytes(data)
print("decoded", decoded)
assert decoded == req

# bytes produced by any protobuf implementation can be read the same way
reply = HelloReply.from_bytes(bytes(HelloReply(message="hi", extra={"k": "v"})))
print("reply  ", reply)
