"""Place copies of a VAT mesh frozen at chosen frames, side by side. Run with ue_exec.py.

Useful to judge a bake from one screenshot. Each copy gets a material instance with
Auto Playback off and Display Frame set.
"""

import unreal

DEST = "/Game/VAT/Wall"
MESH = f"{DEST}/vat_wall_mesh"
PARENT_MI = f"{DEST}/MI_vat_wall"  # the instance the Labs importer made
FRAMES = (6, 24, 240)
SPACING = 1300.0

eal = unreal.EditorAssetLibrary
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
lib = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
mesh = unreal.load_asset(MESH)
parent = unreal.load_asset(PARENT_MI)

for i, frame in enumerate(FRAMES):
    path = f"{DEST}/MI_frame_{frame}"
    if eal.does_asset_exist(path):
        eal.delete_asset(path)
    mi = tools.create_asset(
        f"MI_frame_{frame}",
        DEST,
        unreal.MaterialInstanceConstant,
        unreal.MaterialInstanceConstantFactoryNew(),
    )
    lib.set_material_instance_parent(mi, parent)
    lib.set_material_instance_scalar_parameter_value(mi, "Use Playback Controls", 0.0)
    lib.set_material_instance_scalar_parameter_value(mi, "Display Frame", float(frame))
    lib.set_material_instance_static_switch_parameter_value(mi, "Auto Playback", False)
    eal.save_asset(path)
    actor = eas.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector((i - (len(FRAMES) - 1) / 2) * SPACING, 0, 0)
    )
    actor.static_mesh_component.set_static_mesh(mesh)
    actor.static_mesh_component.set_material(0, mi)
    actor.set_actor_label(f"VAT_frame_{frame}")
unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level()
print("placed", FRAMES)
