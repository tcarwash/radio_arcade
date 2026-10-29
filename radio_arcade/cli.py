"""Small headless reference client for scripting and engine API examples."""

from __future__ import annotations

import argparse

from .service import ArcadeService, EngineEvent
from .settings import Settings
from .transport_factory import create_service


def _print_event(event: EngineEvent) -> None:
    if event.name == "session_updated":
        session = event.data["session"]
        print(f"[{session['id']}] {session['game']} · {session['status']} · rev {session['revision']}")
    elif event.name == "invitation":
        print(f"Invitation from {event.data['sender']} for {event.data['game']}; accept {event.session}")
    elif event.name == "chat_message":
        msg = event.data["message"]
        print(f"[{event.session}] {msg['sender']}: {msg['body']}")
    elif event.name in ("delivery", "transport_status", "protocol_error"):
        print(f"{event.name}: {event.data}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless Radio Arcade client")
    parser.add_argument("--callsign", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--transport", choices=("broadcast", "connected", "practice"), default="broadcast")
    parser.add_argument("--command-port", type=int, default=8300)
    parser.add_argument("--kiss-port", type=int, default=8100)
    parser.add_argument("--group", default="RAGAMES")
    args = parser.parse_args()
    settings = Settings(args.callsign, args.host, args.transport, args.kiss_port, args.command_port, args.group)
    try:
        engine = create_service(settings)
    except Exception as exc:
        raise SystemExit(f"Unable to connect to Data2G: {exc}") from exc
    unsubscribe = engine.subscribe(_print_event)
    print("Radio Arcade headless client. Commands: games, new GAME [CALL], invite ID CALL,")
    print("accept ID, start ID, move ID CELL | move ID PILE AMOUNT, chat ID TEXT,")
    print("connect CALL, disconnect, sync ID, show ID, sessions, quit")
    try:
        while True:
            try:
                words = input("arcade> ").strip().split()
            except EOFError:
                break
            if not words:
                continue
            cmd = words[0].lower()
            try:
                if cmd in ("quit", "exit"):
                    break
                if cmd == "games":
                    for game in engine.catalog():
                        print(f"{game.key}: {game.name} ({game.min_players}-{game.max_players} players)")
                elif cmd == "sessions":
                    for session in engine.sessions():
                        print(f"{session['id']} {session['game']} {session['status']} {session['players']}")
                elif cmd == "show" and len(words) == 2:
                    session = engine.session(words[1])
                    print(f"{session['game']} · {session['status']} · revision {session['revision']}")
                    print(f"Players: {', '.join(session['players'])} · turn: {session['current_player'] or 'none'}")
                    view = session["view"]
                    if view and view.get("kind") == "grid":
                        width = view["width"]
                        for start in range(0, len(view["cells"]), width):
                            print(" | ".join(cell.strip() or str(i + 1) for i, cell in
                                             enumerate(view["cells"][start:start + width], start)))
                    elif view:
                        print(view)
                elif cmd == "new" and len(words) in (2, 3):
                    sid = engine.create_game(words[1], words[2] if len(words) == 3 else None)
                    print(f"Created {sid}")
                elif cmd == "invite" and len(words) == 3:
                    engine.invite_player(words[1], words[2])
                elif cmd == "accept" and len(words) == 2:
                    engine.accept_invite(words[1])
                elif cmd == "start" and len(words) == 2:
                    engine.start_game(words[1])
                elif cmd == "move" and len(words) == 3:
                    engine.legal_action(words[1], bytes((int(words[2]) - 1,)))
                elif cmd == "move" and len(words) == 4:
                    engine.legal_action(words[1], bytes((int(words[2]) - 1, int(words[3]))))
                elif cmd == "chat" and len(words) >= 3:
                    engine.send_chat(words[1], " ".join(words[2:]))
                elif cmd == "sync" and len(words) == 2:
                    engine.request_sync(words[1])
                elif cmd == "connect" and len(words) == 2:
                    engine.connect_peer(words[1])
                elif cmd == "disconnect" and len(words) == 1:
                    engine.disconnect()
                else:
                    print("Unknown command; use games, new, invite, accept, start, move, chat, connect, disconnect, sync, show, sessions, quit")
            except (ValueError, KeyError, IndexError, OSError, RuntimeError) as exc:
                print(f"Could not run command: {exc}")
    finally:
        unsubscribe()
        engine.close()


if __name__ == "__main__":
    main()
