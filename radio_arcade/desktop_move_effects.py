"""Noninteractive paint overlay for the latest move (never changes game state)."""
from PySide6.QtCore import Qt, QTimer, QPoint, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import QWidget


class MoveRipple(QWidget):
    def __init__(self, viewport, target, label, outcome="move"):
        super().__init__(viewport)
        self.setObjectName("moveRipple")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground,True)
        self.center=target.mapTo(viewport,target.rect().center()) if target else QPoint(viewport.width()//2,viewport.height()//2)
        self.label=label
        self.outcome=outcome
        self.frame=0
        self.setGeometry(viewport.rect())
        self.raise_();self.show()
        self._timer=QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._advance)
        self._timer.start()

    def _advance(self):
        self.frame+=1
        if self.frame>=44:
            self._timer.stop()
            self.deleteLater()
        else:self.update()

    def paintEvent(self,event):
        p=QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t=self.frame/44
        color=QColor("#ffffff" if self.outcome=="miss" else "#ff677b" if self.outcome in ("hit","sunk") else "#00f5c4")
        # A submerged target gets two white rings; a hit gets a red flash.
        for i in range(3):
            v=(t-i*.16)
            if not 0<=v<=1:continue
            c=QColor(color);c.setAlpha(int(210*(1-v)**1.6))
            p.setPen(QPen(c,3.3*(1-v)+.7))
            p.setBrush(Qt.BrushStyle.NoBrush)
            radius=8+v*(86 if self.outcome in ("hit","sunk","miss") else 68)
            p.drawEllipse(QPointF(self.center),float(radius),float(radius)*.72)
        if t<.9 and self.label:
            # High-contrast, transient message; continue to show the actual board.
            text=self.label[:100]
            fm=p.fontMetrics()
            width=min(self.width()-20,max(180,fm.horizontalAdvance(text)+24))
            if width>60:
                x=max(6,min(self.center.x()-width/2,self.width()-width-6))
                y=max(8,min(self.center.y()-105,self.height()-48))
                rect=(int(x),int(y),int(width),34)
                bg=QColor("#081827");bg.setAlpha(int(228*(1-t)**.4))
                p.setPen(QPen(QColor("#4c8094"),1));p.setBrush(bg)
                p.drawRoundedRect(*rect,8,8)
                p.setPen(QColor("#f7fbff"))
                p.drawText(rect[0]+8,rect[1],rect[2]-16,rect[3],Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,text)
        p.end()
