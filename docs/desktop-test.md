# Browser-based desktop testing

Run Radio Arcade in an isolated Xvfb/Openbox desktop using the shared
`docker-radio-stream` image with its noVNC and desktop-only modes.
The default source tag is the locally built
`ghcr.io/tcarwash/radio-stream:novnc-dev`; override `RADIO_STREAM_BASE`
with another compatible tag when available.

Build the shared base image first if it is not present:

```sh
docker build -t ghcr.io/tcarwash/radio-stream:novnc-dev /path/to/docker_stream
```

Generate a VNC password file interactively, or point `VNC_PASSWORD_PATH` to
an existing file readable by container UID 1000. Keep it outside Git.

```sh
mkdir -p .desktop-test
docker run --rm -it --user "$(id -u):$(id -g)" \
  -v "$PWD/.desktop-test:/config" \
  --entrypoint x11vnc ghcr.io/tcarwash/radio-stream:novnc-dev \
  -storepasswd /config/vnc.pass
docker compose -f compose.desktop.yaml up -d --build
```

Open `http://HOST:6082/vnc.html?autoconnect=1&resize=scale` and enter the
VNC password. The native VNC port is not published. By default noVNC binds
to all host IPv4 interfaces, as configured by `NOVNC_BIND`; restrict
access using a trusted VPN or firewall. HTTP/WebSocket is not encrypted by
noVNC itself. Do not forward the port to the public Internet.

Use `NOVNC_PORT`, `DESKTOP_WIDTH`, `DESKTOP_HEIGHT` and
`ARCADE_CALLSIGN` to customize the desktop. It starts in practice mode,
with no host devices, radio endpoints or host application state mounted.
State persists in the Compose volume.

```sh
docker compose -f compose.desktop.yaml ps
docker compose -f compose.desktop.yaml logs --tail=100 arcade
docker compose -f compose.desktop.yaml up -d --build
docker compose -f compose.desktop.yaml down
```

The rebuild includes the checked-out app source. Add `-v` to `down` only
if you intend to erase test game state. Choose a separate Compose project
name and unused port to run concurrent independent test desktops.
