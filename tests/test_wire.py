"""Checks for the versioned envelope and opaque-state marker."""
import unittest

from radio_arcade.wire import (FLAG_OPAQUE, CompactFrame, Kind, Packet, decode, encode,
                               decode_compact, encode_compact, migrate_legacy_full_frame)


class WireScenarios(unittest.TestCase):
    def test_compact_envelope_and_round_trip(self):
        packet = Packet(1, Kind.ACTION, 0x0102030405060708, 0x1234, 0x5678,
                        "N0AAA", "N0BBB", b"\x04")
        frame = encode(packet)
        self.assertEqual(frame[:17], bytes.fromhex("0106040001020304050607081234567855"))
        self.assertEqual(decode(frame), packet)
        self.assertEqual(len(frame), 28)

    def test_compact_turn_is_nine_bytes_and_ack_eight(self):
        sid = 0x0102030405060708
        raw = encode_compact(Kind.ACTION, sid, 0x1234, 3, b"\x07")
        self.assertEqual(raw, bytes.fromhex("000305060708123407"))
        self.assertEqual(len(raw), 9)
        self.assertEqual(decode(raw), CompactFrame(Kind.ACTION, 0x05060708, 0x1234, 3, b"\x07"))
        ack = encode_compact(Kind.ACK, sid, 0x1234, 0, acknowledged_kind=Kind.COMMIT)
        self.assertEqual(len(ack), 8)
        self.assertEqual(decode_compact(ack).acknowledged_kind, Kind.COMMIT)
        with self.assertRaises(ValueError):
            encode_compact(Kind.ACK, sid, 1, 0, b"\x00", acknowledged_kind=Kind.COMMIT)
        with self.assertRaises(ValueError):
            decode_compact(ack[:1] + bytes((0xF0,)) + ack[2:])

    def test_packed_callsign_lengths_allow_fifteen_characters(self):
        packet = Packet(1, Kind.INVITE, 1, 0, 0, "ABCDEFGHIJKLMNO", "P/AG7SU-TEST")
        self.assertEqual(decode(encode(packet)), packet)
        self.assertEqual(encode(packet)[16], 0xFC)

    def test_legacy_v5_outbox_frame_conversion(self):
        legacy = bytes.fromhex("010504000102030405060708123456780505") + b"N0AAAN0BBB\x04"
        upgraded = migrate_legacy_full_frame(legacy)
        self.assertEqual(decode(upgraded), Packet(1, Kind.ACTION, 0x0102030405060708,
                                                0x1234, 0x5678, "N0AAA", "N0BBB", b"\x04"))

    def test_forfeit_snapshot_uses_compact_outcome_trailer(self):
        from radio_arcade.wire import encode_snapshot, decode_snapshot, FORFEIT_ACTION
        players = ("N0AAA", "N0BBB")
        normal = encode_snapshot(players, b"opaque state")
        forfeited = encode_snapshot(players, b"opaque state", "N0BBB", FORFEIT_ACTION)
        self.assertEqual(len(forfeited), len(normal) + 1)
        self.assertEqual(decode_snapshot(forfeited), (players, b"opaque state", ("N0BBB", FORFEIT_ACTION)))
        with self.assertRaises(ValueError):
            decode_snapshot(forfeited[:-1] + b"\x02")

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
