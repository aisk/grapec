# Repository Guidelines

## Project Structure & Module Organization
grapec turns plain annotated Python classes into serializable structs that speak the protobuf wire format (and the thrift binary protocol), and calls remote services described by annotated service classes. Python is the source of truth, there are no `.proto` files at runtime. The only runtime dependency is `h2`.

- `src/grapec/__init__.py`: public API (`struct`, `Id`, `is_struct`, error types).
- `src/grapec/struct.py`: the `@struct(package=...)` decorator, turns the class into a keyword only dataclass and injects implicit defaults.
- `src/grapec/schema.py`: resolves type hints into an internal, wire agnostic schema (`StructSchema`, `FieldSpec`, `TypeSpec`). `Annotated[int, I8/I16/I32]` sets `ScalarType.width`, 64 by default.
- `src/grapec/protobuf.py`: encodes and decodes instances with the protobuf wire format using the schema.
- `src/grapec/thrift.py`: the same for the thrift binary protocol, plus the call layer: `method_fields` / `encode_call` / `decode_result` turn a `MethodSpec` into args and result structs (declared exceptions are result fields 1..n), and `ThriftProtocol` is the sans-IO message state machine (strict TBinary header, TFramed, seqid check, TApplicationException to `RpcError`). Validates schemas once per class and rejects what thrift cannot express.
- `src/grapec/wire.py`: low level protobuf wire primitives (varint, tags, length delimited).
- `src/grapec/dict.py`: `to_dict` / `from_dict` and the proto3 JSON mapping (`to_json` / `from_json`).
- `src/grapec/proto.py`: `export_proto`, renders structs and services as proto3 source.
- `src/grapec/service.py`: `Client` / `AsyncClient` base classes (users subclass them with `package=`), `@name(...)`, `CallOptions`, `CallDetails` (response metadata out parameter), and the module helpers `close` / `aclose` / `session_of`. Declared methods are replaced by `_RemoteMethod` descriptors carrying a `MethodSpec` (`params`, `returns`, `raises`, `unary_struct` for the gRPC shape), bound methods are cached per instance and carry the spec too. `@raises(...)` declares thrift exception structs.
- `src/grapec/session.py`: protocol neutral `Session` and `AsyncSession` (connection owners, low level `call`), the `Connection` / `AsyncConnection` protocols transports implement (`healthy`, `poll`, `unary`, `close`), the URL scheme to transport mapping, and the per protocol call adapters (`_GrpcProtocol`, `_ThriftProtocol`: `check` a `MethodSpec` shape, `encode` arguments to `(path, payload)`, `decode` the reply). `check_service` runs the shape check for every method when a client is constructed. `poll` is called before an idle connection is reused and must never block.
- `src/grapec/pool.py`: idle connection bookkeeping shared by both clients, including the `max_idle_time` eviction. The `max_conns` cap lives in `session.py` instead, one semaphore flavour per client.
- `src/grapec/grpc.py`: sans-IO gRPC over HTTP/2 state machine on top of `h2`, the only place that knows gRPC framing and headers. Never touches a socket. Feeds h2 one frame at a time so a response that completes right before a GOAWAY survives, ignores RST_STREAM after END_STREAM, and cancels a stream with RST_STREAM(CANCEL) on deadline expiry instead of dropping the connection.
- `src/grapec/sync.py` and `src/grapec/aio.py`: thin blocking socket and asyncio shells around `GrpcProtocol` and `ThriftProtocol`.
- `src/grapec/errors.py`: `Status`, `RpcError`, `TransportError`.
- `docs/reference.md`: the full user facing reference. The README stays a short introduction, document new public behaviour in the reference.
- `examples/hello/`: gRPC demo. `server.py` is a grpcio server standing in for a foreign service, `client.py` and `async_client.py` use grapec only.
- `examples/store/`: thrift demo, `server.py` is a thriftpy2 server, `client.py` uses grapec only.
- `tests/`: pytest suite. `tests/oracle.thrift` and `tests/rpc.thrift` are loaded by `thriftpy2`, the first as the serialization reference, the second to run a real thrift server. `tests/oracle.proto` and `tests/rpc.proto` are compiled with `grpcio-tools` at test time, the first as a serialization reference, the second to run a real grpcio server. `tests/test_protocol.py` drives `GrpcProtocol` against an h2 server connection without sockets, use it for frame level edge cases.

Keep the schema layer free of wire format details, the clients free of protocol details, and protocol state machines free of IO. A protocol is a sans-IO state machine, two thin IO shells, a call adapter in `session.py` and a URL scheme.

## Build, Test, and Development Commands
- `uv sync`: create/update the virtual environment.
- `uv run pytest`: run the test suite.
- `uv run python examples/hello/server.py` then `uv run python examples/hello/client.py` (from `examples/hello`): run the demo.
- `uv build`: build wheel and sdist into `dist/`.

## Coding Style & Naming Conventions
- PEP 8, 4-space indentation, type annotated public functions.
- Python 3.12+ features are welcome when they make the API nicer.
- Naming must stay transport neutral. Do not put `grpc` or `proto` into public names.
- Keep user facing class bodies pure Python. Extra metadata goes into `Annotated[...]`, never into field defaults.
- `Client` / `AsyncClient` must not grow public (non dunder) attributes or methods, user RPC method names live in that namespace. Put helpers at module level.
- Call options (`timeout`, `metadata`, `compression`, `details`, see `CallOptions`) are keyword arguments of every remote method. Methods may take several parameters (thrift), parameters named like a call option are rejected at class definition time with a hint to rename, since Python parameter names never affect the wire. Protocol specific shape rules (gRPC: one struct in, one struct out) are checked when a client is constructed, not at class definition.

## Testing Guidelines
- Add tests under `tests/`, name files `test_*.py`, cover normal and failure cases.
- Wire format changes must be checked against the protobuf oracle, extend `tests/oracle.proto` when adding types. The thrift codec is checked the same way against `thriftpy2` with `tests/oracle.thrift` (note `i8`, `i16`, `i32` are reserved words there, do not use them as field names).
- thrift RPC is tested against a thriftpy2 server (`thrift_server` fixture, `tests/rpc.thrift`), `tests/test_thrift_protocol.py` feeds frames to `ThriftProtocol` directly. thriftpy2 closes the connection on undeclared handler exceptions instead of sending `TApplicationException`, do not assert on that.
- Transport behaviour is tested against a real grpcio server (`rpc_server` fixture, `tls_rpc_server` for `grpcs://`, `serve` for short lived servers with custom channel options). Cover both `Client` and `AsyncClient` subclasses, async tests use `pytest-asyncio` in strict mode. Anything that can leave a connection in a bad state (cancellation, timeouts, GOAWAY) needs a test that the next call still works.
- Run `uv run pytest` before opening a PR.

## Commit & Pull Request Guidelines
- Concise, imperative commit messages, optional emoji prefix, e.g. `✨ Add map support`.
- Keep commits scoped to one logical change.
- PRs should include purpose, key changes and how to verify.
