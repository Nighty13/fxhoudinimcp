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

## 4d. VAT destruction wall (concrete + continuous rebar → Labs VAT 4.0 → UE 5.8)

**Result:** a believable sim, judged by numbers. 120 concrete pieces + 10 rebar bars (160 links) + steel ball;
soft plastic links bend and hold; 108/281 pieces break (38%); settles by f240 (p99 speed 0.03 m/s). The VAT bake
gives correct textures (281×240). UE import via the Labs Python importer works. **UE playback works** (f6 impact,
f24 break-up, f240 settled debris, colours from the col texture).

**What made the difference (numbers over pictures):**
- A permanent `sim_metrics` wrangle (displacement from rest, `broken`, `speed`) + `get_attrib_stats` percentiles per
  frame. Tuning 3 settings at once by "broken %, p99 speed at f60/f160/f240" took 3 calls each, no screenshots.
  **Candidate tool: `sim_report(node, frames, rest_node)`** returning exactly that table.
- The new `root_errors` and `menu_items` (batch 1) paid off immediately: VEX typing errors and `connecttype` / `constrainttype`
  tokens were one call each.

**New friction / wished tools:**
1. **build_network accepted an expression on a FOLDER parm** (`scatter1` is a folder in rbdmaterialfracture's multiparm;
   the real count is `concrete_scatterpts1`). It should refuse writes to Folder/FolderSet parms.
2. **`get_parameters(patterns=["*1"])` / `["f*"]` returned nothing.** Wildcards aren't supported; I fell back to
   `execute_python` to list multiparm instance parms. Needs glob support.
3. **`reset_simulation` doesn't know SOP solvers** (rbdbulletsolver's `resetsim` button). Identical numbers after a change
   looked like "my fix had no effect"; it was a stale cache. `cook_frame_range` should offer `reset=True`.
4. **Same VEX trap 4 times:** `point()` returns are ambiguous in comparisons, `addpoint()`, `setpointattrib()`. A guide
   line "always assign point() to a typed variable" plus `validate_vex(code=...)` that compiles a snippet in a scratch
   wrangle *before* it lands (today's `validate_vex` only cooks an existing node).
5. **Packed-RBD knowledge gaps that cost several sim rounds (guide lines, not code):** `assemble` puts `name` on POINTS;
   per-piece collision filtering is `collisiongroup` + `collisionignore` (patterns match the group, not names); embedded
   rebar MUST ignore concrete or Bullet explodes the wall; `copytopoints` doesn't carry string attribs; `polywire` drops `name`.
6. **start_render can't verify the Labs VAT ROP** (paths in `path_geo` / `path_pos` …) → "unverified" every time.
7. **Labs VAT 4.0 + Houdini 22.0.508 writes an EMPTY FBX** (its internal FBX ROP exports an object inside the ROP
   network). Workaround: object-merge `/out/vat/export/MESH/EXPORT` with `enablecooking=1` and export with a normal
   FBX ROP. Every re-bake overwrites the fixed FBX: worth a tiny post-render script, and a report to SideFX.
8. **`get_node_errors_detailed` repeats the same warning ~10×**: needs the same root/dedupe treatment as verify_network.
9. **`render_sheet` is client-side only** (spawns hython, takes a license seat): unusable from the plugin bridge and with
   a 1-seat license. Stitching 3 `render_viewport` frames with PIL worked: a server-side multi-frame strip is the fix.
10. `get_node_card` reported "matched 85 of 80" (count bug, cosmetic).

**UE playback bug: root cause and cost (biggest waste of this test, ~25 calls):**
- The black, collapsed wall in UE was **all-zero VAT textures**. The Labs VAT ROP had `enablecooking=0`, set back after
  the FBX workaround, and a re-bake with it off writes zero-filled EXRs **with no error**. I spent rounds on UE-side
  suspects first: MI switches, data rounding, UVs, bounds, camera.
- Checking the textures first would have taken one call. **Candidate: `start_render` / a `verify_outputs` step that
  reads written images and flags all-constant ones** (min==max per channel). This is item 6, made concrete.
- `execute_python` returning a big dict dumped ~15 KB of Labs Unity material YAML (parm strings) into context. **Truncate
  long string values in returned values by default.**
- The UE side: HighResShot / `AutomationLibrary.take_high_res_screenshot` silently write nothing while the editor window
  is **minimized**. `ShowWindow(SW_RESTORE)` + an Alt tap before `SetForegroundWindow` fixed it. This belongs in a future
  `ue_bridge` / UE guide.
- The PowerShell heredoc→file and pipe paths add a UTF-8 BOM, which broke `exec()` in `execute_python`. **Strip a
  leading U+FEFF in the code handler** (one line).

**Done (2026-11-23), Houdini-side only; the UE capture item waits for a UE guide/bridge:**
- `outputs.reported_outputs` falls back to a ROP's visible file parms (Labs VAT `path_pos/rot/col/geo`), listing only
  the ones on disk, so VAT bakes are verified instead of "unverified" (item 6).
- `write_verdict` adds `flat_images` + a message line when a written image is one constant value in every pixel
  (OpenImageIO, skipped if missing). Live: a cooking-off VAT bake → 3 flat textures named; cooking-on → clean.
- `execute_python` strips a leading BOM; strings nested in a returned list/dict are capped at 2000 chars.
- Fixed a test leak (`test_storm_session_fixes` replaced `outputs.reported_outputs` for the rest of the run).

## 4e. Curve-driven river tool → HDA (`HoudiniProjects/river_terrain`, 2026-11-24)

Ranked by cost. None fixed yet.

1. **`build_network` does not compile VEX snippets.** Two wrangles were built with ambiguous-overload errors
   (`append(S, point(...))`, `setdetailattrib(0, "n", detail(...))`). `set_wrangle_code` already returns
   `vex_valid`/`vex_errors`. **Run the same check on every `snippet` in build_network (and in dry_run).** One
   call would have saved two error-hunting rounds.
2. **`verify_network(force_cook=True)` said `healthy: true` while two wrangles had errors.** It cooks only the
   display branch; side branches (water, falls → other subnet outputs) were never cooked. **Also cook every
   `output` SOP / OUT_* node, or list "nodes never cooked" so they are not counted as healthy.**
3. **`get_node_errors_detailed(root_path=...)` descends into locked HDA internals.** 25 hits, all inside stock
   heightfield nodes' unused for-each blocks (`get_piece: Linked SOP isn't generating any pieces`). It drowned the
   real errors. **Skip children of locked assets by default** (`list_children` already has `include_locked_assets`).
4. **No tool for cross-node numeric checks.** "Sample heightfield A and B at curve C's points", "water points
   vs terrain under them" needed `execute_python` 9 times (script kept at `river_terrain/scripts/verify_river.py`).
   **Candidate: `sample_volume(from_node=..., fields=..., node_paths=[A, B])` returning per-point diffs, or a
   `compare_at_points` tool.** `sample_volume(from_node)` exists but takes one volume node.
5. **`set_hda_interface` reply is ~8k tokens for 21 parms**: each stored parm is echoed twice (inside its folder
   and again at top level). **Echo the tree once; flatten only on request.**
6. **`set_hda_interface` with `from_parm` copies the source parm's *template default*, not its current value.**
   Tuned values (waterfall 0.6, cascade 0.1) became 0.35/0.08 defaults. **Add `default_from_value: true`** (or make it
   the default for spare parms on a CONTROLS null, the normal "promote my knobs" case).
7. **No tool for HDA input labels / min inputs.** Edited the DialogScript section by hand via `execute_python`.
   **Add `inputs: [{label, optional}]` to `set_hda_interface` or `create_hda`.**
8. **Promoting CONTROLS → HDA is ~4 calls + python** (interface, allowEditingOfContents, 21× `ch("../x")`, update).
   **Candidate: `create_hda(..., promote_from="CONTROLS")`** that copies the spare parms with current values as
   defaults and links the null's parms to them.
9. **`tidy_network` laid out the subnet without `layout=True`**, though the server instructions say never move
   nodes. The reply said `laid_out: true`. Harmless here (all nodes were mine), but it contradicts the guide.
10. **`update_hda` reported the same `library_file_mtime` (1795525000.0) on two saves minutes apart**; the file on
    disk did change (13:54:15). The value looks rounded or cached. Report the real stat.
11. **`execute_python` namespace is fresh per call** (expected), but the error ("name 'tool' is not defined") cost a
    round. Worth one line in the tool description.
12. **`simulation_cache` note on every `set_parameters`** under a heightfield erode is noise: heightfield_erode
    re-sims on upstream change (measured: range changed after edits). Say so for SOP-level erode, or drop the note
    outside DOP networks.
13. **`create_wrangle` vs build_network**: no way to pass a justification for wrangles inside build_network, so the
    "nodes not code" rule cannot be documented on the node. Minor.
14. Missing skill: the user asked for a `houdini-new-project` skill; none is installed. Project folders were made by
    hand (hda/geo/export/backup/render/notes/scripts). **Ship a `new_project` tool or skill** (folders + hip save +
    `$JOB`).

## 4f. River tool v2: adaptive width, slide, levees, sheets (2026-11-24, same project)

Building blocks this pass needed that the MCP does not have. Ranked by how often they cost a round.

1. **No per-row / per-group geometry statistics.** I needed "water width per row" (group points by `s`, take
   the spread across `uv.x`), "width_mult grouped by x_slope bands", and "sheet points inside terrain". Each was an
   `execute_python`. **Candidate: `get_attrib_stats(group_by="s", reduce=...)`** or a small "group-by" mode:
   aggregate attribute A per bucket of attribute B. It would replace most of my python in this project.
2. **No "sample this heightfield under those points" in one call with a signed difference.** "Water point Y minus
   terrain under it" (floating vs hidden) is the core check for any water / road / decal on terrain.
   `sample_volume(from_node=)` returns raw values; **add `compare_to="P.y"` → per-point signed delta + histogram.**
   (Repeats 4e #4, now measured: 6 of 9 python calls this pass were this.)
3. **No parameter sweep tool.** "slide_strength 0 / 0.5 / 1 → offset stats" was a python loop.
   **Candidate: `sweep_parameter(node, parm, values, measure={node, attribs})`** returning one row per value
   (like `cook_frame_range` does for frames). Generic and cheap; good for any "is this knob doing anything" check.
4. **Ramp parameters cannot be linked with a tool.** `link_parameters` is scalar; relative ramp references need
   `Parm.set(Parm)` in python. **Let `link_parameters` take ramps** (and multiparms).
5. **`set_wrangle_code` with full `vex_code` echoes the whole snippet back** (~10k tokens for a 200-line wrangle).
   With `edits` it answers compactly. **Echo only line count + vex_valid by default for full replacements.**
6. **`edit_hda_interface` / `set_hda_interface` replies list every inserted parm twice** (inside the folder
   and at top level): ~9k tokens for 14 parms this pass. Same as 4e #5.
7. **No `allowEditingOfContents` / "unlock HDA" tool.** Every HDA edit pass starts with python. Pair it with
   `update_hda` (`unlock: true`), or have `update_hda` report `locked` before/after.
8. **`capture_screenshot` silently captured inside the HDA** after the network editor had been entered (the user
   navigated there): only the display node, no water. It *did* add `viewer_note`, which is how I caught it. Good;
   **consider `capture_screenshot(network="/obj/x")`** to pin the viewer level for the capture only.
9. **A viewport the user is also driving moves between my calls.** One overview capture came back from the user's
   camera, not the one I set two calls earlier. **`capture_screenshot(rotation, pivot, distance)`** (set + capture
   atomically) would make captures reproducible.
10. **`get_attrib_stats` cannot list individual elements of a small group.** For 3 waterfall points I wanted the 3
    positions; it returns min/max/mean. **Add `list_if_count_below=N`** (or use `get_points` hint in the reply).
11. **Heightfield erode's `simulation_cache` note still appears on every set** (4e #12). It is the most repeated
    noise in this session: ~25 occurrences.
12. **VEX gotchas the tools could catch statically** (each cost a round this session): `point(0, ...)` inside a
    detail wrangle reads the *input*, not points added by `addpoint` in the same cook; `prim()` vs `point()`
    attribute of the same name; `append(arr, point(...))` overload ambiguity. **A VEX lint in `validate_vex`**
    for "reads input 0 attribute that this snippet also creates with addpoint" would have saved the y=0 sheet bug.
13. Good: `connect_nodes_batch` + `build_network` into an existing network kept the user's node positions;
    `set_wrangle_code(edits=...)` with `vex_valid` per edit is the most efficient loop in the MCP.

Foundations worth adding (the reusable "building blocks" behind this tool):
- **Curve-on-terrain helpers** as tools or a guide: resample, smooth, settle-to-valley, monotone height along a curve,
  lateral bank search. Every river/road/wall tool re-derives these.
- **`terrain_profile(curve, heightfield, offsets=[-60..60])`**: cross-sections along a curve as a compact table.
  It is the single most useful debugging view for anything carved into a heightfield.
- **A `river` / `roads` workflow guide**: the order used here (settle → bank level → features → carve → protect →
  erode → lock → water) and the numeric checks (monotone, depth > 0, edge gap, spikes, sheets per fall).

## 4g. River tool v3: step pools, small falls, lake end (2026-11-24, same project)

1. **`edit_hda_interface` failed with an empty error** ("Error executing tool edit_hda_interface", no message) when a
   `from_parm` spec also had `default`. The docs say that combination is refused; the refusal text never reached
   the client. **Every handler exception must carry its message** (the dry run of the same ops, minus `default`, worked).
2. **No "measure while sweeping" for menus/toggles either**: end_mode 0/1 and mini_fall_drop 1.5/2.5/3/4 were python
   loops reading detail self-checks. The answer (54 → 4 → 0 small falls) was the single most useful design number of
   the pass. Strengthens 4f #3 (`sweep_parameter`).
3. **Viewport camera placement is trial-and-error.** Two of five perspective captures ended up inside a hill or
   below the terrain (rotation sign / distance guess). **Candidate: `frame_all(bounds, direction=[rx, ry])`** for a
   perspective framing, or `set_viewport_direction(look_at=..., from_height=...)` with terrain-aware clearance.
4. **No way to show the user the parameter interface except a pane screenshot.** That worked well
   (`capture_screenshot(pane_name=<Parm pane>)` after `set_selection`). Worth documenting as *the* way to prove
   "where are my controls" in the HDA guide.
5. **VEX: `snoise()` / wobble formula duplicated in two wrangles** (carve and water must agree on the lake shore).
   There is no shared-VEX-include building block in the MCP; a `vex_include` section on the HDA (or a project
   `vex/include` path) written by a tool would remove the copy.
6. I repeated my own bug from 4f #12 (read `point(0, "P", ...)` of points created with `addpoint` in the same wrangle);
   `validate_vex` passed it. Lint candidate stands.

## 4h. River tool v4: folds in bends, box at drops, live co-editing (2026-11-24, same project)

1. **(Revised in 4i: no new tool needed.)** ~~No mesh-quality checks.~~ The user found folded ribbon quads (a 61 deg kink) and a "box" at a drop by eye, in
   wireframe. My numeric checks measured water vs terrain, not the mesh itself. **Candidate: `check_mesh`**: flipped /
   folded faces vs the majority normal, self-intersections, degenerate/sliver faces, max turn angle along a curve,
   min edge length. I wrote the fold count into the project verify script; it belongs in the MCP.
2. **The user edits the scene while the agent works.** This pass they changed 7 HDA parms (res 512, slope window 3.2 m,
   cascade slope 0.001, ...) and moved the display flag inside the HDA. Nothing told me; I found it via
   `get_parameters(non_default_only=True)` after numbers moved. **Candidate: a "scene changed since your last call"
   summary** (parms, flags, nodes) on the next tool reply, or a `get_changes_since(token)` tool. Without it, an agent
   can silently overwrite the user's experiments.
3. **No parameter snapshot/restore.** To compare my fix at defaults without losing the user's values I
   reverted 7 parms and restored them in python. **Candidate: `snapshot_parameters(node) -> id` /
   `restore_parameters(id)`**, or `with_parameters={...}` on measuring tools (measure under temporary values,
   then restore). This is the safe way to test on a scene someone else is editing.
4. **`capture_screenshot` follows whatever network the user is in.** Getting the object-level view meant
   `set_current_network` away from where the user was working, then back. A `network=` / `display_node=` override on
   the capture (4f #8) would avoid moving the user's network editor at all.
5. **`get_parameters` takes `patterns`, not a filter** (my guess `parm_filter`, the name `get_node_card` uses, was refused).
   Small, but the same concept has two parameter names across tools; aligning them saves a round.
6. **Row-count windows vs metre windows**: refining a curve in bends made my row-count smoothing windows 8x
   shorter and produced the last folds. Not an MCP bug, but a guide-worthy rule for any curve-sampled mesh:
   windows in world units, never in sample counts.

## 4i. Mesh QA with native nodes + readable graphs (2026-11-24, same project)

1. **Correction to 4h #1: a `check_mesh` MCP tool would be over-engineering.** Houdini already has the checks:
   Normal (prim) + Group Expression `@N.y < 0` (folded/flipped faces), Divide + Intersection Analysis
   (self-intersections), PolyDoctor in *Mark* mode with "Create a group for each attribute" (ill-formed / tiny /
   non-manifold). A QA_REPORT null with `nprimsgroup()` / `npoints()` expressions shows the counts as parameters, lives
   in the HDA, and re-evaluates on every edit. **What the MCP should add instead is knowledge, not a tool: a
   `mesh_qa` section in a workflow guide** with this recipe and its gotchas:
   - PolyDoctor writes `valid_poly` as an *attribute* unless "Create a group for each attribute" is on, and the
     group is then `valid_poly_grp`, not `valid_poly` (cost two rounds).
   - Intersection Analysis needs triangles (Divide first).
   - It found a real defect my own numeric checks missed: 484 "ill-formed" polys = waterfall sheets whose edge rows
     coincided with river rows as separate points, plus overlapping sheets on consecutive drops. Fixed by welding.
2. ~~`get_parameters` skips spare parameters~~ **Corrected in 4l #4:** patterns are substrings, not globs, so the
   literal `qa_*` never matched. Spare parms are fine; the docstring should say "substring" or accept globs.
3. **`get_help_page(path="nodes/sop/polydoctor", section="Ill-Formed")` failed with an empty error.** Same class
   of bug as 4g #1: handler errors lose their message. (`get_node_card(include_help=True)` had the text.)
4. **No way to frame a whole network for `capture_network_editor`.** It frames one node only; whole-graph shots
   needed `NetworkEditor.setVisibleBounds` in python. **Add `frame="all"`** (and maybe `resolution`: the pane here is
   612 px wide, too small to read node names in a full-graph capture).
5. **Readable layout needs a planned grid, not auto-layout.** What worked: columns per section (Inputs | Terrain on
   top, Carve centre, Falls left, Water + QA right, Outputs one row at the bottom), 1.2 units between chained nodes,
   ~3 units between sections so `tidy_network` box titles do not push into the box above. Then check node overlaps
   and network-box overlaps numerically. **Candidate: `tidy_network(arrange="columns")`** that takes the same sections
   and places them on such a grid, plus an `overlaps` field in its reply. `build_network` auto-placing new nodes on
   top of existing ones (two exact overlaps here) is the root cause of the mess.

## 4j. Offset-curve lattice (PolyExpand2D) + mesh stamping (2026-11-24, same project)

1. **The right answer to the user's question was a workflow name, and the MCP had none.** "Edges snap to terrain,
   centre lines go down, rows never cross" is the standard road/river *spline cross-section lattice*: PolyExpand2D
   offset curves -> rows -> drape (ray/sample terrain) -> skin -> Heightfield Project (minimum). The `heightfields`
   guide mentions none of PolyExpand2D, Heightfield Project as a carve, or offset-curve lattices. **Add a
   `roads_rivers` guide** with this recipe; it would have been the first design instead of the fifth.
2. **Gotchas worth putting in that guide (each cost a round here):**
   - Heightfield Project's **Hit Farthest is on by default**: with overlapping meshes (tributary over main river)
     the far, higher surface wins and the min-carve silently does nothing (16 path samples uncarved). Turn it off.
   - Pairing offset curves into rows **by arc-length fraction drifts** (inner lines are shorter in bends, ends are cut
     square): 4 flipped + 10 ill-formed faces, edges 30 m off. Pairing by *closest point to the perpendicular rib,
     monotone along each line* fixed all of it (QA 0/0/0).
   - `primuvconvert` on a curve has a float overload; passing `set(u,0,0)` picks it ambiguously (warning, wrong cast).
   - PolyExpand2D on an open polyline needs **Split Offset Curves To Omit End Caps** to get separate left/right lines,
     and local scale attributes for a variable width.
3. **`verify_network` again reported nothing while a wrangle inside a for-each block failed to compile** (it does
   not cook inside blocks). Force-cooking each node in python was the only way to see it. Same as 4e #2, now with
   for-each blocks: **cook block_end nodes and report their compile errors.**
4. **`capture_screenshot` output depends on viewer state the agent cannot see**: the object-level display flag
   (moved by the user), the network the user is in, and display options. One capture showed curves only, with no terrain,
   and no error. **The capture reply should list what was drawn** (display node, prim count drawn, hidden types).
5. **Display-flag borrowing**: to show the LATTICE I had to move the user's display flag and restore it. A
   `capture_screenshot(node=...)` that draws one node's geometry without touching flags would remove that risk.

## 4k. Water read from the lattice, pools in the lattice (2026-11-24, same project)

1. **The user caught a half-done migration I had reported as done.** I said "carve and water come from the
   lattice", but the water width still came from a per-row terrain search (zigzags, one-sided pool wing). An agent
   self-check would have caught it: *"is any per-row search left that the new structure was meant to replace?"* This is
   a guide item, not a tool: **when migrating a method, list every consumer of the old one and check each.**
2. **Parameter sweeps again decided the design**: vertical-step position (0.5 vs 0.75 of a segment) and threshold
   (0.5 m vs mini_fall_drop) traded floating edges (+16 m) against water under rock (-10 m). Each variant was a
   manual edit + full verify at two settings with a parameter restore. `sweep_parameter` + `snapshot/restore`
   (4f #3, 4h #3) would turn ~6 calls into 1.
3. **Heightfield resolution vs geometry detail is invisible to the agent until measured**: at res 512 (3.9 m cells)
   a vertical step 1 m from a sample row is unresolvable. A heightfield guide note: *features narrower than ~2 cells
   cannot be stamped; check cell size against the smallest feature first.*
4. `set_wrangle_code` caught the `foreach (int tp; array(t1, t2))` ambiguity (VEX can't type the array literal) -
   good; the error text ("uninitialized __impl_foreach_end") is cryptic. A one-line hint in `validate_vex` output for
   this known pattern would help.

## 4l. Code-level recommendations (read from the source, 2026-11-24)

I mapped every friction item from 4e-4k to the code. Paths: `H/` = `houdini/scripts/python/fxhoudinimcp_server/handlers/`,
`P/` = `python/fxhoudinimcp/`. Ranked by how much each would have changed this session. The two most important
claims (#1, #4) I checked by reading the lines myself; the rest come from a read-only code survey.

### Bugs (small fixes, large effect)
1. **Every handler error loses its message (the "Error executing tool X" with nothing after it).** The handlers'
   messages are good (`help_handlers.py:291` lists the valid sections; `hda_handlers.py:1768` names the bad op). But
   `FXHoudiniError` subclasses plain `Exception` (`P/errors.py:9`), and the installed SDK (mcp 2.3.0,
   `mcp/server/mcpserver/tools/base.py:211-213`) treats anything that is not a `ToolError` as a crash and keeps only
   `Error executing tool <name>`. **Fix: `class FXHoudiniError(ToolError)`** (import `ToolError` via `P/_sdk.py`
   from `mcp.server.mcpserver.exceptions` on 2.x / `mcp.server.fastmcp.exceptions` on 1.x), or raise
   `ToolError(f"[{code}] {message}")` in `P/bridge.py:218`. One line, and it fixes 4g #1, 4i #3 and every future
   unexplained failure. **Highest value change in this list.**
2. **`build_network` never compiles VEX.** It sets `snippet` like any parm (`H/graph_handlers.py:1787`) and cooks only
   the display node (1907). `set_wrangle_code` already validates via `_validate_vex_quick` (`H/vex_handlers.py:34`,
   called at 272). Fix: after 1913, run `_validate_vex_quick` on every created node that has a `snippet`; add failures
   to `error_nodes`. For `dry_run`, build each wrangle spec in the scratch subnet, validate, destroy. (4e #1: three
   rounds lost to ambiguous-overload errors this session.)
3. **`verify_network(force_cook=True)` cannot see uncooked branches.** It force-cooks the display node and only
   re-cooks children that *already* have errors (`H/graph_handlers.py:2080-2093`); a never-cooked node has no errors.
   Fix: with `force_cook`, cook every leaf (no outputs) or every child where `needsToCook()`, cook `block_end` nodes so
   for-each bodies compile, and run `_validate_vex_quick` on `snippet` nodes. (4e #2, 4j #3.)
4. **`get_parameters(patterns=...)` is substring-only** (`H/parameter_handlers.py:1977`: `p in name or p in label`).
   `qa_*` matched nothing. Fix: use `fnmatch.fnmatchcase` when a pattern contains `*?[`, else keep the substring test.
5. **`tidy_network` laid out nodes although auto-layout is off.** The default comes from the *Houdini-side*
   `auto_layout_enabled()` (`H/node_handlers.py:1954`), while `FXHOUDINIMCP_AUTO_LAYOUT=0` is set on the MCP side;
   `P/tools/nodes.py:736` does not forward a default. Fix: send `layout = layout if layout is not None else
   auto_layout_enabled()` from the client, as `layout_children` already does (`nodes.py:546-560`).
6. **`get_node_errors_detailed` walks into locked HDAs** (`H/context_handlers.py:799`, `allSubChildren()`): 25 noise
   hits from stock heightfield nodes. Fix: skip `isInsideLockedHDA()` unless `include_locked_assets=True`; report the
   skipped count.
7. **The `simulation_cache` note fires on every edit upstream of a heightfield_erode** (`H/dop_handlers.py:193-195`
   adds any downstream node with a `resimulate` parm). heightfield_erode re-simulates on input change, so the note
   is wrong for it (~25 occurrences this session). Fix: skip `heightfield_erode*` types, or emit once per network.

### Missing building blocks (what would have let me iterate faster and check my own work)
8. **`sample_volume(..., compare_to="P.y")`**: per-point signed difference (field - P.y) with min/max/percentiles
   and the worst N points (with their attributes). `H/geometry_handlers.py:1851` already samples at `from_node`'s
   points (1873-1883); this is a subtraction and a sort. It replaces most of my `execute_python` calls
   (water vs terrain, bed vs bank, carve depth) and is the core check for anything placed on terrain.
9. **`get_attrib_stats(group_by=attr, ...)`**: aggregate A per value/bucket of B (row width per station, under-terrain
   counts per feature, invalid polys per group). Second most common python use.
10. **`sweep_parameter(node, parm, values, measure=[...])`** with automatic restore: one row per value, like
    `cook_frame_range` does for frames. Design decisions this session (step position 0.5 vs 0.75, mini-fall
    threshold, slide strength, canyon probe) were all manual sweeps.
11. **Parameter snapshot/restore.** `compare_snapshots` (`H/context_handlers.py:621`) can take and compare, but has
    no `restore` (718). Adding `action="restore"` (replay `stored[path]["params"]` with `parm.set`) makes it safe to
    measure at defaults on a scene the user is editing; I hand-rolled this ~8 times.
12. **"Scene changed since your last call"**: the user changed 7 HDA parms and display flags mid-session and nothing
    told me. A cheap version: every reply carries a modification counter plus the paths of parms/flags changed
    outside MCP calls since the previous call.
13. **Captures that do not depend on hidden UI state:**
    - `capture_network_editor(network_path=..., frame_all=True)` via `setVisibleBounds` (`H/viewport_handlers.py:1063`).
      Today it frames one node only, and shows whatever network the user is in.
    - `capture_screenshot(resolution=..., node=..., camera=dict(rotation, pivot, distance))` (`viewport_handlers.py:955`):
      set the camera and capture atomically, draw one node's geometry without moving display flags, and report
      what was drawn (display node, prim count, hidden types). Two of my captures this session drew nothing useful,
      with no error.
14. **Workflow guides as knowledge, not tools.** Guides are plain markdown, picked up automatically from
    `P/prompts/markdown/workflows/<topic>.md` (`P/prompts/workflows.py:172`); escape braces for `str.format`. Add:
    - `roads_rivers.md`: the spline cross-section lattice recipe (PolyExpand2D with Omit End Caps + local scale,
      closest-point-to-rib monotone row pairing, drape, Heightfield Project with **Hit Farthest off**, carve margin
      = one cell, vertical steps only at real drops, water read from the lattice not the terrain).
    - `mesh_qa.md`: Normal + Group Expression, Divide + Intersection Analysis, PolyDoctor Mark with groups
      (`valid_poly_grp`), QA_REPORT null with `nprimsgroup()` expressions.
    - Add aliases in `_SIM_ALIASES` and list the topics in the `get_workflow_guide` docstring (`P/tools/help.py:97`).
    - A `heightfields` addition: "a feature narrower than ~2 cells cannot be stamped; check the cell size first".

### Process notes for the agent side (no code)
- When migrating to a new method, list every consumer of the old one and check each (4k #1: I declared "water from
  the lattice" while its width still came from a terrain search).
- Measure against what the *final* geometry can represent: a heightfield cannot hold a vertical wall or a 3-cell-wide
  V. Several of my "regressions" this session were sampling limits, not logic errors; compare to the cell size first.

## 5. Session log

- 2026-11-23: River/terrain built (`HoudiniProjects/river_test`). 4 `execute_python` fallbacks: curve stash
  probing ×3 (now `set_curve_points`), noise parm discovery ×1. Labs 22.0 confirmed installed; workflow
  guides have no game-engine coverage.
- 2026-11-24: Curve-driven river tool + HDA (`HoudiniProjects/river_terrain`). 14 friction items in 4e; top three:
  build_network skips VEX compile, verify_network only cooks the display branch, error scan enters locked assets.
- 2026-11-24 (pass 2): adaptive width ramps, slide/settle, levees, per-fall sheets. 13 items in 4f; top:
  group-by stats, signed sample-under-points, parameter sweep, ramp linking.
- 2026-11-24 (pass 3): step pools, small waterfalls, lake end welded to the river. 6 items in 4g; top:
  silent error text in edit_hda_interface, parameter sweeps, perspective framing.
- 2026-11-24 (pass 4): bend folds + drop boxes fixed (0 folded quads at both settings). 6 items in 4h; top:
  mesh-quality check tool, notice of user edits, parameter snapshot/restore.
- 2026-11-24 (pass 5): native QA section (0 flipped / 0 intersections / 0 ill-formed after welding), graph laid out
  in columns. 4h #1 revised: no check_mesh tool, a guide recipe instead. 5 items in 4i.
- 2026-11-24 (pass 6): offset-curve lattice + Heightfield Project carve, water on lattice rows. QA 0/0/0, edges +-4.5 m.
  5 items in 4j; top: a roads_rivers guide (recipe + Hit Farthest / row pairing gotchas).
- 2026-11-24 (pass 7): water edges/banks/bed read from the lattice cross-sections, plunge pools inside the lattice,
  vertical steps only at real falls. 4 items in 4k.
- 2026-11-24 (pass 8): fall refine + symmetric sheet zone + carve margin; code-level recommendations in 4l
  (top: FXHoudiniError must subclass ToolError, build_network VEX validation, verify_network cooking).
