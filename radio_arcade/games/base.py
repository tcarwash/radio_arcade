"""Transport- and UI-independent game plugin contract."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Protocol


class Game(Protocol):
    """A deterministic game whose state includes a boolean ``finished`` key.

    ``new_state``, ``apply_action``, and ``decode_state`` must all return
    dictionaries containing that key. The engine uses it to persist and
    restore the session's completion status.
    """
    api_version: int
    game_id: int
    key: str
    name: str
    min_players: int
    max_players: int

    def new_state(self, players: tuple[str, ...]) -> dict[str, Any]: ...
    def apply_action(self, state: dict[str, Any], players: tuple[str, ...], actor: str,
                     action: bytes) -> dict[str, Any]: ...
    def current_player(self, state: dict[str, Any], players: tuple[str, ...]) -> str | None: ...
    def encode_state(self, state: dict[str, Any]) -> bytes: ...
    def decode_state(self, payload: bytes) -> dict[str, Any]: ...
    def describe_action(self, action: bytes) -> str: ...
    def view_model(self, state: dict[str, Any], players: tuple[str, ...]) -> dict[str, Any]: ...


class OpaqueStateCodec(Protocol):
    """Optional per-game codec for UI-hidden, readable on-air state messages."""

    def encode_opaque_state(self, value: Any) -> str: ...
    def decode_opaque_state(self, payload: str) -> Any: ...


@dataclass(frozen=True)
class GameInfo:
    game_id: int
    key: str
    name: str
    min_players: int
    max_players: int
    public_information: bool = True


class GameRegistry:
    """Stable one-byte game ID catalog with duplicate protection."""

    def __init__(self) -> None:
        self._by_id: dict[int, Game] = {}
        self._by_key: dict[str, Game] = {}

    def register(self, game: Game) -> None:
        if getattr(game, "api_version", None) != 1:
            raise ValueError("game plugin must declare engine api_version=1")
        if not 1 <= game.game_id <= 255:
            raise ValueError("game IDs must fit in one byte; zero is reserved")
        if game.game_id in (1, 2, 3, 4):
            from .tictactoe import TicTacToeGame
            from .nim import NimGame
            from .scrabble import OpenWordTilesGame
            from .settlers import IslandSettlersGame
            assigned = {1: TicTacToeGame, 2: NimGame, 3: OpenWordTilesGame, 4: IslandSettlersGame}
            if type(game) is not assigned[game.game_id]:
                raise ValueError(f"game ID {game.game_id} is permanently assigned to a built-in game")
        if not 1 <= game.min_players <= game.max_players <= 255:
            raise ValueError("invalid player-count range")
        if game.game_id in self._by_id or game.key in self._by_key:
            raise ValueError(f"duplicate game ID or key: {game.game_id} / {game.key}")
        if not game.key.isascii() or not game.key.replace("-", "").isalnum():
            raise ValueError("game key must be ASCII letters, digits, and hyphens")
        required = ("new_state", "apply_action", "current_player", "encode_state", "decode_state",
                    "describe_action", "view_model")
        if any(not callable(getattr(game, name, None)) for name in required):
            raise ValueError(f"game plugin must implement: {', '.join(required)}")
        self._by_id[game.game_id] = game
        self._by_key[game.key] = game

    def by_id(self, game_id: int) -> Game:
        try:
            return self._by_id[game_id]
        except KeyError as exc:
            raise ValueError(f"unknown game ID {game_id}") from exc

    def by_key(self, key: str) -> Game:
        try:
            return self._by_key[key]
        except KeyError as exc:
            raise ValueError(f"unknown game {key!r}") from exc

    def catalog(self) -> tuple[GameInfo, ...]:
        return tuple(GameInfo(g.game_id, g.key, g.name, g.min_players, g.max_players)
                     for g in sorted(self._by_id.values(), key=lambda game: game.game_id))

    def load_plugins(self) -> None:
        """Load installed game factories from the documented entry-point group."""
        points = entry_points()
        selected = points.select(group="radio_arcade.games") if hasattr(points, "select") else points.get("radio_arcade.games", ())
        for point in selected:
            loaded = point.load()
            game = loaded() if callable(loaded) else loaded
            if getattr(game, "api_version", None) != 1:
                raise ValueError(f"game plugin {point.name!r} requires engine API version 1")
            self.register(game)


def builtin_registry(load_plugins: bool = True) -> GameRegistry:
    from .tictactoe import TicTacToeGame
    from .nim import NimGame
    from .scrabble import OpenWordTilesGame
    from .settlers import IslandSettlersGame

    registry = GameRegistry()
    registry.register(TicTacToeGame())
    registry.register(NimGame())
    registry.register(OpenWordTilesGame())
    registry.register(IslandSettlersGame())
    if load_plugins:
        registry.load_plugins()
    return registry
