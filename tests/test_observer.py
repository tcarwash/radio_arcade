"""Passive spectator behavior over a shared broadcast frame stream."""
import tempfile
import unittest
from pathlib import Path

from radio_arcade import ArcadeService, SessionStore
from radio_arcade.wire import Kind, decode


class Radio:
    def __init__(self):
        self.nodes = []
        self.sent = []
        self.drop_observer_commit = False

    def attach(self, call, observer=False):
        link = Endpoint(self, call)
        store = SessionStore(Path(self.temp.name) / call)
        service = ArcadeService(call, link, store, observer_mode=observer)
        self.nodes.append((call, service))
        return service


class Endpoint:
    def __init__(self, radio, call):
        self.radio, self.call = radio, call
        self.on_frame = lambda frame: None

    def send(self, frame):
        packet = decode(frame)
        self.radio.sent.append((self.call, packet.kind))
        for call, service in self.radio.nodes:
            if call != self.call:
                if call == "N0OBS" and packet.kind == Kind.COMMIT and self.radio.drop_observer_commit:
                    continue
                service.receive_frame(frame)

    def close(self):
        pass


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.radio = Radio()
        self.radio.temp = self.temp
        self.host = self.radio.attach("N0AAA")
        self.guest = self.radio.attach("N0BBB")
        self.observer = self.radio.attach("N0OBS", observer=True)

    def tearDown(self):
        for _, service in self.radio.nodes:
            service.close()
        self.temp.cleanup()

    def start(self):
        sid = self.host.create_game("tic-tac-toe", "N0BBB")
        self.guest.accept_invite(sid)
        return sid

    def test_passive_start_and_move_never_transmit(self):
        sid = self.start()
        self.assertEqual(self.observer.session(sid)["players"], ["N0AAA", "N0BBB"])
        self.host.legal_action(sid, b"\x04")
        self.assertEqual(self.observer.session(sid)["view"]["cells"][4], "X")
        self.assertFalse(any(call == "N0OBS" for call, _ in self.radio.sent))
        with self.assertRaises(ValueError):
            self.observer.create_game("tic-tac-toe")
        with self.assertRaises(ValueError):
            self.observer.send_chat(sid, "hello")
        with self.assertRaises(ValueError):
            self.observer.legal_action(sid, b"\x00")

    def test_late_discovery_and_explicit_sync(self):
        # Start before the observer tunes to the group.
        observer = self.radio.nodes.pop()[1]
        sid = self.start()
        self.radio.nodes.append(("N0OBS", observer))
        self.host.legal_action(sid, b"\x04")
        session = observer.session(sid)
        self.assertEqual(session["status"], "observing")
        self.assertIsNone(session["view"])
        self.assertFalse(any(call == "N0OBS" for call, _ in self.radio.sent))
        observer.request_sync(sid)
        self.assertEqual([kind for call, kind in self.radio.sent if call == "N0OBS"],
                         [Kind.SYNC_REQUEST])
        self.assertEqual(observer.session(sid)["view"]["cells"][4], "X")

    def test_missed_commit_has_no_automatic_sync(self):
        sid = self.start()
        self.radio.drop_observer_commit = True
        self.host.legal_action(sid, b"\x04")
        self.radio.drop_observer_commit = False
        self.guest.legal_action(sid, b"\x03")
        self.assertIn("Missed moves", self.observer.session(sid)["error"])
        self.assertFalse(any(call == "N0OBS" for call, _ in self.radio.sent))
        self.observer.request_sync(sid)
        self.assertEqual(self.observer.session(sid)["revision"], 2)
        self.assertEqual(self.observer.session(sid)["error"], "")


if __name__ == "__main__":
    unittest.main()
