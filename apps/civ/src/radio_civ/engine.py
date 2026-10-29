"""Game-specific service setup shared by the desktop and optional web clients."""

from __future__ import annotations

from radio_arcade.games.base import GameRegistry
from radio_arcade.service import ArcadeService
from radio_arcade.store import SessionStore, default_data_dir
from radio_arcade.transport_factory import BroadcastConnection

from .rules import CivilizationGame


def registry() -> GameRegistry:
    games = GameRegistry()
    games.register(CivilizationGame())
    return games


class _NoRadio:
    def send(self, _frame):
        return None

    def close(self):
        return None


class HotseatService(ArcadeService):
    """Run two full game-engine seats at one keyboard, with no radio traffic."""

    def __init__(self, callsign: str, store: SessionStore | None = None):
        super().__init__(callsign, _NoRadio(),
                         store or SessionStore(default_data_dir() / "radio-civ-hotseat.sqlite3"),
                         registry())

    def create_game(self, game: str | int = "radio-civ", peer=None, *, hosted=False) -> str:
        if peer or hosted or game not in ("radio-civ", 5):
            raise ValueError("hotseat only supports local Radio Civ games")
        with self._lock:
            second = "LOCAL2" if self.callsign != "LOCAL2" else "LOCAL3"
            row = self._new_session(5, self.callsign, [self.callsign, second], [], "lobby")
            self.start_game(row["id"])
            return row["id"]

    def legal_action(self, session_id: str | int, action: bytes) -> None:
        with self._lock:
            row = self._sessions[self._sid(session_id)]
            if row["status"] != "active" or row["state"] is None:
                raise ValueError("game is not active")
            game = self.registry.by_id(5)
            actor = game.current_player(game.decode_state(row["state"]), tuple(row["players"]))
            game.apply_action(game.decode_state(row["state"]), tuple(row["players"]), actor, action)
            self._commit(row, actor, action)

    def _reliable(self, *args, **kwargs):
        # The second hotseat is at this keyboard, never an on-air recipient.
        return None


def broadcast_service(call: str, host: str, kiss_port: int, command_port: int,
                      group: str, observer: bool = False) -> ArcadeService:
    transport = BroadcastConnection(host, kiss_port, command_port, group)
    name = "radio-civ-observer.sqlite3" if observer else "radio-civ.sqlite3"
    try:
        return ArcadeService(call, transport,
                             SessionStore(default_data_dir() / name), registry(),
                             observer_mode=observer)
    except Exception:
        transport.close()
        raise
