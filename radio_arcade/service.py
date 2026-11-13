"""Headless game engine: sessions, turn validation, persistence, chat, and sync."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import threading
import time
from typing import Any, Callable

from .games.base import GameRegistry, builtin_registry
from .debuglog import opaque_logger
from .store import SessionStore
from .wire import (Kind, Packet, CompactFrame, decode, decode_action, decode_roster, decode_roster_state, encode,
                   encode_action, encode_roster, encode_roster_state, encode_snapshot, decode_snapshot,
                   encode_compact, FLAG_OPAQUE, FORFEIT_ACTION)

ID_INTERVAL_SECONDS = 9 * 60  # One-minute margin before the ten-minute limit.
CQ_LIFETIME_SECONDS = 15 * 60
CQ_ANY_GAME = 255


def station_name(value: str) -> str:
    call = value.strip().upper()
    if not call or len(call) > 15 or not call.isascii() or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-" for c in call):
        raise ValueError("station callsign must be 1–15 ASCII letters, digits, / or -")
    return call


@dataclass(frozen=True)
class EngineEvent:
    name: str
    session: str | None
    data: dict[str, Any]


class ArcadeService:
    """Embeddable game service. It has no dependency on either UI toolkit.

    `subscribe` callbacks run on the calling transport/retry thread; UI clients
    should marshal the event onto their own UI thread. Public methods are safe
    to call from UI or application threads.
    """

    def __init__(self, callsign: str, transport: Any, store: SessionStore | None = None,
                 registry: GameRegistry | None = None, observer_mode: bool = False,
                 tx_enabled: bool = True, compact_turns: bool | None = None):
        self.callsign = station_name(callsign)
        self.observer_mode = observer_mode
        self.tx_enabled = bool(tx_enabled)
        if observer_mode and hasattr(transport, "connect_peer"):
            raise ValueError("observer mode requires a broadcast transport")
        self.transport = transport
        self.cwid_active = bool(getattr(transport, "cwid_active", False))
        self.identification_method = "Data2G CW" if self.cwid_active else "Radio Arcade data ID"
        self.identification_warning = getattr(transport, "cwid_warning", None)
        # Receive-only observers may explicitly transmit SYNC_REQUEST; they
        # still need to identify whenever they exercise that exception.
        self.station_id_enabled = (bool(getattr(transport, "supports_compact", False) or compact_turns)
                                   and not self.cwid_active)
        self.compact_turns = (bool(getattr(transport, "supports_compact", False))
                              if compact_turns is None else bool(compact_turns)) and not observer_mode
        self.store = store or SessionStore((SessionStore.default_observer_path() if observer_mode else None))
        self._opaque_log = opaque_logger(self.store.path.parent)
        self.registry = registry or builtin_registry()
        self._lock = threading.RLock()
        self._id_lock = threading.RLock()
        self._last_id_at: float | None = None
        self._sent_since_id = False
        self._pending_final_ids: dict[str, float] = {}
        self._subscribers: set[Callable[[EngineEvent], None]] = set()
        self._sessions: dict[str, dict[str, Any]] = {}
        for saved in self.store.load_sessions():
            try:
                self.registry.by_id(saved["game_id"])
            except ValueError:
                # Leave unsupported/retired sessions intact on disk. Without
                # their rules, we cannot safely decode or resume their state.
                continue
            self._sessions[saved["id"]] = saved
        self._id_finished = {sid for sid, row in self._sessions.items() if row["status"] == "finished"}
        self._reported_failed: set[tuple[str, str, int, int]] = set()
        self._cq_requests: dict[tuple[str, int], float] = {}
        if not observer_mode:
            self.store.upgrade_v3_outbox()
        self._stop = threading.Event()
        self._closed = False
        self.transport.on_frame = self.receive_frame
        if hasattr(self.transport, "set_status_handler"):
            self.transport.set_status_handler(self._transport_status)
        self._worker = threading.Thread(target=self._retry_loop, name="radio-arcade-engine", daemon=True)
        self._worker.start()

    def _transport_status(self, status: str, peer: str | None) -> None:
        self._emit("transport_status", status=status, peer=peer)
        if self.observer_mode:
            return
        if status != "connected" or not peer:
            return
        with self._lock:
            self.store.resume_outgoing(peer)
            self._reported_failed = {key for key in self._reported_failed if key[1] != peer}
            for row in self._sessions.values():
                if row["status"] not in ("active", "finished") or peer not in row["players"]:
                    continue
                if row["coordinator"] == self.callsign:
                    self._send_snapshot(row, peer)
                else:
                    self.request_sync(row["id"])

    def subscribe(self, callback: Callable[[EngineEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.add(callback)
        return lambda: self.unsubscribe(callback)

    def unsubscribe(self, callback: Callable[[EngineEvent], None]) -> None:
        with self._lock:
            self._subscribers.discard(callback)

    def _emit(self, name: str, session_id: str | None = None, **data: Any) -> None:
        event = EngineEvent(name, session_id, data)
        with self._lock:
            callbacks = tuple(self._subscribers)
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                continue

    @staticmethod
    def _sid(value: str | int) -> str:
        if isinstance(value, int):
            return f"{value:016X}"
        return f"{int(value, 16):016X}"

    def sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for row in self._sessions.values():
                view = {k: v for k, v in row.items() if k not in ("state", "pending_action")}
                view["game"] = self.registry.by_id(row["game_id"]).name
                if row["state"] is not None:
                    game = self.registry.by_id(row["game_id"])
                    state = game.decode_state(row["state"])
                    view["view"] = game.view_model(state, tuple(row["players"]))
                    view["current_player"] = game.current_player(state, tuple(row["players"]))
                    view["last_move"] = ({"actor": row["last_actor"],
                                          "description": "forfeits the game" if row["last_action"] == FORFEIT_ACTION else game.describe_action(row["last_action"])}
                                         if row.get("last_actor") and row.get("last_action") is not None else None)
                else:
                    view["view"] = None
                    view["current_player"] = None
                self._present_forfeit(view)
                result.append(view)
            return sorted(result, key=lambda item: item.get("updated", 0), reverse=True)

    def session(self, session_id: str | int) -> dict[str, Any]:
        sid = self._sid(session_id)
        with self._lock:
            row = self._sessions[sid]
            result = dict(row)
            game = self.registry.by_id(row["game_id"])
            result["game"] = game.name
            state = game.decode_state(row["state"]) if row["state"] is not None else None
            result["view"] = game.view_model(state, tuple(row["players"])) if state is not None else None
            result["current_player"] = game.current_player(state, tuple(row["players"])) if state is not None else None
            if row["game_id"] == 12:
                private = self.store.load_private_game_data(sid)
                result["private_fleet"] = private.get("ships")
            result["last_move"] = ({"actor": row["last_actor"],
                                    "description": "forfeits the game" if row["last_action"] == FORFEIT_ACTION else game.describe_action(row["last_action"])}
                                   if row.get("last_actor") and row.get("last_action") is not None else None)
            # Presentation-only prediction: authoritative state and turn remain
            # unchanged until COMMIT. Both clients can animate while radio waits.
            pending = row.get("pending_action")
            if game.key == "scorched-earth" and state is not None and pending and pending[0] < 128:
                predicted = game.apply_action(state, tuple(row["players"]), self.callsign, pending)
                result["animation_view"] = game.view_model(predicted, tuple(row["players"]))
                result["animation_revision"] = row["revision"] + 1
            self._present_forfeit(result)
            return result

    @staticmethod
    def _present_forfeit(session):
        if session.get("last_action") != FORFEIT_ACTION:
            return
        actor = session["last_actor"]
        winners = [player for player in session["players"] if player != actor]
        session["status"], session["current_player"] = "finished", None
        session["forfeited_by"] = actor
        if session.get("view") is not None:
            session["view"].update(winner=winners[0] if len(winners) == 1 else None,
                                   winners=winners, forfeited_by=actor, draw=False, turn=None,
                                   trajectory=[], impact=None, last_actor=actor, last_action="forfeits the game")

    def _forfeit_actor(self, row):
        return self.callsign

    def forfeit_game(self, session_id: str | int) -> None:
        """End the table, awarding the result to the other participants."""
        self._require_player()
        if not self.tx_enabled:
            raise ValueError("TX is disabled. Enable TX to forfeit over the radio.")
        sid = self._sid(session_id)
        with self._lock:
            row = self._sessions[sid]
            actor = self._forfeit_actor(row)
            if row["status"] != "active" or actor not in row["players"]:
                raise ValueError("only a participant in an active game can forfeit")
            if row["coordinator"] == self.callsign:
                self._commit_forfeit(row, actor)
            else:
                row["pending_action"] = FORFEIT_ACTION
                self._save(row)
                self._reliable(row, row["coordinator"], Kind.RESIGN, row["sequence"] + 1, row["revision"])
                self._emit("session_updated", sid, session=self.session(sid))

    def _record_forfeit(self, row, actor, sequence, revision):
        if actor not in row["players"]:
            raise ValueError("forfeit names a non-player")
        row.update(status="finished", sequence=sequence, revision=revision,
                   last_actor=actor, last_action=FORFEIT_ACTION, pending_action=None, error="")
        for peer in row["players"]:
            self.store.cancel_outgoing(row["id"], peer, int(Kind.ACTION))
            self.store.cancel_outgoing(row["id"], peer, int(Kind.RESIGN))
        self._save(row)
        self._emit("session_updated", row["id"], session=self.session(row["id"]), committed=True)

    def _commit_forfeit(self, row, actor):
        payload = encode_action(actor, FORFEIT_ACTION)
        sequence, revision = row["sequence"] + 1, row["revision"] + 1
        snapshot = encode_snapshot(tuple(row["players"]), row["state"], actor, FORFEIT_ACTION)
        self._make(row, row["coordinator"], Kind.SNAPSHOT, sequence, revision, snapshot)
        self._make(row, "*", Kind.COMMIT, sequence, revision, payload)
        self._record_forfeit(row, actor, sequence, revision)
        if row.get("hosted"):
            self._transmit(self._make(row, "*", Kind.COMMIT, sequence, revision, payload))
        else:
            for peer in row["players"]:
                if peer != self.callsign:
                    self._reliable(row, peer, Kind.COMMIT, sequence, revision, payload)

    def delete_game(self, session_id: str | int) -> None:
        """Delete a game from this station without sending radio traffic."""
        sid = self._sid(session_id)
        with self._lock:
            if sid not in self._sessions:
                raise ValueError("game no longer exists")
            self.store.delete_session(sid)
            self._sessions.pop(sid)
            self._emit("session_deleted", sid)

    def set_tx_enabled(self, enabled: bool) -> None:
        self.tx_enabled = bool(enabled)
        self._emit("tx_state", enabled=self.tx_enabled)

    def catalog(self):
        return self.registry.catalog()

    def connect_peer(self, peer: str) -> None:
        if not hasattr(self.transport, "connect_peer"):
            raise RuntimeError("the selected transport is broadcast; peer connections are not used")
        self.transport.connect_peer(peer.upper())

    def disconnect(self) -> None:
        if not hasattr(self.transport, "disconnect"):
            raise RuntimeError("the selected transport has no connected session to close")
        self.transport.disconnect()

    def _save(self, session: dict[str, Any]) -> None:
        session["updated"] = time.time()
        self.store.save_session(session)
        if (self.station_id_enabled and session["status"] == "finished"
                and session["id"] not in self._id_finished and self.callsign in session["players"]):
            self._id_finished.add(session["id"])
            seat = session["players"].index(self.callsign)
            self._pending_final_ids[session["id"]] = time.monotonic() + 0.5 + 2.0 * seat

    def _new_session(self, game_id: int, coordinator: str, players: list[str],
                     invited: list[str], status: str, hosted: bool = False) -> dict[str, Any]:
        sid = secrets.randbits(64)
        while f"{sid:016X}" in self._sessions:
            sid = secrets.randbits(64)
        session = {"id": f"{sid:016X}", "game_id": game_id, "coordinator": coordinator,
                   "players": players, "invited": invited, "status": status,
                   "revision": 0, "sequence": 0, "state": None, "pending_action": None,
                   "chat_sequence": 0, "opaque_sequence": 0, "last_actor": None, "last_action": None,
                   "updated": time.time(), "error": "", "hosted": hosted}
        self._sessions[session["id"]] = session
        self._save(session)
        return session

    def _make(self, session: dict[str, Any], target: str, kind: Kind,
              sequence: int = 0, revision: int | None = None, payload: bytes = b"",
              flags: int = 0) -> bytes:
        return encode(Packet(session["game_id"], kind, int(session["id"], 16), sequence,
                             session["revision"] if revision is None else revision,
                             self.callsign, target.upper(), payload, flags))

    def _short_session_unique(self, session: int) -> bool:
        """A collision must never route a move to the wrong local game."""
        key = session & 0xFFFFFFFF
        return sum((int(sid, 16) & 0xFFFFFFFF) == key for sid in self._sessions) == 1

    def _compact_packet(self, packet: Packet) -> bytes | None:
        """Compress the established turn/ACK, leaving persistent outbox frames full."""
        if not self.compact_turns or packet.sequence == 0:
            return None
        row = self._sessions.get(f"{packet.session:016X}")
        if (row is None or row["status"] not in ("active", "finished")
                or not (row.get("hosted") or len(row["players"]) == 2)
                or not self._short_session_unique(packet.session)):
            return None
        players = row["players"]
        if packet.kind in (Kind.ACTION, Kind.COMMIT):
            actor, action = decode_action(packet.payload)
            if actor not in players:
                return None
            if packet.kind == Kind.ACTION and (packet.source != actor or packet.destination != row["coordinator"]):
                return None
            if packet.kind == Kind.COMMIT and packet.source != row["coordinator"]:
                return None
            if packet.kind == Kind.COMMIT and packet.destination != ("*" if row.get("hosted") else next(
                    (call for call in players if call != self.callsign), "")):
                return None
            return encode_compact(packet.kind, packet.session, packet.sequence,
                                  players.index(actor), action)
        if packet.kind == Kind.ACK and packet.revision in (int(Kind.ACTION), int(Kind.COMMIT)):
            if packet.source not in players:
                return None
            return encode_compact(Kind.ACK, packet.session, packet.sequence,
                                  players.index(packet.source),
                                  acknowledged_kind=Kind(packet.revision))
        return None

    def _expand_compact(self, frame: CompactFrame) -> Packet:
        """Use the full START roster to restore a normal validated packet."""
        with self._lock:
            matching = [row for sid, row in self._sessions.items()
                        if (int(sid, 16) & 0xFFFFFFFF) == frame.short_session]
            if len(matching) != 1:
                raise ValueError("unknown or ambiguous compact session reference")
            row = matching[0]
            players = row["players"]
            if (frame.seat >= len(players) or not (row.get("hosted") or len(players) == 2)
                    or row["status"] not in ("active", "finished")):
                raise ValueError("compact packet requires a known active roster")
            actor, coordinator, sid = players[frame.seat], row["coordinator"], int(row["id"], 16)
            if frame.kind == Kind.ACTION:
                return Packet(row["game_id"], Kind.ACTION, sid, frame.sequence,
                              frame.sequence - 1, actor, coordinator, encode_action(actor, frame.payload))
            if frame.kind == Kind.COMMIT:
                return Packet(row["game_id"], Kind.COMMIT, sid, frame.sequence,
                              frame.sequence, coordinator, "*" if row.get("hosted") else self.callsign,
                              encode_action(actor, frame.payload))
            if frame.kind == Kind.ACK:
                return Packet(row["game_id"], Kind.ACK, sid, frame.sequence,
                              int(frame.acknowledged_kind), actor, self.callsign)
        raise ValueError("unrecognized compact frame type")

    def _send_station_id(self, *, force: bool = False) -> bool:
        """Full readable ASCII ID; operators can additionally use modem CW ID."""
        if not self.station_id_enabled or not self.tx_enabled:
            return False
        with self._id_lock:
            now = time.monotonic()
            if not force and self._last_id_at is not None and now - self._last_id_at < ID_INTERVAL_SECONDS:
                return False
            frame = encode(Packet(1, Kind.STATION_ID, 0, 0, 0, self.callsign, "*",
                                  b"ID " + self.callsign.encode("ascii")))
            details = {"kind": "STATION_ID", "destination": "*",
                       "reason": "identifying this station on the air"}
            self._emit("transmitting", **details)
            try:
                self.transport.send(frame)
            except (OSError, RuntimeError, ValueError) as exc:
                self._emit("transport_send_error", error=f"station ID failed: {exc}")
                return False
            self._last_id_at = now
            self._sent_since_id = False
            self._emit("station_identified", callsign=self.callsign, mode="ascii-data")
            self._emit("transmission_complete", **details)
            return True

    def _transmit(self, frame: bytes) -> None:
        if not self.tx_enabled:
            self._emit("transmission_blocked", reason="TX is disabled")
            return
        packet = None
        try:
            packet = decode(frame)
            reasons = {
                Kind.INVITE: "inviting a station", Kind.ACCEPT: "accepting an invitation",
                Kind.START: "starting a game", Kind.ACTION: "sending a move",
                Kind.COMMIT: "sharing a validated move", Kind.SYNC_REQUEST: "requesting a game sync",
                Kind.SNAPSHOT: "sending game state", Kind.REJECT: "rejecting an invalid request",
                Kind.RESIGN: "resigning", Kind.CHAT: "sending table chat",
                Kind.ACK: "automatically acknowledging a received packet",
                Kind.OPAQUE_STATE: "sharing game state", Kind.CANCEL_INVITE: "cancelling an invitation",
                Kind.HOST_ANNOUNCE: "announcing an open table", Kind.JOIN_REQUEST: "requesting to join a table",
                Kind.HOST_ROSTER: "updating the hosted table roster", Kind.HOST_CHAT: "relaying table chat",
                Kind.STATION_ID: "identifying this station on the air",
                Kind.CQ_REQUEST: "calling CQ for a game",
            }
            details = {"kind": packet.kind.name, "destination": packet.destination,
                       "reason": reasons.get(packet.kind, "sending a game packet")}
            self._emit("transmitting", f"{packet.session:016X}", **details)
        except (ValueError, TypeError):
            details = {"kind": "DATA", "destination": "", "reason": "sending game data"}
            self._emit("transmitting", **details)
        try:
            if self.station_id_enabled and isinstance(packet, Packet) and packet.kind != Kind.STATION_ID:
                with self._id_lock:
                    # Do not transmit game traffic if its required ID fails.
                    if (self._last_id_at is None or
                            time.monotonic() - self._last_id_at >= ID_INTERVAL_SECONDS):
                        if not self._send_station_id():
                            self._emit("transport_send_error", error="station identification is required before transmitting")
                            return
                    compact_frame = self._compact_packet(packet) if self.compact_turns else None
                    frame = compact_frame or frame
                    self.transport.send(frame)
                    self._sent_since_id = True
            else:
                self.transport.send(frame)
        except (OSError, RuntimeError, ValueError) as exc:
            self._emit("transport_send_error", error=str(exc))
        finally:
            self._emit("transmission_complete", **details)

    def _reliable(self, session: dict[str, Any], target: str, kind: Kind,
                  sequence: int = 0, revision: int | None = None, payload: bytes = b"",
                  flags: int = 0) -> None:
        if self.observer_mode:
            raise ValueError("observer mode is receive-only except for explicit sync requests")
        if not self.tx_enabled:
            raise ValueError("TX is disabled. Enable TX to send radio traffic.")
        frame = self._make(session, target, kind, sequence, revision, payload, flags)
        self.store.save_outgoing(session["id"], target.upper(), int(kind), sequence, frame)
        self._transmit(frame)
        self._emit("delivery", session["id"], target=target.upper(), kind=kind.name,
                   sequence=sequence, state="pending")

    def _ack(self, packet: Packet) -> None:
        ack = Packet(packet.game, Kind.ACK, packet.session, packet.sequence, int(packet.kind),
                     self.callsign, packet.source, b"")
        self._transmit(encode(ack))

    def create_game(self, game: str | int, peer: str | None = None, *, hosted: bool = False) -> str:
        self._require_player()
        definition = self.registry.by_id(game) if isinstance(game, int) else self.registry.by_key(game)
        if hosted and (peer or hasattr(self.transport, "connect_peer")):
            raise ValueError("hosted tables require broadcast mode and no direct invitation")
        if peer:
            peer = station_name(peer)
            if peer == self.callsign:
                raise ValueError("choose another station")
        if definition.max_players < 2:
            raise ValueError("radio games need at least two players")
        row = self._new_session(definition.game_id, self.callsign, [self.callsign], [], "lobby", hosted)
        self._emit("session_updated", row["id"], session=self.session(row["id"]))
        if hosted:
            self.announce_game(row["id"])
        elif peer:
            self.invite_player(row["id"], peer)
        return row["id"]

    def announce_game(self, session_id: str | int) -> None:
        """Advertise a public lobby again, on user request (no automatic beacon)."""
        self._require_player()
        with self._lock:
            row = self._sessions[self._sid(session_id)]
            if not row.get("hosted") or row["coordinator"] != self.callsign or row["status"] != "lobby":
                raise ValueError("only the dealer may advertise an open hosted lobby")
            self._transmit(self._make(row, "*", Kind.HOST_ANNOUNCE,
                                      payload=encode_roster(tuple(row["players"]))))

    def call_cq(self, game: str | int | None = None) -> None:
        """Ask listeners to open a direct table, without creating one yet."""
        self._require_player()
        if hasattr(self.transport, "connect_peer") or not getattr(self.transport, "supports_broadcast", True):
            raise ValueError("CQ requires broadcast radio mode")
        if not self.tx_enabled:
            raise ValueError("TX is disabled. Enable TX to call CQ.")
        definition = (self.registry.by_id(game) if isinstance(game, int) else self.registry.by_key(game)) if game is not None else None
        if definition is not None and definition.max_players < 2:
            raise ValueError("CQ requires a multiplayer game")
        self._transmit(encode(Packet(definition.game_id if definition else CQ_ANY_GAME,
                                     Kind.CQ_REQUEST, 0, 0, 0, self.callsign, "*")))

    def cq_requests(self) -> list[dict[str, Any]]:
        """Recently heard CQ calls; these expire and create no game on receipt."""
        with self._lock:
            now = time.monotonic()
            self._cq_requests = {key: heard for key, heard in self._cq_requests.items()
                                 if now - heard < CQ_LIFETIME_SECONDS}
            return [dict(callsign=call, game_id=game_id,
                         game="Any game" if game_id == CQ_ANY_GAME else self.registry.by_id(game_id).name)
                    for (call, game_id), _heard in sorted(self._cq_requests.items(),
                                                           key=lambda item: item[1], reverse=True)]

    def answer_cq(self, callsign: str, game: str | int) -> str:
        """Answer a recent CQ with a directed invitation."""
        peer = station_name(callsign)
        definition = self.registry.by_id(game) if isinstance(game, int) else self.registry.by_key(game)
        if not any(row["callsign"] == peer and row["game_id"] in (CQ_ANY_GAME, definition.game_id)
                   for row in self.cq_requests()):
            raise ValueError("no matching recent CQ from that station")
        return self.create_game(definition.game_id, peer)

    def set_reply_order(self, session_id: str | int, order: list[str] | tuple[str, ...] | str) -> None:
        """Dealer chooses the roster/turn order before the first roll."""
        self._require_player()
        names = ([station_name(name) for name in order.split(",")] if isinstance(order, str)
                 else [station_name(name) for name in order])
        with self._lock:
            row = self._sessions[self._sid(session_id)]
            if not row.get("hosted") or row["coordinator"] != self.callsign or row["status"] != "lobby":
                raise ValueError("only the dealer can set the order of an open hosted lobby")
            if not names or names[0] != self.callsign or len(set(names)) != len(names) or set(names) != set(row["players"]):
                raise ValueError("list all seated stations once, with the dealer first")
            row["players"] = names
            self._save(row)
            self._transmit(self._make(row, "*", Kind.HOST_ROSTER,
                                      payload=encode_roster(tuple(names))))
            self._emit("session_updated", row["id"], session=self.session(row["id"]))

    def join_game(self, session_id: str | int) -> None:
        """Ask to occupy a seat in an advertised public table."""
        self._require_player()
        with self._lock:
            row = self._sessions[self._sid(session_id)]
            if not row.get("hosted") or row["coordinator"] == self.callsign or row["status"] not in ("lobby", "joining"):
                raise ValueError("select an advertised hosted lobby to join")
            if self.callsign in row["players"]:
                return
            row["status"] = "joining"
            self._save(row)
            # No per-station ACK. Re-click Join if the dealer did not announce a seat.
            self._transmit(self._make(row, row["coordinator"], Kind.JOIN_REQUEST))
            self._emit("session_updated", row["id"], session=self.session(row["id"]))

    def invite_player(self, session_id: str | int, peer: str) -> None:
        self._require_player()
        sid, peer = self._sid(session_id), station_name(peer)
        if peer == self.callsign:
            raise ValueError("choose another station")
        with self._lock:
            row = self._sessions[sid]
            if row.get("hosted"):
                raise ValueError("hosted tables accept join requests instead of invitations")
            definition = self.registry.by_id(row["game_id"])
            if row["coordinator"] != self.callsign or row["status"] != "lobby":
                raise ValueError("only the coordinator can invite players before the game starts")
            if self.store.was_invite_cancelled(sid, peer):
                raise ValueError("this invitation was cancelled; create a new game to invite that station again")
            if peer in row["players"] or peer in row["invited"]:
                return
            if len(row["players"]) + len(row["invited"]) >= definition.max_players:
                raise ValueError("game is full")
            if hasattr(self.transport, "connect_peer") and len(row["players"]) + len(row["invited"]) >= 2:
                raise ValueError("connected mode supports two players until a relay is available")
            if hasattr(self.transport, "connect_peer"):
                self.connect_peer(peer)
            row["invited"].append(peer)
            self._save(row)
            self._reliable(row, peer, Kind.INVITE)
            self._emit("session_updated", sid, session=self.session(sid))

    def accept_invite(self, session_id: str | int) -> None:
        self._require_player()
        sid = self._sid(session_id)
        with self._lock:
            row = self._sessions[sid]
            if row["status"] not in ("invited", "joining"):
                raise ValueError("this invitation is no longer pending")
            row["status"] = "joining"
            self._save(row)
            self._reliable(row, row["coordinator"], Kind.ACCEPT)
            self._emit("session_updated", sid, session=self.session(sid))

    def start_game(self, session_id: str | int) -> None:
        self._require_player()
        sid = self._sid(session_id)
        with self._lock:
            row = self._sessions[sid]
            game = self.registry.by_id(row["game_id"])
            if row["coordinator"] != self.callsign or row["status"] != "lobby":
                raise ValueError("only the coordinator can start this lobby")
            if row["invited"]:
                raise ValueError("wait for pending invitations or cancel them before starting")
            if len(row["players"]) < game.min_players:
                raise ValueError(f"need at least {game.min_players} players")
            if hasattr(self.transport, "connect_peer") and len(row["players"]) > 2:
                raise ValueError("connected mode supports two players until a relay is available")
            players = tuple(row["players"])
            state = game.new_state(players)
            encoded_state = game.encode_state(state)
            snapshot = encode_roster_state(players, encoded_state)
            if row.get("hosted"):
                self._make(row, "*", Kind.START, 0, 0, snapshot)
            else:
                for player in players:
                    if player != self.callsign:
                        self._make(row, player, Kind.START, 0, 0, snapshot)
            row["state"] = encoded_state
            row["status"], row["sequence"], row["revision"] = "active", 0, 0
            row["last_actor"], row["last_action"] = None, None
            self._save(row)
            if row.get("hosted"):
                self._transmit(self._make(row, "*", Kind.START, payload=snapshot))
            else:
                for player in players:
                    if player != self.callsign:
                        self._reliable(row, player, Kind.START, payload=snapshot)
            self._emit("session_updated", sid, session=self.session(sid))

    def place_battleship_fleet(self, session_id: str | int, ships: list[list[int]]) -> None:
        """Save this station's fleet locally; the fleet never enters shared state."""
        sid = self._sid(session_id)
        self._require_player()
        with self._lock:
            row = self._sessions[sid]
            if row["game_id"] != 12 or row["status"] != "active" or self.callsign not in row["players"]:
                raise ValueError("select an active Battleship table")
            from .games.battleship import FLEET
            if len(ships) != len(FLEET) or any(len(ship) != length for ship, length in zip(ships, FLEET)):
                raise ValueError("place all five ships with lengths 5, 4, 3, 3, and 2")
            cells = [cell for ship in ships for cell in ship]
            if any(type(cell) is not int or not 0 <= cell < 100 for cell in cells) or len(set(cells)) != len(cells):
                raise ValueError("fleet cells must be unique board coordinates")
            for ship, length in zip(ships, FLEET):
                rows, cols = [x//10 for x in ship], [x%10 for x in ship]
                if len(set(rows)) == 1:
                    values = cols
                elif len(set(cols)) == 1:
                    values = rows
                else:
                    raise ValueError("each ship must be straight")
                if sorted(values) != list(range(min(values), min(values)+length)):
                    raise ValueError("ship cells must be consecutive")
            state = self.registry.by_id(12).decode_state(row["state"])
            seat = row["players"].index(self.callsign)
            if state["phase"] != "setup" or state["ready"][seat]:
                raise ValueError("your fleet is already deployed for this game")
            self.store.save_private_game_data(sid, {"ships": ships, "hits": []})
            if row["players"][state["turn"]] == self.callsign:
                self.legal_action(sid, b"\x7f")

    def _resolve_battleship_pending(self, row: dict[str, Any]) -> None:
        if row["game_id"] != 12 or row["status"] != "active": return
        game = self.registry.by_id(12); state = game.decode_state(row["state"])
        if state["phase"] != "reply" or row["players"][state["turn"]] != self.callsign: return
        data = self.store.load_private_game_data(row["id"])
        ships, hits = data.get("ships"), set(data.get("hits", []))
        if not ships: return
        target = state["pending"]
        ship_id = next((i for i, ship in enumerate(ships) if target in ship), None)
        if ship_id is None: result = 0
        else:
            hits.add(target)
            result = 2 + ship_id if all(cell in hits for cell in ships[ship_id]) else 1
        data["hits"] = sorted(hits); self.store.save_private_game_data(row["id"], data)
        self.legal_action(row["id"], bytes((0x80 | result,)))

    def legal_action(self, session_id: str | int, action: bytes) -> None:
        if action == FORFEIT_ACTION:
            raise ValueError("use forfeit_game for a forfeit")
        self._require_player()
        sid = self._sid(session_id)
        with self._lock:
            row = self._sessions[sid]
            if row["game_id"] == 12 and action == b"\x7f":
                if not self.store.load_private_game_data(sid).get("ships"):
                    raise ValueError("place your fleet on this station before readying")
            game = self.registry.by_id(row["game_id"])
            if row["status"] != "active" or row["state"] is None:
                raise ValueError("game is not active")
            if row["pending_action"] is not None:
                raise ValueError("waiting for the previous move to be committed")
            state = game.decode_state(row["state"])
            if not self._can_act(game, state, tuple(row["players"]), self.callsign):
                raise ValueError("it is not your turn")
            # Validate before sending. The committed transition is applied only
            # by the coordinator/COMMIT path.
            game.apply_action(state, tuple(row["players"]), self.callsign, action)
            row["error"] = ""
            next_sequence = row["sequence"] + 1
            payload = encode_action(self.callsign, action)
            if row["coordinator"] == self.callsign:
                self._commit(row, self.callsign, action)
            else:
                row["pending_action"] = action
                self._save(row)
                if row.get("hosted"):
                    self._transmit(self._make(row, row["coordinator"], Kind.ACTION,
                                              next_sequence, row["revision"], payload))
                else:
                    self._reliable(row, row["coordinator"], Kind.ACTION, next_sequence,
                                   row["revision"], payload)
                self._emit("session_updated", sid, session=self.session(sid))

    def resend_pending_action(self, session_id: str | int) -> None:
        """Manually retry a lost turn request in a hosted session."""
        self._require_player()
        with self._lock:
            row = self._sessions[self._sid(session_id)]
            if not row.get("hosted") or row["coordinator"] == self.callsign or row["pending_action"] is None:
                raise ValueError("no hosted move waiting to be retried")
            action = row["pending_action"]
            if action == FORFEIT_ACTION:
                self._reliable(row, row["coordinator"], Kind.RESIGN, row["sequence"] + 1, row["revision"])
                return
            self._transmit(self._make(row, row["coordinator"], Kind.ACTION,
                                      row["sequence"] + 1, row["revision"],
                                      encode_action(self.callsign, action)))

    def cancel_invitation(self, session_id: str | int, peer: str) -> None:
        self._require_player()
        sid, peer = self._sid(session_id), peer.upper()
        with self._lock:
            row = self._sessions[sid]
            if row["coordinator"] != self.callsign or row["status"] != "lobby" or peer not in row["invited"]:
                raise ValueError("no pending invitation for that station")
            row["invited"].remove(peer)
            self.store.cancel_outgoing(sid, peer, int(Kind.INVITE))
            self.store.remember_cancelled_invite(sid, peer)
            self._save(row)
            self._reliable(row, peer, Kind.CANCEL_INVITE)
            self._emit("session_updated", sid, session=self.session(sid))

    def _commit(self, row: dict[str, Any], actor: str, action: bytes) -> None:
        game = self.registry.by_id(row["game_id"])
        state = game.decode_state(row["state"])
        new_state = game.apply_action(state, tuple(row["players"]), actor, action)
        encoded_state = game.encode_state(new_state)
        payload = encode_action(actor, action)
        snapshot = encode_snapshot(tuple(row["players"]), encoded_state, actor, action)
        if row.get("hosted"):
            self._make(row, "*", Kind.COMMIT, row["sequence"] + 1, row["revision"] + 1, payload)
        else:
            for player in row["players"]:
                if player != self.callsign:
                    self._make(row, player, Kind.COMMIT, row["sequence"] + 1, row["revision"] + 1, payload)
        row["state"] = encoded_state
        row["sequence"] += 1
        row["revision"] += 1
        row["last_actor"], row["last_action"] = actor, action
        if new_state.get("finished"):
            row["status"] = "finished"
        self._save(row)
        if row["game_id"] in (9, 10):
            self.store.record_engine_action(row["id"], row["revision"], actor, action)
        if row.get("hosted"):
            self._transmit(self._make(row, "*", Kind.COMMIT,
                                      row["sequence"], row["revision"], payload))
        else:
            for player in row["players"]:
                if player != self.callsign:
                    self._reliable(row, player, Kind.COMMIT, row["sequence"], row["revision"], payload)
        self._emit("session_updated", row["id"], session=self.session(row["id"]),
                   action=payload, committed=True)
        self._resolve_battleship_pending(row)

    @staticmethod
    def _can_act(game, state, players, actor):
        custom = getattr(game, "can_act", None)
        return bool(custom(state, players, actor)) if callable(custom) else game.current_player(state, players) == actor

    def send_chat(self, session_id: str | int, text: str) -> int:
        self._require_player()
        sid = self._sid(session_id)
        text = text.strip()
        body = text.encode("utf-8")
        if not text or len(body) > 150:
            raise ValueError("chat must contain 1 to 150 UTF-8 bytes")
        with self._lock:
            row = self._sessions[sid]
            if row["status"] not in ("active", "finished"):
                raise ValueError("side chat is available once a game has started")
            row["chat_sequence"] = (row["chat_sequence"] + 1) & 0xFFFF
            seq = row["chat_sequence"]
            self.store.add_chat(sid, self.callsign, seq, text, "pending")
            self._save(row)
            if row.get("hosted"):
                if row["coordinator"] == self.callsign:
                    self._transmit(self._make(row, "*", Kind.HOST_CHAT, seq,
                                              row["revision"], encode_action(self.callsign, body)))
                    self.store.set_chat_delivery(sid, self.callsign, seq, "broadcast")
                else:
                    self._transmit(self._make(row, row["coordinator"], Kind.CHAT,
                                              seq, row["revision"], body))
            else:
                for player in row["players"]:
                    if player != self.callsign:
                        self._reliable(row, player, Kind.CHAT, seq, row["revision"], body)
            self._emit("chat_message", sid, message={"sender": self.callsign, "sequence": seq,
                                                       "body": text, "delivery": "pending"})
            return seq

    def chat_history(self, session_id: str | int) -> list[dict[str, Any]]:
        return self.store.load_chat(self._sid(session_id))

    def send_opaque_state(self, session_id: str | int, target: str, text: str) -> int:
        self._require_player()
        """Reliably send plain UTF-8 state that bundled UIs must not display."""
        sid, target = self._sid(session_id), target.upper().strip()
        body = text.encode("utf-8")
        if not body or len(body) > 200 or not all(c.isprintable() or c in "\r\n\t" for c in text):
            raise ValueError("opaque state must be readable UTF-8 text up to 200 bytes")
        with self._lock:
            row = self._sessions[sid]
            if row["status"] not in ("active", "finished") or target not in row["players"] or target == self.callsign:
                raise ValueError("opaque state target must be another participant in a started game")
            row["opaque_sequence"] = (row.get("opaque_sequence", 0) + 1) & 0xFFFF
            sequence = row["opaque_sequence"]
            self._save(row)
            self.store.add_opaque_state(sid, self.callsign, sequence, body, "pending")
            self._reliable(row, target, Kind.OPAQUE_STATE, sequence, row["revision"], body, FLAG_OPAQUE)
            self._opaque_log.debug("TX session=%s sender=%s target=%s sequence=%d plaintext=%r",
                                   sid, self.callsign, target, sequence, text)
            return sequence

    def send_game_opaque_state(self, session_id: str | int, target: str, value: Any) -> int:
        self._require_player()
        """Encode a game-owned opaque value, then send its readable wire text."""
        sid = self._sid(session_id)
        with self._lock:
            game = self.registry.by_id(self._sessions[sid]["game_id"])
        encoder = getattr(game, "encode_opaque_state", None)
        if not callable(encoder):
            raise ValueError(f"{game.name} does not define an opaque-state format")
        text = encoder(value)
        if not isinstance(text, str):
            raise ValueError("game opaque-state encoder must return text")
        return self.send_opaque_state(sid, target, text)

    def opaque_state_history(self, session_id: str | int) -> list[dict[str, Any]]:
        """Programmatic/debug API only; the desktop and TUI never render this payload."""
        return self.store.load_opaque_states(self._sid(session_id))

    def request_sync(self, session_id: str | int) -> None:
        sid = self._sid(session_id)
        with self._lock:
            row = self._sessions[sid]
            if self.observer_mode:
                # Explicit UI action only: no automatic requests, retries or ACKs.
                self._transmit(self._make(row, row["coordinator"], Kind.SYNC_REQUEST,
                                          row["sequence"], row["revision"]))
                return
            if row["coordinator"] == self.callsign:
                return
            if row.get("hosted"):
                self._transmit(self._make(row, row["coordinator"], Kind.SYNC_REQUEST,
                                          row["sequence"], row["revision"]))
                return
            self._reliable(row, row["coordinator"], Kind.SYNC_REQUEST,
                           row["sequence"], row["revision"])

    def _require_player(self) -> None:
        if self.observer_mode:
            raise ValueError("observer mode cannot create, join, or play games or send chat")

    def receive_frame(self, frame: bytes) -> None:
        try:
            packet = decode(frame)
            if isinstance(packet, CompactFrame):
                packet = self._expand_compact(packet)
            self._handle(packet)
        except (ValueError, KeyError, IndexError, UnicodeDecodeError) as exc:
            self._emit("protocol_error", error=str(exc))

    def _observe(self, packet: Packet) -> None:
        """Watch overheard broadcast traffic without ACKs or automatic transmissions."""
        if packet.source == self.callsign or packet.kind not in (
                Kind.START, Kind.SNAPSHOT, Kind.COMMIT, Kind.CHAT,
                Kind.HOST_ANNOUNCE, Kind.HOST_ROSTER, Kind.HOST_CHAT):
            return
        sid = f"{packet.session:016X}"
        with self._lock:
            row = self._sessions.get(sid)
            if row is None:
                try:
                    self.registry.by_id(packet.game)
                except ValueError:
                    return
                # START/SNAPSHOT prove the creator and roster. A COMMIT alone
                # provides an incomplete table; the user may request sync.
                if packet.kind in (Kind.HOST_ANNOUNCE, Kind.HOST_ROSTER):
                    players, state, last = decode_roster(packet.payload), None, None
                elif packet.kind == Kind.START:
                    players, state = decode_roster_state(packet.payload)
                    last = None
                elif packet.kind == Kind.SNAPSHOT:
                    players, state, last = decode_snapshot(packet.payload)
                elif packet.kind == Kind.COMMIT:
                    players, state, last = (), None, None
                else:
                    return
                game = self.registry.by_id(packet.game)
                if state is not None:
                    if not game.min_players <= len(players) <= game.max_players or players[0] != packet.source:
                        return
                    decoded = game.decode_state(state)
                    if hasattr(game, "validate_roster"):
                        game.validate_roster(decoded, players)
                    turn = game.current_player(decoded, players)
                    if (decoded.get("finished") and turn is not None) or (not decoded.get("finished") and turn not in players):
                        return
                row = {"id": sid, "game_id": packet.game, "coordinator": packet.source,
                       "players": list(players), "invited": [], "status": "observing" if state is None else
                       ("finished" if decoded.get("finished") or (last and last[1] == FORFEIT_ACTION) else "active"),
                       "revision": packet.revision if state is not None else 0,
                       "sequence": packet.sequence if state is not None else 0,
                       "state": state, "pending_action": None, "chat_sequence": 0,
                       "opaque_sequence": 0, "last_actor": last[0] if last else None,
                       "last_action": last[1] if last else None,
                       "updated": time.time(), "error": "Awaiting snapshot" if state is None else "",
                       "hosted": packet.kind in (Kind.HOST_ANNOUNCE, Kind.HOST_ROSTER) or packet.destination == "*"}
                if packet.kind in (Kind.HOST_ANNOUNCE, Kind.HOST_ROSTER):
                    row["status"], row["error"] = "lobby", ""
                self._sessions[sid] = row
                self._save(row)
                self._emit("session_updated", sid, session=self.session(sid))
                return
            if row["game_id"] != packet.game:
                return
            if packet.kind in (Kind.START, Kind.SNAPSHOT, Kind.COMMIT,
                               Kind.HOST_ANNOUNCE, Kind.HOST_ROSTER, Kind.HOST_CHAT) and packet.source != row["coordinator"]:
                return
            if packet.kind in (Kind.HOST_ANNOUNCE, Kind.HOST_ROSTER) and row.get("hosted") and row["state"] is None:
                self._receive_host_roster(packet, sid)
            elif packet.kind == Kind.SNAPSHOT:
                self._receive_snapshot(row, packet)
            elif packet.kind == Kind.START and row["state"] is None:
                players, state = decode_roster_state(packet.payload)
                self._receive_snapshot(row, Packet(packet.game, Kind.SNAPSHOT, packet.session, 0, 0,
                                                  packet.source, packet.destination,
                                                  encode_snapshot(players, state)))
            elif packet.kind == Kind.COMMIT:
                if row["state"] is None:
                    return
                self._receive_commit(row, packet)
            elif packet.kind == Kind.CHAT and packet.source in row["players"]:
                self._receive_chat(row, packet)
            elif packet.kind == Kind.HOST_CHAT and row.get("hosted"):
                self._receive_host_chat(row, packet)

    def _handle(self, packet: Packet) -> None:
        if packet.kind == Kind.STATION_ID:
            if (packet.session == 0 and packet.sequence == packet.revision == 0
                    and packet.destination == "*" and packet.payload == b"ID " + packet.source.encode("ascii")):
                self._emit("station_identified", callsign=packet.source, mode="ascii-data")
            return
        if self.observer_mode:
            self._observe(packet)
            return
        if packet.source == self.callsign or packet.destination not in (self.callsign, "*"):
            return
        with self._lock:
            if packet.kind == Kind.CQ_REQUEST:
                if (packet.destination != "*" or packet.session != 0 or packet.sequence != 0
                        or packet.revision != 0 or packet.payload):
                    return
                try:
                    caller = station_name(packet.source)
                    game = ("Any game" if packet.game == CQ_ANY_GAME else
                            self.registry.by_id(packet.game).name)
                except ValueError:
                    return
                self._cq_requests[(caller, packet.game)] = time.monotonic()
                self._emit("cq_request", callsign=caller, game_id=packet.game, game=game)
                return
            sid = f"{packet.session:016X}"
            if packet.kind in (Kind.HOST_ANNOUNCE, Kind.HOST_ROSTER):
                self._receive_host_roster(packet, sid)
                return
            if packet.kind == Kind.JOIN_REQUEST:
                self._receive_join_request(packet, sid)
                return
            if packet.kind == Kind.ACK:
                row = self._sessions.get(f"{packet.session:016X}")
                if row is None or row["game_id"] != packet.game:
                    return
                try:
                    kind = Kind(packet.revision)
                except ValueError:
                    return
                if not self.store.acknowledge(f"{packet.session:016X}", packet.source,
                                              int(kind), packet.sequence):
                    return
                if kind == Kind.CHAT:
                    remaining = self.store.count_outgoing(f"{packet.session:016X}", int(kind), packet.sequence)
                    state = "delivered" if remaining == 0 else "pending"
                    if remaining == 0:
                        self.store.set_chat_delivery(f"{packet.session:016X}", self.callsign,
                                                     packet.sequence, state)
                    self._emit("chat_delivery", f"{packet.session:016X}", sequence=packet.sequence,
                               state=state, pending_stations=remaining)
                elif kind == Kind.OPAQUE_STATE:
                    self.store.set_opaque_delivery(f"{packet.session:016X}", self.callsign,
                                                   packet.sequence, "delivered")
                    self._emit("opaque_state_delivery", f"{packet.session:016X}",
                               sequence=packet.sequence, state="delivered")
                else:
                    self._emit("delivery", f"{packet.session:016X}", target=packet.source,
                               kind=kind.name, sequence=packet.sequence, state="delivered")
                return

            if packet.kind == Kind.INVITE:
                self._receive_invite(packet, sid)
                row = self._sessions.get(sid)
                if row and row["game_id"] == packet.game and row["coordinator"] == packet.source:
                    self._ack(packet)
                return
            if packet.kind == Kind.CANCEL_INVITE:
                row = self._sessions.get(sid)
                if row is None:
                    self._receive_invite(packet, sid, announce=False)
                    row = self._sessions.get(sid)
                if row and row["game_id"] == packet.game and row["coordinator"] == packet.source:
                    if row["status"] in ("invited", "joining"):
                        row["status"] = "cancelled"
                        self.store.cancel_outgoing(sid, packet.source, int(Kind.ACCEPT))
                        self._save(row)
                        self._emit("session_updated", sid, session=self.session(sid))
                    self._ack(packet)
                return
            row = self._sessions.get(sid)
            if row is None or row["game_id"] != packet.game:
                return
            if row.get("hosted"):
                self._handle_hosted(row, packet)
                return
            if (packet.kind == Kind.SYNC_REQUEST and row["coordinator"] == self.callsign
                    and packet.source not in row["players"] and packet.source not in row["invited"]
                    and row["status"] in ("active", "finished") and not packet.payload):
                # A spectator receives one untracked snapshot; it need not ACK.
                payload = encode_snapshot(tuple(row["players"]), row["state"],
                                          row.get("last_actor"), row.get("last_action"))
                self._transmit(self._make(row, packet.source, Kind.SNAPSHOT,
                                          row["sequence"], row["revision"], payload))
                return
            if packet.source not in row["players"] and packet.source not in row["invited"]:
                return
            if packet.kind == Kind.ACCEPT:
                self._receive_accept(row, packet)
            elif packet.kind == Kind.START:
                if not self._receive_start(row, packet):
                    return
            elif packet.kind == Kind.RESIGN:
                if not self._receive_resign(row, packet):
                    return
            elif packet.kind == Kind.ACTION:
                self._receive_action(row, packet)
            elif packet.kind == Kind.COMMIT:
                self._receive_commit(row, packet)
            elif packet.kind == Kind.SYNC_REQUEST:
                if row["coordinator"] == self.callsign:
                    self._send_snapshot(row, packet.source)
            elif packet.kind == Kind.SNAPSHOT:
                if not self._receive_snapshot(row, packet):
                    return
            elif packet.kind == Kind.CHAT:
                if not self._receive_chat(row, packet):
                    return
            elif packet.kind == Kind.OPAQUE_STATE:
                if not self._receive_opaque_state(row, packet):
                    return
            elif packet.kind == Kind.REJECT:
                if packet.source != row["coordinator"]:
                    return
                if row["pending_action"] is None or packet.sequence != row["sequence"] + 1:
                    if packet.sequence <= row["sequence"] + 1:
                        self._ack(packet)
                    return
                self.store.acknowledge(sid, row["coordinator"], int(Kind.ACTION), packet.sequence)
                row["pending_action"] = None
                row["error"] = packet.payload.decode("utf-8", "replace")
                self._save(row)
                self._emit("session_updated", sid, session=self.session(sid))
            if packet.kind in (Kind.ACCEPT, Kind.START, Kind.ACTION, Kind.COMMIT,
                               Kind.SYNC_REQUEST, Kind.SNAPSHOT, Kind.REJECT, Kind.CHAT,
                               Kind.OPAQUE_STATE, Kind.RESIGN):
                self._ack(packet)

    def _receive_host_roster(self, packet: Packet, sid: str) -> None:
        if packet.destination != "*" or packet.revision != 0 or packet.sequence != 0:
            return
        game = self.registry.by_id(packet.game)
        players = decode_roster(packet.payload)
        if not 1 <= len(players) <= game.max_players or players[0] != packet.source:
            return
        row = self._sessions.get(sid)
        if row is None:
            row = {"id": sid, "game_id": packet.game, "coordinator": packet.source,
                   "players": list(players), "invited": [], "status": "lobby",
                   "revision": 0, "sequence": 0, "state": None, "pending_action": None,
                   "chat_sequence": 0, "opaque_sequence": 0, "last_actor": None,
                   "last_action": None, "updated": time.time(), "error": "", "hosted": True}
            self._sessions[sid] = row
        elif (not row.get("hosted") or row["coordinator"] != packet.source
              or row["game_id"] != packet.game or row["state"] is not None):
            return
        # A stale earlier roster must never evict our accepted seat; the host's
        # advertised roster grows monotonically while a lobby is open.
        if not set(row["players"]).issubset(players):
            return
        row["players"] = list(players)
        row["status"] = "lobby" if self.callsign in players else (
            "joining" if row["status"] == "joining" else "lobby")
        row["error"] = ""
        self._save(row)
        self._emit("session_updated", sid, session=self.session(sid))

    def _receive_join_request(self, packet: Packet, sid: str) -> None:
        row = self._sessions.get(sid)
        if (row is None or not row.get("hosted") or row["coordinator"] != self.callsign
                or packet.destination != self.callsign or row["status"] != "lobby"
                or packet.game != row["game_id"] or packet.payload):
            return
        players = row["players"]
        if packet.source not in players:
            game = self.registry.by_id(row["game_id"])
            if len(players) >= game.max_players:
                return
            players.append(packet.source)
            self._save(row)
        # Repeat the entire canonical roster, including the newest joiner.
        self._transmit(self._make(row, "*", Kind.HOST_ROSTER,
                                  payload=encode_roster(tuple(players))))
        self._emit("session_updated", sid, session=self.session(sid))

    def _handle_hosted(self, row: dict[str, Any], packet: Packet) -> None:
        """Dealer owns state; only the dealer broadcasts roster, state and chat."""
        if packet.kind == Kind.START:
            if (packet.source == row["coordinator"] and packet.destination == "*"
                    and self.callsign in row["players"]):
                self._receive_start(row, packet)
        elif packet.kind == Kind.ACTION:
            if packet.destination == self.callsign and packet.source in row["players"]:
                self._receive_action(row, packet)
        elif packet.kind == Kind.COMMIT:
            if packet.destination == "*":
                self._receive_commit(row, packet)
        elif packet.kind == Kind.SNAPSHOT:
            if packet.source == row["coordinator"] and packet.destination in (self.callsign, "*"):
                self._receive_snapshot(row, packet)
        elif packet.kind == Kind.SYNC_REQUEST:
            if (row["coordinator"] == self.callsign and packet.destination == self.callsign
                    and row["status"] in ("active", "finished") and not packet.payload):
                self._transmit(self._make(row, packet.source, Kind.SNAPSHOT,
                                          row["sequence"], row["revision"],
                                          encode_snapshot(tuple(row["players"]), row["state"],
                                                          row["last_actor"], row["last_action"])))
        elif packet.kind == Kind.RESIGN:
            if self._receive_resign(row, packet):
                self._ack(packet)
        elif packet.kind == Kind.CHAT:
            if (row["coordinator"] == self.callsign and packet.destination == self.callsign
                    and self._receive_chat(row, packet)):
                self._transmit(self._make(row, "*", Kind.HOST_CHAT,
                                          packet.sequence, row["revision"],
                                          encode_action(packet.source, packet.payload)))
        elif packet.kind == Kind.HOST_CHAT:
            if packet.source == row["coordinator"] and packet.destination == "*":
                self._receive_host_chat(row, packet)
        elif packet.kind == Kind.OPAQUE_STATE:
            if packet.destination == self.callsign and packet.source in row["players"]:
                if self._receive_opaque_state(row, packet):
                    self._ack(packet)
        elif packet.kind == Kind.REJECT and packet.source == row["coordinator"]:
            if row["pending_action"] is not None and packet.sequence == row["sequence"] + 1:
                row["pending_action"] = None
                row["error"] = packet.payload.decode("utf-8", "replace")
                self._save(row)
                self._emit("session_updated", row["id"], session=self.session(row["id"]))

    def _receive_resign(self, row, packet):
        if (packet.payload or packet.source not in row["players"] or row["coordinator"] != self.callsign
                or packet.revision > row["revision"] or packet.sequence > row["sequence"] + 1):
            return False
        if row["status"] == "active":
            self._commit_forfeit(row, packet.source)
        elif row["status"] == "finished":
            self._send_snapshot(row, packet.source)
        else:
            return False
        return True

    def _receive_host_chat(self, row: dict[str, Any], packet: Packet) -> None:
        sender, body = decode_action(packet.payload)
        if sender not in row["players"] or len(body) > 150:
            return
        rebroadcast = Packet(packet.game, Kind.CHAT, packet.session, packet.sequence,
                             packet.revision, sender, self.callsign, body)
        self._receive_chat(row, rebroadcast)
        if sender == self.callsign:
            self.store.set_chat_delivery(row["id"], sender, packet.sequence, "broadcast")
            self._emit("chat_delivery", row["id"], sequence=packet.sequence, state="broadcast")

    def _receive_invite(self, packet: Packet, sid: str, announce: bool = True) -> None:
        if sid in self._sessions:
            return
        try:
            game = self.registry.by_id(packet.game)
        except ValueError:
            self._emit("unsupported_game", sid, game_id=packet.game, sender=packet.source)
            return
        row = {"id": sid, "game_id": game.game_id, "coordinator": packet.source,
               "players": [packet.source, self.callsign], "invited": [], "status": "invited",
               "revision": 0, "sequence": 0, "state": None, "pending_action": None,
               "chat_sequence": 0, "opaque_sequence": 0, "last_actor": None, "last_action": None,
               "updated": time.time(), "error": ""}
        self._sessions[sid] = row
        self._save(row)
        if announce:
            self._emit("invitation", sid, sender=packet.source, game=game.name)

    def _receive_accept(self, row: dict[str, Any], packet: Packet) -> None:
        if row["coordinator"] != self.callsign:
            return
        peer = packet.source
        if row["status"] in ("active", "finished") and peer in row["players"]:
            self._send_snapshot(row, peer)
            return
        if row["status"] != "lobby" or peer not in row["invited"]:
            return
        # Acceptance proves that this station received the invitation even if
        # its standalone acknowledgement was lost.
        self.store.cancel_outgoing(row["id"], peer, int(Kind.INVITE))
        row["invited"].remove(peer)
        row["players"].append(peer)
        self._save(row)
        game = self.registry.by_id(row["game_id"])
        if len(row["players"]) >= game.max_players or (
                len(row["players"]) >= game.min_players == game.max_players):
            self.start_game(row["id"])
        else:
            self._emit("session_updated", row["id"], session=self.session(row["id"]))

    def _receive_start(self, row: dict[str, Any], packet: Packet) -> bool:
        if row["coordinator"] != packet.source or row["status"] not in (("lobby", "joining", "active", "finished") if row.get("hosted") else ("joining", "active", "finished")):
            return False
        if packet.sequence != 0 or packet.revision != 0:
            return False
        players, state = decode_roster_state(packet.payload)
        game = self.registry.by_id(row["game_id"])
        if len(players) < game.min_players or len(players) > game.max_players or players[0] != packet.source or self.callsign not in players:
            return False
        if hasattr(self.transport, "connect_peer") and len(players) > 2:
            raise ValueError("connected mode cannot join a game with more than two players")
        decoded = game.decode_state(state)
        if hasattr(game, "validate_roster"):
            game.validate_roster(decoded, players)
        if decoded.get("finished") or game.current_player(decoded, players) not in players:
            raise ValueError("invalid initial game state")
        if row["status"] in ("active", "finished") and packet.revision <= row["revision"]:
            return tuple(row["players"]) == players
        row["players"], row["state"] = list(players), state
        row["status"], row["sequence"], row["revision"] = "active", packet.sequence, packet.revision
        row["last_actor"], row["last_action"] = None, None
        row["error"] = ""
        self._save(row)
        self._emit("session_updated", row["id"], session=self.session(row["id"]))
        return True

    def _receive_action(self, row: dict[str, Any], packet: Packet) -> None:
        if row["coordinator"] != self.callsign or row["status"] != "active":
            return
        actor, action = decode_action(packet.payload)
        if actor != packet.source or actor not in row["players"]:
            return
        if packet.sequence <= row["sequence"]:
            self._send_snapshot(row, actor)
            return
        if packet.sequence != row["sequence"] + 1 or packet.revision != row["revision"]:
            self._send_snapshot(row, actor)
            return
        if action == FORFEIT_ACTION:
            self._send_reject(row, actor, packet, "use a resignation request")
            return
        game = self.registry.by_id(row["game_id"])
        state = game.decode_state(row["state"])
        if not self._can_act(game, state, tuple(row["players"]), actor):
            self._send_reject(row, actor, packet, "not your turn")
            return
        try:
            game.apply_action(state, tuple(row["players"]), actor, action)
        except ValueError as exc:
            self._send_reject(row, actor, packet, str(exc))
            return
        try:
            self._commit(row, actor, action)
        except ValueError as exc:
            self._send_reject(row, actor, packet, str(exc))

    def _receive_commit(self, row: dict[str, Any], packet: Packet) -> None:
        if packet.source != row["coordinator"] or row["state"] is None:
            return
        if packet.sequence <= row["sequence"] and packet.revision <= row["revision"]:
            return
        if packet.sequence != row["sequence"] + 1 or packet.revision != row["revision"] + 1:
            if self.observer_mode:
                row["error"] = "Missed moves · request sync to recover"
                self._save(row)
                self._emit("session_updated", row["id"], session=self.session(row["id"]))
            else:
                self.request_sync(row["id"])
            return
        actor, action = decode_action(packet.payload)
        if action == FORFEIT_ACTION:
            if row["status"] != "active":
                return
            self._record_forfeit(row, actor, packet.sequence, packet.revision)
            return
        if row["status"] == "finished":
            return
        game = self.registry.by_id(row["game_id"])
        state = game.decode_state(row["state"])
        try:
            new_state = game.apply_action(state, tuple(row["players"]), actor, action)
        except ValueError:
            if self.observer_mode:
                row["error"] = "State conflict · request sync to recover"
                self._save(row)
                self._emit("session_updated", row["id"], session=self.session(row["id"]))
            else:
                self.request_sync(row["id"])
            return
        row["state"] = game.encode_state(new_state)
        if actor == self.callsign and row["pending_action"] == action:
            # A coordinator commit is stronger evidence than an ACTION ack.
            self.store.acknowledge(row["id"], row["coordinator"], int(Kind.ACTION), packet.sequence)
        row["sequence"], row["revision"] = packet.sequence, packet.revision
        row["last_actor"], row["last_action"] = actor, action
        row["pending_action"], row["error"] = None, ""
        if new_state.get("finished"):
            row["status"] = "finished"
        self._save(row)
        if row["game_id"] in (9, 10):
            self.store.record_engine_action(row["id"], row["revision"], actor, action)
        self._emit("session_updated", row["id"], session=self.session(row["id"]),
                   action=packet.payload, committed=True)
        self._resolve_battleship_pending(row)

    def _send_reject(self, row: dict[str, Any], peer: str, packet: Packet, reason: str) -> None:
        payload = reason.encode("utf-8")[:120]
        if row.get("hosted"):
            self._transmit(self._make(row, peer, Kind.REJECT,
                                      packet.sequence, row["revision"], payload))
        else:
            self._reliable(row, peer, Kind.REJECT, packet.sequence, row["revision"], payload)

    def _send_snapshot(self, row: dict[str, Any], target: str) -> None:
        if row["state"] is None:
            return
        payload = encode_snapshot(tuple(row["players"]), row["state"],
                                  row.get("last_actor"), row.get("last_action"))
        try:
            self._make(row, target, Kind.SNAPSHOT, row["sequence"], row["revision"], payload)
        except ValueError:
            if row.get("last_action") == FORFEIT_ACTION:
                raise
            # A long committed game action can make the optional last-move
            # trailer exceed the radio envelope even though the state fits.
            payload = encode_snapshot(tuple(row["players"]), row["state"])
        if row.get("hosted"):
            self._transmit(self._make(row, target, Kind.SNAPSHOT,
                                      row["sequence"], row["revision"], payload))
        else:
            self._reliable(row, target, Kind.SNAPSHOT, row["sequence"], row["revision"], payload)

    def _receive_snapshot(self, row: dict[str, Any], packet: Packet) -> bool:
        if packet.source != row["coordinator"]:
            return False
        players, state, last_move = decode_snapshot(packet.payload)
        game = self.registry.by_id(row["game_id"])
        if (len(players) < game.min_players or len(players) > game.max_players
                or players[0] != row["coordinator"] or (not self.observer_mode and self.callsign not in players)
                or len(set(players)) != len(players)):
            return False
        if hasattr(self.transport, "connect_peer") and len(players) > 2:
            raise ValueError("connected mode cannot recover a game with more than two players")
        if row["status"] not in (("invited", "joining", "observing", "lobby") if row.get("hosted") else ("invited", "joining", "observing")) and tuple(row["players"]) != players:
            return False
        decoded = game.decode_state(state)
        if hasattr(game, "validate_roster"):
            game.validate_roster(decoded, players)
        turn = game.current_player(decoded, players)
        if (decoded.get("finished") and turn is not None) or (not decoded.get("finished") and turn not in players):
            raise ValueError("snapshot turn does not match game state and roster")
        if (row["state"] is not None and packet.revision == row["revision"]
                and packet.sequence == row["sequence"] and state != row["state"]):
            raise ValueError("snapshot conflicts with the current state at the same revision")
        if last_move:
            actor, action = last_move
            if actor not in players:
                raise ValueError("snapshot last move names a non-player")
            if action != FORFEIT_ACTION:
                game.describe_action(action)
        if packet.revision < row["revision"] or (packet.revision == row["revision"] and packet.sequence < row["sequence"]):
            return True
        pending = row["pending_action"]
        advanced = packet.revision > row["revision"] or packet.sequence > row["sequence"]
        if pending is not None and advanced:
            kind = Kind.RESIGN if pending == FORFEIT_ACTION else Kind.ACTION
            self.store.cancel_outgoing(row["id"], row["coordinator"], int(kind))
            pending = None
        row["players"] = list(players)
        row["state"], row["sequence"], row["revision"] = state, packet.sequence, packet.revision
        row["status"] = "finished" if decoded.get("finished") or (last_move and last_move[1] == FORFEIT_ACTION) else "active"
        row["last_actor"], row["last_action"] = last_move if last_move else (None, None)
        row["pending_action"], row["error"] = pending, ""
        self._save(row)
        self._emit("session_updated", row["id"], session=self.session(row["id"]), synchronized=True)
        self._resolve_battleship_pending(row)
        return True

    def _receive_chat(self, row: dict[str, Any], packet: Packet) -> bool:
        if row["status"] not in ("active", "finished") or packet.source not in row["players"]:
            return False
        try:
            text = packet.payload.decode("utf-8")
        except UnicodeDecodeError:
            return False
        if not 0 < len(packet.payload) <= 150:
            return False
        if self.store.add_chat(row["id"], packet.source, packet.sequence, text, "delivered"):
            self._emit("chat_message", row["id"], message={"sender": packet.source,
                       "sequence": packet.sequence, "body": text, "delivery": "delivered"})
        return True

    def _receive_opaque_state(self, row: dict[str, Any], packet: Packet) -> bool:
        if row["status"] not in ("active", "finished") or packet.source not in row["players"]:
            return False
        try:
            text = packet.payload.decode("utf-8")
        except UnicodeDecodeError:
            return False
        if not packet.payload or len(packet.payload) > 200 or not all(c.isprintable() or c in "\r\n\t" for c in text):
            return False
        game = self.registry.by_id(row["game_id"])
        decoder = getattr(game, "decode_opaque_state", None)
        decoded = None
        if callable(decoder):
            try:
                decoded = decoder(text)
            except Exception:
                return False
        sid = row["id"]
        self._opaque_log.debug("RX session=%s sender=%s target=%s sequence=%d plaintext=%r",
                               sid, packet.source, self.callsign, packet.sequence, text)
        fresh = self.store.add_opaque_state(sid, packet.source, packet.sequence,
                                            packet.payload, "delivered")
        if fresh:
            self._emit("opaque_state", sid, sender=packet.source, sequence=packet.sequence,
                       flags=packet.flags, payload=packet.payload, hidden_from_ui=True)
            if callable(decoder):
                self._emit("game_opaque_state", sid, sender=packet.source, sequence=packet.sequence,
                           state=decoded, hidden_from_ui=True)
        return True

    def _retry_loop(self) -> None:
        while not self._stop.wait(0.5):
            if not self.tx_enabled:
                continue
            if self.station_id_enabled:
                with self._lock:
                    now = time.monotonic()
                    final_due = [sid for sid, due_at in self._pending_final_ids.items() if due_at <= now]
                    for sid in final_due:
                        self._pending_final_ids.pop(sid, None)
                    active = any(row["status"] == "active" and self.callsign in row["players"]
                                 for row in self._sessions.values())
                if final_due:
                    self._send_station_id(force=True)
                elif active and self._last_id_at is not None and self._sent_since_id:
                    self._send_station_id()
            retry_target = getattr(self.transport, "retry_target", None)
            target = retry_target() if callable(retry_target) else None
            if callable(retry_target) and target is None:
                due = []
            else:
                due = self.store.due_outgoing(target=target)
            for item in due:
                if item["session"] not in self._sessions:
                    continue  # never retransmit outbox packets for a retired game
                self._transmit(item["frame"])
                self._emit("delivery", item["session"], target=item["target"],
                           kind=Kind(item["kind"]).name, sequence=item["sequence"],
                           state="retry", attempt=item["attempts"])
            for item in self.store.failed_outgoing():
                if item["session"] not in self._sessions:
                    continue
                key = (item["session"], item["target"], item["kind"], item["sequence"])
                if key not in self._reported_failed:
                    self._reported_failed.add(key)
                    self._emit("delivery", item["session"], target=item["target"],
                               kind=Kind(item["kind"]).name, sequence=item["sequence"],
                               state="unconfirmed")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self.station_id_enabled and self._sent_since_id:
            self._send_station_id(force=True)
        self.transport.close()
        self._worker.join(timeout=2.0)
        self.store.close()
