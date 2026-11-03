"""Engine-independent game sessions for LÖVE, Godot and future adapters.

The existing cartridge wire grammar is reused intentionally: on-air game
identity is a hash/version, while individual engines own their rules and
rendering. Game ID 9 remains exclusive to legacy PICO-8 sessions.
"""

from .pico8 import Pico8CartridgeGame


class ExternalGame(Pico8CartridgeGame):
    game_id = 10
    key = "external-game"
    name = "External Engine Game"

    def view_model(self, state, players):
        view = super().view_model(state, players)
        view["kind"] = "external-game"
        return view
