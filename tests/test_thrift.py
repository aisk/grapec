"""thrift binary codec, checked byte for byte against thriftpy2."""

import enum
from datetime import datetime
from pathlib import Path
from typing import Annotated

import pytest
import thriftpy2
from thriftpy2.utils import deserialize, serialize

import grapec
from grapec import I8, I16, I32, Id

oracle = thriftpy2.load(str(Path(__file__).with_name("oracle.thrift")), module_name="oracle_thrift")


class Color(enum.IntEnum):
    UNSPECIFIED = 0
    RED = 1
    BLUE = 2


@grapec.struct(package="test.v1")
class Inner:
    label: str
    weight: int


@grapec.struct(package="test.v1")
class Everything:
    i: int
    n8: Annotated[int, I8]
    n16: Annotated[int, I16]
    n32: Annotated[int, I32]
    f: float
    s: str
    b: bytes
    flag: bool
    color: Color
    inner: Inner
    ints: list[Annotated[int, I32]]
    strs: list[str]
    inners: list[Inner]
    counts: dict[str, int]
    by_id: dict[Annotated[int, I32], Inner]
    opt_i: int | None
    opt_s: str | None
    opt_inner: Inner | None


@grapec.struct(package="test.v1")
class Pinned:
    a: int
    b: Annotated[int, Id(10)]
    c: int


@grapec.struct(package="test.v1")
class Choice:
    choice: int | str | Inner


@grapec.struct(package="test.v1")
class Sparse:
    only: int | None


@grapec.struct(package="test.v1")
class Sets:
    tags: list[str]


def everything() -> Everything:
    return Everything(
        i=-5,
        n8=-128,
        n16=32767,
        n32=-(1 << 31),
        f=1.5,
        s="héllo",
        b=b"\x00\xff",
        flag=True,
        color=Color.BLUE,
        inner=Inner(label="x", weight=1),
        ints=[1, -2, 3],
        strs=["a", "b"],
        inners=[Inner(label="y", weight=2)],
        counts={"k": 7},
        by_id={1: Inner(label="z", weight=3)},
        opt_i=0,
        opt_s="",
        opt_inner=Inner(label="", weight=0),
    )


def everything_oracle() -> object:
    return oracle.Everything(
        i=-5,
        n8=-128,
        n16=32767,
        n32=-(1 << 31),
        f=1.5,
        s="héllo",
        b=b"\x00\xff",
        flag=True,
        color=oracle.Color.BLUE,
        inner=oracle.Inner(label="x", weight=1),
        ints=[1, -2, 3],
        strs=["a", "b"],
        inners=[oracle.Inner(label="y", weight=2)],
        counts={"k": 7},
        by_id={1: oracle.Inner(label="z", weight=3)},
        opt_i=0,
        opt_s="",
        opt_inner=oracle.Inner(label="", weight=0),
    )


def test_everything_matches_oracle():
    data = everything().to_bytes(codec="thrift")
    assert data == serialize(everything_oracle())
    assert Everything.from_bytes(data, codec="thrift") == everything()


def test_zero_values_are_written():
    obj = Everything(
        i=0, n8=0, n16=0, n32=0, f=0.0, s="", b=b"", flag=False, color=Color.UNSPECIFIED,
        inner=Inner(label="", weight=0),
    )
    ref = oracle.Everything(
        i=0, n8=0, n16=0, n32=0, f=0.0, s="", b=b"", flag=False, color=0,
        inner=oracle.Inner(label="", weight=0), ints=[], strs=[], inners=[], counts={}, by_id={},
    )
    data = obj.to_bytes(codec="thrift")
    assert data == serialize(ref)
    assert Everything.from_bytes(data, codec="thrift") == obj


def test_missing_fields_decode_to_zero_values():
    obj = Everything.from_bytes(b"\x00", codec="thrift")
    assert obj.i == 0 and obj.s == "" and obj.inner == Inner(label="", weight=0)
    assert obj.ints == [] and obj.counts == {} and obj.opt_i is None and obj.opt_inner is None


def test_pinned_ids():
    obj = Pinned(a=1, b=2, c=3)
    data = obj.to_bytes(codec="thrift")
    assert data == serialize(oracle.Pinned(a=1, b=2, c=3))
    assert Pinned.from_bytes(data, codec="thrift") == obj


@pytest.mark.parametrize("value", [5, "s", Inner(label="l", weight=9)])
def test_union(value):
    obj = Choice(choice=value)
    data = obj.to_bytes(codec="thrift")
    ref = oracle.WithUnion(choice=oracle.Choice(**{
        int: {"choice_int": 5}, str: {"choice_str": "s"}, Inner: {"choice_inner": oracle.Inner(label="l", weight=9)},
    }[type(value)]))
    assert data == serialize(ref.choice)
    assert Choice.from_bytes(data, codec="thrift") == obj


def test_unset_union_and_optional_are_omitted():
    assert Choice(choice=None).to_bytes(codec="thrift") == b"\x00"
    assert Sparse(only=None).to_bytes(codec="thrift") == serialize(oracle.Sparse())
    assert Sparse.from_bytes(b"\x00", codec="thrift") == Sparse(only=None)


def test_unknown_fields_are_skipped():
    data = serialize(everything_oracle())
    # id 1 is i64 in both, every other field (all container types included) is skipped
    assert Sparse.from_bytes(data, codec="thrift") == Sparse(only=-5)


def test_set_on_the_wire_is_accepted_for_list():
    data = serialize(oracle.Sets(tags={"a"}))
    assert Sets.from_bytes(data, codec="thrift") == Sets(tags=["a"])


def test_oracle_reads_our_bytes():
    ref = deserialize(oracle.Everything(), everything().to_bytes(codec="thrift"))
    assert ref == everything_oracle()


def test_width_range_is_checked():
    with pytest.raises(grapec.EncodeError, match="8 bits"):
        Everything.from_bytes(b"\x00", codec="thrift").__class__(**{**everything().__dict__, "n8": 128}).to_bytes(codec="thrift")
    with pytest.raises(grapec.EncodeError, match="32 bits"):
        Everything(**{**everything().__dict__, "ints": [1 << 31]}).to_bytes(codec="thrift")


def test_width_is_ignored_by_protobuf():
    obj = everything()
    assert Everything.from_bytes(obj.to_bytes()) == obj
    assert Everything(**{**obj.__dict__, "n8": 1000}).to_bytes()  # no range check there


def test_type_mismatch_is_an_error():
    data = serialize(oracle.Inner(label="x", weight=1))

    @grapec.struct(package="test.v1")
    class Other:
        label: int

    with pytest.raises(grapec.ThriftError, match="does not match"):
        Other.from_bytes(data, codec="thrift")


def test_truncated_input():
    data = serialize(oracle.Inner(label="xyz", weight=1))
    with pytest.raises(grapec.ThriftError, match="truncated"):
        Inner.from_bytes(data[:-3], codec="thrift")
    with pytest.raises(grapec.ThriftError, match="trailing"):
        Inner.from_bytes(data + b"\x00", codec="thrift")


def test_datetime_is_rejected():
    @grapec.struct(package="test.v1")
    class Stamped:
        at: datetime

    with pytest.raises(grapec.SchemaError, match="no thrift counterpart"):
        Stamped(at=datetime(2020, 1, 1)).to_bytes(codec="thrift")
    with pytest.raises(grapec.SchemaError, match="no thrift counterpart"):
        Stamped.from_bytes(b"\x00", codec="thrift")


def test_field_id_must_fit_i16():
    @grapec.struct(package="test.v1")
    class Big:
        x: Annotated[int, Id(40000)]

    assert Big(x=1).to_bytes()
    with pytest.raises(grapec.SchemaError, match="i16"):
        Big(x=1).to_bytes(codec="thrift")


def test_width_marker_rules():
    with pytest.raises(grapec.SchemaError, match="only applies to int"):
        @grapec.struct(package="test.v1")
        class Bad:
            s: Annotated[str, I32]
        Bad(s="").to_bytes()

    with pytest.raises(grapec.SchemaError, match="more than one width"):
        @grapec.struct(package="test.v1")
        class Twice:
            n: Annotated[int, I16, I32]
        Twice(n=0).to_bytes()


def test_recursive_struct():
    # review H2: check_schema recursed forever on self referencing structs
    @grapec.struct(package="test.v1")
    class Node:
        children: list["Node"]
        next: "Node | None"

    obj = Node(children=[Node(children=[], next=None)], next=None)
    data = obj.to_bytes(codec="thrift")
    assert Node.from_bytes(data, codec="thrift") == obj


def test_errors_are_grapec_errors():
    assert issubclass(grapec.ThriftError, grapec.GrapecError)
    assert issubclass(grapec.WireError, grapec.GrapecError)


def test_unknown_codec():
    with pytest.raises(ValueError, match="unknown codec"):
        Inner(label="", weight=0).to_bytes(codec="avro")


def test_export_proto_uses_int32_for_narrow_ints():
    text = grapec.export_proto(Everything)
    assert "int32 n8 = 2;" in text and "int32 n32 = 4;" in text and "int64 i = 1;" in text
    assert "repeated int32 ints = 11;" in text
