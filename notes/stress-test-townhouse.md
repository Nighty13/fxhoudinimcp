# Stress test: procedural town house from a concept image (2026-11-24)

Task: rebuild a medieval town house concept (stone block + jettied half-timbered
wing, balcony, tiled hip roof, exterior stair, props) as a procedural HDA,
driving Houdini 22.0.508 (GUI) only through the MCP.

Result: `ste::townhouse` (HoudiniProjects/townhouse/hda/ste_townhouse.hda),
194 SOP nodes, 13 interface parms, ~50k polys, no VEX, no Python for geometry.
Built in 7 `build_network` calls (each dry-run first), healthy at default and
at extreme values (timber_w 7, depth 9, floor_h 3.6, stone_w 4, roof_h 3.5).

**Status (same day):** defects 1-5 fixed and checked live, plus one found after
the test: package help (SideFX Labs, 449 node pages in $SIDEFXLABS/help) was
never indexed, so `search_help("edge damage")` found nothing, Labs cards had
no help, and `labs::filecache` would have been documented by the core File
Cache page. Now indexed from every HOUDINI_PATH help/ folder; the discipline
guide names the Labs tools. Tests in `tests/test_agent_ux_batch3.py`.
Open: 6-10.

## What worked well

- Dry runs validated every spec, including multiparm names (`name1`, `value1v`,
  add's `pt0`/`usept0`) and the `mountain::2.0` parms, first time.
- For-each roof tiling (normal -> matchaxis x2 -> grid points kept inside the face
  -> copytopoints -> matchaxis back) came back to the exact roof bounds.
- `insert_after` spliced nodes into existing chains and reported the rewire.
- `get_parameters(inside=..., value_contains=...)` found all 7 consumers of a
  shared expression in one call.
- `tidy_network` documented 8 sections, `create_hda` promoted the spare parms,
  `update_hda` wrote the library and reported `saved_to_disk`.
- `capture_screenshot` `viewer_note` explained subnet-vs-object display each time.

## Defects / friction found (ranked)

1. **`scene_changed_outside_mcp` false positives.** Reported on almost every
   call although nobody touched Houdini:
   - `flags` on nodes the previous `build_network` itself flagged (events land
     after `end_call`, e.g. deferred display-flag updates);
   - `parm from/to/t/size/rows/cols` on matchaxis/grid nodes inside a for-each:
     expression-driven parms re-evaluated during loop cooks fire
     ParmTupleChanged;
   - `tile_tint/switch1 parm input` (an HDA-internal node).
   Fix idea: ignore ParmTupleChanged while `hou.isCooking()`/for parms holding
   an expression, drain events for a short tick after `end_call`, skip nodes
   inside locked assets. As is, the agent learns to ignore the field, which
   defeats it.
2. **Int menu parms reject menu tokens.** `color` SOP `class: "point"` failed
   validation with advice to write `{"expr": 'point'}` (wrong: it would be an
   expression error). The card lists the tokens; validation should map
   token -> index for Int menus as it does for String menus.
3. **copytopoints made by script carries no "Attributes from Target"**
   (`targetattribs=0`; plain `createNode` does the same, the Tab tool adds the
   default entry). Point `Cd` silently did not reach the copies. Either have
   build_network add the Tab-menu default entry for copytopoints, or warn in the
   build result and the copy guide.
4. **Box `dodivs` + Polygon outputs an open-curve cage**; walls vanished from the
   viewport while the build reported success. `get_geometry_info` has no
   open/closed polygon count; needed `execute_python` to see it. Add
   `open_polygons` to geometry info / build evidence.
5. **`verify_network` lists every node (194 rows) when healthy.** Compact mode
   should list only nodes with errors/warnings/flags of interest.
6. **`set_viewport_direction` framing is not reproducible.** First call on a fresh
   GUI viewport looked from behind; the same `distance` gave different framing
   in later calls; `frame_all(bounds)` + rotation left ~40% empty frame.
7. **`insert_after` cannot reference a node created in the same build**
   (error: "insert after a node that is already built"): chaining two inserts
   needs two calls.
8. **One expression change across 7 nodes = 7 `set_parameters` calls.** A
   find/replace mode on `get_parameters(value_contains)` (or a
   `replace_in_expressions` tool) would make it one.
9. **`set_parameters` echoes `$PT` expressions evaluated out of context**
   (`new_value` 0), which reads like the write failed.
10. Client tool schemas stay stale until the MCP server restarts (no `node=` on
    capture_screenshot, no `sweep_parameter`) - known, but cost a workaround here.

## Round 2: Labs-first building (Labs Building Generator + Edge Damage)

Discovery now works (`search_help("building blockout modules")` ranks the
three Labs building tools first). The round found:

11. **Type resolution disagreed with createNode** (fixed). An unversioned name
    with an exact unversioned type (`labs::building_generator_utility`, 1.0)
    resolved to it, while createNode builds `::2.0`: the card showed 10 of 24
    parms and build_network validated against the wrong type.
    `hou.preferredNodeType` is no guide (None for that Labs type, a different
    type for rbdconcretefracture). Now: highest version wins, hidden included;
    identical to createNode for all 281 versioned SOP/OBJ names here.
12. **build_network refused a radio FolderSet** (fixed): "folder01 is a folder,
    not a value", although the card lists it with its index menu. Labs'
    utility needs it to switch to Floor Description mode.
13. **The watcher watched inside locked assets** (fixed): naming an HDA node
    put callbacks on its children, so Labs' internal line3 "changed".
    `reload_plugin` did not reload `watch`, so the round-1 live check of the
    watcher ran the old code; it now does (takes effect after one restart).
14. **get_attrib_stats / get_geometry_info stats cannot read output 1** (open).
    The generator's placement point cloud is its second output; every check of
    it needed execute_python.
15. **No warning on a polygon explosion** (open). Edge Damage at defaults took
    the display from 557 to 4.93M polygons; the build reported success.
16. **Labs' own help is wrong** for the generator (pattern syntax, module axes).
    Reading the asset's inner Python through get_parameters found the truth;
    the discipline guide now says to do that, with the conventions.
17. execute_python that raises halfway leaves its earlier writes (a sweep left
    a parm at a failing value). A sweep belongs in sweep_parameter, which
    restores values; this session's client predates it.

## Guide notes worth adding

- attribrandomize on a vector randomizes channels independently (pink/green
  stones); for tints randomize a float and map it with color SOP "Ramp from
  Attribute".
- Box with divisions for wall grids: use type `polymesh` + `divrate`, not `dodivs`.
- Tile a roof of any shape: the for-each/matchaxis pattern above (no VEX).
