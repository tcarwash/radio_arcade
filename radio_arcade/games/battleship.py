"""Experimental Battleship protocol. Fleet coordinates are always private."""
from __future__ import annotations
from typing import Any

FLEET = (5, 4, 3, 3, 2)
UNKNOWN, MISS, HIT = 0, 1, 2


class BattleshipGame:
    api_version = 1
    game_id = 12
    key = "battleship"
    name = "Battleship"
    min_players = max_players = 2
    ui_clients = ("web",)

    def new_state(self, players: tuple[str, ...]) -> dict[str, Any]:
        if len(players) != 2 or len(set(players)) != 2:
            raise ValueError("Battleship requires two different players")
        return {"shots": [[UNKNOWN] * 100, [UNKNOWN] * 100,], "ready": [False, False],
                "turn": 0, "phase": "setup", "pending": -1, "finished": False, "winner": -1}

    def current_player(self, state, players):
        return None if state["finished"] else players[state["turn"]]

    def apply_action(self, state, players, actor, action: bytes) -> dict[str, Any]:
        if state["finished"] or actor not in players or players[state["turn"]] != actor:
            raise ValueError("it is not your Battleship turn")
        turn = state["turn"]
        if state["phase"] == "setup":
            if action != b"\x7f" or state["ready"][turn]:
                raise ValueError("finish placing your fleet before readying")
            ready = list(state["ready"]); ready[turn] = True
            if all(ready):
                return {**state, "ready": ready, "phase": "call", "turn": 0}
            return {**state, "ready": ready, "turn": 1 - turn}
        if state["phase"] == "call":
            if len(action) != 1 or action[0] >= 100:
                raise ValueError("call a target coordinate")
            shots = state["shots"][turn]
            if shots[action[0]] != UNKNOWN:
                raise ValueError("that coordinate was already called")
            return {**state, "turn": 1-turn, "phase": "reply", "pending": action[0]}
        if len(action) != 1 or action[0] < 0x80 or (action[0]&0x7f) > 6:
            raise ValueError("reply with miss, hit, or sunk ship ID 0–4")
        result = action[0]&0x7f
        shooter = 1-turn
        shots = [list(x) for x in state["shots"]]
        if result >= 2 and any(value == result+1 for value in shots[shooter]):
            raise ValueError("that ship was already reported sunk")
        shots[shooter][state["pending"]] = MISS if result == 0 else HIT if result == 1 else 1 + result
        sunk = {v - 3 for v in shots[shooter] if v >= 3}
        finished = result >= 2 and len(sunk) == len(FLEET)
        return {**state, "shots": shots, "turn": shooter, "phase": "call", "pending": -1,
                "finished": finished, "winner": shooter if finished else -1}

    def encode_state(self, state) -> bytes:
        # Two public shot grids: 4 bits per cell, no fleet coordinates.
        packed = bytearray(104)
        for p in range(2):
            for i, value in enumerate(state["shots"][p]):
                packed[p*50+i//2] |= value << ((i % 2)*4)
        packed[100] = int(state["ready"][0]) | (int(state["ready"][1]) << 1)
        packed[101] = state["turn"]
        packed[102] = {"setup": 0, "call": 1, "reply": 2}[state["phase"]]
        packed[103] = state["pending"] if state["pending"] >= 0 else 255
        # Finished/winner are inferable from the public sunk IDs.
        return bytes(packed)

    def decode_state(self, payload):
        if len(payload) != 104 or payload[100] > 3 or payload[101] > 1 or payload[102] > 2:
            raise ValueError("invalid Battleship snapshot")
        shots = [[(payload[p*50+i//2] >> ((i % 2)*4)) & 15 for i in range(100)] for p in range(2)]
        if any(x > 7 for row in shots for x in row):
            raise ValueError("invalid Battleship shot result")
        phase = ("setup", "call", "reply")[payload[102]]
        pending = -1 if payload[103] == 255 else payload[103]
        if (pending > 99 or (phase == "reply") != (pending >= 0)
                or (phase == "call" and payload[100] != 3)
                or (phase == "setup" and payload[100] == 3)):
            raise ValueError("invalid pending Battleship shot")
        ready = [bool(payload[100]&1), bool(payload[100]&2)]
        if phase == "setup" and (ready == [True, True] or (ready == [False, False] and payload[101] != 0)):
            raise ValueError("invalid Battleship setup turn")
        if phase != "setup" and ready != [True, True]:
            raise ValueError("both fleets must be ready before firing")
        winner = -1
        sunk = [{v-3 for v in row if v >= 3} for row in shots]
        for p in range(2):
            if len(sunk[p]) == 5:
                winner = 1-p
        return {"shots": shots, "ready": ready, "turn": payload[101], "phase": phase,
                "pending": pending, "finished": winner >= 0, "winner": winner}

    def describe_action(self, action):
        if action == b"\x7f": return "deploys fleet"
        if len(action) != 1: raise ValueError("invalid Battleship action")
        if action[0] < 100: return f"calls row {action[0]//10+1}, column {action[0]%10+1}"
        result = action[0]&0x7f
        if result > 6: raise ValueError("invalid Battleship result")
        return "reports miss" if result == 0 else "reports hit" if result == 1 else f"reports sunk ship {result-2}"

    def view_model(self, state, players):
        shooter = state["turn"] if state["phase"] != "reply" else 1-state["turn"]
        return {"kind": "battleship", "phase": state["phase"], "turn": players[state["turn"]],
                "pending": state["pending"], "shots": state["shots"][shooter],
                "ready": state["ready"], "winner": players[state["winner"]] if state["winner"] >= 0 else None}
