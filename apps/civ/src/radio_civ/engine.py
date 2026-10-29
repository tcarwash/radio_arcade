"""Game-specific service setup shared by the desktop and optional web clients."""

from __future__ import annotations

from radio_arcade.games.base import GameRegistry
from radio_arcade.service import ArcadeService
from radio_arcade.store import SessionStore, default_data_dir
from radio_arcade.transport_factory import BroadcastConnection

from .rules import CivilizationGame, end_turn, turn_bundle


def registry() -> GameRegistry:
    games = GameRegistry()
    games.register(CivilizationGame())
    return games


class _NoRadio:
    def send(self, _frame):
        return None

    def close(self):
        return None


class RadioCivService(ArcadeService):
    """Queue a player's orders locally and commit the full turn once."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._turn_orders: dict[str, list[bytes]] = {}

    def _hotseat(self) -> bool:
        return False

    def _turn_actor(self, row, state) -> str:
        game = self.registry.by_id(row["game_id"])
        actor = game.current_player(state, tuple(row["players"]))
        if actor is None or (not self._hotseat() and actor != self.callsign):
            raise ValueError("it is not your turn")
        return actor

    def legal_action(self, session_id: str | int, action: bytes) -> None:
        self._require_player()
        sid = self._sid(session_id)
        with self._lock:
            row = self._sessions[sid]
            if row["status"] != "active" or row["state"] is None:
                raise ValueError("game is not active")
            if row["pending_action"] is not None:
                raise ValueError("waiting for the previous turn to be committed")
            game = self.registry.by_id(row["game_id"])
            players = tuple(row["players"])
            state = game.decode_state(row["state"])
            actor = self._turn_actor(row, state)
            queued = self._turn_orders.setdefault(sid, [])
            if action == end_turn():
                bundle = turn_bundle(tuple(queued))
                self._turn_orders.pop(sid, None)
                try:
                    self._commit_turn(sid, actor, bundle)
                except Exception:
                    self._turn_orders[sid] = queued
                    raise
                return
            projected = state
            for previous in queued:
                projected = game.apply_action(projected, players, actor, previous)
            game.apply_action(projected, players, actor, action)
            if len(queued) >= 2:
                raise ValueError("a turn can contain at most two orders")
            queued.append(action)
            self._emit("session_updated", sid, session=self.session(sid))

    def _commit_turn(self, sid: str, actor: str, bundle: bytes) -> None:
        super().legal_action(sid, bundle)

    def _project_queued(self, session: dict) -> dict:
        sid = session["id"]
        queued = self._turn_orders.get(sid, ())
        session["queued_orders"] = len(queued)
        pending = session["pending_action"]
        if (not queued and pending is None) or session["state"] is None:
            return session
        game = self.registry.by_id(session["game_id"])
        state = game.decode_state(session["state"])
        actor = game.current_player(state, tuple(session["players"]))
        if actor is None or (not self._hotseat() and actor != self.callsign):
            return session
        if pending is not None:
            try:
                state = game.apply_action(state, tuple(session["players"]), actor, pending)
            except ValueError:
                return session
        else:
            for order in queued:
                state = game.apply_action(state, tuple(session["players"]), actor, order)
        session["view"] = game.view_model(state, tuple(session["players"]))
        session["current_player"] = game.current_player(state, tuple(session["players"]))
        return session

    def session(self, session_id: str | int) -> dict:
        with self._lock:
            return self._project_queued(super().session(session_id))

    def sessions(self) -> list[dict]:
        with self._lock:
            rows = super().sessions()
            for row in rows:
                projected = self.session(row["id"])
                row["queued_orders"] = projected["queued_orders"]
                if projected["queued_orders"] or projected["pending_action"] is not None:
                    row["view"] = projected["view"]
                    row["current_player"] = projected["current_player"]
            return rows


class HotseatService(RadioCivService):
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

    def _hotseat(self) -> bool:
        return True

    def _commit_turn(self, sid: str, actor: str, bundle: bytes) -> None:
        with self._lock:
            row = self._sessions[sid]
            game = self.registry.by_id(row["game_id"])
            state = game.decode_state(row["state"])
            game.apply_action(state, tuple(row["players"]), actor, bundle)
            self._commit(row, actor, bundle)

    def _reliable(self, *args, **kwargs):
        # The second hotseat is at this keyboard, never an on-air recipient.
        return None


def broadcast_service(call: str, host: str, kiss_port: int, command_port: int,
                      group: str, observer: bool = False) -> RadioCivService:
    transport = BroadcastConnection(host, kiss_port, command_port, group)
    name = "radio-civ-observer.sqlite3" if observer else "radio-civ.sqlite3"
    try:
        return RadioCivService(call, transport,
                               SessionStore(default_data_dir() / name), registry(),
                               observer_mode=observer)
    except Exception:
        transport.close()
        raise
