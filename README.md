# GRaPeC

Pronunciation (IPA): `/ɡreɪ.peɪk/` (similar to "gray-pay-k").

A lightweight utility for dynamically loading `.proto` files at runtime and exposing the two Python gRPC module views.

## Usage

```python
import grapec

pb2, pb2_grpc = grapec.load("path/to/your.proto")
```

- `pb2`: message types and `DESCRIPTOR` (equivalent to `*_pb2.py`)
- `pb2_grpc`: `Stub` / `Servicer` / `add_*_to_server` helpers (equivalent to `*_pb2_grpc.py`)
- Supports both relative and absolute proto paths.

> If the proto has no `service`, `load` still returns `(pb2, pb2_grpc)`, but `pb2_grpc` will not contain `Stub/Servicer` symbols.

## Quick Example

The demo lives in `examples/hello/`.

1. Start the server:

```bash
uv run python examples/hello/server.py
```

2. Run the client in another terminal:

```bash
uv run python examples/hello/client.py
```

Expected output:

```text
Hello, Grapec
```
