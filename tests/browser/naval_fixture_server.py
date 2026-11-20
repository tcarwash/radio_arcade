"""Disposable, deterministic naval match with one confirmed sunk ship per game."""
from unittest.mock import patch
from tempfile import TemporaryDirectory
from radio_arcade.web import WebController,make_server
from radio_arcade.practice import PracticeService
from radio_arcade.settings import Settings
from radio_arcade.games.sea_warfare import FLEET as SEA_FLEET
from radio_arcade.practice_ai import fleet
from tests.test_battleship import FLEET_A,FLEET_B

def main():
    with TemporaryDirectory() as folder:
        engine=PracticeService("N0AAA")
        with patch("radio_arcade.practice.random_fleet",side_effect=lambda lengths:
                   FLEET_B if len(lengths)==5 else fleet(SEA_FLEET)):
            sid=engine.create_game("battleship-classic")
            engine.place_battleship_fleet(sid,FLEET_A)
            engine.legal_action(sid,bytes((90,)))
            engine.legal_action(sid,bytes((91,)))
            other=engine.create_game("sea-warfare")
            engine.place_battleship_fleet(other,fleet(SEA_FLEET))
        private=engine.store.load_private_game_data(other)
        ai=private["practice_seats"]["1"]
        sunk=ai["ships"][5]
        ai["hits"]=sunk[:-1]
        ai["fighter_used"]=True
        engine.store.save_private_game_data(other,private)
        engine.legal_action(other,bytes((sunk[-1],)))
        controller=WebController(Settings(callsign="N0AAA",transport="practice"),
                                  service_factory=lambda settings:engine)
        server=make_server(controller,"127.0.0.1",8765)
        print("naval fixture ready",sid,other,flush=True)
        try:server.serve_forever()
        finally:server.server_close();controller.close()

if __name__=="__main__":
    main()
