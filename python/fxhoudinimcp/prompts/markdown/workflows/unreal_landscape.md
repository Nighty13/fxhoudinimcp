**Paint layers need `unreal_material` naming a landscape material that has a Layer Blend for those layer names. Without it, Houdini Engine 3.0 silently drops every weight layer: heights arrive, layers do not, and nothing reports an error.**

You are building terrain in Houdini for an Unreal Engine 5.8 Landscape.

Goal: {description}

Houdini Engine for Unreal ships with Houdini 22.0.508 for UE 5.7 and 5.8 only
(`Houdini Engine/Unreal/22.0.508/<ver>/`); UE 5.9 is not covered by that build.
Read `get_workflow_guide("heightfields")` for the shaping itself; this guide is
what changes when the result has to land in Unreal. Two routes exist and both
start from the same heightfield:

- **Live HDA through Houdini Engine**: the heightfield (height + mask layers) and
  instance points are HDA outputs; Unreal cooks the HDA. Layers become paint
  layers through `unreal_*` attributes. Best for anything the level designer
  should keep editing (a river curve as an input).
- **Files**: heightfield_output writes a 16-bit heightmap and 8-bit/16-bit layer
  images that are imported by hand. heightfield_output exports **image files**;
  convertheightfield is the node that makes polygon geometry, and Unreal never
  needs that for a Landscape.

## Size and spacing (the mistake that costs a rebuild)

UE Landscape vertex counts per side: **1009, 2017, 4033, 8129** (each is
sections x quads + 1). Pick one first, then the cell spacing in metres; the
world size is `(verts - 1) * spacing`.

- A heightfield's resolution is size / grid spacing, truncated. A shaping stage
  at 4 m on a 2016 m terrain gives 504 samples, and resampling 504 by spacing
  gave 2016, not 2017. **Snap every intermediate spacing to `size / round(size / spacing)`**,
  and do the **final resample by sample count** (2017), never by spacing.
- Read the result with `get_volume_info(node)`: the `height` layer's resolution
  must be exactly verts x verts.
- Houdini works in metres, Unreal in centimetres. Houdini Engine converts
  (x100). For the file route, UE's Landscape XY scale = spacing in metres x 100.

## Height range and Z scale (file route)

heightfield_output can auto-remap the height range into 0..65535, but **the
range it used is not stored** (`red_auto_range` stays 0 after an export).
Measure it yourself:

1. `get_volume_info(node)` → `height` min and max (metres).
2. UE maps a 16-bit heightmap onto 512 m at Z scale 100. So
   `Z scale = (max - min) * 100 / 512`, and the Landscape actor's Z location is
   `(min + max) / 2 * 100` cm. (Standard UE heightmap convention; check one
   known peak inside UE after import.)
3. Write the heightmap 16-bit (PNG or RAW). An 8-bit heightmap terraces visibly.

Through Houdini Engine none of this is needed: HE reads the height values and
sets the scale. Heights were verified to match to the centimetre in UE 5.8.4.

## Weight layers

- One heightfield layer per **landscape material layer**, named exactly as the
  material's Layer Blend names them. A third-party landscape material may be
  auto-material driven (an `auto` layer drives texturing and grass, manual
  layers are overrides): painting `auto = 0` everywhere disabled a pack's whole
  look. Find out which layers the material actually reads before mapping.
- Normalise weight-blended layers so they sum to 1 per cell (a heightfield_layer
  combine or a volume wrangle dividing by the sum). Check with
  `get_volume_info`: means like 0.87 / 0.07 / 0.07 sum to ~1.
- `mask` is a shared scratch channel: copy every mask you keep into its own named
  layer with heightfield_copylayer before the next mask node overwrites it.
- A feature narrower than ~2 heightfield cells cannot be represented. Compare the
  cell size to the smallest road, bank or step before blaming the logic.

## Scatter and instances

- heightfield_scatter (use `heightfield_scatter::2.0`) **copies every heightfield
  layer onto the scatter points as point attributes** (20+ attributes). Delete
  them with attribdelete before the points leave the HDA; keep only what UE
  reads (`unreal_instance`, orient/pscale/N/up, any `unreal_uproperty_*`).
- Its coverage **ignores soft mask values**: any mask value above 0 counts as
  fully inside. To get clustering from a soft mask, scatter, then cull points by
  the mask value (group by expression on the sampled mask, or a wrangle comparing
  the mask to a random number), and measure the survivors.
- `unreal_instance` (string, point) is the asset path each point instances,
  e.g. `/Game/Foliage/SM_Pine.SM_Pine`. One string per variant; check the
  spread with `get_attrib_stats(attribs=["unreal_instance"], unique=True)`.
- Pivots: instances are placed at their asset's pivot. Trees authored with the
  pivot at the trunk base sit on the ground; a centroid pivot floats them half
  their height.

## `unreal_*` attributes Houdini Engine reads

Verified in this project's UE 5.8 sessions: `unreal_material` (detail or prim,
on the landscape: the landscape material), `unreal_instance` (points).
From the Houdini Engine for Unreal documentation, not re-verified here; read the
plugin docs for the exact class and spelling before relying on them:

- `unreal_landscape_layer_*` attributes on a layer volume (layer info object,
  non-weight-blended flag) and other `unreal_landscape_*` settings.
- `unreal_uproperty_<Name>`: sets that UPROPERTY on the generated component or
  actor (collision profile, cast shadow, cull distances on instancers).
- `unreal_output_name`, `unreal_bake_folder`: naming and bake location.

Curve inputs: an HDA input becomes a Houdini Engine **curve input** in UE when
the designer picks Curve on that input. Label inputs, keep curve inputs as
input 0..n of the HDA, and resample inside the HDA (UE curves arrive with the
designer's point spacing).

## HDA rules that bite only in Unreal

- **Unreal cooks the HDA definition's defaults, not the values on your Houdini
  instance.** Anything tuned on the instance must be written into the
  definition (`edit_hda_interface` modify default, or `set_hda_interface` with
  the tuned values) and `update_hda`, or UE shows the old look.
- **Declare spare/HDA parm types explicitly** (`float` vs `int`). A parm created
  from the literal `20` becomes an int, and HE's `set_float_parameter_value` on
  it returns False silently.
- Show the user where the controls are: `set_selection([hda])`, then
  `capture_screenshot(pane_name=<the Parameter pane>)`.
- **An optional curve input is never empty in Unreal**: Houdini Engine gives it a
  default 2-point, 2 m spline. Switch to the input only when it is a real curve
  (`(bbox(opinputpath('..',0), D_XSIZE) + bbox(..., D_ZSIZE)) > 20`, or
  `npoints >= 3` for a closed shape), or the asset builds from that stub.
- **`volumesample()` on a heightfield samples a flat volume at y = 0.** Sampling a
  mask at a scattered point's own `@P` (y = terrain height) reads the border
  value, so a "keep clear of the river" cull silently removes nothing. Sample
  `set(@P.x, 0, @P.z)`.
- **Edit layers**: `unreal_landscape_editlayer_name` (prim string) names the layer
  a generated landscape writes. A second HDA writes its own layer onto it in
  modify mode: `unreal_landscape_output_mode` 1, `unreal_landscape_editlayer_type`
  1 (additive: the volume holds the *change*, not the terrain), and the landscape
  fed into input 0 through a **World** input (a Geometry input refuses actors).
  Verified in UE 5.8.4 / HE 3.0: the layer appears, toggles off non-destructively.
- **Do not put `unreal_landscape_editlayer_clear` on a generating landscape**: with
  it, HE 3.0 regenerated paint layers but left the heights flat (z range 0). On a
  modify-mode layer it is what you want.
- Rebuilding the generating asset (after a re-import) recreates its landscape, and
  layers other HDAs wrote onto the old one are gone: recook those afterwards.

## Unreal-side notes (from UE 5.8 sessions, not checkable from Houdini)

- `new_level()` in UE 5.8 brings the Open World template's own landscape, which
  overlaps a generated one. Use an empty level or delete it; count landscapes.
- The first screenshot after a material change shows default white: wait for
  shader compilation to finish before judging it.
- A hand-written `.uproject` has no renderer settings (no Lumen, VSM, distance
  fields; SM5). Set them before judging the look.

## Verify (all before anything goes to Unreal)

1. `verify_network(<terrain net>, force_cook=True)`: `healthy` true, no
   `error_nodes`.
2. `get_volume_info(<output heightfield>)`: `height` resolution is exactly one of
   1009/2017/4033/8129 per side; voxel size equals the planned spacing; record
   height min/max (they set the Z scale on the file route).
3. Every weight layer present by its material name, value range within 0..1;
   for weight-blended layers, the means sum to ~1 (or a wrangle-built `sum`
   layer reads min ~1, max ~1).
4. `get_attrib_stats(<instance points>, attribs=["unreal_instance"], unique=True)`:
   the expected variants and counts; `get_geometry_info` shows no leftover layer
   attributes on the points.
5. `get_attrib_stats(<landscape output>, attrib_class="detail")` or `"prim"`:
   `unreal_material` is present and names the landscape material.
6. File route: `start_render(<heightfield_output>)` lists every image on disk;
   no `flat_images` (a flat layer image is a layer that is all 0 or all 1).
7. HDA route: `get_parameters(<hda>, non_default_only=True)` is empty (or every
   listed value has been promoted into the definition defaults).

{network_housekeeping}
