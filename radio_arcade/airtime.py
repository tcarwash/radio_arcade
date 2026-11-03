"""Developer-only Radio Arcade frame-size and approximate RF airtime estimates.

The application frame size is exact. Time estimates are hypothetical: Data2G
FEC/interleaving, pilots, preambles, rate changes, and retries are not known
from a frame alone. KISS TCP delimiters are not transmitted as RF data.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite

from .wire import Kind, Packet, encode, encode_action


@dataclass(frozen=True)
class AirtimeEstimate:
    """One ACTION/COMMIT; estimated time includes the requested repetitions."""

    payload_bytes: int
    radio_arcade_frame_bytes: int
    radio_arcade_overhead_bytes: int
    effective_rate_bps: float | None
    estimated_symbols: int | None
    estimated_airtime_ms: float | None
    transmissions: int

    @property
    def estimated_airtime_s(self) -> float | None:
        return None if self.estimated_airtime_ms is None else self.estimated_airtime_ms / 1000


def estimate_action(
    payload_bytes: int,
    *,
    source: str,
    destination: str,
    actor: str | None = None,
    kind: Kind = Kind.ACTION,
    game_id: int = 1,
    rate_bps: float | None = None,
    baud: float | None = None,
    bits_per_symbol: int | None = None,
    code_rate: float | None = None,
    modem_overhead_bytes: int = 0,
    preamble_ms: float = 0,
    transmissions: int = 1,
) -> AirtimeEstimate:
    """Calculate the exact app frame size and optionally rough transmit time.

    Supply either ``rate_bps`` (assumed effective bit rate) or both ``baud``
    (symbols/second) and ``bits_per_symbol`` (e.g., 3 for 8-FSK). For baud,
    ``code_rate`` defaults to 1 (uncoded); specify e.g. 0.5 for rate-1/2 FEC.
    The baud calculation rounds up to whole symbols per transmission.

    Additional modem bytes and a preamble duration can be estimated per
    transmission. This is not a Data2G airtime measurement and does not
    include dynamic mode changes, scheduling, turnarounds, or extra ACKs.
    """
    if type(payload_bytes) is not int or not 0 <= payload_bytes <= 200:
        raise ValueError("payload_bytes must be an integer from 0 to 200")
    if kind not in (Kind.ACTION, Kind.COMMIT):
        raise ValueError("kind must be ACTION or COMMIT")
    if type(modem_overhead_bytes) is not int or modem_overhead_bytes < 0:
        raise ValueError("modem_overhead_bytes must be a nonnegative integer")
    if type(transmissions) is not int or transmissions < 1:
        raise ValueError("transmissions must be a positive integer")
    if not isinstance(preamble_ms, (int, float)) or not isfinite(preamble_ms) or preamble_ms < 0:
        raise ValueError("preamble_ms must be finite and nonnegative")

    if baud is not None:
        if rate_bps is not None:
            raise ValueError("choose baud or rate_bps, not both")
        if not isinstance(baud, (int, float)) or not isfinite(baud) or baud <= 0:
            raise ValueError("baud must be finite and positive")
        if type(bits_per_symbol) is not int or bits_per_symbol < 1:
            raise ValueError("bits_per_symbol must be a positive integer with baud")
        if code_rate is None:
            code_rate = 1.0
        if not isinstance(code_rate, (int, float)) or not isfinite(code_rate) or not 0 < code_rate <= 1:
            raise ValueError("code_rate must be > 0 and <= 1")
        effective_rate_bps = baud * bits_per_symbol * code_rate
    else:
        if bits_per_symbol is not None or code_rate is not None:
            raise ValueError("bits_per_symbol and code_rate require baud")
        if rate_bps is not None and (
            not isinstance(rate_bps, (int, float)) or not isfinite(rate_bps) or rate_bps <= 0
        ):
            raise ValueError("rate_bps must be finite and positive")
        effective_rate_bps = rate_bps

    actor = actor or source
    wrapped_action = encode_action(actor, bytes(payload_bytes))
    packet = Packet(game_id, kind, 0, 0, 0, source, destination, wrapped_action)
    frame_bytes = len(encode(packet))  # Real encoder enforces the 240-byte limit.
    estimated_symbols = None
    estimated_ms = None
    if effective_rate_bps is not None:
        modem_bytes = frame_bytes + modem_overhead_bytes
        if baud is not None:
            estimated_symbols = ceil(modem_bytes * 8 / (bits_per_symbol * code_rate))
            duration_ms = estimated_symbols / baud * 1000
        else:
            duration_ms = modem_bytes * 8 / effective_rate_bps * 1000
        estimated_ms = transmissions * (duration_ms + preamble_ms)

    return AirtimeEstimate(
        payload_bytes=payload_bytes,
        radio_arcade_frame_bytes=frame_bytes,
        radio_arcade_overhead_bytes=frame_bytes - payload_bytes,
        effective_rate_bps=effective_rate_bps,
        estimated_symbols=estimated_symbols,
        estimated_airtime_ms=estimated_ms,
        transmissions=transmissions,
    )
