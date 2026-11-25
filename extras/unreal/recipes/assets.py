"""Recipe: bring Houdini exports (textures, FBX) into Unreal.

Run through unreal_python (fxhoudinimcp) or ue_exec.py: send this file's text plus a call, e.g.
    import_texture("C:/proj/export/flowmap_river.png", "/Game/HoudiniStress", srgb=False)
"""

import unreal


def import_fbx_character(file_path, destination, name=None, import_animations=True):
    """Import a KineFX FBX (ROP FBX Character Output) as SkeletalMesh + Skeleton + AnimSequence.

    Returns {"mesh", "skeleton", "animations"} object paths.
    """
    options = unreal.FbxImportUI()
    options.set_editor_property("import_mesh", True)
    options.set_editor_property("import_as_skeletal", True)
    options.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_SKELETAL_MESH)
    options.set_editor_property("import_animations", import_animations)
    options.set_editor_property("import_materials", False)
    options.set_editor_property("import_textures", False)
    options.set_editor_property("create_physics_asset", False)
    task = unreal.AssetImportTask()
    task.filename = file_path
    task.destination_path = destination
    if name:
        task.destination_name = name
    task.automated = True
    task.replace_existing = True
    task.save = True
    task.options = options
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    out = {"mesh": None, "skeleton": None, "animations": []}
    for path in task.imported_object_paths:
        asset = unreal.load_asset(path)
        if isinstance(asset, unreal.SkeletalMesh):
            out["mesh"] = asset.get_path_name()
            out["skeleton"] = asset.skeleton.get_path_name() if asset.skeleton else None
        elif isinstance(asset, unreal.AnimSequence):
            out["animations"].append(
                {"path": asset.get_path_name(), "seconds": round(asset.get_play_length(), 3)}
            )
    if not out["mesh"]:
        raise RuntimeError(
            f"no skeletal mesh imported from {file_path}: {list(task.imported_object_paths)}"
        )
    print("fbx character:", out)
    return out


def import_texture(file_path, destination, srgb=True, compression=None, name=None):
    """Import an image as a Texture2D. Data maps (flow, masks, normals) need srgb=False."""
    task = unreal.AssetImportTask()
    task.filename = file_path
    task.destination_path = destination
    if name:
        task.destination_name = name
    task.automated = True
    task.replace_existing = True
    task.save = False
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    paths = list(task.imported_object_paths)
    if not paths:
        raise RuntimeError(f"importing {file_path} produced nothing")
    tex = unreal.load_asset(paths[0])
    tex.set_editor_property("srgb", srgb)
    if compression is not None:
        tex.set_editor_property("compression_settings", compression)
    elif not srgb:
        tex.set_editor_property(
            "compression_settings", unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP
        )
    unreal.EditorAssetLibrary.save_asset(tex.get_path_name())
    print(
        "texture:",
        tex.get_path_name(),
        tex.blueprint_get_size_x(),
        "x",
        tex.blueprint_get_size_y(),
        "srgb",
        srgb,
    )
    return tex.get_path_name()
