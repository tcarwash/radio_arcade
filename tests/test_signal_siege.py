"""One-byte cooperative deck-builder protocol and game regressions."""

import unittest
import tempfile
from pathlib import Path

from radio_arcade.games.base import builtin_registry
from radio_arcade.games.signal_siege import SignalSiegeGame, pack_draft, pack_play
from radio_arcade.service import ArcadeService
from radio_arcade.store import SessionStore
from radio_arcade.wire import encode_roster_state
from radio_arcade.presentation import scoreboard, result_title


class SignalSiegeTests(unittest.TestCase):
    def setUp(self):
        self.game = SignalSiegeGame()

    def test_builtin_and_solo_start(self):
        self.assertIsInstance(builtin_registry(load_plugins=False).by_key("signal-siege"), SignalSiegeGame)
        state = self.game.new_state(("AG7SU",))
        self.assertEqual(self.game.current_player(state, ("AG7SU",)), "AG7SU")
        self.assertEqual(state, self.game.decode_state(self.game.encode_state(state)))

    def test_four_player_snapshot_is_short_and_independent(self):
        players = ("AG7SU", "K7AAA", "N7BBB", "W7CCC")
        state = self.game.new_state(players)
        self.assertLess(len(self.game.encode_state(state)), 80)
        next_state = self.game.apply_action(state, players, players[0], pack_play(0))
        self.assertEqual(next_state["turn"], 1)
        self.assertEqual(state["stations"][0]["deck"], list((0, 0, 0, 1, 1, 2)))
        self.assertEqual(next_state, self.game.decode_state(self.game.encode_state(next_state)))
        self.assertLess(len(encode_roster_state(players, self.game.encode_state(next_state))), 140)
        # Worst-case team snapshot after both draft phases is still tiny.
        next_state.update(wave=3, boss=self.game._boss_hp(3, len(players)))
        for station in next_state["stations"]:
            station["deck"].extend((3, 6))
        self.assertEqual(len(self.game.encode_state(next_state)), 57)
        self.assertLess(len(encode_roster_state(players, self.game.encode_state(next_state))), 180)

    def test_play_and_target_are_one_byte(self):
        players = ("AG7SU", "N7AAA")
        state = self.game.new_state(players)
        # Put a repair card at the front; target the other station (seat 1).
        state["stations"][0]["deck"][0] = 4
        state["stations"][1]["hp"] = 9
        next_state = self.game.apply_action(state, players, players[0], pack_play(0, 1))
        self.assertEqual(next_state["stations"][1]["hp"], 13)
        self.assertEqual(len(pack_play(2, 3)), 1)
        self.assertEqual(len(pack_draft(2)), 1)

    def test_draft_rewards_and_three_wave_victory(self):
        players = ("AG7SU",)
        state = self.game.new_state(players)
        for wave in range(1, 4):
            # Accelerate damage without changing the actual turn/draft rules.
            while state["phase"] == "battle":
                state["stations"][0]["focus"] = 10
                state = self.game.apply_action(state, players, players[0], pack_play(0))
            if wave < 3:
                self.assertEqual(state["phase"], "draft")
                self.assertEqual(len(self.game.view_model(state, players)["rewards"]), 3)
                state = self.game.apply_action(state, players, players[0], pack_draft(wave - 1))
                self.assertEqual(len(state["stations"][0]["deck"]), 6 + wave)
                self.assertEqual(state["wave"], wave + 1)
                self.assertEqual(state, self.game.decode_state(self.game.encode_state(state)))
        self.assertTrue(state["finished"])
        self.assertEqual(state["result"], "victory")

    def test_team_defeat_and_synchronized_pulse(self):
        players = ("AG7SU", "N7AAA")
        state = self.game.new_state(players)
        state["stations"][0]["hp"] = 1
        state["stations"][1]["hp"] = 1
        state = self.game.apply_action(state, players, players[0], pack_play(0))
        state = self.game.apply_action(state, players, players[1], pack_play(0))
        self.assertFalse(state["finished"])
        state = self.game.apply_action(state, players, players[0], pack_play(0))
        state = self.game.apply_action(state, players, players[1], pack_play(0))
        self.assertTrue(state["finished"])
        self.assertEqual(state["result"], "defeat")
        self.assertIsNone(self.game.current_player(state, players))

    def test_rejects_corrupt_or_illegal_network_data(self):
        players = ("AG7SU", "N7AAA")
        state = self.game.new_state(players)
        for payload in (b"", b"\x01", self.game.encode_state(state) + b"\x00",
                        bytes((2,)) + self.game.encode_state(state)[1:]):
            with self.assertRaises(ValueError):
                self.game.decode_state(payload)
        for payload in (b"", b"\x03", b"\x10", b"\x40", b"\xff", b"\x00\x00"):
            with self.assertRaises(ValueError):
                self.game.apply_action(state, players, "AG7SU", payload)
        with self.assertRaises(ValueError):
            self.game.apply_action(state, players, "N7AAA", b"\x00")

    def test_view_keeps_hand_and_reward_data_for_local_masking(self):
        players = ("AG7SU", "N7AAA")
        view = self.game.view_model(self.game.new_state(players), players)
        self.assertEqual(view["stations"][0]["hand"][0]["name"], "Ping")
        self.assertEqual(view["stations"][1]["deck_size"], 6)

    def test_cooperative_result_presentation(self):
        players = ("AG7SU", "N7AAA")
        state = self.game.new_state(players)
        for outcome in ("victory", "defeat"):
            state["boss"] = 0 if outcome == "victory" else 6
            state.update(phase="finished", result=outcome, finished=True)
            session = {"players": players, "status": "finished", "view": self.game.view_model(state, players)}
            self.assertEqual(result_title(session, "AG7SU"), outcome.upper())
            self.assertEqual([x["unit"] for x in scoreboard(session)["entries"]], ["HP", "HP"])

    def test_solo_game_runs_through_real_session_engine_and_recovers(self):
        class SilentTransport:
            def send(self, frame):
                pass
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.sqlite3"
            engine = ArcadeService("AG7SU", SilentTransport(), SessionStore(path))
            try:
                sid = engine.create_game("signal-siege")
                engine.start_game(sid)
                self.assertEqual(engine.session(sid)["view"]["stations"][0]["hand"][0]["name"], "Ping")
                engine.legal_action(sid, pack_play(0))
                self.assertEqual(engine.session(sid)["revision"], 1)
            finally:
                engine.close()
            reopened = ArcadeService("AG7SU", SilentTransport(), SessionStore(path))
            try:
                restored = reopened.session(sid)
                self.assertEqual(restored["revision"], 1)
                self.assertEqual(restored["view"]["boss_hp"], 7)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
