"""Exercise the *real* Radio Arcade hosted service with external Radio Civ rules."""

from pathlib import Path
import tempfile
import unittest

from radio_arcade.games.base import GameRegistry
from radio_arcade.store import SessionStore
from radio_arcade.wire import Kind, decode, decode_action, decode_snapshot, encode
from radio_civ.engine import RadioCivService
from radio_civ.rules import (CivilizationGame, MAX_CITIES, MAX_UNITS, build,
                             end_turn, grow, turn_bundle)


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
            self.engines.append(RadioCivService(call, self.air.radio(),
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
        self.assertEqual(dealer.session(sid)["revision"], 0)
        self.assertEqual(dealer.session(sid)["queued_orders"], 1)
        self.assertEqual(sum(p.kind == Kind.COMMIT for p in self.air.frames), 0)
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
            self.assertEqual(engine.session(sid)["revision"], 3)
        self.assertEqual(sum(p.kind == Kind.COMMIT for p in self.air.frames), 3)

    def test_dropped_commit_recovers_full_map_and_cities(self):
        sid = self.start_game()
        dealer, second, third = self.engines
        self.air.drop = lambda packet, receiver: (packet.kind == Kind.COMMIT and
                                                  receiver is self.air.radios[2])
        dealer.legal_action(sid, build(0, 0))
        dealer.legal_action(sid, end_turn())
        self.air.pump()
        self.assertEqual(third.session(sid)["revision"], 0)
        self.air.drop = None
        third.request_sync(sid)
        self.air.pump()
        self.assertEqual(third.session(sid)["view"], dealer.session(sid)["view"])
        self.assertEqual(third.session(sid)["revision"], 1)

    def test_guest_sends_one_batched_action_only_when_ending_turn(self):
        sid = self.start_game()
        dealer, guest, _ = self.engines
        dealer.legal_action(sid, end_turn())
        self.air.pump()
        before = len(self.air.frames)

        guest.legal_action(sid, build(1, 0))

        self.assertEqual(len(self.air.frames), before)
        self.assertEqual(guest.session(sid)["queued_orders"], 1)
        guest.legal_action(sid, end_turn())
        actions = [packet for packet in self.air.frames[before:] if packet.kind == Kind.ACTION]
        self.assertEqual(len(actions), 1)
        submitted = decode_action(actions[0].payload)[1]
        self.assertEqual(submitted[0], ord("P"))
        self.assertIsNotNone(guest.session(sid)["pending_action"])
        self.assertEqual(guest.session(sid)["view"]["turn"], "N7XYZ")
        guest.resend_pending_action(sid)
        retried = [packet for packet in self.air.frames[before:] if packet.kind == Kind.ACTION]
        self.assertEqual(decode_action(retried[1].payload)[1], submitted)

        self.air.pump()
        self.assertEqual(dealer.session(sid)["revision"], 2)
        self.assertEqual(guest.session(sid)["revision"], 2)
        self.assertEqual(sum(packet.kind == Kind.COMMIT for packet in self.air.frames), 2)
        self.assertEqual(dealer.session(sid)["view"]["cities"][1]["buildings"], 1)

    def test_two_queued_orders_commit_together_once(self):
        sid = self.start_game()
        for engine in self.engines:
            row = engine._sessions[sid]
            game = engine.registry.by_id(5)
            state = game.decode_state(row["state"])
            state["players"][0]["food"] = 20
            state["players"][0]["wood"] = 12
            row["state"] = game.encode_state(state)
            engine._save(row)
        dealer = self.engines[0]

        dealer.legal_action(sid, build(0, 0))
        dealer.legal_action(sid, grow(0))

        self.assertEqual(dealer.session(sid)["queued_orders"], 2)
        self.assertEqual(dealer.session(sid)["view"]["cities"][0]["pop"], 2)
        self.assertEqual(dealer.session(sid)["revision"], 0)
        self.assertEqual(sum(packet.kind == Kind.COMMIT for packet in self.air.frames), 0)
        dealer.legal_action(sid, end_turn())
        commits = [packet for packet in self.air.frames if packet.kind == Kind.COMMIT]
        action = decode_action(commits[-1].payload)[1]
        self.assertEqual(action[0], ord("P"))
        self.assertEqual(action[1], 2)

        self.air.pump()
        for engine in self.engines:
            self.assertEqual(engine.session(sid)["revision"], 1)
            self.assertEqual(engine.session(sid)["view"]["cities"][0]["pop"], 2)

    def test_sync_snapshot_fits_when_last_move_is_a_full_turn_bundle(self):
        long_calls = ("LONGCALL01", "LONGCALL02", "LONGCALL03", "LONGCALL04")
        game = CivilizationGame()
        state = game.new_state(long_calls)
        state["cities"] = [dict(tile=i * 2, owner=i % 4, pop=5, hp=3, buildings=15)
                            for i in range(MAX_CITIES)]
        state["units"] = [dict(tile=24 + i, owner=i % 4, kind=i % 3, hp=3)
                           for i in range(MAX_UNITS)]
        body = game.encode_state(state)
        engine = RadioCivService(long_calls[0], self.air.radio(),
                                 SessionStore(Path(self.temp.name) / "long.sqlite3"),
                                 GameRegistry())
        self.engines.append(engine)
        row = {"id": "0000000000000001", "game_id": 5, "players": list(long_calls),
               "state": body, "sequence": 1, "revision": 1, "hosted": True,
               "last_actor": long_calls[0],
               "last_action": turn_bundle((b"M\x01\x02", b"M\x01\x02"))}

        engine._send_snapshot(row, long_calls[1])

        self.assertLessEqual(len(encode(self.air.frames[-1])), 240)
        self.assertEqual(decode_snapshot(self.air.frames[-1].payload), (long_calls, body, None))


if __name__ == "__main__":
    unittest.main()
