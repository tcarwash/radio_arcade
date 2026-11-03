@tool
extends EditorPlugin

func _enter_tree() -> void:
	add_custom_type("RadioArcadeNode", "Node",
		preload("res://addons/radio_arcade/radio_arcade_client.gd"), null)

func _exit_tree() -> void:
	remove_custom_type("RadioArcadeNode")
