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

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QSize, Qt, QThread, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QRadialGradient, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSlider, QSplitter, QStackedWidget, QStyle, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

from .presentation import CompletionTracker, scoreboard
from .desktop_results import Scoreboard, ResultDialog
from .scorched_navigation import BattleMapScroll, ScorchedMinimap
from .service import ArcadeService, EngineEvent, station_name
from .settings import Settings
from .store import SessionStore
from .transport_factory import create_service, group_name
from .games.scrabble import make_exchange, make_placement
from .dx_cluster import build_spot, post_spot
from .games.scorched_earth import _trace_shot, _wind, pack_shot


class ChatListWidget(QListWidget):
    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


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
        painter.fillRect(self.rect(), QColor("#121a26"))
        margin = 12
        world_width, world_height = view["width"], view["sky_height"]
        scale = min((self.width() - 2 * margin) / world_width, (self.height() - 2 * margin) / world_height)
        ox = (self.width() - world_width * scale) / 2
        oy = (self.height() - world_height * scale) / 2
        painter.translate(ox, oy)
        painter.scale(scale, scale)
        sky = QLinearGradient(0, 0, 0, world_height)
        sky.setColorAt(0, QColor("#091525")); sky.setColorAt(1, QColor("#28445b"))
        painter.fillRect(0, 0, world_width, world_height, QBrush(sky))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(132, 173, 188, 28))
        distant = QPolygonF([QPointF(0, world_height)] +
                            [QPointF(x, world_height - 9 + ((x * 13 + 7) % 9) / 3) for x in range(world_width + 1)] +
                            [QPointF(world_width, world_height), QPointF(0, world_height)])
        painter.drawPolygon(distant)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(217, 232, 251, 95))
        for i in range(46):
            painter.drawEllipse(QPointF(((i * 187 + 43) % (world_width * 24)) / 24,
                                        ((i * 79 + 17) % (world_height * 12)) / 24), .035 if i % 5 else .07,
                                .035 if i % 5 else .07)
        terrain = QPolygonF([QPointF(0, world_height)] +
                            [QPointF(x + .5, world_height - height) for x, height in enumerate(self.displayed_terrain())] +
                            [QPointF(world_width, world_height)])
        earth = QLinearGradient(0, world_height - 15, 0, world_height)
        earth.setColorAt(0, QColor("#62825c")); earth.setColorAt(1, QColor("#293b35"))
        painter.setBrush(QBrush(earth))
        painter.setPen(QPen(QColor("#91bd89"), .11))
        painter.drawPolygon(terrain)
        def draw_path(points, color, dotted=False):
            if len(points) < 2:
                return
            path = QPainterPath(QPointF(points[0][0], world_height - points[0][1]))
            for x, y in points[1:]:
                path.lineTo(x, world_height - y)
            pen = QPen(QColor(color), .09 if dotted else .12)
            if dotted:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawPath(path)
        trajectory = view.get("trajectory", [])
        if self.flight_progress < 1 and trajectory:
            position = self.flight_progress * (len(trajectory) - 1)
            index = min(len(trajectory) - 2, int(position))
            fraction = position - index
            start = trajectory[index]; end = trajectory[index + 1]
            location = (start[0] + (end[0] - start[0]) * fraction,
                        start[1] + (end[1] - start[1]) * fraction)
            draw_path(trajectory[:index + 1] + [location], "#ffdb8c")
            painter.setPen(QPen(QColor("#ffb55d"), .23))
            painter.setBrush(QColor("#fff5c1"))
            painter.drawEllipse(QPointF(location[0], world_height - location[1]), .18, .18)
        draw_path([(x / 1000, y / 1000) for x, y in self.preview], "#dceeff", True)
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
                painter.setPen(QPen(QColor("#74d9fb"), .12))
                painter.setBrush(QColor(91, 197, 241, 35))
                painter.drawEllipse(QPointF(x, y), 1.0, .8)
            painter.setPen(QPen(QColor("#142333"), .11))
            painter.setBrush(QColor("#76e5c8" if index == 0 else "#f3b476"))
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
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Sans Serif", 7))
            painter.save(); painter.scale(1 / scale, 1 / scale)
            painter.drawText(QPointF(x * scale - 22, (y - .7) * scale), f"{tank['hp']} HP")
            painter.restore()
        painter.end()


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
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
        form_host = QWidget()
        form = QFormLayout(form_host)
        if parent and parent.width() < 500:
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.callsign = QLineEdit(settings.callsign)
        self.callsign.setPlaceholderText("N0CALL")
        self.host = QLineEdit(settings.host)
        self.transport = QComboBox()
        self.transport.addItem("Broadcast · KISS", "broadcast")
        self.transport.addItem("Connected · VARA-like", "connected")
        self.transport.addItem("Local practice · play both sides", "practice")
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
        self.experimental_games.setToolTip("Show prototypes when creating a table or calling CQ")
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
        form_scroll = QScrollArea()
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
        self.selected_id: str | None = None
        self._preserve_empty_selection = False
        self._completion_tracker = CompletionTracker()
        self._result_dialog = None
        self._pending_result_id = None
        self._completion_timer = QTimer(self); self._completion_timer.setSingleShot(True)
        self._completion_timer.timeout.connect(self._finish_scorched_result)
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
        self._apply_style()
        self._update_status("Starting up", False)
        self._update_compact_layout()

    def _build_ui(self):
        root = QWidget(); root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root); layout.setContentsMargins(22, 20, 22, 18); layout.setSpacing(16)
        self.root_layout = layout
        top = QHBoxLayout()
        self.brand = QLabel("◈  RADIO ARCADE"); self.brand.setObjectName("brand")
        top.addWidget(self.brand)
        self.transport_label = QLabel("DATA2G · RADIO GAMES"); self.transport_label.setObjectName("eyebrow")
        top.addWidget(self.transport_label); top.addStretch(1)
        self.link_dot = QLabel("●"); self.link_dot.setObjectName("statusDot")
        self.status_label = QLabel("Starting up"); self.status_label.setObjectName("statusText")
        top.addWidget(self.link_dot); top.addWidget(self.status_label)
        self.tx_activity = QLabel(""); self.tx_activity.setObjectName("muted")
        top.addWidget(self.tx_activity)
        self.tx_toggle = QPushButton("TX ENABLED" if self.settings.tx_enabled else "TX DISABLED")
        self.tx_toggle.setCheckable(True); self.tx_toggle.setChecked(self.settings.tx_enabled)
        self.tx_toggle.setToolTip("Allow or block outgoing Data2G traffic")
        self.tx_toggle.toggled.connect(self._set_tx_enabled)
        self.tx_toggle.setEnabled(self.settings.transport != "practice")
        top.addWidget(self.tx_toggle)
        self.settings_button = QPushButton("⚙  Settings"); self.settings_button.clicked.connect(self._edit_settings)
        top.addWidget(self.settings_button)
        layout.addLayout(top)

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
        left_layout.addWidget(self.session_list, 1)
        tools_scroll = QScrollArea(); tools_scroll.setWidgetResizable(True)
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
        self.cq_heading = QLabel("FIND A GAME · CQ")
        self.cq_heading.setObjectName("eyebrow")
        tools_layout.addWidget(self.cq_heading)
        self.cq_choice = QComboBox(); self.cq_choice.setObjectName("field")
        self.cq_choice.addItem("Any game", None)
        tools_layout.addWidget(self.cq_choice)
        self.cq_send = QPushButton("Call CQ to all stations")
        self.cq_send.clicked.connect(self._call_cq)
        tools_layout.addWidget(self.cq_send)
        self.cq_list = QListWidget(); self.cq_list.setMinimumHeight(85)
        self.cq_list.setToolTip("CQ calls heard in the last 15 minutes")
        tools_layout.addWidget(self.cq_list)
        self.cq_answer = QPushButton("Answer selected CQ")
        self.cq_answer.clicked.connect(self._answer_cq)
        tools_layout.addWidget(self.cq_answer)
        self.cq_widgets = (self.cq_heading, self.cq_choice, self.cq_send, self.cq_list, self.cq_answer)
        for control in self.cq_widgets:
            control.setVisible(self.settings.transport == "broadcast" and not self.settings.observer_mode)
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
        tools_layout.addStretch(1)
        left_layout.addWidget(self.start_button)
        self.forfeit_button = QPushButton("Forfeit game…"); self.forfeit_button.setObjectName("forfeitGame")
        self.forfeit_button.clicked.connect(self._forfeit_game); self.forfeit_button.hide()
        left_layout.addWidget(self.forfeit_button)
        self.delete_button = QPushButton("Delete selected game…")
        self.delete_button.clicked.connect(self._delete_game)
        self.delete_button.setEnabled(False)
        self.delete_button.hide()
        left_layout.addWidget(self.delete_button)
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
        self.sync_button.setVisible(False); center_layout.addWidget(self.sync_button, 0, Qt.AlignmentFlag.AlignLeft)
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
        self.compact_tabs.currentChanged.connect(lambda _index: self._size_chat_rows())
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

    def _update_compact_layout(self):
        compact = self.width() < 950
        if compact == (self.layout_stack.currentWidget() is self.compact_tabs):
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
        self.transport_label.setVisible(not compact)
        self.status_label.setVisible(not compact)
        self.tx_activity.setVisible(not compact)
        self.settings_button.setText("⚙" if compact else "⚙  Settings")
        self.settings_button.setToolTip("Station settings")
        self.footer_label.setVisible(not compact)
        self.mode_label.setVisible(not compact)
        for pane, headers in zip(self.panes, self.card_headers):
            pane.layout().setContentsMargins(*( (10, 10, 10, 10) if compact else
                                                (14, 14, 14, 14) if pane is self.panes[0] else
                                                (18, 18, 18, 18) ))
            pane.layout().setSpacing(6 if compact else 8 if pane is self.panes[0] else 13)
            for header in headers:
                header.setVisible(not compact)
        self.session_list.setMinimumHeight(50 if compact else 70)
        self.session_list.setVisible(not compact or self.session_list.count() > 0)
        has_score = bool(self.scoreboard_compact.text())
        self.scoreboard_compact.setVisible(compact and has_score)
        self.scoreboard_host.setVisible(not compact and has_score)

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
            QWidget#root { background:#0b1018; color:#e8edf6; font-family:'Inter','Segoe UI',sans-serif; font-size:14px; }
            QFrame#card { background:#121a26; border:1px solid #263244; border-radius:18px; }
            QLabel#brand { color:#f5f8ff; font-size:22px; font-weight:800; letter-spacing:2px; }
            QLabel#eyebrow { color:#66d9c2; font-size:10px; font-weight:800; letter-spacing:2px; }
            QLabel#cardTitle { color:#f1f4fa; font-size:17px; font-weight:650; }
            QLabel#gameTitle { color:#f1f4fa; font-size:27px; font-weight:750; }
            QLabel#muted { color:#8e9bb0; }
            QLabel#statusText { color:#bcc7d8; font-size:12px; }
            QLabel#statusDot { color:#607086; font-size:17px; }
            QLabel#turnBadge { background:#1b2b36; color:#7be3cf; border:1px solid #2a514d; border-radius:9px; padding:10px; font-weight:750; }
            QLabel#players { background:#0d141f; color:#bdc8d8; border-radius:10px; padding:12px; }
            QLabel#compactScoreboard { background:#14251f; color:#b8f4d7; border-radius:7px; padding:6px; font-size:11px; }
            QLabel { color:#d7dfeb; }
            QPushButton { background:#202c3c; color:#e7edf7; border:1px solid #34445a; border-radius:9px; padding:9px 12px; }
            QPushButton:hover { background:#2b3b50; border-color:#4b6b81; }
            QPushButton#forfeitGame { color:#f3bf7a; border-color:#755b3c; }
            QPushButton:disabled { color:#637086; background:#161f2c; border-color:#252f3e; }
            QPushButton#primaryButton { color:#071411; font-weight:800; background:#67dfc3; border:0; }
            QPushButton#primaryButton:hover { background:#89f0d8; }
            QLineEdit, QComboBox, QSpinBox { background:#0c131e; color:#eaf0f8; border:1px solid #2e3d50; border-radius:8px; padding:9px; selection-background-color:#318b80; }
            QComboBox QAbstractItemView { background:#111a27; color:#eaf0f8; selection-background-color:#256c65; }
            QListWidget { background:transparent; border:0; outline:0; }
            QScrollArea#toolsScroll, QWidget#toolsViewport, QScrollArea#boardScroll, QWidget#boardViewport,
            QWidget#toolsContent, QWidget#boardHost { background:transparent; border:0; }
            QWidget#scorchedHost, QWidget#scorchedMapColumn, QWidget#scorchedControls,
            QWidget#scorchedViewport, QWidget#scorchedControlsViewport,
            QScrollArea#battlefieldScroll, QScrollArea#scorchedControlsScroll { background:#121a26; border:0; }
            QListWidget::item { padding:12px 10px; margin:2px 0; border-radius:9px; color:#abb8ca; }
            QListWidget::item:selected { background:#1c3438; color:#a2f1df; }
            QListWidget::item:hover { background:#1a2533; }
            QListWidget#chatList::item { padding:8px 10px; margin:3px 0; background:#182331; color:#e3edf7; border-radius:8px; }
            QTabWidget#compactTabs::pane { border:0; }
            QTabWidget#compactTabs QTabBar::tab { background:#202c3c; color:#bfcbdc; padding:9px 12px; min-height:20px; border:1px solid #34445a; }
            QTabWidget#compactTabs QTabBar::tab:selected { background:#254b47; color:#effff8; border-color:#66d9c2; }
            QScrollBar:vertical { background:transparent; width:8px; margin:2px; }
            QScrollBar::handle:vertical { background:#34445a; border-radius:4px; min-height:25px; }
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
            QDialog { background:#111a27; }
            QLabel#dialogTitle { font-size:23px; font-weight:750; color:#f1f4fa; }
            QLabel#chatLine { background:#182331; border-radius:8px; padding:7px; }
            QLabel#modeLabel { color:#8796aa; font-size:11px; }
        """)
        if self.settings.color_scheme == "daylight":
            self.setStyleSheet(self.styleSheet() + """
                QWidget#root { background:#eaf0f4; color:#17212d; }
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
        for control in self.cq_widgets:
            control.setVisible(self.settings.transport == "broadcast" and not self.settings.observer_mode)
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
        if not self.engine: return
        current = self.game_choice.currentData()
        self.game_choice.clear()
        for game in self.engine.catalog():
            if "desktop" not in game.clients or not self.settings.can_offer_game(game.key):
                continue
            self.game_choice.addItem(f"{game.name} · {game.min_players}–{game.max_players}", game.key)
        index = self.game_choice.findData(current)
        if index >= 0: self.game_choice.setCurrentIndex(index)
        selected_cq_game = self.cq_choice.currentData()
        self.cq_choice.clear()
        self.cq_choice.addItem("Any game", None)
        for game in self.engine.catalog():
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
        if event.name in ("session_updated", "invitation", "delivery", "session_deleted"):
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
        for session in self.engine.sessions():
            if session["game_id"] not in supported:
                continue
            opponent = (" / ".join(session["players"]) or session["coordinator"]) if self.settings.observer_mode else next((p for p in session["players"] if p != self.settings.callsign), "Open lobby")
            turn = "your move" if session.get("current_player") == self.settings.callsign else session["status"]
            item = QListWidgetItem(f"{session['game']}\n{opponent}  ·  {turn}  ·  {session['id'][-6:]}")
            item.setData(Qt.ItemDataRole.UserRole, session["id"])
            self.session_list.addItem(item)
            if session["id"] == selected:
                self.session_list.setCurrentItem(item)
        self.session_list.blockSignals(False)
        self.delete_button.setEnabled(bool(self.engine and self.engine.sessions()))
        if self.session_list.currentItem():
            self.selected_id = self.session_list.currentItem().data(Qt.ItemDataRole.UserRole)
        elif self.session_list.count() and not self._preserve_empty_selection:
            self.session_list.blockSignals(True)
            self.session_list.setCurrentRow(0)
            self.session_list.blockSignals(False)
            self.selected_id = self.session_list.currentItem().data(Qt.ItemDataRole.UserRole)
        else:
            self.selected_id = None
        self.session_list.verticalScrollBar().setValue(scroll_position)
        self.session_list.setVisible(self.layout_stack.currentWidget() is not self.compact_tabs or
                                     self.session_list.count() > 0)
        self._show_session(self.selected_id)

    def _select_item(self, current, _previous):
        if current:
            self._preserve_empty_selection = False
        self.selected_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._show_session(self.selected_id)
        if current and self.layout_stack.currentWidget() is self.compact_tabs:
            self.compact_tabs.setCurrentIndex(1)

    def _show_session(self, sid: str | None):
        session = None
        if self.engine and sid:
            try: session = self.engine.session(sid)
            except KeyError: return
            signature = (sid, session["status"], session["revision"], session["sequence"],
                         session["pending_action"], tuple(session["players"]), tuple(session["invited"]),
                         repr(session["view"]), repr(session.get("last_move")), session.get("error"), self.settings.transport, self.settings.tx_enabled)
            if signature == self._board_signature:
                return
            self._board_signature = signature
        else:
            self._board_signature = None
        while self.scoreboard_layout.count():
            self.scoreboard_layout.takeAt(0).widget().deleteLater()
        score_visible = bool(session and session.get("view"))
        compact = self.layout_stack.currentWidget() is self.compact_tabs
        self.scoreboard_host.setVisible(score_visible and not compact)
        self.scoreboard_compact.setVisible(score_visible and compact)
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
            self.turn_badge.setText("NO GAME SELECTED"); self.players_label.setText("Stations appear here when a lobby opens.")
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
        self.sync_button.setVisible(self.settings.observer_mode or (session["coordinator"] != self.settings.callsign and session["status"] in ("active", "finished")))
        self.retry_button.setVisible(not self.settings.observer_mode and session.get("hosted", False) and session["pending_action"] is not None)
        if self.settings.observer_mode:
            self.sync_button.setText("↻  Request snapshot (TRANSMITS)")
        if self.settings.observer_mode:
            self.turn_badge.setText("OBSERVING · RX ONLY" if state else "OBSERVING · AWAITING SNAPSHOT")
        elif session["status"] == "invited":
            self.turn_badge.setText("INVITATION RECEIVED")
            self.accept_button.setVisible(True)
        elif session["status"] == "lobby":
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
        elif session["status"] == "cancelled":
            self.turn_badge.setText("INVITATION CANCELLED")
        elif session["status"] == "finished":
            winner = state.get("winner") if state else None
            self.turn_badge.setText(f"{session['forfeited_by']} FORFEITED" if session.get("forfeited_by") else f"{winner} WINS" if winner else "GAME OVER · DRAW")
        elif session["pending_action"]:
            self.turn_badge.setText("MOVE SENT · WAITING FOR COMMIT")
        else:
            self.turn_badge.setText(f"LOCAL PRACTICE · {current}'s turn" if self.settings.transport == "practice" else f"YOUR TURN · {players.index(current)+1} of {len(players)}" if current == self.settings.callsign
                                    else f"{current or 'Game'} is thinking")
        if session.get("error"):
            self.turn_badge.setText(f"MOVE REJECTED · {session['error']}")
        self.players_label.setText("\n".join([*( ("● " if p == current else "○ ") + p for p in players), *("◌ " + p + " · invited" for p in session["invited"])]) or "No stations yet")
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
            board = QWidget(); grid = QGridLayout(board); grid.setSpacing(10)
            my_turn = not self.settings.observer_mode and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active" and not session["pending_action"] and (self.settings.transport == "practice" or self.settings.tx_enabled)
            for cell, value in enumerate(view["cells"]):
                text = value if value.strip() else str(cell + 1)
                button = QPushButton(text); button.setObjectName(f"boardCell{cell}")
                button.setFixedSize(112, 106); button.setEnabled(my_turn and not value.strip())
                button.setStyleSheet("font-size:34px;font-weight:800;color:#eeeeee" if self.settings.color_scheme == "monochrome" else "font-size:34px;font-weight:800;color:#79e4cb" if value == "X" else
                                     "font-size:34px;font-weight:800;color:#f0b56b" if value == "O" else "font-size:23px;color:#a5b6c9")
                button.clicked.connect(lambda _checked=False, i=cell: self._submit_action(bytes((i,))))
                grid.addWidget(button, cell // 3, cell % 3)
            self.board_layout.addWidget(board)
        elif view["kind"] == "hexxagon":
            from .games.hexxagon import pack_move
            from PySide6.QtCore import QPoint
            from PySide6.QtGui import QPolygon, QRegion
            host = QWidget(); box = QVBoxLayout(host)
            hint = QLabel("Select your stone, then a highlighted hex. Solid = clone · dotted = jump.")
            hint.setWordWrap(True); box.addWidget(hint)
            board = QWidget(); board.setFixedSize(350, 310)
            enabled = (not self.settings.observer_mode and session["status"] == "active" and not session["pending_action"]
                       and (self.settings.transport == "practice" or (self.settings.tx_enabled and session["current_player"] == self.settings.callsign)))
            buttons, selected = {}, [None]
            legal = set(map(tuple, view["legal_moves"]))
            def paint_selection():
                for cell in view["cells"]:
                    i, owner = cell["id"], cell["owner"]
                    target = (selected[0], i) in legal
                    color = "#68daca" if owner == 1 else "#b39bfa" if owner == 2 else "#263c50"
                    border = "#ffe295" if target or i == selected[0] else "#496276"
                    style = "dotted" if target and len(pack_move(selected[0], i)) == 2 else "solid"
                    buttons[i].setStyleSheet(f"background:{color};color:{'#0a1624' if owner else '#c8dce9'};border:2px {style} {border};font-weight:bold;font-size:10px;padding:0;")
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
            host = QWidget(); box = QVBoxLayout(host)
            board = QWidget(); grid = QGridLayout(board); grid.setSpacing(4)
            grid.setContentsMargins(0, 0, 0, 0)
            enabled = not self.settings.observer_mode and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active" and not session["pending_action"] and (self.settings.transport == "practice" or self.settings.tx_enabled)
            for col in range(7):
                choose = QPushButton(f"▼ {col+1}"); choose.setFixedSize(54, 40)
                choose.setEnabled(enabled and view["cells"][col] == " ")
                choose.clicked.connect(lambda _checked=False, c=col: self._submit_action(bytes((c,))))
                grid.addWidget(choose, 0, col)
            for row in range(6):
                for col in range(7):
                    mark = view["cells"][row * 7 + col]
                    disc = QLabel("●" if mark != " " else "○")
                    disc.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    color = "#eeeeee" if self.settings.color_scheme == "monochrome" else "#71e5d3" if mark == "X" else "#f4bd76" if mark == "O" else "#52697e"
                    disc.setStyleSheet(f"background:#1a3548;color:{color};font-size:34px;border-radius:6px")
                    disc.setFixedSize(54, 52)
                    grid.addWidget(disc, row + 1, col)
            box.addWidget(board)
            box.addWidget(QLabel("Select a column to drop a disc. Four in a row wins."))
            self.board_layout.addWidget(host)
        elif view["kind"] == "battleship":
            from .games.battleship import FLEET
            host = QWidget(); box = QVBoxLayout(host)
            active = not self.settings.observer_mode and session["status"] == "active" and not session["pending_action"] and (self.settings.transport == "practice" or (self.settings.tx_enabled and session["current_player"] == self.settings.callsign))
            box.addWidget(QLabel(f"{session['current_player']} · {view['phase']}"))
            if view["phase"] == "setup" and not session.get("private_fleet"):
                draft = self._battleship_drafts.setdefault(session["id"], [])
                vertical = QCheckBox("Place vertically")
                box.addWidget(QLabel(f"Place ship {len(draft)+1} of 5 · length {FLEET[len(draft)]}")); box.addWidget(vertical)
                board = QWidget(); grid = QGridLayout(board); grid.setSpacing(2)
                for i in range(100):
                    button = QPushButton("■" if i in sum(draft, []) else "□"); button.setFixedSize(28, 28)
                    def place(_checked=False, cell=i):
                        size=FLEET[len(draft)]; row,col=divmod(cell,10); vert=vertical.isChecked()
                        cells=[(row+j)*10+col if vert else row*10+col+j for j in range(size)]
                        if (vert and row+size>10) or (not vert and col+size>10) or set(cells)&set(sum(draft, [])): return
                        draft.append(cells)
                        if len(draft)==5:
                            self.engine.place_battleship_fleet(session["id"], draft); self._battleship_drafts.pop(session["id"], None)
                        self._board_signature = None
                        self._refresh_sessions()
                    button.clicked.connect(place); grid.addWidget(button,i//10,i%10)
                box.addWidget(board)
            elif view["phase"] == "setup": box.addWidget(QLabel("Fleet saved privately · waiting for both stations"))
            elif view["phase"] == "reply": box.addWidget(QLabel("Sending the result from your private fleet."))
            else:
                if session.get("private_fleet"):
                    own=QWidget(); own_grid=QGridLayout(own); own_cells=set(sum(session["private_fleet"], []))
                    for i in range(100): own_grid.addWidget(QLabel("■" if i in own_cells else "□"),i//10,i%10)
                    box.addWidget(QLabel("Your fleet · private on this station")); box.addWidget(own)
                board=QWidget(); grid=QGridLayout(board); grid.setSpacing(2)
                for i,value in enumerate(view["shots"]):
                    button=QPushButton("·" if value==1 else "✕" if value==2 else f"S{value-3}" if value>=3 else "□");button.setFixedSize(28,28)
                    button.setEnabled(active and value==0);button.clicked.connect(lambda _=False,cell=i:self._submit_action(bytes((cell,))))
                    grid.addWidget(button,i//10,i%10)
                box.addWidget(QLabel("Enemy waters · called coordinates"));box.addWidget(board)
            self.board_layout.addWidget(host)
        elif view["kind"] == "sea-warfare":
            self._render_sea_warfare(session, view)
        elif view["kind"] == "signal-siege":
            from .games.signal_siege import pack_play, pack_draft
            host = QWidget(); box = QVBoxLayout(host); box.setSpacing(14)
            title = QLabel(f"SIGNAL SIEGE   ·   WAVE {view['wave']}/3   ·   CORE {view['boss_hp']}/{view['boss_max_hp']} HP")
            title.setStyleSheet("color:#e5b9ff;font-size:20px;font-weight:800")
            box.addWidget(title)
            box.addWidget(QLabel(f"Interference pulse: {view['pulse_damage']} per station · in {view['rounds_until_pulse']} round(s) · Jam {view['jam']}"))
            actor_call = session["current_player"] if self.settings.transport == "practice" else self.settings.callsign
            active = not self.settings.observer_mode and session["status"] == "active" and not session["pending_action"] and (self.settings.transport == "practice" or (self.settings.tx_enabled and actor_call == session["current_player"]))
            for station in view["stations"]:
                is_mine = not self.settings.observer_mode and station["callsign"] == actor_call
                description = (f"{station['callsign']}    ♥ {station['hp']}/18     ◈ {station['shield']} shield     ✦ {station['focus']} focus     "
                               f"▤ {station['deck_size']} cards" + ("   YOUR HAND" if is_mine else "   ▤ ▤ ▤"))
                row = QLabel(description); row.setStyleSheet("color:#aee7d7;font-size:12px" if is_mine else "color:#a9b8ce;font-size:12px")
                box.addWidget(row)
            if view["phase"] == "draft":
                box.addWidget(QLabel(f"{session['current_player']} chooses one upgrade for their deck:"))
                row = QHBoxLayout()
                for reward in view["rewards"]:
                    button = QPushButton(f"{reward['name']}\n{reward['description']}\nADD TO DECK +")
                    button.setMinimumSize(150, 115); button.setEnabled(active)
                    button.setStyleSheet("font-size:13px;font-weight:700;color:#f0e5ff;background:#302a4a;border:1px solid #8c79bf;border-radius:12px")
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
                        button.setStyleSheet("font-size:13px;font-weight:700;color:#dbf7ee;background:#194043;border:1px solid #428b81;border-radius:12px")
                        button.clicked.connect(lambda _checked=False, i=card["slot"]: self._submit_action(pack_play(i, chooser.currentData())))
                        row.addWidget(button)
                    box.addLayout(row)
                else:
                    box.addWidget(QLabel("Waiting for another station. Their hand remains face down."))
            else:
                box.addWidget(QLabel(f"MISSION {view['result'].upper()}"))
            self.board_layout.addWidget(host)
        elif view["kind"] == "piles":
            host = QWidget(); box = QVBoxLayout(host)
            for index, count in enumerate(view["piles"]):
                row = QHBoxLayout(); label = QLabel(f"PILE {index+1:02d}"); label.setObjectName("eyebrow")
                row.addWidget(label); row.addStretch(1)
                for _ in range(count):
                    pebble = QLabel("◆"); pebble.setStyleSheet("color:#eeeeee;font-size:20px" if self.settings.color_scheme == "monochrome" else "color:#66d9c2;font-size:20px")
                    row.addWidget(pebble)
                row.addWidget(QLabel(str(count))); box.addLayout(row)
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
            take.setEnabled(not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled) and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active" and not session["pending_action"])
            take.clicked.connect(lambda: self._submit_action(bytes((pile.currentIndex(), amount.value()))))
            controls.addWidget(pile); controls.addWidget(amount); controls.addWidget(take); box.addLayout(controls)
            self.board_layout.addWidget(host)
        elif view["kind"] == "word-tiles":
            host = QWidget(); box = QVBoxLayout(host); box.setContentsMargins(0, 0, 0, 0)
            current = session["current_player"]
            box.addWidget(QLabel(f"BAG {view['bag']}     {view['lexicon']} · OPEN RACKS"))
            grid_host = QWidget(); grid = QGridLayout(grid_host); grid.setSpacing(2); grid.setContentsMargins(0, 4, 0, 4)
            grid.addWidget(QLabel("   "), 0, 0)
            for x in range(15): grid.addWidget(QLabel(chr(65 + x)), 0, x + 1, alignment=Qt.AlignmentFlag.AlignCenter)
            premium_colors = {"TW": "#6e3341", "DW": "#684d52", "TL": "#315971", "DL": "#38525d", "..": "#263746"}
            for y in range(15):
                grid.addWidget(QLabel(str(y + 1)), y + 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)
                for x in range(15):
                    index = y * 15 + x
                    tile = view["cells"][index]
                    text = tile or ("★" if index == 112 else view["premiums"][index])
                    button = QPushButton(text)
                    button.setObjectName(f"wordCell{index}")
                    button.setFixedSize(28, 27)
                    color = "#eeeeee" if tile else ("#343a40" if self.settings.color_scheme == "monochrome" else premium_colors[view["premiums"][index]])
                    foreground = "#111111" if tile else ("#eeeeee" if self.settings.color_scheme == "monochrome" else "#d8e3ed")
                    button.setStyleSheet(f"QPushButton {{ padding:0; font-size:10px; font-weight:700; background:{color}; color:{foreground}; border:1px solid #182635; }}")
                    # Clicking a square chooses the start coordinate; this is
                    # local UI selection and never transmits a move.
                    button.clicked.connect(lambda _checked=False, x=x, y=y: start.setText(f"{chr(65 + x)}{y + 1}"))
                    grid.addWidget(button, y + 1, x + 1)
            box.addWidget(grid_host, alignment=Qt.AlignmentFlag.AlignHCenter)
            for player, rack in view["racks"].items():
                box.addWidget(QLabel(f"{player} RACK  " + "  ".join(rack)))
            enabled = not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled) and (self.settings.transport == "practice" or current == self.settings.callsign) and session["status"] == "active" and not session["pending_action"]
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
            send.setEnabled(not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled) and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active")
            send.clicked.connect(lambda: self._submit_hex_action(raw.text()))
            box.addWidget(raw); box.addWidget(send); self.board_layout.addWidget(panel)

    def _render_sea_warfare(self, session: dict, view: dict):
        host = QWidget(); box = QVBoxLayout(host); box.setSpacing(7)
        active = (not self.settings.observer_mode and session["status"] == "active"
                  and not session["pending_action"]
                  and (self.settings.transport == "practice" or
                       (self.settings.tx_enabled and session["current_player"] == self.settings.callsign)))
        rules = QLabel("Carrier fighter: one 50% block roll · Battleship: one adjacent double shot · "
                       "Cruiser: one free move · Destroyer: disarms one mine · "
                       "Submarine: one sonar row · Patrol boat: optional hidden mine")
        rules.setWordWrap(True); box.addWidget(rules)
        box.addWidget(QLabel(f"{session['current_player']} · {view['phase']}"))

        def refresh_board():
            self._board_signature = None
            self._show_session(session["id"])

        def grid_widget(cells, enabled=False, handler=None, marks=None, fleet=None):
            board = QWidget(); grid = QGridLayout(board); grid.setSpacing(2); grid.setContentsMargins(0, 0, 0, 0)
            for index in range(100):
                mark = (marks or {}).get(index)
                button = QPushButton("■" if index in cells else "□"); button.setFixedSize(27, 27)
                button.setEnabled(enabled and handler is not None)
                if handler is not None:
                    button.clicked.connect(lambda _=False, cell=index: handler(cell))
                grid.addWidget(button, index // 10, index % 10)
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
                width, height = (27, length*27+2*(length-1)) if vertical_ship else (length*27+2*(length-1), 27)
                image = QLabel(); image.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                image.setAlignment(Qt.AlignmentFlag.AlignCenter)
                image.setPixmap(sprite.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
                grid.addWidget(image, row, col, length if vertical_ship else 1,
                               1 if vertical_ship else length)
            for index, mark in (marks or {}).items():
                token=QLabel(mark);token.setAlignment(Qt.AlignmentFlag.AlignCenter)
                token.setStyleSheet("color:#ffd080;font-size:17px;font-weight:800;background:transparent")
                token.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                grid.addWidget(token,index//10,index%10)
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
                box.addWidget(QLabel(f"Place vessel {index+1} of 6 · length {size}")); box.addWidget(vertical)
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
                box.addWidget(grid_widget(occupied, active, place_ship, fleet=ships))
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
                    marks={} if draft["mine"] is None else {draft["mine"]:"✹"}, fleet=ships))
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
                box.addWidget(grid_widget(occupied, active, place_decoy, marks=setup_marks, fleet=ships))
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
            box.addWidget(grid_widget(own, marks=own_marks, fleet=session.get("private_fleet")))

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

            box.addWidget(QLabel("Enemy waters · hits do not identify sunk ships"))
            enemy = QWidget(); grid = QGridLayout(enemy); grid.setSpacing(2)
            for cell, value in enumerate(view["shots"]):
                row, col = divmod(cell, 10)
                button = QPushButton("·" if value == 1 else "×" if value == 2 else "□"); button.setFixedSize(27, 27)
                def fire(_=False, cell=cell):
                    action_mode = mode.currentData()
                    if action_mode == "sonar": return
                    if action_mode == "double":
                        if cell % 10 > 8 or view["shots"][cell+1] != 0: return
                        code = 100 + (cell//10)*9 + cell%10
                    else: code = cell
                    self._submit_action(bytes((code,)))
                button.clicked.connect(fire); grid.addWidget(button, row, col); enemy_buttons.append(button)
            update_mode()
            box.addWidget(enemy)
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
        box.addWidget(QLabel(f"{view['map_name']} · turn {view['round'] + 1}"))
        wind = view["wind"]
        box.addWidget(QLabel(f"Wind {wind['direction']} {wind['label']}"))
        can_play = (not self.settings.observer_mode and session["status"] == "active"
                    and not session["pending_action"] and
                    (self.settings.transport == "practice" or
                     (self.settings.tx_enabled and session["current_player"] == self.settings.callsign)))
        actor = session["current_player"] if self.settings.transport == "practice" else self.settings.callsign
        tank = next((item for item in view["tanks"] if item["player"] == actor), None)
        for item in view["tanks"]:
            box.addWidget(QLabel(f"{item['player']}: ¤{item['credits']} · "
                                 f"{item['fuel']} fuel · {item['repair_kits']} repairs"))
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
        angle = QSlider(Qt.Orientation.Horizontal); angle.setObjectName("scorchedAngle")
        angle.setRange(view["min_angle"], view["max_angle"]); angle.setValue(45)
        box.addWidget(angle_label); box.addWidget(angle)
        power_label = QLabel("Power: 100%")
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
        box.addWidget(QLabel("ARMORY · buying uses your turn"))
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
            bubble.setStyleSheet("padding:10px; border-radius:14px; color:#eef7ff; background:" + ("#425391;" if mine else "#254b47;"))
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
    window.show(); window.connect_service()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
