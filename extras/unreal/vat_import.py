"""Import a Labs VAT 4.0 bake into Unreal and build a test level. Run with ue_exec.py.

Edit the four settings below. The Labs Unreal plugin (SideFX_Labs) must be enabled in the project.
"""

import json

import unreal
from labs_vat_importer import Settings, import_vat

EXPORT_DIR = r"C:/path/to/HoudiniProjects/vat_wall/export/vat/"  # the VAT ROP's exportpath, with a trailing /
NAME = "vat_wall"  # file prefix the VAT ROP used
MODE = "Rigid"  # Rigid | Soft | Fluid | Sprite
DEST = "/Game/VAT/Wall"

eal = unreal.EditorAssetLibrary
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
out = {}

if eal.does_directory_exist(DEST):
    eal.delete_directory(DEST)
settings = Settings(
    mode=MODE,
    meshes=[EXPORT_DIR + f"geo/{NAME}_mesh.fbx"],
    textures=[EXPORT_DIR + f"tex/{NAME}_{k}.exr" for k in ("pos", "rot", "col")],
    destination=DEST,
    material_name=NAME,
    fps=24.0,
    loop=True,
)
out["import_result"] = str(import_vat(settings))[:300]
assets = [unreal.load_asset(a) for a in eal.list_assets(DEST, recursive=True, include_folder=False)]
mesh = next(a for a in assets if isinstance(a, unreal.StaticMesh))
out["assets"] = [a.get_name() for a in assets]

# A bare test level: floor + the VAT mesh (auto playback, using the importer's material instance).
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
les.new_level(f"/Game/Maps/{NAME}_test")
floor = eas.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 0))
floor.static_mesh_component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Plane"))
floor.set_actor_scale3d(unreal.Vector(60, 60, 1))
wall = eas.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 0))
wall.static_mesh_component.set_static_mesh(mesh)
eas.spawn_actor_from_class(
    unreal.DirectionalLight, unreal.Vector(0, 0, 3000), unreal.Rotator(0, -40, 50)
)
eas.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
eas.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 2000))
les.save_current_level()
print(json.dumps(out, default=str))
