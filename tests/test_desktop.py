"""Desktop interaction smoke check; skipped without the desktop extra."""
import importlib.util
from itertools import permutations
import os
import tempfile
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "install the desktop extra")
class DesktopScenarios(unittest.TestCase):
    def test_scorched_earth_native_controls_and_hotseat_fire(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QSlider
        from radio_arcade.desktop import ArcadeWindow, ScorchedBattlefield
        from radio_arcade.practice import PracticeService
        from radio_arcade.settings import Settings

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            window = ArcadeWindow(Settings(callsign="N0CALL", transport="practice"))
            engine = PracticeService("N0CALL")
            window._service_ready(engine)
            sid = engine.create_game("scorched-earth")
            window.selected_id = sid
            window._show_session(sid)
            try:
                self.assertIsNotNone(window.findChild(ScorchedBattlefield, "scorchedBattlefield"))
                weapon = window.findChild(QComboBox, "scorchedWeapon")
                angle = window.findChild(QSlider, "scorchedAngle")
                self.assertEqual(weapon.currentData(), 0)
                self.assertEqual(angle.value(), 45)
                self.assertTrue(window.findChild(QPushButton, "scorchedFire").isEnabled())
                window.findChild(QPushButton, "scorchedFire").click()
                deadline = time.monotonic() + 3
                while engine.session(sid)["revision"] < 1 and time.monotonic() < deadline:
                    app.processEvents(); time.sleep(.01)
                self.assertEqual(engine.session(sid)["revision"], 1)
            finally:
                window.close(); engine.close(); app.processEvents()

    def test_practice_game_through_board_controls(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QPushButton
        from radio_arcade.desktop import ArcadeWindow
        from radio_arcade.practice import PracticeService
        from radio_arcade.settings import Settings

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            window = ArcadeWindow(Settings(callsign="N0CALL", transport="practice"))
            engine = PracticeService("N0CALL")
            window._service_ready(engine)
            sid = engine.create_game("tic-tac-toe")
            window.selected_id = sid
            window._show_session(sid)
            try:
                for revision, cell in enumerate((0, 3, 1, 4, 2), 1):
                    button = window.findChild(QPushButton, f"boardCell{cell}")
                    self.assertTrue(button.isEnabled())
                    button.click()
                    deadline = time.monotonic() + 3
                    while engine.session(sid)["revision"] < revision and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(0.01)
                    self.assertEqual(engine.session(sid)["revision"], revision)
                    window._show_session(sid)
                self.assertEqual(engine.session(sid)["status"], "finished")
            finally:
                window.close()
                app.processEvents()

    def test_open_word_tiles_practice_controls_submit_a_word(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton
        from radio_arcade.desktop import ArcadeWindow
        from radio_arcade.practice import PracticeService
        from radio_arcade.settings import Settings
        from radio_arcade.games.scrabble import _lexicon

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            window = ArcadeWindow(Settings(callsign="N0CALL", transport="practice"))
            engine = PracticeService("N0CALL")
            window._service_ready(engine)
            sid = engine.create_game("open-word-tiles")
            window.selected_id = sid
            session = engine.session(sid)
            window._show_session(sid)
            try:
                game = engine.registry.by_id(3)
                state = game.decode_state(session["state"])
                game.validate_roster(state, tuple(session["players"]))
                rack = "".join(chr(64 + tile) for tile in state["racks"][state["turn"]] if tile not in (0, 31))
                word = next(("".join(chars) for length in range(2, len(rack) + 1)
                             for chars in permutations(rack, length)
                             if "".join(chars) in _lexicon()), None)
                self.assertIsNotNone(word)
                x = 7 - (len(word) - 1) // 2
                self.assertIsNotNone(window.findChild(QPushButton, "wordCell112"))
                window.findChild(QLineEdit, "wordStart").setText(f"{chr(65+x)}8")
                window.findChild(QLineEdit, "wordText").setText(word)
                window.findChild(QPushButton, "wordPlace").click()
                deadline = time.monotonic() + 3
                while engine.session(sid)["revision"] < 1 and time.monotonic() < deadline:
                    app.processEvents(); time.sleep(0.01)
                self.assertEqual(engine.session(sid)["revision"], 1)
            finally:
                window.close(); engine.close(); app.processEvents()
