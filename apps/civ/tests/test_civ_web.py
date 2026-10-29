"""Loopback HTTP API tests: browser and native UI use identical game rules."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from radio_arcade.store import SessionStore
from radio_civ.engine import HotseatService
from radio_civ.web import WebServer


class WebClientBackend(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        engine = HotseatService("AG7SU", SessionStore(Path(self.temp.name) / "practice.db"))
        self.engine = engine
        self.server = WebServer(("127.0.0.1", 0), engine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.engine.close()
        self.temp.cleanup()

    def fetch(self, path, method="GET", data=None, headers=None):
        options = {"headers": headers or {}}
        if method == "POST":
            options["data"] = json.dumps(data or {}).encode()
            options["headers"] = {"Content-Type": "application/json", **options["headers"]}
        request = Request(self.url + path, method=method, **options)
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read())

    def test_browser_hosts_engine_campaign_and_uses_same_actions(self):
        self.assertEqual(self.fetch("/api/config")["local"], True)
        created = self.fetch("/api/campaigns", "POST")
        sid = created["session"]["id"]
        listed = self.fetch("/api/sessions")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["view"]["kind"], "civilization")
        self.assertRegex(listed[0]["view"]["players"][0]["seen"], r"^[0-9A-F]{16}$")
        self.assertEqual(listed[0]["view"]["players"][0]["seen"],
                         f"{self.engine.session(sid)['view']['players'][0]['seen']:016X}")
        order = self.fetch(f"/api/sessions/{sid}/action", "POST", {"bytes": [66, 0, 0]})
        self.assertEqual(order["session"]["revision"], 1)
        self.assertEqual(self.engine.session(sid)["view"]["cities"][0]["buildings"], 1)
        self.fetch(f"/api/sessions/{sid}/action", "POST", {"bytes": [69]})
        self.assertEqual(self.fetch(f"/api/sessions/{sid}")["view"]["turn"], "LOCAL2")
        self.assertEqual(self.fetch(f"/api/sessions/{sid}/chat"), [])

    def test_static_client_and_safe_error_responses(self):
        for path, fragment in (("/", b"RADIO"), ("/app.js", b"ArcadeService"),
                               ("/style.css", b".panels")):
            with urlopen(self.url + path, timeout=3) as response:
                self.assertIn(fragment, response.read())
        with self.assertRaises(HTTPError) as bad:
            self.fetch("/api/campaigns", "POST", headers={"Origin": "https://evil.example"})
        self.assertEqual(bad.exception.code, 403)
        with self.assertRaises(HTTPError) as bad:
            self.fetch("/api/sessions/0000000000000001")
        self.assertEqual(bad.exception.code, 404)
        sid = self.fetch("/api/campaigns", "POST")["session"]["id"]
        with self.assertRaises(HTTPError) as bad:
            self.fetch(f"/api/sessions/{sid}/action", "POST", {"bytes": [999]})
        self.assertEqual(bad.exception.code, 400)
        with self.assertRaises(HTTPError) as bad:
            self.fetch(f"/api/sessions/{sid}/action", "POST", {"bytes": [77, 250, 1]})
        self.assertEqual(bad.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
