You are building a game-ready procedural asset in Houdini for Unreal Engine 5.8,
delivered as a Houdini Engine HDA or as FBX.

Goal: {description}

Read `get_workflow_guide("assets")` for HDA authoring itself; this guide is the
game-engine checklist on top of it. Houdini Engine for Unreal ships with Houdini
22.0.508 for UE 5.7 and 5.8 only.

## HDA or FBX

- **HDA through Houdini Engine** when the asset must stay editable in the level
  (seed, size, a curve the designer draws). Unreal cooks it, and can bake it to
  static meshes later.
- **FBX** when the asset is final, or the team has no Houdini Engine seats.
  Export with a normal FBX ROP (`filmboxfbx` in /out, or `rop_fbx` inside SOPs).
- Do both from the same SOP output, so they cannot drift apart.

## HDA interface rules for Houdini Engine

- **Inputs**: label every input and state which are optional. Geometry and curve
  inputs come in as inputs 0..n; resample and clean them inside the HDA, because
  a UE curve arrives with the designer's point spacing and nothing else.
- **Exposed parms**: only what a designer should touch (seed, size, detail,
  density), in a folder per concern, with ranges that cannot produce broken
  geometry. Everything else stays inside.
- **Seeds**: one integer `seed` parm feeding every random node, so a value
  reproduces the same asset in Houdini and in UE.
- **Declare every parm type explicitly** (`float` vs `int`) when creating spare or
  HDA parms. A parm created from the literal `20` is an int; Houdini Engine's
  `set_float_parameter_value` on it returns False silently, so scripted changes
  from UE do nothing.
- **Unreal cooks the definition defaults, not your instance's values.** After
  tuning, write the values into the definition (`edit_hda_interface` modify
  default) and `update_hda`. `get_parameters(<hda>, non_default_only=True)` lists
  what has not been promoted yet.
- To show the user where the controls are: `set_selection([hda])`, then
  `capture_screenshot(pane_name=<the Parameter pane>)`.

## Geometry checklist

- **Scale**: Houdini 1 unit = 1 m, Unreal 1 unit = 1 cm. Houdini Engine converts.
  For FBX, check the FBX ROP's unit conversion on its card (`get_node_card`) and
  confirm the size once in UE; a 100x asset is the classic mistake.
- **Pivot at the origin**, at the point the asset should stand on (base centre
  for props and trees). matchsize (justify Y to min) puts the base on the
  origin.
- **Polygon winding**: Houdini's front face winds **clockwise** seen from the
  front. Faces built by hand (add, addprim ribbons, Python SOP) came out
  inside-out twice in this project: black in the viewport, culled from above in
  UE. Fix with reverse, and check (Verify, step 3) rather than looking.
- **Normals**: a normal SOP with a cusp angle for hard edges; vertex normals
  survive export, point normals smooth everything.
- **UVs**: `labs::autouv` for procedural UVs, then uvlayout. UV channel 0 is the
  material; a second, non-overlapping channel matters only if the project uses
  baked lighting.
- **LODs**: `labs::lod_create` (prefer `labs::lod_create::2.0`) builds the
  reduced levels. For FBX LOD groups the Labs object `labs::lod_hierarchy`
  assembles them. Check triangle counts per LOD as numbers.
- **Collision**: FBX uses the UE naming convention, a separate mesh named
  `UCX_<MeshName>_00` (convex pieces, one per `_NN`). Through Houdini Engine,
  collision is a primitive group whose name starts with `collision_geo` (the
  plugin documentation lists the simple-shape variants; not re-verified here).
  Keep collision convex and low.
- **Materials**: `unreal_material` (prim string) names the UE material per part;
  a Houdini material assignment does not map onto your project's UE materials.

## Copying and instancing gotchas

- copytopoints' `pivot` parameter defaults to **centroid**: instanced trees float
  half their height. Set it to origin and author the source with its base at the
  origin.
- A string attribute (`name`) did not come through copytopoints in a live
  session. Check strings (`name`, `unreal_instance`, `unreal_material`) on the
  copies with `get_attrib_stats(..., unique=True)` and set them after the copy.
- Packed instances for Unreal: one `unreal_instance` per point is cheaper than
  copied geometry, and becomes an instanced static mesh component.

## Verify (all before export)

1. `verify_network(<asset net>, force_cook=True)`: healthy, no error nodes.
2. `get_bounding_box(<output>)`: size in metres as designed (a 2 m rock is ~2,
   not ~200 or ~0.02); `min.y` ~ 0 and the X/Z centre ~ 0 (pivot at the base centre).
3. Winding: a normal SOP (primitive class) on the output, then
   `get_attrib_stats(<it>, attribs=["N"], attrib_class="prim")`. For a ground
   ribbon N.y min must be > 0. For a closed mesh, count faces whose normal
   points toward the part's centre and expect 0 (recipe in
   `get_workflow_guide("mesh_qa")`).
4. UVs: `get_geometry_info(<output>)` lists `uv` (vertex class);
   `get_attrib_stats(..., attribs=["uv"], attrib_class="vertex")` in 0..1.
5. LODs: `get_geometry_info` on each LOD output; poly counts strictly decreasing
   at the planned ratios.
6. Collision: `get_groups(<output>)` shows the collision group (HE) or the UCX
   piece exists by `name` (FBX); its prim count is small.
7. HDA: `get_parameters(<hda>, non_default_only=True)` is empty after promoting
   tuned values; every parm type is the one intended.
8. FBX: `start_render(<fbx rop>)` reports the file on disk with a non-trivial size.

{network_housekeeping}
