"""Point the editor viewport at a target and save a screenshot. Run with ue_exec.py.

The file lands in <Project>/Saved/Screenshots/WindowsEditor/. The editor writes
NOTHING while its window is minimized: restore it first (see restore_editor.ps1).
"""

import math

import unreal

CAMERA = unreal.Vector(-400, -2300, 650)
TARGET = unreal.Vector(0, 300, 120)
FILENAME = "shot.png"

d = TARGET - CAMERA
rot = unreal.Rotator(
    0, math.degrees(math.atan2(d.z, math.hypot(d.x, d.y))), math.degrees(math.atan2(d.y, d.x))
)
unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
    CAMERA, rot
)
unreal.AutomationLibrary.take_high_res_screenshot(1800, 800, FILENAME)
print("requested", FILENAME)
