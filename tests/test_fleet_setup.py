"""Classic fleet editing is local, reversible and explicitly deployed."""
import importlib.util
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from radio_arcade.fleet_setup import FleetDraft
from radio_arcade.games.battleship import FLEET

class FleetDraftTests(unittest.TestCase):
    def test_many_randomized_fleets_stay_inside_board(self):
        import random
        from radio_arcade.practice_ai import fleet
        for seed in range(100):
            ships=fleet(FLEET,random.Random(seed))
            self.assertEqual(tuple(map(len,ships)),FLEET)
            cells=sum(ships,[])
            self.assertTrue(all(0<=c<100 for c in cells))
            self.assertEqual(len(cells),len(set(cells)))

    def test_edges_overlap_rotation_and_undo(self):
        draft = FleetDraft()
        self.assertEqual(draft.preview(8), ([8, 9], False))
        self.assertFalse(draft.place(8))
        self.assertTrue(draft.place(0))
        self.assertFalse(draft.place(2))
        draft.vertical = True
        self.assertEqual(draft.preview(9), ([9,19,29,39], True))
        self.assertTrue(draft.place(9))
        draft.undo()
        self.assertEqual(draft.ships, [[0,1,2,3,4]])
        draft.randomize()
        self.assertTrue(draft.complete)
        self.assertEqual(tuple(map(len, draft.ships)), FLEET)
        self.assertEqual(len(set(sum(draft.ships, []))), sum(FLEET))
        draft.undo()
        self.assertEqual(draft.ships, [[0,1,2,3,4]])
        draft.clear()
        self.assertFalse(draft.ships)
        draft.undo()
        self.assertEqual(draft.ships, [[0,1,2,3,4]])

@unittest.skipUnless(importlib.util.find_spec("PySide6"), "desktop extra")
class FleetSetupDesktop(unittest.TestCase):
    def test_preview_undo_randomize_and_explicit_deploy(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QPushButton, QLabel
        from radio_arcade.desktop import ArcadeWindow, FleetPlacementCell
        from radio_arcade.settings import Settings
        from radio_arcade.practice import PracticeService
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RADIO_ARCADE_HOME":folder}):
            engine = PracticeService("PLAYER")
            window = ArcadeWindow(Settings(callsign="PLAYER", transport="practice"))
            window.resize(1366,768); window.show(); window._service_ready(engine)
            try:
                sid = engine.create_game("battleship-classic")
                window.selected_id = sid; window._refresh_sessions(); app.processEvents()
                def find(name, kind=QPushButton):
                    return window.board_scroll.findChild(kind, name)
                a1=find("fleetCell0",FleetPlacementCell)
                a1.hovered.emit(0)
                self.assertEqual(find("fleetCell4").property("preview"),"valid")
                find("fleetCell9",FleetPlacementCell).hovered.emit(9)
                self.assertEqual(find("fleetCell9").property("preview"),"invalid")
                self.assertFalse(find("fleetDeploy").isEnabled())
                find("fleetRotate").click()
                a1.hovered.emit(0)
                self.assertEqual(find("fleetCell40").property("preview"),"valid")
                a1.click()
                self.assertIn("Battleship",find("fleetPrompt",QLabel).text())
                self.assertIsNone(engine.session(sid).get("private_fleet"))
                find("fleetUndo").click()
                self.assertIn("Carrier",find("fleetPrompt",QLabel).text())
                find("fleetRandomize").click()
                self.assertTrue(find("fleetDeploy").isEnabled())
                self.assertIsNone(engine.session(sid).get("private_fleet"))
                find("fleetDeploy").click()
                deadline=time.monotonic()+3
                while window._tasks and time.monotonic()<deadline:
                    app.processEvents(); time.sleep(.01)
                self.assertEqual(len(engine.session(sid)["private_fleet"]),5)
                self.assertEqual(engine.session(sid)["view"]["phase"],"call")
                self.assertTrue(any("ships afloat" in l.text() for l in window.board_scroll.findChildren(QLabel)))
            finally:
                window.close(); app.processEvents()
