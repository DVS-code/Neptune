"""Pattern scanning over the game's main module."""
from __future__ import annotations

import itertools
import re

from neptune.memory.process import Process

CHUNK = 0x100000


class SignatureError(Exception):
    """A pattern could not be resolved on the running build."""


def compile_pattern(pattern: str) -> re.Pattern:
    tokens = pattern.split()
    if not tokens:
        raise SignatureError('This game build is not supported.')
    parts = []
    for token in tokens:
        if token in ('?', '??'):
            parts.append(b'.')
            continue
        try:
            parts.append(re.escape(bytes([int(token, 16)])))
        except ValueError as error:
            raise SignatureError('This game build is not supported.') from error
    return re.compile(b''.join(parts), re.DOTALL)


class ModuleImage:
    """A snapshot of the main module so many patterns share one read."""

    def __init__(self, base: int, data: bytes):
        self.base = base
        self.data = data

    @classmethod
    def capture(cls, process: Process, size: int = 0) -> ModuleImage:
        size = size or _image_size(process)
        chunks = []
        for offset in range(0, size, CHUNK):
            length = min(CHUNK, size - offset)
            block = process.read(process.base + offset, length)
            chunks.append(block if block else b'\x00' * length)
        return cls(process.base, b''.join(chunks))

    def find(self, pattern: str, occurrence: int = 0) -> int:
        compiled = compile_pattern(pattern)
        for index, match in enumerate(compiled.finditer(self.data)):
            if index == occurrence:
                return self.base + match.start()
        raise SignatureError('This game build is not supported yet. Check for a Neptune update.')

    def find_all(self, pattern: str, limit: int = 64) -> list[int]:
        compiled = compile_pattern(pattern)
        return [self.base + match.start()
                for match in itertools.islice(compiled.finditer(self.data), limit)]

    def find_optional(self, pattern: str) -> int | None:
        try:
            return self.find(pattern)
        except SignatureError:
            return None


def _image_size(process: Process) -> int:
    header = process.read(process.base, 0x40)
    if not header or header[:2] != b'MZ':
        raise SignatureError('Could not read the game. Try restarting it.')
    pe_offset = int.from_bytes(header[0x3C:0x40], 'little')
    optional = process.read(process.base + pe_offset + 0x18, 0x70)
    if not optional:
        raise SignatureError('Could not read the game. Try restarting it.')
    return int.from_bytes(optional[0x38:0x3C], 'little')
