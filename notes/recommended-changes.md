# Recommended MCP changes (from agent-ux-notes.md, 2026-11-24)

Consolidated from sections 2–4l of `agent-ux-notes.md` (8 stress-test passes: world→UE, stylized world,
VAT wall, river tool v1–v4, lattice, QA). Duplicates merged; each item cites the note IDs it closes.
Ranked by (rounds/tokens saved) ÷ (effort). `H/` = `houdini/scripts/python/fxhoudinimcp_server/handlers/`,
`P/` = `python/fxhoudinimcp/`.

**Status 2026-11-24: P0–P4 implemented (uncommitted on `main`). Unit 2038/2038, live integration (hython
22.0.508) 528/528, live GUI feature script 41/41. Live testing fixed 3 bugs: dry-run VEX errors were
filtered as input noise, the scene watcher lost nodes recreated at the same path, and
capture_screenshot(camera={rotation}) did not frame the node.** (Integration runner note:
`tests/run_integration.py` puts the venv's cp312 numpy on hython 3.13's path; run hython with a
3.13-only pytest folder on PYTHONPATH instead.)
221 tools (+`sweep_parameter`, `sim_report`, `collapse_to_subnet`), 37 guides (+6). Already fixed before
this batch: #2 (BAD_ARGUMENTS had no traceback), #7 (compare_volumes), #10 (ascii_flow). P5 not started.
Needs a live check first: capture_screenshot(node=/camera=) viewer restore, render_sheet in-Houdini path
(now the default; `hython=True` for the old one), create_hda(promote_from), HDA input labels via
DialogScript, the scene-change watcher (`scene_changed_outside_mcp`), build_network VEX dry run.
Restart Houdini (dispatcher.py changed; reload_plugin keeps it stale) and the MCP server before testing.

**Already done (do not redo):** `root_errors` collapse in `verify_network`, `menu_items` in build_network refusals
(batch 1); VAT ROP output verification + `flat_images` check; BOM strip + nested-string cap in `execute_python`;
`set_curve_points`; network-capture staleness fix.

**Checked against code today:** `FXHoudiniError(Exception)` (`P/errors.py:9`) ✔ still a plain Exception;
`get_parameters` patterns are substring-only (`H/parameter_handlers.py:1977`) ✔; `list_node_types` has no
version filter (`P/tools/nodes.py:247`) ✔; `get_parameter_schema` client exposes `parm_name` + `filter` only,
while the handler accepts a `parm_names` list (`H/parameter_handlers.py:1072`) — so 4b #4 is an API mismatch,
not an ignored filter.

---

## P0 — Bugs that hide failures (small, do first)

| # | Change | Where | Closes |
|---|---|---|---|
| 1 | **`FXHoudiniError` subclasses `ToolError`** (or bridge re-raises as `ToolError("[code] msg")`) so error text reaches the client. Biggest single fix: every "Error executing tool X" with no message. | `P/errors.py:9`, `P/bridge.py:218`, `P/_sdk.py` import shim | 4g#1, 4i#3, 4l#1 |
| 2 | **Strip tracebacks from `BAD_ARGUMENTS`** replies; keep message + valid options. | bridge / dispatcher error path | 4b#6 |
| 3 | **`build_network` compiles VEX**: run `_validate_vex_quick` on every node with a `snippet` (also in `dry_run` via the scratch subnet); failures go to `error_nodes`. | `H/graph_handlers.py` ~1787/1913, reuse `H/vex_handlers.py:34` | 4e#1, 4l#2 |
| 4 | **`verify_network(force_cook)` cooks what matters, not just the display branch**: every leaf / `output` SOP / `OUT_*`, every `block_end` (for-each bodies), VEX-validate snippets; list `never_cooked` instead of counting them healthy. | `H/graph_handlers.py:2080-2093` | 4e#2, 4j#3, 4l#3 |
| 5 | **`get_node_errors_detailed` skips locked-HDA internals** by default (`include_locked_assets=True` to opt in, report skipped count) and **dedupes** repeated messages with the same root/blocked_by shape as `verify_network`. | `H/context_handlers.py:799` | 4d#8, 4e#3, 4l#6 |
| 6 | **`tidy_network` respects `FXHOUDINIMCP_AUTO_LAYOUT=0`**: client forwards `layout=auto_layout_enabled()` when omitted (as `layout_children` does). It currently moves nodes against the server rule. | `P/tools/nodes.py:736` | 4e#9, 4l#5 |
| 7 | **`compare_volumes` returns 0 on 2D heightfields** — samples wrong Z. | volume compare handler | 4b#3 |
| 8 | **`start_render` verifies `heightfield_output`** (read its `output` parm; same fallback path as the Labs VAT fix). | outputs.reported_outputs | 4b#2 |
| 9 | **`build_network` refuses writes to Folder/FolderSet parms** (it accepted an expression on a multiparm folder). | build_network parm validation | 4d#1 |
| 10 | **`ascii_flow` is wrong on multi-input nodes** — replace (see P1 #11) rather than patch. | `H/context_handlers.py:191` | 2#1 |
| 11 | **Drop the `simulation_cache` note for `heightfield_erode*`** (it re-sims on input change); ~25 false notes in one session. | `H/dop_handlers.py:193-195` | 4e#12, 4f#11, 4l#7 |
| 12 | `update_hda` reports a stale `library_file_mtime`; `get_node_card` "matched 85 of 80"; `persp` alias for `perspective`. Cosmetic, batch together. | | 4e#10, 4d#10, 4b#11 |

## P1 — Token cuts (replies that are too big or too vague)

| # | Change | Closes |
|---|---|---|
| 13 | **Compact graph text in `get_network_overview`**: one line per node, inputs in input order, flags only when set, positions only on request (`river_sample_height attribwrangle <- river_resample, terrain_noise`). Replaces `ascii_flow` and the 16-decimal positions (~1k tokens / 16 nodes today). | 2#1–2, 4c |
| 14 | **`set_hda_interface` / `edit_hda_interface` echo the parm tree once**, flat list only on request (~8–9k tokens for 14–21 parms today). | 4e#5, 4f#6 |
| 15 | **`set_wrangle_code` full replacement echoes line count + `vex_valid`/errors only**, not the snippet (~10k tokens for 200 lines). | 4f#5 |
| 16 | **`list_node_types` returns the latest version only** (`all_versions=True` for the rest; `node_versions.json` already exists). | 2#3 |
| 17 | **One parameter-filter vocabulary across tools**: `get_parameters(patterns)`, `get_node_card(parm_filter)`, `get_parameter_schema(parm_name/filter)` all accept the same name, the same **glob** semantics (`fnmatch` when `*?[` present, substring otherwise), and a list. Expose `parm_names` list on `get_parameter_schema`. | 4b#4, 4c, 4d#2, 4h#5, 4i#2, 4l#4 |
| 18 | **`get_parameter` returns menu tokens** for menu parms (so the agent doesn't pull an 8k schema to learn `origin`/`centroid`). | 4c |
| 19 | **File-backed long string parms**: `set_parameter(from_file=...)` or a patch op, for Python SOP / long snippets (~32k chars re-sent in one pass); also makes generators diffable in git. | 4c, 4c-2 |
| 20 | **`max_size`/`resolution` on `capture_screenshot` and `capture_network_editor`.** | 1, 3E |
| 21 | **`get_node_card(include_help)`** — trim help to a summary + section list by default (one card = 12.5k chars). | 4b |
| 22 | `get_volume_info`: compact mode (one row per layer) — ~3k chars per call on 4–8 layer heightfields. | 4b |

## P2 — Missing building blocks (each replaces repeated `execute_python`)

Ranked by how many python fallbacks they would have removed.

| # | Change | Notes / closes |
|---|---|---|
| 23 | **`sample_volume(..., node_paths=[A,B], compare_to="P.y")`** → per-point signed delta, min/max/percentiles, worst-N points with attributes. Extends the existing `from_node` sampling (`H/geometry_handlers.py:1851`). Core check for anything on terrain; 6 of 9 python calls in one pass. | 4e#4, 4f#2, 4l#8 |
| 24 | **`get_attrib_stats(group_by=attr, buckets=..., reduce=...)`** and **`list_if_count_below=N`** to return the elements of small groups. | 4f#1, 4f#10, 4l#9 |
| 25 | **`sweep_parameter(node, parm, values, measure={node, attribs/expr})`** with automatic restore, one row per value (like `cook_frame_range` for frames). Works for menus/toggles too. Decided most design choices in the river passes. | 4f#3, 4g#2, 4k#2, 4l#10 |
| 26 | **Parameter snapshot/restore**: `compare_snapshots(action="restore")`, or `with_parameters={...}` on measuring tools. Safe measurement on a scene the user is co-editing (hand-rolled ~8×). | 4h#3, 4l#11 |
| 27 | **"Scene changed since your last call"**: every reply carries a modification counter + paths of parms/flags/nodes changed outside MCP calls. Prevents silently overwriting user experiments. | 4h#2, 4l#12 |
| 28 | **`cook_frame_range(reset=True)`** / `reset_simulation` that knows SOP solvers (`rbdbulletsolver` `resetsim`, etc.) — stale caches looked like "fix had no effect". | 4d#3 |
| 29 | **`sim_report(node, frames, rest_node)`**: per-frame table of broken %, displacement, speed percentiles. Probably a thin wrapper over #24 + frame loop; build after #24. | 4d |
| 30 | **`validate_vex(code=..., run_over=...)`** compiles a snippet in a scratch wrangle before it lands, plus **lint hints** for the known traps: ambiguous `point()` in comparisons/`append`, reading `point(0,...)` of points created by `addpoint` in the same cook, `foreach` over array literal (cryptic `__impl_foreach_end`). | 4d#4, 4f#12, 4g#6, 4k#4 |
| 31 | **HDA authoring gaps**: (a) `create_hda(promote_from="CONTROLS")` copies spare parms with **current values as defaults** and links the null; (b) `set_hda_interface(default_from_value=True)` for `from_parm`; (c) `inputs=[{label, optional}]`; (d) `update_hda(unlock=True)` / report `locked`; (e) `update_hda` warns when instance values differ from definition defaults (Unreal cooks defaults). | 4c-2, 4e#6–8, 4f#7 |
| 32 | **Collapse-into-subnet** as a tool or `build_network` option, handling the auto-created `output0`. | 4b#5 |
| 33 | **`build_network` `insert_after: "node"`** (insert between two wired nodes in one call) and **never auto-place on top of existing nodes**. | 4c, 4i#5 |
| 34 | **`link_parameters` supports ramps and multiparms.** | 4f#4 |

## P3 — Captures that don't depend on hidden UI state

The user drives the same Houdini; captures followed their network, camera and display flag.

| # | Change | Closes |
|---|---|---|
| 35 | **`capture_screenshot(node=..., network=..., camera={rotation,pivot,distance}, resolution=...)`**: set camera + capture atomically, draw one node without moving display flags, restore viewer state after. | 4c, 4f#8–9, 4h#4, 4j#5, 4l#13 |
| 36 | **Capture reply says what was drawn**: display node, prim count, hidden types, viewer network (extends the existing `viewer_note`). | 4j#4 |
| 37 | **`capture_network_editor(network_path=..., frame="all")`** via `setVisibleBounds`. | 4i#4, 4l#13 |
| 38 | **Server-side `render_sheet`**: today it spawns hython and takes a license seat (unusable with 1 seat). Then add `views=[presets]|orbit:N` at one frame and `by_attribute="variant"` contact sheets. | 3D, 4c, 4d#9 |
| 39 | **Perspective framing helper**: `frame_all(direction=[rx,ry])` / `set_viewport_direction(look_at, clearance)` so cameras don't land inside terrain. | 4g#3 |
| 40 | `render_viewport(visualize=True)` for heightfields (temp heightfield_visualize + instance points) — grey previews were useless. Lower priority; numbers carried most checks. | 4b#10 |

## P4 — Workflow guides (markdown only, `P/prompts/markdown/workflows/`)

None of the 31 existing guides cover the stated focus (UE 5.8). Cheapest high-value work; escape braces for
`str.format`, add aliases to `_SIM_ALIASES`, list topics in `get_workflow_guide` docstring (`P/tools/help.py:97`).

- **`unreal_landscape.md`** — **first line: paint layers need `unreal_material` with a Layer Blend material or HE 3.0
  drops every weight layer.** Then: landscape sizes (1009/2017/4033/8129), spacing snap `size/round(size/spacing)`,
  Z scale from measured height min/max (`red_auto_range` isn't stored), clean inherited layer attribs off scatter
  points, `heightfield_scatter` ignores soft mask values, `unreal_*` attribute list, verify section.
- **`game_assets.md`** — HE interface rules, declare parm types explicitly (int vs float setters fail silently),
  UVs/LODs/collision (`UCX_`), polygon winding (clockwise front face), Copy to Points `pivot` default, cm vs m.
- **`vat.md`** — mode per sim type, VAT 4.0 settings, **`enablecooking=1` or zero textures with no error**, empty-FBX
  workaround on 22.0.508, packed-RBD gotchas (name on points, collisiongroup/ignore, rebar must ignore concrete).
- **`niagara.md`** — Labs Niagara ROP attributes, frame rate, spawn/death, HoudiniNiagara DI.
- **`roads_rivers.md`** — spline cross-section lattice (PolyExpand2D w/ Omit End Caps + local scale → closest-point
  row pairing → drape → skin → Heightfield Project with **Hit Farthest off**), order of operations, windows in
  world units not sample counts, numeric checks (monotone, depth > 0, edge gap, min/max spread of driven attribute).
- **`mesh_qa.md`** — native QA recipe: Normal + Group Expression, Divide + Intersection Analysis, PolyDoctor Mark
  with groups (`valid_poly_grp`), QA_REPORT null with `nprimsgroup()`. (Replaces the dropped `check_mesh` tool idea.)
- **Fixes to existing guides**: `heightfields.md` — heightfield_output exports images (not geometry), features
  narrower than ~2 cells can't be stamped; discipline guide — "validate wrangles before build_network", "when
  migrating a method, check every consumer of the old one", "measure against what the final geometry can represent".
- **Tool descriptions**: `execute_python` namespace is fresh per call; capture parm-interface proof via
  `capture_screenshot(pane_name=<Parm pane>)` after `set_selection`.

## P5 — Later / separate project

- **`ue_bridge`** next to the MCP, built on UE Python remote execution (`remote_execution.py`) — live retunes without
  4–5 min editor relaunches; UE content manifest (mesh bounds/pivots, landscape layer names) fed back to Houdini;
  editor-viewport/MRQ capture path (SceneCapture misses grass; minimized/background window writes nothing);
  `.uproject` renderer-settings template (Lumen/VSM/SM6). Notes say this is worth more than further Houdini tools
  once P0–P2 land.
- `new_project` tool or skill (folder layout + hip save + `$JOB`) — 4e#14.
- Shared VEX include section on HDAs — 4g#5.
- `tidy_network(arrange="columns")` with an `overlaps` field — 4i#5.
- `list_node_types(category=...)` — 3F, low priority.

## Not doing (per notes)

`check_mesh` tool (native nodes cover it, 4i#1), extra camera tools, video capture, per-node screenshots, a second
graph-reading tool, custom VAT/Niagara exporters, wrangle justification inside build_network (4e#13).

## Suggested batching

1. **Batch 2 (bugs, ~1 day):** P0 #1–#6, #9, #11, plus P1 #17 (glob) and #18.
2. **Batch 3 (tokens):** P1 #13–#16, #20–#22.
3. **Guides:** P4 in parallel — no code risk.
4. **Batch 4 (building blocks):** #23, #24, #25+#26 together, #30, #31.
5. **Batch 5 (captures):** #35–#38.
6. Re-run stress test 5 (graph scale, 80–150 nodes) to measure batches 2–3.
