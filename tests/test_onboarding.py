"""Fresh-profile desktop onboarding and game discovery regression coverage."""
import importlib.util
import os
import tempfile
import time
import unittest
from unittest.mock import patch

@unittest.skipUnless(importlib.util.find_spec("PySide6"), "install the desktop extra")
class FirstRunUX(unittest.TestCase):
    def test_mode_selection_and_progressive_settings(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QPushButton
        from radio_arcade.desktop import OnboardingDialog, SettingsDialog
        from radio_arcade.settings import Settings
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            welcome = OnboardingDialog()
            welcome.findChild(QPushButton, "onboarding-practice").click()
            self.assertEqual(welcome.mode, "practice")
            settings = Settings(transport="practice")
            dialog = SettingsDialog(settings)
            dialog.show(); app.processEvents()
            self.assertFalse(dialog.host.isVisible())
            self.assertFalse(dialog.kiss_port.isVisible())
            dialog.callsign.clear()
            dialog._save()
            self.assertEqual(Settings.load().callsign, "PLAYER")
            self.assertEqual(Settings.load().transport, "practice")
            dialog = SettingsDialog(settings)
            dialog.show(); dialog.transport.setCurrentIndex(dialog.transport.findData("broadcast")); app.processEvents()
            self.assertTrue(dialog.host.isVisible())
            self.assertFalse(dialog.command_port.isVisible())
            dialog.advanced.setChecked(True)
            self.assertTrue(dialog.command_port.isVisible())
            self.assertTrue(dialog.kiss_port.isVisible())
            dialog.transport.setCurrentIndex(dialog.transport.findData("connected"))
            self.assertFalse(dialog.kiss_port.isVisible())
            self.assertFalse(dialog.observer.isVisible())
            dialog.close()

    def test_library_quick_play_and_focus(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QPushButton
        from radio_arcade.desktop import ArcadeWindow, GameLibrary
        from radio_arcade.practice import PracticeService
        from radio_arcade.settings import Settings
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            engine = PracticeService("PLAYER")
            window = ArcadeWindow(Settings(callsign="PLAYER", transport="practice"))
            window.resize(1366, 768); window.show(); window._service_ready(engine); app.processEvents()
            try:
                self.assertFalse(window.panes[2].isVisible())
                self.assertFalse(window.tx_toggle.isVisible())
                self.assertIn("No RF", window.status_label.text())
                library = window.board_scroll.findChild(GameLibrary)
                self.assertIsNotNone(library)
                cards = {b.property("gameKey"): b for b in library.findChildren(QPushButton, "libraryPlayButton")}
                self.assertIn("battleship-classic", cards)
                self.assertNotIn("signal-siege", cards)
                self.assertEqual(window.board_scroll.horizontalScrollBar().maximum(), 0)
                cards["battleship-classic"].click()
                deadline = time.monotonic() + 3
                while window._tasks and time.monotonic() < deadline:
                    app.processEvents(); time.sleep(.01)
                self.assertEqual(engine.session(window.selected_id)["game_id"], 12)
                self.assertFalse(window.sync_button.isVisible())
                window.focus_toggle.setChecked(True); app.processEvents()
                self.assertFalse(window.panes[0].isVisible())
                self.assertTrue(window.panes[1].isVisible())
                window.focus_toggle.setChecked(False)
                window.chat_toggle.setChecked(True); app.processEvents()
                self.assertTrue(window.panes[2].isVisible())
                window.resize(360, 400); app.processEvents()
                self.assertEqual(window.compact_tabs.count(), 3)
                self.assertEqual(window.tx_toggle.text(), "NO RF")
            finally:
                window.close(); app.processEvents()
