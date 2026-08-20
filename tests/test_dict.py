import json
from datetime import datetime, timedelta, timezone

import pytest
from google.protobuf import json_format

import grapec
from test_struct import Color, Everything, Inner, full_value


def test_to_dict_keeps_python_values():
    d = full_value().to_dict()
    assert d["color"] is Color.BLUE
    assert d["inner"] == {"label": "in", "weight": 7}
    assert d["inners"][0] == {"label": "x", "weight": 1}
    assert d["by_id"] == {5: {"label": "five", "weight": 5}}
    assert isinstance(d["ts"], datetime) and isinstance(d["dur"], timedelta)
    assert d["b"] == b"\x00\x01"
    assert d["opt_inner"] == {"label": "", "weight": 0}
    assert d["choice"] == "pick me"
    assert Everything.from_dict(d) == full_value()


def test_from_dict_partial_and_unknown_keys():
    v = Inner.from_dict({"label": "a", "junk": 1})
    assert v == Inner(label="a", weight=0)
    with pytest.raises(TypeError):
        Inner.from_dict({"label": 1})


def test_json_matches_protobuf(oracle):
    v = full_value()
    msg = oracle.Everything()
    msg.ParseFromString(bytes(v))
    expected = json.loads(json_format.MessageToJson(msg))
    assert json.loads(v.to_json()) == expected


def test_json_roundtrip():
    v = full_value()
    assert Everything.from_json(v.to_json()) == v


def test_from_json_accepts_protobuf_output(oracle):
    msg = oracle.Everything(i=-5, s="x", color=oracle.RED, opt_i=0, ints=[1, 2])
    msg.ts.FromDatetime(datetime(2021, 3, 4, 5, 6, 7, 123000, tzinfo=timezone.utc))
    msg.dur.FromTimedelta(timedelta(seconds=-1, microseconds=-500000))
    msg.choice_inner.label = "c"
    text = json_format.MessageToJson(msg)
    v = Everything.from_json(text)
    assert v.i == -5 and v.s == "x" and v.color is Color.RED
    assert v.opt_i == 0 and v.opt_s is None
    assert v.ints == [1, 2]
    assert v.ts == datetime(2021, 3, 4, 5, 6, 7, 123000, tzinfo=timezone.utc)
    assert v.dur == timedelta(seconds=-1, microseconds=-500000)
    assert v.choice == Inner(label="c", weight=0)


def test_json_defaults_are_omitted():
    v = Everything.from_bytes(b"")
    assert json.loads(v.to_json()) == {"inner": {}, "ts": "1970-01-01T00:00:00Z", "dur": "0s"}


def test_json_special_floats_and_enum_numbers():
    @grapec.struct(package="t")
    class S:
        f: float
        c: Color

    assert json.loads(S(f=float("inf"), c=9).to_json()) == {"f": "Infinity", "c": 9}
    assert S.from_json('{"f": "NaN", "c": "RED"}').c is Color.RED
    assert S.from_dict({"f": 1, "c": 2}) == S(f=1.0, c=Color.BLUE)


def test_json_accepts_snake_and_camel():
    @grapec.struct(package="t")
    class S:
        user_id: int
        display_name: str

    assert json.loads(S(user_id=1, display_name="a").to_json()) == {"userId": "1", "displayName": "a"}
    assert S.from_dict({"user_id": 1, "displayName": "a"}) == S(user_id=1, display_name="a")


def test_oneof_json_uses_member_names():
    v = Everything.from_bytes(b"")
    v.choice = Inner(label="q", weight=1)
    assert json.loads(v.to_json())["choiceInner"] == {"label": "q", "weight": "1"}
    for key in ("choice", "choiceInner", "choice_inner"):
        assert Everything.from_dict({key: {"label": "q"}}).choice == Inner(label="q", weight=0)
    assert Everything.from_dict({"choiceInt": "5"}).choice == 5
    assert Everything.from_dict({"choice": 5}).choice == 5
    assert Everything.from_dict({"choice": "s"}).choice == "s"


def test_null_means_unset():
    v = Everything.from_json('{"i": null, "ints": null, "counts": null, "optI": null, "inner": null, "choiceStr": null}')
    assert v.i == 0 and v.ints == [] and v.counts == {} and v.opt_i is None
    assert v.inner == Inner(label="", weight=0)
    assert v.choice is None
    assert Everything.from_bytes(bytes(v)) == v
    assert Inner.from_dict({"label": None, "weight": 1}) == Inner(label="", weight=1)


def test_from_dict_accepts_struct_instances():
    inner = Inner(label="a", weight=1)
    v = Everything.from_dict({"inner": inner, "inners": [inner], "by_id": {1: inner}, "choice": inner})
    assert v.inner == inner and v.inners == [inner] and v.by_id == {1: inner} and v.choice == inner


@pytest.mark.parametrize(
    "text",
    ["2021-03-04T05:06:07Z", "2021-03-04T05:06:07z", "2021-03-04T05:06:07+00:00", "2021-03-04T07:06:07+0200", "2021-03-04T00:06:07-05:00"],
)
def test_timestamp_formats(text):
    @grapec.struct(package="t")
    class S:
        ts: datetime

    assert S.from_json(json.dumps({"ts": text})).ts == datetime(2021, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
