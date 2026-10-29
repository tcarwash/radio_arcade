"""Tic-tac-toe game rules and compact state codec."""

from __future__ import annotations

from typing import Any

_LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))


class TicTacToeGame:
    api_version = 1
    game_id = 1
    key = "tic-tac-toe"
    name = "Tic-tac-toe"
    min_players = 2
    max_players = 2

    def new_state(self, players: tuple[str, ...]) -> dict[str, Any]:
        if len(players) != 2 or len(set(players)) != 2:
            raise ValueError("tic-tac-toe requires two different players")
        return {"board": " " * 9, "turn": 0, "finished": False, "winner": -1}

    def apply_action(self, state: dict[str, Any], players: tuple[str, ...], actor: str,
                     action: bytes) -> dict[str, Any]:
        if len(players) != 2 or actor not in players:
            raise ValueError("unknown tic-tac-toe player")
        if len(action) != 1 or action[0] > 8:
            raise ValueError("move must select a board cell from 0 through 8")
        if state["finished"]:
            raise ValueError("game is over")
        player_index = players.index(actor)
        if player_index != state["turn"]:
            raise ValueError("it is not that player's turn")
        cell = action[0]
        board = list(state["board"])
        if board[cell] != " ":
            raise ValueError("square is occupied")
        board[cell] = "X" if player_index == 0 else "O"
        winner = next((player_index for a, b, c in _LINES
                       if board[a] == board[b] == board[c] == board[cell]), -1)
        finished = winner >= 0 or " " not in board
        return {"board": "".join(board), "turn": 1 - player_index,
                "finished": finished, "winner": winner}

    def current_player(self, state: dict[str, Any], players: tuple[str, ...]) -> str | None:
        return None if state["finished"] else players[state["turn"]]

    def encode_state(self, state: dict[str, Any]) -> bytes:
        symbols = {" ": 0, "X": 1, "O": 2}
        packed = 0
        for i, cell in enumerate(state["board"]):
            packed |= symbols[cell] << (2 * i)
        result = 0 if not state["finished"] else (1 if state["winner"] < 0 else state["winner"] + 2)
        return packed.to_bytes(3, "little") + bytes((state["turn"], result))

    def decode_state(self, payload: bytes) -> dict[str, Any]:
        if len(payload) != 5 or payload[3] > 1 or payload[4] > 3:
            raise ValueError("invalid tic-tac-toe snapshot")
        packed = int.from_bytes(payload[:3], "little")
        symbols = (" ", "X", "O")
        board = "".join(symbols[(packed >> (2 * i)) & 3] if ((packed >> (2 * i)) & 3) < 3 else "?"
                        for i in range(9))
        if "?" in board:
            raise ValueError("invalid tic-tac-toe board encoding")
        result = payload[4]
        x_count, o_count = board.count("X"), board.count("O")
        if packed >> 18 or x_count not in (o_count, o_count + 1) or payload[3] != (x_count - o_count):
            raise ValueError("inconsistent tic-tac-toe turn or board")
        winners = {board[a] for a, b, c in _LINES if board[a] != " " and board[a] == board[b] == board[c]}
        if (len(winners) > 1 or ("X" in winners and x_count != o_count + 1)
                or ("O" in winners and x_count != o_count)):
            raise ValueError("inconsistent tic-tac-toe winner")
        expected = 2 if "X" in winners else 3 if "O" in winners else 1 if " " not in board else 0
        if result != expected:
            raise ValueError("tic-tac-toe result does not match board")
        return {"board": board, "turn": payload[3], "finished": result != 0,
                "winner": -1 if result == 1 else result - 2 if result >= 2 else -1}

    def describe_action(self, action: bytes) -> str:
        if len(action) != 1 or action[0] > 8:
            raise ValueError("invalid move")
        return f"square {action[0] + 1}"

    @staticmethod
    def symbol(state: dict[str, Any], cell: int) -> str:
        return state["board"][cell]

    def view_model(self, state: dict[str, Any], players: tuple[str, ...]) -> dict[str, Any]:
        return {"kind": "grid", "width": 3, "height": 3,
                "cells": list(state["board"]), "labels": list(range(1, 10)),
                "marks": {players[0]: "X", players[1]: "O"},
                "turn": self.current_player(state, players),
                "winner": players[state["winner"]] if state["winner"] >= 0 else None,
                "draw": state["finished"] and state["winner"] < 0}
