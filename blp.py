#!/usr/bin/env python3
"""Minimal BLP2 decoder and PNG writer.

Pillow would normally cover this, but its compiled extension has no wheel for
the Python running here (3.15 alpha), so the two formats are implemented
directly.  Only what WoW icons actually use is supported: BLP2 with palettised,
DXT1/3/5 or raw BGRA content, top mip level only.
"""
from __future__ import annotations

import struct
import zlib

BLP_MAGIC = b"BLP2"


class BlpError(ValueError):
    pass


def _unpack565(value: int) -> tuple[int, int, int]:
    r = (value >> 11) & 0x1F
    g = (value >> 5) & 0x3F
    b = value & 0x1F
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _decode_dxt(data: bytes, width: int, height: int, flavour: int) -> bytearray:
    """Decode DXT1 (flavour 0), DXT3 (1) or DXT5 (7) into RGBA."""
    out = bytearray(width * height * 4)
    block_bytes = 8 if flavour == 0 else 16
    pos = 0
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            if pos + block_bytes > len(data):
                return out
            block = data[pos:pos + block_bytes]
            pos += block_bytes

            alpha = [255] * 16
            if flavour == 1:                                   # DXT3: 4-bit alpha
                for i in range(8):
                    byte = block[i]
                    alpha[i * 2] = (byte & 0x0F) * 17
                    alpha[i * 2 + 1] = (byte >> 4) * 17
                colour = block[8:]
            elif flavour == 7:                                 # DXT5: interpolated
                a0, a1 = block[0], block[1]
                bits = int.from_bytes(block[2:8], "little")
                table = [a0, a1]
                if a0 > a1:
                    table += [((7 - i) * a0 + (1 + i) * a1) // 7 for i in range(6)]
                else:
                    table += [((5 - i) * a0 + (1 + i) * a1) // 5 for i in range(4)]
                    table += [0, 255]
                for i in range(16):
                    alpha[i] = table[(bits >> (3 * i)) & 0x07]
                colour = block[8:]
            else:
                colour = block

            c0, c1 = struct.unpack("<HH", colour[:4])
            lookup = struct.unpack("<I", colour[4:8])[0]
            r0, g0, b0 = _unpack565(c0)
            r1, g1, b1 = _unpack565(c1)
            palette = [(r0, g0, b0, 255), (r1, g1, b1, 255)]
            if c0 > c1 or flavour != 0:
                palette.append(((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255))
                palette.append(((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255))
            else:                                              # 1-bit alpha variant
                palette.append(((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255))
                palette.append((0, 0, 0, 0))

            for i in range(16):
                x, y = bx + (i % 4), by + (i // 4)
                if x >= width or y >= height:
                    continue
                r, g, b, a = palette[(lookup >> (2 * i)) & 0x03]
                if flavour != 0:
                    a = alpha[i]
                o = (y * width + x) * 4
                out[o:o + 4] = bytes((r, g, b, a))
    return out


def decode(data: bytes) -> tuple[int, int, bytearray]:
    """Return (width, height, RGBA bytes) for the top mip level."""
    if data[:4] != BLP_MAGIC:
        raise BlpError(f"not a BLP2 file (magic {data[:4]!r})")
    kind, encoding, alpha_depth, alpha_encoding, _has_mips = struct.unpack(
        "<I4B", data[4:12])
    width, height = struct.unpack("<II", data[12:20])
    offsets = struct.unpack("<16I", data[20:84])
    sizes = struct.unpack("<16I", data[84:148])
    if kind != 1:
        raise BlpError(f"unsupported BLP content type {kind}")

    palette = data[148:148 + 1024]
    body = data[offsets[0]:offsets[0] + sizes[0]]

    if encoding == 1:                                          # palettised
        out = bytearray(width * height * 4)
        count = width * height
        for i in range(count):
            idx = body[i] if i < len(body) else 0
            b, g, r = palette[idx * 4], palette[idx * 4 + 1], palette[idx * 4 + 2]
            out[i * 4:i * 4 + 3] = bytes((r, g, b))
            out[i * 4 + 3] = 255
        if alpha_depth == 8:
            for i in range(count):
                pos = count + i
                out[i * 4 + 3] = body[pos] if pos < len(body) else 255
        elif alpha_depth == 1:
            for i in range(count):
                pos = count + (i >> 3)
                bit = (body[pos] >> (i & 7)) & 1 if pos < len(body) else 1
                out[i * 4 + 3] = 255 if bit else 0
        return width, height, out

    if encoding == 2:                                          # DXT
        return width, height, _decode_dxt(body, width, height, alpha_encoding)

    if encoding == 3:                                          # raw BGRA
        out = bytearray(width * height * 4)
        for i in range(width * height):
            b, g, r, a = body[i * 4:i * 4 + 4]
            out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
        return width, height, out

    raise BlpError(f"unsupported BLP encoding {encoding}")


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def write_png(path, width: int, height: int, rgba: bytes) -> None:
    """Write RGBA pixels as a PNG, without an imaging library."""
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)                                          # filter: none
        raw += rgba[y * stride:(y + 1) * stride]
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + _chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(png)
