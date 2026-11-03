# Radio Arcade for Godot 4

Copy [`addons/radio_arcade/`](addons/radio_arcade/) into your Godot project's
`res://addons/radio_arcade/` directory. The script defines the global
`RadioArcadeClient` class. The optional editor plugin registers a
`RadioArcadeNode` you can add through the scene editor.

```gdscript
extends Node

var radio: RadioArcadeClient

func _ready() -> void:
    radio = RadioArcadeClient.new()
    add_child(radio)
    radio.configure("my-radio-game", 1, "turn")
    radio.attach_session(OS.get_environment("RADIO_ARCADE_SESSION"))
    radio.event_received.connect(on_game_event)

func on_game_event(player: int, revision: int, payload: PackedByteArray) -> void:
    apply_committed_action(player, revision, payload)

func issue_move(x: int, y: int) -> void:
    if radio.my_turn():
        radio.send_action(PackedByteArray([x, y]))
```

Signals: `event_received`, `side_message`, `status_changed`, `error_occurred`,
`session_created`, `estimate_ready`. Methods include `create_session(peer,
hosted)`, `attach_session(id)`, `control(command,peer)`, `request_sync()`,
`send_action(bytes)`, `send_side(target,message)`, `finish()`, `connected()`,
`my_turn()`, `current_session()` and `estimate_airtime(bytes,baud,bits_per_symbol,
code_rate)`.

Godot's `HTTPRequest` node handles network I/O asynchronously. The adapter
never sends a frame for each rendered frame; it submits only compact actions.
The same game ID, version and mode must be installed at both stations.

## Run the 5×5 strategy example

The [`project.godot`](project.godot) file makes this directory a runnable
project. Create an `external-game` table first, set the environment variable
`RADIO_ARCADE_SESSION`, then open this directory with Godot and run the project.
The demo uses **two bytes per move** and updates its board only after the
radio action is committed. Each player moves one orthogonal cell per turn;
stepping onto the opponent's cell wins.

For actual RF play, each station runs a local Radio Arcade bridge and its own
copy of Godot. For HTTP practice mode, run the example on one local machine.
