"""Houdini Engine tools for Epic's Unreal MCP (AI Toolset Registry).

Every tool names actors by their outliner label, so an agent can go from
list_hda_actors to get/set parameters without object paths. Results that carry
structure are JSON strings.
"""

import json

import toolset_registry
import unreal
from toolset_registry.registration import Registration

_HOUDINI_ACTOR = getattr(unreal, "HoudiniAssetActor", None)


def _api():
    """The Houdini Engine API, with a live session.

    A session whose HARS process crashed reports invalid and does not come back
    with create_session; restart_session does.
    """
    api = unreal.HoudiniPublicAPIBlueprintLib.get_api()
    if not api.is_session_valid():
        api.restart_session()
    if not api.is_session_valid():
        api.create_session()
    if not api.is_session_valid():
        raise RuntimeError("No Houdini Engine session (licence, or Houdini install not found).")
    return api


def _out(value):
    """UE Python returns (ok, out) or out for C++ out-params; take the out value."""
    return value[-1] if isinstance(value, tuple) else value


def _houdini_actors() -> list:
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    return [a for a in actors if _HOUDINI_ACTOR and isinstance(a, _HOUDINI_ACTOR)]


def _actor(label: str):
    found = [a for a in _houdini_actors() if a.get_actor_label() == label]
    if not found:
        names = [a.get_actor_label() for a in _houdini_actors()]
        raise RuntimeError(
            f"No Houdini asset actor labelled '{label}'. In this level: {names or 'none'}."
        )
    return found[0]


def _wrapper(label: str):
    actor = _actor(label)
    wrapper = unreal.HoudiniPublicAPIAssetWrapper.create_wrapper(actor, actor)
    if wrapper is None:
        raise RuntimeError(f"Could not wrap '{label}' (is its Houdini session running?).")
    return wrapper


VERTEX_COLOR_MATERIAL = "/HoudiniEngineToolset/Materials/M_HoudiniVertexColor"


def ensure_vertex_color_material() -> str:
    """A lit material whose base colour is the mesh's vertex colour (Houdini Cd).

    Houdini Engine's default material ignores Cd. HDAs that colour their output
    point `unreal_material` at this one. Created in the plugin's own content the
    first time, so every project with the plugin has it.
    """
    if unreal.EditorAssetLibrary.does_asset_exist(VERTEX_COLOR_MATERIAL):
        return VERTEX_COLOR_MATERIAL + "." + VERTEX_COLOR_MATERIAL.rsplit("/", 1)[1]
    folder, name = VERTEX_COLOR_MATERIAL.rsplit("/", 1)
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, unreal.Material, unreal.MaterialFactoryNew()
    )
    mel = unreal.MaterialEditingLibrary
    vc = mel.create_material_expression(material, unreal.MaterialExpressionVertexColor, -400, 0)
    rough = mel.create_material_expression(material, unreal.MaterialExpressionConstant, -400, 200)
    rough.set_editor_property("r", 0.85)
    mel.connect_material_property(vc, "", unreal.MaterialProperty.MP_BASE_COLOR)
    mel.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
    mel.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(VERTEX_COLOR_MATERIAL)
    return material.get_path_name()


def _asset_name(actor):
    """The HDA an actor instantiates, as Houdini Engine reports it (None when it does not say)."""
    try:
        info = unreal.HoudiniPublicAPIAssetWrapper.create_wrapper(
            actor, actor
        ).get_houdini_asset_info()
        info = _out(info)
        for field in ("asset_name", "full_op_name", "label"):
            value = getattr(info, field, None)
            if value:
                return str(value)
    except Exception:
        pass
    return None


def _tuple_values(tup) -> list:
    for field in ("float_values", "int32_values", "bool_values", "string_values"):
        values = list(getattr(tup, field, []) or [])
        if values:
            return [round(v, 6) if isinstance(v, float) else v for v in values]
    return []


@unreal.uclass()
class HoudiniEngineTools(unreal.ToolsetDefinition):
    """Import Houdini Digital Assets (HDAs) into the project, place them in the
    level, read and change their parameters, recook and bake them. Parameter
    changes recook the asset in the editor, exactly as editing them in the
    Details panel does."""

    @toolset_registry.tool_call
    @staticmethod
    def import_hda(file_path: str, destination_folder: str = "/Game/HoudiniAssets") -> str:
        """Imports an .hda/.hdalc/.otl file as a Houdini Asset, replacing an earlier import.

        Re-importing a changed HDA while its old definition is loaded in the
        session can crash Houdini Engine's HARS on the next spawn; call
        restart_houdini_session after re-importing a changed file.

        Args:
            file_path: Absolute path of the HDA file on disk.
            destination_folder: Content folder for the asset, e.g. /Game/HoudiniAssets.

        Returns:
            The imported asset's object path.
        """
        task = unreal.AssetImportTask()
        task.filename = file_path
        task.destination_path = destination_folder
        task.automated = True
        task.replace_existing = True
        task.save = True
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        paths = list(task.imported_object_paths)
        if not paths:
            raise RuntimeError(f"Importing {file_path} produced no asset (is it a valid HDA?).")
        return str(paths[0])

    @toolset_registry.tool_call
    @staticmethod
    def spawn_hda(
        asset_path: str, label: str, x: float = 0.0, y: float = 0.0, z: float = 0.0
    ) -> str:
        """Places a Houdini Asset in the current level and starts its cook.

        The cook runs in the background: call get_hda_status until num_outputs > 0.

        Args:
            asset_path: Object path of an imported Houdini Asset (from import_hda).
            label: Outliner label for the new actor; an existing actor with it is replaced.
            x: Location X in centimetres.
            y: Location Y in centimetres.
            z: Location Z in centimetres.

        Returns:
            The label of the placed actor.
        """
        asset = unreal.load_asset(asset_path)
        if asset is None:
            raise RuntimeError(f"No asset at {asset_path}.")
        subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        for old in [a for a in _houdini_actors() if a.get_actor_label() == label]:
            subsystem.destroy_actor(old)
        transform = unreal.Transform(location=unreal.Vector(x, y, z))
        wrapper = _api().instantiate_asset(asset, transform, enable_auto_cook=True)
        actor = wrapper.get_houdini_asset_actor() if wrapper else None
        if actor is None:
            raise RuntimeError(f"Houdini Engine did not instantiate {asset_path}.")
        actor.set_actor_label(label)
        return actor.get_actor_label()

    @toolset_registry.tool_call
    @staticmethod
    def list_hda_actors() -> str:
        """Lists the Houdini asset actors in the current level.

        Returns:
            JSON list of {label, asset, location_cm}.
        """
        rows = []
        for actor in _houdini_actors():
            loc = actor.get_actor_location()
            rows.append(
                {
                    "label": actor.get_actor_label(),
                    "ref_path": actor.get_path_name(),
                    "location_cm": [round(loc.x, 1), round(loc.y, 1), round(loc.z, 1)],
                }
            )
        return json.dumps(rows)

    @toolset_registry.tool_call
    @staticmethod
    def get_hda_parameters(actor_label: str) -> str:
        """Reads every parameter of a placed Houdini asset.

        Args:
            actor_label: Outliner label of the Houdini asset actor.

        Returns:
            JSON object of parameter name -> list of values (one per component).
        """
        tuples = _out(_wrapper(actor_label).get_parameter_tuples()) or {}
        return json.dumps({str(name): _tuple_values(tup) for name, tup in tuples.items()})

    @toolset_registry.tool_call
    @staticmethod
    def set_hda_parameters(actor_label: str, parameters_json: str) -> str:
        """Sets parameters on a placed Houdini asset; the asset then recooks.

        Args:
            actor_label: Outliner label of the Houdini asset actor.
            parameters_json: JSON object of name -> value or list of values,
                e.g. {"height": 30, "seed": 7, "rock_color": [0.5, 0.4, 0.3]}.

        Returns:
            JSON {"set": [...], "failed": [...]} naming each parameter.
        """
        wrapper = _wrapper(actor_label)
        wanted = json.loads(parameters_json)
        current = _out(wrapper.get_parameter_tuples()) or {}
        done, failed = [], []
        for name, value in wanted.items():
            values = value if isinstance(value, list) else [value]
            existing = current.get(unreal.Name(name)) or current.get(name)
            if existing is None:
                failed.append({"name": name, "reason": "no such parameter"})
                continue
            ok = True
            for index, v in enumerate(values):
                if list(getattr(existing, "int32_values", []) or []):
                    ok &= bool(wrapper.set_int_parameter_value(name, int(v), index))
                elif list(getattr(existing, "bool_values", []) or []):
                    ok &= bool(wrapper.set_bool_parameter_value(name, bool(v), index))
                elif list(getattr(existing, "string_values", []) or []):
                    ok &= bool(wrapper.set_string_parameter_value(name, str(v), index))
                else:
                    ok &= bool(wrapper.set_float_parameter_value(name, float(v), index))
            (done if ok else failed).append(name if ok else {"name": name, "reason": "set refused"})
        return json.dumps({"set": done, "failed": failed})

    @toolset_registry.tool_call
    @staticmethod
    def recook_hda(actor_label: str, rebuild: bool = False) -> bool:
        """Starts a recook of a placed Houdini asset (non-blocking).

        Args:
            actor_label: Outliner label of the Houdini asset actor.
            rebuild: Re-instantiate from the current HDA definition first. Needed after
                import_hda re-imports a changed file: until then the actor keeps the old
                parameter interface and contents.

        Returns:
            True if the recook (or rebuild) was queued.
        """
        wrapper = _wrapper(actor_label)
        return bool(wrapper.rebuild() if rebuild else wrapper.recook())

    @toolset_registry.tool_call
    @staticmethod
    def get_hda_status(actor_label: str) -> str:
        """Reports whether a Houdini asset has cooked and what it produced.

        Args:
            actor_label: Outliner label of the Houdini asset actor.

        Returns:
            JSON {num_outputs, has_proxy_output, auto_cook, bounds_extent_cm}.
        """
        wrapper = _wrapper(actor_label)
        actor = _actor(actor_label)
        origin, extent = actor.get_actor_bounds(False)
        return json.dumps(
            {
                "session_valid": unreal.HoudiniPublicAPIBlueprintLib.get_api().is_session_valid(),
                "num_outputs": wrapper.get_num_outputs(),
                "has_proxy_output": wrapper.has_any_current_proxy_output(),
                "auto_cook": wrapper.is_auto_cooking_enabled(),
                "bounds_extent_cm": [round(extent.x, 1), round(extent.y, 1), round(extent.z, 1)],
            }
        )

    @toolset_registry.tool_call
    @staticmethod
    def get_hda_inputs(actor_label: str) -> str:
        """Lists a placed Houdini asset's inputs and, for curve inputs, their points.

        Args:
            actor_label: Outliner label of the Houdini asset actor.

        Returns:
            JSON list of {index, type, objects}; a curve object carries points_cm and closed.
        """
        wrapper = _wrapper(actor_label)
        rows = []
        for index in range(wrapper.get_num_node_inputs()):
            inp = _out(wrapper.get_input_at_index(index))
            row = {"index": index, "type": type(inp).__name__ if inp else None, "objects": []}
            for obj in (inp.get_input_objects() if inp else []) or []:
                if isinstance(obj, unreal.HoudiniPublicAPICurveInputObject):
                    pts = [t.translation for t in obj.get_curve_points()]
                    row["objects"].append(
                        {
                            "curve": True,
                            "closed": obj.is_closed(),
                            "points_cm": [
                                [round(p.x, 1), round(p.y, 1), round(p.z, 1)] for p in pts
                            ],
                        }
                    )
                else:
                    row["objects"].append({"object": obj.get_path_name() if obj else None})
            rows.append(row)
        return json.dumps(rows)

    @toolset_registry.tool_call
    @staticmethod
    def set_hda_curve_input(
        actor_label: str,
        input_index: int,
        points_json: str,
        closed: bool = False,
        curve_type: str = "polygon",
    ) -> str:
        """Replaces a Houdini asset input with a curve (a spline you can then edit in the viewport).

        The asset recooks. Points are in Unreal world space, centimetres; Houdini Engine converts.

        Args:
            actor_label: Outliner label of the Houdini asset actor.
            input_index: Which node input (0 is the first).
            points_json: JSON list of [x, y, z] points in cm, e.g. [[0,0,0],[1000,500,0]].
            closed: Close the curve.
            curve_type: polygon, nurbs or bezier.

        Returns:
            JSON {input_index, points, closed}.
        """
        wrapper = _wrapper(actor_label)
        points = json.loads(points_json)
        if len(points) < 2:
            raise RuntimeError("A curve needs at least 2 points.")
        curve_input = wrapper.create_empty_input(unreal.HoudiniPublicAPICurveInput)
        curve = unreal.HoudiniPublicAPICurveInputObject(curve_input)
        curve.set_curve_points(
            [unreal.Transform(location=unreal.Vector(*map(float, p))) for p in points]
        )
        curve.set_closed(closed)
        curve.set_curve_type(getattr(unreal.HoudiniPublicAPICurveType, curve_type.upper()))
        curve_input.set_input_objects([curve])
        if not wrapper.set_input_at_index(input_index, curve_input):
            raise RuntimeError(f"Houdini Engine refused a curve on input {input_index}.")
        return json.dumps({"input_index": input_index, "points": len(points), "closed": closed})

    @toolset_registry.tool_call
    @staticmethod
    def set_hda_object_input(actor_label: str, input_index: int, asset_paths_json: str) -> str:
        """Feeds Unreal assets or level actors into a Houdini asset input.

        Level actors (a Landscape arrives as a heightfield, splines as curves) use a World
        input; assets (static meshes) a Geometry input. Chosen from what is passed.

        Args:
            actor_label: Outliner label of the Houdini asset actor.
            input_index: Which node input (0 is the first).
            asset_paths_json: JSON list of object paths, e.g. ["/Game/Meshes/SM_Rock.SM_Rock"].

        Returns:
            JSON {input_index, objects}.
        """
        wrapper = _wrapper(actor_label)
        objects = [unreal.load_object(None, p) for p in json.loads(asset_paths_json)]
        missing = [
            p for p, o in zip(json.loads(asset_paths_json), objects, strict=True) if o is None
        ]
        if missing:
            raise RuntimeError(f"Not found: {missing}")
        # Actors in the level (a Landscape, a spline actor, placed meshes) go through a World
        # input; a Geometry input refuses them ("not of an acceptable type"). Assets use Geometry.
        is_actor = any(isinstance(o, unreal.Actor) for o in objects)
        kind = unreal.HoudiniPublicAPIWorldInput if is_actor else unreal.HoudiniPublicAPIGeoInput
        inp = wrapper.create_empty_input(kind)
        inp.set_input_objects(objects)
        if not wrapper.set_input_at_index(input_index, inp):
            raise RuntimeError(f"Houdini Engine refused those objects on input {input_index}.")
        return json.dumps(
            {"input_index": input_index, "objects": len(objects), "input_type": kind.__name__}
        )

    @toolset_registry.tool_call
    @staticmethod
    def frame_hda(
        actor_label: str, pitch: float = -15.0, yaw: float = 35.0, distance_scale: float = 2.5
    ) -> str:
        """Points the level viewport camera at a Houdini asset, so a capture shows it whole.

        Follow with EditorAppToolset CaptureViewport, passing the returned camera as captureTransform.

        Args:
            actor_label: Outliner label of the Houdini asset actor.
            pitch: Camera pitch in degrees (negative looks down).
            yaw: Camera yaw in degrees around Z.
            distance_scale: Camera distance as a multiple of the asset's bounding radius.

        Returns:
            JSON camera {location, rotation, scale} as CaptureViewport's captureTransform takes it.
        """
        origin, extent = _actor(actor_label).get_actor_bounds(False)
        radius = max(extent.length(), 100.0)
        rotation = unreal.Rotator(roll=0.0, pitch=pitch, yaw=yaw)
        location = origin - rotation.get_forward_vector() * radius * distance_scale
        unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
            location, rotation
        )
        return json.dumps(
            {
                "location": {"x": location.x, "y": location.y, "z": location.z},
                "rotation": {"pitch": pitch, "yaw": yaw, "roll": 0.0},
                "scale": {"x": 1, "y": 1, "z": 1},
            }
        )

    @toolset_registry.tool_call
    @staticmethod
    def bake_hda(actor_label: str) -> str:
        """Bakes a Houdini asset's outputs to regular Unreal assets and actors.

        Args:
            actor_label: Outliner label of the Houdini asset actor.

        Returns:
            JSON list of the baked actors' labels.
        """
        wrapper = _wrapper(actor_label)
        if not wrapper.bake_all_outputs():
            raise RuntimeError(f"Bake of '{actor_label}' failed.")
        return json.dumps([a.get_actor_label() for a in (wrapper.get_baked_output_actors() or [])])

    @toolset_registry.tool_call
    @staticmethod
    def refine_hda(actor_label: str, disable_proxy: bool = True) -> str:
        """Turns a Houdini asset's fast proxy meshes into real static meshes.

        Proxy meshes (Houdini Engine's preview output) carry no vertex colours,
        collision or Nanite; a capture of a proxy looks unshaded. disable_proxy
        keeps later recooks producing static meshes straight away.

        Args:
            actor_label: Outliner label of the Houdini asset actor.
            disable_proxy: Stop this asset from cooking to proxies from now on.

        Returns:
            JSON {refined, proxy_enabled}.
        """
        wrapper = _wrapper(actor_label)
        if disable_proxy:
            wrapper.set_proxy_mesh_enabled(False)
        refined = wrapper.refine_all_current_proxy_outputs(True)
        return json.dumps(
            {"refined": str(refined), "proxy_enabled": wrapper.is_proxy_mesh_enabled()}
        )

    @toolset_registry.tool_call
    @staticmethod
    def restart_houdini_session() -> bool:
        """Restarts the Houdini Engine session (after a HARS crash, or to drop stale HDA definitions).

        Placed Houdini assets recook in the new session.

        Returns:
            True if a session is valid afterwards.
        """
        api = unreal.HoudiniPublicAPIBlueprintLib.get_api()
        api.restart_session()
        return bool(api.is_session_valid())


registration = Registration([HoudiniEngineTools])
