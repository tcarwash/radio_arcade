"""Session-level regression checks using a deterministic packet link."""
import tempfile
import time
import unittest
from pathlib import Path
from collections import Counter

from radio_arcade import ArcadeService, SessionStore, GameRegistry, builtin_registry
from radio_arcade.wire import Kind, Packet, decode, encode, encode_roster_state, encode_snapshot
from radio_arcade.games.scrabble import _lexicon, make_placement


class Link:
    def __init__(self):
        self.inbox = []
        self.drop_kind = None

    def send(self, frame):
        if decode(frame).kind == self.drop_kind:
            self.drop_kind = None
            return
        self.peer.inbox.append(frame)

    def pump(self):
        while self.inbox:
            self.on_frame(self.inbox.pop(0))

    def close(self):
        pass


class SessionScenarios(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.links = [Link(), Link()]
        self.links[0].peer, self.links[1].peer = self.links[1], self.links[0]
        self.engines = [ArcadeService(call, link, SessionStore(Path(self.directory.name) / str(i)))
                        for i, (call, link) in enumerate(zip(("N0AAA", "N0BBB"), self.links))]

    def tearDown(self):
        for engine in self.engines:
            engine.close()
        self.directory.cleanup()

    def pump(self):
        for _ in range(12):
            for link in self.links:
                link.pump()

    def start(self):
        host, guest = self.engines
        sid = host.create_game("tic-tac-toe", "N0BBB")
        self.pump()
        guest.accept_invite(sid)
        self.pump()
        return sid

    def test_dropped_final_commit_recovers_finished_game(self):
        host, guest = self.engines
        sid = self.start()
        for engine, cell in ((host, 0), (guest, 3), (host, 1), (guest, 4)):
            engine.legal_action(sid, bytes((cell,)))
            self.pump()
        self.links[0].drop_kind = Kind.COMMIT
        host.legal_action(sid, b"\x02")
        self.pump()
        self.assertEqual(guest.session(sid)["status"], "active")
        guest.request_sync(sid)
        self.pump()
        self.assertEqual(guest.session(sid)["status"], "finished")
        self.assertEqual(guest.session(sid)["view"]["winner"], "N0AAA")

    def test_invalid_start_is_not_acknowledged(self):
        host, guest = self.engines
        sid = host.create_game("tic-tac-toe", "N0BBB")
        self.pump()
        guest.accept_invite(sid)
        self.pump()
        self.links[0].inbox.clear()
        bad_roster = ("N0AAA", "N0CCC")
        game = host.registry.by_id(1)
        payload = encode_roster_state(bad_roster, game.encode_state(game.new_state(bad_roster)))
        packet = Packet(1, Kind.START, int(sid, 16), 0, 0, "N0AAA", "N0BBB", payload)
        guest.receive_frame(encode(packet))
        self.assertEqual(self.links[0].inbox, [])

    def test_invalid_snapshot_action_does_not_corrupt_session(self):
        host, guest = self.engines
        sid = self.start()
        self.links[0].inbox.clear()
        state = host.registry.by_id(1).encode_state(host.registry.by_id(1).new_state(("N0AAA", "N0BBB")))
        payload = encode_snapshot(("N0AAA", "N0BBB"), state, "N0AAA", b"\xff")
        packet = Packet(1, Kind.SNAPSHOT, int(sid, 16), 0, 0, "N0AAA", "N0BBB", payload)
        guest.receive_frame(encode(packet))
        self.assertEqual(self.links[0].inbox, [])
        self.assertIsNone(guest.session(sid)["last_move"])

    def test_inconsistent_tictactoe_snapshot_is_rejected(self):
        host, guest = self.engines
        sid = self.start()
        self.links[0].inbox.clear()
        # Empty board marked as an X win is not a valid game state.
        payload = encode_snapshot(("N0AAA", "N0BBB"), b"\x00\x00\x00\x00\x02")
        guest.receive_frame(encode(Packet(1, Kind.SNAPSHOT, int(sid, 16), 0, 0,
                                          "N0AAA", "N0BBB", payload)))
        self.assertEqual(self.links[0].inbox, [])
        self.assertEqual(guest.session(sid)["status"], "active")

    def test_conflicting_snapshot_at_same_revision_is_rejected(self):
        host, guest = self.engines
        sid = self.start()
        host.legal_action(sid, b"\x00")
        self.pump()
        self.links[0].inbox.clear()
        game = host.registry.by_id(1)
        players = ("N0AAA", "N0BBB")
        other = game.apply_action(game.new_state(players), players, "N0AAA", b"\x01")
        payload = encode_snapshot(players, game.encode_state(other), "N0AAA", b"\x01")
        guest.receive_frame(encode(Packet(1, Kind.SNAPSHOT, int(sid, 16), 1, 1,
                                          "N0AAA", "N0BBB", payload)))
        self.assertEqual(self.links[0].inbox, [])
        self.assertEqual(guest.session(sid)["view"]["cells"][0], "X")

    def test_simultaneous_games_keep_moves_and_chat_separate(self):
        host, guest = self.engines
        first, second = self.start(), self.start()
        host.legal_action(first, b"\x00")
        host.legal_action(second, b"\x08")
        self.pump()
        guest.send_chat(first, "first table")
        self.pump()
        self.assertEqual(guest.session(first)["view"]["cells"][0], "X")
        self.assertEqual(guest.session(second)["view"]["cells"][8], "X")
        self.assertEqual(host.chat_history(first)[0]["body"], "first table")
        self.assertEqual(host.chat_history(second), [])

    def test_reopened_player_recovers_missed_move(self):
        host, guest = self.engines
        sid = self.start()
        guest.close()
        host.legal_action(sid, b"\x00")
        self.links[1].inbox.clear()
        guest = ArcadeService("N0BBB", self.links[1], SessionStore(Path(self.directory.name) / "1"))
        self.engines[1] = guest
        self.assertEqual(guest.session(sid)["revision"], 0)
        guest.request_sync(sid)
        self.pump()
        self.assertEqual(guest.session(sid)["revision"], 1)
        self.assertEqual(guest.session(sid)["view"]["cells"][0], "X")

    def test_cancel_before_invitation_stays_cancelled(self):
        host, guest = self.engines
        sid = host.create_game("nim", "N0BBB")
        host.cancel_invitation(sid, "N0BBB")
        frames = self.links[1].inbox
        frames.sort(key=lambda frame: decode(frame).kind != Kind.CANCEL_INVITE)
        self.pump()
        self.assertEqual(guest.session(sid)["status"], "cancelled")
        with self.assertRaises(ValueError):
            host.invite_player(sid, "N0BBB")

    def test_opaque_state_is_retried_stored_and_hidden(self):
        host, guest = self.engines
        sid = self.start()
        notices = []
        guest.subscribe(notices.append)
        self.links[0].drop_kind = Kind.OPAQUE_STATE
        sequence = host.send_opaque_state(sid, "N0BBB", "phase=opening")
        self.pump()
        self.assertEqual(guest.opaque_state_history(sid), [])
        for item in host.store.due_outgoing(time.time() + 400):
            host._transmit(item["frame"])
        self.pump()
        self.assertEqual(guest.opaque_state_history(sid)[0]["body"], b"phase=opening")
        self.assertTrue(any(e.name == "opaque_state" and e.data["hidden_from_ui"] for e in notices))
        self.assertEqual(host.opaque_state_history(sid)[0]["delivery"], "delivered")
        self.assertEqual(host.chat_history(sid), [])
        self.assertIn("phase=opening", (Path(self.directory.name) / "opaque-debug.log").read_text())
        self.assertEqual(host.store.count_outgoing(sid, int(Kind.OPAQUE_STATE), sequence), 0)
        reopened = SessionStore(Path(self.directory.name) / "1")
        try:
            self.assertEqual(reopened.load_opaque_states(sid)[0]["body"], b"phase=opening")
        finally:
            reopened.close()

    def test_opaque_format_is_owned_by_game_plugin(self):
        host, guest = self.engines
        sid = self.start()
        host_game = host.registry.by_id(1)
        guest_game = guest.registry.by_id(1)
        host_game.encode_opaque_state = lambda value: f"v1|phase|{value['phase']}"
        guest_game.decode_opaque_state = lambda text: {"phase": int(text.split("|")[2])}
        notices = []
        guest.subscribe(notices.append)

        host.send_game_opaque_state(sid, "N0BBB", {"phase": 7})
        self.pump()

        event = next(e for e in notices if e.name == "game_opaque_state")
        self.assertEqual(event.data["state"], {"phase": 7})
        self.assertTrue(event.data["hidden_from_ui"])

    def test_outbox_retry_filter_preserves_other_connected_peers(self):
        store = self.engines[0].store
        store.save_outgoing("0000000000000001", "N0BBB", int(Kind.CHAT), 1, b"to-bbb")
        store.save_outgoing("0000000000000002", "N0CCC", int(Kind.CHAT), 1, b"to-ccc")
        due = store.due_outgoing(time.time() + 400, target="n0bbb")
        self.assertEqual([item["target"] for item in due], ["N0BBB"])
        remaining = store.due_outgoing(time.time() + 400, target="N0CCC")
        self.assertEqual([item["target"] for item in remaining], ["N0CCC"])

    def test_open_word_tiles_start_move_and_snapshot_between_stations(self):
        host, guest = self.engines
        sid = host.create_game("open-word-tiles", "N0BBB")
        self.pump()
        guest.accept_invite(sid)
        self.pump()
        session = host.session(sid)
        actor = session["current_player"]
        engine = host if actor == host.callsign else guest
        session = engine.session(sid)
        game = engine.registry.by_id(3)
        state = game.decode_state(session["state"])
        game.validate_roster(state, tuple(session["players"]))
        rack = Counter(chr(64 + tile) for tile in state["racks"][state["turn"]] if tile not in (0, 31))
        blank_count = state["racks"][state["turn"]].count(0)
        choice = next((word for word in sorted(_lexicon()) if 2 <= len(word) <= 7
                       and sum(max(0, n - rack[c]) for c, n in Counter(word).items()) <= blank_count), None)
        self.assertIsNotNone(choice)
        available = rack.copy()
        blanks = blank_count
        notation = ""
        for letter in choice:
            if available[letter]: available[letter] -= 1; notation += letter
            else: blanks -= 1; notation += "{" + letter + "}"
        x = 7 - (len(choice) - 1) // 2
        action = make_placement(state, actor, f"{chr(65+x)}8", "H", notation)
        engine.legal_action(sid, action)
        self.pump()
        self.assertEqual(host.session(sid)["revision"], 1)
        self.assertEqual(guest.session(sid)["revision"], 1)
        self.assertEqual(host.session(sid)["view"]["cells"], guest.session(sid)["view"]["cells"])
        if guest.callsign != host.session(sid)["coordinator"]:
            guest.request_sync(sid)
            self.pump()
        self.assertEqual(guest.session(sid)["revision"], 1)


class MultiplayerScenarios(unittest.TestCase):
    def test_three_player_turns_and_duplicate_move(self):
        class MeshLink:
            def __init__(self):
                self.inbox = []
                self.last_action = None

            def send(self, frame):
                if decode(frame).kind == Kind.ACTION:
                    self.last_action = frame
                for other in links:
                    if other is not self:
                        other.inbox.append(frame)

            def pump(self):
                while self.inbox:
                    self.on_frame(self.inbox.pop(0))

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            links = [MeshLink() for _ in range(3)]
            engines = [ArcadeService(call, link, SessionStore(Path(directory) / str(i)))
                       for i, (call, link) in enumerate(zip(("N0AAA", "N0BBB", "N0CCC"), links))]
            host, second, third = engines

            def pump():
                for _ in range(12):
                    for link in links:
                        link.pump()

            try:
                sid = host.create_game("nim")
                host.invite_player(sid, "N0BBB")
                host.invite_player(sid, "N0CCC")
                pump()
                second.accept_invite(sid)
                third.accept_invite(sid)
                pump()
                host.start_game(sid)
                pump()
                host.legal_action(sid, bytes((0, 1)))
                pump()
                second.legal_action(sid, bytes((1, 3)))
                pump()
                host.receive_frame(links[1].last_action)
                pump()
                self.assertEqual(host.session(sid)["revision"], 2)
                third.legal_action(sid, bytes((2, 5)))
                pump()
                host.legal_action(sid, bytes((3, 7)))
                pump()
                for engine in engines:
                    session = engine.session(sid)
                    self.assertEqual(session["players"], ["N0AAA", "N0BBB", "N0CCC"])
                    self.assertEqual(session["revision"], 4)
                    self.assertEqual(session["status"], "finished")
                    self.assertEqual(session["view"]["winner"], "N0AAA")
            finally:
                for engine in engines:
                    engine.close()


class CatalogScenarios(unittest.TestCase):
    def test_invalid_nim_results_are_rejected(self):
        game = builtin_registry(load_plugins=False).by_key("nim")
        for state in (bytes((2, 3, 5, 7, 0, 255, 2)),
                      bytes((0, 0, 0, 0, 0, 255, 2)),
                      bytes((0, 0, 0, 0, 0, 0, 2))):
            with self.assertRaises(ValueError):
                game.decode_state(state)
        state = game.new_state(("N0AAA", "N0BBB", "N0CCC"))
        with self.assertRaisesRegex(ValueError, "roster"):
            game.validate_roster(state, ("N0AAA", "N0BBB"))

    def test_assigned_id_cannot_be_redefined_in_empty_registry(self):
        class Impostor:
            api_version = 1
            game_id = 1

        with self.assertRaisesRegex(ValueError, "permanently assigned"):
            GameRegistry().register(Impostor())
        self.assertEqual([game.game_id for game in builtin_registry(load_plugins=False).catalog()], [1, 2, 3])

        class RetiredGame:
            api_version = 1
            game_id = 4

        with self.assertRaisesRegex(ValueError, "retired"):
            GameRegistry().register(RetiredGame())

    def test_retired_game_sessions_remain_on_disk_but_not_in_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "saved.sqlite3")
            saved = {"id": "0000000000000044", "game_id": 4, "coordinator": "N0AAA",
                     "players": ["N0AAA"], "invited": [], "status": "active",
                     "revision": 1, "sequence": 1, "state": b"legacy game bytes",
                     "pending_action": None, "chat_sequence": 0, "opaque_sequence": 0,
                     "last_actor": None, "last_action": None, "updated": 1.0,
                     "error": "", "hosted": True}
            store.save_session(saved)
            transport = Link()
            service = ArcadeService("N0AAA", transport, store)
            try:
                self.assertEqual(service.sessions(), [])
                self.assertEqual(len(store.load_sessions()), 1)
                with self.assertRaises(KeyError):
                    service.session(saved["id"])
            finally:
                service.close()

    def test_connected_transport_rejects_multiplayer_lobby(self):
        class ConnectedLink(Link):
            def connect_peer(self, peer):
                pass

            def send(self, frame):
                self.inbox.append(frame)

        with tempfile.TemporaryDirectory() as directory:
            engine = ArcadeService("N0AAA", ConnectedLink(), SessionStore(Path(directory) / "db"))
            try:
                sid = engine.create_game("nim", "N0BBB")
                with self.assertRaisesRegex(ValueError, "two players"):
                    engine.invite_player(sid, "N0CCC")
                row = engine._sessions[sid]
                row["players"] = ["N0AAA", "N0BBB", "N0CCC"]
                row["invited"] = []
                with self.assertRaisesRegex(ValueError, "two players"):
                    engine.start_game(sid)
                guest = engine._new_session(2, "N0BBB", ["N0BBB", "N0AAA"], [], "joining")
                game = engine.registry.by_id(2)
                roster = ("N0BBB", "N0AAA", "N0CCC")
                state = game.encode_state(game.new_state(roster))
                packet = Packet(2, Kind.START, int(guest["id"], 16), 0, 0, "N0BBB", "N0AAA",
                                encode_roster_state(roster, state))
                with self.assertRaisesRegex(ValueError, "more than two"):
                    engine._receive_start(guest, packet)
                self.assertEqual(guest["status"], "joining")
                packet = Packet(2, Kind.SNAPSHOT, int(guest["id"], 16), 0, 0, "N0BBB", "N0AAA",
                                encode_snapshot(roster, state))
                with self.assertRaisesRegex(ValueError, "more than two"):
                    engine._receive_snapshot(guest, packet)
                self.assertEqual(guest["status"], "joining")
                engine.transport.inbox.clear()
                notices = []
                engine.subscribe(notices.append)
                engine.receive_frame(encode(packet))
                self.assertFalse(engine.transport.inbox)
                self.assertTrue(any(e.name == "protocol_error" for e in notices))
            finally:
                engine.close()

if __name__ == "__main__":
    unittest.main()
