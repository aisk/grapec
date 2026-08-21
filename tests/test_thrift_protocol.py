"""Frame level tests for the sans-IO ThriftProtocol."""

import struct

import pytest

import grapec
from grapec.thrift import CALL, EXCEPTION, REPLY, STOP, ThriftProtocol, decode_message, encode_message


def reply(name, seqid, body=b"\x00", mtype=REPLY):
    return encode_message(name, mtype, seqid, body)


def test_call_message_layout():
    proto = ThriftProtocol()
    proto.start("get", b"\x00")
    data = proto.data_to_send()
    length = struct.unpack(">I", data[:4])[0]
    assert length == len(data) - 4
    name, mtype, seqid, body = decode_message(data[4:])
    assert (name, mtype, seqid, body) == ("get", CALL, 1, b"\x00")
    assert proto.busy and not proto.done
    assert proto.data_to_send() == b""


def test_multiplexed_prefix():
    proto = ThriftProtocol("Store")
    proto.start("get", b"\x00")
    assert decode_message(proto.data_to_send()[4:])[0] == "Store:get"
    proto.feed(reply("Store:get", 1))
    assert proto.result() == b"\x00"
    proto.feed  # reply without the prefix is accepted too
    proto.start("get", b"\x00")
    proto.feed(reply("get", 2))
    assert proto.result() == b"\x00"


def test_reply_in_pieces_and_seqid_increments():
    proto = ThriftProtocol()
    proto.start("get", b"\x00")
    proto.data_to_send()
    data = reply("get", 1, b"\x0b\x00\x00\x00\x00\x00\x01x\x00")
    for i in range(len(data)):
        assert not proto.done
        proto.feed(data[i : i + 1])
    assert proto.done
    assert proto.result() == b"\x0b\x00\x00\x00\x00\x00\x01x\x00"
    assert not proto.busy and proto.healthy
    proto.start("get", b"\x00")
    assert decode_message(proto.data_to_send()[4:])[2] == 2


def test_application_exception_maps_to_status():
    proto = ThriftProtocol()
    proto.start("nope", b"\x00")
    body = b"\x0b\x00\x01" + struct.pack(">i", 7) + b"no such" + b"\x08\x00\x02" + struct.pack(">i", 1) + bytes([STOP])
    proto.feed(reply("nope", 1, body, EXCEPTION))
    with pytest.raises(grapec.RpcError) as info:
        proto.result()
    assert info.value.code is grapec.Status.UNIMPLEMENTED
    assert "UNKNOWN_METHOD: no such" in info.value.message
    assert proto.healthy and not proto.busy


@pytest.mark.parametrize(
    "frame, match",
    [
        (reply("get", 2), "seqid"),
        (reply("other", 1), "waiting for"),
        (reply("get", 1, mtype=CALL), "message type"),
        (struct.pack(">I", 4) + b"\x00\x00\x00\x00", "version"),
        (reply("get", 1) + b"\x00", "more than one"),
        (b"", "closed by peer"),
    ],
)
def test_bad_replies_kill_the_connection(frame, match):
    proto = ThriftProtocol()
    proto.start("get", b"\x00")
    with pytest.raises(grapec.TransportError, match=match):
        proto.feed(frame)
    assert not proto.healthy


def test_oversized_frame_is_rejected():
    proto = ThriftProtocol()
    proto.start("get", b"\x00")
    with pytest.raises(grapec.TransportError, match="exceeds"):
        proto.feed(struct.pack(">I", 1 << 30))
    assert not proto.healthy


def test_data_while_idle_is_fatal_but_quiet():
    proto = ThriftProtocol()
    proto.feed_idle(b"\x00")
    assert not proto.healthy
    with pytest.raises(grapec.TransportError, match="closed"):
        proto.start("get", b"\x00")


def test_busy_connection_refuses_second_call():
    proto = ThriftProtocol()
    proto.start("get", b"\x00")
    with pytest.raises(grapec.TransportError, match="busy"):
        proto.start("get", b"\x00")
    proto.abort()
    assert not proto.busy and not proto.healthy
