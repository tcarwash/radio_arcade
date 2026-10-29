"""Compact, deterministic 4X rules for a 240-byte Data2G Radio Arcade envelope.

Nothing in this module imports a UI or network package. Every action is public,
turn-based and reproducible from the original map seed and a compact snapshot.
"""

from __future__ import annotations

import copy
import struct
import zlib
from typing import Any

WIDTH = HEIGHT = 8
TILES = WIDTH * HEIGHT
STARTS = (9, 54, 14, 49)
TERRAIN = ("water", "plains", "forest", "hills", "desert")
TECHS = ("Agriculture", "Mining", "Bronze Working", "Writing", "Navigation")
TECH_COSTS = (6, 7, 10, 9, 11)
TECH_PREREQUISITES = (0, 0, 1 << 1, 0, 1 << 3)
TECH_EFFECTS = (
    "Each city produces +1 food when you end your turn.",
    "Each city produces +1 ore when you end your turn. Unlocks Forges and Bronze Working.",
    "Unlocks Warriors. Your Warriors deal 2 damage instead of 1 when attacking units or besieging cities.",
    "Each city produces +1 science when you end your turn. Unlocks Libraries and Navigation.",
    "Moving Scouts reveal hexes within 2 steps instead of 1. Water remains impassable.",
)
def _tech_guide(index: int) -> dict[str, str | int]:
    required = ", ".join(name for other, name in enumerate(TECHS)
                         if TECH_PREREQUISITES[index] & (1 << other)) or "None"
    return {
        "name": TECHS[index], "cost": TECH_COSTS[index],
        "requires": required, "effect": TECH_EFFECTS[index],
        "tooltip": (f"{TECHS[index]} — {TECH_COSTS[index]} science · 1 order\n"
                    f"Requires: {required}\n{TECH_EFFECTS[index]}\n+3 victory points"),
    }


# Shared, non-wire metadata for the native and browser clients. Keep this in
# sync with the mechanics so both interfaces explain the same effects.
TECH_GUIDE = tuple(_tech_guide(index) for index in range(len(TECHS)))
BUILDINGS = ("Granary", "Sawmill", "Forge", "Library")
BUILD_COSTS = ((2, 4, 0), (0, 5, 1), (0, 3, 5), (0, 4, 3))  # food, wood, ore
BUILD_TECHS = (0, 0, 1 << 1, 1 << 3)
UNITS = ("Scout", "Warrior", "Settler")
UNIT_COSTS = ((2, 3, 0), (2, 4, 2), (6, 6, 0))
MAX_CITIES = 8
MAX_UNITS = 14
MAX_ROUNDS = 24
VICTORY_SCORE = 30


def neighbors(tile: int) -> tuple[int, ...]:
    if not 0 <= tile < TILES:
        raise ValueError("invalid map tile")
    x, y = tile % WIDTH, tile // WIDTH
    offsets = ((-1, 0), (1, 0), (-1, -1), (0, -1), (-1, 1), (0, 1)) if not y % 2 else (
        (-1, 0), (1, 0), (0, -1), (1, -1), (0, 1), (1, 1))
    return tuple((y + dy) * WIDTH + x + dx for dx, dy in offsets
                 if 0 <= x + dx < WIDTH and 0 <= y + dy < HEIGHT)


def distance(a: int, b: int) -> int:
    """Odd-row-offset hex distance, not square/diagonal distance."""
    ax, ay = a % WIDTH, a // WIDTH
    bx, by = b % WIDTH, b // WIDTH
    aq, bq = ax - (ay - (ay & 1)) // 2, bx - (by - (by & 1)) // 2
    return (abs(aq - bq) + abs(ay - by) + abs(aq + ay - bq - by)) // 2


def terrain(seed: int, tile: int) -> int:
    if not 0 <= tile < TILES:
        raise ValueError("invalid map tile")
    if tile in STARTS or any(distance(tile, capital) == 1 for capital in STARTS):
        return 1  # Always spawn with a navigable, productive opening.
    n = (seed ^ ((tile + 1) * 0x9E3779B1)) & 0xFFFFFFFF
    n ^= n >> 16
    n = (n * 0x7FEB352D) & 0xFFFFFFFF
    n ^= n >> 15
    n = (n * 0x846CA68B) & 0xFFFFFFFF
    n ^= n >> 16
    roll = n % 100
    return 0 if roll < 12 else 1 if roll < 51 else 2 if roll < 72 else 3 if roll < 91 else 4


def reveal(mask: int, center: int, radius: int) -> int:
    for tile in range(TILES):
        if distance(tile, center) <= radius:
            mask |= 1 << tile
    return mask


def score(state: dict[str, Any], player: int) -> int:
    cities = [city for city in state["cities"] if city["owner"] == player]
    techs = state["players"][player]["tech"].bit_count()
    return sum(3 + city["pop"] + city["buildings"].bit_count() for city in cities) + 3 * techs


def yields(state: dict[str, Any], city: dict[str, int]) -> tuple[int, int, int, int]:
    surroundings = [terrain(state["seed"], tile) for tile in neighbors(city["tile"])]
    owner = state["players"][city["owner"]]
    buildings = city["buildings"]
    food = 1 + city["pop"] + min(2, surroundings.count(1)) + (2 if buildings & 1 else 0)
    wood = 1 + min(2, surroundings.count(2)) + (2 if buildings & 2 else 0)
    ore = 1 + min(2, surroundings.count(3)) + (2 if buildings & 4 else 0)
    science = 1 + (2 if buildings & 8 else 0)
    return (food + bool(owner["tech"] & 1), wood,
            ore + bool(owner["tech"] & 2), science + bool(owner["tech"] & 8))


class CivilizationGame:
    """A public-information 2–4 station, turn-based mini-4X campaign."""

    api_version = 1
    game_id = 5  # Explicitly reserved for the external Radio Civ application.
    key = "radio-civ"
    name = "Radio Civ"
    min_players = 2
    max_players = 4

    def new_state(self, players: tuple[str, ...]) -> dict[str, Any]:
        if not self.min_players <= len(players) <= self.max_players or len(set(players)) != len(players):
            raise ValueError("Radio Civ requires 2–4 unique civilizations")
        seed = zlib.crc32("|".join(players).encode("ascii")) & 0xFFFFFFFF
        factions = [{"food": 6, "wood": 6, "ore": 4, "science": 2, "tech": 0,
                     "seen": reveal(0, STARTS[i], 2)} for i in range(len(players))]
        cities = [{"tile": STARTS[i], "owner": i, "pop": 1, "hp": 3, "buildings": 0}
                  for i in range(len(players))]
        units = []
        for i, city in enumerate(cities):
            land = next(tile for tile in neighbors(city["tile"]) if terrain(seed, tile) != 0)
            units.append({"tile": land, "owner": i, "kind": 0, "hp": 2})
            factions[i]["seen"] = reveal(factions[i]["seen"], land, 1)
        return {"seed": seed, "turn": 0, "round": 1, "ap": 2, "winner": -1,
                "finished": False, "players": factions, "cities": cities, "units": units}

    def current_player(self, state: dict[str, Any], players: tuple[str, ...]) -> str | None:
        return None if state["finished"] else players[state["turn"]]

    def validate_roster(self, state: dict[str, Any], players: tuple[str, ...]) -> None:
        if len(state["players"]) != len(players):
            raise ValueError("Radio Civ snapshot roster mismatch")
        if state["seed"] != (zlib.crc32("|".join(players).encode("ascii")) & 0xFFFFFFFF):
            raise ValueError("Radio Civ map seed does not match the player roster")

    @staticmethod
    def _pay(player: dict[str, int], costs: tuple[int, int, int]) -> None:
        if any(player[name] < cost for name, cost in zip(("food", "wood", "ore"), costs)):
            raise ValueError("not enough food, timber or ore")
        for name, cost in zip(("food", "wood", "ore"), costs):
            player[name] -= cost

    @staticmethod
    def _owned(items: list[dict[str, int]], index: int, owner: int, name: str) -> dict[str, int]:
        if index >= len(items) or items[index]["owner"] != owner:
            raise ValueError(f"choose one of your {name}")
        return items[index]

    @staticmethod
    def _unit_on(state: dict[str, Any], tile: int) -> dict[str, int] | None:
        return next((unit for unit in state["units"] if unit["tile"] == tile), None)

    @staticmethod
    def _city_on(state: dict[str, Any], tile: int) -> dict[str, int] | None:
        return next((city for city in state["cities"] if city["tile"] == tile), None)

    def _move(self, state: dict[str, Any], owner: int, unit_index: int, destination: int) -> None:
        unit = self._owned(state["units"], unit_index, owner, "units")
        if destination not in neighbors(unit["tile"]):
            raise ValueError("units move one adjacent hex at a time")
        if terrain(state["seed"], destination) == 0:
            raise ValueError("water is impassable; navigation improves scouts, not ships")
        other = self._unit_on(state, destination)
        city = self._city_on(state, destination)
        if other and other["owner"] == owner:
            raise ValueError("one unit per hex")
        if unit["kind"] == 2 and ((other and other["owner"] != owner) or (city and city["owner"] != owner)):
            raise ValueError("settlers cannot attack")
        if other and other["owner"] != owner:
            if unit["kind"] == 0 and other["kind"] == 1:
                raise ValueError("scouts cannot attack enemy warriors")
            damage = 2 if unit["kind"] == 1 and state["players"][owner]["tech"] & 4 else 1
            other["hp"] -= damage
            if other["hp"] <= 0:
                state["units"].remove(other)
                # Clearing a defending garrison does not also capture a
                # fortified enemy city in a single attack. Remain outside
                # so a later order can properly siege the city.
                if city is None or city["owner"] == owner:
                    unit["tile"] = destination
            else:
                unit["hp"] -= 1
                if unit["hp"] <= 0:
                    state["units"].remove(unit)
        elif city and city["owner"] != owner:
            if unit["kind"] != 1:
                raise ValueError("only warriors can besiege cities")
            city["hp"] -= 2 if state["players"][owner]["tech"] & 4 else 1
            if city["hp"] <= 0:
                city.update(owner=owner, hp=2, buildings=0, pop=max(1, city["pop"] - 1))
                unit["tile"] = destination
        else:
            unit["tile"] = destination
        state["players"][owner]["seen"] = reveal(state["players"][owner]["seen"], destination,
                                                     2 if unit["kind"] == 0 and state["players"][owner]["tech"] & 16 else 1)

    def _advance(self, state: dict[str, Any]) -> None:
        n = len(state["players"])
        owner = state["turn"]
        for city in state["cities"]:
            if city["owner"] != owner:
                continue
            for resource, amount in zip(("food", "wood", "ore", "science"), yields(state, city)):
                state["players"][owner][resource] = min(255, state["players"][owner][resource] + amount)
        alive = {city["owner"] for city in state["cities"]}
        alive.update(unit["owner"] for unit in state["units"] if unit["kind"] == 2)
        if len(alive) == 1:
            state["winner"] = next(iter(alive))
            state["finished"] = True
            return
        # End-of-turn victory: score, followed by a fixed round limit/tie-break.
        if state["round"] >= 4 and score(state, owner) >= VICTORY_SCORE:
            state["winner"] = owner
            state["finished"] = True
            return
        if state["turn"] == n - 1:
            state["round"] += 1
        if state["round"] > MAX_ROUNDS:
            state["winner"] = max(range(n), key=lambda player: (score(state, player), -player))
            state["finished"] = True
            return
        for offset in range(1, n + 1):
            candidate = (owner + offset) % n
            if candidate in alive:
                state["turn"] = candidate
                break
        state["ap"] = 2

    def apply_action(self, state: dict[str, Any], players: tuple[str, ...], actor: str,
                     action: bytes) -> dict[str, Any]:
        self.validate_roster(state, players)
        if self.current_player(state, players) != actor:
            raise ValueError("wait for your civilization's turn")
        if not action or len(action) > 3 or action[0] not in b"EMFRGBT":
            raise ValueError("unknown Radio Civ action")
        s = copy.deepcopy(state)
        owner = s["turn"]
        command = chr(action[0])
        lengths = {"E": 1, "M": 3, "F": 2, "R": 3, "G": 2, "B": 3, "T": 2}
        if len(action) != lengths[command]:
            raise ValueError("incorrect Radio Civ action length")
        player = s["players"][owner]
        if command == "E":
            self._advance(s)
            return s
        if s["ap"] <= 0:
            raise ValueError("no orders left; end your turn")
        if command == "M":
            self._move(s, owner, action[1], action[2])
        elif command == "F":
            unit = self._owned(s["units"], action[1], owner, "units")
            if unit["kind"] != 2:
                raise ValueError("select a settler to found a city")
            if len(s["cities"]) >= MAX_CITIES:
                raise ValueError("the world has reached its city limit")
            if any(distance(unit["tile"], city["tile"]) < 2 for city in s["cities"]):
                raise ValueError("found a city at least two hexes from another city")
            if terrain(s["seed"], unit["tile"]) == 0:
                raise ValueError("cities must be founded on land")
            s["cities"].append({"tile": unit["tile"], "owner": owner, "pop": 1, "hp": 3, "buildings": 0})
            s["units"].remove(unit)
            player["seen"] = reveal(player["seen"], s["cities"][-1]["tile"], 2)
        elif command == "R":
            city = self._owned(s["cities"], action[1], owner, "cities")
            kind = action[2]
            if kind >= len(UNITS):
                raise ValueError("unknown unit")
            if len(s["units"]) >= MAX_UNITS:
                raise ValueError("the world has reached its unit limit")
            if kind == 2 and city["pop"] < 2:
                raise ValueError("grow your city to population 2 before training settlers")
            if kind == 1 and not player["tech"] & 4:
                raise ValueError("research Bronze Working before recruiting warriors")
            spots = (city["tile"],) + neighbors(city["tile"])
            destination = next((tile for tile in spots if terrain(s["seed"], tile) != 0
                                and self._unit_on(s, tile) is None
                                and not (self._city_on(s, tile) and self._city_on(s, tile)["owner"] != owner)), None)
            if destination is None:
                raise ValueError("no unoccupied hex next to this city")
            self._pay(player, UNIT_COSTS[kind])
            s["units"].append({"tile": destination, "owner": owner,
                               "kind": kind, "hp": 3 if kind == 1 else 2 if kind == 0 else 1})
            player["seen"] = reveal(player["seen"], destination, 1)
        elif command == "G":
            city = self._owned(s["cities"], action[1], owner, "cities")
            if city["pop"] >= 5:
                raise ValueError("city population is already at its limit")
            self._pay(player, (4 + city["pop"] * 3, 2 + city["pop"], 0))
            city["pop"] += 1
        elif command == "B":
            city = self._owned(s["cities"], action[1], owner, "cities")
            building = action[2]
            if building >= len(BUILDINGS):
                raise ValueError("unknown building")
            if city["buildings"] & 1 << building:
                raise ValueError("that city already has this building")
            if (player["tech"] & BUILD_TECHS[building]) != BUILD_TECHS[building]:
                raise ValueError("research the prerequisite for this building")
            self._pay(player, BUILD_COSTS[building])
            city["buildings"] |= 1 << building
        elif command == "T":
            tech = action[1]
            if tech >= len(TECHS):
                raise ValueError("unknown research")
            if player["tech"] & 1 << tech:
                raise ValueError("research already completed")
            if player["tech"] & TECH_PREREQUISITES[tech] != TECH_PREREQUISITES[tech]:
                raise ValueError("research the prerequisite technology first")
            if player["science"] < TECH_COSTS[tech]:
                raise ValueError("not enough science")
            player["science"] -= TECH_COSTS[tech]
            player["tech"] |= 1 << tech
        s["ap"] -= 1
        # A player explicitly ends their turn; allowing leftover AP to expire is
        # intentional so UI clicks and packet sequencing stay predictable.
        return s

    def encode_state(self, state: dict[str, Any]) -> bytes:
        players, cities, units = state["players"], state["cities"], state["units"]
        if not 2 <= len(players) <= 4 or len(cities) > MAX_CITIES or len(units) > MAX_UNITS:
            raise ValueError("Radio Civ state exceeds the compact snapshot capacity")
        result = bytearray(struct.pack("!BIBBBBBBB", 1, state["seed"], state["turn"], state["round"],
                                       state["ap"], state["winner"] if state["winner"] >= 0 else 255,
                                       len(players), len(cities), len(units)))
        for player in players:
            result.extend(bytes(player[name] for name in ("food", "wood", "ore", "science", "tech")))
            result.extend(player["seen"].to_bytes(8, "big"))
        for city in cities:
            result.extend((city["tile"], city["owner"] | city["hp"] << 2,
                           city["pop"], city["buildings"]))
        for unit in units:
            result.extend((unit["tile"], unit["owner"] << 4 | unit["kind"], unit["hp"]))
        if len(result) > 138:
            raise ValueError("Radio Civ state is too large for a worst-case four-station snapshot")
        return bytes(result)

    def decode_state(self, payload: bytes) -> dict[str, Any]:
        if len(payload) < 12:
            raise ValueError("truncated Radio Civ snapshot")
        version, seed, turn, round_number, ap, winner, n, city_count, unit_count = struct.unpack_from(
            "!BIBBBBBBB", payload)
        if (version != 1 or not 2 <= n <= 4 or city_count > MAX_CITIES or unit_count > MAX_UNITS
                or turn >= n or winner != 255 and winner >= n or ap > 2
                or not 1 <= round_number <= MAX_ROUNDS + 1
                or len(payload) != 12 + n * 13 + city_count * 4 + unit_count * 3):
            raise ValueError("invalid Radio Civ snapshot header or length")
        offset, players = 12, []
        for _ in range(n):
            food, wood, ore, science, tech = payload[offset:offset + 5]
            seen = int.from_bytes(payload[offset + 5:offset + 13], "big")
            if tech & ~0x1F:
                raise ValueError("invalid Radio Civ research flags")
            players.append({"food": food, "wood": wood, "ore": ore, "science": science,
                            "tech": tech, "seen": seen})
            offset += 13
        cities = []
        for _ in range(city_count):
            tile, owner_hp, pop, buildings = payload[offset:offset + 4]
            owner, hp = owner_hp & 3, owner_hp >> 2
            if (tile >= TILES or owner >= n or not 1 <= hp <= 3 or not 1 <= pop <= 5
                    or buildings & ~15 or any(c["tile"] == tile for c in cities)):
                raise ValueError("invalid Radio Civ city")
            cities.append({"tile": tile, "owner": owner, "hp": hp, "pop": pop, "buildings": buildings})
            offset += 4
        units = []
        for _ in range(unit_count):
            tile, owner_kind, hp = payload[offset:offset + 3]
            owner, kind = owner_kind >> 4, owner_kind & 15
            if (tile >= TILES or owner >= n or kind >= len(UNITS) or not 1 <= hp <= 3
                    or any(u["tile"] == tile for u in units)):
                raise ValueError("invalid Radio Civ unit")
            units.append({"tile": tile, "owner": owner, "kind": kind, "hp": hp})
            offset += 3
        return {"seed": seed, "turn": turn, "round": round_number, "ap": ap,
                "winner": -1 if winner == 255 else winner, "finished": winner != 255,
                "players": players, "cities": cities, "units": units}

    def describe_action(self, action: bytes) -> str:
        if not action:
            return "unknown order"
        names = {ord("E"): "ended turn", ord("M"): "moved or attacked", ord("F"): "founded a city",
                 ord("R"): "recruited a unit", ord("G"): "grew a city", ord("B"): "constructed a building",
                 ord("T"): "researched a technology"}
        return names.get(action[0], "unknown order")

    def view_model(self, state: dict[str, Any], players: tuple[str, ...]) -> dict[str, Any]:
        return {"kind": "civilization", "width": WIDTH, "height": HEIGHT,
                "terrain": [TERRAIN[terrain(state["seed"], tile)] for tile in range(TILES)],
                "players": [{"call": call, **copy.deepcopy(info), "score": score(state, i)}
                            for i, (call, info) in enumerate(zip(players, state["players"]))],
                "cities": copy.deepcopy(state["cities"]), "units": copy.deepcopy(state["units"]),
                "turn": players[state["turn"]] if not state["finished"] else None,
                "turn_index": state["turn"], "round": state["round"], "ap": state["ap"],
                "winner": players[state["winner"]] if state["winner"] >= 0 else None,
                "finished": state["finished"], "victory_score": VICTORY_SCORE,
                "max_rounds": MAX_ROUNDS}


# Bytes-only action constructors. Every order is at most three bytes on air.
def move(unit: int, tile: int) -> bytes:
    return bytes((ord("M"), unit, tile))


def found(unit: int) -> bytes:
    return bytes((ord("F"), unit))


def recruit(city: int, kind: int) -> bytes:
    return bytes((ord("R"), city, kind))


def grow(city: int) -> bytes:
    return bytes((ord("G"), city))


def build(city: int, kind: int) -> bytes:
    return bytes((ord("B"), city, kind))


def research(tech: int) -> bytes:
    return bytes((ord("T"), tech))


def end_turn() -> bytes:
    return b"E"
