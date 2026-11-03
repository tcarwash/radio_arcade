"""Deterministic 58-cell Hexxagon with one-byte clones and two-byte jumps."""
from typing import Any

HOLES = {(0, -1), (1, 0), (-1, 1)}
CELLS = tuple((q, r) for r in range(-4, 5) for q in range(-4, 5)
              if max(abs(q), abs(r), abs(q + r)) <= 4 and (q, r) not in HOLES)
INDEX = {cell: i for i, cell in enumerate(CELLS)}


def distance(a, b):
    q, r = CELLS[a]; x, y = CELLS[b]
    return max(abs(q - x), abs(r - y), abs(q + r - x - y))


def pack_move(source, destination):
    if not 0 <= source < len(CELLS) or not 0 <= destination < len(CELLS):
        raise ValueError("invalid hex")
    if distance(source, destination) == 1:
        return bytes((destination,))
    if distance(source, destination) == 2:
        return (0x8000 | source << 6 | destination).to_bytes(2, "big")
    raise ValueError("clone one hex or jump two hexes")


def moves(board, player):
    return [(a, b) for a, mark in enumerate(board) if mark == player
            for b, value in enumerate(board) if not value and distance(a, b) in (1, 2)]


class HexxagonGame:
    api_version = 1
    game_id = 8
    key = "hexxagon"
    name = "Hexxagon"
    min_players = max_players = 2

    def new_state(self, players):
        if len(players) != 2 or len(set(players)) != 2:
            raise ValueError("Hexxagon requires two different players")
        board = [0] * len(CELLS)
        for i, cell in enumerate(((0, -4), (4, -4), (4, 0), (0, 4), (-4, 4), (-4, 0))):
            board[INDEX[cell]] = 1 + i % 2
        return {"board": board, "turn": 0, "finished": False, "winner": -1}

    def current_player(self, state, players):
        return None if state["finished"] else players[state["turn"]]

    def apply_action(self, state, players, actor, action):
        if actor not in players or self.current_player(state, players) != actor:
            raise ValueError("it is not that player's turn")
        board, player = list(state["board"]), state["turn"] + 1
        if len(action) == 1 and action[0] < len(CELLS):
            target = action[0]
            if not any(value == player and distance(i, target) == 1 for i, value in enumerate(board)):
                raise ValueError("clone requires an adjacent friendly piece")
        elif len(action) == 2:
            code = int.from_bytes(action, "big")
            source, target = (code >> 6) & 63, code & 63
            if code & 0xF000 != 0x8000 or source >= len(CELLS) or target >= len(CELLS):
                raise ValueError("invalid jump encoding")
            if board[source] != player or distance(source, target) != 2:
                raise ValueError("jump requires your piece two hexes away")
            board[source] = 0
        else:
            raise ValueError("invalid Hexxagon move")
        if board[target]:
            raise ValueError("destination must be empty")
        board[target] = player
        for i, value in enumerate(board):
            if value and distance(i, target) == 1:
                board[i] = player
        turn = 1 - state["turn"]
        finished = not moves(board, turn + 1)
        if finished:
            board = [value or player for value in board]
        counts = [board.count(1), board.count(2)]
        winner = (0 if counts[0] > counts[1] else 1 if counts[1] > counts[0] else -1) if finished else -1
        return {"board": board, "turn": turn, "finished": finished, "winner": winner}

    def encode_state(self, state):
        if len(state["board"]) != len(CELLS) or any(value not in (0, 1, 2) for value in state["board"]) or state["turn"] not in (0, 1):
            raise ValueError("invalid Hexxagon state")
        packed = sum(value << (i * 2) for i, value in enumerate(state["board"]))
        packed |= state["turn"] << 116 | int(state["finished"]) << 117
        payload = packed.to_bytes(15, "little")
        self.decode_state(payload)
        return payload

    def decode_state(self, payload):
        if len(payload) != 15:
            raise ValueError("invalid Hexxagon snapshot size")
        packed = int.from_bytes(payload, "little")
        board = [(packed >> (i * 2)) & 3 for i in range(len(CELLS))]
        turn, finished = (packed >> 116) & 1, bool((packed >> 117) & 1)
        if packed >> 118 or 3 in board or (finished and 0 in board) or (not finished and not moves(board, turn + 1)):
            raise ValueError("invalid Hexxagon snapshot")
        a, b = board.count(1), board.count(2)
        return {"board": board, "turn": turn, "finished": finished,
                "winner": (0 if a > b else 1 if b > a else -1) if finished else -1}

    def describe_action(self, action):
        if len(action) == 1 and action[0] < len(CELLS):
            return f"clones to hex {action[0] + 1}"
        if len(action) == 2:
            code = int.from_bytes(action, "big")
            return f"jumps from hex {((code >> 6) & 63) + 1} to {(code & 63) + 1}"
        raise ValueError("invalid Hexxagon move")

    def view_model(self, state: dict[str, Any], players):
        return {"kind": "hexxagon", "cells": [dict(id=i, q=q, r=r, owner=state["board"][i]) for i, (q, r) in enumerate(CELLS)],
                "turn": self.current_player(state, players), "active_seat": state["turn"] + 1,
                "legal_moves": moves(state["board"], state["turn"] + 1) if not state["finished"] else [],
                "scores": {p: state["board"].count(i + 1) for i, p in enumerate(players)},
                "winner": players[state["winner"]] if state["winner"] >= 0 else None,
                "draw": state["finished"] and state["winner"] < 0}
