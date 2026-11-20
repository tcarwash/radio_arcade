"""UI-only fleet drafts, never part of the shared radio game state."""
from dataclasses import dataclass, field

from .games.battleship import FLEET
from .practice_ai import fleet as random_fleet

CLASSIC_NAMES = ("Carrier", "Battleship", "Cruiser", "Destroyer", "Patrol boat")


@dataclass
class FleetDraft:
    ships: list[list[int]] = field(default_factory=list)
    vertical: bool = False
    _history: list[list[list[int]]] = field(default_factory=list, repr=False)

    @property
    def complete(self):
        return len(self.ships) == len(FLEET)

    def preview(self, cell):
        """Return in-bounds footprint and validity, including partial edge previews."""
        if self.complete or not 0 <= cell < 100:
            return [], False
        row, col = divmod(cell, 10)
        size = FLEET[len(self.ships)]
        coords = [(row + j if self.vertical else row, col if self.vertical else col + j)
                  for j in range(size)]
        cells = [r * 10 + c for r, c in coords if r < 10 and c < 10]
        return cells, len(cells) == size and not set(cells).intersection(c for ship in self.ships for c in ship)

    def _remember(self):
        self._history.append([s[:] for s in self.ships])

    def place(self, cell):
        cells, valid = self.preview(cell)
        if valid:
            self._remember()
            self.ships.append(cells)
        return valid

    def randomize(self):
        self._remember()
        self.ships = random_fleet(FLEET)

    def undo(self):
        if self._history:
            self.ships = self._history.pop()
        elif self.ships:
            self.ships.pop()

    def clear(self):
        if self.ships:
            self._remember()
            self.ships = []

    @property
    def can_undo(self):
        return bool(self._history or self.ships)
