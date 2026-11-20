"""Opponent move cues stay public and can be replayed without mutating the game."""
import os
import random
import tempfile
import unittest
from unittest.mock import patch

from radio_arcade.practice import PracticeService
from radio_arcade.practice_ai import fleet
from radio_arcade.games.battleship import FLEET


class MoveCueTests(unittest.TestCase):
    def test_tic_tac_toe_targets_opponents_committed_square(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{"RADIO_ARCADE_HOME":d}):
            e=PracticeService("PLAYER")
            try:
                sid=e.create_game("tic-tac-toe")
                self.assertIsNone(e.session(sid)["last_opponent_cue"])
                e.legal_action(sid,b"\x00")
                s=e.session(sid)
                cue=s["last_opponent_cue"]
                self.assertEqual(cue["actor"],"ARCADE-AI")
                self.assertEqual(cue["kind"],"tic-tac-toe")
                self.assertEqual(len(cue["indices"]),1)
                self.assertEqual(s["view"]["cells"][cue["indices"][0]],"O")
                self.assertEqual(e.session(sid)["last_opponent_cue"],cue)
                self.assertTrue(all("private_" not in key for key in cue))
            finally:e.close()

    def test_battleship_targets_incoming_public_shot_and_outcome(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{"RADIO_ARCADE_HOME":d}):
            e=PracticeService("PLAYER")
            try:
                sid=e.create_game("battleship-classic")
                e.place_battleship_fleet(sid,fleet(FLEET,random.Random(13)))
                e.legal_action(sid,b"\x00")
                s=e.session(sid)
                cue=s["last_opponent_cue"]
                self.assertEqual(cue["board"],"incoming")
                self.assertEqual(cue["actor"],"ARCADE-AI")
                self.assertEqual(len(cue["indices"]),1)
                result=s["view"]["incoming_shots"][cue["indices"][0]]
                self.assertEqual(cue["outcome"],"miss" if result==1 else "hit" if result==2 else "sunk" if result>=3 else "incoming")
                self.assertNotIn("ships",cue)
            finally:e.close()


try:
    from PySide6.QtWidgets import QApplication
    HAS_QT=True
except ImportError:
    HAS_QT=False


@unittest.skipUnless(HAS_QT,"desktop extra")
class DesktopMovePulseTests(unittest.TestCase):
    def test_pulse_and_explicit_replay(self):
        from PySide6.QtWidgets import QWidget,QPushButton
        from radio_arcade.desktop import ArcadeWindow
        from radio_arcade.settings import Settings
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{"RADIO_ARCADE_HOME":d}):
            w=ArcadeWindow(Settings(callsign="PLAYER",transport="practice"))
            w.resize(1366,768);w.show()
            try:
                sid=w.engine.create_game("tic-tac-toe") if w.engine else None
                if sid is None:
                    e=PracticeService("PLAYER")
                    w._service_ready(e)
                    sid=e.create_game("tic-tac-toe")
                w.selected_id=sid;w._refresh_sessions()
                w.engine.legal_action(sid,b"\x00")
                w._show_session(sid)
                for _ in range(4):app.processEvents()
                ripple=w.board_scroll.viewport().findChild(QWidget,"moveRipple")
                self.assertIsNotNone(ripple)
                self.assertTrue(w.replay_move_button.isVisible())
                w.replay_move_button.click();app.processEvents()
                self.assertIsNotNone(w.board_scroll.viewport().findChild(QWidget,"moveRipple"))
            finally:
                w.close();app.processEvents()
