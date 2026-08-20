"""Low level protobuf wire format primitives.

Only the pieces needed by the codec are implemented here. Nothing in this
module knows about Python classes or schemas.
"""

from __future__ import annotations

import struct as _struct

VARINT = 0
FIXED64 = 1
LENGTH = 2
FIXED32 = 5

_MASK64 = (1 << 64) - 1


class WireError(ValueError):
    """Raised when bytes cannot be decoded."""


def encode_varint(value: int) -> bytes:
    """Encode a Python int as a protobuf varint.

    Negative numbers are encoded as their 64 bit two's complement, which is
    exactly what protobuf does for int32 / int64 / enum.
    """
    value &= _MASK64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def decode_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise WireError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            # the tenth byte may carry bits above 64, protobuf discards them
            return result & _MASK64, pos
        shift += 7
        if shift > 63:
            raise WireError("varint too long")


def to_signed64(value: int) -> int:
    if value >= 1 << 63:
        value -= 1 << 64
    return value


def encode_tag(number: int, wire_type: int) -> bytes:
    return encode_varint((number << 3) | wire_type)


def decode_tag(buf: bytes, pos: int) -> tuple[int, int, int]:
    key, pos = decode_varint(buf, pos)
    number = key >> 3
    if number == 0:
        raise WireError("invalid field number 0")
    return number, key & 0x7, pos


def encode_double(value: float) -> bytes:
    return _struct.pack("<d", value)


def decode_double(buf: bytes, pos: int) -> tuple[float, int]:
    if pos + 8 > len(buf):
        raise WireError("truncated fixed64")
    return _struct.unpack_from("<d", buf, pos)[0], pos + 8


def encode_bytes(value: bytes) -> bytes:
    return encode_varint(len(value)) + value


def decode_bytes(buf: bytes, pos: int) -> tuple[bytes, int]:
    length, pos = decode_varint(buf, pos)
    end = pos + length
    if end > len(buf):
        raise WireError("truncated length delimited field")
    return bytes(buf[pos:end]), end


def skip_field(buf: bytes, pos: int, wire_type: int) -> int:
    if wire_type == VARINT:
        _, pos = decode_varint(buf, pos)
    elif wire_type == FIXED64:
        pos += 8
    elif wire_type == LENGTH:
        _, pos = decode_bytes(buf, pos)
    elif wire_type == FIXED32:
        pos += 4
    else:
        raise WireError(f"unsupported wire type {wire_type}")
    if pos > len(buf):
        raise WireError("truncated field")
    return pos
