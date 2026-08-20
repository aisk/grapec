import enum
from pathlib import Path

import pytest

import grapec
from test_client import Greeter, HelloReply, HelloRequest
from test_struct import Everything, Inner, full_value


def test_export_matches_oracle(oracle, tmp_path):
    """The exported proto, compiled by protoc, must parse our bytes identically."""
    import grpc_tools
    from grpc_tools import protoc

    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    text = grapec.export_proto(Everything)
    (tmp_path / "exported.proto").write_text(text)
    rc = protoc.main([
        "protoc",
        f"-I{tmp_path}",
        f"-I{Path(grpc_tools.__file__).parent / '_proto'}",
        "--include_imports",
        f"--descriptor_set_out={tmp_path / 'exported.pb'}",
        "exported.proto",
    ])
    assert rc == 0, text

    # a private pool, the oracle already owns test.v1.* in the default one
    fds = descriptor_pb2.FileDescriptorSet.FromString((tmp_path / "exported.pb").read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for fd in fds.file:
        pool.Add(fd)
    exported_cls = message_factory.GetMessageClass(pool.FindMessageTypeByName("test.v1.Everything"))

    data = bytes(full_value())
    ours = exported_cls.FromString(data)
    theirs = oracle.Everything.FromString(data)
    assert ours.SerializeToString(deterministic=True) == theirs.SerializeToString(deterministic=True)
    assert ours.choice_str == "pick me"


def test_export_text():
    text = grapec.export_proto(Greeter)
    assert text == '''syntax = "proto3";

package test.rpc;

message HelloReply {
  string message = 1;
  map<string, string> metadata = 2;
}

message HelloRequest {
  string name = 1;
  bytes blob = 2;
}

service Greeter {
  rpc SayHello (HelloRequest) returns (HelloReply);
  rpc Fail (HelloRequest) returns (HelloReply);
  rpc Slow (HelloRequest) returns (HelloReply);
  rpc Missing (HelloRequest) returns (HelloReply);
  rpc Compressed (HelloRequest) returns (HelloReply);
}
'''


def test_export_oneof_and_optional():
    text = grapec.export_proto(Everything)
    assert "  oneof choice {\n    Inner choice_inner = 22;\n    string choice_str = 23;\n    int64 choice_int = 24;\n  }" in text
    assert "optional int64 opt_i = 13;" in text
    assert "optional Inner opt_inner = 15;" in text
    assert 'import "google/protobuf/timestamp.proto";' in text
    assert "enum Color {\n  UNSPECIFIED = 0;\n  RED = 1;\n  BLUE = 2;\n}" in text


def test_export_cross_package():
    @grapec.struct(package="other.pkg")
    class Ref:
        inner: Inner

    text = grapec.export_proto(Ref)
    assert 'import "test/v1.proto";' in text
    assert "test.v1.Inner inner = 1;" in text
    assert "message Inner" not in text


def test_export_mixed_packages_rejected():
    with pytest.raises(grapec.SchemaError):
        grapec.export_proto(Inner, HelloRequest)
    with pytest.raises(grapec.SchemaError):
        grapec.export_proto(int)


def _compile(tmp_path, text):
    import grpc_tools
    from grpc_tools import protoc

    (tmp_path / "x.proto").write_text(text)
    return protoc.main([
        "protoc",
        f"-I{tmp_path}",
        f"-I{Path(grpc_tools.__file__).parent / '_proto'}",
        f"--descriptor_set_out={tmp_path / 'x.pb'}",
        "x.proto",
    ])


def test_export_enum_value_name_collision_is_rejected():
    class A(enum.IntEnum):
        UNSPECIFIED = 0
        X = 1

    class B(enum.IntEnum):
        UNSPECIFIED = 0
        Y = 1

    @grapec.struct(package="t.enums")
    class M:
        a: A
        b: B

    with pytest.raises(grapec.SchemaError, match="B_UNSPECIFIED"):
        grapec.export_proto(M)


def test_export_enum_zero_position_and_aliases(tmp_path):
    class Order(enum.IntEnum):
        ONE = 1
        ZERO = 0
        FIRST = 1  # alias

    class NoZero(enum.IntEnum):
        ALPHA = 5

    @grapec.struct(package="t.enums2")
    class M:
        o: Order
        n: NoZero

    text = grapec.export_proto(M)
    assert "enum Order {\n  option allow_alias = true;\n  ZERO = 0;\n  ONE = 1;\n  FIRST = 1;\n}" in text
    assert "enum NoZero {\n  NOZERO_UNSPECIFIED = 0;\n  ALPHA = 5;\n}" in text
    assert _compile(tmp_path, text) == 0, text


def test_export_foreign_enum_is_referenced_not_rendered():
    from test_struct import Color

    @grapec.struct(package="other.pkg2")
    class Ref:
        thing: Everything  # Color is first seen through test.v1, so it belongs there
        color: Color

    text = grapec.export_proto(Ref)
    assert "enum Color" not in text
    assert "test.v1.Color color = 2;" in text
    assert text.count('import "test/v1.proto";') == 1


def test_export_includes_inherited_methods():
    class Child(Greeter, name="Greeter"):
        def extra(self, request: HelloRequest) -> HelloReply: ...

    text = grapec.export_proto(Child)
    assert "  rpc SayHello (HelloRequest) returns (HelloReply);\n" in text
    assert "  rpc extra (HelloRequest) returns (HelloReply);\n}" in text
