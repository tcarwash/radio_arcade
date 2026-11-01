"""Exercise camera input through Qt events without invoking any game actions."""
import importlib.util
import os
import unittest


@unittest.skipUnless(importlib.util.find_spec('PySide6'), 'desktop extra required')
class ScorchedNavigationTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication, QWidget
        from radio_arcade.scorched_navigation import BattleMapScroll, ScorchedMinimap
        self.app = QApplication.instance() or QApplication([])
        self.camera = BattleMapScroll()
        self.world = QWidget(); self.world.setFixedSize(1792, 560)
        self.camera.setWidget(self.world); self.camera.resize(400, 200); self.camera.show()
        self.mini = ScorchedMinimap({'width': 128, 'sky_height': 40, 'terrain': [8] * 128,
                                    'tanks': [{'x': 20, 'world_y': 8.65}, {'x': 110, 'world_y': 8.65}]}, self.camera)
        self.mini.resize(240, 72); self.mini.show(); self.app.processEvents()

    def tearDown(self):
        self.mini.close(); self.camera.close(); self.app.processEvents()

    def test_wasd_and_arrows_pan_only_the_camera(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.camera.horizontalScrollBar().setValue(300)
        QTest.keyClick(self.world, Qt.Key.Key_D)
        right = self.camera.horizontalScrollBar().value()
        self.assertGreater(right, 300)
        self.assertFalse(self.camera.follow_shot)
        QTest.keyClick(self.world, Qt.Key.Key_A)
        self.assertEqual(self.camera.horizontalScrollBar().value(), 300)
        QTest.keyClick(self.mini, Qt.Key.Key_S)
        self.assertGreater(self.camera.verticalScrollBar().value(), 0)
        QTest.keyClick(self.mini, Qt.Key.Key_W)
        self.assertEqual(self.camera.verticalScrollBar().value(), 0)
        QTest.keyClick(self.world, Qt.Key.Key_Right)
        self.assertEqual(self.camera.horizontalScrollBar().value(), right)

    def test_minimap_click_drag_and_main_map_drag(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        QTest.mouseClick(self.mini, Qt.MouseButton.LeftButton, pos=QPoint(235, 36))
        self.assertEqual(self.camera.horizontalScrollBar().value(), self.camera.horizontalScrollBar().maximum())
        QTest.mousePress(self.mini, Qt.MouseButton.LeftButton, pos=QPoint(120, 36))
        QTest.mouseMove(self.mini, QPoint(5, 36))
        QTest.mouseRelease(self.mini, Qt.MouseButton.LeftButton, pos=QPoint(5, 36))
        self.assertEqual(self.camera.horizontalScrollBar().value(), 0)
        self.camera.horizontalScrollBar().setValue(300)
        viewport = self.camera.viewport()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=QPoint(180, 70))
        QTest.mouseMove(viewport, QPoint(80, 70))
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=QPoint(80, 70))
        self.assertEqual(self.camera.horizontalScrollBar().value(), 400)
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=QPoint(20, 70))
        self.assertLess(self.camera.horizontalScrollBar().value(), 400)

    def test_chat_typing_does_not_pan(self):
        from PySide6.QtWidgets import QLineEdit
        from PySide6.QtTest import QTest
        field = QLineEdit(); field.show(); field.setFocus()
        self.camera.horizontalScrollBar().setValue(300)
        QTest.keyClicks(field, 'wasd')
        self.assertEqual(field.text(), 'wasd')
        self.assertEqual(self.camera.horizontalScrollBar().value(), 300)
        field.close()
