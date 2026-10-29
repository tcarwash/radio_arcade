"""Example external game. ID 240 is for local experimentation only.

Register CountdownGame() on a GameRegistry alongside the built-ins on every
station before starting a session. Do not transmit with an uncoordinated ID.
"""


class CountdownGame:
    api_version = 1
    game_id = 240
    key = "example-countdown"
    name = "Countdown (example)"
    min_players = max_players = 2

    def new_state(self, players):
        if len(players) != 2 or len(set(players)) != 2:
            raise ValueError("two distinct players required")
        return {"remaining": 7, "turn": 0, "finished": False, "winner": -1}

    def current_player(self, state, players):
        return None if state["finished"] else players[state["turn"]]

    def apply_action(self, state, players, actor, action):
        if self.current_player(state, players) != actor:
            raise ValueError("not your turn")
        if len(action) != 1 or action[0] not in (1, 2) or action[0] > state["remaining"]:
            raise ValueError("take one or two remaining counters")
        remaining = state["remaining"] - action[0]
        return {"remaining": remaining, "turn": 1 - state["turn"],
                "finished": remaining == 0,
                "winner": players.index(actor) if remaining == 0 else -1}

    def encode_state(self, state):
        return bytes((state["remaining"], state["turn"], state["winner"] + 1))

    def decode_state(self, payload):
        if len(payload) != 3 or payload[0] > 7 or payload[1] > 1 or payload[2] > 2:
            raise ValueError("invalid countdown state")
        remaining, turn, winner_code = payload
        if (remaining == 0) != (winner_code != 0):
            raise ValueError("inconsistent countdown result")
        return {"remaining": remaining, "turn": turn, "winner": winner_code - 1,
                "finished": winner_code != 0}

    def describe_action(self, action):
        if len(action) != 1 or action[0] not in (1, 2):
            raise ValueError("invalid countdown action")
        return f"take {action[0]}"

    def view_model(self, state, players):
        return {"kind": "countdown", "remaining": state["remaining"],
                "turn": self.current_player(state, players),
                "winner": players[state["winner"]] if state["finished"] else None}
