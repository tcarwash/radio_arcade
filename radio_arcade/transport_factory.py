"""Data2G endpoint setup shared by desktop, TUI, and headless callers."""

from __future__ import annotations

import socket
import threading
import re
from queue import Queue, Empty
import time

from .connected import ConnectedConnection
from .kiss import KissConnection
from .service import ArcadeService, station_name
from .settings import Settings


def group_name(value: str) -> str:
    group = value.strip().upper()
    if not 1 <= len(group) <= 10 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/- " for c in group):
        raise ValueError("broadcast group must be 1–10 letters, digits, spaces, / or -")
    if len(group.split()) >= 3 and group.split()[-2] == "FROM":
        raise ValueError("broadcast group cannot end with FROM followed by a callsign")
    return group


class BroadcastConnection(KissConnection):
    """Own the command connection that keeps the Data2G group open."""

    def __init__(self, host: str, kiss_port: int, command_port: int, group: str,
                 modem_mode: str = "", *, callsign: str = "",
                 station_id_mode: str = "application", cw_wpm: int = 20):
        group = group_name(group)
        if station_id_mode not in ("auto", "application") or type(cw_wpm) is not int or not 15 <= cw_wpm <= 40:
            raise ValueError("station ID must be auto/application and CW speed 15–40 WPM")
        if station_id_mode == "auto" and not callsign:
            raise ValueError("a callsign is required for Data2G CWID")
        self.cwid_active = False
        self.cwid_warning: str | None = None
        self.supports_hands = False
        self.hand_channels = 40
        self._hand_handler = lambda _event, _payload: None
        self._active_hand_round = None
        self._ack_tag = 0
        self._command_send_lock = threading.Lock()
        self._command_reply = Queue()
        self._command_pending = None
        self._command = socket.create_connection((host, command_port), timeout=5)
        self._command_buffer = bytearray()
        try:
            self._command.settimeout(5)
            if station_id_mode == "auto":
                try:
                    self._command_exchange(f"MYCALL {station_name(callsign)}")
                    self._command_exchange("DATA2G ON")
                    self._command_exchange(f"CWID ONLY {cw_wpm}")
                except RuntimeError as exc:
                    self.cwid_warning = f"Data2G CWID unavailable ({exc}); using Radio Arcade IDs"
                else:
                    self.cwid_active = True
            try:
                version = self._command_exchange("VERSION",prefix="VERSION Data2G ")
                parts = version.rsplit(" ",1)[-1].split(".")
                self.supports_hands = tuple(int(p) for p in parts[:3]) >= (0,5,0)
                if self.supports_hands:
                    self._command_exchange("DATA2G ON")
                    if modem_mode.strip().lower().startswith(("n4-","n10-","fsk16r25-","fsk8r50-")):
                        self._command_exchange("BW500")
                        self.hand_channels = 8
            except (RuntimeError,ValueError):
                self.supports_hands = False
            line = self._command_exchange(f"BCAST OPEN {group}", prefix="BCAST PORT ")
            group_port = int(line.rsplit(" ", 1)[1])
            if modem_mode:
                modem_mode = modem_mode.strip().lower()
                if not re.fullmatch(r"[a-z0-9][a-z0-9/-]{0,63}", modem_mode):
                    raise ValueError("broadcast modem mode must be a Data2G mode name")
                self._command_exchange(f"BCAST MODE {group_port} {modem_mode}")
            self._command.settimeout(None)
            super().__init__(host, kiss_port, group_port)
            if self.supports_hands:
                self.enable_refs()
            self._command_thread = threading.Thread(target=self._drain_commands, daemon=True)
            self._command_thread.start()
        except Exception:
            self._command.close()
            raise

    def _command_line(self) -> str:
        """Read one CR/LF-delimited reply, preserving extra notifications."""
        while True:
            indexes = [pos for pos in (self._command_buffer.find(b"\r"),
                                       self._command_buffer.find(b"\n")) if pos >= 0]
            if indexes:
                n = min(indexes)
                line = bytes(self._command_buffer[:n]).decode("ascii", "replace").strip()
                del self._command_buffer[:n + 1]
                if line:
                    return line
                continue
            if len(self._command_buffer) >= 2048:
                raise RuntimeError("Data2G command response is too long")
            chunk = self._command.recv(256)
            if not chunk:
                raise RuntimeError("Data2G command socket closed")
            self._command_buffer.extend(chunk)

    def _command_exchange(self, line: str, prefix: str = "OK") -> str:
        self._command.sendall(line.encode("ascii") + b"\r")
        for _ in range(32):
            response = self._command_line()
            if response == prefix or (prefix != "OK" and response.startswith(prefix)):
                return response
            if response == "WRONG":
                raise RuntimeError(f"Data2G rejected {line!r}")
            # MYCALL can emit REGISTERED after OK. Ignore asynchronous notices.
        raise RuntimeError(f"No {prefix!r} reply for {line!r}")

    def set_hand_handler(self, callback):
        self._hand_handler = callback or (lambda _event, _payload: None)

    def _hand_status(self, line: str) -> None:
        pieces=line.split()
        if not pieces or pieces[0] != "HAND":return
        try:
            if len(pieces)>=5 and pieces[1].isdigit() and all(c in "0123456789abcdefABCDEF" for c in pieces[2]):
                payload={"round":self._active_hand_round,"channels":self.hand_channels,
                         "channel":int(pieces[1]),"payload":int(pieces[2],16),
                         "snr":float(pieces[3]),"dt":float(pieces[4])}
                self._hand_handler("heard",payload)
            elif len(pieces)>=2 and pieces[1] == "END":
                round_id=self._active_hand_round
                self._active_hand_round=None
                self._hand_handler("end",{"round":round_id})
            elif len(pieces)>=3 and pieces[1] in ("SENT","DROPPED"):
                self._hand_handler(pieces[1].lower(),{"channel":int(pieces[2])})
        except (ValueError,TypeError):
            return

    def _drain_commands(self):
        try:
            while not self.closed.is_set():
                line=self._command_line()
                if line in ("OK","WRONG"):
                    # The lock serializes issued commands, so each response
                    # belongs to the single in-flight command (if any).
                    pending=self._command_pending
                    if pending is not None:
                        self._command_pending = None
                        pending.put_nowait(line)
                elif line.startswith("HAND "):
                    self._hand_status(line)
        except (OSError,RuntimeError):
            pass
        finally:
            pending=self._command_pending
            if pending is not None:
                self._command_pending=None
                pending.put_nowait("CLOSED")
            if not self.closed.is_set():
                self.closed.set()
                self.on_status("offline", None)

    def _hand_command(self, line: str, *, timeout: float = 2.0) -> bool:
        if not self.supports_hands or self.closed.is_set():
            return False
        with self._command_send_lock:
            result=Queue(maxsize=1)
            self._command_pending = result
            try:
                self._command.sendall(line.encode("ascii")+b"\r")
                return result.get(timeout=timeout) == "OK"
            except (OSError,Empty):
                return False
            finally:
                self._command_pending = None

    def send_hand(self, ref: int, tag: int, delay_s: float, channel: int, payload: int) -> bool:
        if not 0 <= channel < self.hand_channels or not 0 <= payload < 1<<63:
            return False
        return self._hand_command(
            f"HAND SEND {ref} {tag} {delay_s:.1f} {channel} {payload:016x}", timeout=1.0)

    def send_roll_call(self, frame: bytes, tag: int, channels: int, *, delay_s: float = 3.0) -> None:
        from .wire import decode
        packet = decode(frame)
        if not self.supports_hands or self._active_hand_round is not None:
            raise RuntimeError("Data2G Hand listening is unavailable or already in use")
        if channels > self.hand_channels or channels not in (8,20,40):
            raise ValueError("invalid Hand channel map")
        self._ack_tag = (self._ack_tag % 65535) + 1
        ack=self._ack_tag
        # HAND LISTEN must be armed *before* sending the KISS frame. The
        # ACKMODE tag ties it to the correct burst even when TX is queued.
        cmd=f"HAND LISTEN {delay_s:.1f} {tag} {ack:04x} " + " ".join(map(str,range(channels)))
        self._active_hand_round=packet.sequence
        if not self._hand_command(cmd,timeout=2.0):
            self._active_hand_round=None
            raise RuntimeError("Data2G would not open a Hand listening window")
        try:
            self.send_ackmode(frame,ack)
        except Exception:
            self._hand_command("HAND CANCEL")
            self._active_hand_round=None
            raise

    def close(self) -> None:
        super().close()
        try:
            self._command.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._command.close()
        if self._command_thread is not threading.current_thread():
            self._command_thread.join(timeout=2.0)


def create_service(settings: Settings) -> ArcadeService:
    call = station_name(settings.callsign)
    if settings.station_id_mode not in ("auto", "application") or type(settings.cw_wpm) is not int or not 15 <= settings.cw_wpm <= 40:
        raise ValueError("station ID must be auto/application and CW speed 15–40 WPM")
    if settings.observer_mode and settings.transport != "broadcast":
        raise ValueError("observer mode requires KISS broadcast transport")
    if settings.transport == "practice":
        from .practice import PracticeService
        return PracticeService(call, ai_difficulty=settings.ai_difficulty)
    # Do not enable autonomous modem transmissions for receive-only observers
    # or initially TX-disabled clients; explicit later TX uses the app fallback.
    modem_id_mode = (settings.station_id_mode if settings.tx_enabled and not settings.observer_mode
                     else "application")
    if settings.transport == "broadcast":
        transport = BroadcastConnection(settings.host, settings.kiss_port, settings.command_port,
                                        settings.group, settings.broadcast_mode, callsign=call,
                                        station_id_mode=modem_id_mode, cw_wpm=settings.cw_wpm)
    elif settings.transport == "connected":
        transport = ConnectedConnection(settings.host, settings.command_port, call,
                                       station_id_mode=modem_id_mode, cw_wpm=settings.cw_wpm)
    else:
        raise ValueError(f"unsupported transport {settings.transport!r}")
    try:
        return ArcadeService(call, transport, observer_mode=settings.observer_mode,
                             tx_enabled=settings.tx_enabled,
                             discovery_auto_reply=settings.discovery_auto_reply,
                             discovery_status=settings.discovery_status,
                             discovery_games=settings.discovery_games,
                             discovery_legacy_cq=settings.discovery_legacy_cq)
    except Exception:
        transport.close()
        raise
