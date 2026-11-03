"""Rules, compact action encoding, and wire bounds for Open Word Tiles."""
from collections import Counter
import unittest

from radio_arcade.games.scrabble import (EMPTY_RACK, OpenWordTilesGame, _lexicon,
                                         TILE_COUNTS, _tile_counts, make_exchange, make_placement)
from radio_arcade.wire import Kind, Packet, encode, encode_roster_state, encode_snapshot


class OpenWordTilesScenarios(unittest.TestCase):
    def setUp(self):
        self.game = OpenWordTilesGame()
        self.players = ("N0CALL", "W1ABC")
        self.state = self.game.new_state(self.players)

    def opening_word(self):
        for _ in range(500):
            state = self.game.new_state(self.players)
            actor = self.players[state["turn"]]
            rack = state["racks"][state["turn"]]
            available = Counter(chr(64 + tile) for tile in rack if tile not in (0, EMPTY_RACK))
            for word in sorted(w for w in _lexicon() if 2 <= len(w) <= 7):
                needed = Counter(word)
                if all(available[c] >= needed[c] for c in needed):
                    return state, actor, word
        self.fail("could not find a seed with a legal opening word")

    def test_opening_move_roundtrips_and_stays_within_radio_packet(self):
        state, actor, word = self.opening_word()
        start_x = 7 - (len(word) - 1) // 2
        start = f"{chr(65 + start_x)}8"
        action = make_placement(state, actor, start, "H", word)
        self.assertLessEqual(len(action), 6)
        next_state = self.game.apply_action(state, self.players, actor, action)
        self.assertEqual(next_state["board"][105 + start_x:105 + start_x + len(word)],
                         [ord(c) - 64 for c in word])
        encoded = self.game.encode_state(next_state)
        restored = self.game.decode_state(encoded)
        self.game.validate_roster(restored, self.players)
        self.assertEqual(restored["board"], next_state["board"])
        self.assertEqual(restored["racks"], next_state["racks"])
        self.assertEqual(restored["scores"], next_state["scores"])
        self.assertEqual(sum(_tile_counts(restored)), sum(_tile_counts(next_state)))
        start_payload = encode_roster_state(self.players, self.game.encode_state(state))
        start_frame = encode(Packet(3, Kind.START, 1, 0, 0, *self.players, start_payload))
        self.assertLessEqual(len(start_frame), 240)
        snapshot_payload = encode_snapshot(self.players, encoded, action)
        snapshot_frame = encode(Packet(3, Kind.SNAPSHOT, 1, 1, 1, *self.players, snapshot_payload))
        self.assertLessEqual(len(snapshot_frame), 240)

    def test_compact_moves_match_legacy_including_blanks(self):
        from radio_arcade.games.scrabble import _pack_place
        state = self.game.new_state(self.players)
        state["racks"] = [[3, 1, 20, 0, 5, 18, 19], [EMPTY_RACK] * 7]
        actor = self.players[state["turn"]]
        for word, blanks in (("CAT", 0), ("C{A}T", 2)):
            compact = make_placement(state, actor, "G8", "H", word)
            legacy = _pack_place(111, True, 3, 7, [3, 1, 20], blanks)
            self.assertLess(len(compact), len(legacy))
            self.assertEqual(self.game.apply_action(state, self.players, actor, compact),
                             self.game.apply_action(state, self.players, actor, legacy))
            for bad in (compact[:-1], compact + b"\x00", bytes((compact[0], compact[1], 255))):
                with self.assertRaises(ValueError):
                    self.game.apply_action(state, self.players, actor, bad)
        compact = make_exchange(state, actor, "CA")
        self.assertEqual(len(compact), 1)
        self.assertEqual(self.game.apply_action(state, self.players, actor, compact),
                         self.game.apply_action(state, self.players, actor, bytes((2, compact[0] & 127))))

    def test_snapshot_migration_and_sparse_size(self):
        import struct
        from radio_arcade.games.scrabble import _pack_bits
        state = self.state
        legacy = (bytes((1, state["turn"], 0, 0, 0))
                  + struct.pack("!Ihh", state["rng"], *state["scores"])
                  + bytes((0,)) + _pack_bits([tile for rack in state["racks"] for tile in rack], 5) + bytes(29))
        compact = self.game.encode_state(state)
        self.assertEqual(len(legacy), 52)
        self.assertEqual(len(compact), 20)
        self.assertEqual(self.game.decode_state(legacy), self.game.decode_state(compact))
        for bad in (compact[:-1], compact + b"\x00", compact[:1] + bytes((compact[1] | 128,)) + compact[2:]):
            with self.assertRaises(ValueError):
                self.game.decode_state(bad)

    def test_exchange_and_pass_are_deterministic_and_preserve_inventory(self):
        player = self.players[self.state["turn"]]
        first_tile = self.state["racks"][self.state["turn"]][0]
        exchange = make_exchange(self.state, player, "?" if first_tile == 0 else chr(64 + first_tile))
        exchanged = self.game.apply_action(self.state, self.players, player, exchange)
        self.assertEqual(sum(_tile_counts(exchanged)), 86)
        passer = self.players[exchanged["turn"]]
        for _ in range(4):
            active = self.players[exchanged["turn"]]
            exchanged = self.game.apply_action(exchanged, self.players, active, b"\x01")
        self.assertTrue(exchanged["finished"])

    def test_rejects_invalid_word_and_wrong_turn(self):
        state, actor, word = self.opening_word()
        action = make_placement(state, actor, "H8", "H", word)
        with self.assertRaisesRegex(ValueError, "not that player's turn"):
            self.game.apply_action(state, self.players, self.players[1 - state["turn"]], action)
        invalid = bytes((0, 112, 0x11, 0, 3, 1, 0))
        with self.assertRaises(ValueError):
            self.game.apply_action(state, self.players, actor, invalid)

    def test_dense_snapshot_fits_even_with_maximum_callsigns(self):
        players = ("ABCDEFGHIJ", "KLMNOPQRST")
        state = self.game.new_state(players)
        letters = [27 if tile == 0 else tile
                   for tile, count in enumerate(TILE_COUNTS) for _ in range(count)]
        state["board"] = letters + [0] * (225 - len(letters))
        state["racks"] = [[EMPTY_RACK] * 7, [EMPTY_RACK] * 7]
        state["scores"] = [1234, 567]
        state["finished"], state["passes"], state["winner"] = True, 0, 0
        state_bytes = self.game.encode_state(state)
        self.assertLessEqual(len(state_bytes), 127)
        snapshot = encode_snapshot(players, state_bytes, players[0], bytes(11))
        packet = encode(Packet(3, Kind.SNAPSHOT, 1, 1, 1, players[0], players[1], snapshot))
        self.assertLessEqual(len(packet), 240)


if __name__ == "__main__":
    unittest.main()
