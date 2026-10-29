"""Small, open-information Nim game for two to four stations."""

from __future__ import annotations

from typing import Any


class NimGame:
    api_version = 1
    game_id = 2
    key = "nim"
    name = "Nim"
    min_players = 2
    max_players = 4

    def new_state(self, players: tuple[str, ...]) -> dict[str, Any]:
        if not self.min_players <= len(players) <= self.max_players or len(set(players)) != len(players):
            raise ValueError("Nim supports two to four unique players")
        return {"piles": [1, 3, 5, 7], "turn": 0, "winner": -1, "finished": False, "num_players": len(players)}

    def current_player(self, state: dict[str, Any], players: tuple[str, ...]) -> str | None:
        return None if state["winner"] >= 0 else players[state["turn"]]

    def validate_roster(self, state: dict[str, Any], players: tuple[str, ...]) -> None:
        if state["num_players"] != len(players):
            raise ValueError("Nim state player count does not match the roster")

    def apply_action(self, state: dict[str, Any], players: tuple[str, ...], actor: str,
                     action: bytes) -> dict[str, Any]:
        if actor not in players or self.current_player(state, players) != actor:
            raise ValueError("it is not that player's turn")
        if len(action) != 2:
            raise ValueError("Nim move is [pile index, number to remove]")
        pile, amount = action
        piles = list(state["piles"])
        if pile >= len(piles) or amount < 1 or amount > piles[pile]:
            raise ValueError("choose a non-empty pile and remove at least one item")
        piles[pile] -= amount
        finished = not any(piles)
        return {"piles": piles, "turn": (state["turn"] + 1) % len(players),
                "winner": players.index(actor) if finished else -1, "finished": finished, "num_players": len(players)}

    def encode_state(self, state: dict[str, Any]) -> bytes:
        piles = state["piles"]
        if len(piles) != 4 or any(not 0 <= n <= 255 for n in piles):
            raise ValueError("invalid Nim pile state")
        winner = state["winner"] if state["winner"] >= 0 else 255
        return bytes(piles) + bytes((state["turn"], winner, state["num_players"]))

    def decode_state(self, payload: bytes) -> dict[str, Any]:
        if (len(payload) != 7 or not self.min_players <= payload[6] <= self.max_players
                or payload[4] >= payload[6] or (payload[5] != 255 and payload[5] >= payload[6])):
            raise ValueError("invalid Nim snapshot")
        piles, turn, winner, count = payload[:4], payload[4], payload[5], payload[6]
        if any(value > initial for value, initial in zip(piles, (1, 3, 5, 7))):
            raise ValueError("Nim pile exceeds its starting size")
        if ((not any(piles)) != (winner != 255)
                or (winner != 255 and winner != (turn - 1) % count)):
            raise ValueError("inconsistent Nim result")
        return {"piles": list(payload[:4]), "turn": payload[4],
                "winner": -1 if payload[5] == 255 else payload[5],
                "finished": payload[5] != 255, "num_players": payload[6]}

    def describe_action(self, action: bytes) -> str:
        if len(action) != 2:
            raise ValueError("invalid Nim move")
        return f"take {action[1]} from pile {action[0] + 1}"

    def view_model(self, state: dict[str, Any], players: tuple[str, ...]) -> dict[str, Any]:
        winner = state["winner"]
        return {"kind": "piles", "piles": list(state["piles"]),
                "players": list(players), "turn": self.current_player(state, players),
                "winner": players[winner] if winner >= 0 else None}
