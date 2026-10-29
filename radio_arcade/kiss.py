"""Small TCP KISS client. Frames are KISS payloads, not AX.25 frames."""

from __future__ import annotations

import socket
import threading

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


def encode(payload: bytes, port: int = 0) -> bytes:
    body = bytes([(port << 4) | 0]) + payload
    body = body.replace(bytes([FESC]), bytes([FESC, TFESC])).replace(bytes([FEND]), bytes([FESC, TFEND]))
    return bytes([FEND]) + body + bytes([FEND])


class Decoder:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.escaped = False

    def feed(self, chunk: bytes) -> list[tuple[int, bytes]]:
        frames = []
        for byte in chunk:
            if byte == FEND:
                if self.buf:
                    command, *payload = self.buf
                    if command & 0x0F == 0:
                        frames.append((command >> 4, bytes(payload)))
                self.buf.clear()
                self.escaped = False
            elif self.escaped:
                self.buf.append({TFEND: FEND, TFESC: FESC}.get(byte, byte))
                self.escaped = False
            elif byte == FESC:
                self.escaped = True
            else:
                self.buf.append(byte)
        return frames


class KissConnection:
    def __init__(self, host: str, port: int, kiss_port: int, on_frame=None):
        self.kiss_port = kiss_port
        self.on_frame = on_frame or (lambda _frame: None)
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

    def _read(self) -> None:
        decoder = Decoder()
        try:
            while chunk := self.sock.recv(4096):
                for port, payload in decoder.feed(chunk):
                    if port == self.kiss_port:
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
