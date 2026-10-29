"""Checks for the versioned envelope and opaque-state marker."""
import unittest

from radio_arcade.wire import FLAG_OPAQUE, Kind, Packet, decode, encode


class WireScenarios(unittest.TestCase):
    def test_compact_envelope_and_round_trip(self):
        packet = Packet(1, Kind.ACTION, 0x0102030405060708, 0x1234, 0x5678,
                        "N0AAA", "N0BBB", b"\x04")
        frame = encode(packet)
        self.assertEqual(frame[:18], bytes.fromhex("010404000102030405060708123456780505"))
        self.assertEqual(decode(frame), packet)
        self.assertEqual(len(frame), 29)

    def test_opaque_flag_is_required_and_exclusive(self):
        opaque = Packet(1, Kind.OPAQUE_STATE, 1, 1, 0, "A", "B", b"phase=1", FLAG_OPAQUE)
        self.assertEqual(decode(encode(opaque)).payload, b"phase=1")
        with self.assertRaises(ValueError):
            encode(Packet(1, Kind.OPAQUE_STATE, 1, 1, 0, "A", "B", b"phase=1"))
        with self.assertRaises(ValueError):
            encode(Packet(1, Kind.CHAT, 1, 1, 0, "A", "B", b"hi", FLAG_OPAQUE))
        old = bytearray(encode(opaque))
        old[1] = 3
        with self.assertRaisesRegex(ValueError, "version 3"):
            decode(bytes(old))
