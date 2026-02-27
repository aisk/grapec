# Repository Guidelines

## Project Structure & Module Organization
This repository is a small Python package for dynamically loading gRPC/protobuf definitions.

- `src/grapec/__init__.py`: core library API (`load(path: str)`).
- `examples/hello/hello.proto`: example protobuf service contract used by demos.
- `examples/hello/server.py` and `examples/hello/client.py`: local gRPC demo scripts.
- `dist/`: built artifacts (`.whl`, `.tar.gz`); treat as output, not source.
- `pyproject.toml` and `uv.lock`: packaging metadata and dependency lockfile.

Keep reusable logic inside `src/grapec/`; avoid adding business logic to demo scripts.

## Build, Test, and Development Commands
Use `uv` for a consistent local environment:

- `uv sync`: create/update the virtual environment from lockfile.
- `uv run python examples/hello/server.py`: run the demo gRPC server on `localhost:50051`.
- `uv run python examples/hello/client.py`: call the demo `Greeter` service.
- `uv build`: build wheel and sdist into `dist/`.

If `uv` is unavailable, install dependencies with `pip install grpcio-tools` and run scripts with `python`.

## Coding Style & Naming Conventions
Follow standard Python conventions (PEP 8):

- 4-space indentation, UTF-8 text files.
- `snake_case` for functions/variables, `PascalCase` for classes.
- Keep public functions type-annotated (for example, `load(path: str) -> tuple[types.ModuleType, types.ModuleType]`).
- Prefer small, focused functions in `src/grapec` and keep examples minimal.

## Testing Guidelines
There is currently no committed automated test suite. For new contributions:

- Add tests under `tests/` using `pytest`.
- Name files `test_*.py` and cover normal + failure cases (e.g., invalid proto path).
- Run tests with `uv run pytest` before opening a PR.

When adding features, include at least one regression test.

## Commit & Pull Request Guidelines
Current history is minimal (e.g., `🎉 Initialize project`), so use concise, imperative commit messages.

- Recommended format: optional emoji + short summary, e.g., `✨ Add proto path validation`.
- Keep commits scoped to one logical change.
- PRs should include: purpose, key changes, how to run/verify, and related issue links.
- For behavior changes in `examples/hello/client.py` or `examples/hello/server.py`, include a short run example or output snippet.
