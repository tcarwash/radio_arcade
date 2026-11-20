"""Creation flow, contextual table actions and actual radio delivery feedback."""
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from radio_arcade.presentation import table_feedback

class FeedbackTests(unittest.TestCase):
    def test_pending_ack_commit_and_recovery(self):
        session = dict(id="1", status="active", current_player="GUEST", coordinator="HOST",
                       sequence=4, pending_action=b"\x00", view={}, players=["HOST","GUEST"])
        def event(state, sequence=5):
            return dict(kind="ACTION", sequence=sequence, state=state)
        self.assertIn("queued", table_feedback(session, "GUEST")[0])
        self.assertIn("Sending", table_feedback(session, "GUEST", submitting=True)[0])
        self.assertIn("acknowledged", table_feedback(session, "GUEST", delivery=[event("delivered")])[0])
        self.assertIn("queued", table_feedback(session, "GUEST", delivery=[event("delivered", 3)])[0])
        self.assertIn("Retrying", table_feedback(session, "GUEST", delivery=[event("retry")])[0])
        self.assertEqual(table_feedback(session, "GUEST", delivery=[event("unconfirmed")])[1], "alert")
        self.assertIn("paused", table_feedback(session, "GUEST", tx_enabled=False)[0])
        session.update(sequence=5, pending_action=None, current_player="HOST", last_move={"actor":"GUEST"})
        self.assertIn("Move confirmed", table_feedback(session, "GUEST")[0])
        self.assertIn("acknowledgement", table_feedback(session, "HOST", outstanding=1)[0])
        self.assertIn("Your turn", table_feedback(session, "HOST")[0])
        session.update(sync_requested_at=42, sync_at=1)
        self.assertEqual(table_feedback(session, "GUEST")[1], "alert")
        self.assertIn("Recovery", table_feedback(session, "GUEST")[0])
        session.update(sync_at=42, hosted=True, last_move={"actor":"HOST"}, current_player="GUEST")
        self.assertIn("applied locally", table_feedback(session, "HOST")[0])
        self.assertIn("receive-only", table_feedback(session, "GUEST", observer=True)[0])

@unittest.skipUnless(importlib.util.find_spec("PySide6"), "install the desktop extra")
class TableUX(unittest.TestCase):
    def test_creation_dialog_modes_validation_and_cancel(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from radio_arcade.desktop import CreateTableDialog
        from radio_arcade.games.base import builtin_registry
        from radio_arcade.settings import Settings
        app = QApplication.instance() or QApplication([])
        game = next(g for g in builtin_registry(load_plugins=False).catalog() if g.key == "connect-four")
        settings = Settings(callsign="N0AAA", transport="broadcast")
        d = CreateTableDialog(game, settings); d.show(); app.processEvents()
        self.assertEqual(d.create.text(), "Create lobby")
        d.peer_input.setText("N0AAA"); d.create.click(); self.assertTrue(d.error.isVisible())
        d.peer_input.setText("bad space"); d.create.click(); self.assertTrue(d.error.isVisible())
        d.peer_input.setText("n0bbb"); d.create.click()
        self.assertEqual((d.peer, d.hosted), ("N0BBB", False))
        d = CreateTableDialog(game, settings); d.show()
        d.mode.setCurrentIndex(d.mode.findData("hosted")); app.processEvents()
        self.assertFalse(d.peer_input.isVisible())
        d.create.click(); self.assertEqual((d.peer, d.hosted), (None, True))
        settings.transport = "connected"
        d = CreateTableDialog(game, settings)
        self.assertEqual(d.mode.count(), 1)
        d.reject(); self.assertEqual(d.result(), 0)

    def test_radio_lobby_and_move_confirmation_in_workspace(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from radio_arcade.desktop import ArcadeWindow
        from radio_arcade.service import ArcadeService
        from radio_arcade.settings import Settings
        from radio_arcade.store import SessionStore
        from tests.test_hosted import Air
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RADIO_ARCADE_HOME":folder}):
            air = Air()
            host = ArcadeService("N0AAA", air.station(), SessionStore(Path(folder)/"a.db"))
            guest = ArcadeService("N0BBB", air.station(), SessionStore(Path(folder)/"b.db"))
            window = ArcadeWindow(Settings(callsign="N0AAA", transport="broadcast"))
            window.show(); window._service_ready(host); app.processEvents()
            try:
                sid = host.create_game("tic-tac-toe", guest.callsign)
                window.selected_id = sid; window._refresh_sessions(); app.processEvents()
                self.assertTrue(window.start_button.isVisible())
                self.assertFalse(window.start_button.isEnabled())
                self.assertTrue(window.lobby_invite.isVisible())
                self.assertIn("Invite",window.turn_badge.text())
                air.pump(); guest.accept_invite(sid); air.pump(); app.processEvents()
                self.assertEqual(host.session(sid)["status"], "active")
                self.assertFalse(window.start_button.isVisible())
                self.assertTrue(window.table_menu_button.isVisible())
                self.assertFalse(window.forfeit_button.isVisible())
                self.assertTrue(window.forfeit_menu_action.isVisible())
                self.assertFalse(window.sync_button.isVisible())
                self.assertTrue(window.sync_menu_action.isVisible())
                window.focus_toggle.setChecked(True); app.processEvents()
                self.assertTrue(window.turn_badge.isVisible())
                self.assertIn("Your turn",window.turn_badge.text())
                host.legal_action(sid,b"\x00"); app.processEvents()
                self.assertIn("acknowledgement",window.turn_badge.text())
                air.pump(); app.processEvents()
                self.assertIn("Move confirmed",window.turn_badge.text())
                window.close(); app.processEvents()
            finally:
                window.close(); host.close(); guest.close(); app.processEvents()
