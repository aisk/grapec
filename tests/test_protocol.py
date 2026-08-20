"""Sans-IO tests for GrpcProtocol using an h2 server connection as the peer."""

import h2.config
import h2.connection
import h2.errors
import hyperframe.frame
import pytest

import grapec
from grapec._grpc import GrpcProtocol


def _pair():
    client = GrpcProtocol("example:1", tls=False)
    server = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False, header_encoding="utf-8"))
    server.initiate_connection()
    # exchange prefaces and settings
    server.receive_data(client.data_to_send())
    client.feed(server.data_to_send())
    server.receive_data(client.data_to_send())
    return client, server


def _start_call(client, server):
    client.start("/svc/M", b"\x08\x01", timeout=None, metadata=None, compression=None)
    events = server.receive_data(client.data_to_send())
    (stream_id,) = [e.stream_id for e in events if isinstance(e, h2.events.RequestReceived)]
    return stream_id


def _raw_rst(stream_id, code=0):
    frame = hyperframe.frame.RstStreamFrame(stream_id)
    frame.error_code = code
    return frame.serialize()


def _raw_ping():
    return hyperframe.frame.PingFrame(0, opaque_data=b"12345678").serialize()


def _reply(server, stream_id, payload=b"\x00\x00\x00\x00\x02\x08\x02", trailers=(("grpc-status", "0"),)):
    server.send_headers(stream_id, [(":status", "200"), ("content-type", "application/grpc")])
    server.send_data(stream_id, payload)
    server.send_headers(stream_id, list(trailers), end_stream=True)


def test_roundtrip():
    client, server = _pair()
    sid = _start_call(client, server)
    _reply(server, sid, trailers=(("grpc-status", "0"), ("x-t", "v"), ("x-b-bin", "AQI=")))
    client.feed(server.data_to_send())
    assert client.done
    payload, headers, trailers = client.result()
    assert payload == b"\x08\x02"
    assert headers == {"content-type": "application/grpc"}
    assert trailers == {"x-t": "v", "x-b-bin": b"\x01\x02"}
    assert client.healthy and not client.busy


def test_rst_stream_after_end_stream_is_ignored():
    client, server = _pair()
    sid = _start_call(client, server)
    _reply(server, sid)
    # h2 refuses to reset a closed stream, build the frame by hand
    client.feed(server.data_to_send() + _raw_rst(sid))  # trailers and RST_STREAM in one read
    assert client.done
    assert client.result()[0] == b"\x08\x02"
    assert client.healthy


def test_goaway_after_complete_response_keeps_the_result():
    client, server = _pair()
    sid = _start_call(client, server)
    _reply(server, sid, trailers=(("grpc-status", "3"), ("grpc-message", "bad%20input")))
    server.close_connection()
    # h2 rejects frames after GOAWAY, the response must survive anyway
    client.feed(server.data_to_send() + _raw_ping())
    assert client.done
    with pytest.raises(grapec.RpcError) as info:
        client.result()
    assert info.value.code is grapec.Status.INVALID_ARGUMENT
    assert info.value.message == "bad input"
    assert not client.healthy


def test_goaway_during_call_is_a_transport_error():
    client, server = _pair()
    _start_call(client, server)
    server.close_connection()
    with pytest.raises(grapec.TransportError, match="connection terminated by peer"):
        client.feed(server.data_to_send())
    assert not client.healthy


def test_goaway_while_idle_marks_dead_without_raising():
    client, server = _pair()
    server.close_connection()
    client.feed_idle(server.data_to_send() + _raw_ping())
    assert not client.healthy
    client.feed_idle(b"")  # EOF is quiet as well


def test_cancel_keeps_the_connection():
    client, server = _pair()
    sid = _start_call(client, server)
    out = client.cancel()
    events = server.receive_data(out)
    assert any(isinstance(e, h2.events.StreamReset) and e.stream_id == sid for e in events)
    assert client.healthy and not client.busy
    # a late response for the cancelled stream is ignored, the next call works
    sid2 = _start_call(client, server)
    assert sid2 != sid
    _reply(server, sid2)
    client.feed(server.data_to_send())
    assert client.result()[0] == b"\x08\x02"


def test_invalid_status_details_do_not_raise():
    client, server = _pair()
    sid = _start_call(client, server)
    _reply(server, sid, trailers=(("grpc-status", "5"), ("grpc-status-details-bin", "!!not base64!!")))
    client.feed(server.data_to_send())
    with pytest.raises(grapec.RpcError) as info:
        client.result()
    assert info.value.code is grapec.Status.NOT_FOUND
    assert info.value.details == b""


def test_frames_split_across_reads():
    client, server = _pair()
    sid = _start_call(client, server)
    _reply(server, sid)
    data = server.data_to_send()
    for i in range(len(data)):
        client.feed(data[i : i + 1])
    assert client.done
    assert client.result()[0] == b"\x08\x02"
