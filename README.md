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

### Import Hook

```python
import grapec

grapec.install_import_hook()

import hello_pb2
import hello_pb2_grpc
```

- After `install_import_hook()`, Grapec searches `cwd + sys.path` for a matching `.proto` file, for example `hello_pb2` -> `hello.proto`.
- Call `grapec.uninstall_import_hook()` to remove the hook when you no longer need it.


## Quick Example

The demo lives in `examples/hello/`.

`examples/hello/hello.proto`:

```proto
syntax = "proto3";
package examples.hello.v1;

// The greeting service definition.
service Greeter {
  // Sends a greeting
  rpc SayHello (HelloRequest) returns (HelloReply) {}
}

// The request message containing the user's name.
message HelloRequest {
  string name = 1;
}

// The response message containing the greetings
message HelloReply {
  string message = 1;
}
```

`examples/hello/server.py`:

```python
from pathlib import Path

import grpc
import logging
import grapec
from concurrent import futures

PROTO_PATH = Path(__file__).resolve().with_name("hello.proto")
hello_pb2, hello_pb2_grpc = grapec.load(str(PROTO_PATH))


class GreeterServicer(hello_pb2_grpc.GreeterServicer):
    def SayHello(self, request, context):
        if not request.name.strip():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "name is required")
        return hello_pb2.HelloReply(message=f"Hello, {request.name}")


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    hello_pb2_grpc.add_GreeterServicer_to_server(
        GreeterServicer(), server
    )
    server.add_insecure_port("localhost:50051")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()
```

`examples/hello/client.py`:

```python
import grpc
import grapec

grapec.install_import_hook()

import hello_pb2
import hello_pb2_grpc


def run():
    with grpc.insecure_channel("localhost:50051") as channel:
        stub = hello_pb2_grpc.GreeterStub(channel)
        req = hello_pb2.HelloRequest(name='Grapec')
        resp = stub.SayHello(req)
        print(resp.message)


if __name__ == "__main__":
    run()
```

1. Start the server:

```bash
uv run --with grapec server.py
```

2. Run the client in another terminal:

```bash
uv run --with grapec client.py
```

Expected output:

```text
Hello, Grapec
```
