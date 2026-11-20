"""Non-destructive archive, snapshot playback and practice difficulty."""
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from radio_arcade.practice import PracticeService
from radio_arcade.settings import Settings
from radio_arcade.web import WebController
from radio_arcade.match_history import match_history
from radio_arcade.games.scrabble import OpenWordTilesGame
from radio_arcade.practice_ai import choose


class ArchiveReplayAITests(unittest.TestCase):
    def test_archive_restore_persistence_stats_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{"RADIO_ARCADE_HOME":folder}):
            e=PracticeService("PLAYER")
            active=e.create_game("tic-tac-toe")
            with self.assertRaisesRegex(ValueError,"Only finished"):
                e.archive_game(active)
            e.legal_action(active,b"\x00")
            e.forfeit_game(active)
            original=e.session(active)
            journal=e.move_history(active)
            replay=e.replay_frames(active)
            self.assertGreaterEqual(len(journal),2)
            self.assertGreaterEqual(len(replay),3)
            self.assertEqual(replay[0]["revision"],0)
            self.assertTrue(all("private_fleet" not in frame["view"] for frame in replay))
            self.assertEqual(replay[-1]["revision"],original["revision"])
            e.archive_game(active)
            self.assertTrue(e.session(active)["archived"])
            self.assertEqual(match_history(e.sessions(),"PLAYER")["totals"]["played"],1)
            e.close()
            e=PracticeService("PLAYER")
            self.assertTrue(e.session(active)["archived"])
            self.assertEqual(e.move_history(active),journal)
            self.assertEqual(e.replay_frames(active),replay)
            e.archive_game(active,False)
            self.assertFalse(e.session(active)["archived"])
            e.archive_game(active,True)
            ctl=WebController(Settings(callsign="PLAYER",transport="practice"),service_factory=lambda _:e,
                              settings_path=Path(folder)/"prefs.json")
            try:
                state=ctl.dispatch("GET","/api/state")[1]
                self.assertNotIn(active,[s["id"] for s in state["sessions"]])
                history=ctl.dispatch("GET","/api/history")[1]
                self.assertTrue(history["matches"][0]["archived"])
                frames=ctl.dispatch("GET","/api/replay/"+active)[1]["frames"]
                self.assertEqual(frames,replay)
                result=ctl._command({"command":"archive_game","session_id":active,"archived":False})
                self.assertTrue(result["ok"])
                self.assertIn(active,[s["id"] for s in ctl.dispatch("GET","/api/state")[1]["sessions"]])
                status,_=ctl.dispatch("POST","/api/command",
                    b'{"command":"archive_game","session_id":"'+active.encode()+b'","archived":"yes"}')
                self.assertEqual(status,400)
                self.assertFalse(e.store.is_archived(active))
                e.delete_game(active)
                self.assertEqual(e.store.state_history(active),[])
                self.assertEqual(e.store.move_history(active),[])
            finally:
                ctl.close()

    def test_difficulty_settings_and_actual_word_opening(self):
        for difficulty in ("casual","standard","challenging"):
            with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{"RADIO_ARCADE_HOME":folder}):
                settings=Settings(callsign="PLAYER",transport="practice",ai_difficulty=difficulty)
                settings.save()
                self.assertEqual(Settings.load().ai_difficulty,difficulty)
                ctl=WebController(settings,service_factory=lambda _:PracticeService("PLAYER",difficulty),
                                  settings_path=Path(folder)/"web-settings.json")
                try:
                    self.assertEqual(ctl.dispatch("GET","/api/state")[1]["ai_difficulty"],difficulty)
                    with patch("radio_arcade.games.scrabble.secrets.randbits",return_value=43):
                        sid=ctl.engine.create_game("open-word-tiles")
                    row=ctl.engine.session(sid)
                    self.assertEqual(row["current_player"],"PLAYER")
                    self.assertGreater(row["revision"],0)
                    self.assertGreater(row["view"]["scores"]["ARCADE-AI"],0)
                    self.assertEqual(ctl.engine.move_history(sid)[0]["actor"],"ARCADE-AI")
                    status,_=ctl.dispatch("POST","/api/settings",b'{"ai_difficulty":"extreme"}')
                    self.assertEqual(status,400)
                finally:
                    ctl.close()

    def test_public_battleship_replay_excludes_private_fleets(self):
        from radio_arcade.practice_ai import fleet
        from radio_arcade.games.battleship import FLEET
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{"RADIO_ARCADE_HOME":folder}):
            e=PracticeService("PLAYER")
            try:
                sid=e.create_game("battleship-classic")
                e.place_battleship_fleet(sid,fleet(FLEET,random.Random(12)))
                snapshots=e.replay_frames(sid)
                self.assertTrue(snapshots)
                for frame in snapshots:
                    self.assertNotIn("private_fleet",frame["view"])
                    self.assertNotIn("practice_seats",frame["view"])
                    self.assertNotIn("ships",frame["view"])
            finally:e.close()
