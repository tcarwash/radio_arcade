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
    async def test_island_settlers_practice_roll_and_build_controls(self):
        from radio_arcade.settings import Settings
        from radio_arcade.tui import ArcadeTUI

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            app = ArcadeTUI(Settings(callsign="N0CALL", transport="practice"))
            async with app.run_test(size=(175, 55)) as pilot:
                await pilot.pause(0.2)
                sid = app.engine.create_game("island-settlers")
                app.selected_id = sid
                await app._show_session(sid)
                self.assertEqual(len(app.query("#island-map")), 1)
                self.assertIn("TIMBER", app.query_one("#island-map").render().plain)
                await pilot.pause(0.3)
                await pilot.click("#island-map", offset=(35, 1))
                self.assertEqual(app.query_one("#island-action").value, "city")
                self.assertEqual(app.query_one("#island-target").value, "0")
                await pilot.click("#island-map", offset=(39, 2))
                self.assertEqual(app.query_one("#island-action").value, "road")
                self.assertEqual(app.query_one("#island-target").value, "0")
                self.assertTrue(app.query_one("#island-roll").display)
                self.assertFalse(app.query_one("#island-roll").disabled)
                app.query_one("#island-roll").press()
                for _ in range(30):
                    await pilot.pause(0.05)
                    if app.engine.session(sid)["revision"] >= 1:
                        break
                self.assertEqual(app.engine.session(sid)["view"]["phase"], "build")
                await app._show_session(sid)
                self.assertFalse(app.query_one("#island-end").disabled)
                app.query_one("#island-action").value = "road"
                app.query_one("#island-target").value = "0"
                app._island_submit()
                for _ in range(30):
                    await pilot.pause(0.05)
                    if app.engine.session(sid)["revision"] >= 2:
                        break
                self.assertEqual(app.engine.session(sid)["view"]["roads"][0]["owner"], "N0CALL")
                await app._show_session(sid)
                self.assertIn("━", app.query_one("#island-map").render().plain)

    async def test_island_board_fits_compact_terminal(self):
        from radio_arcade.settings import Settings
        from radio_arcade.tui import ArcadeTUI

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            app = ArcadeTUI(Settings(callsign="N0CALL", transport="practice"))
            async with app.run_test(size=(80, 32)) as pilot:
                await pilot.pause(0.2)
                sid = app.engine.create_game("island-settlers")
                app.selected_id = sid
                await app._show_session(sid)
                await pilot.press("2")
                await pilot.pause(0.3)
                self.assertTrue(app.screen.has_class("compact"))
                self.assertEqual(app.query_one("#island-map").region.width, 68)
                self.assertGreaterEqual(app.query_one("#board").region.width, 68)
                await pilot.click("#island-map", offset=(35, 1))
                self.assertEqual(app._island_selection, ("site", 0))

    async def test_island_map_renderer_click_targets_and_owner_styles(self):
        from radio_arcade.games.settlers import IslandSettlersGame
        from radio_arcade.island_map import HEIGHT, WIDTH, SITES, map_hit, render_island

        game = IslandSettlersGame()
        players = ("N0AAA", "N0BBB", "N0CCC", "N0DDD")
        state = game.new_state(players)
        state["roads"][0] = 1
        state["sites"][0] |= 0x80
        view = game.view_model(state, players)
        result, hits = render_island(view, ("road", 0))
        lines = result.plain.splitlines()
        self.assertEqual(len(lines), HEIGHT)
        self.assertTrue(all(len(line) == WIDTH for line in lines))
        self.assertEqual(map_hit(hits, *SITES[0]), ("site", 0))
        self.assertEqual(map_hit(hits, 39, 2), ("road", 0))
        self.assertIn("TIMBER", result.plain)
        self.assertIn("◆", result.plain)
        self.assertIn("━", result.plain)
        self.assertIn("◉", render_island(view, ("site", 0))[0].plain)
        mono, _ = render_island(view, monochrome=True)
        self.assertIn("ORE", mono.plain)

    async def test_overlapping_board_refreshes_are_serialized(self):
        from radio_arcade.settings import Settings
        from radio_arcade.tui import ArcadeTUI

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            app = ArcadeTUI(Settings(callsign="N0CALL", transport="practice"))
            async with app.run_test(size=(170, 50)) as pilot:
                await pilot.pause(0.2)
                sid = app.engine.create_game("open-word-tiles")
                app.selected_id = sid
                await asyncio.gather(*(app._show_session(sid) for _ in range(5)))
                self.assertEqual(len(app.query("#word-preview-hint")), 1)
                self.assertTrue(app.query_one("#scoreboard").display)
                self.assertFalse(app.screen.has_class("compact"))

    async def test_word_board_click_and_direction_buttons(self):
        from radio_arcade.settings import Settings
        from radio_arcade.tui import ArcadeTUI

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            app = ArcadeTUI(Settings(callsign="N0CALL", transport="practice"))
            async with app.run_test(size=(100, 56)) as pilot:
                await pilot.pause(0.2)
                sid = app.engine.create_game("open-word-tiles")
                app.selected_id = sid
                await app._show_session(sid)
                self.assertTrue(app.query_one("#scoreboard").display)
                self.assertIn("BAG ", app.query_one("#arena-meta").render().plain)
                self.assertEqual(app.query_one("#score-divider").render().plain, "VS")
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
                self.assertTrue(app.query_one("#word-preview-hint").render().plain.startswith(("PREVIEW", "✓")))

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
                app._refresh_word_preview()
                self.assertIn("POINTS", app.query_one("#word-preview-hint").render().plain)
                app._word_place()
                for _ in range(50):
                    await pilot.pause(0.05)
                    if app.engine.session(sid)["revision"] >= 1: break
                self.assertEqual(app.engine.session(sid)["revision"], 1)
                updated = app.engine.session(sid)["view"]["scores"]
                await app._show_session(sid)
                displayed = {app.query_one("#score-left-value").render().plain,
                             app.query_one("#score-right-value").render().plain}
                self.assertTrue({str(value) for value in updated.values()}.issubset(displayed))
