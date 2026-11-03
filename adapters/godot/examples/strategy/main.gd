extends Node2D
## Deterministic two-player grid strategy demonstration: two bytes per move.

const SIZE := 5
const CELL := 80
const START := Vector2i(200, 90)

var radio: RadioArcadeClient
var positions := [Vector2i(0, 0), Vector2i(4, 4)]
var moves := 0
var winner := -1
var status_line := "Set RADIO_ARCADE_SESSION to a running external-game session"

func _ready() -> void:
	radio = RadioArcadeClient.new()
	add_child(radio)
	radio.configure("radio-strategy-demo", 1, "turn")
	radio.event_received.connect(_on_event)
	radio.error_occurred.connect(_on_radio_error)
	radio.status_changed.connect(_on_radio_status)
	var sid := OS.get_environment("RADIO_ARCADE_SESSION")
	if sid != "":
		radio.attach_session(sid)
		status_line = "Connecting to " + sid

func _process(_delta: float) -> void:
	queue_redraw()

func _draw() -> void:
	var f := ThemeDB.fallback_font
	draw_string(f, Vector2(36, 40), "RADIO ARCADE / GODOT STRATEGY DEMO", HORIZONTAL_ALIGNMENT_LEFT, -1, 25, Color.WHITE)
	for y in range(SIZE):
		for x in range(SIZE):
			var cell := Rect2(Vector2(START + Vector2i(x * CELL, y * CELL)),
				Vector2(CELL - 2, CELL - 2))
			draw_rect(cell, Color("253647") if (x + y) % 2 else Color("31445a"))
	for player in range(2):
		var pos := positions[player]
		var center := Vector2(START + pos * CELL) + Vector2(CELL / 2.0, CELL / 2.0)
		draw_circle(center, 27, Color("32d9c4") if player == 0 else Color("ffc66d"))
		draw_string(f, center + Vector2(-7, 9), str(player + 1), HORIZONTAL_ALIGNMENT_LEFT, -1, 24, Color("101a25"))
	if winner >= 0:
		draw_string(f, Vector2(215, 525), "Player %d wins" % [winner + 1], HORIZONTAL_ALIGNMENT_LEFT, -1, 22, Color.WHITE)
	else:
		var turn := (moves % 2) + 1
		draw_string(f, Vector2(36, 505), "Turn: player %d | Local: %s | Revision: %d" %
			[turn, "READY" if radio.my_turn() else "WAIT", radio.revision()],
			HORIZONTAL_ALIGNMENT_LEFT, -1, 17, Color.WHITE)
	draw_string(f, Vector2(36, 535), status_line, HORIZONTAL_ALIGNMENT_LEFT, 725, 14, Color("b4d6ec"))

func _unhandled_input(event: InputEvent) -> void:
	if not (event is InputEventMouseButton):
		return
	var mouse := event as InputEventMouseButton
	if not mouse.pressed or mouse.button_index != MOUSE_BUTTON_LEFT:
		return
	if winner >= 0 or not radio.my_turn():
		return
	var p: Vector2i = Vector2i((mouse.position - Vector2(START)) / CELL)
	if p.x < 0 or p.x >= SIZE or p.y < 0 or p.y >= SIZE:
		return
	var actor := moves % 2
	var old: Vector2i = positions[actor]
	if abs(p.x - old.x) + abs(p.y - old.y) != 1:
		status_line = "Move one orthogonal square per turn"
		return
	var source := old.x + old.y * SIZE
	var target := p.x + p.y * SIZE
	radio.send_action(PackedByteArray([source, target]))
	status_line = "Move queued; waiting for a radio commit"

func _on_event(player: int, _revision: int, data: PackedByteArray) -> void:
	if winner >= 0 or player != moves % 2 or data.size() != 2:
		status_line = "Received invalid/out-of-order game action"
		return
	var source: int = data[0]
	var target: int = data[1]
	if source >= SIZE * SIZE or target >= SIZE * SIZE:
		return
	var from := Vector2i(source % SIZE, source / SIZE)
	var to := Vector2i(target % SIZE, target / SIZE)
	if positions[player] != from or abs(to.x - from.x) + abs(to.y - from.y) != 1:
		status_line = "Invalid move in committed action"
		return
	positions[player] = to
	moves += 1
	if positions[0] == positions[1]:
		winner = player
	status_line = "Move %d committed over Radio Arcade" % moves

func _on_radio_error(message: String) -> void:
	status_line = message
	queue_redraw()

func _on_radio_status(_connected: bool) -> void:
	queue_redraw()
