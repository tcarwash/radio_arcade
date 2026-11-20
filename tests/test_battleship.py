"""Classic Battleship rules, private fleet projection and local AI regression tests."""
import os
import tempfile
import unittest
from unittest.mock import patch

from radio_arcade.games.battleship import BattleshipGame, FLEET
from radio_arcade.practice import PracticeService
from radio_arcade.presentation import scoreboard
from tests import test_sessions

FLEET_A = [[0,1,2,3,4], [10,11,12,13], [20,21,22], [30,31,32], [40,41]]
FLEET_B = [[50,51,52,53,54], [60,61,62,63], [70,71,72], [80,81,82], [90,91]]

class BattleshipRules(unittest.TestCase):
    def setUp(self):
        self.game = BattleshipGame()
        self.players = ("N0AAA", "N0BBB")
        self.state = self.game.new_state(self.players)
        self.move(0, 127)
        self.move(1, 127)

    def move(self, seat, action):
        self.state = self.game.apply_action(self.state, self.players, self.players[seat], bytes([action]))
        self.assertEqual(self.state, self.game.decode_state(self.game.encode_state(self.state)))

    def test_turns_alternate_after_hit_and_miss(self):
        self.move(0, 0); self.move(1, 128)
        self.assertEqual(self.state["turn"], 1)
        self.move(1, 1); self.move(0, 129)
        self.assertEqual(self.state["turn"], 0)
        self.assertEqual(self.state["shots"][0][0], 1)
        self.assertEqual(self.state["shots"][1][1], 2)
        with self.assertRaises(ValueError):
            self.move(0, 0)

    def test_snapshot_winner_is_the_shooter(self):
        for ship_id in range(len(FLEET)):
            self.move(0, ship_id)
            self.move(1, 130 + ship_id)
            if ship_id != len(FLEET)-1:
                self.move(1, ship_id)
                self.move(0, 128)
        self.assertTrue(self.state["finished"])
        self.assertEqual(self.state["winner"], 0)
        self.assertEqual(self.game.view_model(self.state, self.players)["winner"], self.players[0])
        self.assertIsNone(self.game.current_player(self.state, self.players))

class BattleshipPractice(unittest.TestCase):
    def test_fleets_and_turns(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"RADIO_ARCADE_HOME": tmp}), patch("radio_arcade.practice.random_fleet", return_value=FLEET_B):
            engine = PracticeService("N0AAA")
            try:
                sid = engine.create_game("battleship-classic")
                events = []
                engine.subscribe(lambda event: events.append(event.data.get("session")))
                engine.place_battleship_fleet(sid, FLEET_A)
                self.assertEqual(engine.session(sid)["private_fleet"], FLEET_A)
                for target in [50,55,51]:
                    engine.legal_action(sid, bytes([target]))
                    session = engine.session(sid)
                    self.assertEqual(session["current_player"], "N0AAA")
                    self.assertEqual(session["private_fleet"], FLEET_A)
                    self.assertEqual(sum(v!=0 for v in session["view"]["shots"]), sum(v!=0 for v in session["view"]["incoming_shots"]))
                self.assertEqual(session["view"]["shots"][50], 2)
                self.assertEqual(session["view"]["shots"][55], 1)
                self.assertTrue(all(e.get("private_fleet") in (None, FLEET_A) for e in events if e))
                self.assertEqual(scoreboard(session)["entries"][0]["unit"], "SHIPS SUNK")
            finally:
                engine.close()

class BattleshipRadio(unittest.TestCase):
    def test_early_fleet_ready_and_alternating_turns(self):
        harness = test_sessions.SessionScenarios()
        harness.setUp()
        try:
            host, guest = harness.engines
            sid = host.create_game("battleship-classic", "N0BBB")
            harness.pump()
            guest.accept_invite(sid); harness.pump()
            guest.place_battleship_fleet(sid, FLEET_B); harness.pump()
            host.place_battleship_fleet(sid, FLEET_A); harness.pump()
            self.assertEqual(host.session(sid)["view"]["phase"], "call")
            host.legal_action(sid, bytes([50])); harness.pump()
            self.assertEqual(host.session(sid)["current_player"], "N0BBB")
            guest.legal_action(sid, bytes([0])); harness.pump()
            for engine, fleet, target in [(host, FLEET_A, 50), (guest, FLEET_B, 0)]:
                view = engine.session(sid)
                self.assertEqual(view["current_player"], "N0AAA")
                self.assertEqual(view["private_fleet"], fleet)
                self.assertEqual(view["view"]["shots"][target], 2)
                self.assertEqual(view["view"]["incoming_shots"][0 if target == 50 else 50], 2)
                self.assertEqual(len(view["state"]), 104)
        finally:
            harness.tearDown()

class BattleshipDesktop(unittest.TestCase):
    @unittest.skipUnless(__import__("importlib").util.find_spec("PySide6"), "desktop extra")
    def test_two_boards_miss_hit_markers_and_ship_sprites(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QLabel, QPushButton
        from radio_arcade.desktop import ArcadeWindow
        from radio_arcade.settings import Settings
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"RADIO_ARCADE_HOME": tmp}), patch("radio_arcade.practice.random_fleet", return_value=FLEET_B):
            engine = PracticeService("N0AAA")
            window = ArcadeWindow(Settings(callsign="N0AAA", transport="practice"))
            try:
                window.resize(1366, 768)
                window._service_ready(engine)
                sid = engine.create_game("battleship-classic")
                engine.place_battleship_fleet(sid, FLEET_A)
                with patch("radio_arcade.practice.choose_ai", side_effect=[bytes([99]), bytes([0])]):
                    engine.legal_action(sid, bytes([50]))
                    engine.legal_action(sid, bytes([55]))
                self.assertEqual(engine.session(sid)["view"]["incoming_shots"][99], 1)
                self.assertEqual(engine.session(sid)["view"]["incoming_shots"][0], 2)
                window.selected_id = sid
                window._show_session(sid)
                window.show()
                app.processEvents()
                labels = [label.text() for label in window.board_scroll.findChildren(QLabel)]
                self.assertEqual(window.board_scroll.horizontalScrollBar().maximum(), 0)
                self.assertIn("YOUR FLEET · INCOMING FIRE", labels)
                self.assertIn("ENEMY WATERS · YOUR SHOTS", labels)
                buttons = [button for button in window.board_scroll.findChildren(QPushButton) if button.toolTip().startswith("A6:")]
                self.assertEqual(len(buttons), 2)
                self.assertTrue(any(b.text() == "✕" and not b.isEnabled() for b in buttons))
                sprites = window.board_scroll.findChildren(QLabel, "navalShipSprite")
                self.assertEqual(len(sprites), 5)
                self.assertEqual({label.property("spriteName") for label in sprites}, {
                    "battleship-carrier-horizontal.png", "battleship-hull-horizontal.png",
                    "battleship-cruiser-horizontal.png", "battleship-destroyer-horizontal.png",
                    "battleship-patrol-horizontal.png"})
                self.assertTrue(all(not label.pixmap().isNull() for label in sprites))
                incoming_board = sprites[0].parentWidget()
                miss = next(b for b in incoming_board.findChildren(QPushButton) if b.toolTip() == "J10: miss")
                self.assertEqual(miss.text(), "○")
                self.assertIn("color: #ffffff", miss.styleSheet())
                self.assertFalse(miss.isEnabled())
                hit = next(label for label in incoming_board.findChildren(QLabel, "navalShotMarker")
                           if label.property("shotCell") == 0)
                self.assertEqual(hit.text(), "✕")
            finally:
                window.close()
                engine.close()
                app.processEvents()
