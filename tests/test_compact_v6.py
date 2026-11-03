"""Live compact turn transport, recovery, and station ID regressions."""

from pathlib import Path
import threading
import tempfile
import time
import unittest

from radio_arcade.service import ArcadeService, ID_INTERVAL_SECONDS
from radio_arcade.store import SessionStore
from radio_arcade.wire import CompactFrame, Kind, Packet, decode, encode, encode_compact


class Air:
    def __init__(self):
        self.radios = []
        self.frames = []
        self.drop = None

    def station(self):
        air = self

        class Radio:
            supports_compact = True
            on_frame = None

            def __init__(self):
                self.inbox = []
                air.radios.append(self)

            def send(self, frame):
                air.frames.append((self, frame))
                for target in air.radios:
                    if target is not self and (air.drop is None or not air.drop(decode(frame), target)):
                        target.inbox.append(frame)

            def pump(self):
                while self.inbox:
                    self.on_frame(self.inbox.pop(0))

            def close(self):
                pass

        return Radio()

    def pump(self):
        for _ in range(12):
            for radio in self.radios:
                radio.pump()


class CompactServiceScenarios(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.air = Air()
        self.engines = [ArcadeService(f"N0{chr(65 + i) * 3}", self.air.station(),
                                      SessionStore(Path(self.directory.name) / f"{i}.sqlite3"))
                        for i in range(4)]

    def tearDown(self):
        for engine in self.engines:
            engine.close()
        self.directory.cleanup()

    def directed(self):
        host, guest = self.engines[:2]
        sid = host.create_game("tic-tac-toe", guest.callsign)
        self.air.pump()
        guest.accept_invite(sid)
        self.air.pump()
        return sid

    def hosted(self):
        dealer = self.engines[0]
        sid = dealer.create_game("nim", hosted=True)
        self.air.pump()
        for guest in self.engines[1:]:
            guest.join_game(sid)
            self.air.pump()
        dealer.start_game(sid)
        self.air.pump()
        return sid

    def test_two_player_guest_turn_is_34_bytes_in_four_frames(self):
        sid = self.directed()
        host, guest = self.engines[:2]
        host.legal_action(sid, b"\x00")
        self.air.pump()
        self.air.frames.clear()
        guest.legal_action(sid, b"\x03")
        self.air.pump()
        compact = [decode(raw) for _, raw in self.air.frames if isinstance(decode(raw), CompactFrame)]
        self.assertEqual([frame.kind for frame in compact],
                         [Kind.ACTION, Kind.COMMIT, Kind.ACK, Kind.ACK])
        self.assertEqual([len(raw) for _, raw in self.air.frames], [9, 9, 8, 8])
        self.assertEqual([frame.acknowledged_kind for frame in compact[2:]],
                         [Kind.ACTION, Kind.COMMIT])
        self.assertEqual(host.session(sid)["state"], guest.session(sid)["state"])
        self.assertEqual(host.session(sid)["revision"], 2)
        self.assertFalse(host.store.due_outgoing(time.time() + 1000))
        self.assertFalse(guest.store.due_outgoing(time.time() + 1000))

    def test_four_player_hosted_turns_have_ten_byte_moves(self):
        sid = self.hosted()
        dealer = self.engines[0]
        self.air.frames.clear()
        dealer.legal_action(sid, bytes((0, 1)))
        self.air.pump()
        self.engines[1].legal_action(sid, bytes((1, 1)))
        self.air.pump()
        self.assertEqual([decode(raw).kind for _, raw in self.air.frames],
                         [Kind.COMMIT, Kind.ACTION, Kind.COMMIT])
        self.assertEqual([len(raw) for _, raw in self.air.frames], [10, 10, 10])
        self.assertEqual([decode(raw).seat for _, raw in self.air.frames], [0, 1, 1])
        self.assertEqual({engine.session(sid)["revision"] for engine in self.engines}, {2})

    def test_lost_compact_commit_recovers_with_full_snapshot(self):
        sid = self.hosted()
        dealer = self.engines[0]
        self.air.drop = lambda packet, target: packet.kind == Kind.COMMIT and target is self.air.radios[3]
        dealer.legal_action(sid, bytes((0, 1)))
        self.air.pump()
        self.assertEqual(self.engines[3].session(sid)["revision"], 0)
        self.air.drop = None
        self.engines[3].request_sync(sid)
        self.air.pump()
        self.assertEqual(self.engines[3].session(sid)["revision"], 1)
        self.assertTrue(any(isinstance(decode(raw), Packet) and decode(raw).kind == Kind.SNAPSHOT
                            for _, raw in self.air.frames))

    def test_ambiguous_short_session_falls_back_and_rejects_incoming(self):
        sid = self.directed()
        host = self.engines[0]
        row = dict(host._sessions[sid])
        other_sid = f"{(int(sid, 16) ^ (1 << 32)):016X}"
        row["id"] = other_sid
        host._sessions[other_sid] = row
        self.air.frames.clear()
        host.legal_action(sid, b"\x00")
        self.air.pump()
        self.assertTrue(any(isinstance(decode(raw), Packet) and decode(raw).kind == Kind.COMMIT
                            for _, raw in self.air.frames))
        errors = []
        host.subscribe(lambda event: errors.append(event))
        host.receive_frame(encode_compact(Kind.COMMIT, int(sid, 16), 1, 0, b"\x00"))
        self.assertTrue(any(event.name == "protocol_error" for event in errors))

    def test_identifies_initially_at_nine_minutes_and_on_close(self):
        sid = self.directed()
        host, guest = self.engines[:2]
        ids = [(radio, decode(raw)) for radio, raw in self.air.frames
               if decode(raw).kind == Kind.STATION_ID]
        self.assertEqual(len(ids), 2)
        for radio, packet in ids:
            self.assertEqual(packet.payload, b"ID " + packet.source.encode("ascii"))
        host._last_id_at = time.monotonic() - ID_INTERVAL_SECONDS - 1
        self.air.frames.clear()
        host.legal_action(sid, b"\x00")
        self.air.pump()
        self.assertEqual([decode(raw).kind for radio, raw in self.air.frames if radio is host.transport][:2],
                         [Kind.STATION_ID, Kind.COMMIT])
        self.air.frames.clear()
        host.close()
        self.assertEqual([decode(raw).kind for radio, raw in self.air.frames if radio is host.transport],
                         [Kind.STATION_ID])

    def test_idle_open_game_does_not_beacon_indefinitely(self):
        self.directed()
        host = self.engines[0]
        host._last_id_at = time.monotonic() - ID_INTERVAL_SECONDS - 1
        host._sent_since_id = False
        self.air.frames.clear()
        time.sleep(0.65)
        self.assertEqual(self.air.frames, [])

    def test_active_traffic_gets_periodic_id_without_another_move(self):
        self.directed()
        host = self.engines[0]
        host._last_id_at = time.monotonic() - ID_INTERVAL_SECONDS - 1
        host._sent_since_id = True
        self.air.frames.clear()
        time.sleep(0.65)
        self.assertEqual([decode(raw).kind for radio, raw in self.air.frames
                          if radio is host.transport], [Kind.STATION_ID])
        self.assertFalse(host._sent_since_id)

    def test_failed_station_id_prevents_unidentified_game_transmission(self):
        dealer = self.engines[0]
        normal_send = dealer.transport.send

        def fail_id(frame):
            if decode(frame).kind == Kind.STATION_ID:
                raise OSError("modem ID unavailable")
            normal_send(frame)

        dealer.transport.send = fail_id
        sid = dealer.create_game("nim", hosted=True)
        self.assertEqual(self.air.frames, [])
        dealer.transport.send = normal_send
        dealer.announce_game(sid)
        self.assertEqual([decode(raw).kind for radio, raw in self.air.frames
                          if radio is dealer.transport],
                         [Kind.STATION_ID, Kind.HOST_ANNOUNCE])

    def test_explicit_observer_sync_is_identified_even_without_compact(self):
        observer = ArcadeService("W7OBS", self.air.station(),
                                 SessionStore(Path(self.directory.name) / "observer.sqlite3"),
                                 observer_mode=True)
        try:
            sid = self.hosted()
            self.assertFalse(observer.compact_turns)
            self.assertTrue(observer.station_id_enabled)
            self.air.frames.clear()
            observer.request_sync(sid)
            outgoing = [decode(raw).kind for radio, raw in self.air.frames
                        if radio is observer.transport]
            self.assertEqual(outgoing, [Kind.STATION_ID, Kind.SYNC_REQUEST])
        finally:
            observer.close()

    def test_connected_data_stream_accepts_compact_and_full_station_id(self):
        from radio_arcade.connected import ConnectedConnection

        class Socket:
            def __init__(self):
                self.frames = []

            def sendall(self, data):
                self.frames.append(data)

        link = ConnectedConnection.__new__(ConnectedConnection)
        link.connected = threading.Event()
        link.connected.set()
        link.peer = "K7ABC"
        link._send_lock = threading.Lock()
        link._data = Socket()
        move = encode_compact(Kind.ACTION, 42, 1, 0, b"\x00")
        station_id = encode(Packet(1, Kind.STATION_ID, 0, 0, 0, "AG7SU", "*", b"ID AG7SU"))
        for frame in (station_id, move):
            link.send(frame)
        self.assertEqual(link._data.frames,
                         [len(frame).to_bytes(2, "big") + frame for frame in (station_id, move)])
        with self.assertRaises(ConnectionError):
            link.send(encode(Packet(1, Kind.CHAT, 42, 1, 1, "AG7SU", "N0WRONG", b"hi")))

    def test_passive_observer_reconstructs_compact_broadcast_commits(self):
        observer = ArcadeService("W7OBS", self.air.station(),
                                 SessionStore(Path(self.directory.name) / "passive.sqlite3"),
                                 observer_mode=True)
        try:
            sid = self.hosted()
            self.assertEqual(observer.session(sid)["revision"], 0)
            self.engines[0].legal_action(sid, bytes((0, 1)))
            self.air.pump()
            self.assertEqual(observer.session(sid)["revision"], 1)
            self.assertEqual(observer.session(sid)["state"], self.engines[0].session(sid)["state"])
        finally:
            observer.close()


if __name__ == "__main__":
    unittest.main()
