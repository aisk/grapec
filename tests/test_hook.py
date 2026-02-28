import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import grapec.hook as hook

PROTO_TEXT = """syntax = "proto3";
package tests.hello.v1;

service Greeter {
  rpc SayHello (HelloRequest) returns (HelloReply) {}
}

message HelloRequest {
  string name = 1;
}

message HelloReply {
  string message = 1;
}
"""


def _reset_modules() -> None:
    sys.modules.pop("hello_pb2", None)
    sys.modules.pop("hello_pb2_grpc", None)


def _prepare_proto(tmp_path) -> None:
    (tmp_path / "hello.proto").write_text(PROTO_TEXT, encoding="utf-8")


def test_import_pb2_basic(tmp_path, monkeypatch) -> None:
    _prepare_proto(tmp_path)
    monkeypatch.chdir(tmp_path)
    _reset_modules()
    hook.uninstall()
    hook.install()

    module = importlib.import_module("hello_pb2")

    assert hasattr(module, "DESCRIPTOR")


def test_import_pb2_grpc_basic(tmp_path, monkeypatch) -> None:
    _prepare_proto(tmp_path)
    monkeypatch.chdir(tmp_path)
    _reset_modules()
    hook.uninstall()
    hook.install()

    module = importlib.import_module("hello_pb2_grpc")

    assert hasattr(module, "GreeterStub")
