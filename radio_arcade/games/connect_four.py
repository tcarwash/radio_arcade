"""Classic 7×6 Connect Four, with a compact validated radio snapshot."""

from __future__ import annotations

from typing import Any

WIDTH, HEIGHT = 7, 6
SIZE = WIDTH * HEIGHT


def winning_marks(board: str) -> set[str]:
    marks: set[str] = set()
    for row in range(HEIGHT):
        for col in range(WIDTH):
            mark = board[row * WIDTH + col]
            if mark == " ":
                continue
            for dx, dy in ((1, 0), (0, 1), (1, 1), (-1, 1)):
                end_x, end_y = col + 3 * dx, row + 3 * dy
                if 0 <= end_x < WIDTH and 0 <= end_y < HEIGHT and all(
                    board[(row + step * dy) * WIDTH + col + step * dx] == mark
                    for step in range(1, 4)
                ):
                    marks.add(mark)
    return marks


class ConnectFourGame:
    api_version = 1
    game_id = 6  # 5 is reserved for standalone Radio Civ
    key = "connect-four"
    name = "Connect Four"
    min_players = 2
    max_players = 2

    def new_state(self, players: tuple[str, ...]) -> dict[str, Any]:
        if len(players) != 2 or len(set(players)) != 2:
            raise ValueError("Connect Four requires two different players")
        return {"board": " " * SIZE, "turn": 0, "winner": -1, "finished": False}

    def apply_action(self, state: dict[str, Any], players: tuple[str, ...], actor: str,
                     action: bytes) -> dict[str, Any]:
        if len(action) != 1 or action[0] >= WIDTH:
            raise ValueError("choose a column from 1 through 7")
        if state["finished"]:
            raise ValueError("game is over")
        if actor not in players or players[state["turn"]] != actor:
            raise ValueError("it is not that player's turn")
        col = action[0]
        board = list(state["board"])
        row = next((r for r in range(HEIGHT - 1, -1, -1) if board[r * WIDTH + col] == " "), None)
        if row is None:
            raise ValueError("column is full")
        board[row * WIDTH + col] = "X" if state["turn"] == 0 else "O"
        result = "".join(board)
        winner = state["turn"] if winning_marks(result) else -1
        return {"board": result, "turn": 1 - state["turn"], "winner": winner,
                "finished": winner >= 0 or " " not in result}

    def current_player(self, state: dict[str, Any], players: tuple[str, ...]) -> str | None:
        return None if state["finished"] else players[state["turn"]]

    def encode_state(self, state: dict[str, Any]) -> bytes:
        symbols = {" ": 0, "X": 1, "O": 2}
        packed = 0
        for index, mark in enumerate(state["board"]):
            packed |= symbols[mark] << (index * 2)
        result = 0 if not state["finished"] else 1 if state["winner"] < 0 else state["winner"] + 2
        return packed.to_bytes(11, "little") + bytes((state["turn"], result))

    def decode_state(self, payload: bytes) -> dict[str, Any]:
        if len(payload) != 13 or payload[11] > 1 or payload[12] > 3:
            raise ValueError("invalid Connect Four snapshot")
        packed = int.from_bytes(payload[:11], "little")
        if packed >> (SIZE * 2):
            raise ValueError("invalid padding bits")
        symbols = (" ", "X", "O")
        values = [(packed >> (i * 2)) & 3 for i in range(SIZE)]
        if 3 in values:
            raise ValueError("invalid Connect Four cell")
        board = "".join(symbols[v] for v in values)
        for col in range(WIDTH):
            seen_empty = False
            for row in range(HEIGHT - 1, -1, -1):
                mark = board[row * WIDTH + col]
                if mark == " ":
                    seen_empty = True
                elif seen_empty:
                    raise ValueError("floating Connect Four piece")
        x_count, o_count = board.count("X"), board.count("O")
        if x_count not in (o_count, o_count + 1) or payload[11] != x_count - o_count:
            raise ValueError("inconsistent Connect Four turn")
        winners = winning_marks(board)
        if len(winners) > 1 or ("X" in winners and x_count != o_count + 1) or ("O" in winners and x_count != o_count):
            raise ValueError("inconsistent Connect Four winner")
        expected = 2 if "X" in winners else 3 if "O" in winners else 1 if " " not in board else 0
        if payload[12] != expected:
            raise ValueError("Connect Four result does not match board")
        # A winning board must be reachable by a final move that created the win.
        if winners:
            winner_mark = next(iter(winners))
            if not any(
                not winning_marks(board[:r * WIDTH + c] + " " + board[r * WIDTH + c + 1:])
                for c in range(WIDTH)
                for r in range(HEIGHT)
                if board[r * WIDTH + c] == winner_mark and (r == 0 or board[(r - 1) * WIDTH + c] == " ")
            ):
                raise ValueError("unreachable Connect Four victory")
        return {"board": board, "turn": payload[11], "finished": bool(expected),
                "winner": expected - 2 if expected >= 2 else -1}

    def describe_action(self, action: bytes) -> str:
        if len(action) != 1 or action[0] >= WIDTH:
            raise ValueError("invalid Connect Four column")
        return f"drop in column {action[0] + 1}"

    def view_model(self, state: dict[str, Any], players: tuple[str, ...]) -> dict[str, Any]:
        return {"kind": "connect-four", "width": WIDTH, "height": HEIGHT,
                "cells": list(state["board"]),
                "turn": self.current_player(state, players),
                "marks": {players[0]: "X", players[1]: "O"},
                "winner": players[state["winner"]] if state["winner"] >= 0 else None,
                "draw": state["finished"] and state["winner"] < 0}
