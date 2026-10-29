"""Open-rack, two-player word-tile game with compact deterministic packets.

All racks are public. ENABLE1 is bundled as the fixed lexicon for game ID 3.
The initial RNG state, tile bag, racks, and all later draws are reproducible
from the agreed starting snapshot; no hidden information is implied.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import secrets
import struct
from typing import Any

SIZE = 15
EMPTY_RACK = 31
BLANK = 0
BOARD_VERSION = 1
LEXICON_ID = "ENABLE1"
LEXICON_SHA256 = "3f16130220645692ed49c7134e24a18504c2ca55b3c012f7290e3e77c63b1a89"

# Blank, then A through Z.
TILE_COUNTS = (2, 9, 2, 2, 4, 12, 2, 3, 2, 9, 1, 1, 4, 2, 6,
               8, 2, 1, 6, 4, 6, 4, 2, 2, 1, 2, 1)
TILE_POINTS = (0, 1, 3, 3, 2, 1, 4, 2, 4, 1, 8, 5, 1, 3, 1,
               1, 3, 10, 1, 1, 1, 1, 4, 4, 8, 4, 10)


def _xorshift32(value: int) -> int:
    value ^= (value << 13) & 0xFFFFFFFF
    value ^= value >> 17
    value ^= (value << 5) & 0xFFFFFFFF
    value &= 0xFFFFFFFF
    return value or 0x6D2B79F5


def _tile_counts(state: dict[str, Any]) -> list[int]:
    counts = list(TILE_COUNTS)
    for cell in state["board"]:
        if cell:
            tile = BLANK if cell > 26 else cell
            counts[tile] -= 1
    for rack in state["racks"]:
        for tile in rack:
            if tile != EMPTY_RACK:
                counts[tile] -= 1
    if any(count < 0 for count in counts):
        raise ValueError("tile inventory exceeds the agreed bag")
    return counts


def _draw(state: dict[str, Any]) -> int | None:
    counts = _tile_counts(state)
    total = sum(counts)
    if not total:
        return None
    rng = _xorshift32(state["rng"])
    state["rng"] = rng
    index = rng % total
    for tile, count in enumerate(counts):
        if index < count:
            return tile
        index -= count
    raise AssertionError("unreachable weighted draw")


def _refill(state: dict[str, Any], rack_index: int) -> None:
    rack = state["racks"][rack_index]
    for slot, tile in enumerate(rack):
        if tile == EMPTY_RACK:
            drawn = _draw(state)
            if drawn is None:
                break
            rack[slot] = drawn


def _pack_bits(values: list[int], width: int) -> bytes:
    accumulator = bit_count = 0
    output = bytearray()
    for value in values:
        if not 0 <= value < (1 << width):
            raise ValueError("value does not fit packed width")
        accumulator |= value << bit_count
        bit_count += width
        while bit_count >= 8:
            output.append(accumulator & 0xFF)
            accumulator >>= 8
            bit_count -= 8
    if bit_count:
        output.append(accumulator & 0xFF)
    return bytes(output)


def _unpack_bits(data: bytes, count: int, width: int) -> list[int]:
    expected = (count * width + 7) // 8
    if len(data) != expected:
        raise ValueError("invalid packed word-game data")
    accumulator = bit_count = offset = 0
    output = []
    for _ in range(count):
        while bit_count < width:
            accumulator |= data[offset] << bit_count
            offset += 1
            bit_count += 8
        output.append(accumulator & ((1 << width) - 1))
        accumulator >>= width
        bit_count -= width
    if accumulator:
        raise ValueError("nonzero padding bits")
    return output


@lru_cache(maxsize=1)
def _lexicon() -> frozenset[str]:
    path = files("radio_arcade.games").joinpath("data/enable1.txt")
    words = frozenset(line.strip().upper() for line in path.read_text(encoding="ascii").splitlines()
                      if line.strip().isalpha() and line.strip().isascii())
    if not words:
        raise RuntimeError("bundled ENABLE1 word list is missing or empty")
    return words


def _score(letter: str) -> int:
    return TILE_POINTS[ord(letter) - ord("A") + 1]


_TRIPLE_WORD = {(0, 0), (7, 0), (14, 0), (0, 7), (14, 7), (0, 14), (7, 14), (14, 14)}
_DOUBLE_WORD = {(1, 1), (2, 2), (3, 3), (4, 4), (7, 7), (10, 10), (11, 11), (12, 12), (13, 13),
                (13, 1), (12, 2), (11, 3), (10, 4), (4, 10), (3, 11), (2, 12), (1, 13)}
_TRIPLE_LETTER = {(5, 1), (9, 1), (1, 5), (5, 5), (9, 5), (13, 5),
                  (1, 9), (5, 9), (9, 9), (13, 9), (5, 13), (9, 13)}
_DOUBLE_LETTER = {(3, 0), (11, 0), (6, 2), (8, 2), (0, 3), (7, 3), (14, 3),
                  (2, 6), (6, 6), (8, 6), (12, 6), (3, 7), (11, 7), (2, 8),
                  (6, 8), (8, 8), (12, 8), (0, 11), (7, 11), (14, 11),
                  (6, 12), (8, 12), (3, 14), (11, 14)}


def premium(position: int) -> str:
    x, y = position % SIZE, position // SIZE
    if (x, y) in _TRIPLE_WORD: return "TW"
    if (x, y) in _DOUBLE_WORD: return "DW"
    if (x, y) in _TRIPLE_LETTER: return "TL"
    if (x, y) in _DOUBLE_LETTER: return "DL"
    return ".."


def _line(board: list[int], position: int, delta: int) -> list[int]:
    start = position
    while True:
        before = start - delta
        if not 0 <= before < len(board) or not board[before]:
            break
        if delta == 1 and before // SIZE != start // SIZE:
            break
        start = before
    result = []
    cursor = start
    while 0 <= cursor < len(board) and board[cursor]:
        result.append(cursor)
        following = cursor + delta
        if delta == 1 and following // SIZE != cursor // SIZE:
            break
        cursor = following
    return result


def _board_letter(cell: int) -> str:
    letter = cell if cell <= 26 else cell - 26
    return chr(ord("A") + letter - 1)


def _word_score(board: list[int], positions: list[int], new_positions: set[int]) -> int:
    total = 0
    word_multiplier = 1
    for position in positions:
        cell = board[position]
        value = 0 if cell > 26 else _score(_board_letter(cell))
        if position in new_positions:
            mult = premium(position)
            if mult == "DL": value *= 2
            elif mult == "TL": value *= 3
            elif mult == "DW": word_multiplier *= 2
            elif mult == "TW": word_multiplier *= 3
        total += value
    return total * word_multiplier


def _pack_place(position: int, horizontal: bool, span: int, mask: int,
                letters: list[int], blank_mask: int) -> bytes:
    packed_letters = _pack_bits(letters, 5)
    return (bytes((0, position, (0x10 if horizontal else 0) | (span - 1)))
            + mask.to_bytes(2, "big") + packed_letters + bytes((blank_mask,)))


def make_placement(state: dict[str, Any], actor: str, start: str, direction: str,
                   word: str) -> bytes:
    """Encode e.g. start='H8', direction='H', word='C{A}TS' (blank A)."""
    try:
        x = ord(start.strip()[0].upper()) - ord("A")
        y = int(start.strip()[1:]) - 1
    except (IndexError, ValueError) as exc:
        raise ValueError("start square must look like H8") from exc
    if not 0 <= x < SIZE or not 0 <= y < SIZE:
        raise ValueError("start square must be within A1–O15")
    direction = direction.strip().upper()
    if direction not in ("H", "V"):
        raise ValueError("direction must be H or V")
    horizontal = direction == "H"
    tokens: list[tuple[str, bool]] = []
    i = 0
    word = word.strip().upper()
    while i < len(word):
        if word[i] == "{":
            close = word.find("}", i + 1)
            if close != i + 2 or not word[i + 1].isalpha() or not word[i + 1].isascii():
                raise ValueError("mark a blank tile as {A}, using its assigned letter")
            tokens.append((word[i + 1], True)); i = close + 1
        elif "A" <= word[i] <= "Z":
            tokens.append((word[i], False)); i += 1
        else:
            raise ValueError("word may contain letters and blank notation like {A}")
    if not 1 <= len(tokens) <= SIZE:
        raise ValueError("word length must be from 1 to 15")
    origin = y * SIZE + x
    step = 1 if horizontal else SIZE
    if horizontal and x + len(tokens) > SIZE or not horizontal and y + len(tokens) > SIZE:
        raise ValueError("word runs off the board")
    players = tuple(state.get("players", ()))
    rack_index = players.index(actor) if actor in players else state["turn"]
    rack = state["racks"][rack_index]
    available = list(rack)
    mask = blank_mask = 0
    new_letters = []
    for offset, (letter, use_blank) in enumerate(tokens):
        position = origin + step * offset
        cell = state["board"][position]
        letter_id = ord(letter) - ord("A") + 1
        if cell:
            if use_blank or _board_letter(cell) != letter:
                raise ValueError("word must match every tile already on the board")
            continue
        mask |= 1 << offset
        new_letters.append(letter_id)
        if use_blank:
            try: available.remove(BLANK)
            except ValueError as exc: raise ValueError("your rack has no blank tile") from exc
            blank_mask |= 1 << (len(new_letters) - 1)
        else:
            try: available.remove(letter_id)
            except ValueError as exc: raise ValueError(f"your rack has no {letter} tile") from exc
    if not new_letters or len(new_letters) > 7:
        raise ValueError("a turn must place one to seven tiles")
    return _pack_place(origin, horizontal, len(tokens), mask, new_letters, blank_mask)


def make_exchange(state: dict[str, Any], actor: str, tiles: str) -> bytes:
    players = tuple(state["players"])
    if actor not in players:
        raise ValueError("unknown player")
    rack = state["racks"][players.index(actor)]
    wanted = tiles.upper().strip()
    if not wanted or len(wanted) > 7 or sum(_tile_counts(state)) < 7:
        raise ValueError("exchange 1–7 tiles only while at least 7 remain in the bag")
    mask = 0
    available = list(rack)
    for char in wanted:
        tile = BLANK if char == "?" else ord(char) - ord("A") + 1 if "A" <= char <= "Z" else -1
        try: slot = available.index(tile)
        except ValueError as exc: raise ValueError(f"rack does not contain tile {char}") from exc
        mask |= 1 << slot
        available[slot] = EMPTY_RACK
    return bytes((2, mask))


class OpenWordTilesGame:
    api_version = 1
    game_id = 3
    key = "open-word-tiles"
    name = "Open Word Tiles"
    min_players = 2
    max_players = 2

    def new_state(self, players: tuple[str, ...]) -> dict[str, Any]:
        if len(players) != 2 or len(set(players)) != 2:
            raise ValueError("Open Word Tiles needs two different players")
        _lexicon()
        seed = secrets.randbits(32) or 0x6D2B79F5
        state = {"board": [0] * (SIZE * SIZE), "racks": [[EMPTY_RACK] * 7 for _ in players],
                 "scores": [0, 0], "turn": seed & 1, "passes": 0, "finished": False,
                 "winner": -1, "rng": seed, "players": list(players)}
        _refill(state, 0); _refill(state, 1)
        return state

    def current_player(self, state: dict[str, Any], players: tuple[str, ...]) -> str | None:
        return None if state["finished"] else players[state["turn"]]

    def apply_action(self, state: dict[str, Any], players: tuple[str, ...], actor: str,
                     action: bytes) -> dict[str, Any]:
        if (len(players) != 2 or actor not in players
                or (state.get("players") and state["players"] != list(players))):
            raise ValueError("word-tile roster does not match the game")
        if state["finished"]:
            raise ValueError("game is over")
        if self.current_player(state, players) != actor:
            raise ValueError("it is not that player's turn")
        if not action:
            raise ValueError("empty word-tile action")
        next_state = {**state, "board": list(state["board"]),
                      "racks": [list(rack) for rack in state["racks"]],
                      "scores": list(state["scores"])}
        player = players.index(actor)
        rack = next_state["racks"][player]
        tag = action[0]
        if tag == 1:
            if action != b"\x01": raise ValueError("invalid pass action")
            next_state["passes"] += 1
            next_state["turn"] = 1 - player
            if next_state["passes"] >= 4:
                self._finish(next_state, players, transfer=False)
            return next_state
        if tag == 2:
            if len(action) != 2 or action[1] == 0 or action[1] & 0x80:
                raise ValueError("exchange action must select one to seven rack slots")
            mask = action[1]
            if sum(_tile_counts(next_state)) < 7:
                raise ValueError("at least seven tiles must remain to exchange")
            slots = [index for index in range(7) if mask & (1 << index)]
            if any(rack[index] == EMPTY_RACK for index in slots):
                raise ValueError("cannot exchange an empty rack slot")
            for index in slots: rack[index] = EMPTY_RACK
            # Exchanged pieces return to the public bag before replacement draws.
            # _draw derives the drawable multiset from board and racks, so the
            # returned tiles are back in the bag as soon as they leave the rack.
            for index in slots:
                drawn = _draw(next_state)
                if drawn is None: raise ValueError("bag is empty")
                rack[index] = drawn
            next_state["passes"] = 0
            next_state["turn"] = 1 - player
            return next_state
        if tag != 0 or len(action) < 7:
            raise ValueError("unknown or truncated word-tile action")
        position, dirlen = action[1], action[2]
        horizontal, span = bool(dirlen & 0x10), (dirlen & 0x0F) + 1
        if dirlen & 0xE0 or position >= SIZE * SIZE:
            raise ValueError("invalid word placement header")
        step = 1 if horizontal else SIZE
        x, y = position % SIZE, position // SIZE
        if horizontal and x + span > SIZE or not horizontal and y + span > SIZE:
            raise ValueError("word placement runs off the board")
        mask = int.from_bytes(action[3:5], "big")
        if mask >> span or not mask:
            raise ValueError("invalid placed-tile mask")
        placed_count = mask.bit_count()
        if placed_count > 7:
            raise ValueError("a turn can place at most seven tiles")
        letter_bytes = (placed_count * 5 + 7) // 8
        if len(action) != 5 + letter_bytes + 1:
            raise ValueError("invalid word placement length")
        letters = _unpack_bits(action[5:5 + letter_bytes], placed_count, 5)
        blank_mask = action[-1]
        if blank_mask >> placed_count or any(not 1 <= letter <= 26 for letter in letters):
            raise ValueError("invalid letter or blank mask")
        before = list(next_state["board"])
        new_positions: set[int] = set()
        used_tiles = []
        letter_offset = 0
        for offset in range(span):
            cell_position = position + step * offset
            cell = next_state["board"][cell_position]
            is_new = bool(mask & (1 << offset))
            if is_new == bool(cell):
                raise ValueError("placement mask must mark exactly the empty squares")
            if not is_new: continue
            letter = letters[letter_offset]
            is_blank = bool(blank_mask & (1 << letter_offset))
            tile = BLANK if is_blank else letter
            try: rack_slot = rack.index(tile)
            except ValueError as exc: raise ValueError("rack does not contain the selected tile") from exc
            rack[rack_slot] = EMPTY_RACK
            next_state["board"][cell_position] = letter + 26 if is_blank else letter
            used_tiles.append(tile)
            new_positions.add(cell_position)
            letter_offset += 1
        first_move = not any(before)
        if first_move:
            if 7 * SIZE + 7 not in new_positions:
                raise ValueError("the opening play must cover the center square")
        else:
            if not any(before[n] for position in new_positions for n in self._neighbors(position)):
                raise ValueError("the play must connect to the existing board")
        # The encoded span must cover the full main word, with no omitted ends.
        line_positions = _line(next_state["board"], position, step)
        if line_positions and (line_positions[0] < position or line_positions[-1] >= position + step * span):
            raise ValueError("placement span must include the complete word")
        main_word = "".join(_board_letter(next_state["board"][p]) for p in line_positions)
        if len(main_word) < 2 or main_word not in _lexicon():
            raise ValueError(f"{main_word or 'play'} is not in {LEXICON_ID}")
        score = _word_score(next_state["board"], line_positions, new_positions)
        cross_step = SIZE if horizontal else 1
        for new_position in new_positions:
            cross = _line(next_state["board"], new_position, cross_step)
            if len(cross) > 1:
                cross_word = "".join(_board_letter(next_state["board"][p]) for p in cross)
                if cross_word not in _lexicon():
                    raise ValueError(f"{cross_word} is not in {LEXICON_ID}")
                score += _word_score(next_state["board"], cross, {new_position})
        if placed_count == 7: score += 50
        next_state["scores"][player] += score
        next_state["passes"] = 0
        next_state["turn"] = 1 - player
        _refill(next_state, player)
        if not any(count for count in _tile_counts(next_state)) and not any(t != EMPTY_RACK for t in rack):
            self._finish(next_state, players, transfer=True, emptied=player)
        return next_state

    @staticmethod
    def _neighbors(position: int) -> tuple[int, ...]:
        x, y = position % SIZE, position // SIZE
        result = []
        if x: result.append(position - 1)
        if x + 1 < SIZE: result.append(position + 1)
        if y: result.append(position - SIZE)
        if y + 1 < SIZE: result.append(position + SIZE)
        return tuple(result)

    @staticmethod
    def _finish(state: dict[str, Any], players: tuple[str, ...], transfer: bool,
                emptied: int | None = None) -> None:
        penalties = [sum(TILE_POINTS[tile] for tile in rack if tile not in (EMPTY_RACK, BLANK))
                     for rack in state["racks"]]
        if transfer and emptied is not None:
            other = 1 - emptied
            state["scores"][emptied] += penalties[other]
            state["scores"][other] -= penalties[other]
        else:
            for index in range(2): state["scores"][index] -= penalties[index]
        state["finished"] = True
        state["winner"] = 0 if state["scores"][0] > state["scores"][1] else 1 if state["scores"][1] > state["scores"][0] else -1

    def encode_state(self, state: dict[str, Any]) -> bytes:
        if len(state["board"]) != SIZE * SIZE or len(state["racks"]) != 2:
            raise ValueError("invalid word-tile state")
        occupied = [index for index, tile in enumerate(state["board"]) if tile]
        if len(occupied) > 100 or any(not 1 <= state["board"][index] <= 52 for index in occupied):
            raise ValueError("word-tile board has more tiles than the game bag")
        bitmap = bytearray(29)
        for index in occupied:
            bitmap[index >> 3] |= 1 << (index & 7)
        board_tiles = _pack_bits([state["board"][index] for index in occupied], 6)
        racks = _pack_bits([tile for rack in state["racks"] for tile in rack], 5)
        winner = state["winner"] + 1
        return (bytes((BOARD_VERSION, state["turn"], state["passes"], int(state["finished"]), winner))
                + struct.pack("!Ihh", state["rng"], *state["scores"])
                + bytes((len(occupied),)) + racks + bytes(bitmap) + board_tiles)

    def decode_state(self, payload: bytes) -> dict[str, Any]:
        fixed = 5 + 8 + 1 + 9 + 29
        if len(payload) < fixed or payload[0] != BOARD_VERSION or payload[1] > 1 or payload[2] > 4 or payload[3] > 1 or payload[4] > 2:
            raise ValueError("invalid word-tile snapshot header")
        tile_count = payload[13]
        if tile_count > 100 or len(payload) != fixed + (tile_count * 6 + 7) // 8:
            raise ValueError("invalid word-tile snapshot size")
        rng, score0, score1 = struct.unpack_from("!Ihh", payload, 5)
        if rng == 0: raise ValueError("invalid word-tile random state")
        racks = _unpack_bits(payload[14:23], 14, 5)
        if any(tile > 26 and tile != EMPTY_RACK for tile in racks):
            raise ValueError("invalid rack tile")
        bitmap = payload[23:52]
        if bitmap[-1] & 0xFE:
            raise ValueError("invalid board bitmap padding")
        positions = [index for index in range(SIZE * SIZE)
                     if bitmap[index >> 3] & (1 << (index & 7))]
        if len(positions) != tile_count:
            raise ValueError("board bitmap count does not match its header")
        values = _unpack_bits(payload[52:], tile_count, 6)
        if any(not 1 <= tile <= 52 for tile in values):
            raise ValueError("invalid board tile")
        board = [0] * (SIZE * SIZE)
        for position, tile in zip(positions, values): board[position] = tile
        if not payload[3] and payload[4] != 0:
            raise ValueError("unfinished word-tile game cannot have a winner")
        state = {"board": board, "racks": [racks[:7], racks[7:]], "scores": [score0, score1],
                 "turn": payload[1], "passes": payload[2], "finished": bool(payload[3]),
                 "winner": payload[4] - 1, "rng": rng, "players": []}
        # Inventory must still be conserved across the board, racks and bag.
        remaining = sum(_tile_counts(state))
        emptied_rack = any(all(tile == EMPTY_RACK for tile in rack) for rack in state["racks"])
        if (not state["finished"] and state["passes"] >= 4
                or state["finished"] and state["passes"] < 4
                and (remaining != 0 or not emptied_rack)):
            raise ValueError("inconsistent word-tile end state")
        return state

    def validate_roster(self, state: dict[str, Any], players: tuple[str, ...]) -> None:
        if len(players) != 2:
            raise ValueError("Open Word Tiles supports two players")
        state["players"] = list(players)

    def describe_action(self, action: bytes) -> str:
        if not action: return "invalid move"
        if action[0] == 1: return "pass"
        if action[0] == 2: return "exchange tiles"
        if action[0] == 0 and len(action) >= 3:
            x, y = action[1] % SIZE, action[1] // SIZE
            return f"place tiles at {chr(65+x)}{y+1}"
        return "word-tile move"

    def view_model(self, state: dict[str, Any], players: tuple[str, ...]) -> dict[str, Any]:
        return {"kind": "word-tiles", "width": SIZE, "height": SIZE,
                "cells": ["" if not cell else _board_letter(cell).lower() if cell > 26 else _board_letter(cell)
                          for cell in state["board"]],
                "premiums": [premium(p) for p in range(SIZE * SIZE)],
                "racks": {player: ["?" if tile == BLANK else chr(64 + tile) if tile != EMPTY_RACK else "·"
                                   for tile in state["racks"][index]] for index, player in enumerate(players)},
                "scores": dict(zip(players, state["scores"])),
                "bag": sum(_tile_counts(state)), "turn": self.current_player(state, players),
                "winner": players[state["winner"]] if state["winner"] >= 0 else None,
                "lexicon": LEXICON_ID, "open_racks": True}

