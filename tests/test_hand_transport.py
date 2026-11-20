"""Real socket-pair contract tests: KISS REFS, ACKMODE and Data2G HAND ordering.

No modem or RF transmissions. A synthetic host speaks Data2G's documented
ASCII command protocol and KISS frames to the actual BroadcastConnection.
"""
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from radio_arcade.discovery import Availability, Hand, encode_hand, encode_request, response_channel, round_tag
from radio_arcade.kiss import Decoder, encode
from radio_arcade.service import ArcadeService
from radio_arcade.store import SessionStore
from radio_arcade.transport_factory import BroadcastConnection
from radio_arcade.wire import Kind, Packet, decode as packet_decode, encode as packet_encode


class SyntheticData2G:
    def __init__(self, version="0.5.1"):
        self.version=version
        self.cmd_client,self.cmd_host=socket.socketpair()
        self.kiss_client,self.kiss_host=socket.socketpair()
        self.events=[]
        self._stop=threading.Event()
        self._lock=threading.Lock()
        self.cmd_thread=threading.Thread(target=self._commands,daemon=True)
        self.kiss_thread=threading.Thread(target=self._kiss,daemon=True)
        self.cmd_thread.start();self.kiss_thread.start()

    def log(self,item):
        with self._lock:self.events.append(item)

    def _commands(self):
        pending=bytearray()
        try:
            while data:=self.cmd_host.recv(4096):
                pending.extend(data)
                while b"\r" in pending:
                    raw,_,pending=pending.partition(b"\r")
                    cmd=raw.decode("ascii").strip()
                    self.log(("CMD",cmd))
                    if cmd=="VERSION":reply="VERSION Data2G "+self.version
                    elif cmd.startswith("BCAST OPEN "):reply="BCAST PORT 2"
                    elif cmd.startswith("HAND LISTEN ") and self.version=="0.4.3":reply="WRONG"
                    else:reply="OK"
                    self.cmd_host.sendall(reply.encode()+b"\r")
        except OSError:pass

    def _kiss(self):
        decoder=Decoder()
        try:
            while data:=self.kiss_host.recv(4096):
                for port,command,payload in decoder.feed_all(data):
                    self.log(("KISS",port,command,payload))
        except OSError:pass

    def emit_status(self,line):
        self.cmd_host.sendall(line.encode("ascii")+b"\r")

    def emit_data(self,frame, ref_id=17,snr=-12):
        self.kiss_host.sendall(encode(f"\x11REF {ref_id} 245.00 0.0 {snr}".encode(),0,6)+encode(frame,2))

    def wait_for(self,condition,timeout=2):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            with self._lock: events=list(self.events)
            if condition(events):return events
            time.sleep(.015)
        with self._lock:return list(self.events)

    def close(self):
        for s in (self.cmd_client,self.cmd_host,self.kiss_client,self.kiss_host):
            try:s.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            s.close()
        self.cmd_thread.join(timeout=1)
        self.kiss_thread.join(timeout=1)


class RealTransportTests(unittest.TestCase):
    def make(self,version="0.5.1"):
        host=SyntheticData2G(version)
        connections=iter((host.cmd_client,host.kiss_client))
        patcher=patch("radio_arcade.transport_factory.socket.create_connection",
                      side_effect=lambda *args,**kwargs:next(connections))
        patcher.start()
        try:
            transport=BroadcastConnection("127.0.0.1",8100,8300,"RAGAMES",
                                           callsign="AG7SU",station_id_mode="application")
        finally:patcher.stop()
        self.addCleanup(host.close)
        self.addCleanup(transport.close)
        return host,transport

    def test_listen_is_armed_before_anchor_and_ref_drives_hand_reply(self):
        host,link=self.make()
        with tempfile.TemporaryDirectory() as tmp:
            service=ArcadeService("AG7SU",link,SessionStore(Path(tmp)/"a"),
                                  discovery_auto_reply=True,discovery_legacy_cq=False)
            self.addCleanup(service.close)
            self.assertTrue(link.supports_hands)
            service.call_cq("nim")
            events=host.wait_for(lambda events:
                any(e[:3]==("KISS",2,12) for e in events))
            names=[e[0]+(" "+e[1] if e[0]=="CMD" else " "+str(e[2])) for e in events]
            armed=next(i for i,e in enumerate(events)
                       if e[0]=="CMD" and e[1].startswith("HAND LISTEN"))
            sent=next(i for i,e in enumerate(events) if e[:3]==("KISS",2,12))
            self.assertLess(armed,sent,msg=names)
            self.assertTrue(any(e[:4]==("KISS",0,6,b"REFS ON") for e in events))
            self.assertFalse(any(e[:3]==("KISS",2,0) and
                                 (lambda x:isinstance(x,Packet) and x.kind==Kind.CQ_REQUEST)(packet_decode(e[3]))
                                 for e in events if e[:3]==("KISS",2,0)))
            # A remote reply is surfaced with the active round ID.
            round_id=link._active_hand_round
            payload=encode_hand(Hand("N0AAA",Availability.LOOKING,1<<1,-13,1))
            channel=response_channel("N0AAA",round_id,40)
            host.emit_status(f"HAND {channel} {payload:016x} -8.5 125")
            end=time.monotonic()+1.0
            while not service.nearby_players() and time.monotonic()<end:time.sleep(.02)
            self.assertEqual(service.nearby_players()[0]["callsign"],"N0AAA")
            host.emit_status("HAND END")
            time.sleep(.03)
            self.assertIsNone(link._active_hand_round)
            remote=packet_encode(Packet(12,Kind.ROLL_CALL,0,111,0,"N0BBB","*",encode_request()))
            host.emit_data(remote)
            events=host.wait_for(lambda events:
                any(e[0]=="CMD" and e[1].startswith("HAND SEND ") for e in events))
            sent_hand=[e[1] for e in events if e[0]=="CMD" and e[1].startswith("HAND SEND ")]
            self.assertEqual(len(sent_hand),1,msg=names)
            self.assertTrue(sent_hand[0].startswith("HAND SEND 17 "))

    def test_old_modem_has_normal_cq_and_never_uses_hand_commands(self):
        host,link=self.make("0.4.3")
        with tempfile.TemporaryDirectory() as tmp:
            service=ArcadeService("AG7SU",link,SessionStore(Path(tmp)/"b"))
            self.addCleanup(service.close)
            self.assertFalse(link.supports_hands)
            service.call_cq("nim")
            events=host.wait_for(lambda events:any(e[:3]==("KISS",2,0) for e in events))
            packets=[packet_decode(e[3]) for e in events if e[:3]==("KISS",2,0)]
            self.assertTrue(any(x.kind==Kind.CQ_REQUEST for x in packets))
            self.assertFalse(any(e[0]=="CMD" and e[1].startswith("HAND") for e in events))
