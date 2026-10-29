"""Exercise the actual standalone desktop workflow when Qt is installed."""

import os
from pathlib import Path
import tempfile
import unittest

try:
    from PySide6.QtWidgets import QApplication
    from radio_civ.ui import CivWindow, HotseatService
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 desktop extra not installed")
class CivilizationDesktop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_qt = os.environ.get("QT_QPA_PLATFORM")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        if cls.old_qt is None:
            os.environ.pop("QT_QPA_PLATFORM", None)
        else:
            os.environ["QT_QPA_PLATFORM"] = cls.old_qt

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_data = os.environ.get("XDG_DATA_HOME")
        self.previous_config = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_DATA_HOME"] = self.temp.name
        os.environ["XDG_CONFIG_HOME"] = self.temp.name
        self.window = CivWindow()
        self.window._connect()
        self.assertIsInstance(self.window.engine, HotseatService)

    def tearDown(self):
        self.window.close()
        for key, old in (("XDG_DATA_HOME", self.previous_data), ("XDG_CONFIG_HOME", self.previous_config)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self.temp.cleanup()

    def test_hotseat_map_selection_and_turn_change(self):
        self.window._new_campaign()
        session = self.window.current_session
        self.assertEqual(session["view"]["kind"], "civilization")
        self.assertGreater(len(self.window.map.board.items()), 64)
        self.assertEqual(self.window.turn.text().startswith("◈  AG7SU"), True)
        city_tile = session["view"]["cities"][0]["tile"]
        self.window._click_tile(city_tile)
        self.assertTrue(self.window.recruit_buttons[0].isEnabled())
        self.window._build_city(0)
        self.assertEqual(self.window.current_session["view"]["cities"][0]["buildings"], 1)
        self.window._order(b"E")
        self.assertEqual(self.window.current_session["view"]["turn"], "LOCAL2")
        self.assertIn("LOCAL2", self.window.turn.text())

    def test_disables_orders_in_receive_only_view(self):
        # The UI must never offer input on an observer-owned game view.
        self.window._new_campaign()
        self.window.engine.observer_mode = True
        self.window._render()
        self.assertFalse(self.window.end_button.isEnabled())
        self.assertFalse(self.window.new_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
