import enum
from datetime import datetime, timedelta, timezone
from typing import Annotated

import pytest

import grapec
from grapec import Id


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
    f: float
    s: str
    b: bytes
    flag: bool
    color: Color
    inner: Inner
    ints: list[int]
    strs: list[str]
    inners: list[Inner]
    counts: dict[str, int]
    by_id: dict[int, Inner]
    opt_i: int | None
    opt_s: str | None
    opt_inner: Inner | None
    ts: datetime
    dur: timedelta
    renumbered: Annotated[int, Id(20)]
    colors: list[Color]
    choice: Inner | str | int | None


def full_value() -> Everything:
    return Everything(
        i=-42,
        f=1.5,
        s="héllo",
        b=b"\x00\x01",
        flag=True,
        color=Color.BLUE,
        inner=Inner(label="in", weight=7),
        ints=[1, -2, 3_000_000_000],
        strs=["a", "", "c"],
        inners=[Inner(label="x", weight=1), Inner(label="y", weight=0)],
        counts={"a": 1, "b": 0},
        by_id={5: Inner(label="five", weight=5)},
        opt_i=0,
        opt_s="",
        opt_inner=Inner(label="", weight=0),
        ts=datetime(2024, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc),
        dur=timedelta(seconds=-3, microseconds=-500),
        renumbered=99,
        colors=[Color.RED, Color.BLUE],
        choice="pick me",
    )


def test_roundtrip():
    v = full_value()
    assert Everything.from_bytes(bytes(v)) == v
    assert v.to_bytes() == bytes(v)


def test_decodes_with_protobuf(oracle):
    v = full_value()
    msg = oracle.Everything()
    msg.ParseFromString(bytes(v))
    assert msg.i == -42
    assert msg.f == 1.5
    assert msg.s == "héllo"
    assert msg.b == b"\x00\x01"
    assert msg.flag is True
    assert msg.color == oracle.BLUE
    assert msg.inner.label == "in" and msg.inner.weight == 7
    assert list(msg.ints) == [1, -2, 3_000_000_000]
    assert list(msg.strs) == ["a", "", "c"]
    assert [(x.label, x.weight) for x in msg.inners] == [("x", 1), ("y", 0)]
    assert dict(msg.counts) == {"a": 1, "b": 0}
    assert msg.by_id[5].label == "five"
    assert msg.HasField("opt_i") and msg.opt_i == 0
    assert msg.HasField("opt_s") and msg.opt_s == ""
    assert msg.HasField("opt_inner")
    assert msg.ts.seconds == int(v.ts.timestamp()) and msg.ts.nanos == 678901000
    assert msg.dur.seconds == -3 and msg.dur.nanos == -500000
    assert msg.renumbered == 99
    assert list(msg.colors) == [oracle.RED, oracle.BLUE]
    assert msg.WhichOneof("choice") == "choice_str" and msg.choice_str == "pick me"


def test_encodes_same_bytes_as_protobuf(oracle):
    v = full_value()
    msg = oracle.Everything()
    msg.ParseFromString(bytes(v))
    assert msg.SerializeToString(deterministic=True) == bytes(v)


def test_decodes_protobuf_output(oracle):
    msg = oracle.Everything(
        i=1,
        s="x",
        color=oracle.RED,
        ints=[4, 5],
        strs=["q"],
        counts={"k": 2},
        opt_i=3,
    )
    msg.inner.label = "in"
    msg.inners.add(label="a", weight=2)
    msg.by_id[9].label = "nine"
    msg.ts.FromDatetime(datetime(2020, 5, 6, tzinfo=timezone.utc))
    msg.dur.FromTimedelta(timedelta(minutes=2))
    msg.colors.extend([oracle.BLUE, 77])
    msg.choice_inner.label = "chosen"

    v = Everything.from_bytes(msg.SerializeToString())
    assert v.i == 1 and v.s == "x" and v.color is Color.RED
    assert v.f == 0.0 and v.b == b"" and v.flag is False
    assert v.inner == Inner(label="in", weight=0)
    assert v.ints == [4, 5] and v.strs == ["q"]
    assert v.inners == [Inner(label="a", weight=2)]
    assert v.counts == {"k": 2}
    assert v.by_id == {9: Inner(label="nine", weight=0)}
    assert v.opt_i == 3 and v.opt_s is None and v.opt_inner is None
    assert v.ts == datetime(2020, 5, 6, tzinfo=timezone.utc)
    assert v.dur == timedelta(minutes=2)
    assert v.renumbered == 0
    assert v.colors == [Color.BLUE, 77]
    assert v.choice == Inner(label="chosen", weight=0)


def test_unpacked_repeated_is_accepted():
    # tag for field 8 varint, twice
    data = bytes([0x40, 0x01, 0x40, 0x02])
    v = Everything.from_bytes(data)
    assert v.ints == [1, 2]


def test_unknown_fields_are_skipped():
    data = bytes(Inner(label="a", weight=1)) + bytes([0xF8, 0x7F, 0x05])
    assert Inner.from_bytes(data) == Inner(label="a", weight=1)


def test_required_fields():
    with pytest.raises(TypeError):
        Inner(label="a")  # type: ignore[call-arg]


def test_implicit_defaults():
    @grapec.struct(package="t")
    class S:
        xs: list[int]
        m: dict[str, str]
        o: str | None
        n: int = 3

    s = S()
    assert s.xs == [] and s.m == {} and s.o is None and s.n == 3
    assert bytes(s) == bytes([0x20, 0x03])


def test_keyword_only_and_dataclass_behaviour():
    with pytest.raises(TypeError):
        Inner("a", 1)  # type: ignore[misc]
    assert repr(Inner(label="a", weight=1)) == "Inner(label='a', weight=1)"
    assert grapec.is_struct(Inner) and grapec.is_struct(Inner(label="", weight=0))
    assert not grapec.is_struct(int)


def test_auto_numbering_continues_after_explicit_id():
    from grapec._schema import schema_of

    @grapec.struct(package="t")
    class S:
        a: int
        b: Annotated[int, Id(10)]
        c: int
        d: Annotated[int, Id(3)]
        e: int

    assert [f.number for f in schema_of(S).fields] == [1, 10, 11, 3, 4]


def test_forward_reference_and_recursion():
    @grapec.struct(package="t")
    class Node:
        value: int
        children: list["Node"]
        parent: "Node | None"

    tree = Node(value=1, children=[Node(value=2), Node(value=3, parent=Node(value=0))])
    assert Node.from_bytes(bytes(tree)) == tree


@pytest.mark.parametrize(
    "annotation",
    ["set[int]", "int | bool", "list[int] | None", "list[int | str]", "int | list[str]", "list[list[int]]", "dict[float, int]", "object", "Plain"],
)
def test_unsupported_annotations(annotation):
    class Plain:
        pass

    ns = {"Plain": Plain}
    exec(
        "import grapec\n"
        "@grapec.struct(package='t')\n"
        f"class S:\n    x: {annotation}\n",
        ns,
    )
    with pytest.raises(grapec.SchemaError):
        bytes(ns["S"](x=None)) if "None" in annotation else ns["S"].from_bytes(b"")


def test_plain_enum_rejected():
    class E(enum.Enum):
        A = 1

    @grapec.struct(package="t")
    class S:
        e: E

    with pytest.raises(grapec.SchemaError):
        S.from_bytes(b"")


def test_bad_ids():
    with pytest.raises(grapec.SchemaError):
        Id(0)
    with pytest.raises(grapec.SchemaError):
        Id(19500)

    @grapec.struct(package="t")
    class S:
        a: Annotated[int, Id(2)]
        b: Annotated[int, Id(2)]

    with pytest.raises(grapec.SchemaError, match="duplicate"):
        S.from_bytes(b"")


def test_bad_package():
    with pytest.raises(grapec.SchemaError):
        grapec.struct(package="1bad")
    with pytest.raises(grapec.SchemaError):
        grapec.struct(package="a..b")


def test_wrong_value_types():
    with pytest.raises(grapec.EncodeError):
        bytes(Inner(label=1, weight=1))  # type: ignore[arg-type]
    with pytest.raises(grapec.EncodeError):
        bytes(Inner(label="a", weight=None))  # type: ignore[arg-type]
    with pytest.raises(grapec.EncodeError):
        bytes(Inner(label="a", weight=1 << 64))


def test_truncated_input():
    data = bytes(Inner(label="hello", weight=1))
    with pytest.raises(grapec.WireError):
        Inner.from_bytes(data[:3])


def test_naive_datetime_uses_local_timezone():
    @grapec.struct(package="t")
    class S:
        ts: datetime

    naive = datetime(2024, 1, 1, 12, 0, 0)
    out = S.from_bytes(bytes(S(ts=naive)))
    assert out.ts == naive.astimezone(timezone.utc)


def test_oneof_members():
    for value in (Inner(label="x", weight=1), "s", 5, None):
        v = Everything.from_bytes(bytes(Everything.from_bytes(b"")))
        v.choice = value
        assert Everything.from_bytes(bytes(v)).choice == value


def test_oneof_rejects_foreign_value():
    v = Everything.from_bytes(b"")
    v.choice = 1.5  # type: ignore[assignment]
    with pytest.raises(grapec.EncodeError):
        bytes(v)


def test_oneof_required_rejects_none():
    @grapec.struct(package="t")
    class S:
        kind: Inner | str

    with pytest.raises(TypeError):
        S()  # type: ignore[call-arg]
    with pytest.raises(grapec.EncodeError):
        bytes(S(kind=None))  # type: ignore[arg-type]
    assert S.from_bytes(b"").kind is None


def test_oneof_explicit_ids():
    from grapec._schema import schema_of

    @grapec.struct(package="t")
    class S:
        kind: Annotated[Inner, Id(7)] | Annotated[str, Id(9)] | None
        after: int

    numbers = [f.numbers() for f in schema_of(S).fields]
    assert numbers == [(7, 9), (10,)]
