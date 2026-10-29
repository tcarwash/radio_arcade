"""Exercise the *real* Radio Arcade hosted service with external Radio Civ rules."""

from pathlib import Path
import tempfile
import unittest

from radio_arcade.games.base import GameRegistry
from radio_arcade.service import ArcadeService
from radio_arcade.store import SessionStore
from radio_arcade.wire import Kind, decode
from radio_civ.rules import CivilizationGame, build, end_turn


class Air:
    def __init__(self):
        self.radios = []
        self.frames = []
        self.drop = None

    def radio(self):
        air = self

        class Radio:
            def __init__(self):
                self.inbox = []
                self.on_frame = lambda frame: None
                air.radios.append(self)

            def send(self, frame):
                packet = decode(frame)
                air.frames.append(packet)
                for receiver in air.radios:
                    if receiver is not self and (air.drop is None or not air.drop(packet, receiver)):
                        receiver.inbox.append(frame)

            def pump(self):
                while self.inbox:
                    self.on_frame(self.inbox.pop(0))

            def close(self):
                return None

        return Radio()

    def pump(self):
        for _ in range(10):
            for radio in self.radios:
                radio.pump()


class HostedCivilization(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.air = Air()
        self.engines = []
        for i, call in enumerate(("AG7SU", "K7ABC", "N7XYZ")):
            games = GameRegistry()
            games.register(CivilizationGame())
            self.engines.append(ArcadeService(call, self.air.radio(),
                                SessionStore(Path(self.temp.name) / f"{i}.sqlite3"), games))

    def tearDown(self):
        for engine in self.engines:
            engine.close()
        self.temp.cleanup()

    def start_game(self):
        dealer = self.engines[0]
        sid = dealer.create_game("radio-civ", hosted=True)
        self.air.pump()
        for guest in self.engines[1:]:
            guest.join_game(sid)
            self.air.pump()
        dealer.start_game(sid)
        self.air.pump()
        return sid

    def test_three_station_broadcast_and_deterministic_turns(self):
        sid = self.start_game()
        dealer, second, third = self.engines
        for engine in self.engines:
            self.assertEqual(engine.session(sid)["game_id"], 5)
            self.assertEqual(engine.session(sid)["view"]["round"], 1)
            self.assertEqual(len(engine.session(sid)["view"]["terrain"]), 64)
        self.assertEqual(sum(p.kind == Kind.START for p in self.air.frames), 1)
        dealer.legal_action(sid, build(0, 0))
        self.air.pump()
        dealer.legal_action(sid, end_turn())
        self.air.pump()
        self.assertEqual(second.session(sid)["current_player"], second.callsign)
        second.legal_action(sid, end_turn())
        self.air.pump()
        third.legal_action(sid, end_turn())
        self.air.pump()
        for engine in self.engines:
            self.assertEqual(engine.session(sid)["view"]["round"], 2)
            self.assertEqual(engine.session(sid)["revision"], 4)
        self.assertEqual(sum(p.kind == Kind.COMMIT for p in self.air.frames), 4)

    def test_dropped_commit_recovers_full_map_and_cities(self):
        sid = self.start_game()
        dealer, second, third = self.engines
        self.air.drop = lambda packet, receiver: (packet.kind == Kind.COMMIT and
                                                  receiver is self.air.radios[2])
        dealer.legal_action(sid, build(0, 0))
        self.air.pump()
        self.assertEqual(third.session(sid)["revision"], 0)
        self.air.drop = None
        third.request_sync(sid)
        self.air.pump()
        self.assertEqual(third.session(sid)["view"], dealer.session(sid)["view"])
        self.assertEqual(third.session(sid)["revision"], 1)


if __name__ == "__main__":
    unittest.main()
