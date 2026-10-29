"""Game rules, plugins, and wire-independent state."""

from .base import Game, GameInfo, GameRegistry, OpaqueStateCodec, builtin_registry

__all__ = ["Game", "GameInfo", "GameRegistry", "OpaqueStateCodec", "builtin_registry"]
