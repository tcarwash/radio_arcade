"""Regression checks for Scorched Earth radio-state and impact semantics."""

import unittest
from unittest.mock import patch

from radio_arcade.games.scorched_earth import ScorchedEarthGame, WEAPONS, BUY, WIDTH, MAPS, _trace_shot, _wind


class ScorchedEarthTests(unittest.TestCase):
    def setUp(self):
        self.game = ScorchedEarthGame()
        self.players = ("AG7SU", "N0CALL")
        self.state = self.game.new_state(self.players)

    def test_desktop_and_web_are_supported(self):
        self.assertEqual(self.game.ui_clients, ("desktop", "web"))

    def test_snapshot_round_trip(self):
        payload = self.game.encode_state(self.state)
        self.assertLess(len(payload), 80)
        self.assertEqual(self.game.decode_state(payload), self.state)

    def test_previous_snapshot_loads_with_shop_defaults(self):
        old_payload = self.game.encode_state(self.state)
        old_payload = old_payload[:len(old_payload) - 2 - 2 * len(WEAPONS)]
        # New sparse snapshots always carry the armory, unlike legacy ones.
        with self.assertRaises(ValueError):
            self.game.decode_state(old_payload)
        legacy = self._classic_snapshot(with_shop=False)
        restored = self.game.decode_state(legacy)
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
                self.assertLess(len(self.game.encode_state(result)), 240)
                self.assertEqual(self.game.decode_state(self.game.encode_state(result)), result)

    def test_new_maps_are_wide_and_have_playable_long_range_shots(self):
        self.assertEqual(WIDTH, 128)
        self.assertTrue(all(len(terrain) == WIDTH for _, terrain in MAPS))
        self.assertTrue(all(terrain[:64] != tuple(reversed(terrain[64:])) for _, terrain in MAPS))
        self.assertGreaterEqual(self.state["x"][1] - self.state["x"][0], 70)
        farthest = max(_trace_shot(self.state["terrain"], self.state["x"], 0, 0, angle,
                                   _wind(self.state["map"], 0))["x"] for angle in range(10, 81))
        self.assertGreater(farthest, 55_000)

    def _classic_snapshot(self, with_shop=True):
        state = self.state
        terrain = state["terrain"][:32]
        heights = bytes((terrain[i] << 4) | terrain[i+1] for i in range(0, 32, 2))
        header = bytes((state["map"], state["turn"], state["round"], *state["hp"], 2, 29,
                        *state["kits"], *state["fuel"], 0, *state["last"],
                        state["last_wind"], *state["last_damage"], 0))
        shop = bytes((*state["credits"], *(n for stock in state["ammo"] for n in stock)))
        return header + heights + heights + (shop if with_shop else b"")

    def test_classic_snapshot_migrates_to_wide_terrain(self):
        classic = self._classic_snapshot()
        recovered = self.game.decode_state(classic)
        self.assertEqual(len(recovered["terrain"]), WIDTH)
        self.assertEqual(recovered["terrain"][:32], self.state["terrain"][:32])
        self.assertEqual(self.game.decode_state(self.game.encode_state(recovered)), recovered)

    def test_static_map_is_not_transmitted_and_craters_are_sparse(self):
        original = self.game.encode_state(self.state)
        self.assertEqual(len(original), 18 + 1 + 2 + 2 * len(WEAPONS))
        cratered = next((result for angle in range(10, 81)
                         if (result := self.game.apply_action(self.state, self.players, self.players[0], bytes((0, angle))))["craters"]), None)
        self.assertIsNotNone(cratered)
        payload = self.game.encode_state(cratered)
        self.assertEqual(len(payload), len(original) + 2)
        self.assertEqual(self.game.decode_state(payload), cratered)

    def test_maximally_deformed_map_stays_within_radio_packet_budget(self):
        deformed = dict(self.state)
        deformed["terrain"] = [0] * WIDTH
        deformed["last_terrain"] = [0] * WIDTH
        deformed["craters"] = None  # Recovery fallback for old/extremely long matches.
        payload = self.game.encode_state(deformed)
        self.assertLessEqual(len(payload), 200)
        self.assertEqual(self.game.decode_state(payload), deformed)

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
