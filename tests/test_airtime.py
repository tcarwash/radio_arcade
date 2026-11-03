"""Developer estimates should match Radio Arcade's real wire encoding."""

import unittest

from radio_arcade.airtime import estimate_action
from radio_arcade.wire import Kind, Packet, encode, encode_action


class AirtimeTests(unittest.TestCase):
    def test_exact_size_matches_actual_wire_codec(self):
        for count in (0, 1, 2, 12, 200):
            with self.subTest(payload_bytes=count):
                action = encode_action("AG7SU", bytes(count))
                wire = encode(Packet(1, Kind.ACTION, 0, 0, 0, "AG7SU", "K7ABC", action))
                result = estimate_action(count, source="AG7SU", destination="K7ABC")
                self.assertEqual(result.radio_arcade_frame_bytes, len(wire))
                self.assertEqual(result.radio_arcade_overhead_bytes, len(wire) - count)
                self.assertIsNone(result.estimated_airtime_ms)

    def test_rate_bps_with_preamble_and_repeats(self):
        result = estimate_action(2, source="AG7SU", destination="K7ABC",
                                 rate_bps=1200, modem_overhead_bytes=10,
                                 preamble_ms=300, transmissions=2)
        self.assertEqual(result.radio_arcade_frame_bytes, 37)
        self.assertAlmostEqual(result.estimated_airtime_ms, 2 * (47 * 8 / 1200 * 1000 + 300))
        self.assertIsNone(result.estimated_symbols)

    def test_symbol_rate_8fsk_half_rate_fec(self):
        result = estimate_action(2, source="AG7SU", destination="K7ABC",
                                 baud=600, bits_per_symbol=3, code_rate=0.5)
        self.assertEqual(result.effective_rate_bps, 900)
        self.assertEqual(result.estimated_symbols, 198)  # 37 * 8 / (3 * 0.5)
        self.assertAlmostEqual(result.estimated_airtime_ms, 330)
        self.assertAlmostEqual(result.estimated_airtime_s, 0.33)

    def test_baud_defaults_to_uncoded_and_rounds_whole_symbols(self):
        result = estimate_action(1, source="AG7SU", destination="K7ABC",
                                 baud=600, bits_per_symbol=3)
        self.assertEqual(result.effective_rate_bps, 1800)
        self.assertEqual(result.estimated_symbols, 96)  # 36 * 8 / 3
        self.assertAlmostEqual(result.estimated_airtime_ms, 160)

    def test_hosted_broadcast_commit_has_different_size(self):
        action = estimate_action(1, source="K7ABC", destination="AG7SU")
        commit = estimate_action(1, source="AG7SU", destination="*",
                                 actor="K7ABC", kind=Kind.COMMIT)
        self.assertEqual(action.radio_arcade_frame_bytes, 36)
        self.assertEqual(commit.radio_arcade_frame_bytes, 32)

    def test_validation_and_maximum_frame(self):
        kwargs = dict(source="AG7SU", destination="K7ABC")
        for count in (-1, 201, 2.2, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                estimate_action(count, **kwargs)
        with self.assertRaises(ValueError):
            estimate_action(200, source="CALLSIGN10", destination="CALLSIGN10")
        for assumptions in (
            dict(rate_bps=0), dict(baud=600), dict(baud=600, bits_per_symbol=0),
            dict(baud=600, bits_per_symbol=3, code_rate=0),
            dict(baud=600, bits_per_symbol=3, code_rate=1.2),
            dict(baud=600, bits_per_symbol=3, rate_bps=1200),
            dict(bits_per_symbol=3), dict(code_rate=0.5),
            dict(preamble_ms=-1), dict(transmissions=0),
            dict(modem_overhead_bytes=-1),
        ):
            with self.subTest(assumptions=assumptions), self.assertRaises(ValueError):
                estimate_action(1, **kwargs, **assumptions)
