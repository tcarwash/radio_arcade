"""Local camera controls for the desktop artillery battlefield."""
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QScrollArea, QWidget


class BattleMapScroll(QScrollArea):
    manual_pan = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().installEventFilter(self)
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        self._drag = None
        self.follow_shot = True
        self.manual_pan.connect(self._stop_following)

    def _stop_following(self):
        self.follow_shot = False

    def setWidget(self, widget):
        super().setWidget(widget)
        widget.installEventFilter(self)
        widget.setCursor(Qt.CursorShape.OpenHandCursor)
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def center_fraction(self, x, y=None):
        if not self.widget():
            return
        self.manual_pan.emit()
        self.horizontalScrollBar().setValue(round(x * self.widget().width() - self.viewport().width() / 2))
        if y is not None:
            self.verticalScrollBar().setValue(round(y * self.widget().height() - self.viewport().height() / 2))

    def keyPressEvent(self, event):
        directions = {Qt.Key.Key_A: (-1, 0), Qt.Key.Key_D: (1, 0),
                      Qt.Key.Key_W: (0, -1), Qt.Key.Key_S: (0, 1),
                      Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                      Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if event.key() in directions and not event.modifiers():
            dx, dy = directions[event.key()]
            self.manual_pan.emit()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + dx * max(32, self.viewport().width() // 8))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + dy * max(32, self.viewport().height() // 8))
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.KeyPress:
            self.keyPressEvent(event)
            return event.isAccepted()
        if kind == QEvent.Type.Wheel:
            self.manual_pan.emit()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.manual_pan.emit()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._drag = (event.globalPosition(), self.horizontalScrollBar().value(), self.verticalScrollBar().value(), False)
            watched.setCursor(Qt.CursorShape.ClosedHandCursor)
            return True
        if kind == QEvent.Type.MouseMove and self._drag:
            origin, x, y, moved = self._drag
            delta = event.globalPosition() - origin
            moved = moved or abs(delta.x()) + abs(delta.y()) > 4
            self._drag = (origin, x, y, moved)
            self.horizontalScrollBar().setValue(x - round(delta.x()))
            self.verticalScrollBar().setValue(y - round(delta.y()))
            return True
        if kind == QEvent.Type.MouseButtonRelease and self._drag:
            _, _, _, moved = self._drag
            self._drag = None
            watched.setCursor(Qt.CursorShape.OpenHandCursor)
            if not moved and self.widget():
                point = event.position()
                if watched is self.viewport():
                    point += QPointF(self.horizontalScrollBar().value(), self.verticalScrollBar().value())
                self.center_fraction(point.x() / self.widget().width(), point.y() / self.widget().height())
            return True
        return super().eventFilter(watched, event)


class ScorchedMinimap(QWidget):
    def __init__(self, view, camera, parent=None):
        super().__init__(parent)
        self.view, self.camera = view, camera
        self.setObjectName('scorchedMinimap')
        self.setFixedHeight(72)
        self.setMinimumWidth(160)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip('Click or drag to scout · WASD / arrow keys pan the battlefield')
        for bar in (camera.horizontalScrollBar(), camera.verticalScrollBar()):
            bar.valueChanged.connect(self.update)
            bar.rangeChanged.connect(self.update)
        camera.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.update()
        return False

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#102033'))
        sx, sy = self.width() / self.view['width'], self.height() / self.view['sky_height']
        ground = QPolygonF([QPointF(0, self.height())] +
                           [QPointF((x + .5) * sx, self.height() - h * sy) for x, h in enumerate(self.view['terrain'])] +
                           [QPointF(self.width(), self.height())])
        painter.setPen(QPen(QColor('#b6d09c'), 1)); painter.setBrush(QColor('#668a72')); painter.drawPolygon(ground)
        for index, tank in enumerate(self.view['tanks']):
            painter.setBrush(QColor('#72e9c7' if index == 0 else '#ffbb70'))
            painter.drawEllipse(QPointF(tank['x'] * sx, self.height() - tank['world_y'] * sy), 3, 3)
        if self.camera.widget():
            world = self.camera.widget(); viewport = self.camera.viewport()
            x = self.camera.horizontalScrollBar().value() / world.width() * self.width()
            y = self.camera.verticalScrollBar().value() / world.height() * self.height()
            width = min(self.width(), viewport.width() / world.width() * self.width())
            height = min(self.height(), viewport.height() / world.height() * self.height())
            painter.setPen(QPen(QColor('#a8f9e2'), 2)); painter.setBrush(QColor(139, 230, 211, 35))
            painter.drawRect(QRectF(x, y, width, height))

    def _pan(self, event):
        self.camera.center_fraction(max(0, min(1, event.position().x() / self.width())),
                                    max(0, min(1, event.position().y() / self.height())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(); self._pan(event); event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pan(event); event.accept()

    def keyPressEvent(self, event):
        self.camera.keyPressEvent(event)
