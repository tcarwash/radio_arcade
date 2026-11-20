"""Native desktop client for the headless Radio Arcade engine."""

from __future__ import annotations

import argparse
from functools import partial
import html
from math import cos, sin
from pathlib import Path
import sys
import time
from typing import Callable

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QSettings, QSize, Qt, QThread, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QRadialGradient, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
    QGridLayout, QBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSlider, QSplitter, QStackedWidget, QStyle, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

from .presentation import CompletionTracker, scoreboard
from .desktop_results import Scoreboard, ResultDialog
from .scorched_navigation import BattleMapScroll, ScorchedMinimap
from .service import ArcadeService, EngineEvent, station_name
from .settings import Settings
from .store import SessionStore
from .transport_factory import create_service, group_name
from .games.scrabble import make_exchange, make_placement
from .games.base import builtin_registry
from .dx_cluster import build_spot, post_spot
from .games.scorched_earth import _trace_shot, _wind, pack_shot


class ChatListWidget(QListWidget):
    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


GAME_GLYPHS = {
    "tic-tac-toe": "✕○",
    "connect-four": "●四",
    "battleship": "⚓",
    "battleship-classic": "⚓",
    "sea-warfare": "⚔",
    "scorched-earth": "💥",
    "hexxagon": "⬡",
    "signal-siege": "▤",
    "open-word-tiles": "🔠",
    "nim": "◆",
    "pico8-cartridge": "👾",
    "external-game": "🕹",
}


def game_glyph(game_name: str) -> str:
    key = game_name.lower().replace(" ", "-").replace("·", "-")
    for k, v in GAME_GLYPHS.items():
        if k in key:
            return v
    return "◈"


def _style_naval_button(button: QPushButton, value: int | str, enabled: bool = True,
                        cell_size: int = 28):
    button.setFixedSize(cell_size, cell_size)
    val = str(value).strip()
    if val == "■":
        button.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a3c4d, stop:1 #0c202b);
                color: #38bdf8;
                border: 1px solid #38bdf8;
                border-radius: 4px;
                font-size: 13px;
                font-weight: 900;
            }
        """)
    elif val in ("✕", "×", "2"):
        button.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4c0519, stop:1 #27030c);
                color: #ff4757;
                border: 1px solid #f43f5e;
                border-radius: 4px;
                font-size: 14px;
                font-weight: 900;
            }
        """)
    elif val in ("·", "1"):
        button.setStyleSheet("""
            QPushButton {
                background: #07121d;
                color: #5a7495;
                border: 1px solid rgba(100, 160, 240, 0.14);
                border-radius: 4px;
                font-size: 18px;
                font-weight: 900;
            }
        """)
    elif val.startswith("S") or (isinstance(value, int) and value >= 3):
        button.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #451a03, stop:1 #220d01);
                color: #fbbf24;
                border: 1px solid #f59e0b;
                border-radius: 4px;
                font-size: 10px;
                font-weight: 800;
            }
        """)
    elif enabled:
        button.setStyleSheet("""
            QPushButton {
                background: #0a1926;
                color: #1e3d54;
                border: 1px solid rgba(0, 245, 196, 0.18);
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background: #11384e;
                border: 1px solid #00f5c4;
                color: #00f5c4;
            }
            QPushButton:pressed {
                background: #00f5c4;
                color: #02201d;
            }
        """)
    else:
        button.setStyleSheet("""
            QPushButton {
                background: #06111b;
                color: #152738;
                border: 1px solid rgba(255, 255, 255, 0.04);
                border-radius: 4px;
                font-size: 11px;
            }
        """)


def _add_radar_coordinates(grid: QGridLayout, cell_size: int = 28, responsive: bool = False):
    corner = QLabel("⌖")
    corner.setAlignment(Qt.AlignmentFlag.AlignCenter)
    corner.setStyleSheet("color: #00f5c4; font-size: 11px; font-weight: 800;")
    grid.addWidget(corner, 0, 0)
    for c in range(10):
        lbl = QLabel(chr(65 + c))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setFixedSize(cell_size, max(12, cell_size - 8))
        if responsive: lbl.setProperty("radarColumn", True)
        lbl.setStyleSheet("color: #486581; font-size: 9px; font-weight: 800; font-family: monospace;")
        grid.addWidget(lbl, 0, c + 1)
    for r in range(10):
        lbl = QLabel(str(r + 1))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setFixedSize(max(12, cell_size - 8), cell_size)
        if responsive: lbl.setProperty("radarRow", True)
        lbl.setStyleSheet("color: #486581; font-size: 9px; font-weight: 800; font-family: monospace;")
        grid.addWidget(lbl, r + 1, 0)


class SessionListCard(QWidget):
    """Compact, scannable summary for one saved async game table."""

    def __init__(self, game: str, status: str, opponent: str, stations: int,
                 activity: str, unread: int = 0, status_kind: str = "neutral"):
        super().__init__()
        self.setObjectName("sessionCardWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setToolTip(f"{game}\n{opponent} · {stations} stations\n{status}\n{activity}" +
                        (f"\n{unread} unread messages" if unread else ""))
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(8, 7, 8, 7)
        main_layout.setSpacing(10)

        glyph = QLabel(game_glyph(game))
        glyph.setObjectName("sessionGlyphIcon")
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setFixedSize(38, 38)
        main_layout.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        title = QLabel(game)
        title.setObjectName("sessionGameTitle")
        title.setMinimumWidth(0)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        badge = QLabel(status.upper())
        badge.setObjectName("sessionStatusBadge")
        badge.setProperty("kind", status_kind)
        badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(title, 1)
        head.addWidget(badge, 0)
        players = QLabel(f"{opponent}  ·  {stations} station{'s' if stations != 1 else ''}" +
                         (f"  ·  {unread} unread" if unread else ""))
        players.setObjectName("sessionPlayersLine")
        players.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        last = QLabel(activity)
        last.setObjectName("sessionActivityLine")
        last.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        players.setMinimumWidth(0)
        players.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        last.setMinimumWidth(0)
        last.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addLayout(head)
        layout.addWidget(players)
        layout.addWidget(last)
        main_layout.addLayout(layout, 1)
        self.setMinimumHeight(66)


class HeroWelcomeWidget(QWidget):
    """Luminous tactical welcome state for when no table is currently selected."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("heroWelcomeWidget")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(24, 40, 24, 40)
        layout.setSpacing(14)

        emblem = QLabel("◈")
        emblem.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emblem.setStyleSheet("""
            font-size: 50px;
            color: #00f5c4;
            background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                stop:0 rgba(0, 245, 196, 0.22), stop:0.65 rgba(0, 245, 196, 0.04), stop:1 transparent);
            border-radius: 40px;
            padding: 10px;
        """)
        layout.addWidget(emblem)

        eyebrow = QLabel("SPECTRUM ARENA  ·  RADIO ARCADE")
        eyebrow.setObjectName("eyebrow")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(eyebrow)

        title = QLabel("Take Your Seat at the Airwaves")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 26px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;")
        layout.addWidget(title)

        sub = QLabel("Select an active table from dispatch, play a local match against the computer,\nor broadcast an open invitation across the spectrum.")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        sub.setStyleSheet("font-size: 13px; color: #94a3b8; max-width: 480px; line-height: 1.5;")
        layout.addWidget(sub)

        chips = QHBoxLayout()
        chips.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chips.setSpacing(8)
        for icon, text in [("✳", "2.4GHz KISS Broadcast"), ("⌘", "Deterministic Engine"), ("◉", "Hot-Seat Practice")]:
            chip = QLabel(f"{icon}  {text}")
            chip.setStyleSheet("background: rgba(255, 255, 255, 0.035); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 999px; padding: 5px 12px; color: #cbd5e1; font-size: 11px; font-weight: 600;")
            chips.addWidget(chip)
        layout.addLayout(chips)


class ScorchedBattlefield(QWidget):
    shot_position = Signal(float, float)
    animation_finished = Signal()
    """Resolution-independent native rendering of the shared artillery state."""

    def __init__(self, view: dict, animate: bool = False, parent=None):
        super().__init__(parent)
        self.view = view
        self.preview: list[tuple[int, int]] = []
        self.aim_angle = 45
        self.aim_player = view.get("turn")
        assets = Path(__file__).with_name("web_assets")
        self.sprites = [QPixmap(str(assets / name)) for name in ("tank-teal.png", "tank-amber.png")]
        self.flight_progress = 1.0
        self._flight_started = 0.0
        self._flight_timer = QTimer(self)
        self._flight_timer.setInterval(16)
        self._flight_timer.timeout.connect(self._advance_flight)
        self.setMinimumSize(240, 165)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setObjectName("scorchedBattlefield")
        if animate and len(view.get("trajectory", [])) > 1:
            self.flight_progress = 0.0
            self._flight_started = time.monotonic()
            self._flight_timer.start()

    def replay(self, view=None, started=None):
        if view is not None:
            self.view = view
        if len(self.view.get("trajectory", [])) < 2:
            return
        self.preview = []
        self._flight_started = time.monotonic() if started is None else started
        self.flight_progress = 0.0
        self._flight_timer.start()
        self._advance_flight()

    def _advance_flight(self):
        self.flight_progress = min(1.0, (time.monotonic() - self._flight_started) / 2.0)
        path = self.view.get("trajectory", [])
        if path:
            index = min(len(path) - 1, int(self.flight_progress * (len(path) - 1)))
            self.shot_position.emit(path[index][0], path[index][1])
        self.update()
        if self.flight_progress >= 1:
            if time.monotonic() - self._flight_started >= 4.4:
                self._flight_timer.stop()
                self.animation_finished.emit()

    def displayed_terrain(self):
        after = self.view["terrain"]
        if not self._flight_timer.isActive():
            return after
        before = self.view.get("previous_terrain", after)
        progress = max(0.0, min(1.0, (time.monotonic() - self._flight_started - 2.0) / .65))
        return [old + (new - old) * progress for old, new in zip(before, after)]

    def set_preview(self, path: list[tuple[int, int]], angle: int = 45, player: str | None = None):
        self.preview = path
        self.aim_angle = angle
        self.aim_player = player
        self.update()

    def paintEvent(self, _event):
        view = self.view
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#060b13"))
        margin = 12
        world_width, world_height = view["width"], view["sky_height"]
        scale = min((self.width() - 2 * margin) / world_width, (self.height() - 2 * margin) / world_height)
        ox = (self.width() - world_width * scale) / 2
        oy = (self.height() - world_height * scale) / 2
        painter.translate(ox, oy)
        painter.scale(scale, scale)
        sky = QLinearGradient(0, 0, 0, world_height)
        sky.setColorAt(0, QColor("#040810")); sky.setColorAt(0.4, QColor("#081425")); sky.setColorAt(0.75, QColor("#10243a")); sky.setColorAt(1, QColor("#183852"))
        painter.fillRect(0, 0, world_width, world_height, QBrush(sky))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(80, 130, 170, 30))
        distant = QPolygonF([QPointF(0, world_height)] +
                            [QPointF(x, world_height - 11 + ((x * 13 + 7) % 9) / 3) for x in range(world_width + 1)] +
                            [QPointF(world_width, world_height), QPointF(0, world_height)])
        painter.drawPolygon(distant)
        painter.setBrush(QColor(50, 90, 130, 42))
        mid = QPolygonF([QPointF(0, world_height)] +
                        [QPointF(x, world_height - 6 + ((x * 17 + 11) % 7) / 2.5) for x in range(world_width + 1)] +
                        [QPointF(world_width, world_height), QPointF(0, world_height)])
        painter.drawPolygon(mid)
        for i in range(46):
            star_color = QColor(167, 243, 208, 140) if i % 7 == 0 else QColor(254, 240, 138, 120) if i % 5 == 0 else QColor(217, 232, 251, 95)
            painter.setBrush(star_color)
            size = .045 if i % 5 else .08
            painter.drawEllipse(QPointF(((i * 187 + 43) % (world_width * 24)) / 24,
                                        ((i * 79 + 17) % (world_height * 12)) / 24), size, size)
        terrain = QPolygonF([QPointF(0, world_height)] +
                            [QPointF(x + .5, world_height - height) for x, height in enumerate(self.displayed_terrain())] +
                            [QPointF(world_width, world_height)])
        earth = QLinearGradient(0, world_height - 15, 0, world_height)
        earth.setColorAt(0, QColor("#1e3f34"))
        earth.setColorAt(0.5, QColor("#122620"))
        earth.setColorAt(1, QColor("#08120f"))
        painter.setBrush(QBrush(earth))
        painter.setPen(QPen(QColor("#00f5c4"), .14))
        painter.drawPolygon(terrain)
        def draw_path(points, color, dotted=False):
            if len(points) < 2:
                return
            path = QPainterPath(QPointF(points[0][0], world_height - points[0][1]))
            for x, y in points[1:]:
                path.lineTo(x, world_height - y)
            if dotted:
                glow_pen = QPen(QColor(0, 245, 196, 45), .24)
                painter.setPen(glow_pen); painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawPath(path)
                pen = QPen(QColor("#00f5c4"), .10)
                pen.setStyle(Qt.PenStyle.DashLine)
            else:
                glow_pen = QPen(QColor(255, 180, 80, 60), .26)
                painter.setPen(glow_pen); painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawPath(path)
                pen = QPen(QColor(color), .13)
            painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawPath(path)
        trajectory = view.get("trajectory", [])
        if self.flight_progress < 1 and trajectory:
            position = self.flight_progress * (len(trajectory) - 1)
            index = min(len(trajectory) - 2, int(position))
            fraction = position - index
            start = trajectory[index]; end = trajectory[index + 1]
            location = (start[0] + (end[0] - start[0]) * fraction,
                        start[1] + (end[1] - start[1]) * fraction)
            draw_path(trajectory[:index + 1] + [location], "#ffb84d")
            halo = QRadialGradient(QPointF(location[0], world_height - location[1]), .55)
            halo.setColorAt(0, QColor(255, 255, 220, 240))
            halo.setColorAt(0.4, QColor(255, 170, 50, 180))
            halo.setColorAt(1, QColor(255, 60, 20, 0))
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QBrush(halo))
            painter.drawEllipse(QPointF(location[0], world_height - location[1]), .55, .55)
        draw_path([(x / 1000, y / 1000) for x, y in self.preview], "#00f5c4", True)
        impact = view.get("impact")
        if impact and impact["kind"] != "edge" and self.flight_progress >= 1:
            cx, cy = impact["x"], world_height - impact["y"]
            radius = view["weapons"][impact["weapon"]]["radius"]
            age = max(0.0, time.monotonic() - self._flight_started - 2.0)
            flashing = self._flight_timer.isActive()
            if flashing:
                fade = max(0.0, 1.0 - age / 2.4)
                spread = min(1.0, age / .35)
                painter.setPen(QPen(QColor(255, 215, 155, int(200 * fade)), .15))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(cx, cy), radius * (1 + age * 2), radius * (.6 + age))
                for i in range(10):
                    dx = ((i * 17) % 13 - 6) * .2
                    smoke = QColor(65, 61, 58, int(125 * fade))
                    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(smoke)
                    painter.drawEllipse(QPointF(cx + dx * (1 + age), cy - age * (1.5 + i % 3)),
                                        radius * (.2 + age * .3), radius * (.3 + age * .4))
                if age < 1.2:
                    size = radius * (.4 + spread * 1.4) * (1 - age / 1.8)
                    glow = QRadialGradient(QPointF(cx, cy), max(.1, size))
                    glow.setColorAt(0, QColor(255, 255, 220, int(255 * fade)))
                    glow.setColorAt(.25, QColor(255, 220, 80, int(255 * fade)))
                    glow.setColorAt(.65, QColor(255, 85, 20, int(220 * fade)))
                    glow.setColorAt(1, QColor(170, 30, 10, 0))
                    painter.setBrush(QBrush(glow)); painter.drawEllipse(QPointF(cx, cy), size, size)
                for i in range(38):
                    direction = i * 6.283185307 / 38
                    speed = 2 + (i * 7 % 13) * .4
                    dx, dy = cos(direction) * speed * age, -abs(sin(direction)) * speed * age + age * age * 2
                    painter.setBrush(QColor(255, 175, 70, int(255 * fade)) if i % 3 else QColor(105, 76, 48, int(255 * fade)))
                    painter.drawEllipse(QPointF(cx + dx, cy + dy), .09 + i % 3 * .035, .09 + i % 3 * .035)
            else:
                painter.setPen(QPen(QColor(80, 52, 37, 160), .16))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                scar = QPainterPath()
                connected = False
                for column, (before, after) in enumerate(zip(view.get("previous_terrain", view["terrain"]), view["terrain"])):
                    if before > after:
                        point = QPointF(column + .5, world_height - after)
                        if connected: scar.lineTo(point)
                        else: scar.moveTo(point)
                        connected = True
                    else:
                        connected = False
                painter.drawPath(scar)
        for index, tank in enumerate(view["tanks"]):
            x, y = tank["x"], world_height - tank["world_y"]
            y -= self.displayed_terrain()[tank["x"]] - view["terrain"][tank["x"]]
            if self._flight_timer.isActive() and self.flight_progress < .18 and tank["player"] == view.get("last_actor"):
                x += (-.14 if index == 0 else .14) * sin(self.flight_progress / .18 * 3.14159)
            if self._flight_timer.isActive() and self.flight_progress >= 1 and view.get("last_damage", [0, 0])[index]:
                x += .12 * sin(time.monotonic() * 45)
            if tank["shielded"]:
                painter.setPen(QPen(QColor("#00f5c4"), .14))
                shield_glow = QRadialGradient(QPointF(x, y), 1.2)
                shield_glow.setColorAt(0, QColor(0, 245, 196, 15))
                shield_glow.setColorAt(0.7, QColor(0, 245, 196, 50))
                shield_glow.setColorAt(1, QColor(0, 245, 196, 0))
                painter.setBrush(QBrush(shield_glow))
                painter.drawEllipse(QPointF(x, y), 1.2, .95)
            painter.setPen(QPen(QColor("#142333"), .11))
            painter.setBrush(QColor("#00f5c4" if index == 0 else "#fbbf24"))
            if not self.sprites[index].isNull():
                painter.drawPixmap(QRectF(x - 1.05, y - .96, 2.1, 1.45), self.sprites[index],
                                   QRectF(self.sprites[index].rect()))
            else:
                painter.drawRoundedRect(QRectF(x - .65, y - .25, 1.3, .55), .15, .15)
            painter.setPen(QPen(QColor("#e7edf4"), .15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            angle = (self.aim_angle if tank["player"] == self.aim_player else
                     view["last_action_parameter"] if tank["player"] == view.get("last_actor") and
                     view.get("impact") else 45)
            direction = 1 if index == 0 else -1
            barrel_x, barrel_y = x + direction * .15, y - .48
            theta = angle * 3.1415926535 / 180
            painter.drawLine(QPointF(barrel_x, barrel_y),
                             QPointF(barrel_x + direction * 1.0 * cos(theta), barrel_y - 1.0 * sin(theta)))
            hp = tank["hp"]
            hp_ratio = max(0.0, min(1.0, hp / 100.0))
            bar_w = 2.4; bar_h = .28; bar_x = x - bar_w / 2; bar_y = y - 1.6
            painter.setPen(QPen(QColor(15, 23, 42, 200), .06))
            painter.setBrush(QColor(10, 15, 25, 220))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), .06, .06)
            fill_color = QColor("#00f5c4") if hp > 45 else QColor("#fbbf24") if hp > 20 else QColor("#f43f5e")
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(fill_color)
            painter.drawRoundedRect(QRectF(bar_x + .03, bar_y + .03, max(.1, (bar_w - .06) * hp_ratio), bar_h - .06), .04, .04)
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Sans Serif", 7))
            painter.save(); painter.scale(1 / scale, 1 / scale)
            painter.drawText(QPointF(x * scale - 22, (y - .7) * scale), f"{tank['hp']} HP")
            painter.restore()
        painter.end()


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Station settings · Radio Arcade")
        self.setMinimumSize(300, 300)
        self.settings = settings
        outer = QVBoxLayout(self)
        title = QLabel("Connect your station")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)
        subtitle = QLabel("Choose how Radio Arcade reaches your Data2G host.")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)
        form_host = QWidget(); form_host.setObjectName("settingsFormHost")
        form = QFormLayout(form_host)
        if parent and parent.width() < 500:
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.callsign = QLineEdit(settings.callsign)
        self.callsign.setPlaceholderText("N0CALL")
        self.host = QLineEdit(settings.host)
        self.transport = QComboBox()
        self.transport.addItem("Broadcast · KISS", "broadcast")
        self.transport.addItem("Connected · VARA-like", "connected")
        self.transport.addItem("Local practice · vs computer", "practice")
        self.transport.setCurrentIndex(max(0, self.transport.findData(settings.transport)))
        self.group = QLineEdit(settings.group)
        self.broadcast_mode = QLineEdit(settings.broadcast_mode)
        self.broadcast_mode.setPlaceholderText("Blank = Data2G default · e.g. n10-qpsk-r1/5")
        self.station_id_mode = QComboBox()
        self.station_id_mode.addItem("Data2G CWID · fallback to data ID", "auto")
        self.station_id_mode.addItem("Radio Arcade data ID", "application")
        self.station_id_mode.setCurrentIndex(max(0, self.station_id_mode.findData(settings.station_id_mode)))
        self.cw_wpm = QSpinBox(); self.cw_wpm.setRange(15, 40); self.cw_wpm.setValue(settings.cw_wpm)
        self.command_port = QSpinBox(); self.command_port.setRange(1, 65535); self.command_port.setValue(settings.command_port)
        self.kiss_port = QSpinBox(); self.kiss_port.setRange(1, 65535); self.kiss_port.setValue(settings.kiss_port)
        self.scheme = QComboBox(); self.scheme.addItem("Midnight", "midnight"); self.scheme.addItem("Daylight", "daylight")
        self.scheme.addItem("Monochrome", "monochrome")
        self.scheme.setCurrentIndex(max(0, self.scheme.findData(settings.color_scheme)))
        self.sound = QCheckBox("Play sounds"); self.sound.setChecked(settings.sound_enabled)
        self.experimental_games = QCheckBox("Experimental games")
        self.experimental_games.setToolTip("Show the Signal Siege prototype when creating a table or calling CQ")
        self.experimental_games.setChecked(settings.experimental_games)
        self.observer = QCheckBox("Receive-only observer")
        self.observer.setToolTip("Manual snapshot requests still transmit")
        self.observer.setChecked(settings.observer_mode)
        self.dx_cluster_url = QLineEdit(settings.dx_cluster_url)
        self.dx_cluster_url.setPlaceholderText("https://cluster.example.org")
        form.addRow("Callsign", self.callsign)
        form.addRow("Data2G host", self.host)
        form.addRow("Transport", self.transport)
        form.addRow("Broadcast group", self.group)
        form.addRow("Broadcast modem mode", self.broadcast_mode)
        form.addRow("Station identification", self.station_id_mode)
        form.addRow("CW ID speed (WPM)", self.cw_wpm)
        form.addRow("Command port", self.command_port)
        form.addRow("KISS port", self.kiss_port)
        form.addRow("Color scheme", self.scheme)
        form.addRow("Sound", self.sound)
        form.addRow("Games", self.experimental_games)
        form.addRow("Observer", self.observer)
        form.addRow("DX Cluster URL", self.dx_cluster_url)
        form_scroll = QScrollArea(); form_scroll.setObjectName("settingsFormScroll")
        form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(form_host)
        outer.addWidget(form_scroll, 1)
        buttons = QHBoxLayout(); buttons.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        save = QPushButton("Save settings"); save.setObjectName("primaryButton"); save.clicked.connect(self._save)
        buttons.addWidget(cancel); buttons.addWidget(save); outer.addLayout(buttons)
        if parent:
            self.resize(max(300, min(520, parent.width() - 16)),
                        max(300, min(640, parent.height() - 16)))

    def _save(self):
        try:
            call = station_name(self.callsign.text())
            group = group_name(self.group.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Check settings", str(exc))
            return
        self.settings.callsign = call
        self.settings.host = self.host.text().strip() or "127.0.0.1"
        self.settings.transport = self.transport.currentData()
        if self.observer.isChecked() and self.settings.transport != "broadcast":
            QMessageBox.warning(self, "Observer mode", "Select Broadcast · KISS for observer mode.")
            return
        self.settings.observer_mode = self.observer.isChecked()
        self.settings.group = group
        self.settings.broadcast_mode = self.broadcast_mode.text().strip().lower()
        self.settings.station_id_mode = self.station_id_mode.currentData()
        self.settings.cw_wpm = self.cw_wpm.value()
        self.settings.command_port = self.command_port.value()
        self.settings.kiss_port = self.kiss_port.value()
        self.settings.color_scheme = self.scheme.currentData()
        self.settings.sound_enabled = self.sound.isChecked()
        self.settings.experimental_games = self.experimental_games.isChecked()
        self.settings.dx_cluster_url = self.dx_cluster_url.text().strip()
        self.settings.save()
        self.accept()


class ServiceWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent); self.settings = settings
    def run(self):
        try: self.ready.emit(create_service(self.settings))
        except Exception as exc: self.failed.emit(str(exc))


class TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, fn: Callable):
        super().__init__(); self.fn = fn; self.signals = TaskSignals()
    @Slot()
    def run(self):
        try: self.signals.done.emit(self.fn())
        except Exception as exc: self.signals.failed.emit(str(exc))


class EventBridge(QObject):
    received = Signal(object)


class ArcadeWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.engine: ArcadeService | None = None
        self.unsubscribe = None
        self._ui_settings = QSettings("Radio Arcade", "Radio Arcade")
        self.selected_id: str | None = self._ui_settings.value(f"selectedTable/{settings.callsign}") or None
        self._table_list_initialized = False
        saved_unread = self._ui_settings.value(f"unreadTables/{settings.callsign}", {})
        self._unread_by_session: dict[str, int] = {
            str(key): int(value) for key, value in (saved_unread.items() if isinstance(saved_unread, dict) else [])
            if str(value).isdigit() and int(value) > 0
        }
        self._preserve_empty_selection = False
        self._completion_tracker = CompletionTracker()
        self._result_dialog = None
        self._pending_result_id = None
        self._completion_timer = QTimer(self); self._completion_timer.setSingleShot(True)
        self._completion_timer.timeout.connect(self._finish_scorched_result)
        self._sync_retry_timer = QTimer(self); self._sync_retry_timer.setInterval(1000)
        self._sync_retry_timer.timeout.connect(self._refresh_sync_retry)
        self._sync_retry_timer.start()
        self._board_signature = None
        self._last_scorched_animation_key = None
        self._scorched_weapons = {}
        self._battleship_drafts = {}
        self._event_bridge = EventBridge(self)
        self._event_bridge.received.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        self._pool = QThreadPool.globalInstance()
        self._tasks: set[Task] = set()
        self.setWindowTitle("Radio Arcade")
        self.resize(1320, 820)
        self.setMinimumSize(360, 400)
        self._build_ui()
        # Keep game and CQ choices usable while the radio service is offline.
        # The controls used to retain only their two starter placeholders until
        # a connection succeeded, making the experimental setting appear inert.
        self._populate_games()
        self._apply_style()
        self._update_status("Starting up", False)
        self._show_session(None)
        self._update_compact_layout()

    def _build_ui(self):
        root = QWidget(); root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root); layout.setContentsMargins(18, 16, 18, 14); layout.setSpacing(14)
        self.root_layout = layout
        top_frame = QFrame(); top_frame.setObjectName("topbarFrame")
        self.top_frame = top_frame
        top_layout = QHBoxLayout(top_frame); top_layout.setContentsMargins(16, 8, 16, 8); top_layout.setSpacing(12)
        self.brand = QLabel("◈  RADIO ARCADE"); self.brand.setObjectName("brand")
        top_layout.addWidget(self.brand)
        self.transport_label = QLabel("DATA2G · RADIO GAMES"); self.transport_label.setObjectName("eyebrow")
        top_layout.addWidget(self.transport_label); top_layout.addStretch(1)
        status_pill = QFrame(); status_pill.setObjectName("statusPill")
        self.status_pill = status_pill
        pill_layout = QHBoxLayout(status_pill); pill_layout.setContentsMargins(10, 4, 12, 4); pill_layout.setSpacing(8)
        self.link_dot = QLabel("●"); self.link_dot.setObjectName("statusDot")
        self.status_label = QLabel("Starting up"); self.status_label.setObjectName("statusText")
        pill_layout.addWidget(self.link_dot); pill_layout.addWidget(self.status_label)
        top_layout.addWidget(status_pill)
        self.tx_activity = QLabel(""); self.tx_activity.setObjectName("muted")
        top_layout.addWidget(self.tx_activity)
        self.tx_toggle = QPushButton("TX ENABLED" if self.settings.tx_enabled else "TX DISABLED")
        self.tx_toggle.setObjectName("txToggle")
        self.tx_toggle.setCheckable(True); self.tx_toggle.setChecked(self.settings.tx_enabled)
        self.tx_toggle.setToolTip("Allow or block outgoing Data2G traffic")
        self.tx_toggle.toggled.connect(self._set_tx_enabled)
        self.tx_toggle.setEnabled(self.settings.transport != "practice")
        top_layout.addWidget(self.tx_toggle)
        self.settings_button = QPushButton("⚙  Settings"); self.settings_button.setObjectName("settingsButton")
        self.settings_button.clicked.connect(self._edit_settings)
        top_layout.addWidget(self.settings_button)
        layout.addWidget(top_frame)

        splitter = QSplitter(Qt.Orientation.Horizontal); splitter.setChildrenCollapsible(False)
        self.desktop_splitter = splitter
        left = self._card("GAMES", "Your tables")
        left.setMinimumWidth(245)
        left_layout = left.layout()
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(8)
        self.session_list = QListWidget(); self.session_list.setObjectName("sessionList")
        self.session_list.currentItemChanged.connect(self._select_item)
        self.session_list.setMinimumHeight(70)
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.session_search = QLineEdit(); self.session_search.setPlaceholderText("Find game or station…")
        self.session_search.setClearButtonEnabled(True)
        self.session_filter = QComboBox(); self.session_filter.addItem("All tables", "all")
        self.session_filter.addItem("Your move", "your-turn")
        self.session_filter.addItem("Waiting", "waiting")
        self.session_filter.addItem("Needs resync", "resync")
        self.session_filter.addItem("Unread chat", "unread")
        self.session_search.setMinimumWidth(0)
        self.session_search.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.session_filter.setMinimumWidth(92)
        self.session_search.textChanged.connect(self._refresh_sessions)
        self.session_filter.currentIndexChanged.connect(self._refresh_sessions)
        table_filter_row = QHBoxLayout(); table_filter_row.setSpacing(6)
        table_filter_row.addWidget(self.session_search, 1); table_filter_row.addWidget(self.session_filter)
        left_layout.addLayout(table_filter_row)
        left_layout.addWidget(self.session_list, 2)
        self.cq_toggle = QPushButton("⌁  Find a game · Call CQ")
        self.cq_toggle.setCheckable(True)
        self.cq_toggle.toggled.connect(self._toggle_cq_panel)
        left_layout.addWidget(self.cq_toggle)
        self.cq_scroll = QScrollArea(); self.cq_scroll.setWidgetResizable(True)
        self.cq_scroll.setObjectName("cqScroll"); self.cq_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cq_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cq_scroll.setMaximumHeight(190)
        self.cq_scroll.hide()
        cq_content = QWidget(); cq_content.setObjectName("toolsContent")
        cq_layout = QVBoxLayout(cq_content); cq_layout.setContentsMargins(0, 4, 2, 4); cq_layout.setSpacing(7)
        self.cq_scroll.setWidget(cq_content)
        left_layout.addWidget(self.cq_scroll)
        self.new_game_toggle = QPushButton("＋  Start a new game")
        self.new_game_toggle.setCheckable(True)
        self.new_game_toggle.toggled.connect(self._toggle_new_game_setup)
        left_layout.addWidget(self.new_game_toggle)
        tools_scroll = QScrollArea(); tools_scroll.setWidgetResizable(True)
        self.tools_scroll = tools_scroll
        tools_scroll.setMinimumHeight(90)
        self.new_game_toggle.setChecked(True)
        tools_scroll.setObjectName("toolsScroll"); tools_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tools_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tools_scroll.viewport().setObjectName("toolsViewport")
        tools_widget = QWidget(); tools_widget.setObjectName("toolsContent"); tools_layout = QVBoxLayout(tools_widget)
        tools_layout.setContentsMargins(0, 10, 2, 4); tools_layout.setSpacing(10)
        tools_scroll.setWidget(tools_widget)
        left_layout.addWidget(tools_scroll, 2)
        tools_layout.addWidget(QLabel("START A GAME"), 0)
        self.game_choice = QComboBox(); self.game_choice.setObjectName("field")
        self.game_choice.addItem("Tic-tac-toe", "tic-tac-toe")
        self.game_choice.addItem("Nim · 2–4 players", "nim")
        tools_layout.addWidget(self.game_choice)
        self.hosted_checkbox = QCheckBox("Host / dealer · public broadcast table")
        self.hosted_checkbox.setEnabled(self.settings.transport == "broadcast" and not self.settings.observer_mode)
        self.hosted_checkbox.toggled.connect(self._update_new_game_mode)
        tools_layout.addWidget(self.hosted_checkbox)
        self.peer_input = QLineEdit(); self.peer_input.setPlaceholderText("Opponent callsign")
        tools_layout.addWidget(self.peer_input)
        self.create_hint = QLabel(); self.create_hint.setObjectName("muted")
        self.create_hint.setWordWrap(True); tools_layout.addWidget(self.create_hint)
        self._update_new_game_mode()
        self.connect_button = QPushButton("Connect / resume peer"); self.connect_button.clicked.connect(self._connect_peer)
        self.disconnect_button = QPushButton("Disconnect radio link"); self.disconnect_button.clicked.connect(self._disconnect_peer)
        self.connect_button.setVisible(self.settings.transport == "connected")
        self.disconnect_button.setVisible(self.settings.transport == "connected")
        tools_layout.addWidget(self.connect_button); tools_layout.addWidget(self.disconnect_button)
        self.new_button = QPushButton("＋  New game"); self.new_button.setObjectName("primaryButton")
        self.new_button.clicked.connect(self._new_game)
        self.new_button.setMinimumHeight(42)
        tools_layout.addWidget(self.new_button)
        self.cq_heading = QLabel("CALLS HEARD RECENTLY")
        self.cq_heading.setObjectName("eyebrow")
        cq_layout.addWidget(self.cq_heading)
        self.cq_choice = QComboBox(); self.cq_choice.setObjectName("field")
        self.cq_choice.addItem("Any game", None)
        cq_layout.addWidget(self.cq_choice)
        self.cq_send = QPushButton("Call CQ to all stations")
        self.cq_send.clicked.connect(self._call_cq)
        cq_layout.addWidget(self.cq_send)
        self.cq_list = QListWidget(); self.cq_list.setMinimumHeight(55)
        self.cq_list.setToolTip("CQ calls heard in the last 15 minutes")
        cq_layout.addWidget(self.cq_list)
        self.cq_answer = QPushButton("Answer selected CQ")
        self.cq_answer.clicked.connect(self._answer_cq)
        cq_layout.addWidget(self.cq_answer)
        self.cq_widgets = (self.cq_heading, self.cq_choice, self.cq_send, self.cq_list, self.cq_answer)
        for control in self.cq_widgets:
            control.setVisible(True)
        self._update_cq_visibility()
        self.cq_timer = QTimer(self)
        self.cq_timer.setInterval(60_000)
        self.cq_timer.timeout.connect(self._refresh_cq)
        self.cq_timer.start()
        self.invite_button = QPushButton("Invite to selected lobby"); self.invite_button.clicked.connect(self._invite_player)
        tools_layout.addWidget(self.invite_button)
        self.join_button = QPushButton("Join broadcast table"); self.join_button.clicked.connect(self._join_game)
        tools_layout.addWidget(self.join_button)
        self.announce_button = QPushButton("Re-announce lobby"); self.announce_button.clicked.connect(self._announce_game)
        tools_layout.addWidget(self.announce_button)
        self.spot_button = QPushButton("✦  Spot my game · DX Cluster"); self.spot_button.clicked.connect(self._spot_game)
        tools_layout.addWidget(self.spot_button)
        self.order_input = QLineEdit(); self.order_input.setPlaceholderText("Turn order: HOST,GUEST1,GUEST2")
        tools_layout.addWidget(self.order_input)
        self.order_button = QPushButton("Set dealer reply order"); self.order_button.clicked.connect(self._set_reply_order)
        tools_layout.addWidget(self.order_button)
        self.retry_button = QPushButton("Retry pending move"); self.retry_button.clicked.connect(self._retry_move)
        tools_layout.addWidget(self.retry_button)
        self.cancel_invite_button = QPushButton("Cancel pending invitation")
        self.cancel_invite_button.clicked.connect(self._cancel_invitation)
        tools_layout.addWidget(self.cancel_invite_button)
        self.start_button = QPushButton("Start selected game"); self.start_button.clicked.connect(self._start_game)
        self.start_button.setObjectName("primaryButton")
        self.start_button.setMinimumHeight(42)
        self.start_button.hide()
        tools_layout.addStretch(1)
        left_layout.addWidget(self.start_button)
        self.forfeit_button = QPushButton("Forfeit game…"); self.forfeit_button.setObjectName("forfeitGame")
        self.forfeit_button.clicked.connect(self._forfeit_game); self.forfeit_button.hide()
        self.delete_button = QPushButton("Delete selected game…")
        self.delete_button.clicked.connect(self._delete_game)
        self.delete_button.setEnabled(False)
        self.delete_button.hide()
        splitter.addWidget(left)

        center = self._card("ACTIVE TABLE", "Game workspace")
        center_layout = center.layout()
        self.game_heading = QLabel("Welcome to the airwaves"); self.game_heading.setObjectName("gameTitle")
        self.game_subtitle = QLabel("Start a game or accept an invitation to take a seat."); self.game_subtitle.setObjectName("muted")
        heading_row = QHBoxLayout(); heading_row.addWidget(self.game_heading, 1)
        self.result_button = QPushButton("View match result"); self.result_button.clicked.connect(self._view_result)
        self.result_button.hide(); heading_row.addWidget(self.result_button)
        center_layout.addLayout(heading_row); center_layout.addWidget(self.game_subtitle)
        self.sync_button = QPushButton("↻  Sync / recover state"); self.sync_button.clicked.connect(self._sync_selected)
        self.sync_button.setVisible(False)
        self.table_action_row = QHBoxLayout(); self.table_action_row.setSpacing(6)
        self.table_action_row.addWidget(self.sync_button)
        self.table_action_row.addWidget(self.forfeit_button)
        self.table_action_row.addWidget(self.delete_button)
        self.table_action_row.addStretch(1)
        center_layout.addLayout(self.table_action_row)
        self.scoreboard_host = QWidget(); self.scoreboard_layout = QVBoxLayout(self.scoreboard_host)
        self.scoreboard_layout.setContentsMargins(0, 0, 0, 0)
        self.scoreboard_compact = QLabel(); self.scoreboard_compact.setObjectName("compactScoreboard")
        self.scoreboard_compact.setWordWrap(True)
        self.scoreboard_compact.hide()
        center_layout.addWidget(self.scoreboard_compact)
        center_layout.addWidget(self.scoreboard_host)
        self.board_host = QWidget(); self.board_host.setObjectName("boardHost"); self.board_layout = QVBoxLayout(self.board_host); self.board_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.board_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.board_scroll = QScrollArea(); self.board_scroll.setObjectName("boardScroll")
        self.board_scroll.viewport().setObjectName("boardViewport")
        self.board_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.board_scroll.setWidgetResizable(True); self.board_scroll.setWidget(self.board_host)
        center_layout.addWidget(self.board_scroll, 1)
        self.accept_button = QPushButton("Accept invitation"); self.accept_button.setObjectName("primaryButton")
        self.accept_button.hide()
        self.accept_button.clicked.connect(self._accept_invite); center_layout.addWidget(self.accept_button, 0, Qt.AlignmentFlag.AlignCenter)
        splitter.addWidget(center)

        right = self._card("STATION PANEL", "Players & side chat")
        right_layout = right.layout()
        self.turn_badge = QLabel("NO GAME SELECTED"); self.turn_badge.setObjectName("turnBadge")
        right_layout.addWidget(self.turn_badge)
        self.players_label = QLabel("Stations appear here when a lobby opens."); self.players_label.setObjectName("players")
        self.players_label.setWordWrap(True); right_layout.addWidget(self.players_label)
        right_layout.addWidget(QLabel("SIDE CHAT"))
        self.chat_list = ChatListWidget(); self.chat_list.setObjectName("chatList");
        self.chat_list.setWordWrap(True); self.chat_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.chat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_list.setResizeMode(QListWidget.ResizeMode.Adjust); right_layout.addWidget(self.chat_list, 1)
        self.chat_list.resized.connect(self._size_chat_rows)
        chat_row = QHBoxLayout(); self.chat_input = QLineEdit(); self.chat_input.setPlaceholderText("Message · up to 150 UTF-8 bytes")
        self.chat_input.returnPressed.connect(self._send_chat)
        self.chat_send = QPushButton("Send"); self.chat_send.clicked.connect(self._send_chat)
        chat_row.addWidget(self.chat_input, 1); chat_row.addWidget(self.chat_send); right_layout.addLayout(chat_row)
        self.chat_count = QLabel("0 / 150 bytes"); self.chat_count.setObjectName("muted")
        self.chat_input.textChanged.connect(lambda text: self.chat_count.setText(f"{len(text.strip().encode('utf-8'))} / 150 bytes"))
        right_layout.addWidget(self.chat_count)
        splitter.addWidget(right)
        # Prioritize the battlefield at desktop resolutions. The side panels
        # remain resizable, while Scorched Earth itself pans inside the arena.
        splitter.setSizes([280, 795, 245])
        self.panes = (left, center, right)
        self.card_headers = tuple(
            (pane.layout().itemAt(0).widget(), pane.layout().itemAt(1).widget())
            for pane in self.panes
        )
        self.compact_tabs = QTabWidget()
        self.compact_tabs.setObjectName("compactTabs")
        self.compact_tabs.tabBar().setExpanding(True)
        self.compact_tabs.currentChanged.connect(self._on_compact_tab_changed)
        self.compact_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.layout_stack = QStackedWidget()
        self.layout_stack.addWidget(splitter)
        self.layout_stack.addWidget(self.compact_tabs)
        layout.addWidget(self.layout_stack, 1)
        foot = QHBoxLayout()
        self.footer_label = QLabel("Public radio · keep moves short, keep the good sport")
        self.footer_label.setObjectName("muted"); foot.addWidget(self.footer_label); foot.addStretch(1)
        self.mode_label = QLabel("Offline"); self.mode_label.setObjectName("modeLabel"); foot.addWidget(self.mode_label)
        layout.addLayout(foot)
        self.notification_button = QPushButton(); self.notification_button.hide()
        self.notification_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.notification_button.clicked.connect(self._open_notification)
        layout.addWidget(self.notification_button)
        self._notification_session = None
        self._notification_cq = False
        self._tray = QSystemTrayIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation), self)
        self._tray.setToolTip("Radio Arcade")
        self._tray.messageClicked.connect(self._open_notification)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "layout_stack"):
            self._update_compact_layout()
            self._update_vertical_density()
            QTimer.singleShot(0, self._fit_naval_grid_cells)

    def _update_compact_layout(self):
        compact = self.width() < 950
        narrow = self.width() < 520
        self.forfeit_button.setText("Forfeit" if narrow else "Forfeit game…")
        self.delete_button.setText("Delete" if narrow else "Delete selected game…")
        if self.engine and self.selected_id:
            try: self.sync_button.setText(self._sync_label_for_session(self.engine.session(self.selected_id)))
            except KeyError: self.sync_button.setText("↻  Resync table")
        if compact == (self.layout_stack.currentWidget() is self.compact_tabs):
            self.game_subtitle.setVisible((not compact or self.width() >= 520) and self.height() >= 800)
            self._update_vertical_density()
            return
        if compact:
            self._desktop_sizes = self.desktop_splitter.sizes()
            for pane, title in zip(self.panes, ("Games", "Table", "Chat")):
                self.compact_tabs.addTab(pane, title)
            self.layout_stack.setCurrentWidget(self.compact_tabs)
        else:
            for pane in self.panes:
                self.compact_tabs.removeTab(0)
                self.desktop_splitter.addWidget(pane)
            self.desktop_splitter.setSizes(getattr(self, "_desktop_sizes", [280, 795, 245]))
            self.layout_stack.setCurrentWidget(self.desktop_splitter)
            for pane in self.panes:
                pane.show()
        self.root_layout.setContentsMargins(*( (8, 8, 8, 8) if compact else (22, 20, 22, 18) ))
        self.root_layout.setSpacing(8 if compact else 16)
        self.brand.setText("◈  ARCADE" if compact else "◈  RADIO ARCADE")
        self.game_subtitle.setVisible((not compact or self.width() >= 520) and self.height() >= 800)
        self.transport_label.setVisible(not compact)
        if hasattr(self, "status_pill"):
            self.status_pill.setVisible(not compact)
        if hasattr(self, "top_frame"):
            self.top_frame.layout().setContentsMargins(*( (6, 0, 6, 0) if compact else (16, 8, 16, 8) ))
            self.top_frame.setMaximumHeight(36 if compact else 16777215)
        if hasattr(self, "settings_button"):
            self.settings_button.setMaximumHeight(28 if compact else 16777215)
        if hasattr(self, "tx_toggle"):
            self.tx_toggle.setMaximumHeight(28 if compact else 16777215)
        self.status_label.setVisible(not compact)
        self.tx_activity.setVisible(not compact)
        self.settings_button.setText("⚙" if compact else "⚙  Settings")
        self.settings_button.setToolTip("Station settings")
        self.footer_label.setVisible(not compact)
        self.mode_label.setVisible(not compact)
        for pane, headers in zip(self.panes, self.card_headers):
            pane.layout().setContentsMargins(*( (8, 8, 8, 8) if compact and pane is self.panes[1] else
                                                (10, 10, 10, 10) if compact else
                                                (14, 14, 14, 14) if pane is self.panes[0] else
                                                (18, 18, 18, 18) ))
            pane.layout().setSpacing(4 if compact and pane is self.panes[1] else 6 if compact else 8 if pane is self.panes[0] else 13)
            for header in headers:
                header.setVisible(not compact)
        self.session_list.setMinimumHeight(50 if compact else 70)
        self.session_list.setVisible(not compact or self.session_list.count() > 0)
        has_score = bool(self.scoreboard_compact.text())
        short = self.height() < 800
        tight = self.height() < 500
        self.scoreboard_compact.setVisible((compact or short) and has_score and not tight)
        self.scoreboard_host.setVisible(not compact and not short and has_score)
        self._update_vertical_density()

    def _update_vertical_density(self):
        """Give the game surface first claim on height as the window shrinks."""
        if not hasattr(self, "board_scroll"):
            return
        compact = self.layout_stack.currentWidget() is self.compact_tabs
        short = self.height() < 800
        tight = self.height() < 500
        dense = compact or short

        # Collapse the chrome first. The board remains a real viewport even in
        # a short window; oversized game content can scroll inside it.
        self.top_frame.setMaximumHeight(36 if dense else 16777215)
        self.top_frame.layout().setContentsMargins(*( (6, 0, 6, 0) if dense else (16, 8, 16, 8) ))
        self.transport_label.setVisible(not dense)
        self.status_label.setVisible(not dense)
        self.tx_activity.setVisible(not dense)
        self.footer_label.setVisible(not dense)
        self.mode_label.setVisible(not dense)
        self.game_subtitle.setVisible(not dense and (not compact or self.width() >= 520))
        self.game_heading.setStyleSheet(
            "font-size:16px; font-weight:750;" if tight else
            "font-size:20px; font-weight:750;" if dense else ""
        )
        for pane, headers in zip(self.panes, self.card_headers):
            for header in headers:
                header.setVisible(not dense)
        center_margins = (4, 4, 4, 4) if tight else (8, 8, 8, 8) if dense else (18, 18, 18, 18)
        self.panes[1].layout().setContentsMargins(*center_margins)
        self.panes[1].layout().setSpacing(3 if dense else 13)
        self.board_scroll.setMinimumHeight(155 if dense else 220)
        self._fit_naval_grid_cells()

        has_score = bool(self.scoreboard_compact.text())
        self.scoreboard_host.setVisible(has_score and not compact and not short)
        self.scoreboard_compact.setVisible(has_score and (compact or short))

    def _fit_naval_grid_cells(self):
        if not hasattr(self, "board_scroll"):
            return
        cell_size = max(18, min(28, (self.board_scroll.viewport().height() - 110) // 11))
        grids = set()
        for button in self.board_scroll.findChildren(QPushButton):
            if button.property("adaptiveRadarCell"):
                button.setFixedSize(cell_size, cell_size)
                grids.add(button.parentWidget())
        for label in self.board_scroll.findChildren(QLabel):
            if label.property("radarColumn"):
                label.setFixedSize(cell_size, max(12, cell_size - 8))
            elif label.property("radarRow"):
                label.setFixedSize(max(12, cell_size - 8), cell_size)
        for board in grids:
            if board.layout():
                board.layout().invalidate()
                board.layout().activate()
            board.adjustSize()

    @staticmethod
    def _card(kicker: str, title: str) -> QFrame:
        frame = QFrame(); frame.setObjectName("card")
        box = QVBoxLayout(frame); box.setContentsMargins(18, 18, 18, 18); box.setSpacing(13)
        label = QLabel(kicker); label.setObjectName("eyebrow"); box.addWidget(label)
        heading = QLabel(title); heading.setObjectName("cardTitle"); box.addWidget(heading)
        return frame

    def _update_new_game_mode(self, *_args):
        practice = self.settings.transport == "practice"
        self.hosted_checkbox.setVisible(self.settings.transport == "broadcast")
        hosted = self.hosted_checkbox.isChecked() and not practice
        self.peer_input.setVisible(not practice and not hosted)
        self.create_hint.setText(
            "LOCAL PRACTICE  ·  Players take turns on this computer. No radio required."
            if practice else
            "OPEN BROADCAST  ·  Act as dealer. Other stations can discover and join."
            if hosted else
            "DIRECT TABLE  ·  Invite a callsign now or add a station after creating."
        )
        if hasattr(self, "new_button"):
            self.new_button.setText("▶  Start local match" if practice else "✦  Open broadcast table" if hosted else "＋  Create table")

    def _apply_style(self):
        self.setStyleSheet("""
            QWidget#root {
                background: qradialgradient(cx:0.5, cy:0, radius: 1.2, fx:0.5, fy:0, stop:0 #141f32, stop:0.45 #0b111b, stop:1 #06090e);
                color: #f1f5f9;
                font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', system-ui, sans-serif;
                font-size: 13px;
            }
            QFrame#topbarFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #131c2d, stop:1 #0c1421);
                border: 1px solid rgba(100, 160, 240, 0.16);
                border-radius: 14px;
            }
            QFrame#statusPill {
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
            }
            QFrame#card {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #111a28, stop:1 #0b111c);
                border: 1px solid rgba(100, 160, 240, 0.14);
                border-radius: 18px;
            }
            QLabel#brand {
                color: #ffffff;
                font-size: 19px;
                font-weight: 800;
                letter-spacing: 1.5px;
            }
            QLabel#eyebrow {
                color: #00f5c4;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1.8px;
            }
            QLabel#cardTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: 700;
                letter-spacing: -0.3px;
            }
            QLabel#gameTitle {
                color: #ffffff;
                font-size: 28px;
                font-weight: 800;
                letter-spacing: -0.5px;
            }
            QLabel#muted {
                color: #8292a8;
                font-size: 12px;
            }
            QLabel#statusText {
                color: #cbd5e1;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#statusDot {
                font-size: 14px;
                font-weight: 800;
            }
            QLabel#turnBadge {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0f2b26, stop:1 #081d1a);
                color: #00f5c4;
                border: 1px solid rgba(0, 245, 196, 0.35);
                border-radius: 10px;
                padding: 10px 14px;
                font-weight: 800;
                font-size: 12px;
                letter-spacing: 0.5px;
            }
            QLabel#turnBadge[turn="mine"] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #043d34, stop:1 #065548);
                color: #00f5c4;
                border: 1px solid #00f5c4;
            }
            QLabel#turnBadge[turn="waiting"] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3d2305, stop:1 #4d2d07);
                color: #fbbf24;
                border: 1px solid #f59e0b;
            }
            QLabel#turnBadge[turn="alert"] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #420b16, stop:1 #520f1d);
                color: #fb7185;
                border: 1px solid #f43f5e;
            }
            QLabel#turnBadge[turn="finished"] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #251247, stop:1 #32185e);
                color: #c084fc;
                border: 1px solid #a855f7;
            }
            QLabel#players {
                background: #090f18;
                color: #cbd5e1;
                border: 1px solid #1a283a;
                border-radius: 12px;
                padding: 12px 14px;
                font-family: monospace, -apple-system, sans-serif;
                font-size: 12px;
                line-height: 1.6;
            }
            QLabel#compactScoreboard {
                background: #0c1825;
                color: #7dd3fc;
                border: 1px solid rgba(56, 189, 248, 0.25);
                border-radius: 8px;
                padding: 7px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel { color: #d7dfeb; }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a2638, stop:1 #131c2a);
                color: #e2e8f0;
                border: 1px solid rgba(100, 160, 240, 0.2);
                border-radius: 9px;
                padding: 8px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #23334a, stop:1 #192537);
                border-color: rgba(0, 245, 196, 0.45);
                color: #ffffff;
            }
            QPushButton:pressed {
                background: #0e1520;
                border-color: #00f5c4;
            }
            QPushButton#forfeitGame {
                color: #fca5a5;
                background: rgba(239, 68, 68, 0.1);
                border: 1px solid rgba(239, 68, 68, 0.32);
            }
            QPushButton#forfeitGame:hover {
                background: rgba(239, 68, 68, 0.22);
                border-color: #f87171;
                color: #ffffff;
            }
            QPushButton#txToggle {
                background: #141d2a;
                color: #64748b;
                border: 1px solid #233145;
                border-radius: 9px;
                padding: 6px 14px;
                font-weight: 700;
            }
            QPushButton#txToggle:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #10b981);
                color: #ffffff;
                border: 1px solid #34d399;
                font-weight: 800;
            }
            QPushButton:disabled {
                color: #475569;
                background: #0e141f;
                border-color: #182230;
            }
            QPushButton#primaryButton {
                color: #02201d;
                font-weight: 800;
                font-size: 13px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00f5c4, stop:1 #06d6a0);
                border: none;
                border-radius: 10px;
                padding: 9px 18px;
            }
            QPushButton#primaryButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38ffd9, stop:1 #22e6b2);
                color: #011412;
            }
            QPushButton#primaryButton:pressed {
                background: #00d9ae;
            }
            QLineEdit, QComboBox, QSpinBox {
                background: #090f18;
                color: #f1f5f9;
                border: 1px solid #1f2f44;
                border-radius: 8px;
                padding: 8px 10px;
                selection-background-color: #00f5c4;
                selection-color: #02201d;
                font-size: 12px;
            }
            QLineEdit:hover, QComboBox:hover, QSpinBox:hover {
                border-color: rgba(100, 160, 240, 0.35);
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 1px solid #00f5c4;
                background: #0c1420;
            }
            QComboBox QAbstractItemView {
                background: #0f1724;
                color: #f1f5f9;
                border: 1px solid #23344a;
                border-radius: 8px;
                padding: 4px;
                selection-background-color: #173248;
                selection-color: #00f5c4;
            }
            QListWidget { background:transparent; border:0; outline:0; }
            QScrollArea#toolsScroll, QWidget#toolsViewport, QScrollArea#boardScroll, QWidget#boardViewport,
            QWidget#toolsContent, QWidget#boardHost { background:transparent; border:0; }
            QWidget#scorchedHost, QWidget#scorchedMapColumn,
            QWidget#scorchedViewport, QWidget#scorchedControlsViewport,
            QScrollArea#battlefieldScroll, QScrollArea#scorchedControlsScroll { background:#090f18; border:0; }
            QWidget#scorchedControls { background:#0c1522; border:1px solid rgba(100,160,240,0.14); border-radius:14px; }
            QSlider#scorchedAngle::groove:horizontal, QSlider#scorchedPower::groove:horizontal {
                height: 6px;
                background: #060b13;
                border: 1px solid #1e2c3e;
                border-radius: 3px;
            }
            QSlider#scorchedAngle::sub-page:horizontal, QSlider#scorchedPower::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #00f5c4);
                border-radius: 3px;
            }
            QSlider#scorchedAngle::handle:horizontal, QSlider#scorchedPower::handle:horizontal {
                background: #ffffff;
                border: 2px solid #00f5c4;
                width: 14px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 7px;
            }
            QSlider#scorchedAngle::handle:horizontal:hover, QSlider#scorchedPower::handle:horizontal:hover {
                background: #00f5c4;
                border: 2px solid #ffffff;
            }
            QPushButton#scorchedFire {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e11d48, stop:1 #f43f5e);
                color: #ffffff;
                font-size: 13px;
                font-weight: 800;
                border: 1px solid #fda4af;
                border-radius: 10px;
                padding: 10px 14px;
                letter-spacing: 0.5px;
            }
            QPushButton#scorchedFire:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f43f5e, stop:1 #fb7185);
                border-color: #ffffff;
            }
            QPushButton#scorchedFire:disabled {
                background: #18121a;
                color: #4b3644;
                border-color: #271a23;
            }
            QPushButton#scorchedReplay {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #132235, stop:1 #1e334d);
                color: #38bdf8;
                border: 1px solid rgba(56, 189, 248, 0.4);
                border-radius: 8px;
                font-weight: 700;
                padding: 7px 12px;
            }
            QPushButton#scorchedReplay:hover {
                background: #38bdf8;
                color: #0c1825;
            }
            QListWidget::item { padding:10px 10px; margin:2px 0; border-radius:9px; color:#abb8ca; }
            QListWidget::item:selected { background:#162c3e; color:#7dd3fc; }
            QListWidget::item:hover { background:#141f2e; }
            QListWidget#sessionList::item { padding:0; margin:4px 0; border-radius:11px; border:1px solid transparent; background:rgba(255,255,255,0.02); }
            QListWidget#sessionList::item:hover { background:rgba(255,255,255,0.045); border:1px solid rgba(100,160,240,0.2); }
            QListWidget#sessionList::item:selected { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #142a38,stop:1 #0f202c); border:1px solid rgba(0,245,196,0.38); border-left:3px solid #00f5c4; }
            QWidget#sessionCardWidget { background:transparent; }
            QLabel#sessionGlyphIcon { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #18283a,stop:1 #111d2c); border:1px solid rgba(0,245,196,0.25); border-radius:9px; color:#00f5c4; font-size:16px; font-weight:bold; }
            QLabel#sessionGameTitle { color:#ffffff; font-size:13px; font-weight:750; }
            QLabel#sessionPlayersLine { color:#94a3b8; font-size:11px; }
            QLabel#sessionActivityLine { color:#64748b; font-size:10px; }
            QLabel#sessionStatusBadge { color:#022c22; background:#2dd4bf; border-radius:5px; padding:2px 7px; font-size:9px; font-weight:800; letter-spacing:0.5px; }
            QLabel#sessionStatusBadge[kind="waiting"] { color:#451a03; background:#fbbf24; }
            QLabel#sessionStatusBadge[kind="resync"] { color:#4c0519; background:#fb7185; }
            QLabel#sessionStatusBadge[kind="neutral"] { color:#cbd5e1; background:#1e293b; }
            QListWidget#chatList { background:#080d16; border:1px solid #172436; border-radius:12px; outline:none; }
            QListWidget#chatList::item { padding:4px; margin:2px 0; background:transparent; border:none; color:transparent; }
            QTabWidget#compactTabs::pane { border:0; }
            QTabWidget#compactTabs QTabBar::tab { background:#141e2c; color:#94a3b8; padding:9px 14px; min-height:22px; border:1px solid #23344a; border-radius:8px; margin:2px; font-weight:600; }
            QTabWidget#compactTabs QTabBar::tab:selected { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #043d34,stop:1 #065548); color:#00f5c4; border-color:#00f5c4; font-weight:800; }
            QSplitter::handle:horizontal { width:5px; background:transparent; }
            QSplitter::handle:horizontal:hover { background:rgba(0,245,196,0.5); border-radius:2px; }
            QScrollBar:vertical { background:transparent; width:7px; margin:2px; }
            QScrollBar::handle:vertical { background:#1e2c3e; border-radius:3px; min-height:25px; }
            QScrollBar::handle:vertical:hover { background:#00f5c4; }
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
            QScrollBar:horizontal { background:transparent; height:7px; margin:2px; }
            QScrollBar::handle:horizontal { background:#1e2c3e; border-radius:3px; min-width:25px; }
            QScrollBar::handle:horizontal:hover { background:#00f5c4; }
            QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal { height:0; }
            QDialog { background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #121c2d,stop:1 #0a111c); }
            QDialog#settingsDialog { background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #121c2d,stop:1 #0a111c); color:#f1f5f9; border:1px solid rgba(100,160,240,0.2); border-radius:18px; }
            QDialog#settingsDialog QScrollArea#settingsFormScroll,
            QDialog#settingsDialog QWidget#settingsFormHost,
            QDialog#settingsDialog QScrollArea#settingsFormScroll QWidget#qt_scrollarea_viewport { background:transparent; border:0; }
            QDialog#settingsDialog QLabel { background:transparent; color:#e2e8f0; }
            QDialog#settingsDialog QLabel#muted { color:#94a3b8; }
            QDialog#settingsDialog QLineEdit, QDialog#settingsDialog QComboBox,
            QDialog#settingsDialog QSpinBox { min-height:34px; background:#090f18; color:#f1f5f9; border:1px solid #1f2f44; border-radius:7px; padding:6px 9px; selection-background-color:#00f5c4; selection-color:#02201d; }
            QDialog#settingsDialog QComboBox QAbstractItemView { background:#0f1724; color:#f1f5f9; selection-background-color:#173248; selection-color:#00f5c4; }
            QDialog#settingsDialog QCheckBox { color:#d7e0ec; spacing:8px; }
            QDialog#settingsDialog QCheckBox::indicator { width:18px; height:18px; }
            QLabel#dialogTitle { font-size:23px; font-weight:800; color:#ffffff; letter-spacing:-0.3px; }
            QLabel#chatLine { background:#131c2a; border-radius:8px; padding:8px 12px; }
            QLabel#modeLabel { background:rgba(255,255,255,0.03); border:1px solid #1e2c40; border-radius:6px; padding:3px 8px; color:#94a3b8; font-size:10px; font-weight:600; }
        """)
        if self.settings.color_scheme == "daylight":
            self.setStyleSheet(self.styleSheet() + """
                QWidget#root { background:#eaf0f4; color:#17212d; }
                QDialog { background:#ffffff; color:#17212d; }
                QDialog#settingsDialog { background:#f5f8fb; color:#17212d; }
                QDialog#settingsDialog QScrollArea#settingsFormScroll,
                QDialog#settingsDialog QWidget#settingsFormHost,
                QDialog#settingsDialog QScrollArea#settingsFormScroll QWidget#qt_scrollarea_viewport { background:#f5f8fb; border:0; }
                QDialog#settingsDialog QLabel { color:#243649; }
                QDialog#settingsDialog QLabel#muted { color:#52677b; }
                QDialog#settingsDialog QLineEdit, QDialog#settingsDialog QComboBox,
                QDialog#settingsDialog QSpinBox { background:#ffffff; color:#172431; border-color:#71859a; selection-background-color:#176f63; selection-color:#ffffff; }
                QDialog#settingsDialog QComboBox QAbstractItemView { background:#ffffff; color:#172431; selection-background-color:#176f63; selection-color:#ffffff; }
                QDialog#settingsDialog QCheckBox { color:#243649; }
                QLabel#dialogTitle { color:#1c2936; }
                QLabel#muted { color:#52677b; }
                QFrame#card { background:#ffffff; border-color:#c9d4de; }
                QLabel#brand,QLabel#gameTitle,QLabel#cardTitle,QLabel { color:#1b2936; }
                QLabel#eyebrow { color:#087d70; }
                QLabel#muted,QLabel#statusText,QLabel#modeLabel { color:#5f7080; }
                QLabel#turnBadge { background:#e0f4ef; color:#08695f; border-color:#abd9cf; }
                QLabel#players,QListWidget#chatList { background:#f2f6f8; color:#27394a; }
                QPushButton { background:#f2f5f7; color:#223243; border-color:#c4d1dc; }
                QPushButton:hover { background:#e3edf1; }
                QLineEdit,QComboBox,QSpinBox { background:#ffffff; color:#1c2a38; border-color:#c3d0da; }
                QListWidget::item { color:#33485c; }
                QListWidget::item:selected { background:#d7eee9; color:#075f57; }
                QListWidget#sessionList::item:selected { background:#dcefeb; border:1px solid #76a99e; }
                QLabel#sessionGameTitle { color:#1b2936; }
                QLabel#sessionPlayersLine { color:#33485c; }
                QLabel#sessionActivityLine { color:#52677b; }
                QLabel#sessionStatusBadge { color:#0b5d49; background:#d9f0e6; }
                QLabel#sessionStatusBadge[kind="waiting"] { color:#805312; background:#faedcc; }
                QLabel#sessionStatusBadge[kind="resync"] { color:#713d00; background:#ffe6b3; }
                QLabel#sessionStatusBadge[kind="neutral"] { color:#415268; background:#e5ebf0; }
                QLabel#chatLine { background:#edf3f6; }
            """)
        elif self.settings.color_scheme == "monochrome":
            self.setStyleSheet(self.styleSheet() + """
                QWidget#root, QFrame#card { background:#111111; color:#eeeeee; border-color:#777777; }
                QLabel, QLabel#brand, QLabel#gameTitle, QLabel#cardTitle, QLabel#eyebrow,
                QLabel#turnBadge, QLabel#players { color:#eeeeee; background:#222222; border-color:#888888; }
                QPushButton, QPushButton#primaryButton { background:#333333; color:#ffffff; border:1px solid #aaaaaa; }
                QPushButton:disabled { background:#222222; color:#777777; }
                QLineEdit, QComboBox, QSpinBox { background:#111111; color:#ffffff; border-color:#888888; }
                QListWidget::item:selected { background:#555555; color:#ffffff; }
            """)

    def _update_status(self, text: str, connected: bool):
        self.status_label.setText(text)
        self.link_dot.setToolTip(text)
        color = "#eeeeee" if self.settings.color_scheme == "monochrome" else "#61dfb8" if connected else "#e9ae65"
        self.link_dot.setStyleSheet(f"color: {color}; font-size:17px")

    def connect_service(self):
        if self.unsubscribe:
            self.unsubscribe(); self.unsubscribe = None
        if self.engine:
            self.engine.close(); self.engine = None
        self._update_status("Connecting…", False)
        self.service_worker = ServiceWorker(self.settings, self)
        self.service_worker.ready.connect(self._service_ready)
        self.service_worker.failed.connect(self._connection_failed)
        self.service_worker.start()

    @Slot(object)
    def _service_ready(self, engine: ArcadeService):
        self.engine = engine
        engine.set_tx_enabled(self.settings.tx_enabled if self.settings.transport != "practice" else True)
        self._board_signature = None
        self.unsubscribe = engine.subscribe(lambda event: self._event_bridge.received.emit(event))
        offline = hasattr(engine.transport, "closed") and engine.transport.closed.is_set()
        status_text = ("Offline · reconnect in settings" if offline else "Local practice"
                       if self.settings.transport == "practice" else "Data2G ready")
        if not offline and self.settings.transport != "practice":
            status_text += " · CWID" if engine.cwid_active else " · app ID"
            if engine.identification_warning:
                status_text += " (CWID unavailable)"
        self._update_status(status_text, not offline)
        self.mode_label.setText(f"{self.settings.callsign}  ·  {'OBSERVER · RX' if self.settings.observer_mode else self.settings.transport.title()}")
        can_transmit = not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled)
        for control in (self.game_choice, self.hosted_checkbox, self.peer_input, self.new_button,
                        self.cq_choice, self.cq_send, self.cq_answer,
                        self.invite_button, self.cancel_invite_button, self.start_button, self.chat_input,
                        self.chat_send, self.join_button, self.announce_button, self.order_input,
                        self.order_button, self.retry_button, self.connect_button,
                        self.disconnect_button):
            control.setEnabled(can_transmit)
        self.hosted_checkbox.setEnabled(can_transmit and self.settings.transport == "broadcast")
        self._update_cq_visibility()
        for control in (self.cq_choice, self.cq_send, self.cq_answer):
            control.setEnabled(can_transmit and self.settings.transport == "broadcast")
        if self.settings.transport != "broadcast":
            self.hosted_checkbox.setChecked(False)
        self._update_new_game_mode()
        self.spot_button.setEnabled(not self.settings.observer_mode)
        self.sync_button.setEnabled(self.settings.transport == "practice" or self.settings.tx_enabled)
        self.connect_button.setVisible(self.settings.transport == "connected")
        self.disconnect_button.setVisible(self.settings.transport == "connected")
        self._populate_games()
        self._refresh_cq()
        self.tx_toggle.setEnabled(self.settings.transport != "practice")
        self._refresh_sessions()

    @Slot(bool)
    def _set_tx_enabled(self, enabled: bool):
        if self.settings.transport == "practice":
            enabled = True
        self.settings.tx_enabled = enabled
        self.settings.save()
        self.tx_toggle.setText("TX ENABLED" if enabled else "TX DISABLED")
        can_transmit = not self.settings.observer_mode and (self.settings.transport == "practice" or enabled)
        for control in (self.game_choice, self.hosted_checkbox, self.peer_input, self.new_button,
                        self.cq_choice, self.cq_send, self.cq_answer,
                        self.invite_button, self.cancel_invite_button, self.start_button, self.chat_input,
                        self.chat_send, self.join_button, self.announce_button, self.order_input,
                        self.order_button, self.retry_button, self.connect_button, self.disconnect_button):
            control.setEnabled(can_transmit)
        self.sync_button.setEnabled(self.settings.transport == "practice" or enabled)
        self.spot_button.setEnabled(not self.settings.observer_mode)
        if self.engine:
            self.engine.set_tx_enabled(enabled)
            self._show_session(self.selected_id)

    def _forfeit_game(self):
        if not self.engine or not self.selected_id:
            return
        sid = self.selected_id
        answer = QMessageBox.question(self, "Forfeit game", "Forfeit this game? This ends the table and awards the result to the other player(s).")
        if answer == QMessageBox.StandardButton.Yes:
            self._run(partial(self.engine.forfeit_game, sid))

    def _delete_game(self):
        if not self.engine or not self.selected_id:
            return
        answer = QMessageBox.question(self, "Delete game", "Delete this game's saved state, chat, and queued messages from this device? This does not notify other stations.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        sid = self.selected_id
        def deleted(_result):
            self.selected_id = None
            self._preserve_empty_selection = True
        self._run(partial(self.engine.delete_game, sid), deleted)

    def _populate_games(self):
        catalog = (self.engine.catalog() if self.engine
                   else builtin_registry(load_plugins=False).catalog())
        current = self.game_choice.currentData()
        self.game_choice.clear()
        for game in catalog:
            if "desktop" not in game.clients or not self.settings.can_offer_game(game.key):
                continue
            self.game_choice.addItem(f"{game.name} · {game.min_players}–{game.max_players}", game.key)
        index = self.game_choice.findData(current)
        if index >= 0: self.game_choice.setCurrentIndex(index)
        selected_cq_game = self.cq_choice.currentData()
        self.cq_choice.clear()
        self.cq_choice.addItem("Any game", None)
        for game in catalog:
            if "desktop" in game.clients and game.max_players >= 2 and self.settings.can_offer_game(game.key):
                self.cq_choice.addItem(game.name, game.key)
        index = self.cq_choice.findData(selected_cq_game)
        if index >= 0: self.cq_choice.setCurrentIndex(index)

    def _refresh_cq(self):
        if not self.engine:
            return
        selected = self.cq_list.currentItem()
        selected_key = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.cq_list.clear()
        for request in self.engine.cq_requests():
            item = QListWidgetItem(f"{request['callsign']} · {request['game']}")
            item.setData(Qt.ItemDataRole.UserRole, (request["callsign"], request["game_id"]))
            self.cq_list.addItem(item)
            if item.data(Qt.ItemDataRole.UserRole) == selected_key:
                self.cq_list.setCurrentItem(item)

    def _call_cq(self):
        if self.engine:
            self._run(partial(self.engine.call_cq, self.cq_choice.currentData()))

    def _answer_cq(self):
        if not self.engine or not self.cq_list.currentItem():
            return
        callsign, requested_id = self.cq_list.currentItem().data(Qt.ItemDataRole.UserRole)
        game = self.cq_choice.currentData()
        if requested_id != 255:
            game = requested_id
        elif game is None:
            QMessageBox.information(self, "Choose a game", "Choose a specific game above to answer this CQ.")
            return
        game_key = self.engine.registry.by_id(game).key if isinstance(game, int) else game
        if not self.settings.can_offer_game(game_key):
            QMessageBox.information(self, "Experimental games", "Enable Experimental games in settings to answer this CQ.")
            return
        self._run(partial(self.engine.answer_cq, callsign, game), self._created_game)

    @Slot(str)
    def _connection_failed(self, error: str):
        self._update_status("Offline", False)
        self.mode_label.setText(error)

    def _open_notification(self):
        if self._notification_cq:
            self._notification_cq = False
            self._refresh_cq()
            self.showNormal(); self.raise_(); self.activateWindow()
            if self.layout_stack.currentWidget() is self.compact_tabs:
                self.compact_tabs.setCurrentIndex(0)
            self.notification_button.hide()
            return
        if self._notification_session:
            self.selected_id = self._notification_session
            self._refresh_sessions()
            self.showNormal(); self.raise_(); self.activateWindow()
            self.notification_button.hide()

    def _notify_activity(self, title, body, sid):
        self._notification_cq = False
        self._notification_session = sid
        self.notification_button.setText(f"{title} · {body} · Open table")
        self.notification_button.show()
        QApplication.alert(self, 5000)
        if self._tray.isVisible():
            self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 6000)

    def _on_event(self, event: EngineEvent):
        if event.name == "cq_request":
            self._refresh_cq()
            self._notification_cq = True
            self.notification_button.setText(f"CQ · {event.data['callsign']} · {event.data['game']} · View requests")
            self.notification_button.show()
            QApplication.alert(self, 5000)
            if self._tray.isVisible():
                self._tray.showMessage("Game CQ", f"{event.data['callsign']} · {event.data['game']}",
                                       QSystemTrayIcon.MessageIcon.Information, 6000)
        elif event.name == "invitation":
            self._notify_activity("Game invitation", f"{event.data.get('sender')} · {event.data.get('game')}", event.session)
        elif event.name == "chat_message" and event.data.get("message", {}).get("sender") != self.settings.callsign:
            message = event.data["message"]
            chat_visible = (self.layout_stack.currentWidget() is self.desktop_splitter or
                            (self.layout_stack.currentWidget() is self.compact_tabs and
                             self.compact_tabs.currentWidget() is self.panes[2]))
            if event.session and (event.session != self.selected_id or not chat_visible):
                self._unread_by_session[event.session] = self._unread_by_session.get(event.session, 0) + 1
                self._persist_desktop_state()
            self._notify_activity("New message", f"{message['sender']}: {message['body']}", event.session)
            if self.settings.sound_enabled:
                QApplication.beep()
        if event.name == "transmitting":
            self.tx_activity.setText(f"TX · {event.data.get('kind')} → {event.data.get('destination')} · {event.data.get('reason')}")
            QTimer.singleShot(6000, lambda: self.tx_activity.setText(""))
        elif event.name == "tx_state":
            self.tx_toggle.blockSignals(True)
            self.tx_toggle.setChecked(bool(event.data.get("enabled")))
            self.tx_toggle.setText("TX ENABLED" if event.data.get("enabled") else "TX DISABLED")
            self.tx_toggle.blockSignals(False)
        if self.settings.sound_enabled and (event.name == "invitation" or
                (event.name == "session_updated" and event.data.get("committed") and
                 event.data["session"].get("current_player") == self.settings.callsign)):
            QApplication.beep()
        if event.name == "transport_status":
            status = event.data.get("status")
            self._update_status(f"Connected to {event.data.get('peer')}" if status == "connected" else "Offline · reconnect in settings" if status == "offline" else "Listening / idle", status == "connected")
        elif event.name == "chat_message":
            self._refresh_chat(event.session)
        elif event.name == "chat_delivery":
            self._refresh_chat(event.session)
        elif event.name == "opaque_state":
            self.footer_label.setText(f"Opaque game state synchronized from {event.data['sender']} · content hidden")
        elif event.name == "delivery" and event.data.get("state") == "unconfirmed":
            self.footer_label.setText("No acknowledgement yet · waiting for the other station")
        elif event.name == "protocol_error":
            self.footer_label.setText(f"Ignored malformed frame · {event.data.get('error','')}")
        elif event.name == "unsupported_game":
            self.footer_label.setText(f"Unsupported game ID {event.data['game_id']} from {event.data['sender']}")
        if event.name in ("session_updated", "invitation", "delivery", "session_deleted", "chat_message"):
            self._refresh_sessions()
            if event.session and event.session == self.selected_id:
                self._show_session(event.session)

    def _refresh_sessions(self):
        if not self.engine:
            return
        selected = self.selected_id
        scroll_position = self.session_list.verticalScrollBar().value()
        self.session_list.blockSignals(True); self.session_list.clear()
        supported = {game.game_id for game in self.engine.catalog() if "desktop" in game.clients}
        all_sessions = [s for s in self.engine.sessions() if s["game_id"] in supported]
        query = self.session_search.text().strip().casefold()
        filter_mode = self.session_filter.currentData()
        if not self._table_list_initialized:
            self.new_game_toggle.setChecked(not bool(all_sessions))
            self._toggle_new_game_setup(self.new_game_toggle.isChecked())
            self._table_list_initialized = True
        sessions = []
        for session in all_sessions:
            pending = session.get("sync_requested_at", 0) > session.get("sync_at", 0)
            needs_sync = pending or any(word in (session.get("error") or "").lower() for word in ("missed moves", "state conflict", "awaiting snapshot"))
            practice_turn = self.settings.transport == "practice" and session["status"] == "active"
            own_turn = practice_turn or session.get("current_player") == self.settings.callsign
            last = session.get("last_move") or {}
            other = next((p for p in session["players"] if p != self.settings.callsign), session["coordinator"])
            searchable = " ".join((session["game"], other, session["id"], session["status"],
                                   last.get("actor", ""), last.get("description", ""))).casefold()
            if query and query not in searchable: continue
            if filter_mode == "your-turn" and not (session["status"] == "active" and own_turn): continue
            if filter_mode == "waiting" and not (session["status"] == "active" and not own_turn and not needs_sync and not session["pending_action"]): continue
            if filter_mode == "resync" and not needs_sync: continue
            if filter_mode == "unread" and not self._unread_by_session.get(session["id"], 0): continue
            sessions.append(session)
        def priority(s):
            pending = s.get("sync_requested_at", 0) > s.get("sync_at", 0)
            if s["status"] == "invited": return 0
            if pending: return 1
            if s["status"] == "active" and (self.settings.transport == "practice" or s.get("current_player") == self.settings.callsign): return 2
            if s["status"] == "active": return 3
            return 4
        sessions.sort(key=lambda s: (priority(s), -s.get("updated", 0)))
        for session in sessions:
            opponent = (" / ".join(session["players"]) or session["coordinator"]) if self.settings.observer_mode else next((p for p in session["players"] if p != self.settings.callsign), "Open lobby")
            pending = session.get("sync_requested_at", 0) > session.get("sync_at", 0)
            needs_sync = pending or any(word in (session.get("error") or "").lower() for word in ("missed moves", "state conflict", "awaiting snapshot"))
            own_turn = session["status"] == "active" and not self.settings.observer_mode and (self.settings.transport == "practice" or session.get("current_player") == self.settings.callsign)
            turn = "resync requested" if pending else "needs resync" if needs_sync else "your move" if own_turn else "waiting" if session["status"] == "active" else session["status"]
            last = session.get("last_move")
            activity = f"Last: {last['actor']} {last['description']}" if last else "No moves yet"
            unread = self._unread_by_session.get(session["id"], 0)
            status_kind = "resync" if needs_sync else "active" if own_turn else "waiting" if session["status"] == "active" or session["status"] in ("invited", "joining") else "neutral"
            activity_text = f"Last move · {last['actor']} {last['description']}" if last else "No moves yet"
            item = QListWidgetItem()
            card = SessionListCard(session["game"], turn, opponent, len(session["players"]),
                                   activity_text, unread, status_kind)
            item.setSizeHint(card.sizeHint())
            item.setData(Qt.ItemDataRole.AccessibleTextRole,
                         f"{session['game']}. {opponent}. {turn}. {activity_text}. {unread} unread messages.")
            item.setToolTip(card.toolTip())
            item.setData(Qt.ItemDataRole.UserRole, session["id"])
            self.session_list.addItem(item)
            self.session_list.setItemWidget(item, card)
            if session["id"] == selected:
                self.session_list.setCurrentItem(item)
        self.session_list.blockSignals(False)
        if not sessions:
            self.session_list.addItem(QListWidgetItem("No tables match this search or filter." if all_sessions else "No tables yet. Start a game or accept an invitation."))
        self.delete_button.setEnabled(bool(self.engine and self.engine.sessions()))
        current_item = self.session_list.currentItem()
        current_id = current_item.data(Qt.ItemDataRole.UserRole) if current_item else None
        if current_id:
            self.selected_id = current_id
        elif selected and any(session["id"] == selected for session in all_sessions):
            self.selected_id = selected
        elif sessions and not self._preserve_empty_selection:
            self.session_list.blockSignals(True)
            first = next((index for index in range(self.session_list.count())
                          if self.session_list.item(index).data(Qt.ItemDataRole.UserRole)), -1)
            if first >= 0: self.session_list.setCurrentRow(first)
            self.session_list.blockSignals(False)
            current_item = self.session_list.currentItem()
            self.selected_id = current_item.data(Qt.ItemDataRole.UserRole) if current_item else None
        else:
            self.selected_id = None
        self._persist_desktop_state()
        self.session_list.verticalScrollBar().setValue(scroll_position)
        self.session_list.setVisible(self.layout_stack.currentWidget() is not self.compact_tabs or
                                     self.session_list.count() > 0)
        self._show_session(self.selected_id)

    def _select_item(self, current, _previous):
        if current and not current.data(Qt.ItemDataRole.UserRole):
            return
        if current:
            self._preserve_empty_selection = False
        self.selected_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        if self.selected_id:
            self._unread_by_session.pop(self.selected_id, None)
        self._persist_desktop_state()
        self._refresh_sessions()
        self._show_session(self.selected_id)
        if current and self.layout_stack.currentWidget() is self.compact_tabs:
            self.compact_tabs.setCurrentIndex(1)

    def _toggle_new_game_setup(self, expanded: bool):
        self.tools_scroll.setVisible(expanded)
        self.new_game_toggle.setText("−  Hide game setup" if expanded else "＋  Start a new game")

    def _toggle_cq_panel(self, expanded: bool):
        available = self.settings.transport == "broadcast" and not self.settings.observer_mode
        self.cq_scroll.setVisible(expanded and available)
        self.cq_toggle.setText("−  Hide game calls" if expanded else "⌁  Find a game · Call CQ")

    def _update_cq_visibility(self):
        available = self.settings.transport == "broadcast" and not self.settings.observer_mode
        self.cq_toggle.setVisible(available)
        self.cq_scroll.setVisible(available and self.cq_toggle.isChecked())

    def _persist_desktop_state(self):
        self._ui_settings.setValue(f"selectedTable/{self.settings.callsign}", self.selected_id or "")
        self._ui_settings.setValue(f"unreadTables/{self.settings.callsign}", self._unread_by_session)

    def _on_compact_tab_changed(self, index: int):
        self._size_chat_rows()
        if self.layout_stack.currentWidget() is self.compact_tabs and self.compact_tabs.widget(index) is self.panes[2] and self.selected_id:
            self._unread_by_session.pop(self.selected_id, None)
            self._persist_desktop_state()
            self._refresh_sessions()

    def _show_session(self, sid: str | None):
        session = None
        if self.engine and sid:
            try: session = self.engine.session(sid)
            except KeyError: return
            pending_sync = session.get("sync_requested_at", 0) > session.get("sync_at", 0)
            needs_sync = pending_sync or any(word in (session.get("error") or "").lower() for word in ("missed moves", "state conflict", "awaiting snapshot"))
            session["resync_pending"] = needs_sync
            signature = (sid, session["status"], session["revision"], session["sequence"],
                         session["pending_action"], tuple(session["players"]), tuple(session["invited"]),
                         repr(session["view"]), repr(session.get("last_move")), session.get("error"), pending_sync, self.settings.transport, self.settings.tx_enabled)
            if signature == self._board_signature:
                return
            self._board_signature = signature
        else:
            self._board_signature = None
        while self.scoreboard_layout.count():
            self.scoreboard_layout.takeAt(0).widget().deleteLater()
        score_visible = bool(session and session.get("view"))
        compact = self.layout_stack.currentWidget() is self.compact_tabs
        short = self.height() < 800
        tight = self.height() < 500
        self.scoreboard_host.setVisible(score_visible and not compact and not short)
        self.scoreboard_compact.setVisible(score_visible and (compact or short))
        if score_visible:
            data = scoreboard(session)
            self.scoreboard_compact.setText("  ·  ".join(
                f"{entry['player']}: {entry['value']} {entry['unit']}" for entry in data["entries"]))
        else:
            self.scoreboard_compact.clear()
        self.result_button.setVisible(bool(session and session["status"] == "finished"))
        if session and session.get("view"):
            self.scoreboard_layout.addWidget(Scoreboard(session))
        announce = self._completion_tracker.observe(session) if session else False
        self._clear_board()
        self.forfeit_button.setVisible(bool(session and session["status"] == "active" and not self.settings.observer_mode))
        self.forfeit_button.setEnabled(bool(session and session["pending_action"] is None and
            (self.settings.transport == "practice" or self.settings.tx_enabled)))
        self.delete_button.setEnabled(bool(session))
        self.delete_button.setVisible(bool(session))
        self.accept_button.setVisible(False); self.start_button.setVisible(False); self.invite_button.setVisible(False); self.cancel_invite_button.setVisible(False)
        self.join_button.setVisible(False); self.announce_button.setVisible(False); self.retry_button.setVisible(False)
        self.spot_button.setVisible(False)
        self.order_input.setVisible(False); self.order_button.setVisible(False)
        self.chat_list.clear()
        if not self.engine or not sid:
            self.delete_button.setEnabled(False)
            self.game_heading.setText("Welcome to the airwaves")
            self.game_subtitle.setText("Start a game or accept an invitation to take a seat.")
            self.turn_badge.setText("NO GAME SELECTED")
            self.turn_badge.setProperty("turn", "neutral")
            self.turn_badge.style().unpolish(self.turn_badge)
            self.turn_badge.style().polish(self.turn_badge)
            self.players_label.setText("Stations appear here when a lobby opens.")
            self.board_layout.addWidget(HeroWelcomeWidget(parent=self.board_host))
            return
        game = session["game"]; players = session["players"]
        can_transmit = not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled)
        for control in (self.new_button, self.peer_input, self.invite_button, self.cancel_invite_button,
                        self.start_button, self.join_button, self.announce_button, self.order_input,
                        self.order_button, self.retry_button, self.accept_button,
                        self.chat_input, self.chat_send, self.connect_button, self.disconnect_button):
            control.setEnabled(can_transmit)
        self.sync_button.setEnabled(self.settings.transport == "practice" or self.settings.tx_enabled)
        self.spot_button.setEnabled(not self.settings.observer_mode)
        self.delete_button.setEnabled(True)
        self.spot_button.setVisible(not self.settings.observer_mode and session.get("hosted", False)
                                    and session["status"] in ("lobby", "active")
                                    and session["coordinator"] == self.settings.callsign)
        self.game_heading.setText(game)
        last = session.get("last_move")
        suffix = f"  ·  last: {last['actor']} {last['description']}" if last else ""
        self.game_subtitle.setText(f"Game {sid}  ·  revision {session['revision']}  ·  {session['status'].title()}{suffix}")
        state = session["view"]
        current = session["current_player"]
        self.sync_button.setVisible(self.settings.observer_mode or session["status"] in ("active", "finished"))
        self.retry_button.setVisible(not self.settings.observer_mode and session.get("hosted", False) and session["pending_action"] is not None)
        self.sync_button.setText(self._sync_label_for_session(session))
        self.sync_button.setToolTip("Resynchronization is always available. Your moves are held until the latest table state arrives." if needs_sync else "Request or send the latest shared table state.")
        self.sync_button.setEnabled((self.settings.transport == "practice" or self.settings.tx_enabled) and not pending_sync)
        turn_kind = "neutral"
        if self.settings.observer_mode:
            self.turn_badge.setText("OBSERVING · RX ONLY" if state else "OBSERVING · AWAITING SNAPSHOT")
        elif session["status"] == "invited":
            self.turn_badge.setText("INVITATION RECEIVED")
            self.accept_button.setVisible(True)
            turn_kind = "waiting"
        elif session["status"] == "lobby":
            turn_kind = "waiting"
            if session.get("hosted"):
                owner = session["coordinator"] == self.settings.callsign
                self.turn_badge.setText("DEALER · WAITING FOR JOIN REQUESTS" if owner else "HOSTED TABLE · JOIN TO PLAY")
                self.announce_button.setVisible(owner)
                self.order_input.setVisible(owner)
                self.order_button.setVisible(owner)
                self.join_button.setVisible(not owner and self.settings.callsign not in players and not self.settings.observer_mode)
                self.start_button.setVisible(owner)
            else:
                self.turn_badge.setText("LOBBY · add players, then start")
                self.invite_button.setVisible(session["coordinator"] == self.settings.callsign)
                self.cancel_invite_button.setVisible(bool(session["invited"]))
                self.start_button.setVisible(session["coordinator"] == self.settings.callsign)
        elif session["status"] == "joining":
            self.turn_badge.setText("JOIN REQUEST SENT · WAIT FOR DEALER" if session.get("hosted") else "WAITING FOR THE HOST TO START")
            self.join_button.setVisible(bool(session.get("hosted")))
            turn_kind = "waiting"
        elif session["status"] == "cancelled":
            self.turn_badge.setText("INVITATION CANCELLED")
            turn_kind = "alert"
        elif session["status"] == "finished":
            winner = state.get("winner") if state else None
            self.turn_badge.setText(f"{session['forfeited_by']} FORFEITED" if session.get("forfeited_by") else f"{winner} WINS" if winner else "GAME OVER · DRAW")
            turn_kind = "finished"
        elif needs_sync:
            self.turn_badge.setText("RESYNC REQUESTED · WAITING FOR TABLE" if pending_sync else "TABLE MAY BE OUT OF DATE · RESYNC BEFORE MOVING")
            turn_kind = "alert"
        elif session["pending_action"]:
            self.turn_badge.setText("MOVE SENT · WAITING FOR COMMIT")
            turn_kind = "waiting"
        else:
            is_my_turn = self.settings.transport == "practice" or current == self.settings.callsign
            self.turn_badge.setText(f"LOCAL PRACTICE · {current}'s turn" if self.settings.transport == "practice" else f"YOUR TURN · {players.index(current)+1} of {len(players)}" if is_my_turn else f"{current or 'Game'} is thinking")
            turn_kind = "mine" if is_my_turn else "waiting"
        if session.get("error"):
            self.turn_badge.setText(f"MOVE REJECTED · {session['error']}")
            turn_kind = "alert"
        self.turn_badge.setProperty("turn", turn_kind)
        self.turn_badge.style().unpolish(self.turn_badge)
        self.turn_badge.style().polish(self.turn_badge)
        player_lines = []
        for p in players:
            active = p == current and session["status"] == "active"
            bullet = "● " if active else "○ "
            role = "  ·  HOST" if p == session["coordinator"] else ""
            my_tag = "  (YOU)" if p == self.settings.callsign else ""
            signal = "  ▂▃▄▅" if p == self.settings.callsign else "  ▂▃▄"
            player_lines.append(f"{bullet}{p:<7}{signal}{role}{my_tag}")
        for p in session["invited"]:
            player_lines.append(f"◌ {p:<7}  ·  invited")
        self.players_label.setText("\n".join(player_lines) if player_lines else "No stations yet")
        if state:
            self._render_state(session)
        self._refresh_chat(sid)
        if announce:
            if state and state.get("kind") == "scorched-earth" and state.get("impact") and not session.get("forfeited_by"):
                self._pending_result_id = sid
                self._completion_timer.start(4500)
            elif state and state.get("kind") == "hexxagon" and not session.get("forfeited_by"):
                self._pending_result_id = sid
                self._completion_timer.start(1100)
            else:
                self._view_result()

    def _sync_label_for_session(self, session: dict) -> str:
        requested = float(session.get("sync_requested_at", 0))
        pending = requested > float(session.get("sync_at", 0))
        timed_out = pending and time.time() - requested >= 30
        if self.width() < 520:
            if pending: return "↻  Retry" if timed_out else "↻  Waiting"
            if session["coordinator"] == self.settings.callsign: return "↻  Send state"
            return "↻  Resync"
        if pending:
            return "↻  Resend resync request" if timed_out else "↻  Resync requested · waiting for table"
        if self.settings.observer_mode: return "↻  Resync table (TRANSMITS)"
        if session["coordinator"] == self.settings.callsign: return "↻  Send latest state to table"
        return "↻  Resync table"

    def _refresh_sync_retry(self):
        if not self.engine or not self.selected_id or not self.sync_button.isVisible():
            return
        try:
            session = self.engine.session(self.selected_id)
        except KeyError:
            return
        requested = float(session.get("sync_requested_at", 0))
        pending = requested > float(session.get("sync_at", 0))
        if pending:
            can_retry = time.time() - requested >= 30
            self.sync_button.setText(self._sync_label_for_session(session))
            self.sync_button.setEnabled(can_retry and (self.settings.transport == "practice" or self.settings.tx_enabled))

    def _finish_scorched_result(self):
        if self.isVisible() and self.selected_id == self._pending_result_id:
            self._view_result()
        self._pending_result_id = None

    def _view_result(self):
        self._completion_timer.stop()
        self._pending_result_id = None
        if not self.engine or not self.selected_id:
            return
        session = self.engine.session(self.selected_id)
        if session["status"] != "finished":
            return
        if self._result_dialog:
            self._result_dialog.close()
        self._result_dialog = ResultDialog(session, self.settings, self)
        self._result_dialog.finished.connect(lambda: setattr(self, "_result_dialog", None))
        self._result_dialog.open()

    def _clear_board(self):
        while self.board_layout.count():
            item = self.board_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def _render_state(self, session: dict):
        view = session["view"]
        self.board_layout.setAlignment(Qt.AlignmentFlag(0) if view["kind"] == "scorched-earth" else Qt.AlignmentFlag.AlignCenter)
        if view["kind"] == "scorched-earth":
            self._render_scorched(session, view)
        elif view["kind"] == "grid":
            board = QWidget(); grid = QGridLayout(board); grid.setSpacing(12)
            grid.setContentsMargins(18, 18, 18, 18)
            board.setStyleSheet("""
                QWidget {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #121c2c, stop:1 #0a111b);
                    border: 1px solid rgba(100, 160, 240, 0.16);
                    border-radius: 20px;
                }
            """)
            my_turn = not self.settings.observer_mode and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active" and not session["pending_action"] and not session.get("resync_pending") and (self.settings.transport == "practice" or self.settings.tx_enabled)
            for cell, value in enumerate(view["cells"]):
                val = value.strip()
                text = "✕" if val == "X" else "○" if val == "O" else str(cell + 1)
                button = QPushButton(text); button.setObjectName(f"boardCell{cell}")
                button.setFixedSize(116, 110); button.setEnabled(my_turn and not val)
                if self.settings.color_scheme == "monochrome":
                    btn_style = "font-size:36px;font-weight:800;color:#ffffff;background:#222;border:2px solid #555;border-radius:14px;"
                elif val == "X":
                    btn_style = ("QPushButton { font-size:54px;font-weight:900;color:#00f5c4;"
                                 "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #122838,stop:1 #081724);"
                                 "border:2px solid #00f5c4;border-radius:16px; }")
                elif val == "O":
                    btn_style = ("QPushButton { font-size:54px;font-weight:900;color:#ff9f43;"
                                 "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #2a1f14,stop:1 #160f08);"
                                 "border:2px solid #ff9f43;border-radius:16px; }")
                else:
                    btn_style = ("""
                        QPushButton {
                            font-size: 20px; font-weight: 700; color: #5a7495;
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #152236, stop:1 #0c1522);
                            border: 1px solid rgba(100, 160, 240, 0.22); border-radius: 16px;
                        }
                        QPushButton:hover {
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1e334e, stop:1 #122236);
                            border: 2px solid #00f5c4; color: #00f5c4;
                        }
                    """)
                button.setStyleSheet(btn_style)
                button.clicked.connect(lambda _checked=False, i=cell: self._submit_action(bytes((i,))))
                grid.addWidget(button, cell // 3, cell % 3)
            self.board_layout.addWidget(board)
        elif view["kind"] == "hexxagon":
            from .games.hexxagon import pack_move
            from PySide6.QtCore import QPoint
            from PySide6.QtGui import QPolygon, QRegion
            host = QWidget(); box = QVBoxLayout(host); box.setSpacing(10)
            hint = QLabel("✦ Select your stone, then a highlighted hex. Solid = clone · dotted = jump.")
            hint.setStyleSheet("background: rgba(0, 245, 196, 0.05); border: 1px solid rgba(0, 245, 196, 0.18); border-radius: 9px; padding: 8px 12px; color: #94a3b8; font-size: 11px;")
            hint.setWordWrap(True); box.addWidget(hint)
            board = QWidget(); board.setFixedSize(350, 310)
            board.setStyleSheet("""
                QWidget {
                    background: qradialgradient(cx:0.5, cy:0.5, radius:0.8, fx:0.5, fy:0.5, stop:0 #101c2e, stop:0.8 #08111b, stop:1 #04080e);
                    border: 1px solid rgba(100, 160, 240, 0.18);
                    border-radius: 18px;
                }
            """)
            enabled = (not self.settings.observer_mode and session["status"] == "active" and not session["pending_action"] and not session.get("resync_pending")
                       and (self.settings.transport == "practice" or (self.settings.tx_enabled and session["current_player"] == self.settings.callsign)))
            buttons, selected = {}, [None]
            legal = set(map(tuple, view["legal_moves"]))
            def paint_selection():
                for cell in view["cells"]:
                    i, owner = cell["id"], cell["owner"]
                    target = (selected[0], i) in legal
                    color = "#00f5c4" if owner == 1 else "#c084fc" if owner == 2 else "#0f1a28"
                    text_color = "#02201d" if owner == 1 else "#1e0838" if owner == 2 else "#475e77"
                    if i == selected[0]:
                        border = "#ffffff"; style = "solid"
                    elif target:
                        is_jump = len(pack_move(selected[0], i)) == 2
                        border = "#fbbf24" if is_jump else "#00f5c4"
                        style = "dotted" if is_jump else "solid"
                    else:
                        border = "#1e324a" if owner == 0 else ("#34d399" if owner == 1 else "#a855f7")
                        style = "solid"
                    buttons[i].setStyleSheet(f"background:{color};color:{text_color};border:2px {style} {border};font-weight:900;font-size:10px;padding:0;")
            def choose(i):
                if (selected[0], i) in legal:
                    self._submit_action(pack_move(selected[0], i)); return
                selected[0] = i if view["cells"][i]["owner"] == view["active_seat"] and selected[0] != i else None
                paint_selection()
            for cell in view["cells"]:
                i, q, r = cell["id"], cell["q"], cell["r"]
                button = QPushButton(str(i + 1), board); button.setObjectName(f"hexCell{i}")
                button.setGeometry(int(156 + 34 * (q + r / 2)), int(136 + 30 * r), 32, 36)
                button.setMask(QRegion(QPolygon([QPoint(16, 0), QPoint(31, 9), QPoint(31, 27), QPoint(16, 35), QPoint(0, 27), QPoint(0, 9)])))
                button.setEnabled(enabled); button.setAccessibleName(f"Hex {i + 1}, " + (session["players"][cell["owner"] - 1] if cell["owner"] else "empty"))
                button.clicked.connect(lambda _checked=False, index=i: choose(index))
                buttons[i] = button
            paint_selection(); box.addWidget(board)
            if not hasattr(self, "_hex_frames"):
                self._hex_frames = {}
            prior = self._hex_frames.get(session["id"])
            self._hex_frames[session["id"]] = (session["revision"], view["cells"])
            if prior and prior[0] + 1 == session["revision"]:
                from .hexxagon_effects import HexLightning
                HexLightning(board, prior[1], view["cells"])
            self.board_layout.addWidget(host)
        elif view["kind"] == "connect-four":
            host = QWidget(); box = QVBoxLayout(host); box.setSpacing(12)
            board = QWidget(); grid = QGridLayout(board); grid.setSpacing(6)
            grid.setContentsMargins(18, 16, 18, 16)
            board.setStyleSheet("""
                QWidget {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #101c2e, stop:1 #080f1a);
                    border: 2px solid #1e334a;
                    border-radius: 18px;
                }
            """)
            enabled = not self.settings.observer_mode and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active" and not session["pending_action"] and not session.get("resync_pending") and (self.settings.transport == "practice" or self.settings.tx_enabled)
            for col in range(7):
                choose = QPushButton(f"▼ {col+1}"); choose.setFixedSize(54, 42)
                choose.setEnabled(enabled and view["cells"][col] == " ")
                choose.setStyleSheet("""
                    QPushButton {
                        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #17273c, stop:1 #0f1c2d);
                        color: #00f5c4;
                        border: 1px solid rgba(0, 245, 196, 0.3);
                        border-radius: 8px;
                        font-weight: 800;
                        font-size: 12px;
                    }
                    QPushButton:hover {
                        background: #00f5c4;
                        color: #02201d;
                        border-color: #00f5c4;
                    }
                    QPushButton:disabled {
                        background: #0d1520;
                        color: #334458;
                        border-color: #162230;
                    }
                """)
                choose.clicked.connect(lambda _checked=False, c=col: self._submit_action(bytes((c,))))
                grid.addWidget(choose, 0, col)
            for row in range(6):
                for col in range(7):
                    mark = view["cells"][row * 7 + col]
                    disc = QLabel()
                    disc.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    disc.setFixedSize(54, 52)
                    if self.settings.color_scheme == "monochrome":
                        disc_style = f"background:{'#fff' if mark == 'X' else '#555' if mark == 'O' else '#111'};border-radius:26px;border:2px solid #777;"
                    elif mark == "X":
                        disc_style = "background: qradialgradient(cx:0.35, cy:0.35, radius:0.7, fx:0.3, fy:0.3, stop:0 #a7f3d0, stop:0.5 #10b981, stop:1 #047857); border-radius: 26px; border: 1px solid #34d399;"
                    elif mark == "O":
                        disc_style = "background: qradialgradient(cx:0.35, cy:0.35, radius:0.7, fx:0.3, fy:0.3, stop:0 #fef08a, stop:0.5 #f59e0b, stop:1 #b45309); border-radius: 26px; border: 1px solid #fbbf24;"
                    else:
                        disc_style = "background: #070d16; border: 2px solid #142132; border-radius: 26px;"
                    disc.setStyleSheet(disc_style)
                    grid.addWidget(disc, row + 1, col)
            box.addWidget(board)
            tip = QLabel("Select a column to drop a disc. Four in a row wins.")
            tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tip.setObjectName("muted")
            box.addWidget(tip)
            self.board_layout.addWidget(host)
        elif view["kind"] == "battleship":
            from .games.battleship import FLEET
            host = QWidget(); box = QVBoxLayout(host); box.setSpacing(10)
            active = not self.settings.observer_mode and session["status"] == "active" and not session["pending_action"] and not session.get("resync_pending") and (session["current_player"] == self.settings.callsign and (self.settings.transport == "practice" or self.settings.tx_enabled))
            phase_pill = QLabel(f"◈ {session['current_player'] or view.get('winner') or 'GAME OVER'}  ·  {view['phase'].upper() if session['status'] != 'finished' else 'FINISHED'}")
            phase_pill.setStyleSheet("background: rgba(0, 245, 196, 0.08); border: 1px solid rgba(0, 245, 196, 0.25); border-radius: 8px; padding: 6px 12px; color: #00f5c4; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
            box.addWidget(phase_pill)
            if view["phase"] == "setup" and not session.get("private_fleet"):
                draft = self._battleship_drafts.setdefault(session["id"], [])
                vertical = QCheckBox("Place vertically")
                vertical.setStyleSheet("color: #cbd5e1; font-size: 12px; font-weight: 600;")
                prompt = QLabel(f"Place vessel {len(draft)+1} of 5 · length {FLEET[len(draft)]}")
                prompt.setStyleSheet("color: #38bdf8; font-size: 12px; font-weight: 700;")
                box.addWidget(prompt); box.addWidget(vertical)
                board = QWidget(); grid = QGridLayout(board); grid.setSpacing(2); grid.setContentsMargins(8, 8, 8, 8)
                board.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                board.setStyleSheet("QWidget { background: qradialgradient(cx:0.5, cy:0.5, radius:0.8, fx:0.5, fy:0.5, stop:0 #0a1b28, stop:0.7 #06121c, stop:1 #030a10); border: 1px solid rgba(0, 245, 196, 0.22); border-radius: 12px; }")
                _add_radar_coordinates(grid)
                for i in range(100):
                    val = "■" if i in sum(draft, []) else "□"
                    button = QPushButton(val)
                    _style_naval_button(button, val, enabled=True)
                    def place(_checked=False, cell=i):
                        size=FLEET[len(draft)]; row,col=divmod(cell,10); vert=vertical.isChecked()
                        cells=[(row+j)*10+col if vert else row*10+col+j for j in range(size)]
                        if (vert and row+size>10) or (not vert and col+size>10) or set(cells)&set(sum(draft, [])): return
                        draft.append(cells)
                        if len(draft)==5:
                            self.engine.place_battleship_fleet(session["id"], draft); self._battleship_drafts.pop(session["id"], None)
                        self._board_signature = None
                        self._refresh_sessions()
                    button.clicked.connect(place); grid.addWidget(button, (i//10) + 1, (i%10) + 1)
                box.addWidget(board, 0, Qt.AlignmentFlag.AlignHCenter)
            elif view["phase"] == "setup":
                msg = QLabel("Fleet saved privately · waiting for both stations")
                msg.setStyleSheet("color: #94a3b8; font-size: 12px; padding: 10px 0;")
                box.addWidget(msg)
            else:
                if session["status"] == "finished":
                    status = f"{view.get('winner') or 'No station'} wins"
                elif view["phase"] == "reply":
                    status = f"Awaiting {session['current_player']}'s report for {chr(65 + view['pending'] % 10)}{view['pending'] // 10 + 1}"
                elif active:
                    status = "Your turn · select an uncalled coordinate"
                else:
                    status = f"Waiting for {session['current_player']} to fire"
                box.addWidget(QLabel(status))
                boards = QBoxLayout(QBoxLayout.Direction.LeftToRight if self.width() >= 1180 else QBoxLayout.Direction.TopToBottom)
                boards.setSpacing(12)
                box.addLayout(boards)
                cell_size = 26 if self.width() >= 1180 else 28
                def make_board(title, cells, fleet=None, firing=False):
                    panel = QWidget(); panel_layout = QVBoxLayout(panel)
                    panel_layout.setContentsMargins(0, 0, 0, 0)
                    header = QLabel(title)
                    header.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: 800;")
                    panel_layout.addWidget(header)
                    board = QWidget(); grid = QGridLayout(board)
                    grid.setSpacing(2); grid.setContentsMargins(6, 6, 6, 6)
                    board.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                    _add_radar_coordinates(grid, cell_size)
                    occupied = set(sum(fleet or [], []))
                    sunk_cells = {c for ship in (fleet or []) if all(cells[c] >= 2 for c in ship) for c in ship}
                    for i, value in enumerate(cells):
                        if i in sunk_cells:
                            value = 3
                        text = "·" if value == 1 else "✕" if value == 2 else ("S" if fleet else f"S{value-2}") if value >= 3 else "■" if i in occupied else "□"
                        enabled = firing and active and view["phase"] == "call" and value == 0
                        button = QPushButton(text)
                        _style_naval_button(button, value if value else text, enabled=enabled, cell_size=cell_size)
                        button.setStyleSheet(button.styleSheet().replace("QPushButton {", "QPushButton, QPushButton:disabled {"))
                        button.setEnabled(enabled)
                        button.setToolTip(f"{chr(65+i%10)}{i//10+1}: " + ("sunk" if value >= 3 else "hit" if value == 2 else "miss" if value == 1 else "ship" if i in occupied else "uncalled"))
                        if firing:
                            button.clicked.connect(lambda _=False, cell=i: self._submit_action(bytes((cell,))))
                        grid.addWidget(button, i//10 + 1, i%10 + 1)
                    panel_layout.addWidget(board, 0, Qt.AlignmentFlag.AlignHCenter)
                    boards.addWidget(panel)
                if session.get("private_fleet"):
                    make_board("YOUR FLEET · INCOMING FIRE", view["incoming_shots"], session["private_fleet"])
                make_board("ENEMY WATERS · YOUR SHOTS" if view.get("shot_owner") == self.settings.callsign else f"{view.get('shot_owner', '')} · CALLED COORDINATES", view["shots"], firing=True)
                legend = QLabel("■ Ship    · Miss    ✕ Hit    S Sunk")
                legend.setAlignment(Qt.AlignmentFlag.AlignCenter)
                box.addWidget(legend)
            self.board_layout.addWidget(host)
        elif view["kind"] == "sea-warfare":
            self._render_sea_warfare(session, view)
        elif view["kind"] == "signal-siege":
            from .games.signal_siege import pack_play, pack_draft
            host = QWidget(); box = QVBoxLayout(host); box.setSpacing(12)
            title = QLabel(f"SIGNAL SIEGE   ·   WAVE {view['wave']}/3")
            title.setStyleSheet("color:#e5b9ff;font-size:18px;font-weight:800;letter-spacing:1px")
            box.addWidget(title)
            
            boss_frame = QWidget()
            boss_layout = QVBoxLayout(boss_frame); boss_layout.setContentsMargins(12, 10, 12, 10); boss_layout.setSpacing(6)
            boss_frame.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #250938, stop:1 #13041d); border: 1px solid #a855f7; border-radius: 12px;")
            boss_label = QLabel(f"◈ CORE BOSS   ·   {view['boss_hp']} / {view['boss_max_hp']} HP")
            boss_label.setStyleSheet("color: #f5d0fe; font-size: 12px; font-weight: 800; letter-spacing: 0.8px;")
            boss_layout.addWidget(boss_label)
            
            hp_bar = QProgressBar()
            hp_bar.setRange(0, view['boss_max_hp'])
            hp_bar.setValue(view['boss_hp'])
            hp_bar.setTextVisible(False)
            hp_bar.setFixedHeight(8)
            hp_bar.setStyleSheet("QProgressBar { background: #0c0413; border: none; border-radius: 4px; } QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d946ef, stop:1 #f43f5e); border-radius: 4px; }")
            boss_layout.addWidget(hp_bar)
            
            pulse_lbl = QLabel(f"⚡ Pulse: {view['pulse_damage']} dmg · in {view['rounds_until_pulse']} round(s) · Jam {view['jam']}")
            pulse_lbl.setStyleSheet("color: #cbd5e1; font-size: 11px; font-weight: 600;")
            boss_layout.addWidget(pulse_lbl)
            box.addWidget(boss_frame)
            actor_call = session["current_player"] if self.settings.transport == "practice" else self.settings.callsign
            active = not self.settings.observer_mode and session["status"] == "active" and not session["pending_action"] and not session.get("resync_pending") and (self.settings.transport == "practice" or (self.settings.tx_enabled and actor_call == session["current_player"]))
            for station in view["stations"]:
                is_mine = not self.settings.observer_mode and station["callsign"] == actor_call
                st_card = QWidget()
                st_layout = QHBoxLayout(st_card); st_layout.setContentsMargins(10, 6, 10, 6)
                if is_mine:
                    st_card.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #062b24, stop:1 #0a1f1e); border: 1px solid #00f5c4; border-radius: 8px;")
                else:
                    st_card.setStyleSheet("background: #0d1522; border: 1px solid #1e2c3e; border-radius: 8px;")
                desc = (f"{station['callsign']}    ♥ {station['hp']}/18     ◈ {station['shield']} shield     ✦ {station['focus']} focus     "
                        f"▤ {station['deck_size']} cards" + ("   [YOUR HAND]" if is_mine else "   ▤ ▤ ▤"))
                row = QLabel(desc)
                row.setStyleSheet("color:#00f5c4;font-size:11px;font-weight:700" if is_mine else "color:#94a3b8;font-size:11px")
                st_layout.addWidget(row)
                box.addWidget(st_card)
            if view["phase"] == "draft":
                box.addWidget(QLabel(f"{session['current_player']} chooses one upgrade for their deck:"))
                row = QHBoxLayout()
                for reward in view["rewards"]:
                    button = QPushButton(f"{reward['name']}\n{reward['description']}\nADD TO DECK +")
                    button.setMinimumSize(150, 115); button.setEnabled(active)
                    button.setStyleSheet("font-size:12px;font-weight:700;color:#f5d0fe;background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #271638,stop:1 #150c20);border:1px solid #a855f7;border-radius:12px;padding:8px;")
                    button.clicked.connect(lambda _checked=False, i=reward["slot"]: self._submit_action(pack_draft(i)))
                    row.addWidget(button)
                box.addLayout(row)
            elif view["phase"] == "battle":
                station = next((s for s in view["stations"] if s["callsign"] == actor_call), None) if not self.settings.observer_mode else None
                if station:
                    chooser = QComboBox(); chooser.setObjectName("siegeTarget")
                    for index, ally in enumerate(view["stations"]):
                        chooser.addItem(f"Support target · {ally['callsign']}", index)
                        if ally["callsign"] == actor_call: chooser.setCurrentIndex(index)
                    box.addWidget(chooser)
                    row = QHBoxLayout()
                    for card in station["hand"]:
                        button = QPushButton(f"{card['name']}\n{card['description']}\nPLAY CARD →")
                        button.setMinimumSize(150, 125); button.setEnabled(active)
                        button.setStyleSheet("font-size:12px;font-weight:700;color:#e2e8f0;background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #1a273c,stop:1 #0e1724);border:1px solid rgba(0,245,196,0.35);border-radius:12px;padding:8px;")
                        button.clicked.connect(lambda _checked=False, i=card["slot"]: self._submit_action(pack_play(i, chooser.currentData())))
                        row.addWidget(button)
                    box.addLayout(row)
                else:
                    box.addWidget(QLabel("Waiting for another station. Their hand remains face down."))
            else:
                res_lbl = QLabel(f"MISSION {view['result'].upper()}")
                res_lbl.setStyleSheet("color: #00f5c4; font-size: 18px; font-weight: 800;")
                box.addWidget(res_lbl)
            self.board_layout.addWidget(host)
        elif view["kind"] == "piles":
            host = QWidget(); box = QVBoxLayout(host); box.setSpacing(12)
            piles_card = QWidget()
            piles_card.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #111a28, stop:1 #080f19); border: 1px solid rgba(100, 160, 240, 0.16); border-radius: 16px; padding: 14px;")
            card_layout = QVBoxLayout(piles_card); card_layout.setSpacing(10)
            for index, count in enumerate(view["piles"]):
                row = QHBoxLayout(); label = QLabel(f"PILE {index+1:02d}")
                label.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: 800; letter-spacing: 1.5px;")
                row.addWidget(label); row.addSpacing(16)
                for _ in range(count):
                    pebble = QLabel("◆")
                    pebble.setStyleSheet("color: #00f5c4; font-size: 22px;" if self.settings.color_scheme != "monochrome" else "color: #eeeeee; font-size: 22px;")
                    row.addWidget(pebble)
                row.addStretch(1)
                cnt_badge = QLabel(str(count))
                cnt_badge.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 700; background: #0f1926; border: 1px solid #1e2c3e; border-radius: 6px; padding: 2px 8px;")
                row.addWidget(cnt_badge)
                card_layout.addLayout(row)
            box.addWidget(piles_card)
            controls = QHBoxLayout(); pile = QComboBox(); amount = QSpinBox()
            for i, count in enumerate(view["piles"]):
                pile.addItem(f"Pile {i+1} · {count} remaining", i)
                if count == 0:
                    pile.model().item(i).setEnabled(False)
            first = next((i for i, count in enumerate(view["piles"]) if count), 0)
            pile.setCurrentIndex(first)
            def update_amount(index):
                amount.setRange(1, max(1, view["piles"][index]))
            pile.currentIndexChanged.connect(update_amount)
            update_amount(first)
            take = QPushButton("Take items"); take.setObjectName("primaryButton")
            take.setEnabled(not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled) and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active" and not session["pending_action"] and not session.get("resync_pending"))
            take.clicked.connect(lambda: self._submit_action(bytes((pile.currentIndex(), amount.value()))))
            controls.addWidget(pile); controls.addWidget(amount); controls.addWidget(take); box.addLayout(controls)
            self.board_layout.addWidget(host)
        elif view["kind"] == "word-tiles":
            host = QWidget(); box = QVBoxLayout(host); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(8)
            current = session["current_player"]
            header_bar = QLabel(f"BAG {view['bag']}  ·  {view['lexicon'].upper()}  ·  OPEN RACKS")
            header_bar.setStyleSheet("background: rgba(0, 245, 196, 0.06); border: 1px solid rgba(0, 245, 196, 0.18); border-radius: 8px; padding: 6px 12px; color: #00f5c4; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
            box.addWidget(header_bar)
            grid_host = QWidget(); grid = QGridLayout(grid_host); grid.setSpacing(2); grid.setContentsMargins(6, 6, 6, 6)
            grid_host.setStyleSheet("background: #070d16; border: 1px solid rgba(100, 160, 240, 0.18); border-radius: 12px;")
            grid.addWidget(QLabel("   "), 0, 0)
            for x in range(15):
                lbl = QLabel(chr(65 + x))
                lbl.setStyleSheet("color: #486581; font-size: 9px; font-weight: 800; font-family: monospace;")
                grid.addWidget(lbl, 0, x + 1, alignment=Qt.AlignmentFlag.AlignCenter)
            premium_colors = {"TW": "#7f1d1d", "DW": "#831843", "TL": "#0369a1", "DL": "#0284c7", "..": "#0d1726"}
            for y in range(15):
                row_lbl = QLabel(str(y + 1))
                row_lbl.setStyleSheet("color: #486581; font-size: 9px; font-weight: 800; font-family: monospace;")
                grid.addWidget(row_lbl, y + 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)
                for x in range(15):
                    index = y * 15 + x
                    tile = view["cells"][index]
                    text = tile or ("★" if index == 112 else view["premiums"][index])
                    button = QPushButton(text)
                    button.setObjectName(f"wordCell{index}")
                    button.setFixedSize(28, 27)
                    if tile:
                        button.setStyleSheet("QPushButton { padding:0; font-size:12px; font-weight:800; background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fef9c3, stop:1 #fde047); color: #0f172a; border: 1px solid #ca8a04; border-radius: 4px; }")
                    else:
                        color = "#343a40" if self.settings.color_scheme == "monochrome" else premium_colors[view["premiums"][index]]
                        foreground = "#eeeeee" if self.settings.color_scheme == "monochrome" else "#94a3b8" if view["premiums"][index] == ".." else "#ffffff"
                        button.setStyleSheet(f"QPushButton {{ padding:0; font-size:9px; font-weight:700; background:{color}; color:{foreground}; border:1px solid rgba(255, 255, 255, 0.08); border-radius:3px; }} QPushButton:hover {{ border-color: #00f5c4; }}")
                    button.clicked.connect(lambda _checked=False, x=x, y=y: start.setText(f"{chr(65 + x)}{y + 1}"))
                    grid.addWidget(button, y + 1, x + 1)
            box.addWidget(grid_host, alignment=Qt.AlignmentFlag.AlignHCenter)
            for player, rack in view["racks"].items():
                rack_card = QLabel(f"◈ {player} RACK   " + "   ".join(rack))
                rack_card.setStyleSheet("color: #7dd3fc; font-weight: 700; font-size: 11px; background: rgba(56, 189, 248, 0.06); border: 1px solid rgba(56, 189, 248, 0.2); border-radius: 6px; padding: 6px 12px;")
                box.addWidget(rack_card)
            enabled = not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled) and (self.settings.transport == "practice" or current == self.settings.callsign) and session["status"] == "active" and not session["pending_action"] and not session.get("resync_pending")
            form = QHBoxLayout(); start = QLineEdit(); start.setObjectName("wordStart"); start.setPlaceholderText("H8")
            direction = QComboBox(); direction.setObjectName("wordDirection"); direction.addItem("Across", "H"); direction.addItem("Down", "V")
            word = QLineEdit(); word.setObjectName("wordText"); word.setPlaceholderText("Word · use {A} for a blank tile")
            place = QPushButton("Place word"); place.setObjectName("wordPlace"); place.setEnabled(enabled)
            place.clicked.connect(lambda: self._submit_word_placement(session, start.text(), direction.currentData(), word.text()))
            form.addWidget(start); form.addWidget(direction); form.addWidget(word, 1); form.addWidget(place)
            box.addLayout(form)
            extras = QHBoxLayout(); exchange_text = QLineEdit(); exchange_text.setObjectName("wordExchangeTiles"); exchange_text.setPlaceholderText("Exchange letters; ? = blank")
            exchange = QPushButton("Exchange"); exchange.setObjectName("wordExchange"); exchange.setEnabled(enabled and view["bag"] >= 7)
            exchange.clicked.connect(lambda: self._submit_word_exchange(session, exchange_text.text()))
            pass_turn = QPushButton("Pass"); pass_turn.setEnabled(enabled)
            pass_turn.clicked.connect(lambda: self._submit_action(b"\x01"))
            extras.addWidget(exchange_text, 1); extras.addWidget(exchange); extras.addWidget(pass_turn)
            box.addLayout(extras)
            self.board_layout.addWidget(host)
        else:
            panel = QWidget(); box = QVBoxLayout(panel)
            summary = QLabel(str(view)); summary.setWordWrap(True); box.addWidget(summary)
            box.addWidget(QLabel("Plugin action · hexadecimal bytes"))
            raw = QLineEdit(); raw.setPlaceholderText("e.g. 0102")
            send = QPushButton("Submit action"); send.setObjectName("primaryButton")
            send.setEnabled(not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled) and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active" and not session.get("resync_pending"))
            send.clicked.connect(lambda: self._submit_hex_action(raw.text()))
            box.addWidget(raw); box.addWidget(send); self.board_layout.addWidget(panel)

    def _render_sea_warfare(self, session: dict, view: dict):
        host = QWidget(); box = QVBoxLayout(host); box.setSpacing(4)
        active = (not self.settings.observer_mode and session["status"] == "active"
                  and not session["pending_action"] and not session.get("resync_pending")
                  and (self.settings.transport == "practice" or
                       (self.settings.tx_enabled and session["current_player"] == self.settings.callsign)))
        rules = QLabel("⚓ CARRIER: 50% block roll  ·  BATTLESHIP: adjacent double shot\n"
                       "⚓ CRUISER: one free move  ·  DESTROYER: disarms one mine\n"
                       "⚓ SUBMARINE: one sonar row  ·  PATROL BOAT: optional hidden mine")
        rules.setStyleSheet("background: rgba(0, 245, 196, 0.05); border: 1px solid rgba(0, 245, 196, 0.2); border-radius: 10px; padding: 10px 14px; color: #7dd3fc; font-size: 11px; line-height: 1.5;")
        rules.setWordWrap(True)
        rules.hide()
        phase_badge = QLabel(f"◈ {session['current_player']}  ·  {view['phase'].upper()}")
        phase_badge.setStyleSheet("background: rgba(0, 245, 196, 0.08); border: 1px solid rgba(0, 245, 196, 0.25); border-radius: 8px; padding: 6px 12px; color: #00f5c4; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
        rules_toggle = QPushButton("ⓘ Rules")
        rules_toggle.setCheckable(True)
        rules_toggle.setMaximumWidth(88)
        rules_toggle.toggled.connect(lambda expanded: (rules.setVisible(expanded), rules_toggle.setText("Hide rules" if expanded else "ⓘ Rules")))
        phase_row = QHBoxLayout(); phase_row.setContentsMargins(0, 0, 0, 0); phase_row.setSpacing(6)
        phase_row.addWidget(phase_badge, 1); phase_row.addWidget(rules_toggle)
        box.addLayout(phase_row)
        box.addWidget(rules)

        # Size the 10x10 playfield against the actual available viewport so
        # setup instructions cannot push it below the fold on short windows.
        naval_cell_size = max(18, min(28, (self.board_scroll.viewport().height() - 110) // 11))

        def refresh_board():
            self._board_signature = None
            self._show_session(session["id"])

        def grid_widget(cells, enabled=False, handler=None, marks=None, fleet=None):
            board = QWidget(); grid = QGridLayout(board); grid.setSpacing(1); grid.setContentsMargins(4, 4, 4, 4)
            board.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            board.setStyleSheet("QWidget { background: qradialgradient(cx:0.5, cy:0.5, radius:0.8, fx:0.5, fy:0.5, stop:0 #0a1b28, stop:0.7 #06121c, stop:1 #030a10); border: 1px solid rgba(0, 245, 196, 0.22); border-radius: 12px; }")
            _add_radar_coordinates(grid, naval_cell_size, responsive=True)
            for index in range(100):
                mark = (marks or {}).get(index)
                txt = "■" if index in cells else "□"
                button = QPushButton(txt); button.setFixedSize(naval_cell_size, naval_cell_size)
                button.setProperty("adaptiveRadarCell", True)
                _style_naval_button(button, txt, enabled=bool(enabled and handler is not None),
                                    cell_size=naval_cell_size)
                button.setEnabled(enabled and handler is not None)
                if handler is not None:
                    button.clicked.connect(lambda _=False, cell=index: handler(cell))
                grid.addWidget(button, (index // 10) + 1, (index % 10) + 1)
            assets = Path(__file__).with_name("web_assets")
            sprites = ("battleship-carrier.png", "battleship-hull.png", "battleship-cruiser.png",
                       "battleship-destroyer.png", "battleship-submarine.png", "battleship-patrol.png")
            for ship_id, ship in enumerate(fleet or []):
                if not ship: continue
                first = min(ship); row, col = divmod(first, 10)
                vertical_ship = len(ship) > 1 and ship[1] - ship[0] == 10
                name = sprites[ship_id] + ("" if vertical_ship else "")
                if not vertical_ship: name = name.replace(".png", "-horizontal.png")
                sprite = QPixmap(str(assets / name))
                if sprite.isNull(): continue
                length = len(ship)
                width, height = (naval_cell_size, length*naval_cell_size+(length-1)) if vertical_ship else (length*naval_cell_size+(length-1), naval_cell_size)
                image = QLabel(); image.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                image.setAlignment(Qt.AlignmentFlag.AlignCenter)
                image.setPixmap(sprite.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
                grid.addWidget(image, row + 1, col + 1, length if vertical_ship else 1,
                               1 if vertical_ship else length)
            for index, mark in (marks or {}).items():
                token = QLabel(mark); token.setAlignment(Qt.AlignmentFlag.AlignCenter)
                token.setStyleSheet("color:#ffd080;font-size:17px;font-weight:800;background:transparent")
                token.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                grid.addWidget(token, (index // 10) + 1, (index % 10) + 1)
            return board

        if view["phase"] == "setup" and not session.get("private_fleet"):
            fleet_sizes = (5, 4, 3, 3, 3, 2)
            draft = self._battleship_drafts.setdefault(session["id"],
                     {"ships": [], "mine": None, "mine_done": False, "decoy": None, "decoy_done": False})
            if isinstance(draft, list):
                draft = {"ships": draft, "mine": None, "mine_done": False, "decoy": None, "decoy_done": False}
                self._battleship_drafts[session["id"]] = draft
            ships = draft["ships"]
            vertical = QCheckBox("Place vertically")

            def finish_setup():
                try:
                    self.engine.place_battleship_fleet(session["id"], ships,
                        [] if draft["mine"] is None else [draft["mine"]],
                        [] if draft["decoy"] is None else [draft["decoy"]])
                    self._battleship_drafts.pop(session["id"], None)
                    refresh_board()
                except ValueError as exc:
                    QMessageBox.warning(self, "Fleet placement", str(exc))

            if len(ships) < len(fleet_sizes):
                index = len(ships); size = fleet_sizes[index]
                vertical.setStyleSheet("color:#cbd5e1;font-size:11px;font-weight:600")
                prompt_row = QHBoxLayout(); prompt_row.setContentsMargins(0, 0, 0, 0)
                prompt_row.addWidget(QLabel(f"Place vessel {index+1} of 6 · length {size}")); prompt_row.addStretch(1); prompt_row.addWidget(vertical)
                box.addLayout(prompt_row)
                occupied = set(sum(ships, []))
                def place_ship(cell):
                    row, col = divmod(cell, 10); is_vertical = vertical.isChecked()
                    cells = [(row+j)*10+col if is_vertical else row*10+col+j for j in range(size)]
                    if ((is_vertical and row+size > 10) or (not is_vertical and col+size > 10)
                            or occupied.intersection(cells)):
                        return
                    ships.append(cells)
                    if len(ships) == len(fleet_sizes) and draft["mine_done"] and draft["decoy_done"]:
                        finish_setup()
                    else:
                        refresh_board()
                box.addWidget(grid_widget(occupied, active, place_ship, fleet=ships),
                              0, Qt.AlignmentFlag.AlignHCenter)
            elif not draft["mine_done"]:
                box.addWidget(QLabel("Optional: place one hidden mine on the patrol boat."))
                skip = QPushButton("Skip mine"); skip.clicked.connect(lambda: (draft.update(mine_done=True), refresh_board())); box.addWidget(skip)
                patrol = set(ships[5])
                def place_mine(cell):
                    if cell not in patrol: return
                    draft.update(mine=cell, mine_done=True)
                    if draft["decoy_done"]: finish_setup()
                    else: refresh_board()
                box.addWidget(grid_widget(patrol, active, place_mine,
                    marks={} if draft["mine"] is None else {draft["mine"]:"✹"}, fleet=ships),
                    0, Qt.AlignmentFlag.AlignHCenter)
            elif not draft["decoy_done"]:
                box.addWidget(QLabel("Optional: place a one-cell inflatable decoy in open water."))
                skip = QPushButton("Skip decoy")
                def skip_decoy():
                    draft.update(decoy_done=True)
                    finish_setup()
                skip.clicked.connect(skip_decoy); box.addWidget(skip)
                occupied = set(sum(ships, []))
                def place_decoy(cell):
                    if cell in occupied or cell == draft["mine"]: return
                    draft.update(decoy=cell, decoy_done=True)
                    finish_setup()
                setup_marks = {} if draft["mine"] is None else {draft["mine"]:"✹"}
                if draft["decoy"] is not None: setup_marks[draft["decoy"]] = "◉"
                box.addWidget(grid_widget(occupied, active, place_decoy, marks=setup_marks, fleet=ships),
                              0, Qt.AlignmentFlag.AlignHCenter)
        elif view["phase"] == "setup":
            box.addWidget(QLabel("Fleet and optional extras are saved privately · waiting for both stations."))
        elif view["phase"] == "reply":
            box.addWidget(QLabel("Resolving the call against your private fleet."))
        else:
            own = set(sum(session.get("private_fleet") or [], []))
            own_marks = {cell: "×" for cell in session.get("private_hits", [])}
            if session.get("private_deploy"):
                for cell in session["private_deploy"].get("mines", []): own_marks[cell] = "✹"
                for cell in session["private_deploy"].get("decoys", []): own_marks[cell] = "◉"
            status = "Fighter roll spent" if session.get("fighter_used") else "Fighter roll ready (50%)"
            plane = QLabel(status)
            pixmap = QPixmap(str(Path(__file__).with_name("web_assets") / "battleship-fighter.png"))
            if not pixmap.isNull(): plane.setPixmap(pixmap.scaled(32, 38, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            box.addWidget(plane)
            box.addWidget(QLabel("Your fleet · private on this station"))
            box.addWidget(grid_widget(own, marks=own_marks, fleet=session.get("private_fleet")),
                          0, Qt.AlignmentFlag.AlignHCenter)

            mode = QComboBox(); mode.addItem("One shot", "shot")
            seat = session["players"].index(self.settings.callsign) if self.settings.callsign in session["players"] else 0
            used = view["used"][seat]
            own_fleet = session.get("private_fleet") or []
            private_hits = set(session.get("private_hits", []))
            sunk = lambda n: len(own_fleet) > n and all(c in private_hits for c in own_fleet[n])
            if not (used & 1) and not sunk(1): mode.addItem("Battleship double shot · once", "double")
            if not (used & 2) and not sunk(4): mode.addItem("Submarine sonar · once", "sonar")
            box.addWidget(mode)
            scan_row = QHBoxLayout(); row_choice = QComboBox()
            for row in range(10): row_choice.addItem(f"Scan row {row+1}", row)
            scan = QPushButton("Run sonar"); scan.setEnabled(active); scan.clicked.connect(lambda: self._submit_action(bytes((190 + row_choice.currentData(),))))
            scan_row.addWidget(row_choice); scan_row.addWidget(scan); box.addLayout(scan_row)
            enemy_buttons = []
            def update_mode(*_):
                sonar_mode = mode.currentData() == "sonar"
                scan.setVisible(sonar_mode); row_choice.setVisible(sonar_mode)
                for index, item in enumerate(enemy_buttons):
                    col = index % 10
                    enabled = active and view["shots"][index] == 0 and not sonar_mode
                    if mode.currentData() == "double":
                        enabled = enabled and col < 9 and view["shots"][index+1] == 0
                    item.setEnabled(enabled)
                    if view["shots"][index] == 0:
                        _style_naval_button(item, "□", enabled=enabled)
            mode.currentIndexChanged.connect(update_mode); update_mode()
            if any(value != 255 for value in view.get("sonar", [])):
                sonar_text = " · ".join(f"Row {i+1}: {('clear','faint','strong')[value]}" for i, value in enumerate(view["sonar"]) if value != 255)
                box.addWidget(QLabel("Sonar · " + sonar_text))

            move_row = QHBoxLayout(); vessel = QComboBox()
            for name in ("Carrier", "Battleship", "Cruiser", "Destroyer", "Submarine", "Patrol boat"): vessel.addItem(name)
            direction = QComboBox()
            for name in ("Up", "Down", "Left", "Right"): direction.addItem(name)
            move = QPushButton("Move vessel"); seat = session["players"].index(self.settings.callsign)
            supplies = view["moves"][seat]
            def update_move(*_):
                ship = vessel.currentIndex()
                move.setEnabled(active and not sunk(ship) and (not (used & 4) if ship == 2 else supplies > 0))
            vessel.currentIndexChanged.connect(update_move); update_move()
            move.clicked.connect(lambda: self._submit_action(bytes((200 + vessel.currentIndex()*4 + direction.currentIndex(),))))
            move_row.addWidget(vessel); move_row.addWidget(direction); move_row.addWidget(move); box.addLayout(move_row)

            enemy_header = QLabel("ENEMY WATERS · HITS DO NOT IDENTIFY SUNK SHIPS")
            enemy_header.setStyleSheet("color: #f43f5e; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
            box.addWidget(enemy_header)
            enemy = QWidget(); grid = QGridLayout(enemy); grid.setSpacing(2); grid.setContentsMargins(8, 8, 8, 8)
            enemy.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            enemy.setStyleSheet("QWidget { background: qradialgradient(cx:0.5, cy:0.5, radius:0.8, fx:0.5, fy:0.5, stop:0 #0a1b28, stop:0.7 #06121c, stop:1 #030a10); border: 1px solid rgba(0, 245, 196, 0.22); border-radius: 12px; }")
            _add_radar_coordinates(grid)
            for cell, value in enumerate(view["shots"]):
                row, col = divmod(cell, 10)
                txt = "·" if value == 1 else "×" if value == 2 else "□"
                button = QPushButton(txt); button.setFixedSize(28, 28)
                _style_naval_button(button, value if value else txt, enabled=True)
                def fire(_=False, cell=cell):
                    action_mode = mode.currentData()
                    if action_mode == "sonar": return
                    if action_mode == "double":
                        if cell % 10 > 8 or view["shots"][cell+1] != 0: return
                        code = 100 + (cell//10)*9 + cell%10
                    else: code = cell
                    self._submit_action(bytes((code,)))
                button.clicked.connect(fire); grid.addWidget(button, row + 1, col + 1); enemy_buttons.append(button)
            update_mode()
            box.addWidget(enemy, 0, Qt.AlignmentFlag.AlignHCenter)
        self.board_layout.addWidget(host)

    def _render_scorched(self, session: dict, view: dict):
        """Native artillery controls; only two-byte moves enter the shared engine."""
        host = QWidget(); host.setObjectName("scorchedHost"); layout = QHBoxLayout(host); layout.setContentsMargins(0, 0, 0, 0)
        view = session.get("animation_view", view)
        shot_key = (session["id"], session.get("animation_revision", session["revision"]))
        animate = bool(view.get("impact") and self._last_scorched_animation_key != shot_key)
        if animate:
            self._last_scorched_animation_key = shot_key
            self._scorched_started = time.monotonic()
        battlefield = ScorchedBattlefield(view)
        started = getattr(self, "_scorched_started", 0)
        if view.get("impact") and time.monotonic() - started < 4.4:
            battlefield.replay(started=started)
        # Maintain a consistent, readable vertical view while letting the
        # extra horizontal world extend beyond the desktop viewport.
        scene_scale = 14
        battlefield.setFixedSize(view["width"] * scene_scale, view["sky_height"] * scene_scale)
        map_scroll = BattleMapScroll(); map_scroll.setObjectName("battlefieldScroll")
        map_scroll.viewport().setObjectName("scorchedViewport")
        map_scroll.setWidgetResizable(False); map_scroll.setWidget(battlefield)
        map_scroll.setFrameShape(QFrame.Shape.NoFrame)
        map_scroll.setToolTip("WASD / arrows to pan · click to center · drag to explore")
        battlefield.shot_position.connect(lambda x, y: map_scroll.ensureVisible(int(x * scene_scale + 12),
            int((battlefield.view["sky_height"] - y) * scene_scale + 12), max(1, map_scroll.viewport().width() // 3), max(1, map_scroll.viewport().height() // 3)) if map_scroll.follow_shot else None)
        map_column = QWidget(); map_column.setObjectName("scorchedMapColumn"); map_layout = QVBoxLayout(map_column); map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.addWidget(ScorchedMinimap(view, map_scroll))
        hint = QLabel("WASD / arrows · click to center · drag to pan"); hint.setObjectName("muted")
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed); map_layout.addWidget(hint)
        map_layout.addWidget(map_scroll, 1)
        layout.addWidget(map_column, 4)
        scroll = QScrollArea(); scroll.setObjectName("scorchedControlsScroll")
        scroll.viewport().setObjectName("scorchedControlsViewport")
        scroll.setWidgetResizable(True); scroll.setMinimumWidth(180)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        controls = QWidget(); controls.setObjectName("scorchedControls"); box = QVBoxLayout(controls)
        box.setContentsMargins(6, 2, 6, 2); box.setSpacing(9)
        map_card = QLabel(f"◈ {view['map_name'].upper()}  ·  TURN {view['round'] + 1}")
        map_card.setStyleSheet("color: #00f5c4; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
        box.addWidget(map_card)
        wind = view["wind"]
        wind_lbl = QLabel(f"WIND  {wind['direction']}  {wind['label'].upper()}")
        wind_lbl.setStyleSheet("background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.22); border-radius: 6px; padding: 4px 8px; color: #7dd3fc; font-size: 10px; font-weight: 700;")
        box.addWidget(wind_lbl)
        can_play = (not self.settings.observer_mode and session["status"] == "active"
                    and not session["pending_action"] and not session.get("resync_pending") and
                    (self.settings.transport == "practice" or
                     (self.settings.tx_enabled and session["current_player"] == self.settings.callsign)))
        actor = session["current_player"] if self.settings.transport == "practice" else self.settings.callsign
        tank = next((item for item in view["tanks"] if item["player"] == actor), None)
        for item in view["tanks"]:
            is_actor = item["player"] == actor
            tank_lbl = QLabel(f"{item['player']}: ¤{item['credits']} · {item['fuel']} fuel · {item['repair_kits']} repairs")
            tank_lbl.setStyleSheet("color: #00f5c4; font-size: 11px; font-weight: 700; background: rgba(0, 245, 196, 0.06); border: 1px solid rgba(0, 245, 196, 0.18); border-radius: 6px; padding: 4px 8px;" if is_actor else "color: #94a3b8; font-size: 11px; background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 6px; padding: 4px 8px;")
            box.addWidget(tank_lbl)
        weapon = QComboBox(); weapon.setObjectName("scorchedWeapon")
        for item in view["weapons"]:
            stock = tank["ammo"][item["id"]] if tank else 0
            label = f"{item['name']} · {'∞' if item['id'] == 0 else stock} rounds"
            weapon.addItem(label, item["id"])
            weapon.model().item(item["id"]).setEnabled(bool(stock))
            weapon.setItemData(item["id"], f"{item['tag']} · blast {item['radius']:.1f}", Qt.ItemDataRole.ToolTipRole)
        weapon_key = (session["id"], actor)
        selected_weapon = self._scorched_weapons.get(weapon_key, 0)
        if not tank or not tank["ammo"][selected_weapon]:
            selected_weapon = 0
        weapon.setCurrentIndex(weapon.findData(selected_weapon))
        weapon.currentIndexChanged.connect(
            lambda: self._scorched_weapons.__setitem__(weapon_key, int(weapon.currentData())))
        box.addWidget(weapon)
        angle_label = QLabel("Angle: 45°")
        angle_label.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: 700;")
        angle = QSlider(Qt.Orientation.Horizontal); angle.setObjectName("scorchedAngle")
        angle.setRange(view["min_angle"], view["max_angle"]); angle.setValue(45)
        box.addWidget(angle_label); box.addWidget(angle)
        power_label = QLabel("Power: 100%")
        power_label.setStyleSheet("color: #00f5c4; font-size: 11px; font-weight: 700;")
        power = QSlider(Qt.Orientation.Horizontal); power.setObjectName("scorchedPower")
        power.setRange(view["min_power"] // view["power_step"], view["max_power"] // view["power_step"])
        power.setValue(view["max_power"] // view["power_step"])
        box.addWidget(power_label); box.addWidget(power)
        def preview():
            power_label.setText(f"Power: {power.value() * view['power_step']}%")
            angle_label.setText(f"Angle: {angle.value()}°")
            if not can_play or not tank:
                battlefield.set_preview([]); return
            game = self.engine.registry.by_id(session["game_id"])
            saved = game.decode_state(session["state"])
            shot = _trace_shot(saved["terrain"], saved["x"], saved["turn"],
                               int(weapon.currentData()), angle.value(), _wind(saved["map"], saved["round"]), power.value() * view["power_step"])
            battlefield.set_preview(shot["path"], angle.value(), actor)
        battlefield.animation_finished.connect(preview)
        power.valueChanged.connect(preview)
        angle.valueChanged.connect(preview)
        weapon.currentIndexChanged.connect(preview)
        fire = QPushButton("Fire weapon"); fire.setObjectName("scorchedFire")
        fire.setEnabled(can_play and bool(tank))
        def fire_now():
            action = pack_shot(int(weapon.currentData()), angle.value(), power.value() * view["power_step"])
            game = self.engine.registry.by_id(session["game_id"])
            saved = game.decode_state(session["state"])
            predicted = game.apply_action(saved, tuple(session["players"]), actor, action)
            predicted_view = game.view_model(predicted, tuple(session["players"]))
            self._last_scorched_animation_key = (session["id"], session["revision"] + 1)
            self._scorched_started = time.monotonic()
            battlefield.setFixedSize(predicted_view["width"] * scene_scale, predicted_view["sky_height"] * scene_scale)
            battlefield.replay(predicted_view, self._scorched_started)
            fire.setEnabled(False)
            self._submit_action(action)
        fire.clicked.connect(fire_now)
        box.addWidget(fire)
        replay = QPushButton("Instant replay"); replay.setObjectName("scorchedReplay")
        replay.setEnabled(bool(view.get("impact")))
        def replay_last():
            self._scorched_started = time.monotonic()
            map_scroll.follow_shot = True
            battlefield.replay(started=self._scorched_started)
        replay.clicked.connect(replay_last)
        box.addWidget(replay)
        movement = QHBoxLayout()
        for direction, label in ((0, "◀ Drive"), (1, "Drive ▶")):
            button = QPushButton(label)
            candidate = max(1, min(view["width"] - 2, tank["x"] + (-2 if direction == 0 else 2))) if tank else 0
            other = next((item for item in view["tanks"] if item is not tank), None)
            button.setEnabled(bool(can_play and tank and tank["fuel"] and other and
                                   abs(candidate - other["x"]) >= 6 and candidate != tank["x"]))
            button.clicked.connect(lambda _checked=False, d=direction: self._submit_action(bytes((view["actions"]["drive"], d))))
            movement.addWidget(button)
        box.addLayout(movement)
        repair = QPushButton("Repair +24 HP")
        repair.setEnabled(bool(can_play and tank and tank["repair_kits"] and tank["hp"] < 100))
        repair.clicked.connect(lambda: self._submit_action(bytes((view["actions"]["repair"], 0))))
        shield = QPushButton("Raise shield")
        shield.setEnabled(bool(can_play and tank and not tank["shielded"]))
        shield.clicked.connect(lambda: self._submit_action(bytes((view["actions"]["shield"], 0))))
        box.addWidget(repair); box.addWidget(shield)
        armory_hdr = QLabel("ARMORY · BUYING USES YOUR TURN")
        armory_hdr.setStyleSheet("color: #fbbf24; font-size: 10px; font-weight: 800; letter-spacing: 1.2px; margin-top: 4px;")
        box.addWidget(armory_hdr)
        for item in view["weapons"][1:]:
            buy = QPushButton(f"{item['name']} · ¤{item['cost']}")
            buy.setEnabled(bool(can_play and tank and tank["credits"] >= item["cost"] and tank["ammo"][item["id"]] < 254))
            buy.setToolTip(f"Buy one {item['name']} round · {item['tag']}")
            buy.clicked.connect(lambda _checked=False, i=item["id"]: self._submit_action(bytes((view["actions"]["buy"], i))))
            box.addWidget(buy)
        box.addStretch(1)
        scroll.setWidget(controls); layout.addWidget(scroll, 2)
        if tank:
            map_scroll.horizontalScrollBar().setValue(max(0, int(tank["x"] * scene_scale - map_scroll.viewport().width() / 2)))
        self.board_layout.addWidget(host)
        if tank:
            def initial_camera():
                map_scroll.horizontalScrollBar().setValue(max(0, int(tank["x"] * scene_scale - map_scroll.viewport().width() / 2)))
                map_scroll.verticalScrollBar().setValue(max(0, int((view["sky_height"] - tank["world_y"]) * scene_scale - map_scroll.viewport().height() * .65)))
            QTimer.singleShot(0, map_scroll, initial_camera)
        preview()

    def _refresh_chat(self, sid: str | None):
        if not self.engine or not sid or sid != self.selected_id:
            return
        self.chat_list.clear()
        try: history = self.engine.chat_history(sid)
        except Exception: return
        for message in history:
            item = QListWidgetItem(f"{message['sender']}  ·  {message['delivery']}\n{message['body']}")
            item.setToolTip(message['body'])
            self.chat_list.addItem(item)
            mine = message['sender'] == self.settings.callsign
            row = QWidget(); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(2, 4, 2, 4)
            bubble = QLabel(f"{message['sender']} · {message['delivery']}\n{message['body']}")
            bubble.setTextFormat(Qt.TextFormat.PlainText); bubble.setWordWrap(True)
            bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if mine:
                bubble.setStyleSheet("padding:10px 14px; border-radius:14px; color:#ffffff; background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0f766e, stop:1 #042f2e); border: 1px solid #14b8a6; font-size:12px;")
            else:
                bubble.setStyleSheet("padding:10px 14px; border-radius:14px; color:#f1f5f9; background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1e293b, stop:1 #0f172a); border: 1px solid #334155; font-size:12px;")
            natural_width = max(bubble.fontMetrics().horizontalAdvance(line) for line in bubble.text().splitlines()) + 24
            bubble.setProperty("natural_width", natural_width)
            if mine: row_layout.addStretch(1)
            row_layout.addWidget(bubble)
            if not mine: row_layout.addStretch(1)
            self.chat_list.setItemWidget(item, row)
        self._size_chat_rows()
        self.chat_list.scrollToBottom()

    def _size_chat_rows(self):
        # The list item's style adds 10 px on each side; leave room for the
        # row layout margins too, so the bubble never spills into its neighbor.
        available = max(1, self.chat_list.viewport().width() - 28)
        for index in range(self.chat_list.count()):
            item = self.chat_list.item(index)
            row = self.chat_list.itemWidget(item)
            if row is None:
                continue
            bubble = row.findChild(QLabel)
            width = min(available, max(130, bubble.property("natural_width")))
            bubble.setFixedWidth(width)
            height = bubble.heightForWidth(width)
            bubble.setFixedHeight(height)
            # QSS item padding/margin takes 22 px before the row is laid out.
            item.setSizeHint(QSize(available, height + 30))

    def _run(self, fn: Callable, on_done: Callable | None = None):
        task = Task(fn)
        self._tasks.add(task)
        def done(value):
            self._tasks.discard(task)
            if on_done: on_done(value)
            self._refresh_sessions()
        task.signals.done.connect(done)
        def failed(err):
            self._tasks.discard(task)
            self.footer_label.setText(err)
            if self.layout_stack.currentWidget() is self.compact_tabs:
                QMessageBox.warning(self, "Action failed", err)
        task.signals.failed.connect(failed)
        self._pool.start(task)

    def _new_game(self):
        if not self.engine: return
        game = self.game_choice.currentData(); peer = self.peer_input.text().strip().upper() or None
        hosted = self.hosted_checkbox.isChecked()
        self._run(partial(self.engine.create_game, game, None if hosted else peer, hosted=hosted), self._created_game)

    def _join_game(self):
        if self.engine and self.selected_id: self._run(partial(self.engine.join_game, self.selected_id))

    def _announce_game(self):
        if self.engine and self.selected_id: self._run(partial(self.engine.announce_game, self.selected_id))

    def _spot_game(self):
        if not self.engine or not self.selected_id: return
        if not self.settings.dx_cluster_url:
            QMessageBox.information(self, "DX Cluster", "Configure a DX Cluster URL in Settings first."); return
        try:
            session = self.engine.session(self.selected_id)
            build_spot(session, self.settings.callsign, "14070", "Data2G USB")
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, "DX Cluster", str(exc)); return
        dialog = QDialog(self)
        dialog.setWindowTitle("Spot my game · DX Cluster")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        frequency = QLineEdit(); frequency.setPlaceholderText("14070")
        mode = QLineEdit("Data2G USB")
        note = QLineEdit("Open game · stations welcome")
        form.addRow("Dial frequency (kHz)", frequency)
        form.addRow("Mode / transport", mode)
        form.addRow("Message", note)
        layout.addLayout(form)
        publish = QPushButton("Publish spot"); publish.clicked.connect(dialog.accept)
        layout.addWidget(publish)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try:
            payload = build_spot(session, self.settings.callsign, frequency.text(), mode.text(), note.text())
        except ValueError as exc:
            QMessageBox.warning(self, "DX Cluster", str(exc)); return
        self._run(partial(post_spot, self.settings.dx_cluster_url, payload),
                  lambda _: self.footer_label.setText("Game spotted on the DX Cluster"))

    def _set_reply_order(self):
        if self.engine and self.selected_id:
            self._run(partial(self.engine.set_reply_order, self.selected_id, self.order_input.text()))

    def _retry_move(self):
        if self.engine and self.selected_id: self._run(partial(self.engine.resend_pending_action, self.selected_id))

    def _created_game(self, sid: str):
        self._preserve_empty_selection = False
        self.selected_id = sid
        if self.layout_stack.currentWidget() is self.compact_tabs:
            self.compact_tabs.setCurrentIndex(1)

    def _invite_player(self):
        if not self.engine or not self.selected_id: return
        game = self.engine.registry.by_id(self.engine.session(self.selected_id)["game_id"])
        if not self.settings.can_offer_game(game.key):
            self.footer_label.setText(f"Enable Experimental games in settings to invite players to {game.name}.")
            return
        peer = self.peer_input.text().strip().upper()
        if not peer:
            self.footer_label.setText("Enter a callsign to invite to this lobby."); return
        sid = self.selected_id
        self._run(partial(self.engine.invite_player, sid, peer))

    def _start_game(self):
        if self.engine and self.selected_id:
            sid = self.selected_id; self._run(partial(self.engine.start_game, sid))

    def _cancel_invitation(self):
        if not self.engine or not self.selected_id: return
        peer = self.peer_input.text().strip().upper()
        if not peer:
            self.footer_label.setText("Enter the pending station callsign to cancel its invitation."); return
        sid = self.selected_id
        self._run(partial(self.engine.cancel_invitation, sid, peer))

    def _accept_invite(self):
        if self.engine and self.selected_id:
            sid = self.selected_id; self._run(partial(self.engine.accept_invite, sid))

    def _submit_action(self, action: bytes):
        if self.engine and self.selected_id:
            session = self.engine.session(self.selected_id)
            if session.get("sync_requested_at", 0) > session.get("sync_at", 0) or any(word in (session.get("error") or "").lower() for word in ("missed moves", "state conflict", "awaiting snapshot")):
                self.footer_label.setText("Resync this table before sending your move.")
                return
            sid = self.selected_id; self._run(partial(self.engine.legal_action, sid, action))

    def _submit_hex_action(self, text: str):
        try: action = bytes.fromhex(text.strip())
        except ValueError:
            self.footer_label.setText("Enter a valid hexadecimal game action."); return
        self._submit_action(action)

    def _word_state(self, session: dict):
        game = self.engine.registry.by_id(session["game_id"])
        state = game.decode_state(session["state"])
        game.validate_roster(state, tuple(session["players"]))
        return state

    def _submit_word_placement(self, session: dict, start: str, direction: str, word: str):
        try:
            action = make_placement(self._word_state(session), session["current_player"], start, direction, word)
            self._submit_action(action)
        except Exception as exc:
            self.footer_label.setText(str(exc))

    def _submit_word_exchange(self, session: dict, tiles: str):
        try:
            action = make_exchange(self._word_state(session), session["current_player"], tiles)
            self._submit_action(action)
        except Exception as exc:
            self.footer_label.setText(str(exc))

    def _send_chat(self):
        text = self.chat_input.text().strip()
        if not self.engine or not self.selected_id or not text: return
        sid = self.selected_id
        def sent(_seq):
            if self.selected_id == sid and self.chat_input.text().strip() == text:
                self.chat_input.clear()
            self._refresh_chat(sid)
        self._run(partial(self.engine.send_chat, sid, text), sent)

    def _connect_peer(self):
        if not self.engine: return
        peer = self.peer_input.text().strip().upper()
        if not peer:
            self.footer_label.setText("Enter a callsign to connect or resume."); return
        self._run(partial(self.engine.connect_peer, peer))

    def _disconnect_peer(self):
        if self.engine: self._run(self.engine.disconnect)

    def _sync_selected(self):
        if self.engine and self.selected_id:
            if self.settings.observer_mode and QMessageBox.question(self, "Transmit snapshot request",
                    "This will transmit one synchronization request over the radio. Continue?") != QMessageBox.StandardButton.Yes:
                return
            sid = self.selected_id; self._run(partial(self.engine.request_sync, sid))

    def _edit_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.connect_button.setVisible(self.settings.transport == "connected")
            self.disconnect_button.setVisible(self.settings.transport == "connected")
            if self.settings.transport != "broadcast":
                self.hosted_checkbox.setChecked(False)
            self._update_new_game_mode()
            self._update_cq_visibility()
            self._populate_games()
            self._apply_style()
            self.connect_service()

    def closeEvent(self, event):
        if self.unsubscribe: self.unsubscribe()
        if self.engine: self.engine.close()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description="Radio Arcade desktop client")
    parser.add_argument("--callsign")
    parser.add_argument("--host")
    parser.add_argument("--transport", choices=("broadcast", "connected", "practice"))
    parser.add_argument("--observer", action="store_true", help="passively watch KISS broadcast games")
    args, qt_args = parser.parse_known_args()
    settings = Settings.load()
    if args.callsign: settings.callsign = args.callsign.upper()
    if args.host: settings.host = args.host
    if args.transport: settings.transport = args.transport
    if args.observer: settings.observer_mode = True
    app = QApplication([sys.argv[0], *qt_args]); app.setApplicationName("Radio Arcade")
    window = ArcadeWindow(settings)
    if not settings.callsign:
        dialog = SettingsDialog(settings, window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        # The first-run dialog edits settings before a service exists. Refresh
        # local choices now so offline startup still reflects the checkbox.
        window._populate_games()
    window.show(); window.connect_service()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
