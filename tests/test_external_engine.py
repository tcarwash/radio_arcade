"""Generic external-engine sessions, without any LÖVE or Godot installation."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from radio_arcade.games.external import ExternalGame
from radio_arcade.engine_checkpoint import CheckpointAssembler, checkpoint_chunks, checkpoint_request
from radio_arcade.games.base import builtin_registry
from radio_arcade.practice import PracticeService
from radio_arcade.settings import Settings
from radio_arcade.web import WebController
from tests.test_hosted import Air
from radio_arcade.service import ArcadeService
from radio_arcade.store import SessionStore


def config(mode=0):
    return bytes.fromhex("00123456780001") + bytes([mode])


class ExternalGameTests(unittest.TestCase):
    def test_builtin_game_keeps_pico8_identity_separate(self):
        registry = builtin_registry(load_plugins=False)
        external = registry.by_key("external-game")
        self.assertIsInstance(external, ExternalGame)
        self.assertEqual(external.game_id, 10)
        self.assertEqual(external.view_model(external.new_state(("AG7SU", "K7ABC")),
                                             ("AG7SU", "K7ABC"))["kind"], "external-game")
        self.assertEqual(registry.by_key("pico8-cartridge").game_id, 9)

    def test_generic_sessions_can_exchange_radio_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            air = Air()
            host = ArcadeService("AG7SU", air.station(), SessionStore(Path(directory) / "h.sqlite"))
            guest = ArcadeService("K7ABC", air.station(), SessionStore(Path(directory) / "g.sqlite"))
            try:
                sid = host.create_game("external-game", hosted=True)
                air.pump()
                guest.join_game(sid)
                air.pump()
                host.start_game(sid)
                air.pump()
                for engine, packet in ((host, config()), (host, b"\x01\x10"),
                                       (guest, b"\x01\x11")):
                    engine.legal_action(sid, packet)
                    air.pump()
                self.assertEqual(host.session(sid)["state"], guest.session(sid)["state"])
                for engine in (host, guest):
                    events = engine.store.load_engine_actions(sid)
                    self.assertEqual([e["revision"] for e in events], [1, 2, 3])
                    self.assertEqual([e["action"] for e in events],
                                     [config(), b"\x01\x10", b"\x01\x11"])
                guest.send_opaque_state(sid, "AG7SU", "RA:CHECKPOINT:7:AA==")
                air.pump()
                self.assertEqual(host.store.load_engine_opaque(sid)[-1]["body"],
                                 "RA:CHECKPOINT:7:AA==")
                self.assertEqual(guest.store.load_engine_opaque(sid)[-1]["delivery"],
                                 "delivered")
            finally:
                host.close()
                guest.close()

    def test_checkpoint_chunks_have_acknowledged_radio_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            air = Air()
            host = ArcadeService("AG7SU", air.station(), SessionStore(Path(directory) / "h.sqlite"))
            guest = ArcadeService("K7ABC", air.station(), SessionStore(Path(directory) / "g.sqlite"))
            try:
                sid = host.create_game("external-game", hosted=True)
                air.pump()
                guest.join_game(sid)
                air.pump()
                host.start_game(sid)
                air.pump()
                guest.send_opaque_state(sid, "AG7SU", checkpoint_request("deadbeef"))
                air.pump()
                self.assertEqual(host.store.load_engine_opaque(sid)[-1]["body"],
                                 "RA:REQ:deadbeef")
                payload = bytes(range(256))
                chunks = checkpoint_chunks("deadbeef", 2, payload)
                for chunk in chunks:
                    sequence = host.send_opaque_state(sid, "K7ABC", chunk)
                    air.pump()
                    self.assertEqual(host.store.engine_opaque_delivery(sid, "AG7SU", sequence),
                                     "delivered")
                assembler = CheckpointAssembler("deadbeef")
                complete = None
                for message in guest.store.load_engine_opaque(sid):
                    if message["sender"] == "AG7SU":
                        complete = assembler.accept(message["body"]) or complete
                self.assertEqual(complete, (2, payload))
            finally:
                host.close()
                guest.close()


class ExternalBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"RADIO_ARCADE_HOME": self.tmp.name})
        self.env.start()
        self.controller = WebController(
            Settings(callsign="AG7SU", transport="practice"),
            service_factory=lambda settings: PracticeService(settings.callsign),
            settings_path=Path(self.tmp.name, "settings.json"))

    def tearDown(self):
        self.controller.close()
        self.env.stop()
        self.tmp.cleanup()

    def api(self, method, route, data=None, status=200):
        encoded = json.dumps(data).encode() if data is not None else b""
        result_code, result = self.controller.dispatch(method, route, encoded)
        self.assertEqual(result_code, status, result)
        return result

    def test_create_configure_replay_finish_and_estimate(self):
        created = self.api("POST", "/api/engine/create", {})
        sid = created["session_id"]
        sessions = self.api("GET", "/api/engine/sessions")
        self.assertEqual(len(sessions["sessions"]), 1)
        self.assertEqual(sessions["sessions"][0]["game_id"], 10)
        self.api("POST", "/api/engine/action", {"session_id": sid, "hex": config().hex()})
        self.api("POST", "/api/engine/action", {"session_id": sid, "hex": "010a0b"})
        events = self.api("GET", f"/api/engine/events?session_id={sid}&after=0")
        self.assertEqual([e["hex"] for e in events["events"]],
                         [config().hex(), "010a0b"])
        self.assertEqual(len(self.api("GET", f"/api/engine/events?session_id={sid}&after=1")["events"]), 1)
        estimate = self.api("POST", "/api/engine/estimate", {
            "session_id": sid, "payload_bytes": 2, "baud": 600,
            "bits_per_symbol": 3, "code_rate": 0.5})
        self.assertEqual(estimate["game_payload_bytes"], 2)
        self.assertEqual(estimate["payload_bytes"], 3)  # one-byte EVENT tag
        self.assertGreater(estimate["estimated_airtime_ms"], 0)
        self.api("POST", "/api/engine/action", {"session_id": sid, "hex": "02"})
        self.assertEqual(self.api("GET", f"/api/engine/events?session_id={sid}")["session"]["status"], "finished")

    def test_invalid_and_cross_engine_requests(self):
        sid = self.api("POST", "/api/engine/create", {})["session_id"]
        self.api("POST", "/api/engine/action", {"session_id": sid, "hex": ""}, 400)
        self.api("POST", "/api/engine/action", {"session_id": sid, "hex": "0"}, 400)
        self.api("POST", "/api/engine/control", {"session_id": sid, "command": "delete_game"}, 400)
        self.api("GET", f"/api/engine/events?session_id={sid}&after=-1", status=400)
        self.api("POST", "/api/engine/estimate", {
            "session_id": sid, "payload_bytes": 2, "baud": 0, "bits_per_symbol": 1}, 400)
        self.api("POST", "/api/engine/opaque", {
            "session_id": sid, "target": "K7ABC", "message": "P8:wrong"}, 400)
        other = self.api("POST", "/api/pico8/create", {})["session_id"]
        self.api("GET", f"/api/engine/events?session_id={other}", status=400)
        self.api("GET", f"/api/pico8/events?session_id={sid}", status=400)

    def test_side_channel_filtered_and_not_transmitted_in_estimate(self):
        sid = self.api("POST", "/api/engine/create", {})["session_id"]
        before = self.controller.engine.store.load_engine_opaque(sid)
        self.api("POST", "/api/engine/estimate", {
            "session_id": sid, "payload_bytes": 2, "baud": 600,
            "bits_per_symbol": 3})
        self.assertEqual(self.controller.engine.store.load_engine_opaque(sid), before)
        sent = self.api("POST", "/api/engine/opaque", {
            "session_id": sid, "target": "LOCAL1", "message": "RA:save:AAAA"})
        messages = self.api("GET", f"/api/engine/opaque?session_id={sid}")["messages"]
        self.assertEqual(messages[0]["body"], "RA:save:AAAA")
        self.assertGreater(messages[0]["created"], 0)
        self.assertEqual(self.api("GET", f"/api/engine/delivery?session_id={sid}&sequence={sent['sequence']}")["state"], "pending")


if __name__ == "__main__":
    unittest.main()
