"""A deterministic, low-bandwidth artillery duel on shared terrain."""

from __future__ import annotations

from math import cos, isqrt, radians, sin
from secrets import randbelow
from typing import Any

WIDTH, HEIGHT, SKY_HEIGHT = 64, 16, 32
SCALE = 1000
GRAVITY = 60
MIN_ANGLE, MAX_ANGLE = 10, 80
NO_ACTION = 255
DRIVE, REPAIR, SHIELD = 0x80, 0x81, 0x82
BUY = 0x90
MAX_HP = 100
MIN_SEPARATION = 6
STARTING_CREDITS = 70

# Shared map profiles never travel over the radio. Heights fit in four bits.
# Mirroring the classic 32-column profiles makes larger, continuous arenas
# without sacrificing their recognisable terrain features or map identifiers.
CLASSIC_MAPS = (
    ("Dust Bowl", (2, 2, 3, 3, 4, 5, 6, 7, 8, 9, 8, 7, 6, 5, 4, 3, 3, 4, 5, 6, 7, 8, 9, 8, 7, 6, 5, 4, 3, 3, 2, 2)),
    ("Split Ridge", (2, 2, 3, 4, 6, 8, 10, 12, 13, 14, 12, 10, 8, 6, 4, 3, 3, 4, 6, 8, 10, 12, 14, 13, 12, 10, 8, 6, 4, 3, 2, 2)),
    ("Dry River", (4, 4, 5, 6, 7, 8, 9, 10, 11, 12, 11, 10, 9, 8, 7, 6, 6, 7, 8, 9, 10, 11, 12, 11, 10, 9, 8, 7, 6, 5, 4, 4)),
    ("Broken Mesa", (3, 3, 4, 5, 7, 10, 13, 13, 12, 10, 8, 6, 5, 5, 6, 8, 11, 14, 14, 12, 9, 7, 6, 7, 9, 12, 13, 11, 8, 6, 4, 3)),
    ("Cinder Valley", (5, 5, 4, 3, 3, 4, 5, 7, 10, 12, 11, 8, 5, 3, 2, 2, 3, 5, 8, 11, 13, 12, 9, 6, 4, 3, 4, 6, 8, 7, 6, 5)),
)
MAPS = tuple((name, profile + tuple(reversed(profile))) for name, profile in CLASSIC_MAPS)

# Speed, blast radius, direct damage, and crater depth are fixed game rules.
WEAPONS = (
    {"name": "Cannon", "speed": 2400, "radius": 1800, "damage": 38, "crater": 2, "cost": 0, "tag": "free / balanced"},
    {"name": "High explosive", "speed": 2200, "radius": 3200, "damage": 30, "crater": 4, "cost": 18, "tag": "wide blast"},
    {"name": "Armor piercing", "speed": 2850, "radius": 850, "damage": 62, "crater": 1, "cost": 22, "tag": "direct hit"},
    {"name": "Cluster", "speed": 2300, "radius": 1600, "damage": 12, "crater": 2, "cost": 26, "tag": "three-burst spread"},
    {"name": "Bunker buster", "speed": 1950, "radius": 1200, "damage": 48, "crater": 6, "cost": 30, "tag": "terrain breaker"},
    {"name": "Mega nuke", "speed": 1950, "radius": 5200, "damage": 52, "crater": 7, "cost": 55, "tag": "massive blast"},
    {"name": "Needle", "speed": 3200, "radius": 600, "damage": 90, "crater": 1, "cost": 34, "tag": "precision strike"},
    {"name": "Earth mover", "speed": 2050, "radius": 1400, "damage": 8, "crater": 9, "cost": 28, "tag": "extreme excavation"},
)
WIND_ACCEL = (-3, -2, -1, 0, 1, 2, 3)


def _pack_heights(heights: list[int] | tuple[int, ...]) -> bytes:
    if len(heights) != WIDTH or any(not 0 <= height <= 15 for height in heights):
        raise ValueError("invalid Scorched Earth terrain")
    return bytes((heights[i] << 4) | heights[i + 1] for i in range(0, WIDTH, 2))


def _unpack_heights(payload: bytes) -> list[int]:
    if len(payload) != WIDTH // 2:
        raise ValueError("invalid Scorched Earth terrain data")
    return [height for byte in payload for height in (byte >> 4, byte & 15)]


def _surface(terrain: list[int] | tuple[int, ...], x: int) -> int:
    return terrain[max(0, min(WIDTH - 1, x))]


def _tank_world_y(terrain: list[int], x: int) -> int:
    return _surface(terrain, x) * SCALE + 650


def _wind(map_id: int, round_number: int) -> int:
    """A public, repeatable wind cycle; no random state needs synchronization."""
    return (map_id * 2 + round_number * 3) % len(WIND_ACCEL)


def _trace_shot(terrain: list[int], positions: list[int], shooter: int,
                weapon_id: int, angle: int, wind_id: int) -> dict[str, Any]:
    weapon = WEAPONS[weapon_id]
    direction = 1 if shooter == 0 else -1
    sx = positions[shooter]
    x, y = sx * SCALE + direction * 850, _tank_world_y(terrain, sx)
    theta = radians(angle)
    vx, vy = direction * round(weapon["speed"] * cos(theta)), round(weapon["speed"] * sin(theta))
    path = [(x, y)]
    target = 1 - shooter
    tx = positions[target] * SCALE
    ty = _tank_world_y(terrain, positions[target])
    impact_kind = "edge"
    for _ in range(240):
        x += vx
        y += vy
        vx += WIND_ACCEL[wind_id]
        if direction * vx < 0:
            vx = 0
        vy -= GRAVITY
        path.append((x, y))
        if (x - tx) ** 2 + (y - ty) ** 2 <= 820 * 820:
            impact_kind = "tank"
            break
        column = x // SCALE
        if column < 0 or column >= WIDTH or y < 0 or y > SKY_HEIGHT * SCALE:
            impact_kind = "edge"
            break
        if y <= _surface(terrain, round(x / SCALE)) * SCALE:
            y = _surface(terrain, round(x / SCALE)) * SCALE
            path[-1] = (x, y)
            impact_kind = "terrain"
            break
    return {"path": path, "x": x, "y": y, "kind": impact_kind}


def _resolve_blast(terrain: list[int], positions: list[int], weapon_id: int,
                   impact_x: int, impact_y: int, hp: list[int], shields: list[bool]) -> tuple[list[int], list[bool]]:
    weapon = WEAPONS[weapon_id]
    out, remaining_shields = list(hp), list(shields)
    centers = ([(impact_x - 1200, impact_y), (impact_x, impact_y + 300),
                (impact_x + 1200, impact_y)] if weapon_id == 3 else [(impact_x, impact_y)])
    for player in (0, 1):
        tx = positions[player] * SCALE
        ty = _tank_world_y(terrain, positions[player])
        damage = 0
        for center_x, center_y in centers:
            distance = isqrt((center_x - tx) ** 2 + (center_y - ty) ** 2)
            if distance <= weapon["radius"]:
                damage += max(1, weapon["damage"] * (weapon["radius"] + SCALE - distance) // (weapon["radius"] + SCALE))
        if damage:
            if remaining_shields[player]:
                damage = (damage + 1) // 2
                remaining_shields[player] = False
            out[player] = max(0, out[player] - damage)
    return out, remaining_shields


def _make_crater(terrain: list[int], x: int, depth: int) -> list[int]:
    out = list(terrain)
    center = max(0, min(WIDTH - 1, round(x / SCALE)))
    radius = max(1, depth // 2)
    for column in range(max(0, center - radius), min(WIDTH, center + radius + 1)):
        falloff = radius + 1 - abs(column - center)
        out[column] = max(0, out[column] - max(1, depth * falloff // (radius + 1)))
    return out


class ScorchedEarthGame:
    api_version = 1
    game_id = 7
    key = "scorched-earth"
    name = "Scorched Earth"
    min_players = max_players = 2
    ui_clients = ("desktop", "web")

    def new_state(self, players: tuple[str, ...]) -> dict[str, Any]:
        if len(players) != 2 or len(set(players)) != 2:
            raise ValueError("Scorched Earth requires two different players")
        map_id = randbelow(len(MAPS))
        terrain = list(MAPS[map_id][1])
        # Spawn on an early ridge so the expanded arena doesn't put an
        # impassable mountain directly in front of either cannon.
        left_spawn = max(range(4, 13), key=lambda x: terrain[x])
        return {"map": map_id, "turn": 0, "round": 0, "hp": [MAX_HP, MAX_HP],
                "x": [left_spawn, WIDTH - 1 - left_spawn], "kits": [2, 2], "fuel": [3, 3], "shields": [False, False],
                "credits": [STARTING_CREDITS, STARTING_CREDITS],
                "ammo": [[255] + [1] * (len(WEAPONS) - 1) for _ in range(2)],
                "terrain": terrain, "last_terrain": list(terrain), "last": [NO_ACTION, NO_ACTION],
                "last_wind": NO_ACTION, "last_damage": [0, 0], "winner": -1, "finished": False}

    def apply_action(self, state: dict[str, Any], players: tuple[str, ...], actor: str,
                     action: bytes) -> dict[str, Any]:
        if len(players) != 2 or actor not in players:
            raise ValueError("unknown Scorched Earth player")
        if len(action) != 2:
            raise ValueError("turn must be two bytes")
        if state["finished"]:
            raise ValueError("game is over")
        player = players.index(actor)
        if player != state["turn"]:
            raise ValueError("it is not that player's turn")
        code, parameter = action
        hp, positions = list(state["hp"]), list(state["x"])
        kits, fuel, shields = list(state["kits"]), list(state["fuel"]), list(state["shields"])
        credits = list(state["credits"])
        ammo = [list(stock) for stock in state["ammo"]]
        terrain = list(state["terrain"])
        last_terrain = list(state["last_terrain"])
        damage = [0, 0]
        last_wind = NO_ACTION

        if code < len(WEAPONS):
            if not MIN_ANGLE <= parameter <= MAX_ANGLE:
                raise ValueError(f"firing angle must be from {MIN_ANGLE}° through {MAX_ANGLE}°")
            if ammo[player][code] == 0:
                raise ValueError("out of ammunition; buy another round")
            if code:
                ammo[player][code] -= 1
            last_terrain = list(terrain)
            last_wind = _wind(state["map"], state["round"])
            shot = _trace_shot(terrain, positions, player, code, parameter, last_wind)
            # A projectile that leaves the battlefield cannot explode against
            # a tank near the boundary. Only actual impacts produce a blast.
            if shot["kind"] != "edge":
                hp, shields = _resolve_blast(terrain, positions, code, shot["x"], shot["y"], hp, shields)
            damage = [state["hp"][i] - hp[i] for i in (0, 1)]
            credits[player] = min(255, credits[player] + 8 + damage[1 - player] // 3)
            if shot["kind"] != "edge":
                terrain = _make_crater(terrain, shot["x"], WEAPONS[code]["crater"])
        elif code == DRIVE:
            if parameter not in (0, 1):
                raise ValueError("choose left or right to drive")
            if fuel[player] <= 0:
                raise ValueError("no drive fuel remains")
            step = -2 if parameter == 0 else 2
            candidate = max(1, min(WIDTH - 2, positions[player] + step))
            other_x = positions[1 - player]
            if abs(candidate - other_x) < MIN_SEPARATION:
                raise ValueError("the tank cannot drive through the other tank")
            positions[player] = candidate
            fuel[player] -= 1
        elif code == REPAIR:
            if parameter != 0 or kits[player] <= 0 or hp[player] >= MAX_HP:
                raise ValueError("a repair kit is not available")
            hp[player] = min(MAX_HP, hp[player] + 24)
            kits[player] -= 1
        elif code == SHIELD:
            if parameter != 0 or shields[player]:
                raise ValueError("the shield is already active")
            shields[player] = True
        elif code == BUY:
            if not 1 <= parameter < len(WEAPONS):
                raise ValueError("unknown shop item")
            cost = WEAPONS[parameter]["cost"]
            if credits[player] < cost or ammo[player][parameter] >= 254:
                raise ValueError("insufficient credits or ammunition full")
            credits[player] -= cost
            ammo[player][parameter] += 1
        else:
            raise ValueError("unknown Scorched Earth turn")

        dead = [hp[0] == 0, hp[1] == 0]
        finished = any(dead)
        winner = -1 if dead[0] and dead[1] else 1 if dead[0] else 0 if dead[1] else -1
        return {"map": state["map"], "turn": 1 - player, "round": (state["round"] + 1) & 255,
                "hp": hp, "x": positions, "kits": kits, "fuel": fuel, "shields": shields,
                "credits": credits, "ammo": ammo,
                "terrain": terrain, "last_terrain": last_terrain, "last": [code, parameter],
                "last_wind": last_wind, "last_damage": damage, "winner": winner, "finished": finished}

    def current_player(self, state: dict[str, Any], players: tuple[str, ...]) -> str | None:
        return None if state["finished"] else players[state["turn"]]

    def encode_state(self, state: dict[str, Any]) -> bytes:
        result = 3 if state["finished"] and state["winner"] < 0 else state["winner"] + 1 if state["finished"] else 0
        shields = int(state["shields"][0]) | (int(state["shields"][1]) << 1)
        return bytes((state["map"], state["turn"], state["round"], *state["hp"], *state["x"],
                      *state["kits"], *state["fuel"], shields, *state["last"], state["last_wind"],
                      *state["last_damage"], result)) + _pack_heights(state["terrain"]) + _pack_heights(state["last_terrain"]) + bytes((*state["credits"], *(n for stock in state["ammo"] for n in stock)))

    def decode_state(self, payload: bytes) -> dict[str, Any]:
        # Preserve locally stored 9-byte development sessions from the first
        # Scorched Earth build when users upgrade this still-in-development game.
        if len(payload) == 9:
            map_id, turn, hp0, hp1, result, old_weapon, old_angle, damage0, damage1 = payload
            if (map_id >= 3 or turn > 1 or max(hp0, hp1) > MAX_HP or max(damage0, damage1) > MAX_HP or result > 3 or
                    ((old_weapon == NO_ACTION) != (old_angle == NO_ACTION)) or
                    (old_weapon != NO_ACTION and (old_weapon >= 3 or not MIN_ANGLE <= old_angle <= MAX_ANGLE))):
                raise ValueError("invalid legacy Scorched Earth snapshot")
            valid_result = ((result == 0 and hp0 > 0 and hp1 > 0) or
                            (result == 1 and hp0 > 0 and hp1 == 0) or
                            (result == 2 and hp0 == 0 and hp1 > 0) or
                            (result == 3 and hp0 == 0 and hp1 == 0))
            if not valid_result or (old_weapon == NO_ACTION and (damage0 or damage1)):
                raise ValueError("inconsistent legacy Scorched Earth snapshot")
            terrain = list(CLASSIC_MAPS[map_id][1])
            terrain += list(reversed(terrain))
            last_wind = 3 if old_weapon != NO_ACTION else NO_ACTION
            return {"map": map_id, "turn": turn, "round": 0, "hp": [hp0, hp1],
                    "x": [2, 29], "kits": [2, 2], "fuel": [3, 3], "shields": [False, False],
                    "credits": [STARTING_CREDITS] * 2, "ammo": [[255] + [1] * (len(WEAPONS) - 1) for _ in range(2)],
                    "terrain": terrain, "last_terrain": list(terrain), "last": [old_weapon, old_angle],
                    "last_wind": last_wind, "last_damage": [damage0, damage1],
                    "winner": result - 1 if result in (1, 2) else -1, "finished": result != 0}
        classic_size = 52 + 2 * len(WEAPONS)
        current_size = 84 + 2 * len(WEAPONS)
        if len(payload) not in (50, classic_size, 82, current_size):
            raise ValueError("invalid Scorched Earth snapshot length")
        (map_id, turn, round_number, hp0, hp1, x0, x1, kits0, kits1, fuel0, fuel1,
         shields, last_code, last_parameter, last_wind, damage0, damage1, result) = payload[:18]
        classic = len(payload) in (50, classic_size)
        packed_size = 16 if classic else WIDTH // 2
        terrain = [height for byte in payload[18:18 + packed_size] for height in (byte >> 4, byte & 15)]
        last_terrain = [height for byte in payload[18 + packed_size:18 + packed_size * 2] for height in (byte >> 4, byte & 15)]
        if classic:
            terrain += list(reversed(terrain))
            last_terrain += list(reversed(last_terrain))
        if (map_id >= len(MAPS) or turn > 1 or max(hp0, hp1) > MAX_HP or
                not (1 <= x0 < x1 <= WIDTH - 2) or x1 - x0 < MIN_SEPARATION or
                max(kits0, kits1) > 2 or max(fuel0, fuel1) > 3 or shields > 3 or
                max(damage0, damage1) > MAX_HP or result > 3):
            raise ValueError("invalid Scorched Earth snapshot")
        if last_code == NO_ACTION:
            if last_parameter != NO_ACTION or last_wind != NO_ACTION or damage0 or damage1:
                raise ValueError("invalid initial Scorched Earth snapshot")
        elif last_code < len(WEAPONS):
            if not MIN_ANGLE <= last_parameter <= MAX_ANGLE or last_wind >= len(WIND_ACCEL):
                raise ValueError("invalid last shot")
        elif last_code in (DRIVE, REPAIR, SHIELD, BUY):
            if last_wind != NO_ACTION or damage0 or damage1:
                raise ValueError("invalid last tactical action")
            if ((last_code == DRIVE and last_parameter > 1) or
                    (last_code in (REPAIR, SHIELD) and last_parameter != 0) or
                    (last_code == BUY and not 1 <= last_parameter < len(WEAPONS))):
                raise ValueError("invalid last tactical action")
        else:
            raise ValueError("invalid last action")
        valid_result = ((result == 0 and hp0 > 0 and hp1 > 0) or
                        (result == 1 and hp0 > 0 and hp1 == 0) or
                        (result == 2 and hp0 == 0 and hp1 > 0) or
                        (result == 3 and hp0 == 0 and hp1 == 0))
        if not valid_result:
            raise ValueError("inconsistent Scorched Earth result")
        if len(payload) in (classic_size, current_size):
            shop_offset = 18 + packed_size * 2
            credits = list(payload[shop_offset:shop_offset + 2])
            ammo = [list(payload[shop_offset + 2:shop_offset + 2 + len(WEAPONS)]),
                    list(payload[shop_offset + 2 + len(WEAPONS):])]
            if ammo[0][0] != 255 or ammo[1][0] != 255:
                raise ValueError("invalid unlimited cannon ammunition")
        else:
            credits = [STARTING_CREDITS] * 2
            ammo = [[255] + [1] * (len(WEAPONS) - 1) for _ in range(2)]
        return {"map": map_id, "turn": turn, "round": round_number, "hp": [hp0, hp1], "x": [x0, x1],
                "kits": [kits0, kits1], "fuel": [fuel0, fuel1], "shields": [bool(shields & 1), bool(shields & 2)],
                "credits": credits, "ammo": ammo,
                "terrain": terrain, "last_terrain": last_terrain, "last": [last_code, last_parameter],
                "last_wind": last_wind, "last_damage": [damage0, damage1],
                "winner": result - 1 if result in (1, 2) else -1, "finished": result != 0}

    def describe_action(self, action: bytes) -> str:
        if len(action) != 2:
            raise ValueError("invalid Scorched Earth turn")
        code, parameter = action
        if code < len(WEAPONS) and MIN_ANGLE <= parameter <= MAX_ANGLE:
            return f"fires {WEAPONS[code]['name']} at {parameter}°"
        if code == DRIVE and parameter in (0, 1):
            return f"drives {'left' if parameter == 0 else 'right'}"
        if code == REPAIR and parameter == 0:
            return "uses a repair kit"
        if code == SHIELD and parameter == 0:
            return "raises a shield"
        if code == BUY and 1 <= parameter < len(WEAPONS):
            return f"buys {WEAPONS[parameter]['name']} ammunition"
        raise ValueError("invalid Scorched Earth turn")

    def view_model(self, state: dict[str, Any], players: tuple[str, ...]) -> dict[str, Any]:
        map_id, terrain = state["map"], state["terrain"]
        tanks = []
        for player, callsign in enumerate(players):
            x = state["x"][player]
            tanks.append({"player": callsign, "x": x, "y": HEIGHT - _surface(terrain, x) - 1,
                          "world_y": _tank_world_y(terrain, x) / SCALE, "hp": state["hp"][player],
                          "facing": 0 if player == 0 else 128, "repair_kits": state["kits"][player],
                          "fuel": state["fuel"][player], "shielded": state["shields"][player]})
            tanks[-1]["credits"] = state["credits"][player]
            tanks[-1]["ammo"] = list(state["ammo"][player])
        last_code, last_parameter = state["last"]
        trajectory: list[list[float]] = []
        impact = None
        last_actor = players[1 - state["turn"]] if last_code != NO_ACTION else None
        if last_code < len(WEAPONS):
            shot = _trace_shot(state["last_terrain"], state["x"], 1 - state["turn"],
                               last_code, last_parameter, state["last_wind"])
            points = shot["path"]
            stride = max(1, len(points) // 96)
            sampled = points[::stride]
            if sampled[-1] != points[-1]:
                sampled.append(points[-1])
            trajectory = [[x / SCALE, y / SCALE] for x, y in sampled]
            impact = {"x": shot["x"] / SCALE, "y": shot["y"] / SCALE, "kind": shot["kind"],
                      "weapon": last_code, "angle": last_parameter}
        wind_id = _wind(map_id, state["round"])
        wind_word = ("strong crosswind", "breezy", "light wind", "calm", "light wind", "breezy", "strong crosswind")[wind_id]
        action_text = None
        if last_actor:
            action_text = (self.describe_action(bytes(state["last"])) if last_code < len(WEAPONS)
                           else self.describe_action(bytes(state["last"])))
        return {"kind": "scorched-earth", "width": WIDTH, "height": HEIGHT, "sky_height": SKY_HEIGHT,
                "map_name": MAPS[map_id][0], "terrain": list(terrain), "gravity": GRAVITY / SCALE,
                "wind": {"index": wind_id, "acceleration": WIND_ACCEL[wind_id] / SCALE, "label": wind_word,
                         "direction": "←" if WIND_ACCEL[wind_id] < 0 else "→" if WIND_ACCEL[wind_id] > 0 else "—"},
                "round": state["round"], "tanks": tanks,
                "weapons": [{"id": i, "name": item["name"], "speed": item["speed"] / SCALE,
                             "radius": item["radius"] / SCALE, "damage": item["damage"],
                             "cost": item["cost"], "tag": item["tag"]}
                            for i, item in enumerate(WEAPONS)],
                "actions": {"drive": DRIVE, "repair": REPAIR, "shield": SHIELD, "buy": BUY},
                "min_angle": MIN_ANGLE, "max_angle": MAX_ANGLE,
                "turn": self.current_player(state, players),
                "winner": players[state["winner"]] if state["winner"] >= 0 else None,
                "draw": state["finished"] and state["winner"] < 0,
                "last_actor": last_actor, "last_action": action_text,
                "last_action_code": last_code, "last_action_parameter": last_parameter,
                "last_damage": list(state["last_damage"]), "trajectory": trajectory, "impact": impact}
