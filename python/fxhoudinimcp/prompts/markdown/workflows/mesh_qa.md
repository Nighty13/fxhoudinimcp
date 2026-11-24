You are checking a mesh for defects with Houdini's own nodes.

Goal: {description}

There is no QA tool in this server, on purpose: native nodes already do the
checks, and a QA branch built from them lives inside the HDA, re-evaluates on
every edit and is visible to the user. Build it once as a side branch off the
output and read its counts as numbers.

## The QA branch

1. **Flipped / folded faces**: `normal` (Add Normals To: Primitives), then
   `groupexpression` (primitive group, e.g. `qa_flipped`) with `@N.y < 0` for a
   surface that should face up (water, road, terrain skirts). For a closed mesh,
   compare N with the direction from the part's centre instead (assign the
   centre to a typed vector first). A 61 degree kink in a ribbon is a fold even
   when its normal still points up: add a group on the angle between neighbouring
   rows if folds are the risk.
2. **Self-intersections**: `divide` (triangulate) first, then
   `intersectionanalysis`. It needs triangles; on quads it misses or misreports.
   Its output points mark the intersections.
3. **Ill-formed / degenerate / non-manifold**: `polydoctor` in **Mark** mode
   (not Repair) with **"Create a group for each attribute"** on. Without that
   toggle it writes `valid_poly` as an *attribute*; with it the group is
   **`valid_poly_grp`**, not `valid_poly` (that name cost two rounds). Read the
   card (`get_node_card("polydoctor", "Sop")`) for the other group names.
4. **QA_REPORT**: a `null` with spare **int** parms whose expressions count:
   `nprimsgroup("../qa_flipped", "qa_flipped")`, `npoints("../qa_intersections")`,
   and the polydoctor invalid count (`nprims` of the polydoctor output minus
   `nprimsgroup` of `valid_poly_grp`). Promote them to the HDA interface (read
   only) so the user sees them too.

## Fixing what it finds

- Ill-formed polys where two sets of rows meet: usually **coincident points that
  are not welded** (waterfall sheet edges on river rows, overlapping sheets on
  consecutive drops). `fuse` them; 484 ill-formed polys went to 0 that way.
- Flipped faces on built geometry: winding (Houdini's front face is clockwise),
  fixed with `reverse` on the offending part, or the build order of the rows.
- Folds on curve-driven meshes: smoothing windows in sample counts instead of
  metres, or rows paired by arc-length fraction
  (`get_workflow_guide("roads_rivers")`).

## Verify

1. `verify_network(<net>, force_cook=True)`: the QA branch cooked (it is not on
   the display path, so check it is not listed as never cooked or stale).
2. `get_parameters(<QA_REPORT>, patterns=["qa_"])`: every count is 0
   (flipped / intersections / ill-formed = 0/0/0).
3. If a count is non-zero: `get_group_members(<group node>, "qa_flipped", group_type="prim")`
   lists the offenders; `get_prims(<group node>, group="qa_flipped")` reads them.
4. `get_groups(<polydoctor>)` shows `valid_poly_grp` (proves the toggle is on);
   its size equals the polydoctor input's prim count when clean.

{network_housekeeping}
