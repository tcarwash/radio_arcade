"""Transport-neutral cartridge sessions, with game rules owned by PICO-8.

The Python engine validates envelope/session rules, but cannot validate an
arbitrary cartridge's game semantics. Cartridges must validate received events
and provide their own checkpoints for recovering runtime state.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any


CONFIG = 0
EVENT = 1
FINISH = 2
TURN_BASED = 0
EVENT_DRIVEN = 1
GAME_ID = 9
_STATE = struct.Struct("!BIHBBHIB")
_CONFIG = struct.Struct("!BIHB")


class Pico8CartridgeGame:
    api_version = 1
    game_id = GAME_ID
    key = "pico8-cartridge"
    name = "PICO-8 Cartridge"
    min_players = 2
    max_players = 8
    # The separate /pico8/ launcher provides the cartridge frontend.
    ui_clients = ()

    def new_state(self, players: tuple[str, ...]) -> dict[str, Any]:
        if not 2 <= len(players) <= 8 or len(set(players)) != len(players):
            raise ValueError("PICO-8 requires 2 to 8 distinct players")
        return {"cart_hash": 0, "version": 0, "mode": TURN_BASED,
                "turn": 0, "events": 0, "digest": 0, "finished": False}

    def current_player(self, state, players):
        return None if state["finished"] else players[state["turn"]]

    def can_act(self, state, players, actor):
        if state["finished"] or actor not in players:
            return False
        if not state["cart_hash"]:
            return actor == players[0]
        return actor == players[0] or state["mode"] == EVENT_DRIVEN or actor == players[state["turn"]]

    def apply_action(self, state, players, actor, action: bytes):
        if not action or len(action) > 190 or not self.can_act(state, players, actor):
            raise ValueError("invalid PICO-8 action or turn")
        result = dict(state)
        if action[0] == CONFIG:
            if result["cart_hash"] or len(action) != _CONFIG.size or actor != players[0]:
                raise ValueError("only the host can configure an unconfigured cartridge")
            _, cart_hash, version, mode = _CONFIG.unpack(action)
            if not cart_hash or not version or mode not in (TURN_BASED, EVENT_DRIVEN):
                raise ValueError("invalid cartridge identity, version, or mode")
            result.update(cart_hash=cart_hash, version=version, mode=mode)
        elif action[0] == EVENT:
            if not result["cart_hash"]:
                raise ValueError("the host must configure the cartridge first")
            if result["mode"] == TURN_BASED and actor != players[result["turn"]]:
                raise ValueError("not this cartridge player's turn")
            if result["events"] == 65535:
                raise ValueError("cartridge event counter exhausted; start a new session")
            result["events"] += 1
            if result["mode"] == TURN_BASED:
                result["turn"] = (result["turn"] + 1) % len(players)
        elif action[0] == FINISH:
            if len(action) != 1 or actor != players[0] or not result["cart_hash"]:
                raise ValueError("only the host may finish a configured cartridge session")
            result["finished"] = True
        else:
            raise ValueError("unknown PICO-8 action type")
        result["digest"] = zlib.crc32(actor.encode("ascii") + action, result["digest"])
        return result

    def encode_state(self, state):
        return _STATE.pack(1, state["cart_hash"], state["version"], state["mode"],
                           state["turn"], state["events"], state["digest"], int(state["finished"]))

    def decode_state(self, payload):
        if len(payload) != _STATE.size:
            raise ValueError("invalid PICO-8 state length")
        schema, cart_hash, version, mode, turn, events, digest, finished = _STATE.unpack(payload)
        if (schema != 1 or mode not in (0, 1) or turn > 7 or finished > 1
                or bool(cart_hash) != bool(version)):
            raise ValueError("invalid PICO-8 state")
        return {"cart_hash": cart_hash, "version": version, "mode": mode,
                "turn": turn, "events": events, "digest": digest, "finished": bool(finished)}

    def validate_roster(self, state, players):
        if state["turn"] >= len(players) or not 2 <= len(players) <= 8:
            raise ValueError("PICO-8 state has an invalid roster")

    def describe_action(self, action):
        if not action or action[0] not in (CONFIG, EVENT, FINISH):
            raise ValueError("missing PICO-8 action")
        return {CONFIG: "selects cartridge", EVENT: "sends cartridge event",
                FINISH: "finishes cartridge"}.get(action[0], "unknown PICO-8 action")

    def view_model(self, state, players):
        return {"kind": "pico8-cartridge", "cart_hash": state["cart_hash"],
                "cart_version": state["version"],
                "mode": "event" if state["mode"] == EVENT_DRIVEN else "turn",
                "events": state["events"], "digest": state["digest"],
                "turn": None if state["mode"] == EVENT_DRIVEN else self.current_player(state, players),
                "finished": state["finished"]}
