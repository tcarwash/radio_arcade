"""Versioned compact envelope shared by games and transports.

Layout: game (u8), envelope version (u8), kind (u8), flags (u8), instance ID
(u64), sequence (u16), revision (u16), source/destination lengths (u8 each),
callsigns, then plugin-specific payload. Multi-byte integers use network byte order.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct

PROTOCOL_VERSION = 4
TICTACTOE = 1
MAX_PACKET = 240
_MAX_CALL = 10
FLAG_OPAQUE = 0x01
KNOWN_FLAGS = FLAG_OPAQUE
_HEADER = struct.Struct("!BBBBQHHBB")


class Kind(IntEnum):
    INVITE = 1
    ACCEPT = 2
    START = 3
    ACTION = 4
    COMMIT = 5
    SYNC_REQUEST = 6
    SNAPSHOT = 7
    REJECT = 8
    RESIGN = 9
    CHAT = 10
    ACK = 11
    OPAQUE_STATE = 12
    CANCEL_INVITE = 13


@dataclass(frozen=True)
class Packet:
    game: int
    kind: Kind
    session: int
    sequence: int
    revision: int
    source: str
    destination: str
    payload: bytes = b""
    flags: int = 0


def encode(packet: Packet) -> bytes:
    source = packet.source.upper().encode("ascii")
    destination = packet.destination.upper().encode("ascii")
    if not 1 <= packet.game <= 255 or not 0 <= packet.session < 1 << 64:
        raise ValueError("game and instance IDs are out of range")
    if not 0 <= packet.sequence <= 65535 or not 0 <= packet.revision <= 65535:
        raise ValueError("sequence and revision must fit in 16 bits")
    if not 1 <= len(source) <= _MAX_CALL or not 1 <= len(destination) <= _MAX_CALL:
        raise ValueError("callsigns must be 1 to 10 ASCII bytes")
    if packet.flags & ~KNOWN_FLAGS:
        raise ValueError("unknown packet flag")
    if bool(packet.flags & FLAG_OPAQUE) != (packet.kind == Kind.OPAQUE_STATE):
        raise ValueError("opaque flag is required only for opaque game state packets")
    result = _HEADER.pack(packet.game, PROTOCOL_VERSION, int(packet.kind), packet.flags, packet.session,
                          packet.sequence, packet.revision, len(source), len(destination))
    result += source + destination + packet.payload
    if len(result) > MAX_PACKET:
        raise ValueError("game packet exceeds the 240 byte limit")
    return result


def decode(frame: bytes) -> Packet:
    if len(frame) < _HEADER.size + 2 or len(frame) > MAX_PACKET:
        raise ValueError("invalid game packet length")
    game, version, kind, flags, session, sequence, revision, src_len, dst_len = _HEADER.unpack_from(frame)
    if version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported Radio Arcade protocol version {version}")
    if flags & ~KNOWN_FLAGS or bool(flags & FLAG_OPAQUE) != (kind == int(Kind.OPAQUE_STATE)):
        raise ValueError("invalid packet flags")
    if not 1 <= src_len <= _MAX_CALL or not 1 <= dst_len <= _MAX_CALL:
        raise ValueError("invalid callsign length")
    end_source, end_destination = _HEADER.size + src_len, _HEADER.size + src_len + dst_len
    if len(frame) < end_destination:
        raise ValueError("truncated game packet")
    try:
        source = frame[_HEADER.size:end_source].decode("ascii").upper()
        destination = frame[end_source:end_destination].decode("ascii").upper()
        message_kind = Kind(kind)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid callsign or packet kind") from exc
    return Packet(game, message_kind, session, sequence, revision, source, destination,
                  frame[end_destination:], flags)


def encode_roster(players: tuple[str, ...]) -> bytes:
    if not 1 <= len(players) <= 255:
        raise ValueError("invalid player roster")
    result = bytearray((len(players),))
    for player in players:
        call = player.upper().encode("ascii")
        if not 1 <= len(call) <= _MAX_CALL:
            raise ValueError("callsigns must be 1 to 10 ASCII bytes")
        result.append(len(call))
        result.extend(call)
    return bytes(result)


def decode_roster(payload: bytes) -> tuple[str, ...]:
    if not payload:
        raise ValueError("empty roster")
    count, offset, result = payload[0], 1, []
    if not count:
        raise ValueError("empty roster")
    for _ in range(count):
        if offset >= len(payload):
            raise ValueError("truncated roster")
        size = payload[offset]
        offset += 1
        if not 1 <= size <= _MAX_CALL or offset + size > len(payload):
            raise ValueError("invalid roster callsign")
        try:
            result.append(payload[offset:offset + size].decode("ascii").upper())
        except UnicodeDecodeError as exc:
            raise ValueError("roster callsigns must be ASCII") from exc
        offset += size
    if offset != len(payload) or len(set(result)) != len(result):
        raise ValueError("invalid or duplicate roster entries")
    return tuple(result)


def encode_roster_state(players: tuple[str, ...], state: bytes) -> bytes:
    roster = encode_roster(players)
    if len(roster) > 255 or len(state) > 255:
        raise ValueError("roster too large")
    return bytes((len(roster),)) + roster + bytes((len(state),)) + state


def decode_roster_state(payload: bytes) -> tuple[tuple[str, ...], bytes]:
    if not payload or payload[0] == 0 or len(payload) < 1 + payload[0]:
        raise ValueError("invalid roster snapshot")
    boundary = 1 + payload[0]
    if len(payload) <= boundary:
        raise ValueError("missing game-state length")
    size = payload[boundary]
    if len(payload) != boundary + 1 + size:
        raise ValueError("invalid game-state length")
    return decode_roster(payload[1:boundary]), payload[boundary + 1:]


def encode_snapshot(players: tuple[str, ...], state: bytes,
                    last_actor: str | None = None, last_action: bytes | None = None) -> bytes:
    trailer = encode_action(last_actor, last_action) if last_actor and last_action is not None else b""
    if len(trailer) > 255:
        raise ValueError("snapshot trailer too large")
    return encode_roster_state(players, state) + bytes((len(trailer),)) + trailer


def decode_snapshot(payload: bytes) -> tuple[tuple[str, ...], bytes, tuple[str, bytes] | None]:
    if len(payload) < 2 or payload[0] == 0:
        raise ValueError("invalid full snapshot")
    roster_end = 1 + payload[0]
    if len(payload) <= roster_end + 1:
        raise ValueError("truncated full snapshot")
    state_size = payload[roster_end]
    trailer_len_offset = roster_end + 1 + state_size
    if len(payload) <= trailer_len_offset:
        raise ValueError("missing snapshot trailer")
    trailer_size = payload[trailer_len_offset]
    if len(payload) != trailer_len_offset + 1 + trailer_size:
        raise ValueError("invalid snapshot trailer length")
    players = decode_roster(payload[1:roster_end])
    state = payload[roster_end + 1:trailer_len_offset]
    trailer = payload[trailer_len_offset + 1:]
    return players, state, decode_action(trailer) if trailer else None


def encode_action(actor: str, action: bytes) -> bytes:
    call = actor.upper().encode("ascii")
    if not 1 <= len(call) <= _MAX_CALL or len(action) > 200:
        raise ValueError("invalid action envelope")
    return bytes((len(call),)) + call + bytes((len(action),)) + action


def decode_action(payload: bytes) -> tuple[str, bytes]:
    if len(payload) < 2:
        raise ValueError("invalid action envelope")
    n = payload[0]
    if not 1 <= n <= _MAX_CALL or len(payload) < n + 2:
        raise ValueError("invalid action sender")
    length = payload[n + 1]
    if len(payload) != n + 2 + length:
        raise ValueError("invalid action length")
    try:
        actor = payload[1:n + 1].decode("ascii").upper()
    except UnicodeDecodeError as exc:
        raise ValueError("action sender must be ASCII") from exc
    return actor, payload[n + 2:]
