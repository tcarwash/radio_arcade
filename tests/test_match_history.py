"""Persistent history and local-only shared guides."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from radio_arcade.match_history import match_history
from radio_arcade.practice import PracticeService
from radio_arcade.settings import Settings
from radio_arcade.web import WebController
from radio_arcade.game_guides import guide_for

class HistoryTests(unittest.TestCase):
    def test_saved_results_and_committed_move_history(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{"RADIO_ARCADE_HOME":d}):
            engine=PracticeService("PLAYER")
            sid=engine.create_game("tic-tac-toe")
            engine.legal_action(sid,b"\x00")
            moves=engine.move_history(sid)
            self.assertGreaterEqual(len(moves),2)
            self.assertEqual([m["revision"] for m in moves],list(range(1,len(moves)+1)))
            self.assertEqual(moves[0]["actor"],"PLAYER")
            engine.forfeit_game(sid)
            history=match_history(engine.sessions(),"PLAYER")
            self.assertEqual(history["totals"],dict(played=1,wins=0,losses=1,draws=0))
            self.assertEqual(history["matches"][0]["forfeited_by"],"PLAYER")
            self.assertEqual(engine.move_history(sid)[-1]["description"],"forfeits the game")
            engine.close()
            engine=PracticeService("PLAYER")
            controller=WebController(Settings(callsign="PLAYER",transport="practice"),service_factory=lambda s:engine,settings_path=Path(d)/"settings.json")
            try:
                self.assertEqual(controller.dispatch("GET","/api/history")[1]["totals"],history["totals"])
                self.assertEqual(controller.dispatch("GET","/api/moves/"+sid)[1]["moves"],engine.move_history(sid))
                self.assertEqual(controller.dispatch("GET","/api/guides")[1]["tic-tac-toe"],guide_for("tic-tac-toe"))
                self.assertEqual(match_history(engine.sessions(),"OTHER")["totals"]["played"],0)
                self.assertEqual(match_history(engine.sessions(),"PLAYER",observer=True)["matches"][0]["outcome"],"Observed")
                engine.delete_game(sid)
                self.assertEqual(engine.store.move_history(sid),[])
                self.assertEqual(controller.dispatch("GET","/api/history")[1]["totals"]["played"],0)
            finally:controller.close()

    def test_result_aggregation(self):
        base=dict(game="Nim",players=["PLAYER","GUEST"],status="finished",revision=4,updated=100)
        sessions=[dict(base,id="1",view={"winner":"PLAYER"}),dict(base,id="2",view={"winner":"GUEST"}),
                  dict(base,id="3",view={"winner":None}),dict(base,id="4",status="active",view={"winner":None})]
        self.assertEqual(match_history(sessions,"PLAYER")["totals"],dict(played=3,wins=1,losses=1,draws=1))
