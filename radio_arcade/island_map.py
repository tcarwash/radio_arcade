"""Rich terminal map for the fixed Island Settlers route network.

Only the presentation lives here. Site and road numbers come from the unchanged
game ID 4 topology; crossings of interior routes are *not* playable junctions.
The returned hit map uses terminal-cell coordinates, so the TUI can select a
route or site without introducing any new radio messages.
"""

from __future__ import annotations

from math import hypot

from rich.text import Text

from .games.settlers import EDGES

WIDTH = 68
HEIGHT = 35
CENTER = (35, 16)
HEX_CORNERS = ((35, 1), (58, 7), (58, 25), (35, 31), (12, 25), (12, 7))


def _perimeter() -> tuple[tuple[int, int], ...]:
    points = []
    for i, (x, y) in enumerate(HEX_CORNERS):
        x2, y2 = HEX_CORNERS[(i + 1) % 6]
        for step in range(3):
            points.append((round(x + (x2 - x) * step / 3),
                           round(y + (y2 - y) * step / 3)))
    return tuple(points)


SITES = _perimeter()
REGION_CENTERS = ((43, 9), (49, 16), (43, 23), (27, 23), (21, 16), (27, 9))
REGION_COLORS = (
    ("#a8efc1", "#21493c"),  # timber
    ("#ffc4a9", "#5b322e"),  # clay
    ("#e0d0ff", "#433862"),  # wool
    ("#ffe4a8", "#51462e"),  # grain
    ("#d2e8f4", "#354b60"),  # ore
    ("#a8efc1", "#21493c"),
)
PLAYER_COLORS = ("#8edbfa", "#ffb777", "#d6a8ff", "#a2e4a5")


def _road_points(a: tuple[int, int], b: tuple[int, int]):
    x1, y1 = a
    x2, y2 = b
    steps = max(abs(x2 - x1), abs(y2 - y1) * 2)
    for step in range(steps + 1):
        t = step / steps if steps else 0
        yield round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t)


def _segment_glyph(a: tuple[int, int], b: tuple[int, int], owned: bool) -> str:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx) > abs(dy) * 3:
        return "━" if owned else "─"
    if abs(dy) > abs(dx) * 0.75:
        return "┃" if owned else "│"
    return "╲" if dx * dy > 0 else "╱"


def _outward_label(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    mid_x, mid_y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    vx, vy = mid_x - CENTER[0], (mid_y - CENTER[1]) * 1.7
    norm = hypot(vx, vy) or 1
    return round(mid_x + vx / norm * 2.5) - 1, round(mid_y + vy / norm * 1.5)


def render_island(view: dict, selected: tuple[str, int] | None = None,
                  monochrome: bool = False) -> tuple[Text, dict[tuple[int, int], tuple[str, int]]]:
    """Render a fully clickable 72×35 map with Rich per-cell color.

    Sites are on the perimeter; roads 0–17 follow that perimeter and roads
    18–26 run across the island. Six tinted hexagons show the production regions.
    """
    cells = [[(" ", "", None) for _ in range(WIDTH)] for _ in range(HEIGHT)]
    hit: dict[tuple[int, int], tuple[str, int]] = {}
    player_names = tuple(view["scores"])
    player_colors = {name: PLAYER_COLORS[i] for i, name in enumerate(player_names)}

    def put(x: int, y: int, glyph: str, style: str = "", target=None):
        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            return
        cells[y][x] = (glyph, style, target)
        hit.pop((x, y), None)
        if target is not None:
            hit[(x, y)] = target

    # Trace empty roads first, then owners' roads on top; no road belongs to a
    # junction merely because two traces visually cross.
    for built in (False, True):
        for road in view["roads"]:
            owner = road["owner"]
            if bool(owner) != built:
                continue
            idx = road["index"]
            a, b = SITES[road["from"]], SITES[road["to"]]
            chosen = selected == ("road", idx)
            color = "#ffffff" if chosen else player_colors.get(owner, "#385268")
            if monochrome:
                color = "#ffffff" if built or chosen else "#555555"
            style = ("bold " if built or chosen else "dim ") + color
            glyph = _segment_glyph(a, b, bool(owner) or chosen)
            for x, y in _road_points(a, b):
                put(x, y, glyph, style, ("road", idx))

    # Production regions are translucent-looking hex tiles over the island's
    # interior. Covered road segments are not selectable through a tile.
    for i, region in enumerate(view["regions"]):
        cx, cy = REGION_CENTERS[i]
        fg, bg = REGION_COLORS[i]
        if monochrome:
            fg, bg = "#ffffff", "#333333" if i % 2 else "#252525"
        for dy in range(-2, 3):
            span = (4 if abs(dy) == 2 else 5 if abs(dy) == 1 else 6)
            for dx in range(-span, span + 1):
                border = abs(dx) == span
                char = ("╱" if (dy < 0) == (dx < 0) else "╲") if border else " "
                if dy == 0 and border:
                    char = "│"
                elif abs(dy) == 2 and not border:
                    char = "─"
                put(cx + dx, cy + dy, char, f"{fg} on {bg}")
        label = f"{region['number']} {region['resource'].upper()}"
        for n, char in enumerate(label):
            put(cx - len(label) // 2 + n, cy, char, f"bold {fg} on {bg}")

    # Road indices on the outside of the coastline identify each perimeter
    # segment; interior chords are picked directly or by numeric index.
    for road in view["roads"][:18]:
        idx = road["index"]
        x, y = _outward_label(SITES[road["from"]], SITES[road["to"]])
        owner = road["owner"]
        active = selected == ("road", idx)
        color = "#ffffff" if active else player_colors.get(owner, "#8098ac")
        if monochrome:
            color = "#ffffff" if active or owner else "#888888"
        for n, char in enumerate(f"R{idx:02d}"):
            put(x + n, y, char, "bold " + color if active or owner else color, ("road", idx))

    # Mark settlements last, including short numeric callouts outside the rim.
    for site in view["sites"]:
        idx, owner = site["index"], site["owner"]
        x, y = SITES[idx]
        active = selected == ("site", idx)
        color = ("#ffffff" if active else player_colors.get(owner, "#8d9eae"))
        if monochrome:
            color = "#ffffff" if active or owner else "#999999"
        symbol = "◆" if site["city"] else "●" if owner else "○"
        put(x, y, "◉" if active else symbol, "bold " + color, ("site", idx))
        dx, dy = x - CENTER[0], y - CENTER[1]
        tag_x = x + (3 if dx > 10 else -4 if dx < -10 else -1)
        tag_y = y + (1 if dy > 7 else -1 if dy < -7 else 0)
        for n, char in enumerate(f"S{idx:02d}"):
            put(tag_x + n, tag_y, char, color, ("site", idx))

    # Build Rich runs per row instead of thousands of tiny individual spans.
    result = Text(no_wrap=True, overflow="crop")
    for y, row in enumerate(cells):
        offset = 0
        while offset < WIDTH:
            style = row[offset][1]
            end = offset + 1
            while end < WIDTH and row[end][1] == style:
                end += 1
            result.append("".join(item[0] for item in row[offset:end]), style)
            offset = end
        if y < HEIGHT - 1:
            result.append("\n")
    return result, hit


def map_hit(hits: dict, x: int, y: int) -> tuple[str, int] | None:
    """Pick a printed symbol, or an immediately adjacent road/site cell."""
    target = hits.get((x, y))
    if target is not None:
        return target
    # Expand clickable hit area by one terminal cell, preferring sites.
    nearby = [hits.get((x + dx, y + dy)) for dx, dy in
              ((0, -1), (0, 1), (-1, 0), (1, 0))]
    return next((candidate for candidate in nearby if candidate and candidate[0] == "site"),
                next((candidate for candidate in nearby if candidate), None))
