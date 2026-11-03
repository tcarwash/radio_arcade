"""Local Hexxagon presentation effects; never part of a radio action."""
from math import sin, pi
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class HexLightning(QWidget):
    def __init__(self, board, before, after):
        super().__init__(board)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(board.rect())
        self.age = 0
        self.before, self.after = before, after
        self.target = next((i for i, (a, b) in enumerate(zip(before, after)) if not a['owner'] and b['owner']), None)
        self.captured = [i for i, (a, b) in enumerate(zip(before, after)) if a['owner'] and b['owner'] and a['owner'] != b['owner']]
        self.origin = next((i for i, (a, b) in enumerate(zip(before, after)) if a['owner'] and not b['owner']), None)
        self.timer = QTimer(self); self.timer.setInterval(35); self.timer.timeout.connect(self.advance)
        self.show(); self.raise_(); self.timer.start()

    def advance(self):
        self.age += 35
        if self.age >= 950:
            self.timer.stop(); self.deleteLater()
        else:
            self.update()

    def center(self, i):
        cell = self.after[i]
        return QPointF(172 + 34 * (cell['q'] + cell['r'] / 2), 154 + 30 * cell['r'])

    def paintEvent(self, event):
        if self.target is None: return
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.age / 950
        painter.setOpacity(max(0, 1 - t ** 3))
        center = self.center(self.target)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor('#bdfaff'), 2))
        radius = 8 + t * 32
        painter.drawEllipse(center, radius, radius)
        if self.origin is not None and t < .35:
            start = self.center(self.origin); f = t / .35
            pos = start * (1-f) + center * f + QPointF(0, -sin(f*pi)*20)
            painter.setBrush(QColor('#e6fcff')); painter.drawEllipse(pos, 10, 10)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        for index in self.captured:
            end = self.center(index)
            path = QPainterPath(center)
            for step in range(1, 9):
                f = step / 8
                jitter = sin(step * 7 + self.age * .23) * 5 if step < 8 else 0
                point = center * (1-f) + end * f
                path.lineTo(point + QPointF(jitter, -jitter))
            painter.setPen(QPen(QColor('#5e7cff'), 5)); painter.drawPath(path)
            painter.setPen(QPen(QColor('#e5ffff'), 1.5)); painter.drawPath(path)
            painter.setPen(QPen(QColor('#dbfaff'), 2)); painter.drawEllipse(end, 13 + sin(t*pi)*5, 13 + sin(t*pi)*5)
