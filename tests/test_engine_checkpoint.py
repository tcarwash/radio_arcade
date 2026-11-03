"""Shared Python reference codec for native-engine checkpoint side traffic."""

import unittest

from radio_arcade.engine_checkpoint import (
    CheckpointAssembler, MAX_SNAPSHOT_BYTES, checkpoint_chunks,
    checkpoint_request, fnv1a,
)


class CheckpointCodecTests(unittest.TestCase):
    def test_empty_single_and_maximum_snapshot(self):
        for data in (b"", b"\x00\xff\x07", bytes(range(256)) * 64):
            chunks = checkpoint_chunks("abcdef12", 65535, data)
            self.assertTrue(all(len(chunk) <= 200 for chunk in chunks))
            receiver = CheckpointAssembler("abcdef12")
            recovered = None
            for message in reversed(chunks):
                recovered = receiver.accept(message) or recovered
            self.assertEqual(recovered, (65535, data))

    def test_rejects_corruption_oversize_and_wrong_nonce(self):
        with self.assertRaises(ValueError):
            checkpoint_chunks("notnonce", 1, b"a")
        with self.assertRaises(ValueError):
            checkpoint_chunks("deadbeef", 1, b"a" * (MAX_SNAPSHOT_BYTES + 1))
        self.assertEqual(checkpoint_request("deadbeef"), "RA:REQ:deadbeef")
        assembly = CheckpointAssembler("deadbeef")
        first = checkpoint_chunks("deadbeef", 2, b"hello")[0]
        self.assertIsNone(assembly.accept(first.replace("deadbeef", "badfeed0")))
        self.assertIsNone(assembly.accept(first.replace(":2:0:1:", ":2:99:1:")))
        self.assertIsNone(assembly.accept(first.replace("aGVsbG8=", "aGVsbG9=")))
        self.assertEqual(assembly.accept(first), (2, b"hello"))

    def test_fnv_checksum_is_stable(self):
        self.assertEqual(fnv1a(b"hello"), 0x4F9F2CAB)


if __name__ == "__main__":
    unittest.main()
