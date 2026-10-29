"""Data2G endpoint setup shared by desktop, TUI, and headless callers."""

from __future__ import annotations

import socket
import threading

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

    def __init__(self, host: str, kiss_port: int, command_port: int, group: str):
        group = group_name(group)
        self._command = socket.create_connection((host, command_port), timeout=5)
        try:
            self._command.settimeout(5)
            self._command.sendall(f"BCAST OPEN {group}\r".encode("ascii"))
            response = bytearray()
            while b"\r" not in response and len(response) < 512:
                chunk = self._command.recv(128)
                if not chunk:
                    break
                response.extend(chunk)
            text = response.decode("ascii", "replace")
            line = next((line for line in text.replace("\n", "\r").split("\r")
                         if line.startswith("BCAST PORT ")), None)
            if line is None:
                raise RuntimeError(f"Data2G did not open {group}: {text.strip() or 'no response'}")
            group_port = int(line.rsplit(" ", 1)[1])
            self._command.settimeout(None)
            super().__init__(host, kiss_port, group_port)
            self._command_thread = threading.Thread(target=self._drain_commands, daemon=True)
            self._command_thread.start()
        except Exception:
            self._command.close()
            raise

    def _drain_commands(self):
        try:
            while self._command.recv(4096):
                pass
        except OSError:
            pass
        finally:
            if not self.closed.is_set():
                self.closed.set()
                self.on_status("offline", None)

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
    if settings.observer_mode and settings.transport != "broadcast":
        raise ValueError("observer mode requires KISS broadcast transport")
    if settings.transport == "practice":
        from .practice import PracticeService
        return PracticeService(call)
    if settings.transport == "broadcast":
        transport = BroadcastConnection(settings.host, settings.kiss_port, settings.command_port, settings.group)
    elif settings.transport == "connected":
        transport = ConnectedConnection(settings.host, settings.command_port, call)
    else:
        raise ValueError(f"unsupported transport {settings.transport!r}")
    try:
        return ArcadeService(call, transport, observer_mode=settings.observer_mode)
    except Exception:
        transport.close()
        raise
