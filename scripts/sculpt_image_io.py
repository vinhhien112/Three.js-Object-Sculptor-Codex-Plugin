"""Dependency-free image I/O shared by comparison and PBR extraction."""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
import zlib
from collections.abc import Iterable
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RGBA = tuple[int, int, int, int]
RGB = tuple[int, int, int]


def paeth_predictor(a: int, b: int, c: int) -> int:
    estimate = a + b - c
    distances = (abs(estimate - a), abs(estimate - b), abs(estimate - c))
    if distances[0] <= distances[1] and distances[0] <= distances[2]:
        return a
    return b if distances[1] <= distances[2] else c


def read_png(path: Path) -> tuple[int, int, list[RGBA]]:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG file")
    cursor = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
    while cursor + 8 <= len(data):
        length = struct.unpack(">I", data[cursor : cursor + 4])[0]
        chunk_type = data[cursor + 4 : cursor + 8]
        chunk_data = data[cursor + 8 : cursor + 8 + length]
        cursor += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
    if width is None or height is None or bit_depth != 8 or interlace != 0:
        raise ValueError("unsupported PNG; expected 8-bit non-interlaced image")
    channels_by_type = {0: 1, 2: 3, 4: 2, 6: 4}
    if color_type not in channels_by_type:
        raise ValueError("unsupported PNG color type; convert to RGB/RGBA first")
    channels = channels_by_type[color_type]
    row_bytes = width * channels
    raw = zlib.decompress(bytes(idat))
    expected_minimum = height * (row_bytes + 1)
    if len(raw) < expected_minimum:
        raise ValueError("truncated PNG scanline data")
    rows: list[bytearray] = []
    offset = 0
    previous = bytearray(row_bytes)
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset : offset + row_bytes])
        offset += row_bytes
        for index in range(row_bytes):
            left = row[index - channels] if index >= channels else 0
            up = previous[index]
            up_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                row[index] = (row[index] + left) & 0xFF
            elif filter_type == 2:
                row[index] = (row[index] + up) & 0xFF
            elif filter_type == 3:
                row[index] = (row[index] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                row[index] = (row[index] + paeth_predictor(left, up, up_left)) & 0xFF
            elif filter_type != 0:
                raise ValueError(f"unsupported PNG filter {filter_type}")
        rows.append(row)
        previous = row
    pixels: list[RGBA] = []
    for row in rows:
        for x in range(width):
            base = x * channels
            if color_type == 0:
                gray = row[base]
                pixels.append((gray, gray, gray, 255))
            elif color_type == 2:
                pixels.append((row[base], row[base + 1], row[base + 2], 255))
            elif color_type == 4:
                gray = row[base]
                pixels.append((gray, gray, gray, row[base + 1]))
            else:
                pixels.append((row[base], row[base + 1], row[base + 2], row[base + 3]))
    return width, height, pixels


def png_dimensions(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or not header.startswith(PNG_SIGNATURE):
        return None
    return struct.unpack(">II", header[16:24])


def _rgb_bytes(width: int, height: int, pixels: bytes | bytearray | Iterable[RGB]) -> bytes:
    if isinstance(pixels, (bytes, bytearray)):
        payload = bytes(pixels)
    else:
        flat = bytearray()
        for pixel in pixels:
            if len(pixel) != 3 or any(
                not isinstance(channel, int) or isinstance(channel, bool) or not 0 <= channel <= 255
                for channel in pixel
            ):
                raise ValueError("RGB channels must be integers from 0 to 255")
            flat.extend(pixel)
        payload = bytes(flat)
    if len(payload) != width * height * 3:
        raise ValueError("RGB payload has the wrong size")
    return payload


def write_png_rgb(
    path: Path,
    width: int,
    height: int,
    pixels: bytes | bytearray | Iterable[RGB],
) -> None:
    rgb = _rgb_bytes(width, height, pixels)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    scanlines = bytearray()
    stride = width * 3
    for y in range(height):
        scanlines.append(0)
        scanlines.extend(rgb[y * stride : (y + 1) * stride])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6))
        + chunk(b"IEND", b"")
    )


def _sips_png(path: Path, max_dimension: int | None) -> tuple[int, int, list[RGBA]]:
    executable = shutil.which("sips")
    if not executable:
        raise ValueError("macOS sips is unavailable")
    with tempfile.TemporaryDirectory() as directory:
        converted = Path(directory) / "converted.png"
        command = [executable]
        if max_dimension is not None:
            command.extend(["-Z", str(max_dimension)])
        command.extend(["-s", "format", "png", str(path), "--out", str(converted)])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0 or not converted.is_file():
            raise ValueError(result.stderr.strip() or result.stdout.strip() or "sips conversion failed")
        return read_png(converted)


def load_image_rgba(path: Path) -> tuple[int, int, list[RGBA]]:
    try:
        return read_png(path)
    except Exception as direct_error:
        try:
            return _sips_png(path, None)
        except ValueError as conversion_error:
            raise ValueError(
                f"could not decode {path.name} as PNG or convert it: {conversion_error}"
            ) from direct_error


def load_image_rgba_limited(
    path: Path,
    max_dimension: int | None,
) -> tuple[int, int, list[RGBA], list[str]]:
    if max_dimension is not None and shutil.which("sips"):
        width, height, pixels = _sips_png(path, max_dimension)
        return width, height, pixels, [
            f"source was decoded at a maximum working dimension of {max_dimension}px to limit memory"
        ]
    try:
        width, height, pixels = read_png(path)
        return width, height, pixels, []
    except Exception as direct_error:
        try:
            width, height, pixels = _sips_png(path, None)
        except ValueError as conversion_error:
            raise ValueError(
                f"could not decode {path.name} as PNG or convert it: {conversion_error}"
            ) from direct_error
        return width, height, pixels, [
            "source image was converted to PNG with macOS sips before pixel extraction"
        ]
