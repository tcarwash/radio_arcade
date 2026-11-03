"""Actual localhost HTTP behavior for the browser cartridge launcher."""

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from radio_arcade.practice import PracticeService
from radio_arcade.settings import Settings
from radio_arcade.web import WebController, make_server


class Pico8HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"RADIO_ARCADE_HOME": self.tmp.name,
                                             "RADIO_ARCADE_PICO8_CARTS": self.tmp.name})
        self.env.start()
        Path(self.tmp.name, "game.html").write_text(
            "<html><body><script>var pico8_gpio = new Array(128);</script></body></html>", encoding="utf-8")
        Path(self.tmp.name, "game.js").write_text("const game = 1;", encoding="utf-8")
        self.controller = WebController(
            Settings(callsign="AG7SU", transport="practice"),
            service_factory=lambda settings: PracticeService(settings.callsign),
            settings_path=Path(self.tmp.name, "settings.json"))
        self.server = make_server(self.controller, port=0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.controller.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_serves_launcher_and_injects_bridge_into_export(self):
        with urlopen(self.url + "/pico8/") as response:
            self.assertIn(b"PICO-8", response.read())
        with urlopen(self.url + "/pico8/carts/game.html") as response:
            contents = response.read()
            policy = response.headers["Content-Security-Policy"]
            self.assertIn(b'/pico8/bridge.js', contents)
            self.assertIn(b'var pico8_gpio', contents)
            self.assertIn("wasm-unsafe-eval", policy)
        with urlopen(self.url + "/pico8/bridge.js") as response:
            self.assertIn(b"Pico8Bridge", response.read())
        with urlopen(self.url + "/api/pico8/carts") as response:
            self.assertEqual(json.load(response)["carts"], ["game.html"])

    def test_disallows_traversal_and_unconfigured_files(self):
        for path in ("/pico8/carts/..%2Fsecret.html", "/pico8/carts/missing.html",
                     "/pico8/carts/game.py"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(self.url + path)
            self.assertEqual(error.exception.code, 404)
