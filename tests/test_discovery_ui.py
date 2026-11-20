"""Qt station-arrival animations follow fresh decoded Hands, not periodic refreshes."""
from pathlib import Path
import os
import tempfile
import time
import unittest

from radio_arcade.discovery import Availability, Hand, encode_hand, game_bits, response_channel
from radio_arcade.service import ArcadeService
from radio_arcade.settings import Settings
from radio_arcade.store import SessionStore
from tests.test_discovery import FakeHandLink

try:
    from PySide6.QtWidgets import QApplication
    HAS_QT=True
except ImportError:
    HAS_QT=False


@unittest.skipUnless(HAS_QT,"requires desktop extra")
class DiscoveryDesktopTests(unittest.TestCase):
    def test_new_player_bounces_once_and_busy_player_does_not(self):
        from radio_arcade.desktop import ArcadeWindow
        os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            mesh={}
            link=FakeHandLink(mesh,"AG7SU");link.incoming=[];mesh["AG7SU"]=link
            service=ArcadeService("AG7SU",link,SessionStore(Path(folder)/"engine"),
                                  discovery_auto_reply=False,discovery_legacy_cq=False)
            window=ArcadeWindow(Settings(callsign="AG7SU",transport="broadcast"))
            window.resize(1366,900);window.show();window._service_ready(service)
            try:
                service.call_cq("nim")
                number=link.round
                game_id=service.registry.by_key("nim").game_id
                def deliver(call,status):
                    word=encode_hand(Hand(call,status,game_bits([game_id]),-10,1))
                    service._hand_event("heard",dict(round=number,channel=response_channel(call,number,40),
                                  channels=40,payload=word,snr=-7.2))
                    for _ in range(3):app.processEvents()
                    window._refresh_cq()
                    app.processEvents()
                deliver("N0AAA",Availability.LOOKING)
                self.assertEqual(window.discovery_list.count(),1)
                self.assertEqual(len(window._discovery_animations),1)
                item=window.discovery_list.item(0)
                self.assertLess(item.sizeHint().height(),34)
                deadline=time.monotonic()+1.25
                while window._discovery_animations and time.monotonic()<deadline:
                    app.processEvents();time.sleep(.025)
                self.assertFalse(window._discovery_animations)
                self.assertEqual(item.sizeHint().height(),33)
                window._refresh_cq();app.processEvents()
                self.assertFalse(window._discovery_animations,"Polling restarted the bounce")
                deliver("N0BBB",Availability.BUSY)
                self.assertEqual(window.discovery_list.count(),2)
                self.assertFalse(window._discovery_animations,"Busy arrival should remain understated")
                self.assertEqual(window.discovery_list.item(1).data(256),"N0BBB")
            finally:
                window.close();service.close();app.processEvents()
