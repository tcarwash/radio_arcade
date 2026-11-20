"""Local, isolated practice sessions against a simple computer opponent."""

from .service import ArcadeService
from .store import SessionStore, default_data_dir
from .wire import FORFEIT_ACTION
from .practice_ai import choose as choose_ai, fleet as random_fleet


class _LocalTransport:
    supports_broadcast = False
    def send(self, frame):
        pass

    def close(self):
        pass


class PracticeService(ArcadeService):
    def __init__(self, callsign):
        super().__init__(callsign, _LocalTransport(),
                         SessionStore(default_data_dir() / "practice.sqlite3"))

    def create_game(self, game, peer=None, *, hosted=False):
        if hosted:
            raise ValueError("hosted tables require broadcast radio mode")
        definition = self.registry.by_id(game) if isinstance(game, int) else self.registry.by_key(game)
        players = [self.callsign]
        for i in range(1, definition.min_players):
            name = "ARCADE-AI" if i == 1 else f"ARCADE-AI{i}"
            while name in players:
                name += "X"
            players.append(name)
        with self._lock:
            row = self._new_session(definition.game_id, self.callsign, players, [], "lobby")
            self.start_game(row["id"])
            if definition.game_id in (12, 13):
                from .games.battleship import FLEET as classic_fleet
                from .games.sea_warfare import FLEET as sea_fleet
                private = self.store.load_private_game_data(row["id"])
                private["practice_seats"] = {"1": {"ships": random_fleet(
                    sea_fleet if definition.game_id == 13 else classic_fleet), "hits": [],
                    "mines": [], "decoys": [], "fighter_used": False,
                    "destroyer_sweep_used": False}}
                self.store.save_private_game_data(row["id"], private)
            return row["id"]

    def _forfeit_actor(self, row):
        return self.session(row["id"])["current_player"]

    def _reliable(self, *args, **kwargs):
        pass

    def legal_action(self, session_id, action):
        if action == FORFEIT_ACTION:
            raise ValueError("use forfeit_game for a forfeit")
        with self._lock:
            row = self._sessions[self._sid(session_id)]
            if row["status"] != "active" or row["state"] is None:
                raise ValueError("game is not active")
            game = self.registry.by_id(row["game_id"])
            actor = game.current_player(game.decode_state(row["state"]), tuple(row["players"]))
            # A dealer's explicit finish command is legal even if it is the
            # other seat's turn; mirror radio coordinator behavior.
            if row["game_id"] in (9, 10) and action == b"\x02":
                actor = row["coordinator"]
            self._commit(row, actor, action)
            if not getattr(self, "_resolving_private", False):
                self._run_ai_turns(row)

    def place_battleship_fleet(self, session_id, ships, mines=None, decoys=None):
        """Store the human fleet by seat so both local fleets stay private."""
        sid = self._sid(session_id)
        self._require_player()
        with self._lock:
            row = self._sessions[sid]
            if row["game_id"] not in (12, 13) or row["status"] != "active":
                raise ValueError("select an active Battleship table")
            from .games.battleship import FLEET as classic_fleet
            from .games.sea_warfare import FLEET as sea_fleet
            lengths = sea_fleet if row["game_id"] == 13 else classic_fleet
            if len(ships) != len(lengths) or any(len(ship) != n for ship, n in zip(ships, lengths)):
                raise ValueError("place every vessel in the fleet with its displayed length")
            cells = [c for ship in ships for c in ship]
            if any(type(c) is not int or not 0 <= c < 100 for c in cells) or len(set(cells)) != len(cells):
                raise ValueError("fleet cells must be unique board coordinates")
            for ship, length in zip(ships, lengths):
                rows, cols = [c // 10 for c in ship], [c % 10 for c in ship]
                axis = cols if len(set(rows)) == 1 else rows if len(set(cols)) == 1 else []
                if sorted(axis) != list(range(min(axis, default=-1), min(axis, default=-1) + length)):
                    raise ValueError("each ship must be straight and consecutive")
            mines, decoys = list(mines or []), list(decoys or [])
            if row["game_id"] == 12 and (mines or decoys):
                raise ValueError("classic Battleship does not use mines or decoys")
            if row["game_id"] == 13 and (len(mines) > 1 or len(decoys) > 1
                    or any(type(c) is not int or not 0 <= c < 100 for c in mines + decoys)
                    or (mines and mines[0] not in ships[5])
                    or (decoys and decoys[0] in cells)
                    or (mines and decoys and mines[0] == decoys[0])):
                raise ValueError("place a mine on your fleet and a decoy in open water, or skip either")
            state = self.registry.by_id(row["game_id"]).decode_state(row["state"])
            seat = state["turn"]
            if state["phase"] != "setup" or state["ready"][seat] or row["players"][seat] != self.callsign:
                raise ValueError("your fleet is already deployed for this game")
            private = self.store.load_private_game_data(sid)
            seats = private.setdefault("practice_seats", {})
            seats[str(seat)] = {"ships": ships, "hits": [], "mines": mines, "decoys": decoys,
                                "fighter_used": False, "destroyer_sweep_used": False}
            self.store.save_private_game_data(sid, private)
            self.legal_action(sid, b"\x7f")

    def _private_game_view_data(self, sid):
        private = self.store.load_private_game_data(sid)
        row = self._sessions[sid]
        if "practice_seats" in private and self.callsign in row["players"]:
            return private["practice_seats"].get(str(row["players"].index(self.callsign)), {})
        return private

    def _resolve_battleship_pending(self, row):
        if row["game_id"] not in (12, 13) or row["status"] != "active":
            return
        state = self.registry.by_id(row["game_id"]).decode_state(row["state"])
        if state["phase"] != "reply":
            return
        private = self.store.load_private_game_data(row["id"])
        seats = private.get("practice_seats", {})
        defender = state["turn"]
        if str(defender) not in seats:
            return
        if row["game_id"] == 12:
            # Keep both fleets in their original private records; never put the
            # AI fleet in the human's session view or rewrite a committed turn.
            data = seats[str(defender)]
            target = state["pending"]
            hits = set(data.get("hits", []))
            ship_id = next((i for i, ship in enumerate(data["ships"]) if target in ship), None)
            result = 0
            if ship_id is not None:
                hits.add(target)
                result = 2 + ship_id if all(cell in hits for cell in data["ships"][ship_id]) else 1
            data["hits"] = sorted(hits)
            self.store.save_private_game_data(row["id"], private)
            self._commit(row, row["players"][defender], bytes((0x80 | result,)))
            return
        saved_call = self.callsign
        self.callsign = row["players"][defender]
        self._resolving_private = True
        self.store.save_private_game_data(row["id"], seats[str(defender)])
        try:
            super()._resolve_battleship_pending(row)
            seats[str(defender)] = self.store.load_private_game_data(row["id"])
            resolved = self.registry.by_id(row["game_id"]).decode_state(row["state"])
            if resolved["phase"] == "call":
                resolved["turn"] = defender
                row["state"] = self.registry.by_id(row["game_id"]).encode_state(resolved)
                self._save(row)
                self._emit("session_updated", row["id"], session=self.session(row["id"]))
        finally:
            self.callsign = saved_call
            self._resolving_private = False
            private["practice_seats"] = seats
            self.store.save_private_game_data(row["id"], private)

    def _run_ai_turns(self, row):
        players = tuple(row["players"])
        ai_players = {p for p in players if p.startswith("ARCADE-AI")}
        while row["status"] == "active":
            game = self.registry.by_id(row["game_id"])
            state = game.decode_state(row["state"])
            actor = game.current_player(state, players)
            if actor not in ai_players:
                break
            if row["game_id"] in (12, 13) and state["phase"] == "setup":
                action = b"\x7f"
            else:
                action = choose_ai(game, state, players, actor)
            if action is None:
                break
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
