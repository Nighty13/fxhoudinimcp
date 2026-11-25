"""Recipe: materials Houdini Engine output needs in Unreal.

Run through unreal_python (fxhoudinimcp) or ue_exec.py: send this file's text plus a call, e.g.
    landscape_layer_material("/Game/HoudiniStress/M_ValleyLandscape",
                             {"grass": (0.22, 0.38, 0.12), "rock": (0.42, 0.38, 0.34), "riverbed": (0.55, 0.48, 0.34)})

Houdini Engine drops every landscape paint layer silently unless the landscape's material
has a Layer Blend naming those layers (see the unreal_landscape guide). This makes one with
a flat colour per layer, enough to see the layers land.
"""

import unreal

MEL = unreal.MaterialEditingLibrary


def _new_material(path):
    folder, name = path.rsplit("/", 1)
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    return unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, unreal.Material, unreal.MaterialFactoryNew()
    )


def _custom(material, code, inputs, output_type, x, y, description):
    node = MEL.create_material_expression(material, unreal.MaterialExpressionCustom, x, y)
    node.set_editor_property("code", code)
    node.set_editor_property("output_type", output_type)
    node.set_editor_property("description", description)
    pins = []
    for name in inputs:
        pin = unreal.CustomInput()
        pin.set_editor_property("input_name", name)
        pins.append(pin)
    node.set_editor_property("inputs", pins)
    return node


FLOW_UV_HLSL = """
// World position (cm) -> flowmap UV. Houdini heightfield_output images run
// u along Houdini Z (= UE Y) and v along -Houdini X (= -UE X).
return float2((WP.y - MinY) / SizeY, (MaxX - WP.x) / SizeX);
"""

FLOW_COLOR_HLSL = """
// Two-phase flowmap advection of a procedural ripple pattern.
// Flow.rg: direction (0.5 = still), Flow.b: river mask.
// r = Houdini X = UE X, g = Houdini Z = UE Y.
float2 f = (Flow.rg * 2 - 1);
float2 uv = WP.xy / Tiling;
float t0 = frac(Time * Speed);
float t1 = frac(Time * Speed + 0.5);
float w = abs(t0 - 0.5) * 2;
float2 a = uv - f * t0 * Strength;
float2 b = uv - f * t1 * Strength;
float pa = sin(a.x * 18.85) * sin(a.y * 18.85 + sin(a.x * 9.0));
float pb = sin(b.x * 18.85) * sin(b.y * 18.85 + sin(b.x * 9.0));
float p = lerp(pa, pb, w) * 0.5 + 0.5;
float3 deep = float3(0.02, 0.10, 0.16);
float3 foam = float3(0.40, 0.62, 0.72);
return lerp(deep, foam, saturate(p * p * Flow.b * 1.6));
"""


def flow_water_material(
    path, flow_texture, min_x, max_x, min_y, max_y, tiling=600.0, speed=0.25, strength=1.2
):
    """Opaque water that advects a ripple pattern along a Houdini-baked flowmap.

    min/max are the flowmap's world bounds in cm (the heightfield it was baked on).
    """
    material = _new_material(path)
    wp = MEL.create_material_expression(material, unreal.MaterialExpressionWorldPosition, -1400, 0)
    uv = _custom(
        material,
        FLOW_UV_HLSL,
        ["WP"],
        unreal.CustomMaterialOutputType.CMOT_FLOAT2,
        -1100,
        0,
        "FlowmapUV",
    )
    uv_src = FLOW_UV_HLSL.replace("MinY", f"{min_y:.1f}").replace("SizeY", f"{max_y - min_y:.1f}")
    uv_src = uv_src.replace("MaxX", f"{max_x:.1f}").replace("SizeX", f"{max_x - min_x:.1f}")
    uv.set_editor_property("code", uv_src)
    MEL.connect_material_expressions(wp, "", uv, "WP")
    tex = MEL.create_material_expression(material, unreal.MaterialExpressionTextureSample, -800, 0)
    tex.set_editor_property("texture", unreal.load_asset(flow_texture))
    tex.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
    MEL.connect_material_expressions(uv, "", tex, "UVs")
    time = MEL.create_material_expression(material, unreal.MaterialExpressionTime, -800, 300)
    src = FLOW_COLOR_HLSL.replace("Tiling", f"{tiling:.1f}").replace("Speed", f"{speed:.3f}")
    src = src.replace("Strength", f"{strength:.3f}")
    colour = _custom(
        material,
        src,
        ["Flow", "WP", "Time"],
        unreal.CustomMaterialOutputType.CMOT_FLOAT3,
        -450,
        0,
        "FlowColour",
    )
    MEL.connect_material_expressions(tex, "RGB", colour, "Flow")
    MEL.connect_material_expressions(wp, "", colour, "WP")
    MEL.connect_material_expressions(time, "", colour, "Time")
    rough = MEL.create_material_expression(material, unreal.MaterialExpressionConstant, -450, 250)
    rough.set_editor_property("r", 0.08)
    MEL.connect_material_property(colour, "", unreal.MaterialProperty.MP_BASE_COLOR)
    MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
    MEL.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(path)
    print("flow water material:", material.get_path_name())
    return material.get_path_name()


def flat_material(path, rgb, roughness=0.5):
    """A one-colour lit material, for checking a mesh lands (no textures needed)."""
    material = _new_material(path)
    colour = MEL.create_material_expression(
        material, unreal.MaterialExpressionConstant3Vector, -400, 0
    )
    colour.set_editor_property("constant", unreal.LinearColor(*rgb, 1.0))
    rough = MEL.create_material_expression(material, unreal.MaterialExpressionConstant, -400, 200)
    rough.set_editor_property("r", roughness)
    MEL.connect_material_property(colour, "", unreal.MaterialProperty.MP_BASE_COLOR)
    MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
    MEL.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(path)
    return material.get_path_name()


def landscape_layer_material(path, layers, roughness=0.9):
    """A landscape material with a weight-blended Layer Blend, one flat colour per layer."""
    material = _new_material(path)
    blend = MEL.create_material_expression(
        material, unreal.MaterialExpressionLandscapeLayerBlend, -300, 0
    )
    inputs = []
    for name in layers:
        entry = unreal.LayerBlendInput()  # UE structs take no keyword arguments here
        entry.set_editor_property("layer_name", name)
        entry.set_editor_property("blend_type", unreal.LandscapeLayerBlendType.LB_WEIGHT_BLEND)
        entry.set_editor_property("preview_weight", 1.0)
        inputs.append(entry)
    blend.set_editor_property("layers", inputs)
    for i, (name, rgb) in enumerate(layers.items()):
        colour = MEL.create_material_expression(
            material, unreal.MaterialExpressionConstant3Vector, -700, i * 150
        )
        colour.set_editor_property("constant", unreal.LinearColor(*rgb, 1.0))
        if not MEL.connect_material_expressions(colour, "", blend, f"Layer {name}"):
            raise RuntimeError(f"could not wire layer {name}")
    rough = MEL.create_material_expression(material, unreal.MaterialExpressionConstant, -300, 300)
    rough.set_editor_property("r", roughness)
    MEL.connect_material_property(blend, "", unreal.MaterialProperty.MP_BASE_COLOR)
    MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
    MEL.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(path)
    print("landscape material:", material.get_path_name(), "layers:", list(layers))
    return material.get_path_name()
