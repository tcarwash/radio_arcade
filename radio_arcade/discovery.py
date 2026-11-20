"""Radio Arcade's versioned 63-bit Data2G Hand and game-preference format.

The 28-bit station encoding and 15-bit callsign hashes follow Ragtime's
published Hand representation. This is deliberately a distinct payload
version: a Ragtime receiver must not interpret Arcade preferences as its flags.

    version:3 | callsign:28 | heard S/N:6 | status:2 |
    game interest (IDs 1..16):16 | flags:8

All values are public. No app data is encrypted or hidden on amateur radio.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import hashlib
import struct

HAND_VERSION = 1  # Ragtime's format is 0; ours is intentionally different.
HAND_BITS = 63
CALL_HASH_BASE = (1 << 28) - (1 << 22)
STANDARD_CALLS = 37 * 36 * 10 * 27 * 27 * 27
HAND_DELAY_TENTHS = 30
ROLL_CHANNELS = (8, 20, 40)
MAX_ROUND = 65535


class Availability(IntEnum):
    NOT_LOOKING = 0
    LOOKING = 1
    IN_GAME = 2
    BUSY = 3


STATUS_LABELS = ("Not looking", "Looking to play", "In a game", "Busy")
STATUS_CHOICES = {"not_looking": Availability.NOT_LOOKING,
                  "looking": Availability.LOOKING,
                  "in_game": Availability.IN_GAME,
                  "busy": Availability.BUSY}


def pack_call(call: str) -> int:
    call = call.strip().upper()
    # Standard Ragtime call: [letter or digit][optional second][digit]
    # followed by 1-3 letters, total <= 6. The six positions are padded.
    import re
    match = re.fullmatch(r"([A-Z0-9]?)([A-Z0-9])([0-9])([A-Z]{1,3})", call)
    if match and len(call) <= 6:
        first, second, digit, suffix = match.groups()
        suffix = suffix.ljust(3, " ")
        def first_value(c): return " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ".index(c)
        def second_value(c): return "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ".index(c)
        def letter_value(c): return " ABCDEFGHIJKLMNOPQRSTUVWXYZ".index(c)
        result = first_value(first or " ")
        result = result * 36 + second_value(second)
        result = result * 10 + int(digit)
        for c in suffix:
            result = result * 27 + letter_value(c)
        return result
    if not call or len(call) > 15:
        raise ValueError("invalid callsign")
    digest = hashlib.sha256(call.encode("ascii")).digest()
    return CALL_HASH_BASE + (int.from_bytes(digest[:4], "big") >> 10)


def unpack_call(code: int, known: tuple[str, ...] = ()) -> str:
    if not 0 <= code < 1 << 28:
        raise ValueError("invalid packed callsign")
    if code >= CALL_HASH_BASE:
        for call in known:
            if pack_call(call) == code:
                return call.upper()
        return f"#{code - CALL_HASH_BASE:06X}"
    if code >= STANDARD_CALLS:
        raise ValueError("reserved callsign encoding")
    suffix = []
    for _ in range(3):
        suffix.append(" ABCDEFGHIJKLMNOPQRSTUVWXYZ"[code % 27])
        code //= 27
    digit = str(code % 10)
    code //= 10
    second = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[code % 36]
    code //= 36
    first = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[code]
    call = (first + second + digit + "".join(reversed(suffix))).strip()
    if not call or not any(c.isalpha() for c in call):
        raise ValueError("invalid standard callsign encoding")
    return call


def game_bits(game_ids) -> int:
    mask = 0
    for game_id in game_ids:
        if type(game_id) is not int or not 1 <= game_id <= 16:
            raise ValueError("Hand game interests require IDs 1..16")
        mask |= 1 << (game_id - 1)
    return mask


def interested(mask: int, game_id: int) -> bool:
    return 1 <= game_id <= 16 and bool(mask & (1 << (game_id - 1)))


def round_hash(call: str, round_number: int) -> bytes:
    if type(round_number) is not int or not 1 <= round_number <= MAX_ROUND:
        raise ValueError("invalid discovery round")
    return hashlib.sha256(call.upper().encode("ascii") + b"\0" + struct.pack("!H", round_number)).digest()


def round_tag(call: str, round_number: int) -> int:
    return round_hash(call, round_number)[4]


def response_channel(call: str, round_number: int, channels: int) -> int:
    if channels not in ROLL_CHANNELS:
        raise ValueError("invalid Hand channel count")
    return int.from_bytes(round_hash(call, round_number)[:4], "big") % channels


def encode_request(delay_tenths: int = HAND_DELAY_TENTHS, channels: int = 40) -> bytes:
    if type(delay_tenths) is not int or not 10 <= delay_tenths <= 99 or channels not in ROLL_CHANNELS:
        raise ValueError("invalid discovery window")
    return bytes((1, delay_tenths, channels))


def decode_request(data: bytes) -> tuple[int, int]:
    if len(data) != 3 or data[0] != 1:
        raise ValueError("unsupported discovery request")
    delay_tenths, channels = data[1:]
    encode_request(delay_tenths, channels)
    return delay_tenths, channels


def snr_code(snr: float | None) -> int:
    if snr is None:
        return 0
    return max(1, min(63, round(float(snr)) + 31))


def decode_snr(code: int) -> int | None:
    return None if code == 0 else code - 31


@dataclass(frozen=True)
class Hand:
    callsign: str
    status: Availability
    games: int
    snr: int | None = None
    flags: int = 0


def encode_hand(hand: Hand) -> int:
    if not isinstance(hand.status, Availability) or not 0 <= hand.games <= 65535 or not 0 <= hand.flags <= 255:
        raise ValueError("invalid discovery Hand")
    result = HAND_VERSION
    for width, value in ((28, pack_call(hand.callsign)), (6, snr_code(hand.snr)),
                         (2, int(hand.status)), (16, hand.games), (8, hand.flags)):
        result = (result << width) | value
    if result >= 1 << HAND_BITS:
        raise ValueError("Hand exceeds 63 bits")
    return result


def decode_hand(value: int, known: tuple[str, ...] = ()) -> Hand:
    if type(value) is not int or value < 0 or value >= 1 << HAND_BITS:
        raise ValueError("invalid Hand payload")
    flags = value & 255; value >>= 8
    games = value & 65535; value >>= 16
    status = Availability(value & 3); value >>= 2
    snr = decode_snr(value & 63); value >>= 6
    call = value & ((1 << 28) - 1); value >>= 28
    if value != HAND_VERSION or flags & ~0x01:
        raise ValueError("unsupported or invalid Arcade Hand")
    return Hand(unpack_call(call, known), status, games, snr, flags)
