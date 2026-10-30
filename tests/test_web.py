"""Web client API scenarios using the real local-practice game service."""
from itertools import permutations
import json
import os
import tempfile
import threading
import unittest
from urllib.request import urlopen
from unittest.mock import patch

from radio_arcade.practice import PracticeService
from radio_arcade.settings import Settings
from radio_arcade.web import WebController, make_server
from radio_arcade.games.scrabble import _lexicon


class WebControllerScenarios(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"RADIO_ARCADE_HOME": self.directory.name})
        self.environment.start()
        self.controller = WebController(
            Settings(callsign="N0CALL", transport="practice"),
            service_factory=lambda settings: PracticeService(settings.callsign),
            settings_path=os.path.join(self.directory.name, "settings.json"),
        )

    def tearDown(self):
        self.controller.close()
        self.environment.stop()
        self.directory.cleanup()

    def request(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        status, value = self.controller.dispatch(method, path, body)
        return status, value

    def test_bootstrap_exposes_catalog_and_saved_practice_identity(self):
        status, value = self.request("GET", "/api/state")

        self.assertEqual(status, 200)
        self.assertEqual(value["callsign"], "N0CALL")
        self.assertEqual(value["transport"], "practice")
        self.assertEqual({game["key"] for game in value["catalog"]},
                         {"tic-tac-toe", "nim", "open-word-tiles", "connect-four", "scorched-earth"})

    def test_web_settings_preserve_shared_client_preferences(self):
        self.controller.settings.sound_enabled = True
        self.controller.settings.dx_cluster_url = "https://cluster.example.org"
        status, state = self.request("POST", "/api/settings", {
            "callsign": "N0CALL", "transport": "practice", "color_scheme": "daylight"
        })
        self.assertEqual(status, 200)
        self.assertTrue(state["sound_enabled"])
        self.assertEqual(state["dx_cluster_url"], "https://cluster.example.org")
        status, state = self.request("POST", "/api/settings", {
            "sound_enabled": False, "dx_cluster_url": "https://other.example.org"
        })
        self.assertEqual(status, 200)
        self.assertFalse(state["sound_enabled"])
        self.assertEqual(state["dx_cluster_url"], "https://other.example.org")

    def test_create_play_and_chat_round_trip_through_web_api(self):
        status, created = self.request("POST", "/api/command", {
            "command": "create_game", "game": "tic-tac-toe"})
        self.assertEqual(status, 200)
        sid = created["session_id"]

        status, result = self.request("POST", "/api/command", {
            "command": "legal_action", "session_id": sid, "action": "00"})
        self.assertEqual(status, 200)
        self.assertEqual(result["session"]["view"]["cells"][0], "X")
        self.assertNotIn("state", result["session"])
        self.assertNotIn("pending_action", result["session"])
        json.dumps(result)
        json.dumps(self.request("GET", "/api/state")[1])

        status, result = self.request("POST", "/api/command", {
            "command": "send_chat", "session_id": sid, "text": "Hello from the browser"})
        self.assertEqual(status, 200)
        self.assertEqual(result["chat"][-1]["body"], "Hello from the browser")

    def test_open_word_tiles_placement_uses_game_validation_and_commits(self):
        _, created = self.request("POST", "/api/command", {
            "command": "create_game", "game": "open-word-tiles"})
        sid = created["session_id"]
        session = self.controller.engine.session(sid)
        game = self.controller.engine.registry.by_id(3)
        state = game.decode_state(session["state"])
        game.validate_roster(state, tuple(session["players"]))
        rack = "".join(chr(64 + tile) for tile in state["racks"][state["turn"]] if tile not in (0, 31))
        word = next(("".join(chars) for length in range(2, len(rack) + 1)
                     for chars in permutations(rack, length)
                     if "".join(chars) in _lexicon()), None)
        self.assertIsNotNone(word)
        start_x = 7 - (len(word) - 1) // 2

        status, result = self.request("POST", "/api/command", {
            "command": "place_word", "session_id": sid,
            "start": f"{chr(65 + start_x)}8", "direction": "H", "word": word})

        self.assertEqual(status, 200)
        self.assertEqual(result["session"]["revision"], 1)
        self.assertTrue(any(result["session"]["view"]["cells"]))

    def test_http_server_serves_the_web_client_and_json_state(self):
        server = make_server(self.controller, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/") as response:
                page = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("THE ARENA", page)
                self.assertIn("/app.js", page)
            with urlopen(base + "/api/state") as response:
                state = json.load(response)
                self.assertEqual(state["callsign"], "N0CALL")
                self.assertEqual(response.headers["Content-Type"], "application/json; charset=utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_connect_four_action_updates_the_generic_plugin_view(self):
        _, created = self.request("POST", "/api/command", {
            "command": "create_game", "game": "connect-four"})
        sid = created["session_id"]
        self.assertEqual(created["session"]["view"]["kind"], "connect-four")

        status, result = self.request("POST", "/api/command", {
            "command": "legal_action", "session_id": sid, "action": "00"})

        self.assertEqual(status, 200)
        self.assertEqual(result["session"]["revision"], 1)
        self.assertEqual(result["session"]["view"]["kind"], "connect-four")
        self.assertEqual(result["session"]["view"]["cells"][35], "X")

    def test_invalid_action_and_unknown_command_return_clear_client_errors(self):
        _, created = self.request("POST", "/api/command", {
            "command": "create_game", "game": "tic-tac-toe"})
        sid = created["session_id"]

        status, value = self.request("POST", "/api/command", {
            "command": "legal_action", "session_id": sid, "action": "not-hex"})
        self.assertEqual(status, 400)
        self.assertIn("error", value)

        status, value = self.request("POST", "/api/command", {"command": "delete_everything"})
        self.assertEqual(status, 400)
        self.assertIn("error", value)

    def test_settings_save_restarts_service_and_persists_validated_configuration(self):
        status, value = self.request("POST", "/api/settings", {
            "callsign": "n0web", "host": "127.0.0.1", "transport": "practice",
            "group": "RAGAMES", "command_port": 8300, "kiss_port": 8100,
            "observer_mode": False})

        self.assertEqual(status, 200)
        self.assertEqual(value["callsign"], "N0WEB")
        with open(os.path.join(self.directory.name, "settings.json"), encoding="utf-8") as saved:
            self.assertEqual(json.load(saved)["callsign"], "N0WEB")


if __name__ == "__main__":
    unittest.main()
