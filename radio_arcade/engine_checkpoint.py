"""Reference codec for external-engine application checkpoints.

LÖVE and Godot implement the same tiny, visible-on-air RA: protocol. Their
game runtimes own serialization; Radio Arcade's core state snapshot cannot
reconstruct arbitrary game objects after an interrupted action log.
"""

from __future__ import annotations

import base64
import binascii
from math import ceil
import re


MAX_SNAPSHOT_BYTES = 16_384
SNAPSHOT_CHUNK_BYTES = 90
MAX_CHUNKS = ceil(MAX_SNAPSHOT_BYTES / SNAPSHOT_CHUNK_BYTES)
_NONCE = re.compile(r"^[0-9a-f]{8}$")


def fnv1a(data: bytes) -> int:
    result = 2166136261
    for byte in data:
        result = ((result ^ byte) * 16777619) & 0xFFFFFFFF
    return result


def checkpoint_request(nonce: str) -> str:
    if not _NONCE.fullmatch(nonce):
        raise ValueError("checkpoint nonce must be eight lowercase hex digits")
    return f"RA:REQ:{nonce}"


def checkpoint_chunks(nonce: str, revision: int, data: bytes) -> list[str]:
    checkpoint_request(nonce)
    if not isinstance(data, bytes) or len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("checkpoint must contain at most 16 KiB")
    if type(revision) is not int or not 0 <= revision <= 65535:
        raise ValueError("invalid checkpoint revision")
    pieces = [data[i:i + SNAPSHOT_CHUNK_BYTES]
              for i in range(0, len(data), SNAPSHOT_CHUNK_BYTES)] or [b""]
    checksum = fnv1a(data)
    result = [f"RA:S:{nonce}:{revision}:{index}:{len(pieces)}:{checksum}:"
              + base64.b64encode(piece).decode("ascii")
              for index, piece in enumerate(pieces)]
    if any(len(item.encode("ascii")) > 200 for item in result):
        raise AssertionError("checkpoint header exceeds 200-byte side-message limit")
    return result


class CheckpointAssembler:
    """Out-of-order, duplicate-tolerant reassembly with a bound on RF input."""

    def __init__(self, nonce: str):
        checkpoint_request(nonce)
        self.nonce = nonce
        self.parts: dict[int, bytes] = {}
        self.key: tuple[int, int, int] | None = None

    def accept(self, message: str) -> tuple[int, bytes] | None:
        fields = message.split(":", 7)
        if len(fields) != 8 or fields[:2] != ["RA", "S"] or fields[2] != self.nonce:
            return None
        try:
            revision, index, count, checksum = map(int, fields[3:7])
            if (not 0 <= revision <= 65535 or not 1 <= count <= MAX_CHUNKS
                    or not 0 <= index < count or not 0 <= checksum <= 0xFFFFFFFF):
                return None
            data = base64.b64decode(fields[7], validate=True)
        except (ValueError, binascii.Error):
            return None
        if (len(data) > SNAPSHOT_CHUNK_BYTES or
                base64.b64encode(data).decode("ascii") != fields[7]):
            return None
        key = (revision, count, checksum)
        if self.key is None:
            self.key = key
        if self.key != key or (index in self.parts and self.parts[index] != data):
            return None
        self.parts[index] = data
        if len(self.parts) != count:
            return None
        combined = b"".join(self.parts[i] for i in range(count))
        if len(combined) > MAX_SNAPSHOT_BYTES or fnv1a(combined) != checksum:
            return None
        return revision, combined
