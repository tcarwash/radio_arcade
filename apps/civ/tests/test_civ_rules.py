"""Headless checks for the separate Radio Civ game plugin."""

import unittest

from radio_civ.rules import (
    MAX_CITIES, MAX_UNITS, TECHS, TECH_COSTS, TECH_GUIDE, CivilizationGame, build, distance, end_turn,
    found, grow, move, neighbors, recruit, research, score, terrain, turn_bundle,
)
from radio_arcade.wire import Kind, Packet, decode, encode, encode_snapshot


TWO = ("AG7SU", "K7XYZ")
FOUR = ("LONGCALL01", "LONGCALL02", "LONGCALL03", "LONGCALL04")


class CivilizationRules(unittest.TestCase):
    def setUp(self):
        self.game = CivilizationGame()

    def test_research_help_matches_unlocks_and_prerequisites(self):
        self.assertEqual(len(TECH_GUIDE), len(TECHS))
        for index, tech in enumerate(TECH_GUIDE):
            with self.subTest(name=tech["name"]):
                self.assertEqual(tech["name"], TECHS[index])
                self.assertEqual(tech["cost"], TECH_COSTS[index])
                self.assertIn(tech["effect"], tech["tooltip"])
                self.assertIn("+3 victory points", tech["tooltip"])
        self.assertEqual(TECH_GUIDE[2]["requires"], "Mining")
        self.assertEqual(TECH_GUIDE[4]["requires"], "Writing")
        self.assertIn("Forges", TECH_GUIDE[1]["effect"])
        self.assertIn("Warriors", TECH_GUIDE[2]["effect"])
        self.assertIn("2 steps", TECH_GUIDE[4]["effect"])

    def test_map_and_roster_determinism(self):
        a = self.game.new_state(TWO)
        b = self.game.new_state(TWO)
        self.assertEqual(a, b)
        self.assertEqual([terrain(a["seed"], i) for i in range(64)],
                         [terrain(b["seed"], i) for i in range(64)])
        self.assertEqual(self.game.decode_state(self.game.encode_state(a)), a)
        self.assertEqual(self.game.current_player(a, TWO), TWO[0])
        self.assertEqual(len(neighbors(9)), 6)
        self.assertEqual(distance(9, 9), 0)
        self.assertEqual(distance(9, 10), 1)
        self.assertEqual(distance(9, 54), distance(54, 9))
        with self.assertRaises(ValueError):
            self.game.validate_roster(a, TWO[::-1])

    def test_two_orders_then_end_collects_resources_and_turns(self):
        state = self.game.new_state(TWO)
        first_food = state["players"][0]["food"]
        # Building a granary consumes one order; growing consumes the second.
        state["players"][0]["food"] = 20
        state["players"][0]["wood"] = 12
        state = self.game.apply_action(state, TWO, TWO[0], build(0, 0))
        state = self.game.apply_action(state, TWO, TWO[0], grow(0))
        self.assertEqual(state["ap"], 0)
        with self.assertRaises(ValueError):
            self.game.apply_action(state, TWO, TWO[0], research(0))
        gained = self.game.apply_action(state, TWO, TWO[0], end_turn())
        self.assertGreater(gained["players"][0]["food"], state["players"][0]["food"])
        self.assertEqual(gained["turn"], 1)
        self.assertEqual(gained["ap"], 2)
        with self.assertRaises(ValueError):
            self.game.apply_action(gained, TWO, TWO[0], end_turn())
        self.assertEqual(first_food, 6)

    def test_turn_bundle_applies_orders_and_end_turn_as_one_transition(self):
        state = self.game.new_state(TWO)
        state["players"][0]["food"] = 20
        state["players"][0]["wood"] = 12
        action = turn_bundle((build(0, 0), grow(0)))

        result = self.game.apply_action(state, TWO, TWO[0], action)

        self.assertEqual(result["turn"], 1)
        self.assertEqual(result["ap"], 2)
        self.assertEqual(result["cities"][0]["buildings"], 1)
        self.assertEqual(result["cities"][0]["pop"], 2)
        self.assertEqual(state["turn"], 0)
        self.assertEqual(len(action), 8)

    def test_turn_bundle_rejects_invalid_later_order_without_mutating_state(self):
        state = self.game.new_state(TWO)
        original = self.game.encode_state(state)
        action = turn_bundle((build(0, 0), move(99, 1)))

        with self.assertRaises(ValueError):
            self.game.apply_action(state, TWO, TWO[0], action)

        self.assertEqual(self.game.encode_state(state), original)

    def test_research_cost_prerequisites_and_warriors(self):
        state = self.game.new_state(TWO)
        with self.assertRaisesRegex(ValueError, "prerequisite"):
            self.game.apply_action(state, TWO, TWO[0], research(2))
        state["players"][0]["science"] = 25
        state = self.game.apply_action(state, TWO, TWO[0], research(1))
        state = self.game.apply_action(state, TWO, TWO[0], research(2))
        self.assertEqual(state["players"][0]["tech"] & 6, 6)
        self.assertEqual(state["ap"], 0)
        with self.assertRaises(ValueError):
            self.game.apply_action(state, TWO, TWO[0], recruit(0, 1))
        state = self.game.apply_action(state, TWO, TWO[0], end_turn())
        state = self.game.apply_action(state, TWO, TWO[1], end_turn())
        state = self.game.apply_action(state, TWO, TWO[0], recruit(0, 1))
        self.assertTrue(any(unit["kind"] == 1 and unit["owner"] == 0 for unit in state["units"]))

    def test_settlers_found_cities_and_respect_spacing(self):
        state = self.game.new_state(TWO)
        state["cities"][0]["pop"] = 2
        state["players"][0]["food"] = 30
        state["players"][0]["wood"] = 30
        state = self.game.apply_action(state, TWO, TWO[0], recruit(0, 2))
        idx = len(state["units"]) - 1
        with self.assertRaisesRegex(ValueError, "two hexes"):
            self.game.apply_action(state, TWO, TWO[0], found(idx))
        # Exercise the founding rule on an independently discovered open hex.
        destination = next(t for t in range(64) if terrain(state["seed"], t) != 0
                           and all(distance(t, city["tile"]) >= 2 for city in state["cities"])
                           and all(unit["tile"] != t for unit in state["units"]))
        state["units"][idx]["tile"] = destination
        new_state = self.game.apply_action(state, TWO, TWO[0], found(idx))
        self.assertEqual(len(new_state["cities"]), 3)
        self.assertEqual(len(new_state["units"]), len(state["units"]) - 1)
        self.assertEqual(new_state["cities"][-1]["tile"], destination)
        self.assertEqual(self.game.decode_state(self.game.encode_state(new_state)), new_state)

    def test_combat_capture_and_elimination(self):
        state = self.game.new_state(TWO)
        enemy_city = state["cities"][1]
        destination = enemy_city["tile"]
        free_neighbor = next(n for n in neighbors(destination) if terrain(state["seed"], n) != 0
                             and all(u["tile"] != n for u in state["units"]))
        state["players"][0]["tech"] = 6
        state["units"].append({"tile": free_neighbor, "owner": 0, "kind": 1, "hp": 3})
        index = len(state["units"]) - 1
        first = self.game.apply_action(state, TWO, TWO[0], move(index, destination))
        self.assertEqual(first["cities"][1]["hp"], 1)
        second = self.game.apply_action(first, TWO, TWO[0], move(index, destination))
        self.assertEqual(second["cities"][1]["owner"], 0)
        self.assertEqual(second["units"][index]["tile"], destination)
        ended = self.game.apply_action(second, TWO, TWO[0], end_turn())
        self.assertTrue(ended["finished"])
        self.assertEqual(ended["winner"], 0)
        self.assertIsNone(self.game.current_player(ended, TWO))

    def test_enemy_garrison_requires_separate_siege_orders(self):
        state = self.game.new_state(TWO)
        city = state["cities"][1]
        destination = city["tile"]
        free_neighbor = next(n for n in neighbors(destination) if terrain(state["seed"], n) != 0
                             and all(u["tile"] != n for u in state["units"]))
        state["players"][0]["tech"] = 6
        state["units"].append({"tile": free_neighbor, "owner": 0, "kind": 1, "hp": 3})
        index = len(state["units"]) - 1
        state["units"][1]["tile"] = destination  # Enemy scout garrisons its own city.
        attacked = self.game.apply_action(state, TWO, TWO[0], move(index, destination))
        self.assertFalse(any(u["owner"] == 1 for u in attacked["units"]))
        self.assertEqual(attacked["units"][-1]["tile"], free_neighbor)
        self.assertEqual(attacked["cities"][1]["hp"], 3)
        sieged = self.game.apply_action(attacked, TWO, TWO[0], move(index - 1, destination))
        self.assertEqual(sieged["cities"][1]["hp"], 1)

    def test_movement_and_fog(self):
        state = self.game.new_state(TWO)
        current = state["units"][0]["tile"]
        destination = next(tile for tile in neighbors(current)
                           if terrain(state["seed"], tile) != 0
                           and all(unit["tile"] != tile for unit in state["units"]))
        old_seen = state["players"][0]["seen"]
        moved = self.game.apply_action(state, TWO, TWO[0], move(0, destination))
        self.assertEqual(moved["units"][0]["tile"], destination)
        self.assertEqual(moved["players"][0]["seen"] & old_seen, old_seen)
        self.assertEqual(state["units"][0]["tile"], current)  # No input mutation.
        with self.assertRaisesRegex(ValueError, "adjacent"):
            self.game.apply_action(state, TWO, TWO[0], move(0, 63))

    def test_every_snapshot_fits_worst_case_envelope(self):
        state = self.game.new_state(FOUR)
        state["cities"] = [dict(tile=i * 2, owner=i % 4, pop=5, hp=3, buildings=15)
                           for i in range(MAX_CITIES)]
        state["units"] = [dict(tile=24 + i, owner=i % 4, kind=i % 3, hp=3)
                          for i in range(MAX_UNITS)]
        body = self.game.encode_state(state)
        self.assertEqual(len(body), 138)
        self.assertEqual(self.game.decode_state(body), state)
        packet = Packet(5, Kind.SNAPSHOT, 123, 10, 10, FOUR[0], FOUR[1],
                        encode_snapshot(FOUR, body, FOUR[0], b"M\x01\x02"))
        frame = encode(packet)
        self.assertEqual(len(frame), 239)
        self.assertEqual(decode(frame), packet)

    def test_invalid_actions_and_snapshots_fail_closed(self):
        state = self.game.new_state(TWO)
        for action in (b"", b"Z", b"M", b"E\x00", research(99), build(0, 100), move(99, 1)):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.game.apply_action(state, TWO, TWO[0], action)
        raw = self.game.encode_state(state)
        for invalid in (b"", raw[:-1], bytes((2,)) + raw[1:], raw + b"\0"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.game.decode_state(invalid)
        self.assertGreaterEqual(score(state, 0), 1)


if __name__ == "__main__":
    unittest.main()
