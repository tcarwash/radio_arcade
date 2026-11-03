extends Node
class_name RadioArcadeClient
## Godot 4 client for Radio Arcade's generic binary-action sessions (game ID 10).
## HTTPRequest is asynchronous; no frame awaits radio or HTTP traffic.

signal event_received(player: int, revision: int, payload: PackedByteArray)
signal side_message(sender: String, message: String, sequence: int)
signal status_changed(connected: bool)
signal error_occurred(message: String)
signal session_created(session_id: String)
signal estimate_ready(estimate: Dictionary)

@export var server_url := "http://127.0.0.1:8765"
@export var session_id := ""
@export_range(0.1, 10, 0.1) var poll_interval := 0.3

var game_id := ""
var game_version := 1
var game_mode := "turn"
var game_hash := 0

var _http: HTTPRequest
var _busy := false
var _active_job: Dictionary = {}
var _commands: Array[Dictionary] = []
var _outbox: Array[PackedByteArray] = []
var _session: Dictionary = {}
var _callsign := ""
var _transport := ""
var _revision := 0
var _opaque_cursor := 0
var _elapsed := 0.0
var _side_elapsed := 0.0
var _need_identity := true
var _config_queued := false
var _waiting_commit := false
var _last_connected := false
var _retry_cooldown := 0.0
var last_error := ""

func _ready() -> void:
	_http = HTTPRequest.new()
	_http.timeout = 2.0
	add_child(_http)
	_http.request_completed.connect(_on_http_completed)

func configure(id: String, version: int = 1, mode: String = "turn") -> void:
	assert(id.length() > 0 and id.length() <= 48, "game ID must be 1..48 ASCII bytes")
	for byte in id.to_utf8_buffer():
		assert(byte >= 33 and byte <= 126, "game ID must be printable ASCII")
	assert(version >= 1 and version <= 32767, "version must be 1..32767")
	assert(mode == "turn" or mode == "event", "mode must be turn or event")
	game_id = id
	game_version = version
	game_mode = mode
	game_hash = 2166136261
	for byte in id.to_utf8_buffer():
		game_hash = ((game_hash ^ int(byte)) * 16777619) & 0xffffffff

func create_session(peer: String = "", hosted: bool = false) -> void:
	_commands.append({"op": "create", "method": HTTPClient.METHOD_POST,
		"path": "/api/engine/create", "body": {"peer": peer, "hosted": hosted}})

func attach_session(id: String) -> void:
	var matcher := RegEx.create_from_string("^[0-9a-fA-F]{16}$")
	assert(matcher.search(id) != null, "session ID must be 16 hex digits")
	session_id = id
	_revision = 0
	_opaque_cursor = 0
	_session = {}
	_config_queued = false
	_waiting_commit = false
	_outbox.clear()
	_elapsed = poll_interval

func control(command: String, peer: String = "") -> void:
	assert(session_id != "", "attach or create a session first")
	_commands.append({"op": "control", "method": HTTPClient.METHOD_POST,
		"path": "/api/engine/control",
		"body": {"session_id": session_id, "command": command, "peer": peer}})

func send_action(payload: PackedByteArray) -> void:
	assert(payload.size() <= 189, "actions may contain only 0..189 game bytes")
	var action := PackedByteArray([1])
	action.append_array(payload)
	_outbox.append(action)

func finish() -> void:
	_outbox.append(PackedByteArray([2]))

func request_sync() -> void:
	control("request_sync")

func send_side(target: String, message: String) -> void:
	assert(message.begins_with("RA:") and message.to_utf8_buffer().size() <= 200,
		"side message must start RA: and fit 200 UTF-8 bytes")
	_commands.append({"op": "side", "method": HTTPClient.METHOD_POST,
		"path": "/api/engine/opaque",
		"body": {"session_id": session_id, "target": target, "message": message}})

func estimate_airtime(payload_bytes: int, baud: float, bits_per_symbol: int,
		code_rate: float = 1.0) -> void:
	_commands.append({"op": "estimate", "method": HTTPClient.METHOD_POST,
		"path": "/api/engine/estimate",
		"body": {"session_id": session_id, "payload_bytes": payload_bytes,
			"baud": baud, "bits_per_symbol": bits_per_symbol, "code_rate": code_rate}})

func current_session() -> Dictionary:
	return _session.duplicate(true)

func revision() -> int:
	return _revision

func connected() -> bool:
	if _session.is_empty():
		return false
	var view: Dictionary = _session.get("view", {})
	return _session.get("status", "") == "active" and game_hash != 0 and \
		int(view.get("cart_hash", 0)) == game_hash and \
		int(view.get("cart_version", 0)) == game_version and \
		view.get("mode", "") == game_mode

func my_turn() -> bool:
	if not connected():
		return false
	if _transport == "practice":
		return true
	return _callsign in _session.get("players", []) and (
		game_mode == "event" or _session.get("current_player") == _callsign)

func _config_frame() -> PackedByteArray:
	return PackedByteArray([0, (game_hash >> 24) & 255, (game_hash >> 16) & 255,
		(game_hash >> 8) & 255, game_hash & 255,
		(game_version >> 8) & 255, game_version & 255,
		1 if game_mode == "event" else 0])

func _can_send(action: PackedByteArray) -> bool:
	if _session.is_empty() or _session.get("status") != "active" or \
		_session.get("has_pending_action", false):
		return false
	if action[0] == 0:
		return _session.get("coordinator") == _callsign and \
			int(_session.get("view", {}).get("cart_hash", 0)) == 0
	if action[0] == 2:
		return _session.get("coordinator") == _callsign
	return my_turn()

func _queue(op: String, method: int, path: String, body: Dictionary = {}) -> void:
	_commands.append({"op": op, "method": method, "path": path, "body": body})

func _process(delta: float) -> void:
	if game_id == "" or _http == null:
		return
	if _busy:
		return
	_retry_cooldown = maxf(0.0, _retry_cooldown - delta)
	if _retry_cooldown > 0.0:
		return
	if not _commands.is_empty():
		_send_job(_commands.pop_front())
		return
	if _need_identity:
		_send_job({"op": "identity", "method": HTTPClient.METHOD_GET,
			"path": "/api/engine/sessions", "body": {}})
		return
	if session_id == "":
		return
	_elapsed += delta
	_side_elapsed += delta
	if _elapsed >= poll_interval or _session.is_empty():
		_elapsed = 0
		_send_job({"op": "events", "method": HTTPClient.METHOD_GET,
			"path": "/api/engine/events?session_id=" + session_id + "&after=" + str(_revision),
			"body": {}})
	elif not _outbox.is_empty() and not _waiting_commit and _can_send(_outbox[0]):
		_send_job({"op": "action", "method": HTTPClient.METHOD_POST,
			"path": "/api/engine/action",
			"body": {"session_id": session_id, "hex": _outbox[0].hex_encode()}})
	elif _side_elapsed >= poll_interval * 2:
		_side_elapsed = 0
		_send_job({"op": "opaque", "method": HTTPClient.METHOD_GET,
			"path": "/api/engine/opaque?session_id=" + session_id + "&after=" + str(_opaque_cursor),
			"body": {}})

func _send_job(job: Dictionary) -> void:
	_active_job = job
	_busy = true
	var method: int = job.get("method", HTTPClient.METHOD_GET)
	var content := "" if method == HTTPClient.METHOD_GET else JSON.stringify(job.get("body", {}))
	var headers := PackedStringArray() if content == "" else \
		PackedStringArray(["Content-Type: application/json"])
	var error := _http.request(server_url.trim_suffix("/") + job["path"], headers,
		method, content)
	if error != OK:
		_busy = false
		_fail("HTTPRequest could not start: " + str(error))

func _on_http_completed(result: int, response_code: int,
		_headers: PackedStringArray, body: PackedByteArray) -> void:
	_busy = false
	var job := _active_job
	if result != HTTPRequest.RESULT_SUCCESS:
		_fail("Radio Arcade HTTP transport error: " + str(result))
		return
	var parsed: Variant = JSON.parse_string(body.get_string_from_utf8())
	if not (parsed is Dictionary):
		_fail("Invalid Radio Arcade JSON response")
		return
	var data: Dictionary = parsed
	if response_code < 200 or response_code >= 300:
		_fail(str(data.get("error", "Radio Arcade HTTP " + str(response_code))))
		return
	match job.get("op", ""):
		"identity":
			_callsign = str(data.get("callsign", ""))
			_transport = str(data.get("transport", ""))
			_need_identity = false
		"create":
			attach_session(str(data.get("session_id", "")))
			session_created.emit(session_id)
		"events":
			_consume_events(data)
		"opaque":
			for item in data.get("messages", []):
				_opaque_cursor = maxi(_opaque_cursor, int(item.get("id", 0)))
				if item.get("sender") != _callsign:
					side_message.emit(str(item.get("sender")), str(item.get("body")),
						int(item.get("sequence", 0)))
		"action":
			_outbox.pop_front()
			_waiting_commit = true
		"estimate":
			estimate_ready.emit(data)

func _consume_events(data: Dictionary) -> void:
	_session = data.get("session", {})
	var view: Dictionary = _session.get("view", {})
	var hash_value := int(view.get("cart_hash", 0))
	if hash_value != 0 and (hash_value != game_hash or \
		int(view.get("cart_version", 0)) != game_version or \
		view.get("mode", "") != game_mode):
		_fail("Different game ID, version or mode at this table")
		return
	for entry in data.get("events", []):
		var revision_number := int(entry.get("revision", -1))
		if revision_number != _revision + 1:
			_fail("Missing committed actions: application checkpoint required")
			return
		_revision = revision_number
		var raw: PackedByteArray = str(entry.get("hex", "")).hex_decode()
		if raw.size() > 0 and raw[0] == 1:
			var player_index: int = _session.get("players", []).find(entry.get("actor"))
			if player_index >= 0:
				event_received.emit(player_index, _revision, raw.slice(1))
		if _waiting_commit and (entry.get("actor") == _callsign or _transport == "practice"):
			_waiting_commit = false
	if int(data.get("revision", 0)) > _revision and data.get("events", []).is_empty():
		_fail("Action log incomplete: application checkpoint required")
	if hash_value == 0 and _session.get("status") == "active" and \
		_session.get("coordinator") == _callsign and not _config_queued:
		_outbox.push_front(_config_frame())
		_config_queued = true
	var now_connected := connected()
	if now_connected != _last_connected:
		_last_connected = now_connected
		status_changed.emit(now_connected)

func _fail(message: String) -> void:
	last_error = message
	_waiting_commit = false
	_retry_cooldown = 1.0
	error_occurred.emit(message)
