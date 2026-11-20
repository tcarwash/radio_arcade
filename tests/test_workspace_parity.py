"""Shared status presentation, first run, and local tile drop validation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio_arcade.word_draft import drop_rack_tile
from radio_arcade.settings import Settings
from radio_arcade.web import WebController
from radio_arcade.practice import PracticeService
from radio_arcade.service import ArcadeService
from radio_arcade.store import SessionStore
from tests.test_hosted import Air

class WorkspaceParity(unittest.TestCase):
    def test_first_run_without_station_name(self):
        with tempfile.TemporaryDirectory() as d,patch.dict("os.environ",{"RADIO_ARCADE_HOME":d}):
            web=WebController(Settings(),service_factory=lambda s:PracticeService(s.callsign),settings_path=Path(d)/"settings.json")
            try:
                status,result=web.dispatch("POST","/api/settings",json.dumps({"transport":"practice","callsign":""}).encode())
                self.assertEqual(status,200,result);self.assertEqual(result["callsign"],"PLAYER")
                status,_=web.dispatch("POST","/api/settings",b'{"transport":"broadcast"}')
                self.assertEqual(status,400)
            finally:web.close()

    def test_actual_delivery_status(self):
        with tempfile.TemporaryDirectory() as d:
            air=Air()
            host=ArcadeService("N0AAA",air.station(),SessionStore(Path(d)/"host.db"))
            guest=ArcadeService("N0BBB",air.station(),SessionStore(Path(d)/"guest.db"))
            web=WebController(Settings(callsign="N0AAA",transport="broadcast"),service_factory=lambda s:host)
            try:
                sid=host.create_game("tic-tac-toe","N0BBB")
                def feedback():return web.dispatch("GET","/api/session/"+sid,b"")[1]["session"]["feedback"]["text"]
                self.assertIn("Invite",feedback())
                air.pump();guest.accept_invite(sid);air.pump()
                self.assertIn("Your turn",feedback())
                host.legal_action(sid,b"\x00")
                self.assertIn("acknowledgement",feedback())
                air.pump();self.assertIn("Move confirmed",feedback())
            finally:web.close();guest.close()

    def test_drop_uses_origin_and_extends_only_contiguously(self):
        cells=[""]*225
        self.assertEqual(drop_rack_tile("A1","H","","C",cells,112),("H8","C"))
        self.assertEqual(drop_rack_tile("H8","H","C","A",cells,113),("H8","CA"))
        with self.assertRaises(ValueError):drop_rack_tile("H8","H","C","A",cells,114)
        cells[112]="A"
        self.assertEqual(drop_rack_tile("A1","H","","T",cells,113),("H8","AT"))
        with self.assertRaises(ValueError):drop_rack_tile("A1","H","","T",cells,112)
        self.assertEqual(drop_rack_tile("O14","V","C","{A}",cells,224),("O14","C{A}"))
        with self.assertRaises(ValueError):drop_rack_tile("O15","H","C","A",cells,0)
