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
    def test_games_panel_create_delete_and_reselect(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMessageBox
        from radio_arcade.desktop import ArcadeWindow
        from radio_arcade.practice import PracticeService
        from radio_arcade.settings import Settings

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            window = ArcadeWindow(Settings(callsign="N0CALL", transport="practice"))
            engine = PracticeService("N0CALL")
            window._service_ready(engine)
            window.show()

            def wait_for(predicate):
                deadline = time.monotonic() + 3
                while not predicate() and time.monotonic() < deadline:
                    app.processEvents(); time.sleep(.01)
                self.assertTrue(predicate())

            try:
                window.new_button.click()
                wait_for(lambda: window.selected_id is not None and not window._tasks)
                first = window.selected_id
                window.new_button.click()
                wait_for(lambda: window.selected_id != first and not window._tasks)
                removed = window.selected_id
                engine.send_chat(removed, "Delete this saved chat too")
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                    window.delete_button.click()
                    wait_for(lambda: window.selected_id is None and not window._tasks)
                self.assertEqual([row["id"] for row in engine.sessions()], [first])
                self.assertEqual([row["id"] for row in engine.store.load_sessions()], [first])
                self.assertEqual(engine.store.load_chat(removed), [])
                window._refresh_sessions()
                self.assertIsNone(window.selected_id)
                self.assertFalse(window.delete_button.isEnabled())
                window.session_list.setCurrentRow(0)
                self.assertEqual(window.selected_id, first)
                self.assertTrue(window.delete_button.isEnabled())
                for width, height in ((1024, 680), (2048, 1152)):
                    window.resize(width, height); app.processEvents()
                    self.assertTrue(window.new_button.isVisible())
                    self.assertGreaterEqual(window.new_button.height(), 42)
                    self.assertLess(window.new_button.geometry().bottom(), window.delete_button.geometry().top())
            finally:
                window.close(); app.processEvents()

    def test_crater_settles_after_projectile_arrives(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from radio_arcade.desktop import ScorchedBattlefield
        from radio_arcade.games.scorched_earth import ScorchedEarthGame
        app = QApplication.instance() or QApplication([])
        game = ScorchedEarthGame(); players = ("A", "B"); state = game.new_state(players)
        result = next(result for angle in range(91)
                      if (result := game.apply_action(state, players, "A", bytes((1, angle))))["terrain"] != state["terrain"])
        restored = game.decode_state(game.encode_state(result))
        view = game.view_model(restored, players)
        battlefield = ScorchedBattlefield(view, animate=True)
        try:
            started = battlefield._flight_started
            with patch("radio_arcade.desktop.time.monotonic", return_value=started + 1):
                self.assertEqual(battlefield.displayed_terrain(), state["terrain"])
            with patch("radio_arcade.desktop.time.monotonic", return_value=started + 2.325):
                midway = battlefield.displayed_terrain()
                self.assertNotEqual(midway, state["terrain"])
                self.assertNotEqual(midway, result["terrain"])
            with patch("radio_arcade.desktop.time.monotonic", return_value=started + 3):
                self.assertEqual(battlefield.displayed_terrain(), result["terrain"])
            battlefield._flight_timer.stop()
            self.assertEqual(battlefield.displayed_terrain(), result["terrain"])
        finally:
            battlefield.close(); app.processEvents()

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
                power = window.findChild(QSlider, "scorchedPower")
                self.assertIsNotNone(power)
                power.setValue(10)  # 50% power

                self.assertTrue(window.findChild(QPushButton, "scorchedFire").isEnabled())
                window.findChild(QPushButton, "scorchedFire").click()
                deadline = time.monotonic() + 3
                while engine.session(sid)["revision"] < 1 and time.monotonic() < deadline:
                    app.processEvents(); time.sleep(.01)
                self.assertEqual(engine.session(sid)["revision"], 1)
                self.assertEqual(engine.session(sid)["view"]["impact"]["power"], 50)
                window._show_session(sid)
                animated = window.findChild(ScorchedBattlefield, "scorchedBattlefield")
                self.assertIsNotNone(animated)
                self.assertLess(animated.flight_progress, 1)
                self.assertTrue(animated._flight_timer.isActive())
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
                self.assertIsNotNone(window._result_dialog)
                window._result_dialog.accept()
                window._refresh_sessions()
                self.assertIsNone(window._result_dialog)
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
