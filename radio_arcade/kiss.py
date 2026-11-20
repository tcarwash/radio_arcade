"""Small TCP KISS client. Frames are KISS payloads, not AX.25 frames."""

from __future__ import annotations

import socket
import threading

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


def encode(payload: bytes, port: int = 0, command: int = 0) -> bytes:
    if not 0 <= port <= 15 or not 0 <= command <= 15:
        raise ValueError("invalid KISS port or command")
    body = bytes([(port << 4) | command]) + payload
    body = body.replace(bytes([FESC]), bytes([FESC, TFESC])).replace(bytes([FEND]), bytes([FESC, TFEND]))
    return bytes([FEND]) + body + bytes([FEND])


class Decoder:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.escaped = False

    def feed_all(self, chunk: bytes) -> list[tuple[int, int, bytes]]:
        frames = []
        for byte in chunk:
            if byte == FEND:
                if self.buf:
                    command, *payload = self.buf
                    frames.append((command >> 4, command & 15, bytes(payload)))
                self.buf.clear()
                self.escaped = False
            elif self.escaped:
                self.buf.append({TFEND: FEND, TFESC: FESC}.get(byte, byte))
                self.escaped = False
            elif byte == FESC:
                self.escaped = True
            elif len(self.buf) < 65535:
                self.buf.append(byte)
            else:
                self.buf.clear()
        return frames

    def feed(self, chunk: bytes) -> list[tuple[int, bytes]]:
        return [(port,payload) for port,command,payload in self.feed_all(chunk) if command == 0]


class KissConnection:
    supports_compact = True

    def __init__(self, host: str, port: int, kiss_port: int, on_frame=None):
        self.kiss_port = kiss_port
        self.on_frame = on_frame or (lambda _frame: None)
        self.on_frame_with_ref = None
        self.current_ref = None
        self.on_status = lambda _state, _peer: None
        self.sock = socket.create_connection((host, port), timeout=8)
        self.sock.settimeout(None)
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def send(self, payload: bytes) -> None:
        with self.lock:
            self.sock.sendall(encode(payload, self.kiss_port))

    def send_ackmode(self, payload: bytes, tag: int) -> None:
        if not 0 <= tag <= 65535:
            raise ValueError("invalid ACKMODE tag")
        with self.lock:
            self.sock.sendall(encode(tag.to_bytes(2,"big")+payload, self.kiss_port,12))

    def enable_refs(self) -> None:
        with self.lock:
            self.sock.sendall(encode(b"REFS ON",0,6))

    def _read(self) -> None:
        decoder = Decoder()
        try:
            while chunk := self.sock.recv(4096):
                for port,command,payload in decoder.feed_all(chunk):
                    if command == 6 and port == 0 and payload.startswith(b"\x11REF "):
                        pieces = payload[1:].decode("ascii","replace").split()
                        try:
                            self.current_ref = {"id":int(pieces[1]),"snr":float(pieces[4]),
                                                "end":float(pieces[2]),"offset":float(pieces[3])}
                        except (IndexError,ValueError):
                            self.current_ref = None
                    elif command == 0 and port == self.kiss_port:
                        if self.on_frame_with_ref is not None:
                            self.on_frame_with_ref(payload,self.current_ref)
                        else:
                            self.on_frame(payload)
        except OSError:
            pass
        finally:
            unexpected = not self.closed.is_set()
            self.closed.set()
            if unexpected:
                self.on_status("offline", None)

    def set_status_handler(self, callback) -> None:
        self.on_status = callback or (lambda _state, _peer: None)
        if self.closed.is_set():
            self.on_status("offline", None)

    def close(self) -> None:
        self.closed.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=2.0)
