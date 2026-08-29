"""Decode .swatchbin textures (Forza's "Grub bundle" container around a TXCB blob).
Ported from https://github.com/D3FEKT/ForzaTechStudio, thanks a lot to the original authors for the research and the reference implementation.
"""
from __future__ import annotations

import struct

import texture2ddecoder

BUNDLE_TAG = 0x47727562  # "Grub"
TAG_BLOB_TXCB = 0x54584342
TAG_METADATA_TXCH = 0x54584348

# encoding/transcoding -> (non-sRGB DXGI format, sRGB DXGI format); only the formats
# actually decodable below are listed.
ENCODING_TO_DXGI = {0: (71, 72), 2: (77, 78), 9: (98, 99), 22: (98, 99)}
TRANSCODING_TO_DXGI = {2: (71, 72), 4: (77, 78), 11: (98, 99)}

DECODERS = {
    71: texture2ddecoder.decode_bc1, 72: texture2ddecoder.decode_bc1,
    77: texture2ddecoder.decode_bc3, 78: texture2ddecoder.decode_bc3,
    98: texture2ddecoder.decode_bc7, 99: texture2ddecoder.decode_bc7,
}


class SwatchbinError(Exception):
    """The file isn't a swatchbin Neptune can decode — never worth crashing over."""


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from('<I', data, off)[0]


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from('<H', data, off)[0]


def _i32(data: bytes, off: int) -> int:
    return struct.unpack_from('<i', data, off)[0]


def decode(data: bytes) -> tuple[int, int, bytes]:
    """(width, height, BGRA8888 pixel bytes) for mip 0 of the first texture slice."""
    if len(data) < 6 or _u32(data, 0) != BUNDLE_TAG:
        raise SwatchbinError('not a Grub bundle')
    version_major = data[4]

    if version_major > 1 or (version_major == 1 and data[5] >= 1):
        blob_count = _u32(data, 16)
        blob_headers_start = 20
    else:
        blob_count = _u16(data, 6)
        blob_headers_start = 16

    txcb = None
    for i in range(blob_count):
        off = blob_headers_start + i * 0x18
        if off + 0x18 > len(data):
            break
        if _u32(data, off) == TAG_BLOB_TXCB:
            txcb = off
            break
    if txcb is None:
        raise SwatchbinError('no TXCB (texture) blob in this file')

    blob_version_major, blob_version_minor = data[txcb + 4], data[txcb + 5]
    if blob_version_major == 2 and blob_version_minor == 0:
        raise SwatchbinError('Xbox/Durango tiled format not supported')

    metadata_count = _u16(data, txcb + 6)
    metadata_offset = _u32(data, txcb + 8)
    data_offset = _u32(data, txcb + 12)
    uncompressed_size = _u32(data, txcb + 20)

    txch = None
    for i in range(metadata_count):
        meta_hdr = metadata_offset + i * 0x08
        if _u32(data, meta_hdr) == TAG_METADATA_TXCH:
            size = _u16(data, meta_hdr + 4) >> 4
            start = meta_hdr + _u16(data, meta_hdr + 6)
            txch = data[start:start + size]
            break
    if txch is None:
        raise SwatchbinError('no TXCH (texture header) metadata found')

    width, height = _u32(txch, 0x18), _u32(txch, 0x1C)
    num_slices = _u16(txch, 0x24) & 0x3FFF
    transcoding = _i32(txch, 0x28)
    target_color_profile = _i32(txch, 0x30)
    slices_offset = _u32(txch, 0x38)
    if slices_offset == 0 or num_slices == 0:
        raise SwatchbinError('texture has no slices')

    encoding = _i32(txch, slices_offset)
    mips_offset = _u32(txch, slices_offset + 4)
    mip0_size = _u32(txch, mips_offset)
    mip0_offset = _u32(txch, mips_offset + 4)

    srgb = target_color_profile != 0  # 0 = Rec709Linear
    pair = TRANSCODING_TO_DXGI.get(transcoding) if transcoding > 1 else \
        ENCODING_TO_DXGI.get(encoding)
    if not pair:
        raise SwatchbinError(f'unsupported encoding={encoding} transcoding={transcoding}')
    dxgi_format = pair[1] if srgb else pair[0]

    decoder = DECODERS.get(dxgi_format)
    if decoder is None:
        raise SwatchbinError(f'unsupported DXGI format {dxgi_format}')

    blob = data[data_offset:data_offset + uncompressed_size]
    pixels = blob[mip0_offset:mip0_offset + mip0_size]
    bgra = decoder(pixels, width, height)
    return width, height, bgra
