"""Data2G CWID command negotiation and application-ID fallback tests."""

from pathlib import Path
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from radio_arcade.connected import ConnectedConnection
from radio_arcade.kiss import KissConnection
from radio_arcade.service import ArcadeService
from radio_arcade.settings import Settings
from radio_arcade.store import SessionStore
from radio_arcade.transport_factory import BroadcastConnection, create_service
from radio_arcade.wire import Kind, decode


class CommandSocket:
    def __init__(self, reject_cwid=False):
        self.commands = []
        self.pending = bytearray()
        self.reject_cwid = reject_cwid

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        command = data.decode("ascii").strip()
        self.commands.append(command)
        if command.startswith("MYCALL "):
            reply = "OK\rREGISTERED AG7SU\r"
        elif command.startswith("CWID ") and self.reject_cwid:
            reply = "WRONG\r"
        elif command.startswith("BCAST OPEN "):
            reply = "BCAST PORT 2\r"
        else:
            reply = "OK\r"
        self.pending.extend(reply.encode("ascii"))

    def recv(self, n):
        if not self.pending:
            return b""
        data = bytes(self.pending[:n])
        del self.pending[:n]
        return data

    def shutdown(self, how):
        pass

    def close(self):
        pass


class CwidIntegrationTests(unittest.TestCase):
    def broadcast(self, reject=False, mode="auto"):
        command = CommandSocket(reject_cwid=reject)

        def fake_kiss_init(link, host, port, kiss_port, on_frame=None):
            link.closed = threading.Event()
            link.on_status = lambda *_: None
            link.on_frame = on_frame
            link.kiss_port = kiss_port

        with (patch("radio_arcade.transport_factory.socket.create_connection", return_value=command),
              patch.object(KissConnection, "__init__", fake_kiss_init),
              patch.object(KissConnection, "close", lambda link: link.closed.set()),
              patch.object(BroadcastConnection, "_drain_commands", lambda link: None)):
            link = BroadcastConnection("127.0.0.1", 8100, 8300, "RAGAMES",
                                       callsign="AG7SU", station_id_mode=mode, cw_wpm=20)
            link.close()
        return link, command.commands

    def test_broadcast_configures_real_cwid_before_opening_group(self):
        link, commands = self.broadcast()
        self.assertTrue(link.cwid_active)
        self.assertIsNone(link.cwid_warning)
        self.assertEqual(commands, ["MYCALL AG7SU", "DATA2G ON", "CWID ONLY 20",
                                    "BCAST OPEN RAGAMES"])

    def test_broadcast_cwid_rejection_keeps_application_id_fallback(self):
        link, commands = self.broadcast(reject=True)
        self.assertFalse(link.cwid_active)
        self.assertIn("using Radio Arcade IDs", link.cwid_warning)
        self.assertEqual(commands[-1], "BCAST OPEN RAGAMES")

    def test_opt_out_does_not_modify_modem_cwid_configuration(self):
        link, commands = self.broadcast(mode="application")
        self.assertFalse(link.cwid_active)
        self.assertEqual(commands, ["BCAST OPEN RAGAMES"])

    def connected(self, reject=False, mode="auto"):
        commands = []

        def fake_command(link, line, timeout=8.0):
            commands.append(line)
            if reject and line.startswith("CWID "):
                raise RuntimeError("Data2G rejected CWID")

        with (patch("radio_arcade.connected.socket.create_connection", side_effect=lambda *a, **k: CommandSocket()),
              patch.object(ConnectedConnection, "_read_commands", lambda link: None),
              patch.object(ConnectedConnection, "_read_data", lambda link: None),
              patch.object(ConnectedConnection, "_command", fake_command)):
            link = ConnectedConnection("127.0.0.1", 8300, "AG7SU",
                                       station_id_mode=mode, cw_wpm=25)
            link.close()
        return link, commands

    def test_connected_enables_cwid_after_data2g_on(self):
        link, commands = self.connected()
        self.assertTrue(link.cwid_active)
        self.assertEqual(commands, ["MYCALL AG7SU", "DATA2G ON", "CWID ONLY 25", "LISTEN ON"])

    def test_connected_unsupported_cwid_falls_back(self):
        link, commands = self.connected(reject=True)
        self.assertFalse(link.cwid_active)
        self.assertIsNotNone(link.cwid_warning)
        self.assertEqual(commands[-1], "LISTEN ON")

    def test_cwid_success_omits_application_identification_packets(self):
        class Radio:
            supports_compact = True
            cwid_active = True
            on_frame = None

            def __init__(self):
                self.frames = []

            def send(self, frame):
                self.frames.append(frame)

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            radio = Radio()
            engine = ArcadeService("AG7SU", radio, SessionStore(Path(directory) / "db.sqlite3"))
            try:
                engine.create_game("nim", hosted=True)
                self.assertFalse(engine.station_id_enabled)
                self.assertEqual(engine.identification_method, "Data2G CW")
                self.assertEqual([decode(frame).kind for frame in radio.frames], [Kind.HOST_ANNOUNCE])
            finally:
                engine.close()
            self.assertEqual([decode(frame).kind for frame in radio.frames], [Kind.HOST_ANNOUNCE])

    def test_settings_persist_cwid_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            Settings(callsign="AG7SU", station_id_mode="application", cw_wpm=28).save(path)
            restored = Settings.load(path)
            self.assertEqual((restored.station_id_mode, restored.cw_wpm), ("application", 28))

    def test_observer_and_initially_disabled_tx_do_not_configure_autonomous_cwid(self):
        class Radio:
            supports_compact = True
            cwid_active = False
            on_frame = None

            def send(self, frame):
                pass

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RADIO_ARCADE_HOME": directory}):
            with patch("radio_arcade.transport_factory.BroadcastConnection", return_value=Radio()) as constructor:
                observer = create_service(Settings(callsign="AG7SU", observer_mode=True))
                self.assertEqual(constructor.call_args.kwargs["station_id_mode"], "application")
                observer.close()
                disabled = create_service(Settings(callsign="AG7SU", tx_enabled=False))
                self.assertEqual(constructor.call_args.kwargs["station_id_mode"], "application")
                disabled.close()

    def test_reject_invalid_cw_settings_before_opening_a_socket(self):
        with patch("radio_arcade.transport_factory.BroadcastConnection") as constructor:
            for opts in ({"cw_wpm": 50}, {"station_id_mode": "disabled"}, {"cw_wpm": True}):
                with self.subTest(opts=opts), self.assertRaises(ValueError):
                    create_service(Settings(callsign="AG7SU", **opts))
            constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
