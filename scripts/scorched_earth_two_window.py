#!/usr/bin/env python3
"""Open two browser clients linked by a local, simulated broadcast channel.

Run from the checkout with:
    uv run python scripts/scorched_earth_two_window.py

No Data2G host, KISS socket, modem, or RF hardware is used. Ctrl+C closes both
web rooms and removes the temporary game databases.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import queue
import random
import shlex
import subprocess
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from radio_arcade.service import ArcadeService
from radio_arcade.settings import Settings
from radio_arcade.store import SessionStore
from radio_arcade.web import WebController, make_server
from radio_arcade.wire import decode


@dataclass
class _Transmission:
    sender: str
    frame: bytes


class SimulatedRadio:
    """A tiny broadcast channel with configurable delay and frame loss."""

    def __init__(self, delay: float, loss: float, seed: int):
        self.delay = delay
        self.loss = loss
        self.random = random.Random(seed)
        self.lock = threading.Lock()
        self.transports: dict[str, SimulatedTransport] = {}
        self.frames: queue.Queue[_Transmission | None] = queue.Queue()
        self.stopped = threading.Event()
        self.worker = threading.Thread(target=self._run, name="simulated-radio", daemon=True)
        self.worker.start()

    def endpoint(self, callsign: str) -> "SimulatedTransport":
        endpoint = SimulatedTransport(self, callsign)
        with self.lock:
            self.transports[callsign] = endpoint
        return endpoint

    def _run(self) -> None:
        while not self.stopped.is_set():
            try:
                item = self.frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                packet = decode(item.frame)
                detail = f"{packet.kind.name} {len(item.frame)} B"
                destination = packet.destination
            except ValueError:
                detail, destination = f"DATA {len(item.frame)} B", "*"
            if self.random.random() < self.loss:
                print(f"[AIR] {item.sender} -X-> {destination} {detail} (simulated loss)", flush=True)
                continue
            if self.delay:
                time.sleep(self.delay)
            with self.lock:
                peers = tuple(endpoint for call, endpoint in self.transports.items()
                              if call != item.sender and not endpoint.closed.is_set())
            for peer in peers:
                callback = peer.on_frame
                if callback:
                    callback(item.frame)
            print(f"[AIR] {item.sender} --> {destination} {detail}", flush=True)

    def close(self) -> None:
        self.stopped.set()
        self.frames.put(None)
        self.worker.join(timeout=2)
        with self.lock:
            endpoints = tuple(self.transports.values())
            self.transports.clear()
        for endpoint in endpoints:
            endpoint.close()


class SimulatedTransport:
    def __init__(self, radio: SimulatedRadio, callsign: str):
        self.radio = radio
        self.callsign = callsign
        self.closed = threading.Event()
        self.on_frame = None

    def send(self, frame: bytes) -> None:
        if self.closed.is_set():
            raise RuntimeError("simulated radio is closed")
        self.radio.frames.put(_Transmission(self.callsign, bytes(frame)))

    def close(self) -> None:
        self.closed.set()


def open_window(url: str, browser_command: str | None) -> None:
    if browser_command:
        subprocess.Popen([*shlex.split(browser_command), "--new-window", url])
    else:
        webbrowser.open(url, new=1, autoraise=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay-ms", type=float, default=120,
                        help="one-way simulated radio delay (default: 120 ms)")
    parser.add_argument("--loss", type=float, default=0.0,
                        help="probability of dropping each radio frame, from 0 to 1")
    parser.add_argument("--seed", type=int, default=7, help="repeatable simulated-loss sequence")
    parser.add_argument("--browser-command", metavar="COMMAND",
                        help="browser executable; launched with --new-window for each client")
    parser.add_argument("--no-browser", action="store_true", help="print the two URLs without opening them")
    args = parser.parse_args()
    if args.delay_ms < 0:
        parser.error("--delay-ms must be zero or greater")
    if not 0 <= args.loss <= 1:
        parser.error("--loss must be between 0 and 1")

    with tempfile.TemporaryDirectory(prefix="radio-arcade-scorched-earth-") as temporary:
        data_dir = Path(temporary)
        radio = SimulatedRadio(args.delay_ms / 1000, args.loss, args.seed)
        controllers: list[WebController] = []
        servers = []
        threads = []
        try:
            for callsign in ("N0AAA", "N0BBB"):
                settings = Settings(callsign=callsign, host="simulated-radio", transport="broadcast",
                                    group="SIMNET", tx_enabled=True)

                def service_factory(current: Settings, *, _radio=radio, _data=data_dir):
                    endpoint = _radio.endpoint(current.callsign)
                    return ArcadeService(current.callsign, endpoint,
                                         SessionStore(_data / f"{current.callsign}.sqlite3"))

                controller = WebController(settings, service_factory,
                                           settings_path=data_dir / f"{callsign}-settings.json")
                controllers.append(controller)
                server = make_server(controller, "127.0.0.1", 0)
                servers.append(server)
                thread = threading.Thread(target=server.serve_forever,
                                          name=f"web-{callsign}", daemon=True)
                threads.append(thread)
                thread.start()

            first, second = controllers
            session_id = first.engine.create_game("scorched-earth", "N0BBB")
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    invited = second.engine.session(session_id)
                    if invited["status"] == "invited":
                        break
                except KeyError:
                    pass
                time.sleep(0.05)
            else:
                raise TimeoutError("simulated radio did not deliver the invitation")
            second.engine.accept_invite(session_id)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    if (first.engine.session(session_id)["status"] == "active" and
                            second.engine.session(session_id)["status"] == "active"):
                        break
                except KeyError:
                    pass
                time.sleep(0.05)
            else:
                raise TimeoutError("simulated radio did not deliver the game start")

            urls = [f"http://127.0.0.1:{server.server_port}/" for server in servers]
            print("Simulated radio is active; no Data2G, KISS, modem, or RF connection is used.", flush=True)
            print(f"N0AAA: {urls[0]}", flush=True)
            print(f"N0BBB: {urls[1]}", flush=True)
            print("The Scorched Earth match is ready. Close both windows or press Ctrl+C to stop.", flush=True)
            if not args.no_browser:
                open_window(urls[0], args.browser_command)
                open_window(urls[1], args.browser_command)
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for controller in controllers:
                controller.close()
            radio.close()


if __name__ == "__main__":
    main()
