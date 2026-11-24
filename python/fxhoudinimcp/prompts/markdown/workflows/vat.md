You are baking a simulation to Vertex Animation Textures (VAT) for Unreal Engine 5.8.

Goal: {description}

The SideFX Labs Vertex Animation Textures ROP writes the textures, a mesh and a
material description; Unreal plays the sim back in a material, with no
simulation at runtime. Run it with `start_render`, never by pressing its button
in `execute_python`.

Node names: `labs::vertex_animation_textures` in /out. Version 4.0 was the one
used with Houdini 22.0.508 + Labs 22.0; this repo's sampled node table
(tools/node_versions.json) only reaches `::3.0` on 22.0.368, so confirm with
`list_node_types(context="Driver", filter="vertex_animation")` and use the
highest version present.

## Which mode for which sim

| Sim | VAT mode | Why |
|---|---|---|
| RBD / packed pieces (destruction) | Rigid-Body | one position + rotation per piece; cheap, exact for rigid pieces |
| Vellum cloth, soft bodies, deformers with constant topology | Soft-Body (deforming) | one position per point, fixed point count |
| FLIP surface, anything whose topology changes per frame | Dynamic Remeshing (fluid) | stores the mesh per frame; costliest |
| Particles drawn as cards | Particle Sprites | one position per particle, turned to face the view in the material |

Pick by topology, not by solver: a vellum sim that tears changes topology.

## Settings that matter

- **`enablecooking` must be 1 on the VAT ROP.** With it off, a re-bake writes
  **all-zero EXRs with no error**. In UE that looks like a black, collapsed mesh,
  and a whole session went to UE-side suspects (material switches, UVs, bounds,
  viewpoint) before anyone read the textures. `start_render` now flags this:
  check `flat_images` in its reply before anything else.
- **Frame range**: texture height = frames (one row per frame for soft/rigid),
  width = points or pieces (wrapped into rows past the max width). Keep both at
  or under 8192, and prefer power-of-two-friendly sizes. Fewer frames or pieces
  is the fix, not a bigger texture. Bake the settled frames only.
- **Precision**: positions in HDR (EXR, 16- or 32-bit float). 8-bit PNG
  positions quantise visibly on anything larger than a few metres. Colour may be
  8-bit.
- **Bounds**: positions are stored relative to bounds the ROP measures over the
  whole range; the UE material reads those bounds/scale from the material
  parameters the Labs importer sets. A sim that flies far away from its start
  widens the bounds and costs precision: trim the range or kill escaped pieces.
- Save the hip first; the ROP's paths default under `$HIP`.

## Labs VAT 4.0 + Houdini 22.0.508: empty FBX

The ROP's internal FBX export writes an **empty FBX** on this build (it exports
an object inside the ROP network). Workaround:

1. An Object Merge (`object_merge` SOP) of `/out/<vat rop>/export/MESH/EXPORT`,
   with `enablecooking=1` on the ROP so that geometry exists.
2. Export it with a normal FBX ROP (`filmboxfbx` or SOP `rop_fbx`) to the path the
   VAT ROP's mesh would have had.
3. **Every re-bake overwrites the fixed FBX with an empty one again**: run step 2
   after every bake, and check the FBX size each time.

## Packed RBD before the bake (each cost sim rounds)

- `assemble` puts `name` on **points** (packed prims' points), not on prims.
- Per-piece collision filtering is `collisiongroup` + `collisionignore`; the
  ignore patterns match **group values**, not piece names.
- Rebar or any embedded piece **must ignore the concrete it sits in**, or Bullet
  resolves the overlap on frame 1 and the wall explodes.
- `polywire` drops `name`; set it again after.
- A string `name` did not come through `copytopoints`; check it on the copies
  (`get_attrib_stats(..., unique=True)`) and set it after copying.
- Stale SOP-solver caches look like "my fix did nothing": reset the RBD Bullet
  solver's sim (`resetsim`) before comparing numbers.

## Measure the sim with numbers, not screenshots

Keep a permanent `sim_metrics` point wrangle after the solver, rest geometry in
input 1: `vector rest = point(1, "P", @ptnum); f@disp = distance(@P, rest);`
(the typed variable avoids `point()`'s ambiguous overloads), a `broken` flag,
`f@speed = length(v@v);`. Then one call per question:

`get_attrib_stats(<sim_metrics>, attribs=["disp", "speed", "broken"], percentiles=[50, 95, 99], frames=[60, 160, 240])`

"broken %, p99 speed at f60/f160/f240" tuned three settings at once in three
calls. A settled sim has p99 speed near 0 (0.03 m/s was the bar used).

## Verify (before importing into UE)

1. `verify_network(<sim net>, force_cook=True)`: healthy.
2. `get_parameter(<vat rop>, "enablecooking")` is 1.
3. `start_render(<vat rop>, frame_range=[a, b])`: every texture path in the reply
   is on disk, **`flat_images` is absent or empty**. A flat image is the zero-EXR
   failure; stop there.
4. Texture size = what you planned: width = pieces (rigid) or points (soft),
   height = frame count (e.g. 281 x 240 for 281 pieces over 240 frames), every
   side <= 8192.
5. Frame count of the bake = `b - a + 1`.
6. Bounds: `get_bounding_box(<sim output>)` at the first and last baked frame
   (or `get_attrib_stats(..., attribs=["P"], frames=[a, b])`); no axis spans far
   more than the asset (escaped pieces).
7. FBX (22.0.508 workaround): the re-exported FBX exists and is not tiny; its
   point/prim count equals `get_geometry_info(<object merge>)`.
8. UE side (not checkable from Houdini): the Labs importer creates the material
   instance; the first frame should match frame `a` in Houdini.

{network_housekeeping}
