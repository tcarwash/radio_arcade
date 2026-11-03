"""Cartridge-owned rules, two-station Data2G simulation and browser API."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from radio_arcade.games.pico8 import CONFIG, EVENT, GAME_ID, Pico8CartridgeGame
from radio_arcade.service import ArcadeService
from radio_arcade.store import SessionStore
from radio_arcade.settings import Settings
from radio_arcade.web import WebController
from radio_arcade.practice import PracticeService
from tests.test_hosted import Air


def configuration(mode=0):
    return bytes((CONFIG, 0x12, 0x34, 0x56, 0x78, 0, 1, mode))


class CartridgeGameTests(unittest.TestCase):
    players = ("AG7SU", "K7ABC")

    def test_codec_action_validation_and_finish(self):
        game = Pico8CartridgeGame()
        initial = game.new_state(self.players)
        with self.assertRaisesRegex(ValueError, "configure"):
            game.apply_action(initial, self.players, "AG7SU", bytes((EVENT, 3)))
        selected = game.apply_action(initial, self.players, "AG7SU", configuration())
        self.assertEqual(game.decode_state(game.encode_state(selected)), selected)
        with self.assertRaises(ValueError):
            game.apply_action(selected, self.players, "K7ABC", configuration())
        moved = game.apply_action(selected, self.players, "AG7SU", bytes((EVENT, 42)))
        self.assertEqual(game.current_player(moved, self.players), "K7ABC")
        with self.assertRaisesRegex(ValueError, "turn"):
            game.apply_action(moved, self.players, "AG7SU", bytes((EVENT, 42)))
        finished = game.apply_action(moved, self.players, "AG7SU", b"\x02")
        self.assertTrue(finished["finished"])
        self.assertIsNone(game.current_player(finished, self.players))

    def test_event_mode_permits_any_player(self):
        game = Pico8CartridgeGame()
        state = game.apply_action(game.new_state(self.players), self.players,
                                  "AG7SU", configuration(mode=1))
        self.assertTrue(game.can_act(state, self.players, "K7ABC"))
        state = game.apply_action(state, self.players, "K7ABC", b"\x01\x03")
        self.assertEqual(state["events"], 1)
        self.assertEqual(state["turn"], 0)


class CartridgeRadioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.air = Air()
        self.host = ArcadeService("AG7SU", self.air.station(),
                                  SessionStore(Path(self.tmp.name) / "host.sqlite3"))
        self.guest = ArcadeService("K7ABC", self.air.station(),
                                   SessionStore(Path(self.tmp.name) / "guest.sqlite3"))

    def tearDown(self):
        self.host.close()
        self.guest.close()
        self.tmp.cleanup()

    def start(self):
        sid = self.host.create_game("pico8-cartridge", hosted=True)
        self.air.pump()
        self.guest.join_game(sid)
        self.air.pump()
        self.host.start_game(sid)
        self.air.pump()
        return sid

    def test_committed_events_are_durable_and_broadcast(self):
        sid = self.start()
        for service, action in ((self.host, configuration()),
                                (self.host, b"\x01\x42"), (self.guest, b"\x01\x37")):
            service.legal_action(sid, action)
            self.air.pump()
        self.assertEqual(self.host.session(sid)["state"], self.guest.session(sid)["state"])
        for service in (self.host, self.guest):
            events = service.store.load_pico8_actions(sid)
            self.assertEqual([x["revision"] for x in events], [1, 2, 3])
            self.assertEqual([x["action"] for x in events],
                             [configuration(), b"\x01\x42", b"\x01\x37"])
        self.guest.request_sync(sid)
        self.air.pump()
        self.assertEqual(self.host.session(sid)["view"], self.guest.session(sid)["view"])

    def test_event_mode_non_turn_action_and_checkpoint_side_channel(self):
        sid = self.start()
        self.host.legal_action(sid, configuration(mode=1))
        self.air.pump()
        self.guest.legal_action(sid, b"\x01\x10")
        self.air.pump()
        self.guest.legal_action(sid, b"\x01\x11")
        self.air.pump()
        self.assertEqual(self.guest.session(sid)["revision"], 3)

        self.guest.send_opaque_state(sid, "AG7SU", "P8:REQ:a1")
        self.air.pump()
        self.assertEqual(self.host.store.load_pico8_opaque(sid)[-1]["body"], "P8:REQ:a1")
        self.assertEqual(self.guest.store.load_pico8_opaque(sid)[-1]["delivery"], "delivered")
        self.host.send_opaque_state(sid, "K7ABC", "P8:S:a1:3:0:1:ffff:AA==")
        self.air.pump()
        self.assertEqual(self.guest.store.load_pico8_opaque(sid)[-1]["sender"], "AG7SU")
        self.assertEqual(self.host.store.load_pico8_opaque(sid)[-1]["delivery"], "delivered")


class CartridgeWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"RADIO_ARCADE_HOME": self.tmp.name})
        self.environment.start()
        self.controller = WebController(
            Settings(callsign="AG7SU", transport="practice"),
            service_factory=lambda settings: PracticeService(settings.callsign),
            settings_path=Path(self.tmp.name) / "settings.json")

    def tearDown(self):
        self.controller.close()
        self.environment.stop()
        self.tmp.cleanup()

    def request(self, method, route, data=None):
        status, value = self.controller.dispatch(method, route, json.dumps(data).encode() if data else b"")
        self.assertEqual(status, 200, value)
        return value

    def test_rest_api_create_config_send_and_replay(self):
        created = self.request("POST", "/api/pico8/create")
        sid = created["session_id"]
        state = self.request("GET", "/api/pico8/sessions")
        self.assertEqual(state["sessions"][0]["game_id"], GAME_ID)
        self.request("POST", "/api/pico8/action", {"session_id": sid,
                                                     "hex": configuration().hex()})
        self.request("POST", "/api/pico8/action", {"session_id": sid,
                                                     "hex": "0105"})
        events = self.request("GET", f"/api/pico8/events?session_id={sid}&after=0")
        self.assertEqual([event["hex"] for event in events["events"]],
                         [configuration().hex(), "0105"])
        since_one = self.request("GET", f"/api/pico8/events?session_id={sid}&after=1")
        self.assertEqual(len(since_one["events"]), 1)
        status, error = self.controller.dispatch("POST", "/api/pico8/action",
                                                 json.dumps({"session_id": sid, "hex": ""}).encode())
        self.assertEqual(status, 400)
