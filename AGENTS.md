# Repository Guidelines

## Project Structure & Module Organization
grapec turns plain annotated Python classes into serializable structs that speak the protobuf wire format. Python is the source of truth, there are no `.proto` files at runtime and no runtime dependencies.

- `src/grapec/__init__.py`: public API (`struct`, `Id`, `is_struct`, error types).
- `src/grapec/_struct.py`: the `@struct(package=...)` decorator, turns the class into a keyword only dataclass and injects implicit defaults.
- `src/grapec/_schema.py`: resolves type hints into an internal, wire agnostic schema (`StructSchema`, `FieldSpec`, `TypeSpec`).
- `src/grapec/_codec.py`: encodes and decodes instances using the schema.
- `src/grapec/_wire.py`: low level protobuf wire primitives (varint, tags, length delimited).
- `examples/hello/`: minimal usage demo.
- `tests/`: pytest suite. `tests/oracle.proto` is compiled with `grpcio-tools` at test time and used as a reference implementation.

Keep the schema layer free of wire format details so other transports and formats (thrift, RPC) can reuse it.

## Build, Test, and Development Commands
- `uv sync`: create/update the virtual environment.
- `uv run pytest`: run the test suite.
- `uv run python examples/hello/main.py`: run the demo (from `examples/hello`).
- `uv build`: build wheel and sdist into `dist/`.

## Coding Style & Naming Conventions
- PEP 8, 4-space indentation, type annotated public functions.
- Python 3.12+ features are welcome when they make the API nicer.
- Naming must stay transport neutral. Do not put `grpc` or `proto` into public names.
- Keep user facing class bodies pure Python. Extra metadata goes into `Annotated[...]`, never into field defaults.

## Testing Guidelines
- Add tests under `tests/`, name files `test_*.py`, cover normal and failure cases.
- Wire format changes must be checked against the protobuf oracle, extend `tests/oracle.proto` when adding types.
- Run `uv run pytest` before opening a PR.

## Commit & Pull Request Guidelines
- Concise, imperative commit messages, optional emoji prefix, e.g. `✨ Add map support`.
- Keep commits scoped to one logical change.
- PRs should include purpose, key changes and how to verify.
