"""Deterministic, isolated practice server for the word / artillery browser walkthrough."""
from tempfile import TemporaryDirectory
from unittest.mock import patch

from radio_arcade.web import WebController, make_server
from radio_arcade.settings import Settings
from radio_arcade.practice import PracticeService


def main():
    with TemporaryDirectory() as folder:
        controller=WebController(Settings(callsign="N0TEST",transport="practice"),
                                 service_factory=lambda settings:PracticeService(settings.callsign),
                                 settings_path=folder+"/settings.json")
        server=make_server(controller,"127.0.0.1",8765)
        try:
            # This deterministic seed gives the local player the opening turn and a rack
            # containing a verified two-letter word for the repeatable UI test.
            with patch("radio_arcade.games.scrabble.secrets.randbits",return_value=2):
                server.serve_forever()
        finally:
            server.server_close()
            controller.close()


if __name__ == "__main__":
    main()
