"""A hull is revealed only after sinking, never from an unsunk private fleet."""
import os
import random
import tempfile
import unittest
from unittest.mock import patch

from radio_arcade.naval_reveals import encode_reveal, decode_reveal, revealed_enemy_ships
from radio_arcade.practice import PracticeService
from radio_arcade.games.sea_warfare import FLEET as SEA_FLEET
from radio_arcade.practice_ai import fleet
from tests.test_battleship import FLEET_A,FLEET_B
from tests.test_sessions import SessionScenarios


class Reveals(unittest.TestCase):
    def test_malformed_and_premature_disclosures_not_projected(self):
        encoded=encode_reveal(12,4,[90,91],9)
        self.assertEqual(decode_reveal(12,encoded),dict(revision=9,id=4,cells=[90,91]))
        for cells in ([90,92],[90,100],[90,91,91],[],[-1,0]):
            with self.assertRaises(ValueError):encode_reveal(12,4,cells,9)
        for text in ("NAVSINK1:9:4:90,92","NAVSINK1:9:20:90,91","NAVSINK1:x:4:90,91","NAVSINK1:9:4:90,<script>"):
            with self.assertRaises(ValueError):decode_reveal(12,text)
        records=[{"sender":"B","body":encoded.encode()}]
        self.assertEqual(revealed_enemy_ships(12,records,["A","B"],"A",8,[0]*100),[])
        shots=[0]*100
        shots[90]=2
        self.assertEqual(revealed_enemy_ships(12,records,["A","B"],"A",9,shots),[])
        shots[91]=7
        self.assertEqual(revealed_enemy_ships(12,records,["A","B"],"A",9,shots),[dict(id=4,cells=[90,91])])
        self.assertEqual(revealed_enemy_ships(12,records,["A","B"],"B",9,shots),[])
        self.assertEqual(revealed_enemy_ships(12,records,["A","B"],"OBSERVER",9,shots),[])

    def test_classic_practice_sinks_reveal_only_destroyed_vessel(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{"RADIO_ARCADE_HOME":d}),patch("radio_arcade.practice.random_fleet",return_value=FLEET_B):
            e=PracticeService("N0AAA")
            try:
                sid=e.create_game("battleship-classic")
                e.place_battleship_fleet(sid,FLEET_A)
                e.legal_action(sid,b"\x5a")
                self.assertFalse(e.session(sid).get("revealed_enemy_ships"))
                e.legal_action(sid,b"\x5b")
                s=e.session(sid)
                self.assertEqual(s["revealed_enemy_ships"],[dict(id=4,cells=[90,91])])
                self.assertNotIn("ships",str(s["view"]))
                self.assertEqual(len([m for m in e.opaque_state_history(sid) if m["body"].startswith(b"NAVSINK1:")]),1)
                self.assertEqual(e.session(sid)["revealed_enemy_ships"],s["revealed_enemy_ships"])
            finally:e.close()

    def test_sea_warfare_private_sunk_ship_is_disclosed(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{"RADIO_ARCADE_HOME":d}):
            e=PracticeService("PLAYER")
            try:
                sid=e.create_game("sea-warfare")
                e.place_battleship_fleet(sid,fleet(SEA_FLEET,random.Random(4)))
                private=e.store.load_private_game_data(sid)
                ai=private["practice_seats"]["1"]
                ship=ai["ships"][5]
                ai["hits"]=ship[:-1]
                ai["fighter_used"]=True
                e.store.save_private_game_data(sid,private)
                self.assertFalse(e.session(sid).get("revealed_enemy_ships"))
                e.legal_action(sid,bytes((ship[-1],)))
                self.assertEqual(e.session(sid)["revealed_enemy_ships"],[dict(id=5,cells=sorted(ship))])
                self.assertEqual(e.session(sid)["view"]["shots"][ship[-1]],2)
            finally:e.close()

    def test_radio_sunk_reveal_via_existing_opaque_channel(self):
        harness=SessionScenarios();harness.setUp()
        try:
            host,guest=harness.engines
            sid=host.create_game("battleship-classic","N0BBB")
            harness.pump();guest.accept_invite(sid);harness.pump()
            guest.place_battleship_fleet(sid,FLEET_B);harness.pump()
            host.place_battleship_fleet(sid,FLEET_A);harness.pump()
            host.legal_action(sid,b"\x5a");harness.pump()
            self.assertFalse(host.session(sid).get("revealed_enemy_ships"))
            guest.legal_action(sid,b"\x00");harness.pump()
            host.legal_action(sid,b"\x5b");harness.pump()
            self.assertEqual(host.session(sid)["revealed_enemy_ships"],[dict(id=4,cells=[90,91])])
            self.assertEqual(guest.session(sid)["revealed_enemy_ships"],[])
            self.assertEqual(len(host.session(sid)["state"]),104)
        finally:harness.tearDown()
