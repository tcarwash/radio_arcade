"""Hand format, selective discovery replies, and fallback CQ in synthetic RF."""
from __future__ import annotations
import tempfile, unittest
from pathlib import Path

from radio_arcade.discovery import (
    Hand, Availability, encode_hand, decode_hand, game_bits,
    interested, pack_call, unpack_call, encode_request, decode_request,
    round_tag, response_channel,
)
from radio_arcade.service import ArcadeService
from radio_arcade.store import SessionStore
from radio_arcade.wire import decode, Kind


class FakeHandLink:
    supports_broadcast = True
    supports_hands = True
    hand_channels = 40
    supports_compact = False

    def __init__(self, mesh, call):
        self.mesh,self.call=mesh,call
        self.sent=[]
        self.hands=[]
        self.on_frame=None
        self.on_frame_with_ref=None
        self.status=None
        self.hand_handler=None

    def send(self, frame):
        self.sent.append(frame)
        for other in self.mesh.values():
            if other is not self:
                other.incoming.append((frame,{"id":11,"snr":-13.1}))

    def send_roll_call(self, frame,tag,channels,delay_s=3.0):
        self.round=decode(frame).sequence
        self.tag,self.channels=tag,channels
        self.send(frame)

    def send_hand(self, ref,tag,delay_s,channel,payload):
        self.hands.append((ref,tag,delay_s,channel,payload))
        return True

    def set_hand_handler(self,handler):
        self.hand_handler=handler

    def set_status_handler(self,handler):
        self.status=handler

    def close(self):
        pass

    def pump(self):
        while self.incoming:
            frame,ref=self.incoming.pop(0)
            if self.on_frame_with_ref:
                self.on_frame_with_ref(frame,ref)
            else:self.on_frame(frame)

    incoming = None


class DiscoveryTests(unittest.TestCase):
    def test_codec_and_unknown_calls(self):
        for call in ("AG7SU","K7ABC","W1AW","2E0ABC","N0CALL","K7ABC/P"):
            code=pack_call(call)
            self.assertEqual(unpack_call(code,(call,)),call)
        mask=game_bits([1,4,12,16,4])
        self.assertTrue(interested(mask,12))
        self.assertFalse(interested(mask,11))
        for status in Availability:
            msg=Hand("AG7SU",status,mask,-16,1)
            value=encode_hand(msg)
            self.assertLess(value,1<<63)
            self.assertEqual(decode_hand(value),msg)
        with self.assertRaises(ValueError): decode_hand(0)
        with self.assertRaises(ValueError):game_bits([17])
        with self.assertRaises(ValueError):decode_request(b"\x01\x03\x28")
        self.assertEqual(decode_request(encode_request(30,8)),(30,8))
        self.assertTrue(0<=response_channel("AG7SU",30,8)<8)
        self.assertEqual(round_tag("AG7SU",30),round_tag("ag7su",30))

    def test_many_stations_respond_with_own_preferences_and_nonresponders_stay_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            mesh={}
            def make(call,**kwargs):
                link=FakeHandLink(mesh,call);link.incoming=[]
                mesh[call]=link
                e=ArcadeService(call,link,SessionStore(Path(tmp)/call),**kwargs)
                return e
            a=make("AG7SU",discovery_auto_reply=True,discovery_legacy_cq=False)
            b=make("N0AAA",discovery_auto_reply=True,discovery_games=["battleship-classic"],
                   discovery_status="looking",discovery_legacy_cq=False)
            c=make("N0BBB",discovery_auto_reply=True,discovery_games=["nim"],
                   discovery_status="busy",discovery_legacy_cq=False)
            d=make("N0CCC",discovery_auto_reply=False,discovery_games=["battleship-classic"])
            try:
                a.call_cq("battleship-classic")
                for e in (b,c,d):e.transport.pump()
                self.assertEqual(len(b.transport.hands),1)
                self.assertEqual(len(c.transport.hands),1)
                self.assertEqual(len(d.transport.hands),0)
                round_id=a.transport.round
                for source in (b,c):
                    ref,tag,delay,channel,payload=source.transport.hands[0]
                    self.assertEqual(ref,11)
                    self.assertEqual(tag,a.transport.tag)
                    self.assertEqual(delay,3.0)
                    a._hand_event("heard",dict(round=round_id,channel=channel,payload=payload,
                                               snr=-8.2,channels=40))
                stations={s["callsign"]:s for s in a.nearby_players("battleship-classic")}
                self.assertEqual(len(stations),2)
                self.assertTrue(stations["N0AAA"]["available"])
                self.assertTrue(stations["N0AAA"]["interested"])
                self.assertFalse(stations["N0BBB"]["available"])
                self.assertFalse(stations["N0BBB"]["interested"])
                # A forged channel, stale round or unsupported payload is ignored.
                a._hand_event("heard",dict(round=round_id,channel=(channel+1)%40,
                                           payload=payload,snr=5,channels=40))
                self.assertEqual(len(a.nearby_players()),2)
                # Replay of same broadcast cannot solicit a second RF response.
                b.receive_with_ref(a.transport.sent[0],{"id":11,"snr":-10})
                self.assertEqual(len(b.transport.hands),1)
                c.set_tx_enabled(False)
                a.call_cq("nim")
                for e in (b,c,d):e.transport.pump()
                self.assertEqual(len(c.transport.hands),1)
            finally:
                for e in (a,b,c,d):e.close()

    def test_narrow_station_does_not_remap_a_wide_roll_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            mesh={}
            caller_link=FakeHandLink(mesh,"AG7SU");caller_link.incoming=[]
            caller_link.hand_channels=40
            mesh["AG7SU"]=caller_link
            caller=ArcadeService("AG7SU",caller_link,SessionStore(Path(tmp)/"wide"),
                                 discovery_legacy_cq=False)
            try:
                caller.call_cq("nim")
                number=caller_link.round
                # Find a valid callsign whose assigned channel is not in 500 Hz.
                candidate=next(f"N0A{chr(65+i//26)}{chr(65+i%26)}"
                               for i in range(40)
                               if response_channel(f"N0A{chr(65+i//26)}{chr(65+i%26)}",
                                                   number,40)>=8)
                narrow_link=FakeHandLink(mesh,candidate);narrow_link.incoming=[]
                narrow_link.hand_channels=8
                mesh[candidate]=narrow_link
                responder=ArcadeService(candidate,narrow_link,SessionStore(Path(tmp)/"narrow"),
                                        discovery_auto_reply=True)
                try:
                    narrow_link.incoming.append((caller_link.sent[0],{"id":11,"snr":-9}))
                    narrow_link.pump()
                    self.assertEqual(narrow_link.hands,[],
                                     "A 500 Hz responder must not remap a 40-channel request")
                finally:responder.close()
            finally:caller.close()

    def test_status_auto_while_playing_and_manual_busy_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            mesh={}
            link=FakeHandLink(mesh,"N0AAA");link.incoming=[]
            service=ArcadeService("N0AAA",link,SessionStore(Path(tmp)/"status"),
                                  discovery_status="looking")
            try:
                self.assertEqual(service._availability(),Availability.LOOKING)
                service._sessions["TEST"]={"status":"active","players":["N0AAA","N0BBB"]}
                self.assertEqual(service._availability(),Availability.IN_GAME)
                service.configure_discovery(auto_reply=False,status="busy",games=["nim"])
                self.assertEqual(service._availability(),Availability.BUSY)
            finally:service.close()

    def test_fallback_original_cq_without_hand_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            mesh={}
            link=FakeHandLink(mesh,"AG7SU");link.incoming=[]
            link.supports_hands=False
            e=ArcadeService("AG7SU",link,SessionStore(Path(tmp)/"calls"))
            try:
                e.call_cq("nim")
                frame=decode(link.sent[-1])
                self.assertEqual(frame.kind,Kind.CQ_REQUEST)
                self.assertEqual(frame.payload,b"")
                self.assertEqual(frame.sequence,0)
            finally:e.close()
