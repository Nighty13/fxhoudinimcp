You are building a road, river or other curve-driven feature carved into a
heightfield terrain.

Goal: {description}

Read `get_workflow_guide("heightfields")` first; for Unreal output also
`get_workflow_guide("unreal_landscape")`. This guide is the structure that
worked after four failed designs: a **spline cross-section lattice**. Edges snap
to the terrain, the centre goes down, rows never cross.

## The lattice recipe

1. **Centre curve**: resample by length (metres, not a point count), smooth with
   a window in metres.
2. **Offset lines**: `polyexpand2d` on the open centre curve, with **Split Offset
   Curves To Omit End Caps** on, so you get separate left and right lines instead
   of one closed outline. Variable width comes from its local scale attribute on
   the curve points (look the parm names up with `get_node_card`). Several
   offsets give several lines per side (bank, edge, bed).
3. **Pair the lines into rows**: for each centre sample, take the **closest point
   on each offset line to the perpendicular rib**, monotone along each line (a row
   never goes back along a line). **Not by arc-length fraction**: inner lines are
   shorter in bends and ends are cut square, so fraction pairing drifted 30 m off
   and produced 4 flipped + 10 ill-formed faces; closest-point pairing gave 0/0/0.
4. **Drape**: put each row's edge points on the terrain (`ray` onto a
   convertheightfield copy, or sample the height layer); the centre points take
   the designed bed height.
5. **Skin** the rows into a quad lattice (skin, or a grid built row by row).
6. **Carve**: `heightfield_project` the lattice into the height layer with the
   **minimum** combine, and **Hit Farthest OFF**. It is on by default: where two
   lattices overlap (a tributary over the main river) the farther, higher surface
   wins and the carve silently does nothing (16 path samples stayed uncarved).
7. **Carve margin = one heightfield cell** past the edge rows, so the bank meets
   the lattice instead of leaving a lip.
8. **Water reads the lattice, not the terrain**: water edges, banks and bed come
   from the lattice rows. A per-row search of the terrain for the water width
   zigzags and makes one-sided pools.

## Order of operations

settle (centre curve into the valley) → bank level → features (pools, falls,
levees) → carve → protect (mask the carved area) → erode → lock (copy the carved
height back over the eroded one inside the mask) → water.

Erosion after an unprotected carve fills the channel; water before the lock
floats over the filled bed.

## Rules

- **Smoothing and search windows in world units, never in sample counts.**
  Refining the curve in bends made row-count windows 8x shorter and produced the
  last folds.
- **Vertical steps only at real drops** (falls above a threshold drop). Steps
  elsewhere put water under rock or floating edges.
- **A feature narrower than ~2 heightfield cells cannot be stamped.** At 3.9 m
  cells a step 1 m from a sample row is unresolvable. Check the cell size
  (`get_volume_info` voxel size) against the smallest feature before treating a
  difference as a bug.
- **When migrating to a new method, list every consumer of the old one and check
  each.** "Carve and water come from the lattice" was reported while the water
  width still came from a terrain search.
- VEX on curves: assign `point()` results to a typed variable before comparing
  or appending (`vector p = point(0, "P", pt); append(pts, p);`).
  `primuvconvert` on a curve has a float overload: pass a float `u` (and read the
  modes on `get_help_page("vex/functions/primuvconvert")`), not `set(u, 0, 0)`,
  which is resolved ambiguously with only a warning.
- In a detail wrangle, `point(0, ...)` reads the **input**, never points created
  by `addpoint` in the same cook.
- Weld coincident edge rows (fuse) where a fall sheet meets the river rows;
  separate coincident points show up as ill-formed polys in QA.

## Verify (numbers; screenshots only for the look)

1. `verify_network(<net>, force_cook=True)`: healthy. Side branches (water, falls)
   that are not displayed may not have cooked: check each output's node too.
2. **Spread of the driven attribute**: `get_bounding_box(<water>)` and
   `get_attrib_stats(<lattice>, attribs=["P"])`. **y-min == y-max means a flat
   river**: the bed profile logic is not reaching the output.
3. **Monotone height**: along the centre curve, height never rises downstream
   (for a river). Store `dh` (this sample minus the previous) on the curve and
   `get_attrib_stats(attribs=["dh"])`: max <= 0 (or within a tolerance).
4. **Depth > 0**: `sample_volume(<terrain>, fields=["height"], from_node=<water points>)`
   and compare to the water points' P.y (`get_attrib_stats(<water>, attribs=["P"])`):
   water above the bed everywhere; store `depth` on the points and check its min.
5. **Edge gap**: water edge points vs the terrain under them, within a few
   decimetres (edges neither floating nor buried).
6. **Spikes**: `get_volume_info(<carved terrain>)` height min/max and percentiles
   before vs after the carve; no new extreme outside the carve range.
7. **Sheets per fall**: one waterfall sheet per real drop
   (`get_attrib_stats(<sheets>, attribs=["fall_id"], attrib_class="prim", unique=True)`).
8. Mesh QA on the lattice and water: `get_workflow_guide("mesh_qa")`; expect
   0 flipped / 0 intersections / 0 ill-formed.

{network_housekeeping}
