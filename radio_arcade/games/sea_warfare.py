"""Compact Battleship variant with private tactical extras."""
from __future__ import annotations
from typing import Any

from .battleship import UNKNOWN, MISS, HIT

FLEET = (5, 4, 3, 3, 3, 2)  # carrier, battleship, cruiser, destroyer, submarine, patrol boat


class SeaWarfareGame:
    api_version = 1
    game_id = 13
    key = "sea-warfare"
    name = "Battleship: Sea Warfare"
    min_players = max_players = 2
    ui_clients = ("desktop", "web")

    def new_state(self, players: tuple[str, ...]) -> dict[str, Any]:
        if len(players) != 2 or len(set(players)) != 2:
            raise ValueError("Sea Warfare requires two different players")
        return {"shots": [[UNKNOWN] * 100 for _ in range(2)], "sonar": [[255] * 10 for _ in range(2)], "ready": [False, False],
                "turn": 0, "phase": "setup", "pending": -1, "pending_kind": 0,
                "used": [0, 0], "moves": [3, 3], "finished": False, "winner": -1}

    def current_player(self, state, players):
        return None if state["finished"] else players[state["turn"]]

    def apply_action(self, state, players, actor, action: bytes) -> dict[str, Any]:
        if state["finished"] or actor not in players or players[state["turn"]] != actor:
            raise ValueError("it is not your Sea Warfare turn")
        turn = state["turn"]
        if state["phase"] == "setup":
            if action != b"\x7f" or state["ready"][turn]:
                raise ValueError("finish deploying your fleet before readying")
            ready = list(state["ready"]); ready[turn] = True
            if all(ready):
                return {**state, "ready": ready, "phase": "call", "turn": 0}
            return {**state, "ready": ready, "turn": 1 - turn}
        if state["phase"] == "call":
            if len(action) != 1:
                raise ValueError("Sea Warfare actions are one byte")
            code = action[0]
            if code < 100:
                if state["shots"][turn][code] != UNKNOWN:
                    raise ValueError("that coordinate was already called")
                return {**state, "turn": 1-turn, "phase": "reply", "pending": code, "pending_kind": 0}
            if 100 <= code <= 189:
                used = list(state["used"])
                if used[turn] & 1: raise ValueError("the battleship salvo was already used")
                pair = code - 100; row, col = divmod(pair, 9)
                cells = (row*10+col, row*10+col+1)
                if any(state["shots"][turn][cell] != UNKNOWN for cell in cells):
                    raise ValueError("both salvo coordinates must be uncalled")
                used[turn] |= 1
                return {**state, "used": used, "turn": 1-turn, "phase": "reply", "pending": cells[0], "pending_kind": 1}
            if 190 <= code <= 199:
                used = list(state["used"])
                if used[turn] & 2: raise ValueError("submarine sonar was already used")
                used[turn] |= 2
                return {**state, "used": used, "turn": 1-turn, "phase": "reply", "pending": code-190, "pending_kind": 2}
            if 200 <= code <= 223:
                used = list(state["used"]); moves = list(state["moves"])
                ship, direction = divmod(code-200, 4)
                if ship >= len(FLEET): raise ValueError("unknown vessel")
                if ship == 2:
                    if used[turn] & 4: raise ValueError("the cruiser has already used its free move")
                    used[turn] |= 4
                else:
                    if moves[turn] <= 0: raise ValueError("no movement supplies remain")
                    moves[turn] -= 1
                return {**state, "used": used, "moves": moves, "turn": 1-turn,
                        "phase": "call", "pending": -1, "pending_kind": 0}
            raise ValueError("unknown Sea Warfare action")
        if state["phase"] != "reply" or len(action) != 1 or not 0x80 <= action[0] <= 0x97:
            raise ValueError("invalid Sea Warfare result")
        shooter = 1-turn; kind = state["pending_kind"]; result = action[0] & 0x1f
        shots = [list(x) for x in state["shots"]]
        used = list(state["used"])
        finished = bool(result & (0x10 if kind == 1 else 0x04)) if kind in (0, 1) else False
        if kind == 0:
            shots[shooter][state["pending"]] = HIT if result & 1 else MISS
        elif kind == 1:
            pair = state["pending"]; row, col = divmod(pair, 10)
            shots[shooter][pair] = HIT if result & 1 else MISS
            shots[shooter][pair+1] = HIT if result & 2 else MISS
        elif kind == 2:
            sonar = [list(x) for x in state["sonar"]]
            sonar[shooter][state["pending"]] = result & 3
        else:
            raise ValueError("invalid pending Sea Warfare action")
        return {**state, "shots": shots, "sonar": sonar if kind == 2 else state["sonar"], "used": used, "turn": shooter, "phase": "call", "pending": -1,
                "pending_kind": 0, "finished": finished, "winner": shooter if finished else -1}

    def encode_state(self, state) -> bytes:
        # Four results per byte. This 80-byte snapshot is smaller than the
        # classic game's 104-byte four-bit grid snapshot.
        packed = bytearray(80)
        for p in range(2):
            for i, value in enumerate(state["shots"][p]):
                packed[p*25+i//4] |= value << ((i % 4)*2)
        packed[50] = int(state["ready"][0]) | (int(state["ready"][1]) << 1)
        packed[51] = state["turn"]
        packed[52] = {"setup": 0, "call": 1, "reply": 2}[state["phase"]]
        packed[53] = state["pending"] if state["pending"] >= 0 else 255
        packed[54] = state["pending_kind"]
        packed[55], packed[56] = state["used"]
        packed[57], packed[58] = state["moves"]
        packed[59] = state["winner"] + 1 if state["finished"] else 0
        packed[60:70] = bytes(state["sonar"][0])
        packed[70:80] = bytes(state["sonar"][1])
        return bytes(packed)

    def decode_state(self, payload):
        if len(payload) != 80 or payload[50] > 3 or payload[51] > 1 or payload[52] > 2:
            raise ValueError("invalid Sea Warfare snapshot")
        shots = [[(payload[p*25+i//4] >> ((i % 4)*2)) & 3 for i in range(100)] for p in range(2)]
        phase = ("setup", "call", "reply")[payload[52]]
        pending = -1 if payload[53] == 255 else payload[53]
        kind = payload[54]
        if (any(v > 2 for row in shots for v in row) or payload[55] > 7 or payload[56] > 7
            or payload[57] > 3 or payload[58] > 3 or payload[59] > 2 or kind > 2
            or (phase == "reply") != (pending >= 0) or (phase == "call" and payload[50] != 3)
            or (phase == "setup" and payload[50] == 3) or (phase == "reply" and ((kind in (0,1) and pending > 99) or (kind == 2 and pending > 9)))
            or (phase != "reply" and kind != 0)):
            raise ValueError("invalid Sea Warfare snapshot fields")
        ready = [bool(payload[50]&1), bool(payload[50]&2)]
        if phase == "setup" and ready == [True, True]: raise ValueError("invalid Sea Warfare setup")
        if phase != "setup" and ready != [True, True]: raise ValueError("both fleets must deploy before firing")
        winner = payload[59]-1 if payload[59] else -1
        sonar = [list(payload[60:70]), list(payload[70:80])]
        if any(x not in (0, 1, 2, 255) for row in sonar for x in row):
            raise ValueError("invalid Sea Warfare sonar state")
        return {"shots": shots, "sonar": sonar, "ready": ready, "turn": payload[51], "phase": phase,
                "pending": pending, "pending_kind": kind, "used": [payload[55], payload[56]],
                "moves": [payload[57], payload[58]], "finished": winner >= 0, "winner": winner}

    def describe_action(self, action):
        if action == b"\x7f": return "deploys a Sea Warfare fleet"
        if len(action) != 1: raise ValueError("invalid Sea Warfare action")
        code = action[0]
        if code < 100: return f"fires at row {code//10+1}, column {code%10+1}"
        if 128 <= code <= 159: return "reports a compact combat result or calls a salvo"
        if code <= 189:
            pair=code-100; return f"fires a battleship salvo across row {pair//9+1}"
        if code <= 199: return f"runs submarine sonar on row {code-190+1}"
        if code <= 223: return "repositions a vessel"
        return "reports a tactical result"

    def view_model(self, state, players):
        shooter = state["turn"] if state["phase"] != "reply" else 1-state["turn"]
        return {"kind": "sea-warfare", "phase": state["phase"], "turn": players[state["turn"]],
                "pending": state["pending"], "pending_kind": state["pending_kind"],
                "shots": state["shots"][shooter], "ready": state["ready"],
                "sonar": state["sonar"][shooter],
                "used": state["used"], "moves": state["moves"],
                "winner": players[state["winner"]] if state["winner"] >= 0 else None}
