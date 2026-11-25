"""Recipe: a clean test level with stable lighting for judging Houdini output.

Run through unreal_python (fxhoudinimcp) or ue_exec.py: send this file's text plus a call, e.g.
    test_level("/Game/HoudiniStress/Maps/Valley", remove_floor=True)

Template_Default's auto exposure washes colours out when the camera looks down at the
bright floor; this level fixes exposure with an unbound post-process volume.
"""

import unreal


def test_level(
    level_path,
    template="/Engine/Maps/Templates/Template_Default",
    remove_floor=False,
    exposure_ev100=12.0,
):
    subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if unreal.EditorAssetLibrary.does_asset_exist(level_path):
        subsystem.load_level(level_path)
        state = "opened"
    else:
        if not subsystem.new_level_from_template(level_path, template):
            raise RuntimeError(f"could not create {level_path}")
        state = "created"
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if remove_floor:
        for actor in actors.get_all_level_actors():
            if actor.get_actor_label() in ("Floor", "SM_Template_Map_Floor"):
                actors.destroy_actor(actor)
    volumes = [a for a in actors.get_all_level_actors() if isinstance(a, unreal.PostProcessVolume)]
    ppv = (
        volumes[0]
        if volumes
        else actors.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector())
    )
    ppv.set_actor_label("PPV_FixedExposure")
    ppv.set_editor_property("unbound", True)
    settings = ppv.get_editor_property("settings")
    settings.set_editor_property("override_auto_exposure_method", True)
    settings.set_editor_property("auto_exposure_method", unreal.AutoExposureMethod.AEM_MANUAL)
    settings.set_editor_property("override_auto_exposure_apply_physical_camera_exposure", True)
    settings.set_editor_property("auto_exposure_apply_physical_camera_exposure", False)
    settings.set_editor_property("override_auto_exposure_bias", True)
    settings.set_editor_property("auto_exposure_bias", 0.0)
    ppv.set_editor_property("settings", settings)
    subsystem.save_current_level()
    print(f"level {state}: {level_path}")
    return state
