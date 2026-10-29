"""Connect Four rule, codec, and catalog regressions."""
import unittest

from radio_arcade.games.base import builtin_registry
from radio_arcade.games.connect_four import ConnectFourGame


class ConnectFourTests(unittest.TestCase):
    def setUp(self):
        self.game = ConnectFourGame()
        self.players = ("AG7SU", "W7XYZ")
        self.state = self.game.new_state(self.players)

    def drop(self, col):
        actor = self.players[self.state["turn"]]
        self.state = self.game.apply_action(self.state, self.players, actor, bytes((col,)))
        self.assertEqual(self.game.decode_state(self.game.encode_state(self.state)), self.state)

    def test_registry_and_snapshot(self):
        self.assertEqual(builtin_registry(load_plugins=False).by_id(6).key, "connect-four")
        self.assertEqual(len(self.game.encode_state(self.state)), 13)
        self.drop(3)
        self.assertEqual(self.state["board"][38], "X")

    def test_horizontal_win(self):
        for col in (0, 0, 1, 1, 2, 2, 3):
            self.drop(col)
        self.assertEqual(self.state["winner"], 0)
        self.assertTrue(self.state["finished"])
        with self.assertRaises(ValueError):
            self.game.apply_action(self.state, self.players, "W7XYZ", b"\x04")

    def test_vertical_win(self):
        for col in (0, 1, 0, 1, 0, 1, 0):
            self.drop(col)
        self.assertEqual(self.state["winner"], 0)

    def test_diagonal_win(self):
        for col in (0, 1, 1, 2, 4, 2, 2, 3, 4, 3, 5, 3, 3):
            self.drop(col)
        self.assertEqual(self.state["winner"], 0)

    def test_full_column_and_turn(self):
        for col in (0, 0, 0, 0, 0, 0):
            self.drop(col)
        with self.assertRaisesRegex(ValueError, "full"):
            self.game.apply_action(self.state, self.players, "AG7SU", b"\x00")
        with self.assertRaises(ValueError):
            self.game.apply_action(self.state, self.players, "W7XYZ", b"\x01")
        for invalid in (b"", b"\x07", b"\x00\x01"):
            with self.assertRaises(ValueError):
                self.game.apply_action(self.state, self.players, "AG7SU", invalid)

    def test_invalid_snapshots(self):
        payload = self.game.encode_state(self.state)
        for bad in (payload[:-1], bytes([255]) + payload[1:], payload[:-2] + b"\x03\x00"):
            with self.assertRaises(ValueError):
                self.game.decode_state(bad)
        floating = (1 << (2 * 28)).to_bytes(11, "little") + b"\x01\x00"
        with self.assertRaisesRegex(ValueError, "floating"):
            self.game.decode_state(floating)

    def test_view(self):
        self.drop(3)
        view = self.game.view_model(self.state, self.players)
        self.assertEqual((view["width"], view["height"]), (7, 6))
        self.assertEqual(view["turn"], "W7XYZ")
        self.assertEqual(view["cells"][38], "X")


if __name__ == "__main__":
    unittest.main()
