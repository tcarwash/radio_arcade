"""Offline web fixture with a deterministic, simulated Hand responder."""
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from radio_arcade.discovery import Hand, Availability, game_bits, encode_hand, response_channel
from radio_arcade.web import WebController, make_server
from radio_arcade.settings import Settings
from radio_arcade.service import ArcadeService
from radio_arcade.store import SessionStore
from radio_arcade.wire import decode


class DemoHandHost:
    supports_hands=True
    hand_channels=40
    supports_broadcast=True
    supports_compact=False

    def __init__(self):
        self.last_frame=None
        self.on_frame=None
        self.on_frame_with_ref=None
        self.hand_handler=None

    def set_hand_handler(self,callback):self.hand_handler=callback
    def set_status_handler(self,callback):pass
    def send(self,frame):self.last_frame=frame
    def send_roll_call(self,frame,tag,channels,delay_s):
        self.last_frame=frame
        round_id=decode(frame).sequence
        call="N0AAA"
        payload=encode_hand(Hand(call,Availability.LOOKING,game_bits([12,7]),-11,1))
        self.hand_handler("heard",dict(round=round_id,channels=channels,
                          channel=response_channel(call,round_id,channels),
                          payload=payload,snr=-8.5,dt=124))
        self.hand_handler("end",dict(round=round_id))
    def send_hand(self,*args):return True
    def close(self):pass


def main():
    with TemporaryDirectory() as folder:
        settings=Settings(callsign="AG7SU",transport="broadcast",discovery_auto_reply=False)
        link=DemoHandHost()
        engine=ArcadeService("AG7SU",link,SessionStore(Path(folder)/"engine"),
                             discovery_auto_reply=False)
        controller=WebController(settings,service_factory=lambda _:engine,
                                 settings_path=Path(folder)/"settings.json")
        server=make_server(controller,"127.0.0.1",8765)
        print("discovery fixture ready",flush=True)
        try:server.serve_forever()
        finally:server.server_close();controller.close()


if __name__=="__main__":main()
