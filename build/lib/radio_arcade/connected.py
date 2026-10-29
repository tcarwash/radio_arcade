"""Data2G's VARA-like connected command and byte-stream client."""

from __future__ import annotations

import queue
import socket
import threading
import time

from .wire import decode

MAX_PACKET = 240


class ConnectedConnection:
    """Connected-mode transport using command TCP and the session byte stream.

    The Data2G stream has no message boundaries, so app packets are prefixed
    with a two-byte unsigned big-endian length.
    """

    def __init__(self, host: str, command_port: int, callsign: str, on_frame=None):
        self.callsign = callsign.upper()
        self.on_frame = on_frame or (lambda _frame: None)
        self.on_status = lambda _state, _peer: None
        self.closed = threading.Event()
        self.connected = threading.Event()
        self.peer: str | None = None
        self._responses: queue.Queue[str] = queue.Queue()
        self._command_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._receive_lock = threading.Lock()
        self._receive_buffer = bytearray()
        self._expected = None
        self._cmd = socket.create_connection((host, command_port), timeout=8)
        self._cmd.settimeout(None)
        try:
            self._data = socket.create_connection((host, command_port + 1), timeout=8)
        except Exception:
            self._cmd.close()
            raise
        self._data.settimeout(None)
        self._cmd_thread = threading.Thread(target=self._read_commands, daemon=True)
        self._data_thread = threading.Thread(target=self._read_data, daemon=True)
        self._cmd_thread.start()
        self._data_thread.start()
        try:
            self._command(f"MYCALL {self.callsign}")
            self._command("DATA2G ON")
            self._command("LISTEN ON")
        except Exception:
            self.close()
            raise

    def _command(self, line: str, timeout: float = 8.0) -> None:
        with self._command_lock:
            self._cmd.sendall(line.encode("ascii") + b"\r")
            try:
                answer = self._responses.get(timeout=timeout)
            except queue.Empty as exc:
                raise TimeoutError(f"Data2G command timed out: {line}") from exc
            if answer != "OK":
                raise RuntimeError(f"Data2G rejected command {line}: {answer}")

    def retry_target(self) -> str | None:
        """Return the only station reachable by the current stream, if any."""
        if self.connected.is_set() and self.peer:
            return self.peer
        return None

    def _read_commands(self) -> None:
        buf = bytearray()
        try:
            while chunk := self._cmd.recv(2048):
                buf.extend(chunk)
                while b"\r" in buf or b"\n" in buf:
                    end = min(i for i in (buf.find(b"\r"), buf.find(b"\n")) if i >= 0)
                    line = bytes(buf[:end]).decode("ascii", "replace").strip()
                    del buf[:end + 1]
                    if not line:
                        continue
                    if line in ("OK", "WRONG"):
                        self._responses.put(line)
                    elif line.startswith("CONNECTED "):
                        fields = line.split()
                        if len(fields) >= 3:
                            self.peer = (fields[2] if fields[1].upper() == self.callsign else fields[1]).upper()
                        self.connected.set()
                        self.on_status("connected", self.peer)
                    elif line.startswith("DISCONNECTED"):
                        peer = self.peer
                        self.connected.clear()
                        self.peer = None
                        with self._receive_lock:
                            self._receive_buffer.clear()
                            self._expected = None
                        self.on_status("disconnected", peer)
                    elif line.startswith("REGISTERED ") or line.startswith("IAMALIVE") or line.startswith("BUFFER "):
                        continue
                    # Other status lines (PTT, BUSY, MODE) do not affect game transport.
        except OSError:
            pass
        finally:
            unexpected = not self.closed.is_set()
            self.closed.set()
            self.connected.clear()
            if unexpected:
                self.on_status("offline", None)

    def _read_data(self) -> None:
        try:
            while chunk := self._data.recv(4096):
                frames = []
                with self._receive_lock:
                    buf = self._receive_buffer
                    buf.extend(chunk)
                    while True:
                        if self._expected is None:
                            if len(buf) < 2:
                                break
                            self._expected = int.from_bytes(buf[:2], "big")
                            del buf[:2]
                            if self._expected == 0 or self._expected > MAX_PACKET:
                                buf.clear()
                                self._expected = None
                                break
                        if len(buf) < self._expected:
                            break
                        frames.append(bytes(buf[:self._expected]))
                        del buf[:self._expected]
                        self._expected = None
                for frame in frames:
                    self.on_frame(frame)
        except OSError:
            pass
        finally:
            unexpected = not self.closed.is_set()
            self.closed.set()
            self.connected.clear()
            if unexpected:
                self.on_status("offline", None)

    def connect_peer(self, peer: str, timeout: float = 120.0) -> None:
        peer = peer.upper()
        if self.connected.is_set():
            if self.peer == peer:
                return
            raise RuntimeError(f"already connected to {self.peer}; disconnect first")
        self._command(f"CONNECT {self.callsign} {peer}")
        if not self.connected.wait(timeout):
            raise TimeoutError(f"Data2G did not connect to {peer} within {timeout:g}s")
        if self.peer != peer:
            raise RuntimeError(f"connected peer mismatch: expected {peer}, got {self.peer}")

    def set_status_handler(self, callback) -> None:
        self.on_status = callback or (lambda _state, _peer: None)
        if self.closed.is_set():
            self.on_status("offline", None)
        elif self.connected.is_set():
            self.on_status("connected", self.peer)

    def disconnect(self) -> None:
        if self.connected.is_set():
            self._command("DISCONNECT")
            deadline = time.monotonic() + 3.0
            while self.connected.is_set() and time.monotonic() < deadline:
                time.sleep(0.05)

    def send(self, frame: bytes) -> None:
        if not self.connected.is_set():
            raise ConnectionError("no connected Data2G session")
        if not 0 < len(frame) <= MAX_PACKET:
            raise ValueError("game packet is too large")
        destination = decode(frame).destination
        if destination != self.peer:
            raise ConnectionError(f"packet is for {destination}; connected peer is {self.peer}")
        with self._send_lock:
            self._data.sendall(len(frame).to_bytes(2, "big") + frame)

    def close(self) -> None:
        self.closed.set()
        for sock in (self._cmd, self._data):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        for thread in (self._cmd_thread, self._data_thread):
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)
