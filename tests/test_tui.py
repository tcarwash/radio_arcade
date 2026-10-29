"""Keyboard interaction smoke check; skipped when the TUI extra is absent."""
import asyncio
import importlib.util
from itertools import permutations
import os
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("textual"), "install the tui extra")
class TuiScenarios(unittest.IsolatedAsyncioTestCase):
    async def test_word_board_click_and_direction_buttons(self):
        from radio_arcade.settings import Settings
        from radio_arcade.tui import ArcadeTUI

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            app = ArcadeTUI(Settings(callsign="N0CALL", transport="practice"))
            async with app.run_test(size=(100, 42)) as pilot:
                await pilot.pause(0.2)
                sid = app.engine.create_game("open-word-tiles")
                app.selected_id = sid
                await app._show_session(sid)
                await pilot.press("2")
                await pilot.pause(0.2)
                # The first board tile starts at offset x=4, y=1.
                await pilot.click("#word-board", offset=(5, 1))
                self.assertEqual(app.query_one("#word-start").value, "A1")
                await pilot.click("#word-board", offset=(33, 8))
                self.assertEqual(app.query_one("#word-start").value, "H8")
                await pilot.click("#word-vertical")
                self.assertEqual(app._word_direction, "V")
                await pilot.click("#word-horizontal")
                self.assertEqual(app._word_direction, "H")
                app.query_one("#word-text").value = "CAT"
                await pilot.pause(0.1)
                preview = app.query_one("#word-board").render()
                self.assertIn(" C   A   T ", preview.plain)
                self.assertIn("Preview only", app.query_one("#word-preview-hint").render().plain)

    async def test_word_preview_flags_conflicts_and_board_edges(self):
        from radio_arcade.tui import WORD_PREMIUM_STYLES, word_preview

        self.assertEqual(len({WORD_PREMIUM_STYLES[key] for key in ("TW", "DW", "TL", "DL", "..")}), 5)

        board = {"cells": ["" for _ in range(225)]}
        board["cells"][7 * 15 + 7] = "X"
        preview, status = word_preview(board, "H8", "H", "CAT")
        self.assertEqual(preview[7 * 15 + 7], "C")
        self.assertIn("conflicts", status)
        preview, status = word_preview(board, "O15", "H", "CAT")
        self.assertEqual(preview[224], "C")
        self.assertIn("beyond", status)

    async def test_overlapping_session_refreshes_do_not_duplicate_widget_ids(self):
        from radio_arcade.settings import Settings
        from radio_arcade.tui import ArcadeTUI

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            app = ArcadeTUI(Settings(callsign="N0CALL", transport="practice"))
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause(0.2)
                for _ in range(4):
                    app.engine.create_game("tic-tac-toe")
                await asyncio.gather(*(app._refresh_sessions() for _ in range(5)))
                items = app.query_one("#session-list").children
                self.assertEqual(len(items), 4)
                self.assertEqual(len({item.id for item in items}), 4)

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
