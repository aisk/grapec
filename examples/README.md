# Examples

Two of them need nothing but a network connection, run them first.

| File | Needs | Shows |
| --- | --- | --- |
| [eliza.py](eliza.py) | network | the smallest possible client, one public gRPC service over TLS |
| [grpcbin.py](grpcbin.py) | network | a shared `Session` across services, nested and repeated fields, metadata, `CallDetails`, `RpcError`, plaintext and TLS, an async client |
| [grpc/](grpc) | `grpcio`, `grpcio-tools` | every supported proto type against a local grpcio server: enums, maps, oneof, `Timestamp`, `Duration`, `bytes`, explicit field ids, compression, `export_proto` |
| [thrift/](thrift) | `thriftpy2` | a local thrift server: multi parameter methods, `i32` widths, optional fields, declared and undeclared exceptions |

```
uv run python examples/eliza.py
uv run python examples/grpcbin.py
```

The local ones start a server written with the standard library of that
protocol, standing in for a service in any language. grapec never reads the
`.proto` or `.thrift` file, `models.py` is the whole contract on the client
side. `export.py` renders the proto again from `models.py` so the two can be
compared.

```
cd examples/grpc
uv run python server.py &
uv run python client.py
uv run python async_client.py
uv run python export.py

cd examples/thrift
uv run python server.py &
uv run python client.py
uv run python async_client.py
```

There is no public thrift endpoint we know of, so thrift only has the local
variant.
