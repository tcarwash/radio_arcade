"""Multiplayer radio-game engine with optional desktop and TUI frontends."""

from .games.base import GameRegistry, builtin_registry
from .service import ArcadeService, EngineEvent
from .store import SessionStore

__version__ = "0.2.0"

__all__ = ["ArcadeService", "EngineEvent", "GameRegistry", "SessionStore", "builtin_registry"]

