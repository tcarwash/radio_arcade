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
        self.assertLessEqual(len(action), 11)
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
