"""Local, isolated hot-seat sessions for exploring the game interfaces."""

from .service import ArcadeService
from .store import SessionStore, default_data_dir


class _LocalTransport:
    def send(self, frame):
        pass

    def close(self):
        pass


class PracticeService(ArcadeService):
    def __init__(self, callsign):
        super().__init__(callsign, _LocalTransport(),
                         SessionStore(default_data_dir() / "practice.sqlite3"))

    def create_game(self, game, peer=None):
        definition = self.registry.by_id(game) if isinstance(game, int) else self.registry.by_key(game)
        players = [self.callsign]
        for i in range(1, definition.min_players):
            name = f"LOCAL{i}"
            while name in players:
                name += "X"
            players.append(name)
        with self._lock:
            row = self._new_session(definition.game_id, self.callsign, players, [], "lobby")
            self.start_game(row["id"])
            return row["id"]

    def _reliable(self, *args, **kwargs):
        pass

    def legal_action(self, session_id, action):
        with self._lock:
            row = self._sessions[self._sid(session_id)]
            if row["status"] != "active" or row["state"] is None:
                raise ValueError("game is not active")
            game = self.registry.by_id(row["game_id"])
            actor = game.current_player(game.decode_state(row["state"]), tuple(row["players"]))
            self._commit(row, actor, action)

    def send_chat(self, session_id, text):
        sid = self._sid(session_id)
        text = text.strip()
        if not text or len(text.encode("utf-8")) > 150:
            raise ValueError("chat must contain 1 to 150 UTF-8 bytes")
        with self._lock:
            row = self._sessions[sid]
            if row["status"] not in ("active", "finished"):
                raise ValueError("side chat is available once a game has started")
            row["chat_sequence"] = (row["chat_sequence"] + 1) & 0xFFFF
            seq = row["chat_sequence"]
            self.store.add_chat(sid, self.callsign, seq, text, "local")
            self._save(row)
            self._emit("chat_message", sid, message={"sender": self.callsign,
                       "sequence": seq, "body": text, "delivery": "local"})
            return seq
