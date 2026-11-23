# Agent UX notes: fxhoudinimcp fork

Working notes from driving Houdini 22.0.508 through the MCP. Nothing here is built yet.
Rule for every item: it must remove a real failure or a real token cost we measured, and
extend an existing tool before adding a new one.

## 0. Focus

Houdini → Unreal Engine 5.8 for games:

1. **World building:** heightfield terrain, masks, rivers/roads from splines, biome scattering → UE Landscape (heightmap + layer weights) and foliage instances, or a live HDA through Houdini Engine.
2. **Procedural assets:** HDAs with exposed parameters (seed, size, detail, curve inputs), game-ready output (UVs, LODs, collision, materials) → Houdini Engine or FBX.
3. **Vertex Animation Textures (VAT):** baked sims (rigid, soft, fluid, sprite) → textures + mesh + UE material.
4. **Niagara:** Houdini point caches → UE Niagara via the HoudiniNiagara plugin.

What this machine has: SideFX Labs 22.0 (VAT ROP up to 4.0, `labs::niagara_rop`, `labs::niagara::2.0`,
`labs::biome_plant_scatter`, `labs::lod_create`, `labs::autouv`, `heightfield_scatter::2.0`, 46 heightfield SOPs).
Houdini Engine for Unreal ships for **UE 5.7 and 5.8** only (`Houdini Engine/Unreal/22.0.508/<ver>/`), including
the `HoudiniNiagara` plugin. UE 5.9 is not covered by this build.

## 1. What the MCP already has

| Need | Exists? | Tools |
|---|---|---|
| Read a graph as text | Yes | `get_network_overview`, `verify_network`, `explain_node`, `get_node_info`, `get_cook_chain`, `get_scene_summary`, `compare_snapshots` |
| Read geometry/volumes as numbers | Yes | `get_geometry_info`, `get_attrib_stats`, `get_bounding_box`, `get_volume_info`, `sample_volume`, `sample_geometry` |
| Viewport camera | Yes | `set_viewport_direction` (preset or rotation/pivot/distance), `set_viewport_camera`, `frame_all`, `set_viewport_display` |
| Several images in one | Partly | `render_sheet` tiles a **frame range** into one image (agent picks columns and resolution). `render_quad_view` writes 4 separate files. No multi-angle sheet. |
| Image size control | Partly | `resolution` on `render_viewport` and `render_sheet`; none on `capture_screenshot` / `capture_network_editor`. |
| HDAs | Yes | 13 tools: create, install, versions, sections, interface. |
| Run ROPs / exports | Yes | `start_render` (any ROP, incl. Labs VAT/Niagara ROPs), `get_render_progress`, `export_file`, `write_cache`. |
| Game-engine know-how | **No** | 31 workflow guides, none mention VAT, Niagara, Unreal, Houdini Engine, `unreal_*` attributes or Landscape export. |

## 2. Findings (evidence)

1. **`ascii_flow` gives a misleading picture of the graph.** On `/obj/terrain_river` it showed
   `terrain_noise -> river_sample_height` as the main chain and left `RIVER_SPLINE -> river_resample`
   dangling. In fact `river_resample` is input 0 and `terrain_noise` is input 1; two other second
   inputs were missing too. Nodes with several inputs get squashed into chains. **Bug.**
2. **The overview costs ~1k tokens for 16 nodes** (4.2k chars compact): 16-decimal positions,
   all-false flags, verbose wire objects. That's roughly 6–8k tokens at 100 nodes.
3. **`list_node_types` returns every version**: 6 VAT ROPs (`::2.0` … `::4.0`), 4 biome scatters.
   That costs tokens, and the agent may pick an old version.
4. **No game-engine guidance** (section 1). The agent would have to guess Landscape resolutions,
   height scale, VAT texture formats and `unreal_*` attributes. Guesses are expensive to discover
   wrong, because the result only fails inside Unreal.
5. **The agent can't see Unreal.** It can verify the Houdini side numerically, but whether the
   asset imports and looks right in UE 5.8 is checked by a human (for now).
6. *Fixed (branch `feat/curve-points-and-netcapture-fix`):* Curve SOP points could not be set
   (`set_curve_points`); network-editor capture was noise, then one frame stale.
7. `reload_plugin` silently keeps stale code when `dispatcher.py` changes (it reports success:false).
8. Parameter semantics surprise the agent (noise amp 140 gave ~45 m of relief). Build, then *measure*.
   Teach that in the guides, not in code.

## 3. Candidate changes (ranked for the focus; small first)

**A. Four workflow guides (markdown only, no code).** Uses the existing `get_workflow_guide` mechanism:
- `unreal_landscape`: UE Landscape sizes (e.g. 1009 / 2017 / 4033 / 8129 verts), grid spacing ↔ UE scale,
  16-bit heightmap export and Z scale, layer-weight masks per material layer, foliage/instance points,
  splines → UE splines; what Houdini Engine reads (`unreal_landscape_layer_*`, `unreal_instance`,
  `unreal_material`, `unreal_uproperty_*`, curve inputs).
- `game_assets`: HDA interface rules for Houdini Engine (inputs, curve inputs, exposed parms, seeds),
  game-ready checklist: UVs (`labs::autouv`), LODs (`labs::lod_create`), collision (`UCX_` / `collision_geo`),
  normals, pivot at origin, scale in cm vs m, FBX vs HDA.
- `vat`: which VAT mode for which sim (rigid / soft / fluid / sprite), VAT 4.0 ROP settings, frame range
  and texture-size limits, precision (HDR/EXR vs 8-bit), how the UE material reads bounds/scale, what
  numbers to read back to verify the bake.
- `niagara`: Labs Niagara ROP point-cache export (attributes to keep: P, v, age/life, Cd, pscale, id),
  frame rate and spawn/death handling, the HoudiniNiagara data interface on the UE side.

Every guide ends with a *verify* section: the exact text/number checks (`verify_network`,
`get_attrib_stats`, file sizes from `start_render`) that prove the output is right **before** it goes to Unreal.

**B. Compact, faithful graph text** in `get_network_overview` (fixes findings 1–2). One line per node,
inputs in order; positions only on request:
```
river_sample_height  attribwrangle  <- river_resample, terrain_noise
OUT                  null           <- preview_merge   [D][R]
```

**C. Latest version only** in `list_node_types` by default (`all_versions=True` to see the rest).
The repo already tracks versions in `node_versions.json`.

**D. Multi-angle sheet:** extend `render_sheet` with `views` (presets or `orbit: N`) at one frame; the agent
picks tile size, so it controls the token cost. Especially useful for asset turntables and VAT checks.

**E. `max_size` on screenshots.** One-line token control.

**F. Node catalog categories** (`list_node_types(category=...)` + one-line summary). Lower priority now that
the guides name the right nodes for the focus workflows.

**Later, only if the stress tests demand it:** an Unreal-side bridge (UE Python remote execution or an
Unreal MCP) so the agent can import and check the result in 5.8 itself. Big; not now.

**Not doing:** extra camera tools, video capture, per-node screenshots, a second graph-reading tool,
custom VAT/Niagara export code (the Labs ROPs already do it, and `start_render` runs them).

## 4. Stress tests (in order)

Each run records: tool calls, chars returned per tool, screenshots taken, failures, and every fallback
to `execute_python` (each one is a missing tool or a missing guide line).

1. **World:** 2017×2017 terrain with erosion, masks (slope/height/flow), the spline river from
   `river_test`, biome scatter (trees/rocks) → export heightmap + layer weights + instance points for a
   UE 5.8 Landscape, **and** the same as an HDA in Houdini Engine with the river curve as an editable input.
2. **Procedural asset:** a rock/cliff generator HDA (seed, size, detail) → UVs, 3 LODs, collision → HDA in
   UE 5.8 and FBX; check poly counts per LOD as numbers.
3. **VAT:** one rigid (RBD destruction) and one soft (vellum cloth) bake with the VAT 4.0 ROP → UE material.
   Read back texture resolution, frame count and bounds.
4. **Niagara:** a small particle sim (sparks / debris) → Labs Niagara ROP → HoudiniNiagara in UE 5.8.
   Check attribute list and particle counts per frame.
5. **Graph scale:** take test 1's network (likely 80–150 nodes) and measure overview/verify tokens; check
   `ascii_flow` on merges.

## 4b. Stress test 1 results: World → UE 5.8 (2026-11-23)

**Built:** `HoudiniProjects/world_test/world_test.hip`. 2017² terrain at 1 m (noise → erosion at 4 m → spline
river carve → resample), slope mask → normalised Grass/Rock/Sand weights (87% / 7% / 7%), 25.6k scatter points
with `unreal_instance`, 16-bit heightmap + 8-bit layer PNGs, HDA `xsolla::world_builder` (curve input 0,
terrain output 0, instances output 1, 15 promoted sliders). The external-curve input was verified numerically.
Not checked inside Unreal yet.

**Cost:** 84 calls, ~109k chars returned (~27k tokens), 1 `execute_python` fallback, 2 images in total
(the grey preview was nearly useless). Everything else was judged from numbers.

| Tool | Calls | Chars |
|---|---|---|
| get_node_card | 8 | 33.5k (one card *with* help = 12.5k alone) |
| get_volume_info | 8 | 24.3k (~3k per call on a 4–8 layer heightfield) |
| get_parameter_schema | 1 | 8.3k (filter ignored, see below) |
| build_network | 10 | 8.2k (good: 13 nodes built + cooked evidence in one call) |

**What worked well:** `build_network` (dry run, atomic, cooked evidence with layer ranges), `verify_network`
(caught the duplicate `output0` after collapse from text alone), `get_volume_info` (the resolution
mistake 504 → 2016 → 2017 was caught purely from numbers), `get_attrib_stats` unique counts, `create_hda`
(auto-promoted the subnet's spare parms), and `set_curve_points` (new).

**New findings:**
1. **Guide error:** `heightfields.md` says heightfield_output "converts to renderable geometry". It exports
   layers to image files; `convertheightfield` converts to geometry.
2. **`start_render` can't verify heightfield_output files**: it reported `wrote_files: false, unverified`
   although 4 PNGs were written. It doesn't know the node's `output` parm.
3. **`compare_volumes` returns 0 for every band on 2D heightfields** while the layer means are 0.87 / 0.07.
   Looks like it samples the wrong height. **Bug.**
4. **`get_parameter_schema` ignored `parm_names`** and returned all ~55 parms (8.3k chars).
5. **No collapse-into-subnet tool**: the only `execute_python` fallback. Collapse also auto-creates
   `output0`, which conflicted with explicit output nodes. Worth a small tool or a `build_network` option.
6. **Error replies carry a full Python traceback** even for argument mistakes; that's ~300 chars of noise per error.
7. **Scatter points inherit every heightfield layer as point attributes** (20+ attribs). That's a Houdini behaviour,
   but the `unreal_landscape` guide must say to clean them before output.
8. **UE Landscape sizing is easy to get wrong**: size/spacing truncation gave 504 → 2016 instead of 2017.
   Fix used: snap shaping spacing to `size/round(size/spacing)`, final resample by sample count. A guide line, not code.
9. **Heightfield auto-remap range isn't stored** on heightfield_output (`red_auto_range` stayed 0), so the
   UE Z scale must come from the measured `height` min/max. A guide line.
10. **Quick previews don't show layers or instances.** A viewport render of a heightfield is grey.
    Candidate: a `visualize` option on `render_viewport` (temporary heightfield_visualize + instance
    points), or teach the agent to wire one. Ties into the multi-angle sheet idea (3D).
11. Direction presets: `persp` is rejected, only `perspective`. A tiny alias.
12. Most other argument errors in this run were from calling the plugin directly (bypassing the MCP schema),
    not real agent-facing problems: `params` vs `parameters`, `source_path`, `parent_path`.

**Unreal side (verified in UE 5.8.4 through Houdini Engine 3.0, project `D:\UE_25_PRJ\HoudiniWorldTest`):**
landscape heights match to the centimetre; the instancers came out right first time. Three things only
showed up inside UE:
13. **Paint layers need a landscape material** naming them (Layer Blend). Without `unreal_material`, HE 3.0
    silently drops every weight layer. Found by reading the plugin source. **Must be in the
    `unreal_landscape` guide, first line.**
14. `new_level()` in 5.8 brings the Open World template landscape; it overlapped ours (found by a
    landscape count in the report, then the screenshot).
15. The first screenshot after a material change shows default white: wait for shader compilation.
**UE loop cost:** 4 editor launches (~2–4 min each), driven by a startup Python hook + JSON report.
`-ExecutePythonScript` is unusable for HE tests (the editor quits before the asynchronous cook).
**Candidate (later, section 3 "Unreal-side bridge"):** this hook + report pattern is the minimal Unreal
bridge. It's worth turning into a reusable `ue_test` script before considering an Unreal MCP.

**Changes this adds to section 3:** fix 1 in the guide (A), fix 2–4 as bugs (cheap), add 5 (small),
strip tracebacks from BAD_ARGUMENTS (6, one line), put 7–9 in the `unreal_landscape` guide, and keep 10 under D.

## 4c. Friction log: stylized world pass (2026-11-23)

Building: stylized terrain (ridges, terraces, 5 erosion-driven layers), 15 procedural low-poly assets
(Python SOP), 5 biome scatters → packed instancing (106k), river water mesh with flow UVs, UE lighting/post.

**Wished-for tools / options**
- **`render_viewport(node_path=...)`**: render one node isolated, independent of display flags. I added a
  temp lineup wrangle, set its display flag, framed it, and the render still showed the whole world (the
  viewer was on the HDA's display). That cost 3 renders (~1 image each) and a temp node to clean up.
- **`render_sheet` over pieces** (`by_attribute="variant"`): a contact sheet of every asset variant in one
  image. This is what checking a procedural asset library really needs; it would have been one call.
- **Compact graph text**: I wrote my own `ins[to][input] = from` printer over `get_network_overview` because
  `ascii_flow` is misleading (finding 2.1). Every non-trivial edit needs this view first.
- **Insert node between two nodes**: every insertion was `build_network` + `connect_nodes` (2 calls, plus
  remembering input indices). An `insert_after: "node"` key in a build_network spec would cover it.

**Token waste**
- `get_parameter_schema` returns every parm (filter ignored); `get_parameter` didn't return the menu tokens
  for `pivot` (int menu), so I had to pull the whole schema (~8k chars) to learn `origin`/`centroid`.
- `get_node_card(parm_filter="piece|pack|id")` matched nothing, so it's not a regex; then the unfiltered card.
- Asset Python (~9k chars) goes through `set_parameters` on every edit (sent twice). A `set_parm_from_file`
  or a patch/replace op for long string parms would cut that to a few hundred chars.

**What worked well**
- `edit_hda_interface` added 13 controls in one call (dry run first); `get_volume_info` with `bins` /
  percentiles calibrated the Dirt layer from real erosion data (debris p95 0.10, sediment p95 6.3, flow p95 6.7):
  numbers, no screenshots. `get_attrib_stats(unique)` showed the conifer imbalance (9%) in one call.
- `connect_nodes_batch`, `update_hda`, `verify_network(force_cook)`.

**Struggles (my mistakes, worth guide lines)**
- Polygon winding: Houdini's front face is clockwise; my hand-built faces were inside-out (black in the viewport,
  and they would be culled in UE). A guide line for procedural mesh building, plus a `verify_network` check for
  "normals mostly facing the part centroid" would catch it.
- Copy to Points `pivot` defaults to `centroid`: instanced trees would float half their height. Guide line.
- Point-class wrangle using `@primnum` silently gives garbage. That's a VEX habit, not the MCP.

### 4c-2. Second pass (water, humidity, density, variety, sunset)

- **`verify_network` error cascade:** one VEX typo (`point() < point()` is ambiguous) produced 21 error
  entries, each repeating the full upstream message (~15k chars). Wanted: report the **root** error once
  (`root_errors`), list the rest as `blocked_by: <root>`. That's the single biggest token waste so far.
- **A sanity check found the water bug, not a screenshot:** `get_bounding_box` on the water showed y-min == y-max
  (a perfectly flat river), which pointed straight at the bed-profile logic. Lesson for the guide: after any
  curve-driven build, check the min/max spread of the driven attribute.
- **HDA instance vs definition defaults:** values tuned on the Houdini instance (`shape_spacing`,
  `grass_spacing`…) didn't reach Unreal, which cooks the definition defaults. Every tuning had to be repeated
  with `edit_hda_interface modify default`. Wanted: `update_hda(promote_current_values=True)` or a warning
  in `update_hda` listing parms whose instance value differs from the default.
- **Polygon winding again (water):** an `addprim` ribbon faced down, so UE culled it from above. A generic
  `orient_faces(up|outward)` option (or a check in `verify_network` for meshes that leave the HDA) would catch both cases.
- **heightfield_scatter coverage ignores soft mask values** (any > 0 counts): forests only cluster after
  culling by the mask in VEX. Guide line for the `unreal_landscape` / biome guide.
- **Unreal side:** `take_high_res_screenshot` re-enters slate tick callbacks (fired ~17×); UE 5.8 renamed
  `volumetric_fog` to `enable_volumetric_fog`. These belong in the future `ue_test` helper, not the MCP.
- **Long-parm round trips:** the asset SOP's Python is now ~10.7k chars and was re-sent 3 times (~32k chars).
  A file-backed parm (`set_parameter_from_file`, or a node that reads `$HIP/scripts/assets_sop.py`) is the fix;
  it also makes the generator diffable in git.

### 4c-3. Project-asset pass (Swamp_Forest pack from AP47) + live Unreal loop

- **Asset-pack introspection had to happen on the Unreal side** (mesh bounds, landscape layer names, which
  textures each layer function uses, Landscape Grass Types). The Houdini MCP can't see UE content. A
  tiny "UE content manifest" (JSON of mesh sizes/pivots and landscape layer names) fed back into Houdini is the
  real need. Today: one remote-exec script plus one binary scan of the .uasset files.
- **The big unlock was UE Python remote execution** (`bRemoteExecution=True` + Epic's own `remote_execution.py`).
  Material retunes, grass density, fog, and HDA parameter changes (via the Houdini Engine public API wrapper,
  which recooks in place) all became live, with no editor relaunch (each relaunch is ~4–5 min). This should be the
  basis of a small `ue_bridge` next to the MCP; it's far more valuable than more Houdini tools.
- **UE captures need the editor in front**: background CPU throttling stops rendering, so SceneCapture exports are
  black. The `bThrottleCPUWhenNotForeground=False` ini didn't take; bringing the window forward for ~45 s worked.
- **HE parameter setter types**: `set_float_parameter_value` on an int parm returns False silently. Spare
  parms created as `int` because their default was an int literal (`20`). Guide line: declare parm types explicitly.
- **VEX gotchas cost two verify rounds** (`range()` doesn't exist; `point() < point()` is ambiguous).
  A `validate_vex` dry run exists in the MCP (VEX category); I should have used it before building. Guide line:
  "validate wrangles with `validate_vex` before `build_network`".
- **attribinterpolate after scatter::2.0** needs the source-prim attributes enabled; `xyzdist` + `primuv` in the
  wrangle is simpler. Worth a line in a scattering guide.
- **Switch-driven variants** (Asset Source: procedural vs project) inside one HDA worked well and kept both looks
  alive; `edit_hda_interface` menu parms worked first try.

### 4c-4. Lighting pass (Lumen / SM6) and the pack landscape material

- **A hand-written .uproject has no renderer settings.** No Lumen, no VSM, no distance fields, static lighting
  allowed, SM5. Every "make it look AAA" attempt before fixing this was wasted. Template for future UE test
  projects: `[/Script/Engine.RendererSettings]` (Lumen GI + reflections, VSM, mesh SDFs, TSR, volumetric fog,
  extended luminance range, AllowStaticLighting=False) + `[WindowsTargetSettings]` DX12 + `PCD3D_SM6`.
- **The pack's landscape material is auto-material driven**: grass outputs and texturing come from the `auto`
  layer (MF_auto); manual `layer_01..04` are overrides. Painting `auto=0` disabled the pack's look and grass.
  Lesson: introspect a third-party landscape material's grass-output graph before mapping layers
  (`MaterialEditingLibrary.get_inputs_for_material_expression` made this visible).
- **Landscape Grass only generates around a realtime, focused editor viewport**: 302 grass components, 0
  instances from offscreen SceneCapture shots. Offscreen capture is therefore unreliable for grass / foliage
  LOD / virtual textures. A proper UE bridge needs a "render through the editor viewport (or Movie Render Queue)"
  path, not only SceneCapture2D.
- UE Python property names to remember: `aerial_pespective_view_distance_scale` (UE's own typo),
  `enable_volumetric_fog`, PPV fields need `override_<name>=True` first.

## 5. Session log

- 2026-11-23: River/terrain built (`HoudiniProjects/river_test`). 4 `execute_python` fallbacks: curve stash
  probing ×3 (now `set_curve_points`), noise parm discovery ×1. Labs 22.0 confirmed installed; workflow
  guides have no game-engine coverage.
