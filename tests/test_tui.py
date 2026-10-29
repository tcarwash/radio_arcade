"""Keyboard interaction smoke check; skipped when the TUI extra is absent."""
import importlib.util
from itertools import permutations
import os
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("textual"), "install the tui extra")
class TuiScenarios(unittest.IsolatedAsyncioTestCase):
    async def test_compact_keyboard_practice_game(self):
        from radio_arcade.settings import Settings
        from radio_arcade.tui import ArcadeTUI

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            app = ArcadeTUI(Settings(callsign="N0CALL", transport="practice"))
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause(0.5)
                app.query_one("#new-game").focus()
                await pilot.press("enter")
                await pilot.pause(0.5)
                await pilot.press("2")
                for cell in (0, 3, 1, 4, 2):
                    app.query_one(f"#cell-{cell}").focus()
                    await pilot.press("enter")
                    await pilot.pause(0.3)
                self.assertEqual(app.engine.session(app.selected_id)["status"], "finished")
                await pilot.press("3")
                self.assertTrue(app.screen.has_class("show-chat"))

    async def test_open_word_tiles_practice_controls_submit_a_word(self):
        from radio_arcade.games.scrabble import _lexicon
        from radio_arcade.settings import Settings
        from radio_arcade.tui import ArcadeTUI

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            app = ArcadeTUI(Settings(callsign="N0CALL", transport="practice"))
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause(0.5)
                sid = app.engine.create_game("open-word-tiles")
                app.selected_id = sid
                session = app.engine.session(sid)
                await app._show_session(sid)
                game = app.engine.registry.by_id(3)
                state = game.decode_state(session["state"])
                game.validate_roster(state, tuple(session["players"]))
                rack = "".join(chr(64 + tile) for tile in state["racks"][state["turn"]] if tile not in (0, 31))
                word = next(("".join(chars) for length in range(2, len(rack) + 1)
                             for chars in permutations(rack, length)
                             if "".join(chars) in _lexicon()), None)
                self.assertIsNotNone(word)
                x = 7 - (len(word) - 1) // 2
                app.query_one("#word-start").value = f"{chr(65+x)}8"
                app.query_one("#word-text").value = word
                app._word_place()
                for _ in range(50):
                    await pilot.pause(0.05)
                    if app.engine.session(sid)["revision"] >= 1: break
                self.assertEqual(app.engine.session(sid)["revision"], 1)
