"""Multi-station hosted broadcast protocol regression checks."""

from pathlib import Path
import tempfile
import unittest

from radio_arcade.service import ArcadeService
from radio_arcade.store import SessionStore
from radio_arcade.wire import Kind, decode


class Air:
    def __init__(self):
        self.nodes = []
        self.frames = []
        self.drop = None

    def station(self):
        air = self

        class Radio:
            inbox = None
            on_frame = None

            def __init__(self):
                self.inbox = []
                air.nodes.append(self)

            def send(self, frame):
                packet = decode(frame)
                air.frames.append(packet)
                for radio in air.nodes:
                    if radio is not self and (air.drop is None or not air.drop(packet, radio)):
                        radio.inbox.append(frame)

            def pump(self):
                while self.inbox:
                    self.on_frame(self.inbox.pop(0))

            def close(self):
                pass

        return Radio()

    def pump(self):
        for _ in range(8):
            for node in self.nodes:
                node.pump()


class HostedScenarios(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.air = Air()
        self.engines = [ArcadeService(f"N0{chr(65+i)*3}", self.air.station(),
                                      SessionStore(Path(self.directory.name) / f"{i}.sqlite3"))
                        for i in range(4)]

    def tearDown(self):
        for engine in self.engines:
            engine.close()
        self.directory.cleanup()

    def start(self, game="nim"):
        dealer = self.engines[0]
        sid = dealer.create_game(game, hosted=True)
        self.air.pump()
        for guest in self.engines[1:]:
            self.assertEqual(guest.session(sid)["status"], "lobby")
            guest.join_game(sid)
            self.air.pump()
        dealer.start_game(sid)
        self.air.pump()
        return sid

    def test_joins_broadcast_roster_and_one_start(self):
        sid = self.start()
        for engine in self.engines:
            info = engine.session(sid)
            self.assertEqual(len(info["players"]), 4)
            self.assertTrue(info["hosted"])
            self.assertEqual(info["status"], "active")
        self.assertEqual(sum(packet.kind == Kind.START for packet in self.air.frames), 1)
        self.assertEqual(sum(packet.kind == Kind.HOST_ROSTER for packet in self.air.frames), 3)
        self.assertEqual(len(self.engines[0].store.due_outgoing(1e12)), 0)

    def test_one_broadcast_commit_and_canonical_turn(self):
        sid = self.start()
        dealer = self.engines[0]
        dealer.legal_action(sid, bytes((0, 1)))
        self.air.pump()
        self.assertEqual(sum(p.kind == Kind.COMMIT for p in self.air.frames), 1)
        for engine in self.engines:
            self.assertEqual(engine.session(sid)["revision"], 1)
            self.assertEqual(engine.session(sid)["current_player"], self.engines[1].callsign)
        self.engines[1].legal_action(sid, bytes((1, 1)))
        self.air.pump()
        for engine in self.engines:
            self.assertEqual(engine.session(sid)["revision"], 2)
        self.assertEqual(sum(p.kind == Kind.ACTION for p in self.air.frames), 1)
        self.assertEqual(sum(p.kind == Kind.COMMIT for p in self.air.frames), 2)
        with self.assertRaises(ValueError):
            self.engines[3].legal_action(sid, bytes((0, 1)))

    def test_lost_commit_manual_sync_and_pending_retry(self):
        sid = self.start()
        self.air.drop = lambda packet, radio: packet.kind == Kind.COMMIT and radio is self.air.nodes[3]
        self.engines[0].legal_action(sid, bytes((0, 1)))
        self.air.pump()
        self.assertEqual(self.engines[3].session(sid)["revision"], 0)
        self.air.drop = None
        self.engines[3].request_sync(sid)
        self.air.pump()
        self.assertEqual(self.engines[3].session(sid)["revision"], 1)
        # Simulate an ACTION lost on the way to the dealer.
        self.air.drop = lambda packet, radio: packet.kind == Kind.ACTION and radio is self.air.nodes[0]
        self.engines[1].legal_action(sid, bytes((1, 1)))
        self.air.pump()
        self.assertIsNotNone(self.engines[1].session(sid)["pending_action"])
        self.air.drop = None
        self.engines[1].resend_pending_action(sid)
        self.air.pump()
        self.assertEqual(self.engines[1].session(sid)["revision"], 2)
        self.assertIsNone(self.engines[1].session(sid)["pending_action"])

    def test_missed_start_recovers_from_explicit_snapshot(self):
        dealer = self.engines[0]
        guest = self.engines[1]
        sid = dealer.create_game("nim", hosted=True)
        self.air.pump()
        guest.join_game(sid)
        self.air.pump()
        self.air.drop = lambda packet, radio: packet.kind == Kind.START and radio is self.air.nodes[1]
        dealer.start_game(sid)
        self.air.pump()
        self.assertIsNone(guest.session(sid)["state"])
        self.air.drop = None
        guest.request_sync(sid)
        self.air.pump()
        self.assertEqual(guest.session(sid)["status"], "active")
        self.assertEqual(guest.session(sid)["view"]["piles"], [1, 3, 5, 7])

    def test_bad_remote_action_is_rejected_without_broadcast_commit(self):
        sid = self.start()
        self.engines[0].legal_action(sid, b"\x00\x01")
        self.air.pump()
        guest = self.engines[1]
        # Legal on the guest, but corrupt its transmitted action in transit.
        original_send = guest.transport.send
        def bad_action(frame):
            from radio_arcade.wire import Packet, encode, encode_action
            packet = decode(frame)
            if packet.kind == Kind.ACTION:
                packet = Packet(packet.game, packet.kind, packet.session,
                                packet.sequence, packet.revision, packet.source,
                                packet.destination, encode_action(guest.callsign, b"\xff\x01"))
                frame = encode(packet)
            original_send(frame)
        guest.transport.send = bad_action
        try:
            guest.legal_action(sid, b"\x01\x01")
            self.air.pump()
            self.assertEqual(self.engines[0].session(sid)["revision"], 1)
            self.assertEqual(guest.session(sid)["pending_action"], None)
            self.assertTrue(guest.session(sid)["error"])
            self.assertEqual(sum(p.kind == Kind.COMMIT for p in self.air.frames), 1)
        finally:
            guest.transport.send = original_send

    def test_hosted_chat_one_dealer_rebroadcast(self):
        sid = self.start()
        self.engines[2].send_chat(sid, "CQ from the table")
        self.air.pump()
        self.assertEqual(sum(p.kind == Kind.HOST_CHAT for p in self.air.frames), 1)
        for engine in self.engines:
            history = engine.chat_history(sid)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["body"], "CQ from the table")
            self.assertEqual(history[0]["sender"], self.engines[2].callsign)

    def test_join_is_rejected_after_start(self):
        dealer = self.engines[0]
        sid = dealer.create_game("nim", hosted=True)
        self.air.pump()
        self.engines[1].join_game(sid)
        self.air.pump()
        dealer.start_game(sid)
        self.air.pump()
        self.engines[2].join_game(sid)
        self.air.pump()
        self.assertEqual(len(dealer.session(sid)["players"]), 2)

    def test_dealer_controls_reply_order(self):
        dealer = self.engines[0]
        sid = dealer.create_game("nim", hosted=True)
        self.air.pump()
        for guest in self.engines[1:]:
            guest.join_game(sid)
            self.air.pump()
        names = [engine.callsign for engine in self.engines]
        chosen = [names[0], names[3], names[1], names[2]]
        dealer.set_reply_order(sid, ",".join(chosen))
        self.air.pump()
        for engine in self.engines:
            self.assertEqual(engine.session(sid)["players"], chosen)
        with self.assertRaises(ValueError):
            dealer.set_reply_order(sid, names[1:])
        dealer.start_game(sid)
        self.air.pump()
        dealer.legal_action(sid, b"\x00\x01")
        self.air.pump()
        self.assertEqual(dealer.session(sid)["current_player"], names[3])

    def test_observer_sees_hosted_lobby_and_broadcast_state(self):
        observer = ArcadeService("OBSERVER", self.air.station(),
                                 SessionStore(Path(self.directory.name) / "observer.sqlite3"), observer_mode=True)
        try:
            sid = self.start()
            observed = observer.session(sid)
            self.assertTrue(observed["hosted"])
            self.assertEqual(observed["status"], "active")
            self.engines[0].legal_action(sid, b"\x00\x01")
            self.air.pump()
            self.assertEqual(observer.session(sid)["revision"], 1)
            with self.assertRaises(ValueError):
                observer.join_game(sid)
        finally:
            observer.close()


