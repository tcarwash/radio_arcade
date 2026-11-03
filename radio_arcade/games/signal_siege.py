"""Signal Siege: an open-hand, 1-4 station cooperative micro-deckbuilder.

Each on-air action is one byte. All randomness is deliberately absent; the
coordinator's ordinary START snapshot is sufficient to reproduce every turn.
"""

from __future__ import annotations

from typing import Any

MAX_HP = 18
MAX_SHIELD = 25
MAX_FOCUS = 10
MAX_CARDS = 8
STARTER = (0, 0, 0, 1, 1, 2)

# Card IDs are permanent wire-format identifiers. Do not reorder them.
CARDS = (
    ("Ping", "Attack for 3", "attack"),
    ("Firewall", "Gain 4 shield", "defense"),
    ("Tune", "Add 2 damage to your next attack", "support"),
    ("Beam", "Attack for 5", "attack"),
    ("Repair", "Restore 4 HP to a station", "support"),
    ("Fortify", "Gain 6 shield", "defense"),
    ("Burst", "Attack for 3 and gain 2 shield", "attack"),
    ("Scramble", "Attack for 2; weaken the next interference pulse by 2", "attack"),
    ("Overdrive", "Attack for 7; take 2 damage", "attack"),
    ("Relay", "Give a station 3 shield and 1 focus", "support"),
)
REWARDS = ((3, 4, 5), (6, 7, 8))
PHASES = ("battle", "draft", "finished")
RESULTS = ("", "victory", "defeat")


def pack_play(slot: int, target: int = 0) -> bytes:
    """Play one of the three visible cards, optionally targeting seat 0-3."""
    if not 0 <= slot < 3 or not 0 <= target < 4:
        raise ValueError("slot must be 0-2 and target must be 0-3")
    return bytes(((target << 2) | slot,))


def pack_draft(choice: int) -> bytes:
    if not 0 <= choice < 3:
        raise ValueError("reward choice must be 0-2")
    return bytes((0x40 | choice,))


class SignalSiegeGame:
    api_version = 1
    game_id = 11
    key = "signal-siege"
    name = "Signal Siege"
    min_players = 1
    max_players = 4

    def new_state(self, players: tuple[str, ...]) -> dict[str, Any]:
        if not 1 <= len(players) <= 4 or len(set(players)) != len(players):
            raise ValueError("Signal Siege requires one to four unique stations")
        return {"phase": "battle", "wave": 1, "turn": 0, "boss": self._boss_hp(1, len(players)),
                "round": 0, "jam": 0, "result": "", "finished": False,
                "stations": [{"hp": MAX_HP, "shield": 0, "focus": 0, "deck": list(STARTER)}
                             for _ in players]}

    @staticmethod
    def _boss_hp(wave: int, count: int) -> int:
        return (7 + wave * 3) * (1 if count == 1 else count * 3 // 2)

    def validate_roster(self, state: dict[str, Any], players: tuple[str, ...]) -> None:
        if len(state["stations"]) != len(players):
            raise ValueError("Signal Siege snapshot has a different player count")

    def current_player(self, state: dict[str, Any], players: tuple[str, ...]) -> str | None:
        self.validate_roster(state, players)
        return None if state["finished"] else players[state["turn"]]

    def apply_action(self, state: dict[str, Any], players: tuple[str, ...], actor: str,
                     action: bytes) -> dict[str, Any]:
        if self.current_player(state, players) != actor:
            raise ValueError("it is not that station's turn")
        if len(action) != 1:
            raise ValueError("Signal Siege actions must be one byte")
        value = action[0]
        next_state = {**state, "stations": [{**s, "deck": list(s["deck"])} for s in state["stations"]]}
        seat = next_state["turn"]
        station = next_state["stations"][seat]
        if next_state["phase"] == "draft":
            if value not in (0x40, 0x41, 0x42):
                raise ValueError("choose a reward using 0x40, 0x41, or 0x42")
            if len(station["deck"]) >= MAX_CARDS:
                raise ValueError("station deck is full")
            station["deck"].append(REWARDS[next_state["wave"] - 1][value & 3])
            if seat + 1 < len(players):
                next_state["turn"] += 1
            else:
                next_state.update(phase="battle", wave=next_state["wave"] + 1, turn=0,
                                  round=0, jam=0, boss=self._boss_hp(next_state["wave"] + 1, len(players)))
                for ally in next_state["stations"]:
                    ally["hp"] = min(MAX_HP, ally["hp"] + 6)
            return next_state

        if next_state["phase"] != "battle" or value & 0xF0 or value & 3 == 3:
            raise ValueError("choose hand slot 0-2 and target seat 0-3")
        slot, target = value & 3, (value >> 2) & 3
        if target >= len(players):
            raise ValueError("target station does not exist")
        card = station["deck"].pop(slot)
        station["deck"].append(card)
        recipient = next_state["stations"][target]
        damage = 0
        if card == 0: damage = 3
        elif card == 1: station["shield"] = min(MAX_SHIELD, station["shield"] + 4)
        elif card == 2: station["focus"] = min(MAX_FOCUS, station["focus"] + 2)
        elif card == 3: damage = 5
        elif card == 4: recipient["hp"] = min(MAX_HP, recipient["hp"] + 4)
        elif card == 5: station["shield"] = min(MAX_SHIELD, station["shield"] + 6)
        elif card == 6:
            damage = 3
            station["shield"] = min(MAX_SHIELD, station["shield"] + 2)
        elif card == 7:
            damage = 2
            next_state["jam"] = min(6, next_state["jam"] + 2)
        elif card == 8:
            damage = 7
            station["hp"] = max(0, station["hp"] - 2)
        elif card == 9:
            recipient["shield"] = min(MAX_SHIELD, recipient["shield"] + 3)
            recipient["focus"] = min(MAX_FOCUS, recipient["focus"] + 1)
        if damage:
            next_state["boss"] = max(0, next_state["boss"] - damage - station["focus"])
            station["focus"] = 0
        if next_state["boss"] == 0:
            if next_state["wave"] == 3:
                next_state.update(phase="finished", result="victory", finished=True)
            else:
                next_state.update(phase="draft", turn=0)
        elif any(s["hp"] == 0 for s in next_state["stations"]):
            next_state.update(phase="finished", result="defeat", finished=True)
        elif seat + 1 < len(players):
            next_state["turn"] += 1
        else:
            next_state["turn"] = 0
            next_state["round"] = min(255, next_state["round"] + 1)
            # Pulse every second team round. Each station gets breathing room
            # to build shield or focus before the next interference burst.
            if next_state["round"] % 2 == 0:
                pulse = max(0, (5 if len(players) == 1 else 3) + next_state["wave"] - next_state["jam"])
                next_state["jam"] = 0
                for ally in next_state["stations"]:
                    injury = max(0, pulse - ally["shield"])
                    ally["shield"] = max(0, ally["shield"] - pulse)
                    ally["hp"] = max(0, ally["hp"] - injury)
                if any(s["hp"] == 0 for s in next_state["stations"]):
                    next_state.update(phase="finished", result="defeat", finished=True)
        return next_state

    def encode_state(self, state: dict[str, Any]) -> bytes:
        if state["phase"] not in PHASES or state["result"] not in RESULTS:
            raise ValueError("unknown Signal Siege phase or outcome")
        count = len(state["stations"])
        head = (1, count, PHASES.index(state["phase"]), state["wave"], state["turn"],
                state["boss"], state["round"], state["jam"], RESULTS.index(state["result"]))
        result = bytearray(head)
        for station in state["stations"]:
            deck = station["deck"]
            result.extend((station["hp"], station["shield"], station["focus"], len(deck)))
            result.extend(deck)
        payload = bytes(result)
        self.decode_state(payload)
        return payload

    def decode_state(self, payload: bytes) -> dict[str, Any]:
        if len(payload) < 19 or payload[0] != 1 or payload[1] not in range(1, 5):
            raise ValueError("invalid Signal Siege snapshot header")
        _, count, phase, wave, turn, boss, round_number, jam, outcome = payload[:9]
        if (phase >= len(PHASES) or outcome >= len(RESULTS) or wave not in (1, 2, 3)
                or turn >= count or jam > 6):
            raise ValueError("invalid Signal Siege snapshot metadata")
        stations, offset = [], 9
        for _ in range(count):
            if len(payload) < offset + 4:
                raise ValueError("truncated Signal Siege station")
            hp, shield, focus, deck_length = payload[offset:offset + 4]
            offset += 4
            if (hp > MAX_HP or shield > MAX_SHIELD or focus > MAX_FOCUS
                    or not 6 <= deck_length <= MAX_CARDS or len(payload) < offset + deck_length):
                raise ValueError("invalid Signal Siege station stats or deck")
            deck = list(payload[offset:offset + deck_length])
            if any(card >= len(CARDS) for card in deck):
                raise ValueError("invalid Signal Siege card ID")
            stations.append({"hp": hp, "shield": shield, "focus": focus, "deck": deck})
            offset += deck_length
        if offset != len(payload):
            raise ValueError("trailing Signal Siege snapshot data")
        phase_name = PHASES[phase]
        if (phase_name == "battle" and (boss == 0 or outcome != 0)
                or phase_name == "draft" and (boss != 0 or wave == 3 or outcome != 0)
                or phase_name == "finished" and (outcome == 0 or (outcome == 1) != (boss == 0))
                or phase_name != "finished" and any(s["hp"] == 0 for s in stations)):
            raise ValueError("inconsistent Signal Siege phase or outcome")
        return {"phase": phase_name, "wave": wave, "turn": turn, "boss": boss,
                "round": round_number, "jam": jam, "result": RESULTS[outcome],
                "finished": phase_name == "finished", "stations": stations}

    def describe_action(self, action: bytes) -> str:
        if len(action) != 1:
            raise ValueError("Signal Siege action must be one byte")
        if action[0] in (0x40, 0x41, 0x42):
            return f"drafts reward {action[0] - 0x40 + 1}"
        if action[0] < 16 and action[0] & 3 != 3:
            return f"plays hand card {(action[0] & 3) + 1} targeting station {((action[0] >> 2) & 3) + 1}"
        raise ValueError("invalid Signal Siege action")

    def view_model(self, state: dict[str, Any], players: tuple[str, ...]) -> dict[str, Any]:
        self.validate_roster(state, players)
        offers = REWARDS[state["wave"] - 1] if state["phase"] == "draft" else ()
        return {"kind": "signal-siege", "wave": state["wave"], "waves": 3,
                "phase": state["phase"], "boss_hp": state["boss"],
                "boss_max_hp": self._boss_hp(state["wave"], len(players)),
                "round": state["round"], "jam": state["jam"], "result": state["result"],
                "pulse_damage": (5 if len(players) == 1 else 3) + state["wave"],
                "pulse_every": 2,
                "rounds_until_pulse": 2 - state["round"] % 2,
                "turn": self.current_player(state, players),
                "stations": [dict(callsign=call, hp=s["hp"], max_hp=MAX_HP,
                                  shield=s["shield"], focus=s["focus"], deck_size=len(s["deck"]),
                                  hand=[self._card_view(i, card) for i, card in enumerate(s["deck"][:3])],
                                  acquired=[self._card_view(i, card) for i, card in enumerate(s["deck"]) if card >= 3])
                             for call, s in zip(players, state["stations"])],
                "rewards": [self._card_view(i, card) for i, card in enumerate(offers)],
                "scores": {call: s["hp"] for call, s in zip(players, state["stations"])},
                "winners": list(players) if state["result"] == "victory" else []}

    @staticmethod
    def _card_view(slot: int, card_id: int) -> dict[str, Any]:
        name, description, family = CARDS[card_id]
        return {"slot": slot, "id": card_id, "name": name, "description": description, "family": family}
