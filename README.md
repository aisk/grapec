# GRaPeC

Pronunciation (IPA): `/ɡreɪ.peɪk/` (similar to "gray-pay-k").

Declare plain Python classes, get cross language serialization for free.

You write a class that looks like a dataclass. grapec turns it into one and
adds `to_bytes` / `from_bytes` that speak the protobuf wire format, so the
bytes can be exchanged with any standard protobuf or gRPC implementation.
No `.proto` files, no code generation, no runtime dependencies.

Current scope is struct serialization. RPC transports are the next step.

## Install

Requires Python 3.12+.

```
pip install grapec
```

## Usage

```python
import enum
from datetime import datetime

import grapec


class Priority(enum.IntEnum):
    UNSPECIFIED = 0
    LOW = 1
    HIGH = 2


@grapec.struct(package="example.hello.v1")
class Tag:
    key: str
    value: str


@grapec.struct(package="example.hello.v1")
class HelloRequest:
    name: str
    priority: Priority = Priority.LOW
    tags: list[Tag]
    sent_at: datetime | None


req = HelloRequest(name="grapec", tags=[Tag(key="lang", value="python")])

data = bytes(req)                       # or req.to_bytes()
same = HelloRequest.from_bytes(data)
assert same == req
```

The equivalent proto definition, which is what the other side would use:

```proto
syntax = "proto3";
package example.hello.v1;

message Tag {
  string key = 1;
  string value = 2;
}

message HelloRequest {
  string name = 1;
  Priority priority = 2;
  repeated Tag tags = 3;
  optional google.protobuf.Timestamp sent_at = 4;
}
```

## Rules

- `@grapec.struct(package=...)` makes the class a keyword only dataclass. `package` is required and becomes the namespace on the wire side.
- Fields are numbered 1, 2, 3, ... in declaration order. Use `Annotated[T, grapec.Id(n)]` to pin a number. Fields after a pinned one continue counting from it.
- Fields without a default are required when constructing, like a dataclass. `list` and `dict` fields default to empty, `T | None` fields default to `None`.
- When decoding, a missing field becomes the zero value (`0`, `""`, `False`, empty list, default instance). A missing `T | None` field becomes `None`.
- Unknown fields in the input are skipped, unknown enum values are kept as plain `int`.

## Type mapping

| Python | proto |
|---|---|
| `int` | `int64` |
| `float` | `double` |
| `str` | `string` |
| `bytes` | `bytes` |
| `bool` | `bool` |
| `enum.IntEnum` subclass | `enum` |
| another `@grapec.struct` class | `message` |
| `list[T]` | `repeated T` |
| `dict[K, V]` | `map<K, V>` (keys: `int`, `str`, `bool`) |
| `T \| None` | `optional T` |
| `datetime` | `google.protobuf.Timestamp` (naive values are treated as local time) |
| `timedelta` | `google.protobuf.Duration` |

`int32`, `uint64`, `sint64`, `fixed64` and friends share the varint encoding with `int64` for the common cases, so only `int` is exposed. `float32`, `oneof`, `Any` and proto2 features are out of scope for now.

## Example

```
cd examples/hello
python main.py
```

## Development

```
uv sync
uv run pytest
```

Tests compile `tests/oracle.proto` with `grpcio-tools` and compare grapec's bytes against the official protobuf implementation.
