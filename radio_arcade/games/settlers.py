"""Island Settlers: a compact, public-information resource/route game.

Inspired by the general genre of settlement-building board games, this is an
original small radio game, not an implementation of Catan's board or rules.
All game state fits in one Radio Arcade packet, even with four callsigns.
"""

from __future__ import annotations

from copy import deepcopy
import zlib


RESOURCES = ("timber", "clay", "wool", "grain", "ore")
SECTORS = (0, 1, 2, 3, 4, 0)  # six production regions; roll 1–6
SITES = 18
EDGES = tuple((i, (i + 1) % SITES) for i in range(SITES)) + tuple(
    (i, i + 9) for i in range(9)
)
START_SITES = {2: (0, 9), 3: (0, 6, 12), 4: (0, 4, 9, 13)}
ROAD_COST = (1, 1, 0, 0, 0)
SETTLEMENT_COST = (1, 1, 1, 1, 0)
CITY_COST = (0, 0, 0, 2, 3)


class IslandSettlersGame:
    api_version = 1
    game_id = 4
    key = "island-settlers"
    name = "Island Settlers"
    min_players = 2
    max_players = 4

    def new_state(self, players: tuple[str, ...]) -> dict:
        if not self.min_players <= len(players) <= self.max_players or len(set(players)) != len(players):
            raise ValueError("Island Settlers requires 2–4 distinct stations")
        sites = [0] * SITES
        for index, site in enumerate(START_SITES[len(players)]):
            sites[site] = index + 1
        return {"count": len(players), "turn": 0, "phase": 0, "roll": 0,
                "winner": -1, "finished": False,
                "rng": zlib.crc32("/".join(players).encode("ascii")) & 0xffff,
                "sites": sites, "roads": [0] * len(EDGES),
                "stocks": [[5, 5, 2, 2, 0] for _ in players] + [[0] * 5 for _ in range(4-len(players))]}

    def validate_roster(self, state: dict, players: tuple[str, ...]) -> None:
        if len(players) != state["count"] or len(set(players)) != len(players):
            raise ValueError("Island Settlers roster mismatch")

    def current_player(self, state: dict, players: tuple[str, ...]) -> str | None:
        return None if state["finished"] else players[state["turn"]]

    @staticmethod
    def _pay(stock: list[int], costs: tuple[int, ...]) -> None:
        if any(owned < cost for owned, cost in zip(stock, costs)):
            raise ValueError("not enough resources")
        for index, cost in enumerate(costs):
            stock[index] -= cost

    def apply_action(self, state: dict, players: tuple[str, ...], actor: str, action: bytes) -> dict:
        if state["finished"] or actor != self.current_player(state, players):
            raise ValueError("not this station's turn")
        if not action:
            raise ValueError("missing settlement action")
        opcode = action[0]
        if len(action) != (1 if opcode in (1, 6) else 2) or opcode not in (1, 2, 3, 4, 5, 6):
            raise ValueError("invalid settlement action")
        result = deepcopy(state)
        player = result["turn"] + 1
        stock = result["stocks"][player - 1]
        if opcode == 1:  # roll for all players
            if result["phase"] != 0:
                raise ValueError("resources already produced this turn")
            result["rng"] = (result["rng"] * 25173 + 13849) & 0xffff
            result["roll"] = result["rng"] % 6 + 1
            result["phase"] = 1
            for site, owner in enumerate(result["sites"]):
                if not owner:
                    continue
                multiplier = 2 if owner & 0x80 else 1
                index = (owner & 0x7f) - 1
                for sector in (site // 3, (site // 3 + 5) % 6):
                    if sector + 1 == result["roll"]:
                        resource = SECTORS[sector]
                        result["stocks"][index][resource] = min(255, result["stocks"][index][resource] + multiplier)
            return result
        if result["phase"] != 1:
            raise ValueError("roll production before building, trading or ending")
        if opcode == 2:  # road
            edge = action[1]
            if edge >= len(EDGES) or result["roads"][edge]:
                raise ValueError("road is unavailable")
            a, b = EDGES[edge]
            adjacent = any((result["sites"][site] & 0x7f) == player for site in (a, b))
            connected = any(owner == player and set(EDGES[i]) & {a, b}
                            for i, owner in enumerate(result["roads"]))
            if not adjacent and not connected:
                raise ValueError("road must connect to your settlement or road")
            self._pay(stock, ROAD_COST)
            result["roads"][edge] = player
        elif opcode == 3:  # outpost
            site = action[1]
            if site >= SITES or result["sites"][site]:
                raise ValueError("site is unavailable")
            if any(result["sites"][other] for a, b in EDGES[:SITES]
                   for other in ((b,) if a == site else (a,) if b == site else ())):
                raise ValueError("outpost must not touch another outpost")
            if not any(owner == player and site in EDGES[i]
                       for i, owner in enumerate(result["roads"])):
                raise ValueError("outpost requires a connected road")
            self._pay(stock, SETTLEMENT_COST)
            result["sites"][site] = player
        elif opcode == 4:  # city
            site = action[1]
            if site >= SITES or result["sites"][site] != player:
                raise ValueError("only your outpost can be upgraded")
            self._pay(stock, CITY_COST)
            result["sites"][site] = player | 0x80
        elif opcode == 5:  # bank trade 3:1
            give, receive = action[1] >> 4, action[1] & 0x0f
            if give >= 5 or receive >= 5 or give == receive or stock[give] < 3:
                raise ValueError("trade three matching resources for one different resource")
            if stock[receive] == 255:
                raise ValueError("resource limit reached")
            stock[give] -= 3
            stock[receive] += 1
        else:  # end turn
            result["turn"] = (result["turn"] + 1) % result["count"]
            result["phase"], result["roll"] = 0, 0
        points = sum(2 if site & 0x80 else 1 for site in result["sites"]
                     if site and (site & 0x7f) == player)
        if points >= 6:
            result["winner"], result["finished"] = player - 1, True
        return result

    def encode_state(self, state: dict) -> bytes:
        winner = state["winner"] if state["winner"] >= 0 else 255
        payload = bytes((state["count"], state["turn"], state["phase"], state["roll"], winner))
        payload += state["rng"].to_bytes(2, "big")
        payload += bytes(state["sites"]) + bytes(state["roads"])
        payload += bytes(value for stock in state["stocks"] for value in stock)
        self.decode_state(payload)
        return payload

    def decode_state(self, payload: bytes) -> dict:
        if len(payload) != 7 + SITES + len(EDGES) + 20:
            raise ValueError("invalid Island Settlers snapshot length")
        count, turn, phase, roll, winner = payload[:5]
        sites = list(payload[7:7 + SITES])
        roads = list(payload[7 + SITES:7 + SITES + len(EDGES)])
        values = payload[7 + SITES + len(EDGES):]
        stocks = [list(values[i:i + 5]) for i in range(0, 20, 5)]
        if not 2 <= count <= 4 or turn >= count or phase not in (0, 1) or (roll == 0) != (phase == 0):
            raise ValueError("invalid turn or roll in Island Settlers state")
        if roll > 6 or (winner != 255 and winner >= count):
            raise ValueError("invalid roll or winner")
        if any((s & 0x7f) > count or (s & 0x7f) == 0 and s != 0 for s in sites):
            raise ValueError("invalid site ownership")
        if any(road > count for road in roads) or any(any(stock) for stock in stocks[count:]):
            raise ValueError("invalid road ownership or inactive station stock")
        result = {"count": count, "turn": turn, "phase": phase, "roll": roll,
                  "winner": -1 if winner == 255 else winner, "finished": winner != 255,
                  "rng": int.from_bytes(payload[5:7], "big"), "sites": sites,
                  "roads": roads, "stocks": stocks}
        return result

    def describe_action(self, action: bytes) -> str:
        if not action:
            raise ValueError("missing action")
        names = {1: "roll production", 2: "build road", 3: "build outpost",
                 4: "upgrade city", 5: "trade 3:1", 6: "end turn"}
        if action[0] not in names or len(action) != (1 if action[0] in (1, 6) else 2):
            raise ValueError("invalid settlement action")
        suffix = "" if len(action) == 1 else f" #{action[1]}"
        return names[action[0]] + suffix

    def view_model(self, state: dict, players: tuple[str, ...]) -> dict:
        sites = [({"index": i, "owner": players[(v & 0x7f) - 1] if v else None,
                   "city": bool(v & 0x80), "region": i // 3 + 1})
                 for i, v in enumerate(state["sites"])]
        roads = [{"index": i, "from": a, "to": b,
                  "owner": players[state["roads"][i] - 1] if state["roads"][i] else None}
                 for i, (a, b) in enumerate(EDGES)]
        scores = {player: sum((2 if s & 0x80 else 1) for s in state["sites"]
                               if s and (s & 0x7f) == j + 1) for j, player in enumerate(players)}
        return {"kind": "settlement", "sites": sites, "roads": roads,
                "regions": [{"number": i + 1, "resource": RESOURCES[resource]}
                            for i, resource in enumerate(SECTORS)],
                "resources": {p: dict(zip(RESOURCES, state["stocks"][i])) for i, p in enumerate(players)},
                "scores": scores, "turn": self.current_player(state, players),
                "phase": "roll" if state["phase"] == 0 else "build",
                "roll": state["roll"], "winner": players[state["winner"]] if state["finished"] else None,
                "goal": 6}
