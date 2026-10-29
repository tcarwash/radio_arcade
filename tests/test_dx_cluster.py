import io
import json
import unittest
from unittest.mock import patch

from radio_arcade.dx_cluster import build_spot, post_spot
from radio_arcade.settings import Settings


SESSION = {"id": "ABC123", "game": "open-word-tiles", "hosted": True,
           "coordinator": "AG7SU", "status": "lobby"}


class DXClusterTests(unittest.TestCase):
    def test_payload(self):
        spot = build_spot(SESSION, "AG7SU", "14070", "Data2G USB", "Join me")
        self.assertEqual(spot["game"], "Open Word Tiles")
        self.assertEqual(spot["session"], "ABC123")
        self.assertEqual(spot["frequency_khz"], 14070)

    def test_only_own_hosted_tables(self):
        for change in ({"hosted": False}, {"coordinator": "W7XYZ"}, {"status": "finished"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_spot({**SESSION, **change}, "AG7SU", "14070", "USB")

    def test_invalid_frequency(self):
        for value in ("14.070", "", "nan", "0", "3000001"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_spot(SESSION, "AG7SU", value, "USB")

    def test_post_contract(self):
        payload = build_spot(SESSION, "AG7SU", "14070", "USB")
        with patch("radio_arcade.dx_cluster.urlopen") as opener:
            response = io.BytesIO(json.dumps({"spot": {"id": "abc"}}).encode())
            opener.return_value.__enter__.return_value = response
            result = post_spot("https://cluster.example.org/", payload)
            req = opener.call_args.args[0]
            self.assertEqual(req.full_url, "https://cluster.example.org/api/spots")
            self.assertEqual(json.loads(req.data), payload)
            self.assertEqual(result["spot"]["id"], "abc")

    def test_settings_round_trip(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            Settings(dx_cluster_url="https://cluster.example.org").save(path)
            self.assertEqual(Settings.load(path).dx_cluster_url, "https://cluster.example.org")


if __name__ == "__main__":
    unittest.main()
