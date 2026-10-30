"""Regression checks for Scorched Earth radio-state and impact semantics."""

import unittest
from unittest.mock import patch

from radio_arcade.games.scorched_earth import ScorchedEarthGame, WEAPONS, BUY


class ScorchedEarthTests(unittest.TestCase):
    def setUp(self):
        self.game = ScorchedEarthGame()
        self.players = ("AG7SU", "N0CALL")
        self.state = self.game.new_state(self.players)

    def test_snapshot_round_trip(self):
        payload = self.game.encode_state(self.state)
        self.assertEqual(len(payload), 52 + 2 * len(WEAPONS))
        self.assertEqual(self.game.decode_state(payload), self.state)

    def test_previous_snapshot_loads_with_shop_defaults(self):
        old_payload = self.game.encode_state(self.state)[:50]
        restored = self.game.decode_state(old_payload)
        self.assertEqual(restored["credits"], [70, 70])
        self.assertEqual(restored["ammo"][0][0], 255)

    def test_cannot_purchase_without_credits(self):
        self.state["credits"][0] = 0
        with self.assertRaisesRegex(ValueError, "credits"):
            self.game.apply_action(self.state, self.players, self.players[0], bytes((BUY, 5)))

    def test_out_of_bounds_shot_has_no_blast_even_near_tank(self):
        # An edge exit can be within a weapon's blast radius of a tank.
        # It must not act as an invisible wall that explodes the projectile.
        tank_x = self.state["x"][1] * 1000
        tank_y = self.state["terrain"][self.state["x"][1]] * 1000 + 650
        with patch("radio_arcade.games.scorched_earth._trace_shot", return_value={
            "kind": "edge", "x": tank_x + 100, "y": tank_y, "path": []
        }):
            next_state = self.game.apply_action(self.state, self.players, self.players[0], bytes((1, 45)))
        self.assertEqual(next_state["hp"], [100, 100])
        self.assertEqual(next_state["last_damage"], [0, 0])
        self.assertEqual(next_state["terrain"], self.state["terrain"])

    def test_valid_actions_remain_two_bytes_and_serializable(self):
        for action in (bytes((0, 45)), bytes((0x80, 1)), bytes((0x82, 0))):
            with self.subTest(action=action):
                result = self.game.apply_action(self.state, self.players, self.players[0], action)
                self.assertEqual(len(self.game.encode_state(result)), 52 + 2 * len(WEAPONS))
                self.assertEqual(self.game.decode_state(self.game.encode_state(result)), result)

    def test_purchase_consumes_turn_and_credits(self):
        self.state["ammo"][0][5] = 0
        result = self.game.apply_action(self.state, self.players, self.players[0], bytes((BUY, 5)))
        self.assertEqual(result["credits"][0], 70 - WEAPONS[5]["cost"])
        self.assertEqual(result["ammo"][0][5], 1)
        self.assertEqual(result["turn"], 1)
        self.assertEqual(self.game.decode_state(self.game.encode_state(result)), result)

    def test_empty_ammunition_cannot_fire(self):
        self.state["ammo"][0][5] = 0
        with self.assertRaisesRegex(ValueError, "ammunition"):
            self.game.apply_action(self.state, self.players, self.players[0], bytes((5, 45)))

    def test_cannon_is_unlimited_and_earns_credits(self):
        result = self.game.apply_action(self.state, self.players, self.players[0], bytes((0, 45)))
        self.assertEqual(result["ammo"][0][0], 255)
        self.assertGreater(result["credits"][0], self.state["credits"][0])

    def test_invalid_firing_angle_rejected(self):
        for angle in (0, 9, 81, 255):
            with self.subTest(angle=angle), self.assertRaises(ValueError):
                self.game.apply_action(self.state, self.players, self.players[0], bytes((0, angle)))


if __name__ == "__main__":
    unittest.main()
